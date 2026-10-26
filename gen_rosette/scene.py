"""场景装配层：``Design`` → 扁平的 ``Shape`` 列表（世界坐标）。

坐标约定与 ``gen_zellij`` 一致：

* 设计在 y 向下的平面坐标系里算（玫瑰线中心是原点），装配的最后一步统一翻成 Blender
  的 **y 向上**；
* 结果落在 ``[margin, margin + size]`` 的正方形内，等比缩放、居中；
* ``Shape`` 只带点列 / 填充色 / 描边色 / 是否闭合 / 图层名，任何后端都能消费。

四个图层（从下到上）：

``Faces``
    色块：同一张平面化边网切出来的封闭区域（:mod:`faces`），按质心半径分圈取色。
    带子两侧各压掉半个带宽，所以色块正好顶到带子边缘，不留白缝。
``Rosette Case``
    编织带的外圈描边（更粗、更深）。它永远在所有本体下面，露出来的那一圈就是蕾丝边。
``Rosette``
    编织带本体：交叉点上交替压上 / 压下，压下的那一段在几何里被切掉缺口
    （见 :mod:`weave`），缺口处再补一块沿对方边缘裁齐的多边形。
``Guides``
    构造辅助线：第 ``level`` 阶的核 + 玫瑰线中心线（跳步合并后的连续曲线，
    一条轨道一笔画到底）。默认隐藏，用来核对几何，也可直接驱动生长动画。
"""

from __future__ import annotations

from dataclasses import dataclass

from .design import Design, colour_for_radius
from .faces import interior_faces
from .weave import WeaveStyle, build_weave, edge_network

__all__ = [
    "LAYER_BAND",
    "LAYER_CASE",
    "LAYER_FACES",
    "LAYER_GUIDES",
    "LAYER_ORDER",
    "RGBF",
    "Shape",
    "build_layers",
]

RGBF = tuple[float, float, float]

#: 图层名（索引越大越靠上；建层顺序 = ``Shape.layer`` 首次出现顺序）。
LAYER_FACES = "Faces"
LAYER_CASE = "Rosette Case"
LAYER_BAND = "Rosette"
LAYER_GUIDES = "Guides"
LAYER_ORDER = (LAYER_FACES, LAYER_CASE, LAYER_BAND, LAYER_GUIDES)

BLACK: RGBF = (0.0, 0.0, 0.0)


@dataclass(frozen=True, slots=True)
class Shape:
    """一条待渲染的路径。"""

    points: list[tuple[float, float]]
    fill: RGBF | None
    stroke: RGBF | None
    closed: bool
    layer: str = LAYER_FACES
    #: ``(起点平头, 终点平头)``；默认圆头。平头是 GP 的 ``start_cap`` / ``end_cap``。
    caps: tuple[bool, bool] = (False, False)


# --- 归一化 ---------------------------------------------------------------


class _Frame:
    """设计坐标 → 画布坐标的等比变换（居中 + y 翻转）。"""

    __slots__ = ("mid_x", "mid_y", "scale", "span", "target_x", "target_y")

    def __init__(self, curves, size: float, margin: float):
        xs = [p[0] for curve in curves for p in curve.points]
        ys = [p[1] for curve in curves for p in curve.points]
        lo_x, hi_x = min(xs), max(xs)
        lo_y, hi_y = min(ys), max(ys)
        box_w = max(hi_x - lo_x, 1e-9)
        box_h = max(hi_y - lo_y, 1e-9)
        canvas = size - 2.0 * margin
        self.scale = min(canvas / box_w, canvas / box_h)
        self.span = max(box_w, box_h)
        self.mid_x = 0.5 * (lo_x + hi_x)
        self.mid_y = 0.5 * (lo_y + hi_y)
        self.target_x = margin + 0.5 * canvas
        self.target_y = margin + 0.5 * canvas

    def map(self, point) -> tuple[float, float]:
        return (
            self.target_x + (point[0] - self.mid_x) * self.scale,
            self.target_y - (point[1] - self.mid_y) * self.scale,
        )

    def map_all(self, points) -> list[tuple[float, float]]:
        return [self.map(point) for point in points]


def _rgb8(colour) -> RGBF:
    return (colour[0] / 255.0, colour[1] / 255.0, colour[2] / 255.0)


# --- 面 -------------------------------------------------------------------


def _face_shapes(design: Design, frame: _Frame) -> list[Shape]:
    """色块：设计空间里切面（原点 = 花心，半径才有意义），再整体变换到画布。"""
    polygons = [curve.points for curve in design.curves]
    points, edges = edge_network(polygons)
    faces = interior_faces(points, edges, frame.span)
    if not faces:
        return []

    max_radius = max(face.radius for face in faces) or 1.0
    shapes: list[Shape] = []
    for face in faces:
        colour = _rgb8(
            colour_for_radius(
                face.radius, max_radius, design.colours, design.face_slots
            )
        )
        shapes.append(
            Shape(frame.map_all(face.points), colour, None, True, LAYER_FACES)
        )
    return shapes


# --- 编织 -----------------------------------------------------------------


def _weave_shapes(design: Design, frame: _Frame, style: WeaveStyle) -> list[Shape]:
    """编织：在**世界坐标**里编（带宽、缺口都是世界单位），所以先变换再编织。"""
    polygons = [frame.map_all(curve.points) for curve in design.curves]
    if not polygons:
        return []

    case_colour = _rgb8(design.colours[0])
    band_colour = _rgb8(design.colours[1])
    result = build_weave(polygons, style)

    shapes: list[Shape] = []
    if result.paths:
        # 描边层：整根带子（``tuck`` 下不切口——它永远在最下面，切了也看不出来）
        if style.casing > 0.0:
            shapes.extend(
                Shape(points, None, case_colour, closed, LAYER_CASE, caps)
                for (points, closed), caps in zip(result.paths, result.caps)
            )
        body = result.body_paths or result.paths
        body_caps = result.body_caps or result.caps
        shapes.extend(
            Shape(points, None, band_colour, closed, LAYER_BAND, caps)
            for (points, closed), caps in zip(body, body_caps)
        )
        # 本体退出去留下的缺口：沿对方边缘裁齐的凸多边形，只填不描
        shapes.extend(
            Shape(points, band_colour, None, True, LAYER_BAND) for points in result.tips
        )
    else:
        # 退化配置（例如 n=4 的某些高阶核把所有点并成了同一点）：至少把原始曲线画出来
        shapes.extend(
            Shape(points, None, band_colour, curve.closed, LAYER_BAND)
            for curve, points in zip(design.curves, polygons)
        )
    return shapes


# --- 辅助线 ---------------------------------------------------------------


def _guide_shapes(design: Design, frame: _Frame) -> list[Shape]:
    """辅助线：第 ``level`` 阶的核 + 玫瑰线中心线（**连续**的，一笔到底）。

    中心线取 :attr:`Design.curves`（跳步重排后合并过的），**不是**逐片花瓣：
    按片画会得到 ``n`` 条互不相连的短路径，端点各自断开，"连着长出来"的动画就没了。
    合并后是 ``gcd(n, level + 1)`` 条首尾相接的曲线（步长与 ``n`` 互质时整朵花就一笔），
    画出来的线段与逐片画**完全相同**，只是点序变成了真正的绘制顺序、
    接缝处的重复点被去掉 —— 于是可以直接拿去驱动生长动画（GP 的 Build 修改器、
    几何节点按长度展开之类都只认一条笔划的点序）。
    """
    colour = _rgb8(design.colours[0])
    shapes = [Shape(frame.map_all(design.kernel), None, colour, False, LAYER_GUIDES)]
    shapes.extend(
        Shape(frame.map_all(curve.points), None, colour, curve.closed, LAYER_GUIDES)
        for curve in design.curves
    )
    return shapes


# --- 顶层 -----------------------------------------------------------------


def build_layers(
    design: Design,
    *,
    size: float = 10.0,
    margin: float = 0.0,
    weave_style: WeaveStyle | None = None,
) -> list[Shape]:
    """一次生成所有图层的 ``Shape``（扁平列表，按 ``Shape.layer`` 过滤即得分层）。

    四个图层**总是全部生成**（面板只切显隐，不重建几何）。所有图层共用**同一个**
    变换，所以色块边界、带子中心线、辅助线严格对齐。

    ``weave_style`` 为 ``None``（或 ``half_width <= 0``）时不编织，只剩色块与辅助线。
    编织的缺口、端头平/圆都是几何，所以**改带宽 / 描边倍数必须重新生成**
    （见 ``weave._end_cut``）。
    """
    if size <= 0.0:
        raise ValueError("size must be positive")
    if not design.curves:
        raise ValueError("design has no curves")

    margin = max(0.0, min(float(margin), size * 0.5 - 1e-6))
    frame = _Frame(design.curves, size, margin)

    shapes: list[Shape] = []
    shapes.extend(_face_shapes(design, frame))
    if weave_style is not None and weave_style.half_width > 0.0:
        shapes.extend(_weave_shapes(design, frame, weave_style))
    shapes.extend(_guide_shapes(design, frame))
    return shapes
