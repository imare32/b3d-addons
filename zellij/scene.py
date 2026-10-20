"""渲染无关的场景层：把 ``Design`` 变成扁平的 ``Shape`` 列表。

坐标约定
--------
* 设计在 p5 坐标系里算（y 向下），最后一步统一翻转成 Blender 的 y 向上。
* 结果落在 ``[0, size]`` 的正方形内；``margin == 0`` 时设计包围盒的
  左下角正好对齐原点（FR-3）。

``Shape`` 只带点列 / 填充色 / 描边色 / 是否闭合，任何后端（Grease Pencil、
SVG、mesh ...）都可以直接消费。
"""

from __future__ import annotations

from dataclasses import dataclass

from .design import RGB, Design, filler_transform, match_filler, rand_fill
from .geom import Box, fill_box, make_affine, mul, point_add, point_scale, point_sub
from .rng import Mulberry32

__all__ = [
    "Shape",
    "VIEW_MODES",
    "VIEW_ZELLIJ",
    "VIEW_TILING",
    "VIEW_LINES",
    "VIEW_POLYGONS",
    "LAYER_FILLS",
    "LAYER_TILING",
    "LAYER_LINES",
    "LAYER_ORDER",
    "build_shapes",
    "build_layers",
]

RGBF = tuple[float, float, float]

VIEW_ZELLIJ = "zellij"
VIEW_TILING = "tiling"
VIEW_LINES = "lines"
VIEW_POLYGONS = "polygons"

#: 面板 View 下拉里的四种模式（顺序即菜单顺序）。
VIEW_MODES = (VIEW_ZELLIJ, VIEW_TILING, VIEW_LINES, VIEW_POLYGONS)

BLACK: RGBF = (0.0, 0.0, 0.0)
RED: RGBF = (1.0, 0.0, 0.0)

#: 构造线沿画布宽度的采样段数：间距 = 包围盒宽度 / 该值（虚线疏密与线长无关）。
LINE_SAMPLES_PER_WIDTH = 320

#: 拓扑分类 → Grease Pencil 图层（后端按此分层）。
#: ``Fills`` 会在后端再按填充色拆成多层；``Tiling`` 是红色瓷砖轮廓，``Lines`` 是构造线。
LAYER_FILLS = "Fills"
LAYER_TILING = "Tiling"
LAYER_LINES = "Lines"
LAYER_OUTLINES = LAYER_TILING
LAYER_ORDER = (LAYER_FILLS, LAYER_TILING, LAYER_LINES)


@dataclass(frozen=True, slots=True)
class Shape:
    """一条待渲染的路径。"""

    points: list[tuple[float, float]]
    fill: RGBF | None
    stroke: RGBF | None
    closed: bool
    layer: str = LAYER_FILLS


def _rgb8_to_float(colour: RGB) -> RGBF:
    return (colour[0] / 255.0, colour[1] / 255.0, colour[2] / 255.0)


def _box_of_points(points) -> Box:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return Box(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def _box_of_shapes(shapes) -> Box:
    return _box_of_points([p for s in shapes for p in s.points])


def _guard_box(box: Box) -> Box:
    """退化包围盒保护（原实现在这里会除以 0）。"""
    if box.w > 1e-9 and box.h > 1e-9:
        return box
    return Box(box.x, box.y, max(box.w, 1e-6), max(box.h, 1e-6))


def _tile_shapes(matrix, path, design: Design, rng: Mulberry32) -> list[Shape]:
    """原 ``drawTile``：签名匹配 → 图案坐标 → 世界坐标 + 颜色抖动。"""
    rotated, _signature, clusters = match_filler(path, rng)
    if rotated is None or not clusters:
        return []

    cluster = clusters[int(rng.random() * len(clusters))]
    transform = mul(matrix, filler_transform(cluster["bounds"], rotated[0], rotated[1]))

    shapes = []
    for sub in cluster["shapes"]:
        coords = sub["path"]
        points = [
            mul(transform, (coords[i], coords[i + 1]))
            for i in range(0, len(coords) - 1, 2)
        ]
        if len(points) < 3:
            continue
        colour = _rgb8_to_float(rand_fill(design, sub["colour"], design.shimmer, rng))
        shapes.append(Shape(points, colour, None, True))
    return shapes


def _line_shapes(matrix, design: Design, size: float) -> list[Shape]:
    """原 ``drawLines`` 的构造线部分。

    两端各延长 ``size``（原实现是 ``min(width, height)``，即画布边长），
    延长量在屏幕空间里加，所以构造线会溢出画布（与原实现一致）。
    """
    shapes = []
    for line in design.lines:
        center = mul(matrix, line.pos)
        direction = point_scale(line.dir, size)
        shapes.append(
            Shape(
                [point_add(center, direction), point_sub(center, direction)],
                None,
                BLACK,
                False,
                LAYER_LINES,
            )
        )
    return shapes


def _outline_shapes(matrix, design: Design, layer: str = LAYER_TILING) -> list[Shape]:
    """瓷砖轮廓（未填充），``Tiling`` 图层用。"""
    shapes = []
    for tile in design.tiles:
        points = [mul(matrix, p) for p in tile.path]
        if len(points) < 3:
            continue
        shapes.append(Shape(points, None, RED, True, layer))
    return shapes


def _convex_hull(points):
    """Andrew 单调链，输入 ``[(x, y), ...]``，返回逆时针凸包顶点。"""
    unique = sorted(set(points))
    if len(unique) <= 2:
        return unique

    def cross(origin, a, b):
        return (a[0] - origin[0]) * (b[1] - origin[1]) - (a[1] - origin[1]) * (
            b[0] - origin[0]
        )

    lower: list[tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def _clip_ray_polygon(pos, direction, polygon):
    """把过 ``pos``、方向 ``direction`` 的直线裁到凸多边形，返回两个端点或 ``None``。"""
    params = []
    count = len(polygon)
    for index in range(count):
        a = polygon[index]
        b = polygon[(index + 1) % count]
        ex, ey = b[0] - a[0], b[1] - a[1]
        det = ex * direction[1] - direction[0] * ey
        if abs(det) < 1e-12:
            continue
        wx, wy = pos[0] - a[0], pos[1] - a[1]
        t = (wx * ey - ex * wy) / det
        s = (wx * direction[1] - direction[0] * wy) / det
        if -1e-9 <= s <= 1.0 + 1e-9:
            params.append(t)
    if len(params) < 2:
        return None
    low, high = min(params), max(params)
    if abs(high - low) < 1e-9:
        return None
    return point_add(pos, point_scale(direction, low)), point_add(
        pos, point_scale(direction, high)
    )


def _line_shapes_local(
    design: Design, polygon, step: float, layer: str = LAYER_LINES
) -> list[Shape]:
    """构造线（设计空间），裁到瓷砖区域的凸包内，并按**固定间距** ``step`` 采样。

    原 p5 草图靠画布裁剪来“截断”构造线，Blender 没有画布裁剪，按原样延长会
    拖出主体一大截；裁到包围盒又会把空角落留下来，所以这里裁到实际拼贴区域的凸包。

    采样必须按固定弧长：Dash 修改器是在顶点之间切分的，点数固定时短线的点密、
    长线的点稀，虚线的疏密就会随线长变化。这里每条线都按同一个 ``step`` 均分。
    """
    shapes = []
    for line in design.lines:
        clipped = _clip_ray_polygon(line.pos, line.dir, polygon)
        if clipped is None:
            continue
        start, end = clipped
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = (dx * dx + dy * dy) ** 0.5
        count = max(2, int(round(length / step)) + 1)
        ux = dx / (count - 1)
        uy = dy / (count - 1)
        points = [(start[0] + ux * i, start[1] + uy * i) for i in range(count)]
        shapes.append(Shape(points, None, BLACK, False, layer))
    return shapes


def build_shapes(
    design: Design,
    rng: Mulberry32 | None = None,
    *,
    size: float = 1.0,
    mode: str = VIEW_ZELLIJ,
    margin: float = 0.0,
    allow_rotation: bool = False,
    fit: str = "shapes",
) -> list[Shape]:
    """生成一份场景的 ``Shape`` 列表。

    ``mode`` 取 ``VIEW_MODES`` 之一；``margin == 0`` 时设计正好贴住原点。
    ``allow_rotation`` 对应原实现的 ``fillBox`` 旋转分支（正方形画布上会直接
    转 90°，所以默认关闭，与原草图 DEBUG 打开时的行为保持一致）。

    ``fit`` 决定缩放基准：``"shapes"``（默认）用实际图形包围盒，保证结果
    严格落在 ``[0, size]``；``"boundary"`` 用交点边界，与原草图的构图完全
    一致（填充图案会略微溢出画布）。
    """
    if size <= 0.0:
        raise ValueError("size must be positive")
    if mode not in VIEW_MODES:
        raise ValueError(f"unknown view mode: {mode!r}")
    if fit not in ("shapes", "boundary"):
        raise ValueError(f"unknown fit: {fit!r}")

    if rng is None:
        rng = design.rng()

    margin = max(0.0, min(float(margin), size * 0.5 - 1e-6))
    canvas = Box(margin, margin, size - 2.0 * margin, size - 2.0 * margin)

    if mode in (VIEW_ZELLIJ, VIEW_TILING):
        # 图案匹配始终在原始设计空间完成，缩放只在最后一步做。
        raw: list[Shape] = []
        if mode == VIEW_ZELLIJ:
            for tile in design.tiles:
                raw.extend(_tile_shapes(_IDENTITY, tile.path, design, rng))
        else:
            raw = _outline_shapes(_IDENTITY, design)

        if fit == "boundary" and design.boundary:
            box = _box_of_points(design.boundary)
        else:
            box = _box_of_shapes(raw) if raw else canvas
        box = _guard_box(box)
        matrix = fill_box(box, canvas, allow_rotation)
        if fit == "boundary":
            # 与原草图构图完全一致：只做 y 镜像，不再对齐包围盒
            flip = make_affine(1.0, 0.0, 0.0, 0.0, -1.0, size)
        else:
            flip = _flip_matrix(_transform_box(matrix, box), margin)
        return _apply(raw, mul(flip, matrix))

    # lines / polygons：以整张交点网格为基准，构造线外溢属预期行为。
    grid_box = Box(0.0, 0.0, float(design.grid_side), float(design.grid_side))
    matrix = fill_box(grid_box, canvas, allow_rotation)
    raw = _line_shapes(matrix, design, size)
    if mode == VIEW_POLYGONS:
        raw.extend(_outline_shapes(matrix, design))
    return _apply(raw, _flip_matrix(canvas, margin))


def build_layers(
    design: Design,
    rng: Mulberry32 | None = None,
    *,
    size: float = 1.0,
    margin: float = 0.0,
    allow_rotation: bool = False,
) -> list[Shape]:
    """一次性生成所有拓扑的 ``Shape``，并共用同一个变换矩阵。

    各视图分开算会各自拟合包围盒，叠在一起就会错位；这里以填充图案的包围盒
    为准只算一次 ``matrix``，轮廓与构造线用同一个矩阵，图层才能严丝合缝地叠起来。

    图层：``Fills``（成品马赛克，后端再按颜色拆分）、``Tiling``（红色瓷砖轮廓）、
    ``Lines``（构造线，裁到图案包围盒内）。
    """
    if size <= 0.0:
        raise ValueError("size must be positive")

    if rng is None:
        rng = design.rng()

    margin = max(0.0, min(float(margin), size * 0.5 - 1e-6))
    canvas = Box(margin, margin, size - 2.0 * margin, size - 2.0 * margin)

    fills: list[Shape] = []
    for tile in design.tiles:
        fills.extend(_tile_shapes(_IDENTITY, tile.path, design, rng))

    box = _guard_box(_box_of_shapes(fills) if fills else canvas)
    matrix = fill_box(box, canvas, allow_rotation)
    transform = mul(_flip_matrix(_transform_box(matrix, box), margin), matrix)

    shapes = _dedupe(_apply(fills, transform))
    shapes.extend(_apply(_outline_shapes(_IDENTITY, design, LAYER_TILING), transform))

    hull = _convex_hull([point for shape in fills for point in shape.points])
    if len(hull) >= 3:
        step = max(box.w, box.h) / LINE_SAMPLES_PER_WIDTH
        shapes.extend(_apply(_line_shapes_local(design, hull, step), transform))
    return shapes


_IDENTITY = make_affine(1.0, 0.0, 0.0, 0.0, 1.0, 0.0)


def _dedupe(shapes: list[Shape], digits: int = 4) -> list[Shape]:
    """去掉几何重合的填充块（保留最上面那一份，颜色以盖在上面的为准）。

    填充是实心的，重合块在屏幕上只看得见最上面一层；但半透明描边会因此叠画两次，
    边界会显得更深更粗。相邻瓷砖常常被匹配到同一个图案簇，投影到同一个位置后
    只差几个 1e-5，所以按 4 位小数量化比较。
    """
    seen = set()
    kept = []
    for shape in reversed(shapes):
        key = (
            len(shape.points),
            tuple(
                sorted((round(x, digits), round(y, digits)) for x, y in shape.points)
            ),
        )
        if key in seen:
            continue
        seen.add(key)
        kept.append(shape)
    kept.reverse()
    return kept


def _apply(shapes: list[Shape], matrix) -> list[Shape]:
    return [
        Shape(
            [mul(matrix, p) for p in shape.points],
            shape.fill,
            shape.stroke,
            shape.closed,
            shape.layer,
        )
        for shape in shapes
    ]


def _transform_box(matrix, box: Box) -> Box:
    corners = [
        mul(matrix, (box.x, box.y)),
        mul(matrix, (box.x + box.w, box.y)),
        mul(matrix, (box.x, box.y + box.h)),
        mul(matrix, (box.x + box.w, box.y + box.h)),
    ]
    return _box_of_points(corners)


def _flip_matrix(reference: Box, margin: float):
    """p5 (y 向下) → Blender (y 向上)，并把参考框左下角对齐到 ``(margin, margin)``。"""
    # x' = x - ref_min_x + margin；y' = ref_max_y - y + margin
    return make_affine(
        1.0,
        0.0,
        margin - reference.x,
        0.0,
        -1.0,
        reference.y + reference.h + margin,
    )
