"""Conway 生命游戏（面邻域，任意曲面网格）：纯 NumPy 后端 + 后端工厂。

**细胞 = 网格的一个面**，邻域是 Moore 邻域 —— 至少共用一个顶点的其他面（四边形网格
通常是 8 个，三角面一般 11~12 个，上限见 :data:`MAX_NEIGHBORS`）。邻域由 Blender 层
（:mod:`sim_game_of_life.mesh`）从 BMesh 提取后以 ``(neighbors, counts)`` 两个数组传进来；
本模块只认这两个数组，所以可以脱离 Blender 单独跑（系统 Python + numpy 即可）::

    from sim_game_of_life import solver
    s = solver.create(8, birth=8, survive=12)
    s.load_topology(neighbors, counts)
    s.set_state(states)
    s.step(10)
    print(s.state_flat().mean())

状态两代并存：``state`` 是第 g 代，``next`` 是第 g+1 代（:meth:`step` 往前推一代）。
两代都按 ``[face]`` 展平，和 Blender 的面属性顺序一致，所以可以直接写进属性、不需要重排。

规则用 B/S **位掩码**表示（第 n 位 = 「n 个活邻」这个条件成立），见 :data:`RULES`：
经典康威是 B3/S23，即 ``birth = 1<<3``、``survive = (1<<2) | (1<<3)``。

Taichi 后端在 :mod:`sim_game_of_life.taichi_solver`（同一套规则，GPU kernel），两者 API
相同，由 :func:`create` 按需选择；本模块**不 import taichi**。
"""

from __future__ import annotations

import numpy as np

__all__ = [
    "BACKEND_AUTO",
    "BACKEND_NUMPY",
    "BACKEND_TAICHI",
    "DEFAULT_RULE",
    "INTERPOLATIONS",
    "MAX_NEIGHBORS",
    "RULES",
    "RULE_ITEMS",
    "NumPyLife",
    "blend",
    "create",
    "rule_masks",
    "taichi_available",
]

#: 单个面的邻域上限。平整的四边形网格只用到 8 个，三角面最多 12 个；但极点的
#: 高价值顶点会让一圈面**全都**互为邻居（UV 球两极的一个三角面能到 50 个），
#: 所以提取时按「共边优先」排序后截到这里（见 :func:`sim_game_of_life.mesh.extract_topology`）。
MAX_NEIGHBORS = 24

#: ``create`` 的后端选择。
BACKEND_AUTO = "AUTO"
BACKEND_TAICHI = "TAICHI"
BACKEND_NUMPY = "NUMPY"


def _mask(*counts: int) -> int:
    """把「活邻个数」集合压成位掩码：``_mask(2, 3)`` → ``0b1100``。"""
    value = 0
    for count in counts:
        value |= 1 << int(count)
    return value


#: 规则库：B/S 记号 → 出生 / 存活位数掩码。
#: 顺序即面板枚举顺序，``DEFAULT_RULE`` 是缺省项。
RULES = {
    "CONWAY": {
        "label": "康威 B3/S23",
        "description": "经典规则：3 个活邻出生，2~3 个活邻存活",
        "birth": _mask(3),
        "survive": _mask(2, 3),
    },
    "HIGHLIFE": {
        "label": "HighLife B36/S23",
        "description": "康威加一条出生条件（6 个活邻），会出现复制自身的「复制器」",
        "birth": _mask(3, 6),
        "survive": _mask(2, 3),
    },
    "SEEDS": {
        "label": "Seeds B2/S",
        "description": "只有 2 个活邻才出生，所有活细胞立刻死亡：爆开、扩散、不留痕迹",
        "birth": _mask(2),
        "survive": _mask(),
    },
    "DAYNIGHT": {
        "label": "Day & Night B3678/S34678",
        "description": "生与死近似对称的规则，大片斑块会同时扩张与收缩",
        "birth": _mask(3, 6, 7, 8),
        "survive": _mask(3, 4, 6, 7, 8),
    },
    "NO_DEATH": {
        "label": "无死亡 B3/S012345678",
        "description": "活细胞永不死亡，只有出生：一路堆成越来越厚的纹理",
        "birth": _mask(3),
        "survive": _mask(0, 1, 2, 3, 4, 5, 6, 7, 8),
    },
}

DEFAULT_RULE = "CONWAY"

#: 面板枚举项 ``(标识, 显示名, 说明)``。
RULE_ITEMS = [
    (key, entry["label"], entry["description"]) for key, entry in RULES.items()
]

#: 代与代之间的插值方式 ``(标识, 显示名, 说明)``。
INTERPOLATIONS = [
    ("LINEAR", "线性", "匀速过渡"),
    ("SMOOTHSTEP", "平滑", "三次平滑：两头顿、中间快，看起来像细胞在「呼吸」"),
    ("EASE_IN", "缓入", "起步慢、收尾快"),
    ("EASE_OUT", "缓出", "起步快、收尾慢"),
]


def rule_masks(name: str) -> tuple[int, int]:
    """规则名 → ``(birth, survive)`` 位掩码；认不出来就用缺省规则。"""
    entry = RULES.get(str(name).upper()) or RULES[DEFAULT_RULE]
    return int(entry["birth"]), int(entry["survive"])


#: 缺省规则的掩码（两个后端建实例时的缺省形参）。
DEFAULT_BIRTH, DEFAULT_SURVIVE = rule_masks(DEFAULT_RULE)


def blend(
    current: np.ndarray, target: np.ndarray, progress: float, mode: str = "SMOOTHSTEP"
) -> np.ndarray:
    """两代状态之间按 ``progress`` 插值（``mode`` 见 :data:`INTERPOLATIONS`）。"""
    t = float(np.clip(progress, 0.0, 1.0))
    key = str(mode).upper()
    if key == "SMOOTHSTEP":
        weight = t * t * (3.0 - 2.0 * t)
    elif key == "EASE_IN":
        weight = t * t
    elif key == "EASE_OUT":
        weight = 1.0 - (1.0 - t) * (1.0 - t)
    else:
        weight = t
    return (current + (target - current) * weight).astype(np.float32)


class NumPyLife:
    """生命游戏求解器（NumPy 后端）。

    邻域在 :meth:`load_topology` 里摊平成一张**边表** ``(src, dst)``（每行是
    「面 src 是面 dst 的邻居」），每一代用 ``np.bincount`` 数活邻 —— 一整代只走两遍
    数组，不写 Python 循环。邻域必须是**去重**过的，否则同一个邻居会被数两次。

    ``n_faces`` 只是和 Taichi 后端对齐的形参（那边要预先分配字段）：NumPy 版的真实
    大小由 :meth:`load_topology` 决定。
    """

    backend = "numpy"

    def __init__(
        self,
        n_faces: int = 0,
        birth: int = DEFAULT_BIRTH,
        survive: int = DEFAULT_SURVIVE,
    ):
        #: 后端说明（面板读数用；NumPy 后端没有可报告的回退原因）。
        self.note = "numpy"
        self.birth = int(birth)
        self.survive = int(survive)

        self._n = 0
        self._src = np.zeros(0, np.int32)
        self._dst = np.zeros(0, np.int32)
        self._state = np.zeros(0, np.float32)
        self._next = np.zeros(0, np.float32)

    # --- 拓扑 ---------------------------------------------------------------

    @property
    def count(self) -> int:
        """细胞（面）数量。"""
        return self._n

    def load_topology(self, neighbors: np.ndarray, counts: np.ndarray) -> None:
        """载入面邻域：``neighbors`` 是 ``(n, k)`` 的索引表，``-1`` 表示空位。

        ``counts`` 给出每个面**有效**的邻居数（用来忽略填充位）。尺寸不一致时抛
        ``ValueError`` —— 这类错误来自拓扑提取，越早报越好。
        """
        table = np.asarray(neighbors, np.int32)
        lengths = np.asarray(counts, np.int32).ravel()
        if table.ndim != 2:
            raise ValueError(f"neighbors 必须是二维数组，收到 {table.shape}")
        if lengths.size != table.shape[0]:
            raise ValueError(
                f"counts 长度 {lengths.size} 与面数 {table.shape[0]} 不一致"
            )

        n, width = table.shape
        limit = np.minimum(lengths, width)[:, None]
        valid = (table >= 0) & (np.arange(width)[None, :] < limit)

        self._n = int(n)
        self._src = np.broadcast_to(np.arange(n, dtype=np.int32)[:, None], table.shape)[
            valid
        ].astype(np.int32)
        self._dst = table[valid].astype(np.int32)

        self._state = np.zeros(self._n, np.float32)
        self._next = np.zeros(self._n, np.float32)

    def set_rule(self, birth: int, survive: int) -> bool:
        """换规则；返回是否真的变了（没变就不重算下一代）。"""
        changed = (int(birth), int(survive)) != (self.birth, self.survive)
        self.birth, self.survive = int(birth), int(survive)
        if changed:
            self._compute_next()
        return changed

    # --- 状态 ---------------------------------------------------------------

    def set_state(self, states: np.ndarray) -> None:
        """铺初始状态（``> 0.5`` 算活），并顺带算出下一代。"""
        values = np.asarray(states, np.float32).ravel()[: self._n]
        self._state = np.zeros(self._n, np.float32)
        self._state[: values.size] = (values > 0.5).astype(np.float32)
        self._compute_next()

    def state_flat(self) -> np.ndarray:
        """第 g 代状态（0 / 1，按面展平；只读视图，别就地改）。"""
        return self._state

    def next_flat(self) -> np.ndarray:
        """第 g+1 代状态（同上）。"""
        return self._next

    def step(self, steps: int = 1) -> None:
        """往前推 ``steps`` 代。"""
        for _ in range(max(0, int(steps))):
            self._state, self._next = self._next, self._state
            self._compute_next()

    # --- 演化 ---------------------------------------------------------------

    def _compute_next(self) -> None:
        """按规则从 ``_state`` 算出 ``_next``。"""
        if self._n == 0:
            self._next = np.zeros(0, np.float32)
            return

        alive = self._state > 0.5
        # 只数「活细胞那一端」的边：每条边贡献 1，bincount 按 dst 汇聚
        counted = np.bincount(self._dst[alive[self._src]], minlength=self._n)
        birth = ((self.birth >> counted) & 1).astype(bool)
        survive = ((self.survive >> counted) & 1).astype(bool)
        self._next = np.where(alive, survive, birth).astype(np.float32)


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
    n_faces: int = 0,
    backend: str = BACKEND_AUTO,
    birth: int = 8,
    survive: int = 12,
):
    """建求解器：``AUTO`` 优先 Taichi GPU，取不到就退回 NumPy。

    ``backend`` 显式给 ``TAICHI`` 时初始化失败会抛异常（面板会显示原因），给 ``NUMPY``
    就永远走纯 NumPy。回退原因写在实例的 ``note`` 上。
    """
    choice = str(backend).upper()
    if choice in (BACKEND_AUTO, BACKEND_TAICHI):
        try:
            from .taichi_solver import TaichiLife

            return TaichiLife(n_faces, birth=birth, survive=survive)
        except Exception as exc:
            if choice == BACKEND_TAICHI:
                raise RuntimeError(f"Taichi 后端不可用: {exc}") from exc
            solver = NumPyLife(n_faces, birth=birth, survive=survive)
            solver.note = f"Taichi 不可用（{exc}），已回退 NumPy"
            return solver
    return NumPyLife(n_faces, birth=birth, survive=survive)
