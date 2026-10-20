"""Grease Pencil v3 后端：把 ``Shape`` 写进固定的 GP 对象（分层）。

要点（全部实测于 Blender 5.2）：

* **颜色不用材质**：笔划颜色来自 ``vertex_color``（``POINT`` 域 ``FLOAT_COLOR``），
  填充颜色来自 ``fill_color``（``CURVE`` 域 ``FLOAT_COLOR``）。两者都按**线性**
  存储，写之前把 8 bit sRGB 转成线性，视口与 SVG 导出才都是设计色。
* **描边色跟随填充色**：填充层的 ``vertex_color`` 不是纯黑，而是该色压暗
  ``STROKE_SHADE`` 后的色，勾边才会和色块同色系。
* **填充靠 ``fill_id``**（``CURVE`` 域 ``INT``）：0 = 不参与填充，非零 = 参与，
  相同 ID 并成一个填充区域。这里每条形状一个 ID，避免相邻形状被合并。
* **``hide_stroke``** 保持 ``False``（SVG 导出器会整条跳过 ``True`` 的笔划），
  笔划宽度交给 ``fill_width`` / ``tiling_width`` / ``lines_width`` 三个滑块：0 时半径与
  不透明度都归 0，视口里真正看不见。
* **不用材质、只留 ``material_index``**：填充 0 / ``Tiling`` 1 / ``Lines`` 2。
  半透明靠 ``Opacity`` 修改器的 ``use_material_pass_filter`` + ``material_pass_filter``
  按索引过滤，Solid 着色模式也生效；虚线同理用 Dash 修改器过滤索引 2。
* **对象固定**：始终复用名为 ``Zellij`` 的对象，每次生成清空全部图层后重建；
  填充按颜色拆成 ``Fills_01_xxxxxx`` 等图层，``Tiling`` / ``Lines`` 各占一层。
"""

from __future__ import annotations

import colorsys

import bpy

from .scene import LAYER_FILLS, LAYER_LINES, LAYER_TILING

__all__ = [
    "OBJECT_NAME",
    "FILL_LAYER_PREFIX",
    "build_object",
    "find_object",
    "clear_object",
    "apply_stroke_widths",
]

#: 固定对象名（生成结果始终写进这一个对象）。
OBJECT_NAME = "Zellij"

#: 填充层描边的饱和度增益：同色相但更艳，避免不同颜色的描边看起来都发灰。
STROKE_SATURATION = 1.35

#: 填充层描边的顶点不透明度。半透明由材质（``Zellij Fill``，alpha 0.5）负责，
#: 这里保持 1.0，否则两者相乘只剩 0.25，描边会淡到看不见。
STROKE_ALPHA = 1.0

#: 填充图层名前缀，后接序号与色值。
FILL_LAYER_PREFIX = "Fills_"

#: 不使用材质，只保留笔划的 ``material_index``（0/1/2）备查。
MATERIAL_INDEX_FILLS = 0
MATERIAL_INDEX_TILING = 1
MATERIAL_INDEX_LINES = 2

#: 没有材质就没有 ``material.pass_index``，所以修改器改用**图层 pass_index** 过滤。
LAYER_PASS_FILLS = 1
LAYER_PASS_TILING = 2
LAYER_PASS_LINES = 3

#: 构造线的虚线修改器与点距（``dash`` / ``gap``）。
DASH_MODIFIER = "Zellij Dashes"
DASH_LENGTH = 2
DASH_GAP = 2

#: 半透明度：靠 ``Opacity`` 修改器按图层 pass_index 过滤实现，Solid 着色模式也生效。
OPACITY_MODIFIER = "Opacity"
TILING_OPACITY_MODIFIER = "Tiling Opacity"
LINES_OPACITY_MODIFIER = "Lines Opacity"
FILL_OPACITY = 0.0
TILING_OPACITY = 0.0
LINES_OPACITY = 0.0

_ATTR_RADIUS = "radius"
_ATTR_OPACITY = "opacity"
_ATTR_VERTEX_COLOR = "vertex_color"
_ATTR_FILL_COLOR = "fill_color"
_ATTR_FILL_ID = "fill_id"
_ATTR_HIDE_STROKE = "hide_stroke"

#: 辅助拓扑图层（按顺序写入）。
AUX_LAYERS = (LAYER_TILING, LAYER_LINES)


def _grease_pencils():
    collection = getattr(bpy.data, "grease_pencils_v3", None)
    if collection is None:
        collection = bpy.data.grease_pencils
    return collection


def find_object():
    """返回固定的 GP 对象；不存在（或类型不对）时返回 ``None``。"""
    obj = bpy.data.objects.get(OBJECT_NAME)
    if obj is None or obj.type != "GREASEPENCIL":
        return None
    return obj


def _ensure_object():
    obj = bpy.data.objects.get(OBJECT_NAME)
    if obj is not None and obj.type != "GREASEPENCIL":
        bpy.data.objects.remove(obj, do_unlink=True)
        obj = None

    if obj is None:
        collection = _grease_pencils()
        data = collection.get(OBJECT_NAME)
        if data is None:
            data = collection.new(OBJECT_NAME)
        obj = bpy.data.objects.new(OBJECT_NAME, data)
        bpy.context.collection.objects.link(obj)
    return obj


def _clear_layers(data) -> None:
    """删掉全部图层（连带其中的几何数据）。"""
    while data.layers:
        data.layers.remove(data.layers[0])


def clear_object() -> None:
    """清空固定对象的所有几何数据（保留对象本身）。"""
    obj = find_object()
    if obj is None:
        return
    _clear_layers(obj.data)
    obj.data.materials.clear()


def _srgb_to_linear(value: float) -> float:
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def _linear_rgba(colour, alpha: float = 1.0) -> tuple[float, float, float, float]:
    if colour is None:
        return (0.0, 0.0, 0.0, alpha)
    return (
        _srgb_to_linear(colour[0]),
        _srgb_to_linear(colour[1]),
        _srgb_to_linear(colour[2]),
        alpha,
    )


def _stroke_rgba(colour, alpha: float = STROKE_ALPHA):
    """由填充色推出同色相的描边色。

    直接乘一个系数会把深色压成一片黑、浅色压成一片灰，五层描边看起来就“统一”了。
    这里保留色相、提饱和度，并把明度朝填充色的反方向推：浅底配深描边、深底配亮描边，
    每层描边既和自身填充同色系，彼此又分得开。
    """
    if colour is None:
        return (0.0, 0.0, 0.0, alpha)
    hue, saturation, value = colorsys.rgb_to_hsv(*colour)
    saturation = min(1.0, saturation * STROKE_SATURATION + 0.15)
    value = min(0.78, max(0.28, 0.68 - 0.36 * value))
    red, green, blue = colorsys.hsv_to_rgb(hue, saturation, value)
    return _linear_rgba((red, green, blue), alpha)


def _hex_name(colour) -> str:
    """``(r, g, b)`` 0..1 → ``D8E7D0``，用于图层名。"""
    return "".join(f"{int(round(max(0.0, min(1.0, c)) * 255)):02X}" for c in colour)


def _group_by_colour(shapes, palette=None):
    """按填充色分组，数量多的在前；返回 ``[(colour, shapes), ...]``。

    ``palette`` 给出基色表时（``Design.colours``，0-255），每个形状按**最接近的基色**
    归组——量化只用于分组，写进 ``fill_color`` 的仍是形状自己的颜色，shimmer 的抖动
    不会被拉平。没有调色板时退化为精确色值分组。
    """
    if not palette:
        buckets: dict[tuple[int, int, int], list] = {}
        for shape in shapes:
            key = tuple(int(round(max(0.0, min(1.0, c)) * 255)) for c in shape.fill)
            buckets.setdefault(key, []).append(shape)
        ordered = sorted(buckets.items(), key=lambda item: (-len(item[1]), item[0]))
        return [
            ((r / 255.0, g / 255.0, b / 255.0), group) for (r, g, b), group in ordered
        ]

    base = [(c[0] / 255.0, c[1] / 255.0, c[2] / 255.0) for c in palette]
    buckets = {index: [] for index in range(len(base))}
    for shape in shapes:
        if shape.fill is None:
            continue
        buckets[_nearest(base, shape.fill)].append(shape)
    ordered = sorted(
        ((base[index], group) for index, group in buckets.items() if group),
        key=lambda item: (-len(item[1]), item[0]),
    )
    return ordered


def _nearest(palette, colour) -> int:
    """欧氏距离最近的基色下标。"""
    best_index, best_distance = 0, None
    for index, base in enumerate(palette):
        distance = sum((a - b) ** 2 for a, b in zip(base, colour))
        if best_distance is None or distance < best_distance:
            best_index, best_distance = index, distance
    return best_index


def _ensure_dash_modifier(obj):
    """只对构造线图层（layer pass 3）打虚线。"""
    modifier = obj.modifiers.get(DASH_MODIFIER)
    if modifier is None:
        modifier = obj.modifiers.new(DASH_MODIFIER, "GREASE_PENCIL_DASH")
    modifier.material_filter = None
    modifier.use_material_pass_filter = False
    modifier.use_layer_pass_filter = True
    modifier.layer_pass_filter = LAYER_PASS_LINES
    modifier.invert_layer_pass_filter = False
    segments = modifier.segments
    if len(segments) == 0:
        segments.new()
    segment = segments[0]
    segment.dash = DASH_LENGTH
    segment.gap = DASH_GAP
    while len(segments) > 1:
        segments.remove(segments[-1])
    return modifier


def _ensure_opacity_modifier(obj, name: str, pass_index: int, factor: float):
    """按图层 pass_index 过滤，只把该层笔划的不透明度设成 ``factor``。"""
    modifier = obj.modifiers.get(name)
    if modifier is None:
        modifier = obj.modifiers.new(name, "GREASE_PENCIL_OPACITY")
    modifier.color_mode = "STROKE"
    modifier.color_factor = factor
    modifier.material_filter = None
    modifier.use_material_pass_filter = False
    modifier.use_layer_pass_filter = True
    modifier.layer_pass_filter = pass_index
    modifier.invert_layer_pass_filter = False
    return modifier


def apply_opacities(
    obj, *, fill_opacity: float, tiling_opacity: float, lines_opacity: float
) -> None:
    """面板滑块 → 三个 Opacity 修改器的实时回写。"""
    _ensure_opacity_modifier(obj, OPACITY_MODIFIER, LAYER_PASS_FILLS, fill_opacity)
    _ensure_opacity_modifier(
        obj, TILING_OPACITY_MODIFIER, LAYER_PASS_TILING, tiling_opacity
    )
    _ensure_opacity_modifier(
        obj, LINES_OPACITY_MODIFIER, LAYER_PASS_LINES, lines_opacity
    )
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()


def _set_points(drawing, name, dtype, key, values, default) -> None:
    attribute = drawing.attributes.get(name)
    if attribute is None:
        attribute = drawing.attributes.new(name, dtype, "POINT")
        count = len(drawing.attributes["position"].data)
        packed = list(default) if isinstance(default, (tuple, list)) else [default]
        attribute.data.foreach_set(key, packed * count)
    attribute.data.foreach_set(key, values)


def _write_width(drawing, radius: float, total: int) -> None:
    """写点半径；半径为 0 时同时把点不透明度压到 0，避免视口里留下 1px 细线。"""
    _set_points(drawing, _ATTR_RADIUS, "FLOAT", "value", [radius] * total, radius)
    _set_points(
        drawing,
        _ATTR_OPACITY,
        "FLOAT",
        "value",
        [1.0 if radius > 0.0 else 0.0] * total,
        1.0,
    )


def _write_drawing(
    drawing, shapes, size: float, *, filled: bool, width: float, material_index: int = 0
) -> None:
    total = sum(len(shape.points) for shape in shapes)
    drawing.add_strokes([len(shape.points) for shape in shapes])

    positions = [0.0] * (total * 3)
    colours = [0.0] * (total * 4)
    index = 0
    colour_index = 0
    for shape in shapes:
        if filled:
            rgba = _stroke_rgba(shape.fill)
        else:
            rgba = _linear_rgba(shape.stroke)
        for x, y in shape.points:
            positions[index] = x
            positions[index + 1] = y
            positions[index + 2] = 0.0
            index += 3
        for _ in shape.points:
            colours[colour_index : colour_index + 4] = rgba
            colour_index += 4

    drawing.attributes["position"].data.foreach_set("vector", positions)

    _write_width(drawing, width * size, total)

    _set_points(
        drawing,
        _ATTR_VERTEX_COLOR,
        "FLOAT_COLOR",
        "color",
        colours,
        (0.0, 0.0, 0.0, 1.0),
    )

    strokes = drawing.strokes
    if filled:
        per_curve = [_linear_rgba(shape.fill) for shape in shapes]
        fill_color = drawing.attributes.get(_ATTR_FILL_COLOR)
        if fill_color is None:
            fill_color = drawing.attributes.new(
                _ATTR_FILL_COLOR, "FLOAT_COLOR", "CURVE"
            )
        fill_color.data.foreach_set("color", [c for rgba in per_curve for c in rgba])

        fill_id = drawing.attributes.get(_ATTR_FILL_ID)
        if fill_id is None:
            fill_id = drawing.attributes.new(_ATTR_FILL_ID, "INT", "CURVE")
        fill_id.data.foreach_set("value", list(range(1, len(shapes) + 1)))

    # 描边必须保持可见（导出器会跳过 hide_stroke=True 的笔划），
    # 不可见由半径 / 不透明度为 0 实现。
    hide_stroke = drawing.attributes.get(_ATTR_HIDE_STROKE)
    if hide_stroke is None:
        hide_stroke = drawing.attributes.new(_ATTR_HIDE_STROKE, "BOOLEAN", "CURVE")
    hide_stroke.data.foreach_set("value", [False] * len(shapes))

    for index, shape in enumerate(shapes):
        strokes[index].cyclic = bool(shape.closed)
        strokes[index].material_index = material_index


def _write_layer(
    data,
    name,
    shapes,
    size,
    *,
    filled,
    width,
    frame,
    material_index: int = 0,
    pass_index: int = 0,
) -> None:
    layer = data.layers.new(name, set_active=True)
    layer.pass_index = pass_index
    drawing = layer.frames.new(int(frame)).drawing
    _write_drawing(
        drawing, shapes, size, filled=filled, width=width, material_index=material_index
    )


def build_object(
    shapes,
    *,
    size: float,
    frame: int = 1,
    properties: dict | None = None,
    fill_width: float = 0.0,
    tiling_width: float = 0.0,
    lines_width: float = 0.0,
    fill_opacity: float = FILL_OPACITY,
    tiling_opacity: float = TILING_OPACITY,
    lines_opacity: float = LINES_OPACITY,
    palette=None,
):
    """把 ``Shape`` 列表写进固定对象 ``Zellij``。

    填充按颜色拆图层；``Tiling``（红）与 ``Lines`` 各占一层。
    ``fill_width`` / ``tiling_width`` / ``lines_width`` 是相对 ``size`` 的笔划半径，0 即不可见；
    ``fill_opacity`` / ``tiling_opacity`` 写给两个 Opacity 修改器。
    """
    if not shapes:
        raise ValueError("no shapes to build")

    obj = _ensure_object()
    data = obj.data
    _clear_layers(data)
    data.materials.clear()

    material_index = {
        LAYER_TILING: MATERIAL_INDEX_TILING,
        LAYER_LINES: MATERIAL_INDEX_LINES,
    }
    layer_pass = {
        LAYER_TILING: LAYER_PASS_TILING,
        LAYER_LINES: LAYER_PASS_LINES,
    }
    layer_width = {LAYER_TILING: tiling_width, LAYER_LINES: lines_width}
    _ensure_dash_modifier(obj)
    _ensure_opacity_modifier(obj, OPACITY_MODIFIER, LAYER_PASS_FILLS, fill_opacity)
    _ensure_opacity_modifier(
        obj, TILING_OPACITY_MODIFIER, LAYER_PASS_TILING, tiling_opacity
    )
    _ensure_opacity_modifier(
        obj, LINES_OPACITY_MODIFIER, LAYER_PASS_LINES, lines_opacity
    )

    fills = [shape for shape in shapes if shape.layer == LAYER_FILLS]
    for index, (colour, group) in enumerate(_group_by_colour(fills, palette), start=1):
        name = f"{FILL_LAYER_PREFIX}{index:02d}_{_hex_name(colour)}"
        _write_layer(
            data,
            name,
            group,
            size,
            filled=True,
            width=fill_width,
            frame=frame,
            material_index=MATERIAL_INDEX_FILLS,
            pass_index=LAYER_PASS_FILLS,
        )

    for layer_name in AUX_LAYERS:
        group = [shape for shape in shapes if shape.layer == layer_name]
        if not group:
            continue
        _write_layer(
            data,
            layer_name,
            group,
            size,
            filled=False,
            width=layer_width[layer_name],
            frame=frame,
            material_index=material_index[layer_name],
            pass_index=layer_pass[layer_name],
        )

    for key, value in (properties or {}).items():
        obj[key] = value

    view_layer = bpy.context.view_layer
    for other in view_layer.objects:
        other.select_set(False)
    obj.select_set(True)
    view_layer.objects.active = obj

    return obj


def apply_stroke_widths(
    obj, *, fill_width: float, tiling_width: float, lines_width: float
) -> None:
    """把三个滑块的值实时写回已有对象（不重新生成几何）。"""
    size = float(obj.get("size", 1.0)) or 1.0
    for layer in obj.data.layers:
        if layer.name.startswith(FILL_LAYER_PREFIX):
            width = fill_width
        elif layer.name == LAYER_TILING:
            width = tiling_width
        else:
            width = lines_width
        radius = width * size
        for frame in layer.frames:
            drawing = frame.drawing
            attribute = drawing.attributes.get(_ATTR_RADIUS)
            if attribute is None or len(attribute.data) == 0:
                continue
            total = len(attribute.data)
            attribute.data.foreach_set("value", [radius] * total)
            opacity = drawing.attributes.get(_ATTR_OPACITY)
            if opacity is None:
                opacity = drawing.attributes.new(_ATTR_OPACITY, "FLOAT", "POINT")
            opacity.data.foreach_set("value", [1.0 if radius > 0.0 else 0.0] * total)
            drawing.tag_positions_changed()
