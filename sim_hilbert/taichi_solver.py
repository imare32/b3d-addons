"""Taichi 后端的弹簧质点求解器（GPU kernel，实时）。

与 :mod:`sim_hilbert.solver` 的 NumPy 版**同一套方程**（那边的模块文档有完整推导），
只是把每个子步拆成 ``_force_kernel`` / ``_advance_kernel`` 两个 kernel 跑在 GPU 上，
力的累加交给 ``self.force[a] += f`` 的原子加（NumPy 那边用 ``bincount`` 散射）。

一份 kernel 同时服务 3 阶（512 顶点 / 5 千根弹簧）和 4 阶（4096 顶点 / 4.7 万根弹簧）：
顶点数 / 弹簧数存在 0 维 i32 字段里，换拓扑只重新灌一次数据，不重新编译。

Taichi 的 ``ti.init`` 一个进程只做一次：Blender 里热重载模块时复用已有的 runtime，
不会因为重复初始化而报错。取不到 GPU 时退到 CPU（``ti.cpu``），再失败就由
:func:`sim_hilbert.solver.create` 回退到 NumPy 后端。
"""

# 注意：**不能**加 ``from __future__ import annotations`` —— Taichi 直接读
# ``__annotations__`` 来判断 kernel 参数类型，PEP 563 的字符串注解会让它报
# ``TaichiSyntaxError: Invalid type annotation``。

import glob
import os
import sys

import numpy as np

from .solver import DEFAULTS

__all__ = ["TaichiMassSpring", "ensure_init"]


def _local_module_roots() -> list:
    """第三方库的兜底查找路径（只在 ``import taichi`` 失败时才用）。

    正常启动（带 ``BLENDER_USER_RESOURCES``）时 Blender 会把
    ``$BLENDER_USER_RESOURCES/scripts/modules`` 加进 ``sys.path``，用不着这段逻辑；但
    ``--factory-startup`` 不读用户偏好，那一份路径也会丢 —— 后台测试 / CI 常这么启动,
    于是就会「明明装了 taichi 却回退 NumPy」。这里按环境变量与便携版目录再补一次。
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
class TaichiMassSpring:
    """弹簧质点求解器（Taichi 后端，GPU 优先）。"""

    backend = "taichi"

    def __init__(
        self,
        positions,
        spring_pairs,
        spring_lengths,
        params=None,
    ):
        arch = ensure_init()

        init_positions = np.ascontiguousarray(positions, np.float32)
        pairs = np.ascontiguousarray(spring_pairs, np.int32)
        rest = np.ascontiguousarray(spring_lengths, np.float32)
        self.n = int(init_positions.shape[0])
        self.m = int(pairs.shape[0])

        # 顶点参与的弹簧数（算平均应变用）：静态，灌一次
        incident = np.zeros(self.n, np.float32)
        np.add.at(incident, pairs[:, 0], 1.0)
        np.add.at(incident, pairs[:, 1], 1.0)

        self.pos0 = ti.Vector.field(3, dtype=ti.f32, shape=self.n)
        self.pos = ti.Vector.field(3, dtype=ti.f32, shape=self.n)
        self.vel = ti.Vector.field(3, dtype=ti.f32, shape=self.n)
        self.force = ti.Vector.field(3, dtype=ti.f32, shape=self.n)

        self.pairs = ti.Vector.field(2, dtype=ti.i32, shape=self.m)
        self.rest = ti.field(dtype=ti.f32, shape=self.m)
        self.incident = ti.field(dtype=ti.f32, shape=self.n)
        self.strain_sum = ti.field(dtype=ti.f32, shape=self.n)

        self.n_active = ti.field(dtype=ti.i32, shape=())
        self.m_active = ti.field(dtype=ti.i32, shape=())

        # 物理参数放 0 维字段：kernel 里按常量读，改参数不用重编译
        self.s_stiffness = ti.field(dtype=ti.f32, shape=())
        self.s_damping = ti.field(dtype=ti.f32, shape=())
        self.s_mass = ti.field(dtype=ti.f32, shape=())
        self.s_dt = ti.field(dtype=ti.f32, shape=())
        self.s_gravity = ti.field(dtype=ti.f32, shape=())
        self.s_ground = ti.field(dtype=ti.f32, shape=())
        self.s_restitution = ti.field(dtype=ti.f32, shape=())
        self.s_friction = ti.field(dtype=ti.f32, shape=())

        # 导出缓冲（复用，避免每帧新建数组）
        self._pos_buf = np.zeros((self.n, 3), np.float32)
        self._speed_buf = np.zeros(self.n, np.float32)
        self._strain_buf = np.zeros(self.n, np.float32)
        #: 导出缓冲里是不是刚算过（``speed()`` 先于 ``positions()`` 被调用时兜底）
        self._exported = False

        self.params = dict(DEFAULTS)
        self.set_params(**(params or {}))
        self._load_kernel(
            self.n, init_positions, self.m, pairs, rest, np.ascontiguousarray(incident)
        )

        #: 后端说明（面板读数用）。
        self.note = f"taichi/{arch}"

    # --- 参数 ---------------------------------------------------------------

    def set_params(self, **kwargs) -> None:
        """就地更新物理参数（下一子步生效，不重置状态）。"""
        for key, value in kwargs.items():
            if key in DEFAULTS and value is not None:
                self.params[key] = float(value)
        p = self.params
        self.s_stiffness[None] = p["stiffness"]
        self.s_damping[None] = p["damping"]
        self.s_mass[None] = max(p["mass"], 1e-9)
        self.s_dt[None] = p["dt"]
        self.s_gravity[None] = -abs(p["gravity"])
        self.s_ground[None] = p["ground_height"]
        self.s_restitution[None] = p["restitution"]
        self.s_friction[None] = 1.0 - p["friction"]

    # --- 灌数据 / 重置 -------------------------------------------------------

    @ti.kernel
    def _load_kernel(
        self,
        n: ti.i32,
        init_pos: ti.types.ndarray(),
        m: ti.i32,
        sp_pairs: ti.types.ndarray(),
        sp_rest: ti.types.ndarray(),
        incident: ti.types.ndarray(),
    ):
        self.n_active[None] = n
        self.m_active[None] = m
        for i in range(n):
            p = ti.Vector([init_pos[i, 0], init_pos[i, 1], init_pos[i, 2]])
            self.pos0[i] = p
            self.pos[i] = p
            self.vel[i] = ti.Vector([0.0, 0.0, 0.0])
            self.force[i] = ti.Vector([0.0, 0.0, 0.0])
            self.incident[i] = incident[i]
        for s in range(m):
            self.pairs[s] = ti.Vector([sp_pairs[s, 0], sp_pairs[s, 1]])
            self.rest[s] = sp_rest[s]

    def reset(self) -> None:
        """回到初始格子（速度归零）。"""
        self._reset_kernel()
        self._exported = False

    @ti.kernel
    def _reset_kernel(self):
        for i in range(self.n_active[None]):
            self.pos[i] = self.pos0[i]
            self.vel[i] = ti.Vector([0.0, 0.0, 0.0])
            self.force[i] = ti.Vector([0.0, 0.0, 0.0])

    # --- 物理 ---------------------------------------------------------------

    @ti.kernel
    def _substep_kernel(self):
        """一个子步 = 重力 + 弹簧力 → 积分 → 地面钳位。

        三个**顶层** range-for 在 Taichi 里是顺序执行的独立 offload（它们之间有屏障），
        所以"先算完所有力再积分"与拆成两个 kernel 完全等价；合成一个 kernel 只为了把
        每子步的 Python→CUDA 调用次数从 2 次压到 1 次（150 子步实测 20 ms → 13 ms）。
        """
        mass = self.s_mass[None]
        stiff = self.s_stiffness[None]
        damp = self.s_damping[None]
        gravity = self.s_gravity[None]

        for i in range(self.n_active[None]):
            self.force[i] = ti.Vector([0.0, 0.0, gravity]) * mass

        for s in range(self.m_active[None]):
            a = self.pairs[s][0]
            b = self.pairs[s][1]
            delta = self.pos[a] - self.pos[b]
            length = delta.norm()
            if length > 1e-6:
                direction = delta / length
                along = (self.vel[a] - self.vel[b]).dot(direction)
                magnitude = -stiff * (length - self.rest[s]) - damp * along
                f = magnitude * direction
                self.force[a] += f
                self.force[b] -= f

        dt = self.s_dt[None]
        inv_mass = 1.0 / mass
        ground = self.s_ground[None]
        restitution = self.s_restitution[None]
        friction = self.s_friction[None]

        for i in range(self.n_active[None]):
            self.vel[i] += self.force[i] * (dt * inv_mass)
            self.pos[i] += self.vel[i] * dt
            if self.pos[i][2] < ground:
                self.pos[i][2] = ground
                v = self.vel[i]
                self.vel[i] = ti.Vector(
                    [v[0] * friction, v[1] * friction, -v[2] * restitution]
                )

    def step(self, substeps: int = 1) -> None:
        """推进 ``substeps`` 个子步（每帧推进量 = 子步数 × ``dt``）。"""
        for _ in range(max(0, int(substeps))):
            self._substep_kernel()
        self._exported = False

    # --- 导出 ---------------------------------------------------------------

    @ti.kernel
    def _export_kernel(
        self, out_pos: ti.types.ndarray(), out_speed: ti.types.ndarray()
    ):
        for i in range(self.n_active[None]):
            p = self.pos[i]
            out_pos[i, 0] = p[0]
            out_pos[i, 1] = p[1]
            out_pos[i, 2] = p[2]
            out_speed[i] = self.vel[i].norm()

    @ti.kernel
    def _strain_kernel(self, out: ti.types.ndarray()):
        for i in range(self.n_active[None]):
            self.strain_sum[i] = 0.0
        for s in range(self.m_active[None]):
            a = self.pairs[s][0]
            b = self.pairs[s][1]
            rest = self.rest[s]
            value = ti.abs((self.pos[a] - self.pos[b]).norm() - rest) / ti.max(
                rest, 1e-6
            )
            ti.atomic_add(self.strain_sum[a], value)
            ti.atomic_add(self.strain_sum[b], value)
        for i in range(self.n_active[None]):
            out[i] = self.strain_sum[i] / ti.max(self.incident[i], 1.0)

    def positions(self) -> np.ndarray:
        """当前顶点位置 ``(N,3)`` float32（**内部缓冲**，每帧覆盖，别长期持有）。

        顺手把速度大小也刷进 ``_speed_buf``：一帧只需要一次导出 kernel。
        """
        self._export_kernel(self._pos_buf, self._speed_buf)
        ti.sync()
        self._exported = True
        return self._pos_buf

    def speed(self) -> np.ndarray:
        """逐顶点速度大小 ``(N,)`` float32（内部缓冲，随 :meth:`positions` 一起刷新）。"""
        if not self._exported:
            self.positions()
        return self._speed_buf

    def strain(self) -> np.ndarray:
        """逐顶点平均应变 ``|L − L0| / L0``（内部缓冲）。"""
        self._strain_kernel(self._strain_buf)
        ti.sync()
        return self._strain_buf
