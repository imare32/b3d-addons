"""3D 希尔伯特曲线：索引 ↔ 格子坐标映射 + 弹簧质点网络拓扑。

纯算法层，**不 import bpy**，可以脱离 Blender 单独跑测试::

    python -c "from sim_hilbert import curve; n = curve.network(3); print(n.positions.shape, n.pairs.shape)"

两件事分开：

* :func:`hilbert_point` —— 把一维序号（0 ≤ index < 8^order）映射成 ``order`` 阶 3D
  希尔伯特曲线上的格子坐标。这是经典的位运算变换（Butz 算法）：先把序号按 3 位一组
  交错成 ``x/y/z``，再做格雷码式的转轴旋转，让相邻序号的格子始终**共享一个面**
  （曼哈顿距离 1）—— 这是希尔伯特曲线作为"空间填充曲线"的全部意义；
* :func:`network` —— 把序号顺序当作**曲线的走向**（顶点 i 与 i+1 是曲线上相邻的一段），
  同时在格子上按邻域距离铺一层**弹簧网络**（6 / 18 / 26 邻域）。

约定
----
* 顶点编号 = 希尔伯特序号，所以「曲线」不需要额外存边表：``0..N-1`` 依次连起来就是；
  画成一条连续笔划时直接按顶点顺序取点；
* 弹簧网络是**格子的**，不是曲线的：6 邻域只连轴向（像一堆绳段，最软）、18 邻域再加
  面对角（等价于"格子骨架"）、26 邻域连体对角（最硬，落下去像一块胶体）；
* 坐标：格子铺在 ``[-size/2, size/2]³``，整体抬高 ``drop_height``，z 向上、重力沿 -Z；
* 所有长度沿用 ``size``（包围盒边长）作单位，物理参数里的刚度 / 质量按这个尺度取。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = [
    "MAX_ORDER",
    "NEIGHBOUR_ITEMS",
    "POSITION_DTYPE",
    "Network",
    "grid_size",
    "hilbert_point",
    "lattice_index",
    "network",
    "neighbour_offsets",
    "vertex_count",
]

#: 阶数上限：4 阶 = 16³ = 4096 个顶点、26 邻域约 3.6 万根弹簧，已经吃满单帧预算。
MAX_ORDER = 4

#: 顶点位置的精度（写进 Blender 用 f32 就够，物理内部用 f64）。
POSITION_DTYPE = np.float32

#: 邻域档位（面板枚举项）：值 = 每格最多连几个邻居。
NEIGHBOUR_ITEMS = [
    (
        6,
        "6 邻域（轴向）",
        "只连上下左右前后的格子：结构最软，落下去像一团绳结，曲线折得很厉害",
    ),
    (
        18,
        "18 邻域（+面对角）",
        "再连 12 条面对角：介于两者之间，格子开始有「板」的刚性",
    ),
    (
        26,
        "26 邻域（+体对角）",
        "全连（缺省）：等价于给每条边、每个面、每个体对角都上弹簧，落下去像一块软胶体",
    ),
]


def grid_size(order: int) -> int:
    """每边的格子数 ``2^order``。"""
    return 1 << int(order)


def vertex_count(order: int) -> int:
    """顶点总数 ``8^order``。"""
    return grid_size(order) ** 3


def hilbert_point(index: int, order: int) -> tuple[int, int, int]:
    """一维序号 → 3D 格子坐标 ``(x, y, z)``（``0 <= index < 8^order``）。

    与参考实现（``ga2/common/hilbert_curve.py``）逐式一致：先把序号按 3 位交错拆成三个
    坐标，再做 ``t = x[2] >> 1`` 的转轴变换，最后按 ``q = 2,4,8,...`` 逐层转格子 ——
    每一层把「当前这半格」映射成四种旋转之一，保证相邻序号永远面相邻。
    """
    n = 3
    bits = format(int(index), f"0{int(order) * n}b")
    x = [int(bits[i::n], 2) if bits[i::n] else 0 for i in range(n)]

    t = x[n - 1] >> 1
    for i in range(n - 1, 0, -1):
        x[i] ^= x[i - 1]
    x[0] ^= t

    q = 2
    z = 1 << int(order)
    while q != z:
        p = q - 1
        for i in range(n - 1, -1, -1):
            if x[i] & q:
                x[0] ^= p
            else:
                t = (x[0] ^ x[i]) & p
                x[0] ^= t
                x[i] ^= t
        q <<= 1
    return int(x[0]), int(x[1]), int(x[2])


def lattice_index(order: int) -> np.ndarray:
    """``(g, g, g)`` 的 int32 数组：``[gx, gy, gz]`` = 该格子的希尔伯特序号。"""
    g = grid_size(order)
    out = np.zeros((g, g, g), np.int32)
    for index in range(g**3):
        out[hilbert_point(index, order)] = index
    return out


def neighbour_offsets(mode: int = 26) -> list[tuple[int, int, int]]:
    """邻域档位 → 偏移表（已按「首个非零分量为正」去重，只留一半方向）。

    ``mode`` 是 6 / 18 / 26，含义是**曼哈顿长度上限**：``1``（轴向 6 个）、``2``
    （再加 12 条面对角 = 18 个）、``3``（再加 8 条体对角 = 26 个）。
    """
    limit = {6: 1, 18: 2, 26: 3}.get(int(mode))
    if limit is None:
        raise ValueError(f"neighbours must be 6, 18 or 26, got {mode!r}")

    offsets = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                if abs(dx) + abs(dy) + abs(dz) > limit:
                    continue
                for component in (dx, dy, dz):
                    if component:
                        if component > 0:
                            offsets.append((dx, dy, dz))
                        break
    return offsets


@dataclass(frozen=True)
class Network:
    """一个阶数/尺寸下的曲线 + 弹簧网络（构建一次，物理自己复制状态）。"""

    order: int
    size: float
    drop_height: float
    neighbours: int
    #: (N,3) float32，顶点编号 = 希尔伯特序号。
    positions: np.ndarray
    #: (M,2) int32，弹簧两端的顶点编号（``a < b`` 没有保证，只保证不重复）。
    pairs: np.ndarray
    #: (M,) float32，弹簧原长（= 初始位置的点距，斜向弹簧自然更长）。
    rest_lengths: np.ndarray

    @property
    def grid(self) -> int:
        """每边格子数。"""
        return grid_size(self.order)

    @property
    def vertex_count(self) -> int:
        return len(self.positions)

    @property
    def spring_count(self) -> int:
        return len(self.pairs)

    @property
    def spacing(self) -> float:
        """轴向格距。"""
        g = self.grid
        return float(self.size) / (g - 1) if g > 1 else float(self.size)

    def signature(self) -> tuple:
        """几何签名：变了就得重建拓扑与求解器。"""
        return (
            int(self.order),
            round(float(self.size), 6),
            round(float(self.drop_height), 6),
            int(self.neighbours),
        )


def network(
    order: int = 3,
    size: float = 4.0,
    drop_height: float = 5.0,
    neighbours: int = 26,
) -> Network:
    """按阶数铺一条 3D 希尔伯特曲线，并在同样这些格子上建弹簧网络。

    ``size`` 是包围盒边长（不是格距）：格距 = ``size / (2^order − 1)``，所以改阶数时
    整条曲线的实际跨度不变，只是格子变密。
    """
    order = int(order)
    if not 1 <= order <= MAX_ORDER:
        raise ValueError(f"order must be 1..{MAX_ORDER}, got {order}")

    g = grid_size(order)
    n_vertices = g**3
    spacing = float(size) / (g - 1) if g > 1 else float(size)
    half = float(size) * 0.5
    height = float(drop_height)

    positions = np.empty((n_vertices, 3), POSITION_DTYPE)
    index_of = np.zeros((g, g, g), np.int32)
    for index in range(n_vertices):
        gx, gy, gz = hilbert_point(index, order)
        index_of[gx, gy, gz] = index
        positions[index] = (
            gx * spacing - half,
            gy * spacing - half,
            gz * spacing - half + height,
        )

    # 弹簧：每个半边方向切一次切片，一次拿到整批 (源, 目标) 顶点对。
    blocks = []
    for dx, dy, dz in neighbour_offsets(neighbours):
        offset = (dx, dy, dz)
        source = tuple(slice(max(0, -c), g - max(0, c)) for c in offset)
        target = tuple(slice(max(0, c), g - max(0, -c)) for c in offset)
        blocks.append(
            np.stack((index_of[source].ravel(), index_of[target].ravel()), axis=1)
        )

    pairs = (
        np.concatenate(blocks).astype(np.int32)
        if blocks
        else np.zeros((0, 2), np.int32)
    )
    deltas = positions[pairs[:, 0]] - positions[pairs[:, 1]]
    rest = np.sqrt(np.einsum("ij,ij->i", deltas, deltas)).astype(np.float32)

    return Network(
        order=order,
        size=float(size),
        drop_height=height,
        neighbours=int(neighbours),
        positions=positions,
        pairs=pairs,
        rest_lengths=rest,
    )
