"""平面图的**面**：把编织用的同一张平面化边网切成若干封闭区域（色块）。

编织关心"带子怎么走"，填充关心"带子围出了哪些区域"。两者共用同一张平面化后的边网
（``weave.edge_network``），所以色块边界与带子中心线**严格重合**：带子两侧各压掉半个
带宽，色块正好顶到带子边缘，不留白缝。

算法是标准的半边遍历：

1. 每个结点的出边按极角（逆时针）排序；
2. 沿半边 ``u → v`` 走，在 ``v`` 处取 ``v → u`` 在逆时针序里的**前一条**出边；
3. 这样每走一圈，面始终在**左手边**——内部面是逆时针（有向面积为正），最外圈的边界是
   顺时针。只保留有向面积为正的面，就正好是"带子围出来的所有区域"，背景天然被排除
   （不需要额外判断哪块是背景）。

实测面数（``level 2``，PA）：``n = 8 / 10 / 16 / 24`` 分别是 ``17 / 21 / 33 / 49`` 块，
最大的面只占包围盒面积 ``8%`` 左右（面积已经由带子切碎），所以填色是"彩色花窗"而不是
一块色斑。``level 0`` 是例外：那里是一整颗实心星（一块面，占 55% 上下）。

纯组合运算，不 import bpy，也不碰随机数。
"""

from __future__ import annotations

import math

__all__ = [
    "MIN_FACE_RATIO",
    "Face",
    "build_faces",
    "interior_faces",
    "signed_area",
]

Point = tuple[float, float]

#: 面积小于"包围盒面积 × 该比例"的面丢掉：那是带子把角落切出来的退化细缝，填上
#: 也只多一条形状，看不见。
MIN_FACE_RATIO = 1e-6


class Face:
    """一块封闭区域：``points`` 是逆时针顶点表（首点不重复）。"""

    __slots__ = ("area", "centre", "points")

    def __init__(self, points: list[Point]):
        self.points = points
        self.area = signed_area(points)
        count = len(points)
        self.centre = (
            sum(p[0] for p in points) / count,
            sum(p[1] for p in points) / count,
        )

    @property
    def radius(self) -> float:
        """质心到原点的距离（设计坐标系的原点就是玫瑰线中心）。"""
        return math.hypot(self.centre[0], self.centre[1])

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Face({len(self.points)} pts, area={self.area:.5f})"


def signed_area(points: list[Point]) -> float:
    """有向面积（鞋带公式）：逆时针为正。"""
    total = 0.0
    count = len(points)
    for index in range(count):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % count]
        total += x1 * y2 - x2 * y1
    return total * 0.5


def build_faces(points: list[Point], edges: list[tuple[int, int]]) -> list[Face]:
    """由平面化边网（结点坐标 + 无向边）取出所有内部面，按面积从大到小返回。

    边网必须已经**平面化**（没有两条边内部相交、没有端点落在别的边内部），否则同一个
    区域会被切成互不相连的碎块——那正是 ``weave.edge_network`` 保证的事。
    """
    # 出边按"指向邻居的方向角"排序：半边遍历靠这个序找"顺时针第一条"
    around: dict[int, list[tuple[float, int]]] = {}
    for a, b in edges:
        ax, ay = points[a]
        bx, by = points[b]
        around.setdefault(a, []).append((math.atan2(by - ay, bx - ax), b))
        around.setdefault(b, []).append((math.atan2(ay - by, ax - bx), a))

    # (结点, 邻居) → 在逆时针序里的位置。边表已经去过重，没有平行边，键不会冲突。
    slot: dict[tuple[int, int], int] = {}
    for node, outgoing in around.items():
        outgoing.sort()
        for index, (_angle, other) in enumerate(outgoing):
            slot[(node, other)] = index

    seen: set[tuple[int, int]] = set()
    faces: list[Face] = []

    for a, b in edges:
        for start in ((a, b), (b, a)):
            if start in seen:
                continue
            loop: list[Point] = []
            current = start
            while current not in seen:
                seen.add(current)
                loop.append(points[current[0]])
                node, came_from = current[1], current[0]
                outgoing = around[node]
                index = slot[(node, came_from)]
                # 逆时针序里往前退一格 = 顺时针第一条 = 面留在左手边
                current = (node, outgoing[(index - 1) % len(outgoing)][1])
            if len(loop) >= 3:
                face = Face(loop)
                if face.area > 0.0:
                    faces.append(face)

    faces.sort(key=lambda face: face.area, reverse=True)
    return faces


def interior_faces(
    points: list[Point], edges: list[tuple[int, int]], span: float
) -> list[Face]:
    """去掉退化细缝（判据见 :data:`MIN_FACE_RATIO`），其余按面积从大到小返回。"""
    if span <= 0.0:
        return []
    floor = MIN_FACE_RATIO * span * span
    return [face for face in build_faces(points, edges) if face.area > floor]
