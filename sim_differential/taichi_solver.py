"""Taichi 后端的差分生长求解器（GPU kernel，实时）。

与 :mod:`sim_differential.solver` 的 NumPy 版**同一套方程**（那边的模块文档有完整说明），
差别只在实现：

* 物理子步在 GPU 上跑：每个节点并行算自己的位移 —— 斥力这里用**暴力扫描**所有节点
  （O(n²)，n = 1500 时单子步 2.25M 次距离判定，GPU 上是实时档；NumPy 版反而走空间哈希，
  因为 CPU 上 O(n²) 在几千节点时会到几十毫秒）；
* 速度（惯性低通）也放在字段里：``vel = vel·inertia + disp·(1−inertia)``，与 NumPy 版逐式
  一致；
* 拓扑生长仍在 Python 里（:func:`sim_differential.solver.grow_topology`）：分裂要重排整个
  数组，节点数每帧都会变，放在 numpy 里做最省事，一次也只有几千个 float。**速度场跟着
  一起重排**（新点取两边节点的均值），否则每帧分裂都会把惯性清零；
* 位置与速度存在 ``(capacity, 3)`` 的定长字段里，``n`` 由 Python 侧维护 —— 字段不用重建，
  容量就是面板上的「节点上限」。

Taichi 的 ``ti.init`` 一个进程只做一次：Blender 里热重载模块时复用已有的 runtime，
不会因为重复初始化而报错。取不到 GPU 时退到 CPU（``ti.cpu``），再失败就由
:func:`sim_differential.solver.create` 回退到 NumPy 后端。
"""

# 注意：**不能**加 ``from __future__ import annotations`` —— Taichi 直接读
# ``__annotations__`` 来判断 kernel 参数类型，PEP 563 的字符串注解会让它报
# ``TaichiSyntaxError: Invalid type annotation``。

import glob
import os
import sys

import numpy as np

from .solver import (
    DEFAULTS,
    _STEP_LIMIT,
    curvature_of,
    grow_topology,
    initial_loop,
    stretch_of,
)


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

__all__ = ["TaichiDifferentialGrowth", "ensure_init"]

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
class TaichiDifferentialGrowth:
    """差分生长求解器（Taichi 后端，GPU 优先）。"""

    backend = "taichi"

    def __init__(
        self,
        segments: int = 24,
        radius: float = 1.0,
        jitter: float = 0.05,
        seed: int = 0,
        capacity: int = 1500,
        params: dict | None = None,
    ):
        arch = ensure_init()
        self.segments = max(3, int(segments))
        self.radius = float(radius)
        self.jitter = float(jitter)
        self.seed = int(seed)
        self.capacity = max(self.segments, int(capacity))

        self.pos = ti.field(dtype=ti.f32, shape=(self.capacity, 3))
        self.vel = ti.field(dtype=ti.f32, shape=(self.capacity, 3))
        self.new_pos = ti.field(dtype=ti.f32, shape=(self.capacity, 3))
        self.new_vel = ti.field(dtype=ti.f32, shape=(self.capacity, 3))

        self.params = dict(DEFAULTS)
        self.params.update(self._clean(params or {}))

        self._initial = initial_loop(self.segments, self.radius, self.jitter, self.seed)
        self._rng = np.random.default_rng(self.seed + 7919)
        self.n = 0
        self.reset()

        #: 后端说明（面板读数用）。
        self.note = f"taichi/{arch}"

    # --- 参数 ---------------------------------------------------------------

    @staticmethod
    def _clean(params: dict) -> dict:
        """只收下已知参数并转成 float（面板可能多传 / 少传）。"""
        return {
            key: float(value)
            for key, value in params.items()
            if key in DEFAULTS and value is not None
        }

    def set_params(self, **kwargs) -> None:
        """就地更新物理参数（下一子步生效，不重置场、不改拓扑）。"""
        self.params.update(self._clean(kwargs))

    @property
    def _flatten(self) -> bool:
        return self.params["flatten"] > 0.5

    # --- 初始化 -------------------------------------------------------------

    def _upload(self, pos: np.ndarray, velocity: np.ndarray | None = None) -> None:
        """把 ``(n, 3)`` 的位置 / 速度写进字段；空余槽位填最后一个点（拓扑是定长的）。"""
        filled = np.repeat(pos[-1:], self.capacity, axis=0).astype(np.float32)
        filled[: len(pos)] = pos
        self.pos.from_numpy(np.ascontiguousarray(filled))
        if velocity is None:
            self.vel.fill(0.0)
        else:
            filled = np.repeat(velocity[-1:], self.capacity, axis=0).astype(np.float32)
            filled[: len(velocity)] = velocity
            self.vel.from_numpy(np.ascontiguousarray(filled))

    def reset(self) -> None:
        """回到初始圆环（节点数也回到 ``segments``，速度清零）。"""
        self.n = int(len(self._initial))
        self._upload(self._initial)
        self._rng = np.random.default_rng(self.seed + 7919)

    @property
    def node_count(self) -> int:
        return int(self.n)

    # --- 物理 ---------------------------------------------------------------

    @ti.kernel
    def relax_kernel(
        self,
        n: ti.i32,
        rep_radius: ti.f32,
        rep_weight: ti.f32,
        smooth_weight: ti.f32,
        bound_radius: ti.f32,
        bound_weight: ti.f32,
        flatten: ti.i32,
        max_step: ti.f32,
        inertia: ti.f32,
    ):
        """一个 PBD 子步：平滑 + 斥力 + 边界 → 速度低通 → ``new_pos`` / ``new_vel``。"""
        for i in range(n):
            p_i = ti.Vector([self.pos[i, 0], self.pos[i, 1], self.pos[i, 2]])
            prev_i = (i - 1 + n) % n
            next_i = (i + 1) % n
            mid = (
                ti.Vector(
                    [self.pos[prev_i, 0], self.pos[prev_i, 1], self.pos[prev_i, 2]]
                )
                + ti.Vector(
                    [self.pos[next_i, 0], self.pos[next_i, 1], self.pos[next_i, 2]]
                )
            ) * 0.5
            disp = (mid - p_i) * smooth_weight

            # 斥力：节点 j 在半径内就沿 (p_i − p_j) 推开，强度 ∝ 侵入深度
            repulsion = ti.Vector([0.0, 0.0, 0.0])
            for j in range(n):
                if i != j:
                    diff = p_i - ti.Vector(
                        [self.pos[j, 0], self.pos[j, 1], self.pos[j, 2]]
                    )
                    dist = diff.norm()
                    if dist < rep_radius and dist > 1e-6:
                        repulsion += diff / dist * (rep_radius - dist)
            disp += repulsion * rep_weight

            # 培养皿边界：超出半径就往回收（锁 Z 时只按 XY 距离算，且推力没有 Z 分量）
            if bound_radius > 0.0:
                # 先赋值再在分支里覆盖：Taichi 的分支作用域不会把新变量带出来
                dist_center = p_i.norm()
                if flatten == 1:
                    dist_center = ti.sqrt(p_i[0] * p_i[0] + p_i[1] * p_i[1])
                if dist_center > bound_radius:
                    direction = -p_i / ti.max(dist_center, 1e-6)
                    if flatten == 1:
                        direction[2] = 0.0
                    disp += direction * ((dist_center - bound_radius) * bound_weight)

            # 速度低通（惯性 0 = 纯 PBD）
            velocity = ti.Vector(
                [self.vel[i, 0], self.vel[i, 1], self.vel[i, 2]]
            ) * inertia + disp * (1.0 - inertia)

            # 单步位移上限
            magnitude = velocity.norm()
            if magnitude > max_step:
                velocity = velocity / magnitude * max_step

            if flatten == 1:
                self.new_pos[i, 0] = p_i[0] + velocity[0]
                self.new_pos[i, 1] = p_i[1] + velocity[1]
                self.new_pos[i, 2] = 0.0
            else:
                self.new_pos[i, 0] = p_i[0] + velocity[0]
                self.new_pos[i, 1] = p_i[1] + velocity[1]
                self.new_pos[i, 2] = p_i[2] + velocity[2]

            self.new_vel[i, 0] = velocity[0]
            self.new_vel[i, 1] = velocity[1]
            self.new_vel[i, 2] = 0.0 if flatten == 1 else velocity[2]

    @ti.kernel
    def copy_kernel(self, n: ti.i32):
        """``new_pos`` / ``new_vel`` → ``pos`` / ``vel``（逐值拷贝，避免读写竞争）。"""
        for i in range(n):
            for channel in ti.static(range(3)):
                self.pos[i, channel] = self.new_pos[i, channel]
                self.vel[i, channel] = self.new_vel[i, channel]

    def _relax(self, damping: float | None = None) -> None:
        if damping is None:
            damping = min(max(float(self.params["inertia"]), 0.0), 0.95)
        self.relax_kernel(
            self.n,
            max(float(self.params["rep_radius"]), 1e-6),
            float(self.params["rep_weight"]),
            float(self.params["smooth_weight"]),
            float(self.params["bound_radius"]),
            float(self.params["bound_weight"]),
            1 if self._flatten else 0,
            _STEP_LIMIT * max(float(self.params["rep_radius"]), 1e-6),
            damping,
        )
        self.copy_kernel(self.n)

    def _grow(self, quota: int | None = None) -> None:
        """拓扑生长：回读位置与速度 → numpy 切最长的边 → 写回字段。"""
        split = (
            max(1, int(self.params["growth_rate"])) if quota is None else int(quota)
        )
        if split <= 0:
            return
        current = self.pos.to_numpy()[: self.n]
        velocity = self.vel.to_numpy()[: self.n]
        grown, _chosen, (new_velocity,) = grow_topology(
            current,
            split_dist=float(self.params["split_dist"]),
            max_splits=split,
            capacity=self.capacity,
            noise=float(self.params["split_noise"]),
            flatten=self._flatten,
            rng=self._rng,
            extras=(velocity,),
        )
        if len(grown) != len(current):
            self.n = int(len(grown))
            self._upload(grown, new_velocity)

    def step(self, substeps: int = 1) -> None:
        """推进**一帧**：``substeps`` 个物理子步，帧尾做一次拓扑生长。

        惯性按帧给、换算成子步级阻尼（``inertia ** (1/子步)``），与 NumPy 版一致。
        """
        substeps = max(1, int(substeps))
        inertia = min(max(float(self.params["inertia"]), 0.0), 0.95)
        damping = inertia ** (1.0 / substeps)
        for _ in range(substeps):
            self._relax(damping)
        self._grow()

    # --- 导出 ---------------------------------------------------------------

    def positions(self) -> np.ndarray:
        """``(n, 3)`` 位置副本（顶点顺序 = 折线顺序）。"""
        return self.pos.to_numpy()[: self.n].copy()

    def stretch(self) -> np.ndarray:
        """``(n,)`` 逐点拉伸比（见 :func:`sim_differential.solver.stretch_of`）。"""
        return stretch_of(self.positions(), float(self.params["split_dist"]))

    def curvature(self) -> np.ndarray:
        """``(n,)`` 逐点转角 / π（见 :func:`sim_differential.solver.curvature_of`）。"""
        return curvature_of(self.positions())
