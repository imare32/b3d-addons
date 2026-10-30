"""Grease Pencil v3 渲染后端：``Shape`` 列表 → 分层 GP 对象。

渲染层只认 ``Shape`` 契约（点列 / 填充色 / 描边色 / 是否闭合 / 图层名），与算法层
完全解耦；``scene.build_shapes()`` 给什么就画什么。

以下行为是 **Blender 5.2.2 实测**结论（与 ``gen_zellij/gp.py`` 同源），改动前先读：

* 颜色不用材质：``vertex_color``（POINT 域，FLOAT_COLOR）按**线性**存，所以 sRGB
  必须先转线性，否则整体发灰发白；
* **``layer.use_lights`` 新建时默认 ``True``**：没有灯时只拿到 ``World.color`` 的
  环境光，实测纯红渲染成 0.247。本后端一律建**无光照层**；
* **``cyclic`` 是一条 CURVE 域的 BOOLEAN 属性，可以整体写**（实测 5.2.2）：
  ``drawing.attributes.new("cyclic", "BOOLEAN", "CURVE")`` 之后 ``foreach_set``，
  ``stroke.cyclic`` 读回完全一致。**不要**写成 ``for stroke in strokes:
  stroke.cyclic = ...`` —— 那是逐笔划的 RNA 调用，实测 3600 笔要 22 ms，
  批量写是 0.03 ms（700 倍）；
* **结构性改动会让 ``drawing`` 指针失效**：``add_strokes`` / ``remove_strokes``
  之后必须重新取一次 drawing，否则 ``foreach_set`` 报
  ``internal error setting the array``；长度对不上时 Blender 也只报这一句，不说
  是哪条属性——所以写之前按领域长度对一遍（:func:`_ensure`）；
* ``layer.frames.remove()`` 收的是**帧号**（int），不是 ``GreasePencilFrame`` 对象；
* 拐角用默认的**圆角**（不写 ``miter_angle``，与 JS 的 ``stroke-linejoin: round``
  一致）；端头按需写 ``start_cap`` / ``end_cap``（``0`` = 圆头、``1`` = 平头，
  交织样式的切口用平头）；
* **填充是几何不是材质开关**：``fill_id`` 非 0 才填充，每条形状一个独立 id，相邻色块
  不会被合并成一个连通区域；``fill_color`` / ``fill_id`` / ``hide_stroke`` 必须与
  笔划数等长，长度不对 ``foreach_set`` 只报一句
  ``internal error setting the array``，不说哪条属性。
"""

from __future__ import annotations

import numpy as np

import bpy

from .palette import srgb_to_linear

__all__ = [
    "OBJECT_NAME",
    "build_frames",
    "build_object",
    "clear_object",
    "find_object",
    "remove_object",
    "set_colour",
    "set_layer_radius",
    "set_layer_visible",
    "set_opacity",
]

#: 生成结果固定写进这一个对象，反复生成不会堆一地对象。
OBJECT_NAME = "Gen Girih"

_ATTR_POSITION = "position"
_ATTR_RADIUS = "radius"
_ATTR_OPACITY = "opacity"
_ATTR_VERTEX_COLOR = "vertex_color"
_ATTR_CYCLIC = "cyclic"
_ATTR_FILL_ID = "fill_id"
_ATTR_FILL_COLOR = "fill_color"
_ATTR_HIDE_STROKE = "hide_stroke"
_ATTR_START_CAP = "start_cap"
_ATTR_END_CAP = "end_cap"

_POINT = "POINT"
_CURVE = "CURVE"


def _grease_pencils():
    """GP 数据块集合：4.3 起叫 ``grease_pencils_v3``，5.2 上就是 ``grease_pencils``。"""
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


def _ensure_object(name: str = OBJECT_NAME, collection=None):
    obj = bpy.data.objects.get(name)
    if obj is not None and obj.type != "GREASEPENCIL":
        bpy.data.objects.remove(obj, do_unlink=True)
        obj = None

    if obj is None:
        datas = _grease_pencils()
        data = datas.get(name) or datas.new(name)
        obj = bpy.data.objects.new(name, data)
        target = collection or bpy.context.collection or bpy.context.scene.collection
        target.objects.link(obj)
    elif collection is not None and obj.name not in collection.objects:
        collection.objects.link(obj)
    return obj


def remove_object(name: str = OBJECT_NAME) -> bool:
    """删除生成对象（连带数据块）；返回是否真的删掉了东西。"""
    obj = bpy.data.objects.get(name)
    if obj is None:
        return False
    data = obj.data if obj.type == "GREASEPENCIL" else None
    bpy.data.objects.remove(obj, do_unlink=True)
    if data is not None and data.users == 0:
        _grease_pencils().remove(data)
    return True


def clear_object(name: str = OBJECT_NAME) -> None:
    """清空图层与材质，保留对象本身（对象上的修改器 / 自定义属性留着）。"""
    obj = find_object(name)
    if obj is None:
        return
    data = obj.data
    while data.layers:
        data.layers.remove(data.layers[0])
    data.materials.clear()


# --- 颜色 -----------------------------------------------------------------


def _linear_rgba(colour, alpha: float = 1.0) -> np.ndarray:
    """面板存的是 sRGB（``COLOR_GAMMA``），写进属性前转线性。"""
    if colour is None:
        return np.array([0.0, 0.0, 0.0, alpha], dtype=np.float32)
    return np.array(
        [
            srgb_to_linear(colour[0]),
            srgb_to_linear(colour[1]),
            srgb_to_linear(colour[2]),
            float(alpha),
        ],
        dtype=np.float32,
    )


# --- 写入 -----------------------------------------------------------------


def _ensure(drawing, name: str, dtype: str, domain: str, count: int):
    """按当前长度 / 领域 / 类型取一条属性，不合适就重建。"""
    attribute = drawing.attributes.get(name)
    if (
        attribute is None
        or attribute.domain != domain
        or attribute.data_type != dtype
        or len(attribute.data) != count
    ):
        if attribute is not None:
            drawing.attributes.remove(attribute)
        attribute = drawing.attributes.new(name, dtype, domain)
    return attribute


def _write_drawing(drawing, shapes, radius: float, opacity: float) -> None:
    """把一个图层的形状写进 drawing（整层属性一次写完）。

    三种形状可以混在同一层里：描边（只描）、本体（只描，端头按需平头）、补片
    （``fill`` 非空且 ``stroke`` 为空 = 只填不描，半径写 0）。
    """
    counts = [max(2, len(shape.points)) for shape in shapes]
    drawing.add_strokes(counts)
    total = int(sum(counts))
    count_shapes = len(shapes)

    # 点：先摊平成一张表，再一次性塞进 position（逐点 Python 赋值会慢一个数量级）
    flat = [point for shape in shapes for point in shape.points]
    positions = np.zeros((total, 3), dtype=np.float32)
    positions[: len(flat), :2] = flat

    colours = np.empty((total, 4), dtype=np.float32)
    radii = np.empty(total, dtype=np.float32)
    fill_ids = np.zeros(count_shapes, dtype=np.int32)
    fill_colours = np.empty((count_shapes, 4), dtype=np.float32)
    hidden = np.zeros(count_shapes, dtype=bool)
    cap_start = np.zeros(count_shapes, dtype=np.int8)
    cap_end = np.zeros(count_shapes, dtype=np.int8)

    cursor = 0
    for index, (shape, count) in enumerate(zip(shapes, counts)):
        colours[cursor : cursor + count] = _linear_rgba(shape.stroke or shape.fill)
        # 只填不描的形状半径写 0：轮廓线上就不会多出一条边
        radii[cursor : cursor + count] = float(radius) if shape.stroke is not None else 0.0
        if shape.fill is not None:
            fill_ids[index] = index + 1  # 每条一个独立 id，相邻色块不会被并成一个
            fill_colours[index] = _linear_rgba(shape.fill)
            hidden[index] = shape.stroke is None
        else:
            fill_colours[index] = 0.0
        cap_start[index] = 1 if shape.caps[0] else 0
        cap_end[index] = 1 if shape.caps[1] else 0
        cursor += count

    drawing.attributes[_ATTR_POSITION].data.foreach_set("vector", positions.reshape(-1))
    _ensure(drawing, _ATTR_RADIUS, "FLOAT", _POINT, total).data.foreach_set("value", radii)
    _ensure(drawing, _ATTR_OPACITY, "FLOAT", _POINT, total).data.foreach_set(
        "value", np.full(total, float(opacity), dtype=np.float32)
    )
    _ensure(drawing, _ATTR_VERTEX_COLOR, "FLOAT_COLOR", _POINT, total).data.foreach_set(
        "color", colours.reshape(-1)
    )
    # 闭合标记：整层一次写完（见模块说明里的实测对比）
    _ensure(drawing, _ATTR_CYCLIC, "BOOLEAN", _CURVE, count_shapes).data.foreach_set(
        "value", np.array([bool(shape.closed) for shape in shapes], dtype=bool)
    )

    # 只有这一层真有填充 / 平头时才建对应的属性，纯描边层不背这两个数组
    if fill_ids.any():
        _ensure(drawing, _ATTR_FILL_ID, "INT", _CURVE, count_shapes).data.foreach_set(
            "value", fill_ids
        )
        _ensure(drawing, _ATTR_FILL_COLOR, "FLOAT_COLOR", _CURVE, count_shapes).data.foreach_set(
            "color", fill_colours.reshape(-1)
        )
        _ensure(drawing, _ATTR_HIDE_STROKE, "BOOLEAN", _CURVE, count_shapes).data.foreach_set(
            "value", hidden
        )
    if cap_start.any() or cap_end.any():
        _ensure(drawing, _ATTR_START_CAP, "INT8", _CURVE, count_shapes).data.foreach_set(
            "value", cap_start
        )
        _ensure(drawing, _ATTR_END_CAP, "INT8", _CURVE, count_shapes).data.foreach_set(
            "value", cap_end
        )


def build_frames(
    frames,
    *,
    name: str = OBJECT_NAME,
    frame_start: int = 1,
    width: float = 0.02,
    widths: dict[str, float] | None = None,
    opacity: float = 1.0,
    collection=None,
) -> dict:
    """把**多帧**的 ``Shape`` 写进同一个 GP 对象（接触角扫掠用）。

    ``frames`` 是 ``[[Shape, ...], ...]``，第 i 项写到第 ``frame_start + i`` 帧。
    图层集合取各帧的并集、按首次出现排序；某一帧没有这层的内容就不建那一帧——
    GP 在那里显示空白，正好是"这一层这一帧什么也没有"。

    返回 ``{图层名: 单帧最多形状数}``。
    """
    if not frames or not any(frames):
        raise ValueError("no shapes to build")

    obj = _ensure_object(name, collection)
    data = obj.data
    while data.layers:
        data.layers.remove(data.layers[0])
    data.materials.clear()

    order: list[str] = []
    for shapes in frames:
        for shape in shapes:
            if shape.layer not in order:
                order.append(shape.layer)

    counts: dict[str, int] = {}
    for layer_name in order:
        layer = data.layers.new(layer_name, set_active=True)
        layer.use_lights = False
        radius = float(width if widths is None else widths.get(layer_name, width))
        most = 0
        for index, shapes in enumerate(frames):
            group = [shape for shape in shapes if shape.layer == layer_name]
            if not group:
                continue
            drawing = layer.frames.new(int(frame_start) + index).drawing
            _write_drawing(drawing, group, radius, opacity)
            most = max(most, len(group))
        counts[layer_name] = most

    obj["gen_girih_shapes"] = max((len(shapes) for shapes in frames), default=0)
    obj["gen_girih_layers"] = ", ".join(counts)
    obj["gen_girih_frames"] = len(frames)

    view_layer = bpy.context.view_layer
    obj.select_set(False)
    if view_layer.objects.active is obj:
        view_layer.objects.active = None

    return counts


def build_object(
    shapes,
    *,
    name: str = OBJECT_NAME,
    frame: int = 1,
    width: float = 0.02,
    widths: dict[str, float] | None = None,
    opacity: float = 1.0,
    collection=None,
) -> dict:
    """把 ``Shape`` 列表写进固定 GP 对象，按 ``Shape.layer`` 分组建图层。

    ``width`` 是笔划**半径**（世界单位）；``widths`` 可以按图层名覆盖它
    （带子样式里描边层比本体层粗，就是走这里）。线条 / 带子样式里线宽不参与几何，
    换宽度只需重写 ``radius`` 属性（见 :func:`set_layer_radius`）；**交织样式例外**
    —— 缺口按可见半宽算，宽度变了必须重新生成。

    图层严格按 ``Shape`` 首次出现的顺序创建，不做重排：``data.layers`` 里索引越大
    越靠上，所以**列表里靠后的图层盖住靠前的**。返回 ``{图层名: 形状数}``。
    """
    return build_frames(
        [shapes],
        name=name,
        frame_start=frame,
        width=width,
        widths=widths,
        opacity=opacity,
        collection=collection,
    )


# --- 生成后的就地编辑 -----------------------------------------------------


def _layer_drawing(obj, layer_name: str):
    if obj is None or obj.type != "GREASEPENCIL":
        return None
    layer = obj.data.layers.get(layer_name)
    if layer is None or not len(layer.frames):
        return None
    return layer.frames[0].drawing


def set_layer_radius(obj, layer_name: str, radius: float) -> bool:
    """就地改某层的笔划半径（不重建几何）。返回是否改成功。

    线条的宽度**不在几何里**，所以拖滑块走这条路；等阶段三开始切缺口之后，
    带宽就变成几何参数了（缺口按它算），那时才需要重新生成。
    """
    drawing = _layer_drawing(obj, layer_name)
    if drawing is None:
        return False
    attribute = drawing.attributes.get(_ATTR_RADIUS)
    if attribute is None:
        return False
    attribute.data.foreach_set("value", np.full(len(attribute.data), float(radius), np.float32))
    return True


def set_colour(obj, layer_name: str, colour) -> bool:
    """就地改某层的线色：只重写 ``vertex_color``，几何一律不动。"""
    drawing = _layer_drawing(obj, layer_name)
    if drawing is None:
        return False
    attribute = drawing.attributes.get(_ATTR_VERTEX_COLOR)
    if attribute is None:
        return False
    attribute.data.foreach_set(
        "color", np.tile(_linear_rgba(colour), len(attribute.data))
    )
    return True


def set_opacity(obj, layer_name: str, opacity: float) -> bool:
    """就地改某层的不透明度：只重写 ``opacity`` 属性。"""
    drawing = _layer_drawing(obj, layer_name)
    if drawing is None:
        return False
    attribute = drawing.attributes.get(_ATTR_OPACITY)
    if attribute is None:
        return False
    attribute.data.foreach_set(
        "value", np.full(len(attribute.data), float(opacity), np.float32)
    )
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
