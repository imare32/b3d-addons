"""Taichi 后端的生命游戏（GPU kernel，实时）。

与 :mod:`sim_game_of_life.solver` 的 NumPy 版**同一套规则**，只是把每一代丢给 kernel：

* 邻域按 ``(capacity, MAX_NEIGHBORS)`` 的索引表上传，``-1`` 是填充位；
* 规则压成一张 ``(2, MAX_NEIGHBORS+1)`` 的查表（``[0]`` 出生、``[1]`` 存活），
  kernel 里只做一次查表 —— 换规则不用重新编译；
* 容量按需扩容（``_reserve``），小网格改大网格只重传数据，不重建整个后端。

Taichi 的 ``ti.init`` 一个进程只做一次：Blender 里热重载模块时复用已有的 runtime，
不会因为重复初始化而报错。取不到 GPU 时退到 CPU（``ti.cpu``），再失败就由
:func:`sim_game_of_life.solver.create` 回退到 NumPy 后端。
"""

# 注意：**不能**加 ``from __future__ import annotations`` —— Taichi 直接读
# ``__annotations__`` 来判断 kernel 参数类型，PEP 563 的字符串注解会让它报
# ``TaichiSyntaxError: Invalid type annotation``。

import glob
import os
import sys

import numpy as np

from .solver import MAX_NEIGHBORS


def _local_module_roots() -> list[str]:
    """第三方库的兜底查找路径（只在 ``import taichi`` 失败时才用）。

    正常启动（带 ``BLENDER_USER_RESOURCES``）时 Blender 会把
    ``$BLENDER_USER_RESOURCES/scripts/modules`` 加进 ``sys.path``；但
    ``--factory-startup`` 不读用户偏好，那一份路径也会丢 —— 后台测试 / CI 常这么启动，
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

__all__ = ["TaichiLife", "ensure_init"]

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
class TaichiLife:
    """面邻域生命游戏求解器（Taichi 后端，GPU 优先）。"""

    backend = "taichi"

    def __init__(self, n_faces: int = 1, birth: int = 8, survive: int = 12):
        # 形参缺省与 :data:`sim_game_of_life.solver.DEFAULT_BIRTH` 一致（康威 B3/S23）；
        # 这里写字面量是因为 solver 那边的缺省值要等 RULES 定义完才算得出来。
        arch = ensure_init()
        #: 后端说明（面板读数用）。
        self.note = f"taichi/{arch}"
        self.birth = int(birth)
        self.survive = int(survive)
        self._n = 0
        self._capacity = 0
        self._reserve(max(int(n_faces), 1))

    # --- 字段 ---------------------------------------------------------------

    def _reserve(self, capacity: int) -> None:
        """扩容到至少 ``capacity`` 个细胞（只增不减，避免反复分配）。"""
        if capacity <= self._capacity:
            return
        self._capacity = int(capacity)
        self._num = ti.field(ti.i32, shape=())
        self._state = ti.field(ti.f32, shape=self._capacity)
        self._next = ti.field(ti.f32, shape=self._capacity)
        self._neighbors = ti.field(ti.i32, shape=(self._capacity, MAX_NEIGHBORS))
        self._counts = ti.field(ti.i32, shape=self._capacity)
        self._table = ti.field(ti.i32, shape=(2, MAX_NEIGHBORS + 1))
        self._num[None] = self._n
        self._upload_rule()

    def _upload_rule(self) -> None:
        """规则 → 查表：``table[0, n]`` / ``table[1, n]`` 是该活邻数下是否出生 / 存活。"""
        table = np.zeros((2, MAX_NEIGHBORS + 1), np.int32)
        for count in range(MAX_NEIGHBORS + 1):
            table[0, count] = (self.birth >> count) & 1
            table[1, count] = (self.survive >> count) & 1
        self._table.from_numpy(table)

    # --- 拓扑 ---------------------------------------------------------------

    @property
    def count(self) -> int:
        """细胞（面）数量。"""
        return self._n

    def load_topology(self, neighbors: np.ndarray, counts: np.ndarray) -> None:
        """载入面邻域（与 NumPy 后端同接口）。

        ``neighbors`` 的列数可以比 :data:`~sim_game_of_life.solver.MAX_NEIGHBORS` 少
        （例如手写的 8 邻域测试），多出来的列会被截断。
        """
        table = np.asarray(neighbors, np.int32)
        lengths = np.asarray(counts, np.int32).ravel()
        if table.ndim != 2:
            raise ValueError(f"neighbors 必须是二维数组，收到 {table.shape}")
        if lengths.size != table.shape[0]:
            raise ValueError(
                f"counts 长度 {lengths.size} 与面数 {table.shape[0]} 不一致"
            )

        n = int(lengths.size)
        self._reserve(max(n, 1))
        self._n = n
        self._num[None] = n

        width = min(table.shape[1], MAX_NEIGHBORS)
        padded_neighbors = np.full((self._capacity, MAX_NEIGHBORS), -1, np.int32)
        padded_neighbors[:n, :width] = table[:, :width]
        self._neighbors.from_numpy(padded_neighbors)

        padded_counts = np.zeros(self._capacity, np.int32)
        padded_counts[:n] = np.minimum(lengths, MAX_NEIGHBORS)
        self._counts.from_numpy(padded_counts)

        self._state.from_numpy(np.zeros(self._capacity, np.float32))
        self._next.from_numpy(np.zeros(self._capacity, np.float32))

    def set_rule(self, birth: int, survive: int) -> bool:
        """换规则；返回是否真的变了（没变就不重算下一代）。"""
        changed = (int(birth), int(survive)) != (self.birth, self.survive)
        self.birth, self.survive = int(birth), int(survive)
        if changed:
            self._upload_rule()
            self._step_kernel()
        return changed

    # --- 状态 ---------------------------------------------------------------

    def set_state(self, states: np.ndarray) -> None:
        """铺初始状态（``> 0.5`` 算活），并顺带算出下一代。"""
        values = np.asarray(states, np.float32).ravel()[: self._n]
        buffer = np.zeros(self._capacity, np.float32)
        buffer[: values.size] = (values > 0.5).astype(np.float32)
        self._state.from_numpy(buffer)
        self._step_kernel()

    def state_flat(self) -> np.ndarray:
        """第 g 代状态（0 / 1，按面展平）。"""
        return self._state.to_numpy()[: self._n].astype(np.float32)

    def next_flat(self) -> np.ndarray:
        """第 g+1 代状态（同上）。"""
        return self._next.to_numpy()[: self._n].astype(np.float32)

    def step(self, steps: int = 1) -> None:
        """往前推 ``steps`` 代。"""
        for _ in range(max(0, int(steps))):
            self._advance_kernel()
            self._step_kernel()

    # --- kernel -------------------------------------------------------------

    @ti.kernel
    def _step_kernel(self):
        """按规则从 ``_state`` 算出 ``_next``（每个面一个线程）。"""
        n = self._num[None]
        for i in range(n):
            alive = 0
            count = self._counts[i]
            for j in range(count):
                neighbor = self._neighbors[i, j]
                if neighbor >= 0 and self._state[neighbor] > 0.5:
                    alive += 1
            if self._state[i] > 0.5:
                self._next[i] = ti.cast(self._table[1, alive], ti.f32)
            else:
                self._next[i] = ti.cast(self._table[0, alive], ti.f32)

    @ti.kernel
    def _advance_kernel(self):
        """把 ``_next`` 提到 ``_state``（推进一代）。"""
        n = self._num[None]
        for i in range(n):
            self._state[i] = self._next[i]
