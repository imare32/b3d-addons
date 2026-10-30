"""场景装配层：镶嵌 + 接触角 → 扁平的 ``Shape`` 列表（世界坐标）。

坐标约定
--------
* 图案先在**图案单位**里算（砖的边长恒为 1），再乘 ``size``（世界单位 / 边长）
  得到世界坐标；
* 画布边长 ``canvas``，图案以**第一块砖的中心**对齐画布中心 —— 与 JS 版
  （``cx = W/2 + p·k``）完全同一套坐标，便于逐项对照；
* **不做 y 翻转**：girih 的图案没有"上"的方向，翻与不翻都是同一类花样，省一层
  变换更容易和参考实现对账。

``Shape`` 只带点列 / 填充色 / 描边色 / 是否闭合 / 端头 / 图层名，任何后端（Grease
Pencil、SVG、mesh …）都能直接消费。

样式
----
``STYLE_LINES``
    单层 ``Lines``：一根线一支笔，最省。
``STYLE_BANDS``
    两层：``Case``（更粗的描边，画在下面）+ ``Band``（本体）。露出来的那一圈就是
    蕾丝边。
``STYLE_INTERLACE``
    在带子的交叉点分上下：本体压在下面的那一段退到对方 footprint 之外（平头），
    缺口用补片贴平；描边层整根不切，所以那一圈蕾丝边始终连续。**默认样式。**

**宽度在两种样式里的地位不同**：线条 / 带子的宽度只是笔划半径（改它不用重算几何）；
交织样式的缺口按"可见半宽"算，**宽度与描边倍数都是几何参数**。

可选的两层
----------
``Fills``
    每块砖的**星形轮廓**填色（``motif_and_star`` 的 star 点列），画在最底下。
    与参考实现的 ``fill`` 一致：按砖的边数分色时，同一块砖的星心同色。
``Tiling``
    镶嵌本身的砖轮廓，纯调试用，默认不出。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import girih, interlace
from .palette import AZURITE, CINNABAR, MOSS, OCHRE, hex_to_rgb

__all__ = [
    "FILL_BY_SIDES",
    "LAYER_BAND",
    "LAYER_CASE",
    "LAYER_FILLS",
    "LAYER_LINES",
    "LAYER_ORDER",
    "LAYER_TILING",
    "MAX_EDGES",
    "RGBF",
    "STYLE_BANDS",
    "STYLE_INTERLACE",
    "STYLE_LINES",
    "STYLES",
    "Shape",
    "build_shapes",
]

RGBF = tuple[float, float, float]

STYLE_LINES = "LINES"
STYLE_BANDS = "BANDS"
STYLE_INTERLACE = "INTERLACE"
STYLES = (STYLE_LINES, STYLE_BANDS, STYLE_INTERLACE)

#: 图层名（索引越大越靠上；建层顺序 = ``Shape.layer`` 首次出现的顺序）。
LAYER_FILLS = "Fills"
LAYER_LINES = "Lines"
LAYER_CASE = "Case"
LAYER_BAND = "Band"
LAYER_TILING = "Tiling"
LAYER_ORDER = (LAYER_FILLS, LAYER_LINES, LAYER_CASE, LAYER_BAND, LAYER_TILING)

#: 按砖的边数分色（星形填充）。取自项目色板，与参考实现的 ``P.fill[sides]`` 同构：
#: 三角形苔绿、正方形石青、六边形赭石、十二边形朱砂。表里没有的边数用本体色。
FILL_BY_SIDES: dict[int, RGBF] = {
    3: hex_to_rgb(MOSS),
    4: hex_to_rgb(AZURITE),
    6: hex_to_rgb(OCHRE),
    12: hex_to_rgb(CINNABAR),
}



#: 画布最多容纳多少条砖边（= ``canvas / size``）。
#:
#: 砖数按**面积**涨：默认 15.6 条边是一千多块砖、20 ms 上下；三角形那种最密的镶嵌
#: 在 15.6 条边时已经 60~80 ms，交织再乘上一倍多的形状数。再往上翻一倍就是四倍耗时，
#: 界面会明显卡住，所以这里硬性拦住，让人去调「单元边长」——比让 Blender 假死好。
MAX_EDGES = 64


@dataclass(frozen=True, slots=True)
class Shape:
    """一条待渲染的路径。

    ``fill`` 非空 = 这条要填充（每条一个独立 id，相邻色块不会被合并）；
    ``stroke`` 为空 = 只填不描（端头补片）。``caps`` 是两端是否平头。
    """

    points: list[tuple[float, float]]
    fill: RGBF | None
    stroke: RGBF | None
    closed: bool
    layer: str = LAYER_LINES
    caps: tuple[bool, bool] = (False, False)


def build_shapes(
    tiling: str,
    angle: float,
    gap: float = 0.0,
    *,
    style: str = STYLE_INTERLACE,
    size: float = 0.64,
    canvas: float = 10.0,
    colour: RGBF = (0.0, 0.0, 0.0),
    case_colour: RGBF = (0.0, 0.0, 0.0),
    width: float = 0.035,
    casing: float = 1.35,
    seam: float = 0.15,
    fills: bool = False,
    fill_colour: RGBF = (0.0, 0.0, 0.0),
    fill_by_sides: bool = False,
    fill_colours: dict[int, RGBF] | None = None,
    show_tiles: bool = False,
) -> tuple[list[Shape], dict]:
    """跑一遍「镶嵌 → motif → 股线（→ 交织）」，返回 ``(shapes, 统计)``。

    ``size`` 是砖的边长（世界单位）：它同时决定图案的**尺度**与**密度**——
    画布固定时，``size`` 越小，格子越多、线越密。``gap`` 把每条边的两个出发点
    沿边推开（按边长计），得到"双线"的观感；它只改几何，不改拓扑。

    ``width`` / ``casing`` / ``seam`` 只有交织样式用得着（缺口按可见半宽算），
    其它样式由 ``gp`` 那侧按层写半径，这里只是接受参数、不参与几何。

    ``fills`` 打开时给每块砖的星形轮廓铺底（``Fills`` 层，最下）；``fill_by_sides``
    再按砖的边数取色（``fill_colours`` 给表，默认 :data:`FILL_BY_SIDES`）。
    ``show_tiles`` 出 ``Tiling`` 调试层（砖轮廓，用描边色画在最上）。
    """
    if size <= 0.0:
        raise ValueError("size must be positive")
    if canvas <= 0.0:
        raise ValueError("canvas must be positive")
    if tiling not in girih.TILINGS:
        raise ValueError(f"unknown tiling: {tiling!r}")
    if style not in STYLES:
        raise ValueError(f"unknown style: {style!r}")
    edges = canvas / size
    if edges > MAX_EDGES:
        raise ValueError(
            f"图案太密（画布 = {edges:.0f} 条边，上限 {MAX_EDGES}）："
            "调大「单元边长」或调小「画布」"
        )

    centre = canvas / 2.0
    # 画布换算回图案单位：向外多要一格，免得压边的那圈砖被截掉
    x0 = -centre / size - 1.0
    y0 = -centre / size - 1.0
    x1 = centre / size + 1.0
    y1 = centre / size + 1.0

    tiles = girih.tiles_in(tiling, x0, y0, x1, y1)

    def to_world(point) -> tuple[float, float]:
        return (centre + point[0] * size, centre + point[1] * size)

    segments = []
    stars: list[tuple[list[tuple[float, float]], int]] = []
    for tile in tiles:
        tile_segments, star = girih.motif_and_star(tile, angle, gap)
        for a, b in tile_segments:
            segments.append((to_world(a), to_world(b)))
        if fills:
            stars.append(([to_world(point) for point in star], tile.sides))

    strands, crossings = girih.weave(segments)

    shapes: list[Shape] = []
    extra: dict = {}

    # 星形填充画在最底下（先建层 = 最下层），带子压在上面
    if fills:
        table = FILL_BY_SIDES if fill_colours is None else fill_colours
        shapes.extend(
            Shape(
                points,
                table.get(sides, fill_colour) if fill_by_sides else fill_colour,
                None,
                True,
                LAYER_FILLS,
            )
            for points, sides in stars
            if len(points) >= 3
        )
        extra["fills"] = len(stars)

    if style == STYLE_INTERLACE:
        # 一上一下：只有交织样式需要，算一次几毫秒
        extra["mismatches"] = girih.over_under(strands, crossings)
        reach = max(float(width), float(width) * float(casing))
        woven = interlace.build_weave(
            strands,
            crossings,
            half_width=float(width),
            casing=float(width) * float(casing),
            seam=float(seam) * reach,
        )
        # 描边先出（画在下面）、本体后出：建层顺序就是叠放顺序
        shapes.extend(
            Shape(list(points), None, case_colour, closed, LAYER_CASE)
            for points, closed in woven.casing
            if len(points) >= 2
        )
        # 一整圈都没有切口时（``_spans`` 只剩一段、首尾重合）要按**闭合**画，
        # 否则两端各是平头端帽，接缝处的转角又会张一个黑扇形。
        shapes.extend(
            Shape(
                list(piece.points),
                None,
                colour,
                len(piece.points) > 2
                and abs(piece.points[0][0] - piece.points[-1][0]) < 1e-9
                and abs(piece.points[0][1] - piece.points[-1][1]) < 1e-9,
                LAYER_BAND,
                piece.caps,
            )
            for piece in woven.band
        )
        # 补片：只填不描，与本体同色，画在本体之后（盖住切口那点重叠）
        shapes.extend(
            Shape(list(patch), colour, None, True, LAYER_BAND)
            for patch in woven.patches
            if len(patch) >= 3
        )
        extra.update(woven.stats)
    elif style == STYLE_BANDS:
        shapes.extend(
            Shape(list(strand.points), None, case_colour, strand.closed, LAYER_CASE)
            for strand in strands
            if len(strand.points) >= 2
        )
        shapes.extend(
            Shape(list(strand.points), None, colour, strand.closed, LAYER_BAND)
            for strand in strands
            if len(strand.points) >= 2
        )
    else:
        shapes.extend(
            Shape(list(strand.points), None, colour, strand.closed, LAYER_LINES)
            for strand in strands
            if len(strand.points) >= 2
        )

    # 调试层最后出 = 最上层：砖轮廓压在图案上看（用描边色，跟随配色预设）
    if show_tiles:
        shapes.extend(
            Shape(
                [to_world(vertex) for vertex in tile.vertices],
                None,
                case_colour,
                True,
                LAYER_TILING,
            )
            for tile in tiles
            if len(tile.vertices) >= 3
        )

    stats = {
        "tiles": len(tiles),
        "segments": sum(len(strand.segments) for strand in strands),
        "strands": len(strands),
        "crossings": len(crossings),
        # 描边层是整根股线（点最多但笔划最少），统计只看真正画的那些
        "points": sum(len(shape.points) for shape in shapes if shape.layer != LAYER_CASE),
        "closed": sum(1 for strand in strands if strand.closed),
        "layers": len({shape.layer for shape in shapes}),
    }
    stats.update(extra)
    return shapes, stats
