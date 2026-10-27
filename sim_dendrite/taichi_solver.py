"""Taichi 后端的 Warren 相场求解器（GPU kernel，实时）。

与 :mod:`sim_dendrite.solver` 的 NumPy 版**同一套方程**（那边的模块文档有完整推导），
只是把每个子步拆成 ``evolve_kernel`` / ``update_kernel`` 两个 kernel 跑在 GPU 上：

* 每格算四个面的各向异性通量（``arctan2`` + ``tan`` 的有理形式）再取散度；
* 温度场用五点拉普拉斯 + ``∂φ/∂t`` 作为潜热源；
* 边界靠 ``get_val`` 的索引钳位表达零通量 Neumann（与参考实现一致）。

字段按 ``[i, j]`` 索引并直接以 C 序导出，编号与 ``mesh.grid_geometry`` 的顶点顺序
一致（``index = i*ny + j``）。

Taichi 的 ``ti.init`` 一个进程只做一次：Blender 里热重载模块时复用已有的 runtime，
不会因为重复初始化而报错。取不到 GPU 时退到 CPU（``ti.cpu``），再失败就由
:func:`sim_dendrite.solver.create` 回退到 NumPy 后端。
"""

# 注意：**不能**加 ``from __future__ import annotations`` —— Taichi 直接读
# ``__annotations__`` 来判断 kernel 参数类型，PEP 563 的字符串注解会让它报
# ``TaichiSyntaxError: Invalid type annotation``。

import glob
import os
import sys

import numpy as np

from .solver import DEFAULTS


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

__all__ = ["TaichiPhaseField", "ensure_init"]

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
class TaichiPhaseField:
    """二维 Warren 相场求解器（Taichi 后端，GPU 优先）。"""

    backend = "taichi"

    def __init__(
        self,
        nx: int = 250,
        ny: int = 250,
        dx: float = 0.025,
        dy: float = 0.025,
        params: dict | None = None,
    ):
        arch = ensure_init()
        self.nx = int(nx)
        self.ny = int(ny)
        self.dx = float(dx)
        self.dy = float(dy)

        shape = (self.nx, self.ny)
        self.phi_field = ti.field(dtype=ti.f32, shape=shape)
        self.dT_field = ti.field(dtype=ti.f32, shape=shape)
        self.new_phi_field = ti.field(dtype=ti.f32, shape=shape)
        self.new_dT_field = ti.field(dtype=ti.f32, shape=shape)

        # 物理参数放 0 维字段：kernel 里按常量读，改参数不用重编译
        self.s_DT_heat = ti.field(dtype=ti.f32, shape=())
        self.s_alpha = ti.field(dtype=ti.f32, shape=())
        self.s_c_ani = ti.field(dtype=ti.f32, shape=())
        self.s_N_sym = ti.field(dtype=ti.f32, shape=())
        self.s_theta_off = ti.field(dtype=ti.f32, shape=())
        self.s_tau = ti.field(dtype=ti.f32, shape=())
        self.s_kappa1 = ti.field(dtype=ti.f32, shape=())
        self.s_kappa2 = ti.field(dtype=ti.f32, shape=())
        self.s_dt = ti.field(dtype=ti.f32, shape=())

        self.params = dict(DEFAULTS)
        self.set_params(**(params or {}))

        #: 后端说明（面板读数用）。
        self.note = f"taichi/{arch}"

    # --- 参数 ---------------------------------------------------------------

    def set_params(self, **kwargs) -> None:
        """就地更新物理参数（下一子步生效，不重置场）。"""
        for key, value in kwargs.items():
            if key in DEFAULTS and value is not None:
                self.params[key] = float(value)
        self.apply_params(
            self.params["DT_heat"],
            self.params["alpha"],
            self.params["c_ani"],
            self.params["N_sym"],
            self.params["theta_off"],
            self.params["tau"],
            self.params["kappa1"],
            self.params["kappa2"],
            self.params["dt"],
        )

    @ti.kernel
    def apply_params(
        self,
        DT_heat: ti.f32,
        alpha: ti.f32,
        c_ani: ti.f32,
        N_sym: ti.f32,
        theta_off: ti.f32,
        tau: ti.f32,
        kappa1: ti.f32,
        kappa2: ti.f32,
        dt: ti.f32,
    ):
        self.s_DT_heat[None] = DT_heat
        self.s_alpha[None] = alpha
        self.s_c_ani[None] = c_ani
        self.s_N_sym[None] = N_sym
        self.s_theta_off[None] = theta_off
        self.s_tau[None] = tau
        self.s_kappa1[None] = kappa1
        self.s_kappa2[None] = kappa2
        self.s_dt[None] = dt

    # --- 初始化 -------------------------------------------------------------

    @ti.func
    def get_val(self, field: ti.template(), i: ti.i32, j: ti.i32) -> ti.f32:
        """零通量边界：越界索引钳到域内（等价于边界处梯度取 0）。"""
        return field[
            ti.max(0, ti.min(self.nx - 1, i)), ti.max(0, ti.min(self.ny - 1, j))
        ]

    @ti.kernel
    def initialize_kernel(
        self,
        seed_radius: ti.f32,
        center_x: ti.f32,
        center_y: ti.f32,
        initial_dT: ti.f32,
    ):
        radius_sq = seed_radius**2
        for i, j in self.phi_field:
            dx = i * self.dx - center_x
            dy = j * self.dy - center_y
            self.phi_field[i, j] = 1.0 if dx * dx + dy * dy < radius_sq else 0.0
            self.dT_field[i, j] = initial_dT

    @ti.kernel
    def seed_from_points_kernel(
        self, points: ti.types.ndarray(dtype=ti.f32, ndim=2), radius_cells: ti.i32
    ):
        """在归一化点位 ``[-1, 1]`` 上各放一个半径 ``radius_cells`` 的圆核。"""
        n_points = points.shape[0]
        for p_idx in range(n_points):
            ci = int((points[p_idx, 0] + 1.0) * 0.5 * self.nx)
            cj = int((points[p_idx, 1] + 1.0) * 0.5 * self.ny)
            for i in range(
                ti.max(0, ci - radius_cells), ti.min(self.nx, ci + radius_cells + 1)
            ):
                for j in range(
                    ti.max(0, cj - radius_cells), ti.min(self.ny, cj + radius_cells + 1)
                ):
                    if (i - ci) ** 2 + (j - cj) ** 2 <= radius_cells**2:
                        self.phi_field[i, j] = 1.0

    def initialize(
        self,
        seed_radius_units: float = 5.0,
        center: tuple[float, float] | None = None,
        seed_points: np.ndarray | None = None,
        initial_dT: float = -0.5,
    ) -> None:
        """铺初始场：中心一个圆形晶核（可选再加若干点位晶核），液体整体过冷。"""
        if center is None:
            center_x = self.nx * self.dx * 0.5
            center_y = self.ny * self.dy * 0.5
        else:
            center_x, center_y = float(center[0]), float(center[1])

        self.initialize_kernel(
            self.dx * float(seed_radius_units),
            center_x,
            center_y,
            float(initial_dT),
        )

        if seed_points is not None and len(seed_points) > 0:
            points = np.ascontiguousarray(seed_points, dtype=np.float32).reshape(-1, 2)
            self.seed_from_points_kernel(points, max(1, int(seed_radius_units)))

    # --- 物理 ---------------------------------------------------------------

    @ti.func
    def anisotropy(self, grad_x: ti.f32, grad_y: ti.f32) -> ti.types.vector(2, ti.f32):
        """``(Dxx, off)``：``Dxx = M(1+cβ)``，``off = M·c·∂_ψβ``。"""
        n_sym = self.s_N_sym[None]
        psi = self.s_theta_off[None] + ti.atan2(grad_y, grad_x)
        phi_t = ti.tan(n_sym * psi * 0.5)
        q = phi_t * phi_t

        beta = -1.0
        dbeta = 0.0
        # tan 在 ψ = π/N 处发散：q 超过 1e10 就当 β = −1、∂_ψβ = 0（与参考实现同判据）
        if q <= 1e10:
            beta = (1.0 - q) / (1.0 + q)
            dbeta = -n_sym * 2.0 * phi_t / (1.0 + q)

        c_ani = self.s_c_ani[None]
        stiffness = self.s_alpha[None] ** 2 * (1.0 + c_ani * beta)
        return ti.Vector(
            [stiffness * (1.0 + c_ani * beta), stiffness * (c_ani * dbeta)]
        )

    @ti.func
    def flux_x(self, i: ti.i32, j: ti.i32) -> ti.f32:
        """格 ``(i, j)`` 右表面 ``(i+0.5, j)`` 上的通量。"""
        grad_x = (
            self.get_val(self.phi_field, i + 1, j) - self.get_val(self.phi_field, i, j)
        ) / self.dx
        grad_y = 0.5 * (
            (
                self.get_val(self.phi_field, i, j + 1)
                - self.get_val(self.phi_field, i, j - 1)
            )
            / (2.0 * self.dy)
            + (
                self.get_val(self.phi_field, i + 1, j + 1)
                - self.get_val(self.phi_field, i + 1, j - 1)
            )
            / (2.0 * self.dy)
        )
        diag, off = self.anisotropy(grad_x, grad_y)
        return diag * grad_x - off * grad_y

    @ti.func
    def flux_y(self, i: ti.i32, j: ti.i32) -> ti.f32:
        """格 ``(i, j)`` 上表面 ``(i, j+0.5)`` 上的通量。"""
        grad_y = (
            self.get_val(self.phi_field, i, j + 1) - self.get_val(self.phi_field, i, j)
        ) / self.dy
        grad_x = 0.5 * (
            (
                self.get_val(self.phi_field, i + 1, j)
                - self.get_val(self.phi_field, i - 1, j)
            )
            / (2.0 * self.dx)
            + (
                self.get_val(self.phi_field, i + 1, j + 1)
                - self.get_val(self.phi_field, i - 1, j + 1)
            )
            / (2.0 * self.dx)
        )
        diag, off = self.anisotropy(grad_x, grad_y)
        return off * grad_x + diag * grad_y

    @ti.kernel
    def evolve_kernel(self):
        dt = self.s_dt[None]
        inv_tau = 1.0 / self.s_tau[None]
        k1_over_pi = self.s_kappa1[None] / np.pi
        k2 = self.s_kappa2[None]

        for i, j in self.phi_field:
            div_flux = (self.flux_x(i, j) - self.flux_x(i - 1, j)) / self.dx + (
                self.flux_y(i, j) - self.flux_y(i, j - 1)
            ) / self.dy

            p = self.phi_field[i, j]
            t = self.dT_field[i, j]

            driving = p - 0.5 - k1_over_pi * ti.atan2(k2 * t, 1.0)
            dphi_dt = (div_flux + p * (1.0 - p) * driving) * inv_tau
            self.new_phi_field[i, j] = ti.max(0.0, ti.min(1.0, p + dphi_dt * dt))

            lap_T = (
                self.get_val(self.dT_field, i + 1, j)
                + self.get_val(self.dT_field, i - 1, j)
                + self.get_val(self.dT_field, i, j + 1)
                + self.get_val(self.dT_field, i, j - 1)
                - 4.0 * t
            ) / (self.dx**2)
            self.new_dT_field[i, j] = t + (self.s_DT_heat[None] * lap_T + dphi_dt) * dt

    @ti.kernel
    def update_kernel(self):
        for i, j in self.phi_field:
            self.phi_field[i, j] = self.new_phi_field[i, j]
            self.dT_field[i, j] = self.new_dT_field[i, j]

    def step(self, substeps: int = 1) -> None:
        """推进 ``substeps`` 个时间步。"""
        for _ in range(max(1, int(substeps))):
            self.evolve_kernel()
            self.update_kernel()

    # --- 导出 ---------------------------------------------------------------

    def phi_flat(self) -> np.ndarray:
        """相场展平成顶点顺序（``index = i*ny + j``，C 序）。"""
        return self.phi_field.to_numpy().ravel()

    def dT_flat(self) -> np.ndarray:
        """过冷度展平成顶点顺序（同上）。"""
        return self.dT_field.to_numpy().ravel()

    def fields(self) -> tuple[np.ndarray, np.ndarray]:
        """``(phi, dT)`` 的展平副本。"""
        return self.phi_flat(), self.dT_flat()
