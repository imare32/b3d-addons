"""差分生长（differential growth）求解器：位置约束（PBD）+ 拓扑分裂。

模型和参考实现 ``.ref/differential_growth.py``（Taichi）一致 —— 一条**闭合折线**，每个
子步把所有节点往三个目标推，然后把位移一次性加上去::

    Δᵢ = w_s·(½(pᵢ₋₁ + pᵢ₊₁) − pᵢ)                      平滑（拉普拉斯，收紧曲线）
       + w_r·Σ_{|pᵢ−pⱼ|<R} (pᵢ−pⱼ)/|pᵢ−pⱼ|·(R − |pᵢ−pⱼ|)   斥力（撑开相邻节点）
       + w_b·(−p̂ᵢ)·max(0, |pᵢ| − R_b)                      培养皿边界（往回收）
    vᵢ ← vᵢ·inertia + Δᵢ·(1 − inertia)                  速度低通（惯性，可关）
    pᵢ ← pᵢ + clamp(vᵢ, 单步上限)

**拓扑生长**：每帧结尾，把所有长于 ``split_dist`` 的边按长度降序切开（每条边插入一个弦
中点，每帧最多 ``growth_rate`` 条，节点数不超过 ``capacity``）。

生长必须**整帧一次性切**，不能摊到各子步之间：切完一刀若立刻弛豫，新节点的两条子边会
被斥力重新拉长、又是下一条"最长的边"，分裂就集中到同一片区域，最后长成一个单侧大褶
（实测：摊到子步分 120 帧后，一半周向是光滑的圆弧，另一半全挤在一起）。

于是差分生长就出来了：斥力把相邻节点互相推开 → 边长被重新拉长 → 下一帧又被切开 → 弧长
不断增长；边界约束不许它整体外扩，多出来的长度只能自己打褶 —— 折痕越挤越密，就是那团
脑回状的图案。没有斥力只有分裂的话，曲线只是一直被均匀细分，什么都不会发生。

和参考实现的差别（都是工程性的，观感一致）：

* **斥力按空间哈希取邻居**（NumPy 后端）：参考实现是 O(n²) 暴力扫描，本实现按
  ``rep_radius`` 分格、只查 3ᵈ 邻域，n = 3000 时单子步从"几十毫秒"降到"亚毫秒"，
  且结果是精确的（斥力截断半径 = 格边长，邻域查全了）；
* **单步位移改成截断而不是分段**：参考实现超过 ``R/2`` 就先放大到 ``R/2``、再突然改成
  ``R/10``（不连续）；这里统一钳到 ``0.25·R``；
* **速度低通（惯性）**：参考实现是纯位置约束，每个子步的位移直接叠到位置上，节点轨迹
  逐帧变向（实测"加速度 ≈ 速度"，也就是抖动）；这里让速度过一层一阶低通（``inertia``
  按**帧**计，0 = 回到参考实现的行为），运动变成有连续性的流；
* **分层**：几何（初始节点数 / 半径 / 抖动 / 种子 / 容量）+ 物理（斥力 / 平滑 / 边界 /
  分裂距离 / 生长速率 / 惯性 / 是否锁 Z）分开，前者重建、后者实时生效（见 :mod:`props`）。

本模块**不 import bpy**（只用 numpy），可以脱离 Blender 单独跑::

    python -c "from sim_differential import solver; g=solver.create(); [g.step(8) for _ in range(50)]; print(g.node_count)"

Taichi 后端在 :mod:`sim_differential.taichi_solver`（同一套方程，GPU kernel），两者 API
相同，由 :func:`create` 按需选择。
"""

from __future__ import annotations

import numpy as np

__all__ = [
    "BACKEND_AUTO",
    "BACKEND_NUMPY",
    "BACKEND_TAICHI",
    "DEFAULTS",
    "PARAM_KEYS",
    "NumPyDifferentialGrowth",
    "create",
    "curvature_of",
    "edge_lengths",
    "grow_topology",
    "initial_loop",
    "stretch_of",
    "taichi_available",
]

#: ``create`` 的后端选择。
BACKEND_AUTO = "AUTO"
BACKEND_TAICHI = "TAICHI"
BACKEND_NUMPY = "NUMPY"

#: 默认物理参数（与参考实现同一量级，但整体缩小到"半径 1 的培养皿"里）。
DEFAULTS = {
    "rep_radius": 0.10,  # 斥力半径 R：节点间距撑到它附近就打住
    "rep_weight": 0.20,  # 斥力权重
    "smooth_weight": 0.35,  # 平滑权重（越大曲线越"紧"、折痕越圆）
    "bound_radius": 2.0,  # 培养皿半径 R_b（0 = 不设边界）
    "bound_weight": 0.50,  # 边界刚性
    "split_dist": 0.04,  # 边长超过它就切开
    "split_noise": 0.10,  # 切口随机偏移（占分裂距离）：没有它，早期就锁死在单侧生长
    "growth_rate": 8.0,  # 每帧最多切几条边
    "inertia": 0.0,  # 速度低通（按**帧**计）：0 = 纯位置约束（参考实现）；>0 更顺但会偏科
    "flatten": 1.0,  # 1 = 严格锁 Z = 0（纯 2D）
}

PARAM_KEYS = tuple(DEFAULTS)

#: 单步位移上限占斥力半径的比例：纯工程常数，不进面板（改它只会让物理更容易炸）。
_STEP_LIMIT = 0.25


# --- 拓扑 / 几何工具（两个后端共用，纯 numpy） -------------------------------


def initial_loop(
    segments: int = 24,
    radius: float = 1.0,
    jitter: float = 0.05,
    seed: int = 0,
) -> np.ndarray:
    """初始闭合折线：``segments`` 个点均匀铺在半径 ``radius`` 的圆上，加一点抖动。

    抖动是必要的 —— 完美的圆在对称的力场里只会均匀膨胀，永远不打褶。抖动幅度是
    ``jitter·radius``（x/y/z 各一份，锁 Z 时 z 每步都会被清零，不影响 2D）。
    """
    count = max(3, int(segments))
    angles = 2.0 * np.pi * np.arange(count, dtype=np.float32) / count
    pos = np.zeros((count, 3), np.float32)
    pos[:, 0] = np.cos(angles) * float(radius)
    pos[:, 1] = np.sin(angles) * float(radius)

    amount = float(jitter) * abs(float(radius))
    if amount > 0.0:
        rng = np.random.default_rng(int(seed))
        pos += rng.uniform(-amount, amount, pos.shape).astype(np.float32)
    return pos


def edge_lengths(pos: np.ndarray) -> np.ndarray:
    """每条边的长度（边 ``i`` = 节点 ``i`` → ``i+1``，闭合）。"""
    return np.linalg.norm(np.roll(pos, -1, axis=0) - pos, axis=1)


def grow_topology(
    pos: np.ndarray,
    split_dist: float,
    max_splits: int,
    capacity: int,
    noise: float = 0.0,
    flatten: bool = True,
    rng: np.random.Generator | None = None,
    extras: tuple = (),
) -> tuple:
    """把最长的若干条边从中间切开（每条边插一个点）。

    返回 ``(新位置, 被切开的边索引, 跟着插值的 extras)``：

    * ``max_splits`` 同时被剩余容量钳住，节点数永远不会超过 ``capacity``；
    * 新点放在**弦中点**上，缺省**不加随机偏移** —— 偏移会在每个切口上留一个折角，
      而随机性早就由初始抖动与"每次切哪几条边"提供了；
    * ``extras``（每项形状 ``(n, k)``）跟着点数一起长：插进去的那一行的值取两边节点的
      均值。速度场就靠它保持连续，不然每帧分裂都会把惯性清掉。
    """
    count = len(pos)
    room = int(capacity) - int(count)
    if room <= 0 or count < 2:
        return pos, np.empty(0, np.int64), tuple(np.asarray(v) for v in extras)

    lengths = edge_lengths(pos)
    candidates = np.flatnonzero(lengths > float(split_dist))
    if candidates.size == 0:
        return pos, np.empty(0, np.int64), tuple(np.asarray(v) for v in extras)

    take = min(int(max_splits), int(room), int(candidates.size))
    chosen = candidates[np.argsort(lengths[candidates])[::-1][:take]]
    offset = max(float(noise), 0.0) * float(split_dist)

    rng = rng or np.random.default_rng()
    keep = np.zeros(count, bool)
    keep[chosen] = True

    merged = []
    grown = [[] for _ in extras]
    for index in range(count):
        merged.append(pos[index])
        for slot, values in enumerate(extras):
            grown[slot].append(values[index])
        if not keep[index]:
            continue
        middle = 0.5 * (pos[index] + pos[(index + 1) % count])
        if offset > 0.0:
            jitter = rng.uniform(-offset, offset, 3).astype(np.float32)
            if flatten:
                jitter[2] = 0.0
            middle = middle + jitter
        merged.append(middle)
        for slot, values in enumerate(extras):
            grown[slot].append(
                0.5 * (values[index] + values[(index + 1) % count])
            )

    return (
        np.asarray(merged, np.float32),
        chosen.astype(np.int64),
        tuple(np.asarray(values, np.float32) for values in grown),
    )


def stretch_of(pos: np.ndarray, split_dist: float) -> np.ndarray:
    """逐节点拉伸比：相邻两条边长度的均值 / ``split_dist``（1.0 = 马上要被切开）。"""
    lengths = edge_lengths(pos)
    average = 0.5 * (lengths + np.roll(lengths, 1))
    return (average / max(float(split_dist), 1e-9)).astype(np.float32)


def curvature_of(pos: np.ndarray) -> np.ndarray:
    """逐节点转角 / π ∈ ``[0, 1]``：折痕最弯的地方接近 1，直段接近 0。"""
    incoming = pos - np.roll(pos, 1, axis=0)
    outgoing = np.roll(pos, -1, axis=0) - pos
    cross = np.cross(incoming, outgoing)
    dot = np.einsum("ij,ij->i", incoming, outgoing)
    angle = np.arctan2(np.linalg.norm(cross, axis=1), dot)
    return (angle / np.pi).astype(np.float32)


# --- NumPy 后端 -------------------------------------------------------------


class NumPyDifferentialGrowth:
    """差分生长求解器（纯 NumPy 后端，斥力走空间哈希）。"""

    backend = "numpy"

    def __init__(
        self,
        segments: int = 24,
        radius: float = 1.0,
        jitter: float = 0.05,
        seed: int = 0,
        capacity: int = 1500,
        params: dict | None = None,
    ):
        self.segments = max(3, int(segments))
        self.radius = float(radius)
        self.jitter = float(jitter)
        self.seed = int(seed)
        self.capacity = max(self.segments, int(capacity))

        self.params = dict(DEFAULTS)
        self.params.update(self._clean(params or {}))

        self._initial = initial_loop(self.segments, self.radius, self.jitter, self.seed)
        self._pos = self._initial.copy()
        #: 速度（子步之间的位置增量），惯性就是它的一阶低通；空数组在 reset 里补
        self._vel = np.zeros_like(self._pos)
        self._rng = np.random.default_rng(self.seed + 7919)

        #: 后端说明（面板读数用；NumPy 后端没有可报告的回退原因）。
        self.note = "numpy"

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

    def reset(self) -> None:
        """回到初始圆环（节点数也回到 ``segments``）。"""
        self._pos = self._initial.copy()
        self._vel = np.zeros_like(self._pos)
        self._rng = np.random.default_rng(self.seed + 7919)

    @property
    def node_count(self) -> int:
        return int(self._pos.shape[0])

    # --- 物理 ---------------------------------------------------------------

    def _repulsion(self, pos: np.ndarray) -> np.ndarray:
        """斥力位移：对每个节点的 3ᵈ 邻域内所有节点求和（空间哈希，精确到截断半径）。"""
        radius = max(float(self.params["rep_radius"]), 1e-6)
        count = len(pos)
        dims = 2 if self._flatten else 3
        # 格边长 = 斥力半径：只要查 3ᵈ 邻域就一定覆盖整个截断球
        cells = np.floor(pos[:, :dims] / radius).astype(np.int64)
        low = cells.min(axis=0)
        extent = (cells.max(axis=0) - low) + 2  # 两端各留一格空位，越界偏移不会串到别的格子

        key = np.zeros(count, np.int64)
        for axis in range(dims):
            key = key * int(extent[axis]) + (cells[:, axis] - int(low[axis]) + 1)

        order = np.argsort(key, kind="stable")
        sorted_keys = key[order]

        index_i = []
        index_j = []
        for offset in _offsets(dims):
            delta = 0
            for axis in range(dims):
                delta = delta * int(extent[axis]) + int(offset[axis])
            target = key + delta
            left = np.searchsorted(sorted_keys, target, side="left")
            right = np.searchsorted(sorted_keys, target, side="right")
            counts = right - left
            total = int(counts.sum())
            if total == 0:
                continue
            starts = np.repeat(left, counts)
            base = np.repeat(np.cumsum(counts) - counts, counts)
            picked = np.arange(total) - base + starts
            index_i.append(np.repeat(np.arange(count), counts))
            index_j.append(order[picked])

        displacement = np.zeros_like(pos)
        if not index_i:
            return displacement

        i = np.concatenate(index_i)
        j = np.concatenate(index_j)
        keep = i != j
        if not keep.any():
            return displacement
        i = i[keep]
        j = j[keep]

        diff = pos[i] - pos[j]
        dist = np.linalg.norm(diff, axis=1)
        inside = (dist < radius) & (dist > 1e-6)
        if not inside.any():
            return displacement

        diff = diff[inside]
        i = i[inside]
        dist = np.maximum(dist[inside], 1e-6)
        gain = (radius - dist) / dist
        for channel in range(3):
            displacement[:, channel] = np.bincount(
                i, weights=diff[:, channel] * gain, minlength=count
            )[:count]
        return displacement

    def _relax(self, damping: float | None = None) -> None:
        """一个 PBD 子步：平滑 + 斥力 + 边界 → 速度低通 → 位置。

        ``damping`` 是**子步级**速度记忆（由 :meth:`step` 按 ``inertia^(1/子步)`` 换算），
        缺省取面板的 ``inertia`` 原值 —— 测试里直接调 ``_relax`` 时就是纯 PBD 的行为。
        """
        pos = self._pos
        count = len(pos)
        if count < 3:
            return

        params = self.params
        disp = (
            0.5 * (np.roll(pos, 1, axis=0) + np.roll(pos, -1, axis=0)) - pos
        ) * params["smooth_weight"]
        disp += self._repulsion(pos) * params["rep_weight"]

        bound = float(params["bound_radius"])
        if bound > 0.0:
            if self._flatten:
                dist = np.sqrt(pos[:, 0] ** 2 + pos[:, 1] ** 2)
            else:
                dist = np.linalg.norm(pos, axis=1)
            over = dist > bound
            if over.any():
                scale = np.maximum(dist[over], 1e-6)
                direction = -pos[over] / scale[:, None]
                if self._flatten:
                    direction[:, 2] = 0.0
                disp[over] += direction * (
                    (dist[over] - bound) * params["bound_weight"]
                )[:, None]

        # 速度 = 位移的一阶低通（damping 0 = 纯 PBD，等于直接把位移加上去）。
        # 低通把逐子步的高频抖动压掉，画面上看到的是"流"而不是"抖"。
        if damping is None:
            damping = min(max(float(params["inertia"]), 0.0), 0.95)
        self._vel = self._vel * damping + disp * (1.0 - damping)

        # 单步位移上限：纯 PBD 的稳定化手段，不设的话边界附近会甩出去
        limit = _STEP_LIMIT * max(float(params["rep_radius"]), 1e-6)
        magnitude = np.linalg.norm(self._vel, axis=1)
        shrink = np.minimum(1.0, limit / np.maximum(magnitude, 1e-9))
        self._pos = pos + self._vel * shrink[:, None]
        if self._flatten:
            self._pos[:, 2] = 0.0

    def _grow(self, quota: int | None = None) -> None:
        """拓扑生长：切最长的若干条边（节点数上限 = ``capacity``）。

        速度场跟着插值（新点取两边节点的均值），否则每帧分裂都会把惯性清零。
        """
        split = (
            max(1, int(self.params["growth_rate"])) if quota is None else int(quota)
        )
        if split <= 0:
            return np.empty(0, np.int64)
        grown, chosen, (velocity,) = grow_topology(
            self._pos,
            split_dist=float(self.params["split_dist"]),
            max_splits=split,
            capacity=self.capacity,
            noise=float(self.params["split_noise"]),
            flatten=self._flatten,
            rng=self._rng,
            extras=(self._vel,),
        )
        if grown.shape[0] != self._pos.shape[0]:
            self._pos = grown
            self._vel = velocity
        return chosen

    def step(self, substeps: int = 1) -> None:
        """推进**一帧**：``substeps`` 个物理子步，帧尾做一次拓扑生长。

        惯性按**帧**给（面板上的值），这里换算成子步级阻尼
        ``inertia ** (1/子步)`` —— 否则子步越多、低通忘得越快，滑块就形同虚设
        （子步 12、惯性 0.5 时子步级 0.5 的"帧记忆"只有 0.0002）。
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
        return self._pos.copy()

    def stretch(self) -> np.ndarray:
        """``(n,)`` 逐点拉伸比（见 :func:`stretch_of`）。"""
        return stretch_of(self._pos, float(self.params["split_dist"]))

    def curvature(self) -> np.ndarray:
        """``(n,)`` 逐点转角 / π（见 :func:`curvature_of`）。"""
        return curvature_of(self._pos)


def _offsets(dims: int) -> list:
    """``dims`` 维上的 3ᵈ 邻域偏移（-1 / 0 / 1 的笛卡尔积）。"""
    if dims == 2:
        return [(a, b) for a in (-1, 0, 1) for b in (-1, 0, 1)]
    return [
        (a, b, c) for a in (-1, 0, 1) for b in (-1, 0, 1) for c in (-1, 0, 1)
    ]


# --- 工厂 -------------------------------------------------------------------


def taichi_available() -> bool:
    """本进程能否 ``import taichi``。

    第一次调用要把 taichi 加载进来（几百毫秒），别在面板 ``draw`` 里调。
    """
    try:
        import taichi  # noqa: F401
    except Exception:
        return False
    return True


def create(
    segments: int = 24,
    radius: float = 1.0,
    jitter: float = 0.05,
    seed: int = 0,
    capacity: int = 1500,
    backend: str = BACKEND_AUTO,
    params: dict | None = None,
):
    """建求解器：``AUTO`` 优先 Taichi GPU，取不到就退回 NumPy。

    ``backend`` 显式给 ``TAICHI`` 时初始化失败会抛异常（面板会显示原因），给 ``NUMPY``
    就永远走纯 NumPy。回退原因写在实例的 ``note`` 上。
    """
    choice = str(backend).upper()
    if choice in (BACKEND_AUTO, BACKEND_TAICHI):
        try:
            from .taichi_solver import TaichiDifferentialGrowth

            return TaichiDifferentialGrowth(
                segments, radius, jitter, seed, capacity, params=params
            )
        except Exception as exc:
            if choice == BACKEND_TAICHI:
                raise RuntimeError(f"Taichi 后端不可用: {exc}") from exc
            solver = NumPyDifferentialGrowth(
                segments, radius, jitter, seed, capacity, params=params
            )
            solver.note = f"Taichi 不可用（{exc}），已回退 NumPy"
            return solver
    return NumPyDifferentialGrowth(
        segments, radius, jitter, seed, capacity, params=params
    )
