"""Grease Pencil v3 渲染后端：``Shape`` 列表 → 分层 GP 对象。

渲染层只认 ``Shape`` 契约（点列 / 填充色 / 描边色 / 是否闭合 / 图层名），
与算法层完全解耦；``scene.build_layers()`` 给什么就画什么。

以下行为是 **Blender 5.2.2 实测**结论（与 ``gen_zellij/gp.py`` 同源），改动前先读注释：

* 颜色不用材质：``vertex_color``（POINT 域，FLOAT_COLOR）按**线性**存储，
  所以 sRGB 0..1 必须先转线性，否则整体发灰发白；描边色由场景层直接给，
  后端不做派生；
* 填充是几何不是材质开关：``fill_id``（CURVE 域 INT）非 0 才填充，
  ``fill_color``（CURVE 域 FLOAT_COLOR）给填充色；每条形状各自一个 id，
  相邻色块不会被合并成一个连通区域；
* **``layer.use_lights`` 新建时默认 ``True``**（实测），着色层在没有灯的情况下
  只拿到 ``World.color`` 的环境光，实测纯红填充渲染成 0.247 而不是 1.0。
  本后端一律建 **无光照层**（``use_lights = False``），颜色所见即所得；
* 拐角：**写不写** ``miter_angle``（POINT，FLOAT，弧度）决定连接方式——写 = 斜接
  （值是最小夹角，小于它的角被切平；取算子下限 1°），不写 = 圆角连接。色块用斜接
  （尖角立得住），编织两层用圆角（90° 转弯是四分圆弧，和端头圆角一个观感、内侧不叠画）。
  界面里的 ``grease_pencil.set_corner_type(corner_type='ROUND')`` 就是把这个属性删掉；
* 端头：``start_cap`` / ``end_cap``（CURVE 域 INT8）0 = 圆头、1 = 平头。编织的
  "压在下面那一段"必须用平头（切口端面要贴平对方边缘），自由的端头保持圆头。
"""

from __future__ import annotations

import math

import bpy

from .palette import srgb_to_linear

__all__ = [
    "OBJECT_NAME",
    "build_object",
    "clear_object",
    "find_object",
    "remove_object",
    "set_layer_radius",
    "set_layer_visible",
]

#: 生成结果固定写进这一个对象，重复生成不会堆一地对象。
OBJECT_NAME = "Gen Rosette"

#: 尖角阈值：算子下限 1°，只切针状夹角（见模块文档）。
MITER_ANGLE = math.radians(1.0)

_ATTR_RADIUS = "radius"
_ATTR_OPACITY = "opacity"
_ATTR_VERTEX_COLOR = "vertex_color"
_ATTR_FILL_COLOR = "fill_color"
_ATTR_FILL_ID = "fill_id"
_ATTR_HIDE_STROKE = "hide_stroke"
_ATTR_MITER_ANGLE = "miter_angle"
#: 端头：**CURVE 域 INT8**，0 = 圆头，1 = 平头（实测 ``caps_set`` 就是写这两个属性）。
#: 界面里的 ``bpy.ops.grease_pencil.caps_set(type='FLAT')`` 等价于逐条写 1。
_ATTR_START_CAP = "start_cap"
_ATTR_END_CAP = "end_cap"
CAP_ROUND = 0
CAP_FLAT = 1


# --- 对象 -----------------------------------------------------------------


def _grease_pencils():
    """4.2 之后 GP 数据块统一叫 ``grease_pencils``（旧分支留兜底）。"""
    collection = getattr(bpy.data, "grease_pencils_v3", None)
    if collection is None:
        collection = bpy.data.grease_pencils
    return collection


def find_object(name: str = OBJECT_NAME):
    """返回生成对象；不存在或类型不对时返回 ``None``。"""
    obj = bpy.data.objects.get(name)
    if obj is None or obj.type != "GREASEPENCIL":
        return None
    return obj


def _ensure_object(name: str, collection=None):
    obj = bpy.data.objects.get(name)
    if obj is not None and obj.type != "GREASEPENCIL":
        bpy.data.objects.remove(obj, do_unlink=True)
        obj = None

    if obj is None:
        datas = _grease_pencils()
        data = datas.get(name) or datas.new(name)
        obj = bpy.data.objects.new(name, data)
        target = collection or bpy.context.collection
        target.objects.link(obj)
    elif collection is not None and obj.name not in collection.objects:
        collection.objects.link(obj)
    return obj


def remove_object(name: str = OBJECT_NAME) -> bool:
    """删除生成对象（连带数据块）。返回是否真的删掉了东西。"""
    obj = bpy.data.objects.get(name)
    if obj is None:
        return False
    data = obj.data if obj.type == "GREASEPENCIL" else None
    bpy.data.objects.remove(obj, do_unlink=True)
    if data is not None and data.users == 0:
        _grease_pencils().remove(data)
    return True


def clear_object(name: str = OBJECT_NAME) -> None:
    """清空图层与材质，保留对象本身（对象上的修改器/自定义属性留着）。"""
    obj = find_object(name)
    if obj is None:
        return
    data = obj.data
    while data.layers:
        data.layers.remove(data.layers[0])
    data.materials.clear()


# --- 颜色 -----------------------------------------------------------------


def _linear_rgba(colour, alpha: float = 1.0) -> list[float]:
    if colour is None:
        return [0.0, 0.0, 0.0, alpha]
    return [
        srgb_to_linear(colour[0]),
        srgb_to_linear(colour[1]),
        srgb_to_linear(colour[2]),
        alpha,
    ]


# --- 写入 -----------------------------------------------------------------


def _set_points(drawing, name, dtype, key, values, default) -> None:
    attribute = drawing.attributes.get(name)
    if attribute is None:
        attribute = drawing.attributes.new(name, dtype, "POINT")
        count = len(drawing.attributes["position"].data)
        packed = list(default) if isinstance(default, (tuple, list)) else [default]
        attribute.data.foreach_set(key, packed * count)
    attribute.data.foreach_set(key, values)


def _set_curves(drawing, name, dtype, key, values, default) -> None:
    """逐**笔划**写一条属性（``fill_id`` / 端头这类 CURVE 域的东西）。"""
    attribute = drawing.attributes.get(name)
    if attribute is None:
        attribute = drawing.attributes.new(name, dtype, "CURVE")
        packed = list(default) if isinstance(default, (tuple, list)) else [default]
        attribute.data.foreach_set(key, packed * len(values))
    attribute.data.foreach_set(key, values)


def _write_drawing(
    drawing, shapes, *, width: float, hide_stroke: bool = False, miter: bool = True
) -> None:
    total = sum(len(shape.points) for shape in shapes)
    drawing.add_strokes([len(shape.points) for shape in shapes])

    positions = [0.0] * (total * 3)
    colours = [0.0] * (total * 4)
    index = 0
    colour_index = 0
    for shape in shapes:
        # 描边色由场景层给出（填充层只填不描边，靠 hide_stroke 关掉）；缺省为黑。
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

    _set_points(drawing, _ATTR_RADIUS, "FLOAT", "value", [width] * total, width)
    _set_points(drawing, _ATTR_OPACITY, "FLOAT", "value", [1.0] * total, 1.0)
    if miter:
        # 有这条属性 = **斜接**拐角（值是最小夹角，小于它的角会被切平）；
        # 没有它 = **圆角连接**（外角是个四分圆弧、内角不叠画）。见模块文档。
        _set_points(
            drawing,
            _ATTR_MITER_ANGLE,
            "FLOAT",
            "value",
            [MITER_ANGLE] * total,
            MITER_ANGLE,
        )
    _set_points(
        drawing,
        _ATTR_VERTEX_COLOR,
        "FLOAT_COLOR",
        "color",
        colours,
        (0.0, 0.0, 0.0, 1.0),
    )

    # fill_color / fill_id 都是逐笔划的，**必须与笔划数等长**：同一图层里可能既有
    # 只描不填的（编织本体）又有只填不描的（端头补片），两个属性都得给全。
    fill_colours = []
    fill_ids = []
    filled = 0
    for shape in shapes:
        if shape.fill is None:
            fill_colours.append((0.0, 0.0, 0.0, 1.0))
            fill_ids.append(0)
        else:
            filled += 1
            fill_colours.append(_linear_rgba(shape.fill))
            fill_ids.append(filled)

    if filled:
        fill_color = drawing.attributes.get(_ATTR_FILL_COLOR)
        if fill_color is None:
            fill_color = drawing.attributes.new(
                _ATTR_FILL_COLOR, "FLOAT_COLOR", "CURVE"
            )
        fill_color.data.foreach_set("color", [c for rgba in fill_colours for c in rgba])
    fill_id = drawing.attributes.get(_ATTR_FILL_ID)
    if fill_id is None:
        fill_id = drawing.attributes.new(_ATTR_FILL_ID, "INT", "CURVE")
    fill_id.data.foreach_set("value", fill_ids)

    # 只填不描的形状（填充层的瓷砖、编织层里的端头补片）逐笔划关掉描边：
    # 图层宽度是统一的，补片要是跟着描一圈，会把不该盖的地方盖掉。
    hidden = [
        bool(hide_stroke) or (shape.fill is not None and shape.stroke is None)
        for shape in shapes
    ]
    hide_attr = drawing.attributes.get(_ATTR_HIDE_STROKE)
    if hide_attr is None:
        hide_attr = drawing.attributes.new(_ATTR_HIDE_STROKE, "BOOLEAN", "CURVE")
    hide_attr.data.foreach_set("value", hidden)

    # 端头：默认圆头，属性不存在就等价于全 0；只有真的混着平头时才写
    if any(start or end for start, end in (shape.caps for shape in shapes)):
        _set_curves(
            drawing,
            _ATTR_START_CAP,
            "INT8",
            "value",
            [CAP_FLAT if shape.caps[0] else CAP_ROUND for shape in shapes],
            CAP_ROUND,
        )
        _set_curves(
            drawing,
            _ATTR_END_CAP,
            "INT8",
            "value",
            [CAP_FLAT if shape.caps[1] else CAP_ROUND for shape in shapes],
            CAP_ROUND,
        )

    strokes = drawing.strokes
    for index, shape in enumerate(shapes):
        strokes[index].cyclic = bool(shape.closed)


def build_object(
    shapes,
    *,
    name: str = OBJECT_NAME,
    frame: int = 1,
    width: float = 0.01,
    widths: dict[str, float] | None = None,
    hide_stroke_layers: set[str] | None = None,
    round_join_layers: set[str] | None = None,
    collection=None,
) -> dict:
    """把 ``Shape`` 列表写进固定 GP 对象，按 ``Shape.layer`` 分组建图层。

    ``width`` 是缺省笔划**半径**（世界单位）；``widths`` 按图层覆盖
    （``{图层名: 半径}``）。线宽不参与几何，所以换宽度只需重写 ``radius`` 属性
    （见 :func:`set_layer_radius`），不必重建设计。

    ``hide_stroke_layers`` 里的图层只留填充/几何、不画描边 —— 色块层（``Faces``）
    与编织层的端头补片就是这么用的（补片要是跟着描一圈，会把不该盖的地方盖掉）。

    ``round_join_layers`` 里的图层**不写** ``miter_angle``：缺这条属性时 GP 用圆角连接，
    和"端头圆角"是一个观感；编织的两层就该这样（90° 转弯处是四分圆弧，内侧也不叠画）。
    其余图层照旧写 1°（斜接），色块与辅助线的尖角才立得起来。

    ``Shape.caps`` 给出平头端头（GP 的 ``start_cap`` / ``end_cap``，CURVE 域 INT8，
    1 = 平头）；整层都没有平头时不建这两个属性。

    图层严格按 ``Shape`` 首次出现的顺序创建，不做重排：``data.layers`` 里
    索引越大越靠上，所以**列表里靠后的图层盖住靠前的**。返回 ``{图层名: 形状数}``，
    顺序同建层顺序。
    """
    hidden = hide_stroke_layers or set()
    rounded = round_join_layers or set()
    if not shapes:
        raise ValueError("no shapes to build")

    obj = _ensure_object(name, collection)
    data = obj.data
    while data.layers:
        data.layers.remove(data.layers[0])
    data.materials.clear()

    groups: dict[str, list] = {}
    for shape in shapes:
        groups.setdefault(shape.layer, []).append(shape)

    counts: dict[str, int] = {}
    for layer_name, group in groups.items():
        layer = data.layers.new(layer_name, set_active=True)
        # 实测坑：新图层默认 use_lights=True，没有灯时颜色只剩约 25% 的亮度。
        layer.use_lights = False
        drawing = layer.frames.new(int(frame)).drawing
        radius = width if widths is None else widths.get(layer_name, width)
        _write_drawing(
            drawing,
            group,
            width=radius,
            hide_stroke=layer_name in hidden,
            miter=layer_name not in rounded,
        )
        counts[layer_name] = len(group)

    obj["gen_rosette_shapes"] = len(shapes)
    obj["gen_rosette_layers"] = ", ".join(counts)

    view_layer = bpy.context.view_layer
    obj.select_set(False)
    if view_layer.objects.active is obj:
        view_layer.objects.active = None

    return counts


# --- 生成后的就地编辑 ------------------------------------------------------


def _layer_drawing(layer):
    if not layer.frames:
        return None
    return layer.frames[0].drawing


def set_layer_radius(obj, layer_name: str, radius: float) -> bool:
    """就地改某层的笔划半径（不重建几何）。返回是否改成功。

    线宽不属于几何时（``Guides`` 的辅助线）改宽度不必重新生成设计，面板拖滑块时走
    这条路；编织的两层**不能**这么改——缺口宽度是按带宽算进几何里的，带宽一变缺口
    就不对了（见 ``weave._end_cut``）。
    """
    if obj is None or obj.type != "GREASEPENCIL":
        return False
    layer = obj.data.layers.get(layer_name)
    if layer is None:
        return False
    drawing = _layer_drawing(layer)
    if drawing is None:
        return False
    attribute = drawing.attributes.get(_ATTR_RADIUS)
    if attribute is None:
        return False
    attribute.data.foreach_set("value", [float(radius)] * len(attribute.data))
    return True


def set_layer_visible(obj, layer_name: str, visible: bool) -> bool:
    """就地切图层显隐（``layer.hide`` 同时作用于视口与渲染）。"""
    if obj is None or obj.type != "GREASEPENCIL":
        return False
    layer = obj.data.layers.get(layer_name)
    if layer is None:
        return False
    layer.hide = not visible
    return True
