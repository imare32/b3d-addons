"""Taichi 后端的 Gray-Scott 反应扩散求解器（GPU kernel，实时）。

与 :mod:`sim_reaction_diffusion.solver` 的 NumPy 版**同一套方程**（那边的模块文档有
完整推导），只是把每个子步拆成 ``evolve_kernel`` / ``update_kernel`` 两个 kernel 跑在
GPU 上：

* 九点各向同性拉普拉斯（十字 0.2、对角 0.05、中心 −1），与参考实现逐式一致；
* 周期性边界用 ``get_val`` 的索引取模表达（NumPy 版等价于 ``mode="wrap"`` 的 padding）；
* 显式欧拉，每步把 ``u`` / ``v`` 夹到 ``[0, 1]``。

字段按 ``[i, j]`` 索引并直接以 C 序导出，编号与 :func:`mesh.grid_geometry` 的顶点顺序
一致（``index = i*ny + j``）。

``initialize`` 直接复用 :func:`solver.seed_arrays` 的 NumPy 种子，再 ``from_numpy`` 上传
—— 种子逻辑只有一份，两个后端不会长歪。Taichi 的 ``ti.init`` 一个进程只做一次：
Blender 里热重载模块时复用已有的 runtime，不会因为重复初始化而报错。取不到 GPU 时退到
CPU（``ti.cpu``），再失败就由 :func:`sim_reaction_diffusion.solver.create` 回退到 NumPy。
"""

# 注意：**不能**加 ``from __future__ import annotations`` —— Taichi 直接读
# ``__annotations__`` 来判断 kernel 参数类型，PEP 563 的字符串注解会让它报
# ``TaichiSyntaxError: Invalid type annotation``。

import glob
import os
import sys

import numpy as np

from .solver import DEFAULTS, seed_arrays


def _local_module_roots() -> list[str]:
    """第三方库的兜底查找路径（只在 ``import taichi`` 失败时才用）。

    正常启动（带 ``BLENDER_USER_RESOURCES``）时 Blender 会把
    ``$BLENDER_USER_RESOURCES/scripts/modules`` 加进 ``sys.path``，用不着这段逻辑；但
    ``--factory-startup`` 不读用户偏好，那一份路径也会丢 —— 后台测试 / CI 常这么启动，
    于是就会「明明装了 taichi 却回退 NumPy」。这里按环境变量与便携版目录再补一次，
    不改动已有环境；官方扩展依赖目录 ``extensions/.local/lib/python<X.Y>/site-packages``
    本来就在 ``sys.path`` 上，只是顺手一起兜底。
    """
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    roots = []
    user = os.environ.get("BLENDER_USER_RESOURCES")
    if user:
        roots.append(os.path.join(user, "scripts", "modules"))
        roots.append(
            os.path.join(user, "extensions", ".local", "lib", version, "site-packages")
        )
    appdata = os.environ.get("APPDATA")
    if appdata:
        pattern = os.path.join(
            appdata, "Blender Foundation", "Blender", "*", "scripts", "modules"
        )
        roots.extend(sorted(glob.glob(pattern), reverse=True))
    return [root for root in roots if os.path.isdir(root)]


try:
    import taichi as ti
except ImportError:  # pragma: no cover - 取决于本机安装
    for _root in _local_module_roots():
        if _root not in sys.path:
            sys.path.append(_root)
    import taichi as ti

__all__ = ["TaichiReactionDiffusion", "ensure_init"]

#: 是否已完成 ``ti.init``（热重载会把模块级变量清掉，但 runtime 还在，会重新探测）。
_READY = False

#: 实际用的架构名（cuda / vulkan / metal / cpu / ...），只用于面板读数。
_ARCH = "?"


def ensure_init() -> str:
    """保证 Taichi 已初始化，返回实际架构名。优先 GPU，失败退 CPU。"""
    global _READY, _ARCH
    if _READY:
        return _ARCH

    runtime = ti.lang.impl.get_runtime()
    if getattr(runtime, "prog", None) is None:
        try:
            ti.init(arch=ti.gpu, offline_cache=True)
        except Exception:
            # 没有可用 GPU/驱动：CPU 后端照样正确，只是慢
            ti.init(arch=ti.cpu, offline_cache=True)
    try:
        _ARCH = str(ti.lang.impl.current_cfg().arch).split(".")[-1].lower()
    except Exception:
        _ARCH = "?"
    _READY = True
    return _ARCH


@ti.data_oriented
class TaichiReactionDiffusion:
    """二维 Gray-Scott 反应扩散求解器（Taichi 后端，GPU 优先）。"""

    backend = "taichi"

    def __init__(self, nx: int = 200, ny: int = 200, params: dict | None = None):
        arch = ensure_init()
        self.nx = int(nx)
        self.ny = int(ny)

        shape = (self.nx, self.ny)
        self.u_field = ti.field(dtype=ti.f32, shape=shape)
        self.v_field = ti.field(dtype=ti.f32, shape=shape)
        self.u_next_field = ti.field(dtype=ti.f32, shape=shape)
        self.v_next_field = ti.field(dtype=ti.f32, shape=shape)

        # 物理参数放 0 维字段：kernel 里按常量读，改参数不用重编译
        self.s_Du = ti.field(dtype=ti.f32, shape=())
        self.s_Dv = ti.field(dtype=ti.f32, shape=())
        self.s_F = ti.field(dtype=ti.f32, shape=())
        self.s_k = ti.field(dtype=ti.f32, shape=())
        self.s_dt = ti.field(dtype=ti.f32, shape=())

        self.params = dict(DEFAULTS)
        self.set_params(**(params or {}))

        #: 后端说明（面板读数用）。
        self.note = f"taichi/{arch}"

        self.initialize()

    # --- 参数 ---------------------------------------------------------------

    def set_params(self, **kwargs) -> None:
        """就地更新物理参数（下一子步生效，不重置场）。"""
        for key, value in kwargs.items():
            if key in DEFAULTS and value is not None:
                self.params[key] = float(value)
        self.apply_params(
            self.params["Du"],
            self.params["Dv"],
            self.params["F"],
            self.params["k"],
            self.params["dt"],
        )

    @ti.kernel
    def apply_params(self, Du: ti.f32, Dv: ti.f32, F: ti.f32, k: ti.f32, dt: ti.f32):
        self.s_Du[None] = Du
        self.s_Dv[None] = Dv
        self.s_F[None] = F
        self.s_k[None] = k
        self.s_dt[None] = dt

    # --- 初始化 / 注种 ------------------------------------------------------

    def initialize(
        self,
        mode: str = "CENTER",
        radius_cells: float = 5.0,
        points: np.ndarray | None = None,
        rng_seed: int = 0,
    ) -> None:
        """重铺初始场：种子由 :func:`solver.seed_arrays` 生成后整块上传。"""
        u, v = seed_arrays(self.nx, self.ny, mode, radius_cells, points, rng_seed)
        self.u_field.from_numpy(u)
        self.v_field.from_numpy(v)

    @ti.kernel
    def inject_kernel(self, cx: ti.i32, cy: ti.i32, radius: ti.i32):
        """在 ``(cx, cy)`` 盖一个方形核（周期性边界：越界部分绕回另一边）。"""
        span = 2 * radius + 1
        for di, dj in ti.ndrange(span, span):
            offset_i = di - radius
            offset_j = dj - radius
            i = (cx + offset_i + self.nx) % self.nx
            j = (cy + offset_j + self.ny) % self.ny
            self.u_field[i, j] = 0.5
            self.v_field[i, j] = 1.0

    def inject_seed(self, cx: int, cy: int, radius_cells: float = 4.0) -> None:
        """在格坐标 ``(cx, cy)`` 注一颗新种子（不重置其他区域）。"""
        self.inject_kernel(int(cx), int(cy), max(1, round(float(radius_cells))))

    # --- 物理 ---------------------------------------------------------------

    @ti.func
    def get_val(self, field: ti.template(), i: ti.i32, j: ti.i32) -> ti.f32:
        """周期性边界：索引取模。"""
        return field[(i + self.nx) % self.nx, (j + self.ny) % self.ny]

    @ti.func
    def laplacian(self, field: ti.template(), i: ti.i32, j: ti.i32) -> ti.f32:
        """九点各向同性拉普拉斯：十字 0.2、对角 0.05、中心 −1。"""
        cardinals = (
            self.get_val(field, i + 1, j)
            + self.get_val(field, i - 1, j)
            + self.get_val(field, i, j + 1)
            + self.get_val(field, i, j - 1)
        )
        diagonals = (
            self.get_val(field, i + 1, j + 1)
            + self.get_val(field, i - 1, j + 1)
            + self.get_val(field, i + 1, j - 1)
            + self.get_val(field, i - 1, j - 1)
        )
        return 0.2 * cardinals + 0.05 * diagonals - self.get_val(field, i, j)

    @ti.kernel
    def evolve_kernel(self):
        Du = self.s_Du[None]
        Dv = self.s_Dv[None]
        feed = self.s_F[None]
        kill = self.s_k[None]
        dt = self.s_dt[None]

        for i, j in self.u_field:
            u = self.u_field[i, j]
            v = self.v_field[i, j]
            reaction = u * v * v

            u_new = (
                u
                + (
                    Du * self.laplacian(self.u_field, i, j)
                    - reaction
                    + feed * (1.0 - u)
                )
                * dt
            )
            v_new = (
                v
                + (
                    Dv * self.laplacian(self.v_field, i, j)
                    + reaction
                    - (feed + kill) * v
                )
                * dt
            )

            self.u_next_field[i, j] = ti.max(0.0, ti.min(1.0, u_new))
            self.v_next_field[i, j] = ti.max(0.0, ti.min(1.0, v_new))

    @ti.kernel
    def update_kernel(self):
        for i, j in self.u_field:
            self.u_field[i, j] = self.u_next_field[i, j]
            self.v_field[i, j] = self.v_next_field[i, j]

    def step(self, substeps: int = 1) -> None:
        """推进 ``substeps`` 个时间步。"""
        for _ in range(max(1, int(substeps))):
            self.evolve_kernel()
            self.update_kernel()

    # --- 导出 ---------------------------------------------------------------

    def u_flat(self) -> np.ndarray:
        """基质浓度展平成顶点顺序（``index = i*ny + j``，C 序）。"""
        return self.u_field.to_numpy().ravel()

    def v_flat(self) -> np.ndarray:
        """活化剂浓度展平成顶点顺序（同上）。"""
        return self.v_field.to_numpy().ravel()

    def fields(self) -> tuple[np.ndarray, np.ndarray]:
        """``(u, v)`` 的展平副本。"""
        return self.u_flat(), self.v_flat()
