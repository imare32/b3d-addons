"""星形纹样的几何：阿基米德镶嵌 → Hankin 射线 → 股线（**不 import bpy**）。

方法来自 E. H. Hankin（1925）的 "polygons in contact"，Craig S. Kaplan
（"Islamic star patterns from polygons in contact", 2005）的表述：拿一张正多边形的
镶嵌，从**每条边的中点**向两侧的砖里各发一条射线，射线与边成**接触角**；射线停在
它与相邻那条边的射线相遇处（角平分线上）。相邻两砖的射线在同一中点相遇并交叉，
于是线从一块砖连到下一块砖，星形与玫瑰纹就长出来了。

三步::

    镶嵌（两个格矢 + 一格的若干多边形）
      → motif（每块砖：边中点发两条射线 → 线段）
      → 股线（线段首尾相连；四端相遇 = 直通，两端相遇 = 拐角）

**为什么用"镶嵌 + 每砖 motif"而不是先造一张全局边网**：这里的几何是解析的，
线段的端点都由格矢与接触角直接算出，不需要 ``gen_zellij/weave.py`` 那套"端点聚类
+ 打断 + 插入交点"的平面化（那是给"由各自的仿射变换摆出来的色块轮廓"用的）。

**本文件是 JS 版 ``islamic-patterns/js/girih.js`` 的移植**（MIT，见本文件末尾的
版权与许可声明）：``TILINGS`` / ``tilesIn`` / ``motif`` / ``weave`` 逐函数对照，
数值行为保持一致（段数、股线数、交叉数、股线的闭合标记与点数都能对上）。
差异只有三处：

1. 点用 ``tuple[float, float]``，没有 SVG 那套 ``[x, y]`` 数组与像素缩放；
2. 去掉了绘制相关的部分（``patternSvg`` / 调色板），那属于渲染层；
3. 上下交错的求解（:func:`over_under`）单独成一个函数、结果写在 ``Crossing.over`` 上；
   切缺口与补片在 :mod:`interlace` 里（参考实现是渲染时"重描压上段"，没有这两件事）。
   线条 / 带子样式不需要上下关系，所以 ``weave()`` 不自动求解。

纯 Python（只用 ``math``），可以脱离 Blender 单独跑：

    python -c "import gen_girih; ..."
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = [
    "TILINGS",
    "Crossing",
    "Strand",
    "Tile",
    "Tiling",
    "motif",
    "motif_and_star",
    "over_under",
    "tiles_in",
    "tiling_ids",
    "weave",
]

Point = tuple[float, float]
Segment = tuple[Point, Point]

_SQRT3 = math.sqrt(3)
_D2R = math.pi / 180
#: 边长 1 的正十二边形内切圆半径。
_IN12 = (2 + _SQRT3) / 2
#: 边长 1 的正八边形宽度（相对两边的距离）。
_W8 = 1 + math.sqrt(2)

#: 端点归并的量化精度：坐标先乘它再取整当键。
#:
#: 相邻两砖各自算出的那条**共享边**，端点只在浮点末位上有差别（同一段缝的两份坐标
#: 差 ``1e-16``~``1e-12``）。所以这里不需要 ``gen_zellij`` 那种"容差聚类"——输入是解析
#: 几何而不是各砖自己的仿射变换，量化到这个精度（边长 1 时是 ``1e-5``）足够，
#: 而且与 JS 版逐位一致，方便对照测试。
_KEY = 1e5


@dataclass(frozen=True, slots=True)
class Tiling:
    """一种阿基米德镶嵌：两个格矢 + 一个格胞里的多边形。

    ``a`` / ``b`` 是平移格矢；``polys`` 每项是 ``(中心 x, 中心 y, 边数, 首个顶点的
    角度°)``，边长一律为 1。
    """

    key: str
    name: str
    a: Point
    b: Point
    polys: tuple[tuple[float, float, int, float], ...]


@dataclass(frozen=True, slots=True)
class Tile:
    """一块砖：正 ``sides`` 边形，顶点按逆时针排列（边长 1）。"""

    sides: int
    centre: Point
    vertices: tuple[Point, ...]


@dataclass(frozen=True, slots=True)
class Strand:
    """一根股线：首尾相连的一串线段，``closed`` 时末点回到首点（**不重复**首点）。

    ``segments`` 是这条股线依次走过的线段号——阶段一不用，阶段三要把交叉点的弧长
    换算成"切在哪儿"，那时要用它反查。
    """

    points: list[Point]
    closed: bool
    segments: tuple[int, ...] = ()


@dataclass(slots=True)
class Crossing:
    """一次交叉：交叉点、夹角正弦，以及它在两根股线上的**弧长位置**。

    ``a`` / ``b`` 是 ``(股线号, 弧长)``，切缺口就是按这两个弧长把"压在下面"的那一段
    摘出来。``over`` 由 :func:`over_under` 填（``"a"`` = a 那一侧压在上面），
    没解之前是空串。
    """

    point: Point
    sin_angle: float
    a: tuple[int, float]
    b: tuple[int, float]
    over: str = ""

    @property
    def under(self) -> tuple[int, float]:
        """压在下面的那一侧 ``(股线号, 弧长)``。"""
        return self.b if self.over == "a" else self.a

    @property
    def above(self) -> tuple[int, float]:
        """压在上面的那一侧 ``(股线号, 弧长)``。"""
        return self.a if self.over == "a" else self.b


# --- 镶嵌表 ---------------------------------------------------------------


def _snub_triangles(side: float, radius: float):
    """snub square 格胞里的四个三角形（逐字移植 JS 版的 ``snubTriangles``）。

    snub square 的两块正方形各转了 15°，中间的空隙由三角形填。JS 版的做法是：把
    两块正方形在 3×3 个格胞里的顶点全列出来，找出所有"三边都等于 1"的顶点三元组，
    取质心落在格胞内、且不在任何正方形里的那些——数学上等价于"补角三角形"，
    但写起来短。顶点数只有几十个，O(n³) 完全无所谓。
    """

    def square(cx: float, cy: float, a0: float) -> list[Point]:
        return [
            (
                cx + radius * math.cos((a0 + 90 * k) * _D2R),
                cy + radius * math.sin((a0 + 90 * k) * _D2R),
            )
            for k in range(4)
        ]

    verts: list[Point] = []
    for cx, cy, a0 in ((0.0, 0.0, 60.0), (side / 2, side / 2, 30.0)):
        for i in (-1, 0, 1):
            for j in (-1, 0, 1):
                verts.extend(square(cx + i * side, cy + j * side, a0))

    def is_unit(p: Point, q: Point) -> bool:
        return abs(math.hypot(p[0] - q[0], p[1] - q[1]) - 1.0) < 1e-6

    tris: list[tuple[float, float, int, float]] = []
    count = len(verts)
    for i in range(count):
        for j in range(i + 1, count):
            if not is_unit(verts[i], verts[j]):
                continue
            for k in range(j + 1, count):
                if not is_unit(verts[i], verts[k]) or not is_unit(verts[j], verts[k]):
                    continue
                centre = (
                    (verts[i][0] + verts[j][0] + verts[k][0]) / 3,
                    (verts[i][1] + verts[j][1] + verts[k][1]) / 3,
                )
                # 只留格胞内的那一份
                if (
                    centre[0] < -1e-6
                    or centre[1] < -1e-6
                    or centre[0] >= side - 1e-6
                    or centre[1] >= side - 1e-6
                ):
                    continue
                # "两块正方形的三个顶点"也能凑出边长 1 的三角形，但它落在正方形里
                inside_square = False
                for qx, qy in ((0.0, 0.0), (side / 2, side / 2)):
                    for i2 in range(-1, 3):
                        for j2 in range(-1, 3):
                            if math.hypot(centre[0] - qx - i2 * side, centre[1] - qy - j2 * side) < 0.6:
                                inside_square = True
                if inside_square:
                    continue
                if any(math.hypot(t[0] - centre[0], t[1] - centre[1]) < 1e-6 for t in tris):
                    continue
                angle = math.atan2(verts[i][1] - centre[1], verts[i][0] - centre[0]) / _D2R
                tris.append((centre[0], centre[1], 3, angle))
    return tris


def _tiling_4612() -> tuple[Point, Point, tuple[tuple[float, float, int, float], ...]]:
    """4.6.12：十二边形 + 两个六边形 + 三个正方形。"""
    d = 2 * _IN12 + 1
    q = _IN12 + 0.5
    return (
        (d, 0.0),
        (d / 2, d * _SQRT3 / 2),
        (
            (0.0, 0.0, 12, 15.0),
            (q, 0.0, 4, 45.0),
            (q * math.cos(60 * _D2R), q * math.sin(60 * _D2R), 4, 105.0),
            (q * math.cos(120 * _D2R), q * math.sin(120 * _D2R), 4, 165.0),
            (d / 2, d / (2 * _SQRT3), 6, 0.0),
            (d / 2, -d / (2 * _SQRT3), 6, 0.0),
        ),
    )


def _tiling_3464() -> tuple[Point, Point, tuple[tuple[float, float, int, float], ...]]:
    """3.4.6.4：六边形 + 三个正方形 + 两个三角形。"""
    d = _SQRT3 + 1
    q = _SQRT3 / 2 + 0.5
    return (
        (d, 0.0),
        (d / 2, d * _SQRT3 / 2),
        (
            (0.0, 0.0, 6, 30.0),
            (q, 0.0, 4, 45.0),
            (q * math.cos(60 * _D2R), q * math.sin(60 * _D2R), 4, 105.0),
            (q * math.cos(120 * _D2R), q * math.sin(120 * _D2R), 4, 165.0),
            (d / 2, d / (2 * _SQRT3), 3, 90.0),
            (d / 2, -d / (2 * _SQRT3), 3, 30.0),
        ),
    )


_A_4612, _B_4612, _P_4612 = _tiling_4612()
_A_3464, _B_3464, _P_3464 = _tiling_3464()
_SNUB_SIDE = math.sqrt(2 + _SQRT3)  # snub square 的格胞边长

#: 十种阿基米德镶嵌（键就是顶点构型记法，与 JS 版一致）。
TILINGS: dict[str, Tiling] = {
    "4.8.8": Tiling(
        "4.8.8",
        "Octagons and squares",
        (_W8, 0.0),
        (0.0, _W8),
        ((0.0, 0.0, 8, 22.5), (_W8 / 2, _W8 / 2, 4, 0.0)),
    ),
    "4.6.12": Tiling("4.6.12", "Dodecagons, hexagons and squares", _A_4612, _B_4612, _P_4612),
    "3.12.12": Tiling(
        "3.12.12",
        "Dodecagons and triangles",
        (2 * _IN12, 0.0),
        (_IN12, _SQRT3 * _IN12),
        (
            (0.0, 0.0, 12, 15.0),
            (_IN12, _IN12 / _SQRT3, 3, 30.0),
            (_IN12, -_IN12 / _SQRT3, 3, -30.0),
        ),
    ),
    "6.6.6": Tiling("6.6.6", "Hexagons", (_SQRT3, 0.0), (_SQRT3 / 2, 1.5), ((0.0, 0.0, 6, 30.0),)),
    "3.4.6.4": Tiling("3.4.6.4", "Hexagons, squares and triangles", _A_3464, _B_3464, _P_3464),
    "3.6.3.6": Tiling(
        "3.6.3.6",
        "Hexagons and triangles",
        (2.0, 0.0),
        (1.0, _SQRT3),
        ((0.0, 0.0, 6, 0.0), (1.0, 1 / _SQRT3, 3, 270.0), (0.0, 2 / _SQRT3, 3, 90.0)),
    ),
    "4.4.4.4": Tiling("4.4.4.4", "Squares", (1.0, 0.0), (0.0, 1.0), ((0.0, 0.0, 4, 45.0),)),
    "3.3.4.3.4": Tiling(
        "3.3.4.3.4",
        "Snub squares",
        (_SNUB_SIDE, 0.0),
        (0.0, _SNUB_SIDE),
        (
            (0.0, 0.0, 4, 60.0),
            (_SNUB_SIDE / 2, _SNUB_SIDE / 2, 4, 30.0),
            *_snub_triangles(_SNUB_SIDE, 1 / math.sqrt(2)),
        ),
    ),
    "3.3.3.4.4": Tiling(
        "3.3.3.4.4",
        "Squares and triangles in rows",
        (1.0, 0.0),
        (0.5, 1 + _SQRT3 / 2),
        (
            (0.5, 0.5, 4, 45.0),
            (0.5, 1 + _SQRT3 / 6, 3, 90.0),
            (1.0, 1 + _SQRT3 / 3, 3, 270.0),
        ),
    ),
    "3.3.3.3.3.3": Tiling(
        "3.3.3.3.3.3",
        "Triangles",
        (1.0, 0.0),
        (0.5, _SQRT3 / 2),
        ((0.5, _SQRT3 / 6, 3, 90.0), (1.0, _SQRT3 / 3, 3, 270.0)),
    ),
}


def tiling_ids() -> tuple[str, ...]:
    """镶嵌的键（保持面板里的顺序）。"""
    return tuple(TILINGS)


# --- 瓦片 -----------------------------------------------------------------


def tiles_in(tiling: str, x0: float, y0: float, x1: float, y1: float) -> list[Tile]:
    """覆盖矩形 ``[x0, x1] × [y0, y1]`` 的所有砖（图案单位，边长 1）。

    做法：把矩形的四个角换算回**格矢坐标**，得到要访问的格胞下标范围（各外扩两格，
    免得漏掉压边的那一圈），再逐格胞摆出多边形。多摆出来的砖由外面的取景/相机裁掉。
    """
    spec = TILINGS[tiling]
    ax, ay = spec.a
    bx, by = spec.b
    det = ax * by - ay * bx

    def cell(x: float, y: float) -> tuple[float, float]:
        return ((x * by - y * bx) / det, (ax * y - ay * x) / det)

    corners = [cell(x0, y0), cell(x1, y0), cell(x0, y1), cell(x1, y1)]
    i0 = math.floor(min(c[0] for c in corners)) - 2
    i1 = math.ceil(max(c[0] for c in corners)) + 2
    j0 = math.floor(min(c[1] for c in corners)) - 2
    j1 = math.ceil(max(c[1] for c in corners)) + 2

    tiles: list[Tile] = []
    for i in range(i0, i1 + 1):
        for j in range(j0, j1 + 1):
            for cx0, cy0, sides, start in spec.polys:
                cx = cx0 + i * ax + j * bx
                cy = cy0 + i * ay + j * by
                radius = 1 / (2 * math.sin(math.pi / sides))
                if (
                    cx + radius < x0
                    or cx - radius > x1
                    or cy + radius < y0
                    or cy - radius > y1
                ):
                    continue
                vertices = []
                for k in range(sides):
                    angle = (start + 360.0 * k / sides) * _D2R
                    vertices.append(
                        (cx + radius * math.cos(angle), cy + radius * math.sin(angle))
                    )
                tiles.append(Tile(sides, (cx, cy), tuple(vertices)))
    return tiles


# --- Hankin 射线 ----------------------------------------------------------


def _cross2(ax: float, ay: float, bx: float, by: float) -> float:
    return ax * by - ay * bx


def _meet(p: Point, u: Point, q: Point, w: Point) -> Point | None:
    """直线 ``p + t·u`` 与 ``q + s·w`` 的交点；平行返回 ``None``。"""
    d = _cross2(u[0], u[1], w[0], w[1])
    if abs(d) < 1e-12:
        return None
    t = _cross2(q[0] - p[0], q[1] - p[1], w[0], w[1]) / d
    return (p[0] + t * u[0], p[1] + t * u[1])


def motif_and_star(
    tile: Tile, angle: float, gap: float = 0.0
) -> tuple[list[Segment], list[Point]]:
    """一块砖的线，以及它**星形的轮廓**（2n 个点，``[边上交叉点, 角点]`` 交替）。

    ``angle`` 接触角（度，0 贴着边、90 垂直于边）；``gap`` 把一条边的两个出发点
    沿边各推开 ``gap × 边长 / 2``，两条射线于是**在边内交叉**——这就是 JS 版
    "Gap: splits each crossing point in two, for doubled lines" 的效果。

    射线与角平分线求交，而不是射线与射线求交：相邻两条射线在极端角度下可能近乎平行，
    与角平分线求交则永远有解（对角线方向固定）。

    星形轮廓就是依次连"每条边上的交叉点 → 下一个角的汇合点"，围出来的 2n 边形。
    它正好被股线围住，所以填充它等于给每块砖的星心铺底；``gap = 0`` 时边上的那个
    点就是边中点，星形与股线严丝合缝。
    """
    theta = angle * _D2R
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    centre = tile.centre
    verts = tile.vertices
    sides = tile.sides

    toward_b: list[tuple[Point, Point]] = []
    toward_a: list[tuple[Point, Point]] = []
    contact: list[Point] = []  # 每条边上两条射线的交叉点
    for k in range(sides):
        a = verts[k]
        b = verts[(k + 1) % sides]
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        e = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
        normal = (-e[1], e[0])
        middle = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        if (centre[0] - middle[0]) * normal[0] + (centre[1] - middle[1]) * normal[1] < 0:
            normal = (-normal[0], -normal[1])  # 朝砖内
        half = gap * length / 2
        # 有 gap 时两条射线交叉在边内：偏向 b 端的那条从靠近 a 的点出发，反之亦然
        point_b = (middle[0] - e[0] * half, middle[1] - e[1] * half)
        point_a = (middle[0] + e[0] * half, middle[1] + e[1] * half)
        direction_b = (e[0] * cos_t + normal[0] * sin_t, e[1] * cos_t + normal[1] * sin_t)
        direction_a = (-e[0] * cos_t + normal[0] * sin_t, -e[1] * cos_t + normal[1] * sin_t)
        toward_b.append((point_b, direction_b))
        toward_a.append((point_a, direction_a))
        # 没有 gap 时两条射线本来就在中点相遇，不必再求一次交（也更稳）
        cross = _meet(point_b, direction_b, point_a, direction_a) if half > 0.0 else None
        contact.append(cross if cross is not None else middle)

    segments: list[Segment] = []
    star: list[Point] = []
    for k in range(sides):
        origin, direction = toward_b[k]
        corner = verts[(k + 1) % sides]
        meeting = _meet(origin, direction, corner, (centre[0] - corner[0], centre[1] - corner[1]))
        if meeting is None:
            meeting = centre
        segments.append((origin, meeting))
        segments.append((meeting, toward_a[(k + 1) % sides][0]))
        star.append(contact[k])
        star.append(meeting)
    return segments, star


def motif(tile: Tile, angle: float, gap: float = 0.0) -> list[Segment]:
    """只要线（星形轮廓见 :func:`motif_and_star`）。"""
    return motif_and_star(tile, angle, gap)[0]


# --- 股线 -----------------------------------------------------------------


def _key(point: Point) -> tuple[int, int]:
    return (round(point[0] * _KEY), round(point[1] * _KEY))


def _unit(segment: Segment, end: int) -> Point:
    """线段从 ``end`` 那一端**向外**的单位方向。"""
    p, q = (segment[1], segment[0]) if end else (segment[0], segment[1])
    length = math.hypot(q[0] - p[0], q[1] - p[1])
    if length <= 1e-12:
        return (0.0, 0.0)
    return ((q[0] - p[0]) / length, (q[1] - p[1]) / length)


def _sin_between(one: Segment, two: Segment) -> float:
    r = (one[1][0] - one[0][0], one[1][1] - one[0][1])
    q = (two[1][0] - two[0][0], two[1][1] - two[0][1])
    scale = math.hypot(r[0], r[1]) * math.hypot(q[0], q[1])
    if scale <= 1e-12:
        return 1.0
    return abs(_cross2(r[0], r[1], q[0], q[1])) / scale


def _proper_cross(one: Segment, two: Segment) -> Point | None:
    """两条线段**内部**相交的交点（端点附近不算，那里归结点配对）。"""
    r = (one[1][0] - one[0][0], one[1][1] - one[0][1])
    q = (two[1][0] - two[0][0], two[1][1] - two[0][1])
    d = _cross2(r[0], r[1], q[0], q[1])
    if abs(d) < 1e-12:
        return None
    wx, wy = two[0][0] - one[0][0], two[0][1] - one[0][1]
    u = _cross2(wx, wy, q[0], q[1]) / d
    v = _cross2(wx, wy, r[0], r[1]) / d
    margin = 1e-6
    if u <= margin or u >= 1 - margin or v <= margin or v >= 1 - margin:
        return None
    return (one[0][0] + u * r[0], one[0][1] + u * r[1])


def weave(segments: list[Segment]) -> tuple[list[Strand], list[Crossing]]:
    """线段 → 股线 + 交叉（**不决定上下**）。

    四步（与 JS 版一致）：

    1. **去重**：相邻两砖会各自生成共享边上的射线，两端点相同的线段只留一条；
    2. **结点配对**：每个结点上的线段端点两两配对——两端相遇是拐角（同一根线的延续），
       四端相遇是**直通**（取最接近反向的那一对）。配不成对的端点是股线末端
       （镶嵌边界上那些度 1 的结点）；
    3. **追股线**：沿配对表一直走，绕回起点的就是闭合股线；
    4. **找交叉**：结点处的四端相遇 + 砖内两条线段真正相交（用平均段长的 1.5 倍做
       栅格，只测邻近的对）。

    结点度既不是 2 也不是 4 时**不配对**——这在镶嵌边界上很正常（度 1），
    内部则说明几何退化了（``gap`` 很大会让射线在边内交叉，但结点度数不变）。
    """
    seen: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    kept: list[tuple[Segment, tuple[int, int], tuple[int, int]]] = []
    for segment in segments:
        a, b = segment
        if math.hypot(a[0] - b[0], a[1] - b[1]) < 1e-9:
            continue
        ka, kb = _key(a), _key(b)
        index = (ka, kb) if ka < kb else (kb, ka)
        if index in seen:
            continue
        seen.add(index)
        kept.append((segment, ka, kb))

    segments = [item[0] for item in kept]

    nodes: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for number, (_segment, ka, kb) in enumerate(kept):
        nodes.setdefault(ka, []).append((number, 0))
        nodes.setdefault(kb, []).append((number, 1))

    # link[(段号, 端点)] = 同一点上接下去的那一段及其**入口**端点
    link: dict[tuple[int, int], tuple[int, int]] = {}
    node_crossings: list[tuple[Point, int, int]] = []
    for ends in nodes.values():
        if len(ends) == 2:
            link[(ends[0][0], ends[0][1])] = ends[1]
            link[(ends[1][0], ends[1][1])] = ends[0]
        elif len(ends) == 4:
            directions = [_unit(segments[i], end) for i, end in ends]
            # 与第一个端点最"反向"（点积最小）的那个就是它的直通对端
            best, best_dot = 1, 2.0
            for j in range(1, 4):
                dot = directions[0][0] * directions[j][0] + directions[0][1] * directions[j][1]
                if dot < best_dot:
                    best_dot, best = dot, j
            rest = [j for j in (1, 2, 3) if j != best]
            for x, y in ((0, best), (rest[0], rest[1])):
                link[(ends[x][0], ends[x][1])] = ends[y]
                link[(ends[y][0], ends[y][1])] = ends[x]
            entry = ends[0]
            node_crossings.append(
                (
                    segments[entry[0]][1] if entry[1] else segments[entry[0]][0],
                    ends[0][0],
                    ends[rest[0]][0],
                )
            )
        # 其它度数：不配对 → 股线在这里收尾

    strand_of = [-1] * len(segments)
    #: 段号 → 该段在自己股线上的弧长起点与两个端点（交叉点换算弧长要用）。
    at: list[tuple[float, Point, Point]] = [(0.0, (0.0, 0.0), (0.0, 0.0))] * len(segments)
    strands: list[Strand] = []
    for start in range(len(segments)):
        if strand_of[start] >= 0:
            continue
        number = len(strands)
        # 向前回溯到开路股线的起点（闭合的话绕一圈回到自己）
        cursor, end = start, 0
        for _ in range(len(segments)):
            nxt = link.get((cursor, end))
            if nxt is None or nxt[0] == start:
                break
            cursor, end = nxt[0], 1 - nxt[1]
        points = [segments[cursor][1] if end else segments[cursor][0]]
        walked: list[int] = []
        closed = False
        for _ in range(len(segments) + 1):
            strand_of[cursor] = number
            walked.append(cursor)
            points.append(segments[cursor][0] if end else segments[cursor][1])
            nxt = link.get((cursor, 1 - end))
            if nxt is None:
                break
            if strand_of[nxt[0]] == number:
                closed = True
                break
            cursor, end = nxt[0], nxt[1]

        # 弧长表按"末点仍重复首点"的点列建立（每个段都必须记上）
        offset = 0.0
        for k, segment_index in enumerate(walked):
            p, q = points[k], points[k + 1]
            at[segment_index] = (offset, p, q)
            offset += math.hypot(q[0] - p[0], q[1] - p[1])
        if closed and len(points) > 1 and points[0] == points[-1]:
            points.pop()  # 闭合标记已经表达了收口那条边，别再重复一次首点
        strands.append(Strand(points, closed, tuple(walked)))

    def where(segment_index: int, point: Point) -> tuple[int, float]:
        """交叉点在它那根股线上的 ``(股线号, 弧长)``。"""
        offset, start, _end = at[segment_index]
        return (
            strand_of[segment_index],
            offset + math.hypot(point[0] - start[0], point[1] - start[1]),
        )

    crossings = [
        Crossing(point, _sin_between(segments[one], segments[two]), where(one, point), where(two, point))
        for point, one, two in node_crossings
    ]

    # 砖内的真交叉：用平均段长 1.5 倍的栅格找邻近对
    total_length = sum(
        math.hypot(s[1][0] - s[0][0], s[1][1] - s[0][1]) for s in segments
    )
    cell = max(1e-6, (total_length / max(1, len(segments))) * 1.5)
    grid: dict[tuple[int, int], list[int]] = {}
    for number, ((a, b), _ka, _kb) in enumerate(kept):
        gx0 = math.floor(min(a[0], b[0]) / cell)
        gx1 = math.floor(max(a[0], b[0]) / cell)
        gy0 = math.floor(min(a[1], b[1]) / cell)
        gy1 = math.floor(max(a[1], b[1]) / cell)
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                grid.setdefault((gx, gy), []).append(number)

    tested: set[tuple[int, int]] = set()
    for bucket in grid.values():
        for x in range(len(bucket)):
            for y in range(x + 1, len(bucket)):
                one, two = bucket[x], bucket[y]
                pair = (one, two) if one < two else (two, one)
                if pair in tested:
                    continue
                tested.add(pair)
                if (
                    kept[one][1] == kept[two][1]
                    or kept[one][1] == kept[two][2]
                    or kept[one][2] == kept[two][1]
                    or kept[one][2] == kept[two][2]
                ):
                    continue  # 共用端点：交给结点配对
                point = _proper_cross(segments[one], segments[two])
                if point is None:
                    continue
                crossings.append(
                    Crossing(
                        point,
                        _sin_between(segments[one], segments[two]),
                        where(one, point),
                        where(two, point),
                    )
                )

    # 上下关系**不在这里定**：线条 / 带子样式用不上，只有交织样式需要，
    # 由调用方显式调 over_under()（交叉点此时 over 为空串）。
    return strands, crossings


def over_under(strands: list[Strand], crossings: list[Crossing]) -> int:
    """沿股线交替地定下每个交叉点谁压谁，返回**没能严格交替**的处数。

    规则只有一条：**同一根股线上相邻的两次经过，必须一上一下**。相邻关系是沿弧长
    排的，所以把每根股线上的交叉按弧长排序，再在"相邻两次经过"之间传一个异或方程::

        over(第 k 次经过) = 该股线的相位 XOR (k & 1)

    一次交叉有两根股线各经过一次，两者必须一个压上一个压下，于是::

        相位[b] = 相位[a] XOR 1 XOR (序号_a & 1) XOR (序号_b & 1)

    沿约束图 BFS 一遍就定下来。这不是"每个交叉随手翻一下"的近似：同一条线上相邻的
    两次经过必然一上一下，编织感就来自这里。

    **无解的情况**：自交（同一条股线自己压自己）会退化成
    ``1 XOR (序号_a & 1) XOR (序号_b & 1) == 0``，即那两次的序号必须奇偶不同；股线
    路径绕成奇环时也一样。遇到这种块，按发现顺序从下一个没定的交叉重新播种（等式
    左边先到先得），返回的计数就是"理想交替被打破了几处"——参考实现也是这么做的，
    宁可标出来，不假装严格。

    返回值写进面板读数（``mismatches``）。实测十种镶嵌 × 角度 10°~80° × gap 0~0.4
    全部为 0；只有 ``gap > 0`` 时股线被拆成小环，才可能出现非零。
    """
    #: 每根股线上的经过：(弧长, 交叉号, 哪一侧)，按弧长排序
    along: list[list[tuple[float, int, str]]] = [[] for _ in strands]
    for index, crossing in enumerate(crossings):
        along[crossing.a[0]].append((crossing.a[1], index, "a"))
        along[crossing.b[0]].append((crossing.b[1], index, "b"))
    for items in along:
        items.sort()

    #: (交叉号, 哪一侧) → 它在该股线上是第几次经过
    position: dict[tuple[int, str], tuple[int, int]] = {}
    for strand_index, items in enumerate(along):
        for order, (_arc, crossing_index, side) in enumerate(items):
            position[(crossing_index, side)] = (strand_index, order)

    for seed in range(len(crossings)):
        if crossings[seed].over:
            continue
        crossings[seed].over = "a"
        queue = [seed]
        while queue:
            current = queue.pop()
            crossing = crossings[current]
            for side in ("a", "b"):
                strand_index, order = position[(current, side)]
                is_over = crossing.over == side
                for neighbour in (order - 1, order + 1):
                    if not (0 <= neighbour < len(along[strand_index])):
                        continue
                    _arc, other_index, other_side = along[strand_index][neighbour]
                    other = crossings[other_index]
                    if other.over:
                        continue
                    # 相邻的两次经过一上一下：这一段压上，那一段就压下，反之亦然
                    other.over = ("b" if other_side == "a" else "a") if is_over else other_side
                    queue.append(other_index)

    # 统计"理想交替"被打破的处数（相邻两次经过同上或同下）
    mismatches = 0
    for strand_index, items in enumerate(along):
        for k in range(1, len(items)):
            _a, first_index, first_side = items[k - 1]
            _b, second_index, second_side = items[k]
            first_over = crossings[first_index].over == first_side
            second_over = crossings[second_index].over == second_side
            if first_over == second_over:
                mismatches += 1
    return mismatches


# --- 版权与许可 -----------------------------------------------------------
#
# 本模块的镶嵌表（TILINGS）与 ``tiles_in`` / ``motif`` / ``weave`` / ``_snub_triangles``
# 移植自 islamic-patterns 的 js/girih.js：
#
#     islamic-patterns — https://github.com/evoluteur/islamic-patterns
#     Copyright (c) 2026 Olivier Giulieri
#     MIT License