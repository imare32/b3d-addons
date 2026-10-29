"""Grease Pencil v3 后端：几何核心 → 一个 GP 对象（每股一条闭合笔划）。

默认输出就是它 —— **不是网格**：环面结是一维的曲线，画成笔划最贴身，也方便后续拿
「几何节点 → Grease Pencil to Curves → Curve to Mesh」扫成实体管（见 :mod:`tube`）。

写进 drawing 的属性：:

    POINT  position      float3   点坐标（GP 自带）
    POINT  radius        float    线宽 —— 每条笔划的粗细
    POINT  opacity       float    不透明度
    POINT  vertex_color  color    线色（**线性**存，见 :func:`_linear_rgba`）
    POINT  u_map         float    几何核心：绕主圆的归一化角度
    POINT  v_map         float    几何核心：绕副圆的归一化角度
    CURVE  cyclic                 每股闭合（`stroke.cyclic`，GP 用它收尾，扫管时也不用封盖）

**颜色不用材质**：``vertex_color`` 就够，:func:`build_object` 还会 ``materials.clear()``
（GP 的材质槽留着反而让人以为颜色由材质决定）。因此 3D 视图 Solid 模式得把
「着色 → 颜色」切到 Attribute 才看得见 —— 生成算子会顺手切一次（``viewport``）。

**实测坑（5.2.2）**：新建图层 ``use_lights`` 默认 ``True``，没有灯时颜色只剩约四分之一
亮度；本后端一律建**无光照层**（``use_lights = False``），颜色所见即所得。另外不给
``miter_angle`` 属性 —— 没有它就按**圆角**连接拐角（有它才是斜接），结的折点不会崩出尖角。
"""

from __future__ import annotations

import bpy
import numpy as np

from .knot import ATTR_U, ATTR_V, TorusKnot
from .palette import srgb_to_linear

__all__ = [
    "LAYER_NAME",
    "OBJECT_NAME",
    "build_object",
    "clear_object",
    "find_object",
    "remove_object",
    "set_colour",
    "set_layer_radius",
]

#: 生成对象的名字（固定名，方便找回来）。
OBJECT_NAME = "Torus Knot"
#: 图层名。
LAYER_NAME = "Knot"

#: ``foreach_set`` 的字段名 → 每个点占几个分量（写之前按它对长度，免得 Blender 只报
#: 一句 "Array length mismatch" 而不说是哪条属性）。
_COMPONENTS = {"value": 1, "vector": 3, "color": 4}

_ATTR_RADIUS = "radius"
_ATTR_OPACITY = "opacity"
_ATTR_VERTEX_COLOR = "vertex_color"


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
        # 老版本留下的网格对象占了这个名字：删掉重来
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
    """清空图层与材质，保留对象本身（对象上的修改器 / 自定义属性留着）。"""
    obj = find_object(name)
    if obj is None:
        return
    data = obj.data
    while data.layers:
        data.layers.remove(data.layers[0])
    data.materials.clear()


def _linear_rgba(colour, alpha: float = 1.0) -> list[float]:
    """面板存的是 sRGB（``COLOR_GAMMA``），写进属性前转线性。"""
    if colour is None:
        return [0.0, 0.0, 0.0, alpha]
    return [
        srgb_to_linear(float(colour[0])),
        srgb_to_linear(float(colour[1])),
        srgb_to_linear(float(colour[2])),
        alpha,
    ]


def _set_points(drawing, name, dtype, key, values, default) -> None:
    """按点写一条属性；属性是新建的话先铺一遍默认值（长度按 ``position`` 的数）。"""
    attribute = drawing.attributes.get(name)
    if attribute is None:
        attribute = drawing.attributes.new(name, dtype, "POINT")
        count = len(drawing.attributes["position"].data)
        packed = list(default) if isinstance(default, (tuple, list)) else [default]
        attribute.data.foreach_set(key, packed * count)
    width = _COMPONENTS.get(key, 1)
    expected = len(attribute.data) * width
    if len(values) != expected:
        # 长度对不上时 Blender 只报 "Array length mismatch (expected n, got m)"，不说是哪条
        # 属性；这里补上名字（多股时最容易踩：某条属性忘了按股广播）
        raise ValueError(
            f"attribute {name!r}: got {len(values)} values, expected {expected} "
            f"({len(attribute.data)} points x {width})"
        )
    attribute.data.foreach_set(key, values)


def _colours(total: int, rgba) -> np.ndarray:
    """``total`` 个点的 ``vertex_color``：平铺成 ``(total, 4)`` 再拉平。"""
    return np.tile(np.asarray(rgba, dtype=np.float32), (total, 1)).reshape(-1)


def build_object(
    knot: TorusKnot,
    *,
    width: float,
    colour=(0.0, 0.0, 0.0),
    frame: int = 1,
    collection=None,
):
    """把几何核心写进生成对象（图层 / 笔划 / 属性），返回对象。

    对象已存在时就地更新：图层重建，对象上的修改器（管状实体）与自定义属性都不动，
    所以反复生成不会把关掉的管状实体又打开、也不会把参数记录抖掉。
    """
    obj = _ensure_object(collection=collection)
    data = obj.data
    while data.layers:
        data.layers.remove(data.layers[0])
    data.materials.clear()

    layer = data.layers.new(LAYER_NAME, set_active=True)
    # 实测：新图层默认 use_lights=True，没灯时颜色只剩约四分之一亮度
    layer.use_lights = False

    drawing = layer.frames.new(int(frame)).drawing
    counts = list(knot.counts)
    drawing.add_strokes(counts)
    total = knot.vertex_count

    drawing.attributes["position"].data.foreach_set("vector", knot.points.reshape(-1))
    _set_points(drawing, _ATTR_RADIUS, "FLOAT", "value", np.full(total, float(width), np.float32), width)
    _set_points(drawing, _ATTR_OPACITY, "FLOAT", "value", np.ones(total, np.float32), 1.0)
    _set_points(
        drawing,
        _ATTR_VERTEX_COLOR,
        "FLOAT_COLOR",
        "color",
        _colours(total, _linear_rgba(colour)),
        (0.0, 0.0, 0.0, 1.0),
    )
    _set_points(drawing, ATTR_U, "FLOAT", "value", knot.u_map, 0.0)
    _set_points(drawing, ATTR_V, "FLOAT", "value", knot.v_map, 0.0)

    for stroke in drawing.strokes:
        stroke.cyclic = bool(knot.closed)

    return obj


def _layer(obj):
    data = getattr(obj, "data", None)
    if data is None:
        return None
    return data.layers.get(LAYER_NAME)


def _drawing(obj):
    layer = _layer(obj)
    if layer is None or not len(layer.frames):
        return None
    return layer.frames[0].drawing


def set_layer_radius(obj, width: float) -> bool:
    """就地改线宽：只重写 ``radius`` 属性，几何一点不动（拖滑块是实时的）。"""
    drawing = _drawing(obj)
    if drawing is None:
        return False
    attribute = drawing.attributes.get(_ATTR_RADIUS)
    if attribute is None:
        return False
    attribute.data.foreach_set("value", np.full(len(attribute.data), float(width), np.float32))
    return True


def set_colour(obj, colour) -> bool:
    """就地改线色：只重写 ``vertex_color`` 属性。"""
    drawing = _drawing(obj)
    if drawing is None:
        return False
    attribute = drawing.attributes.get(_ATTR_VERTEX_COLOR)
    if attribute is None:
        return False
    attribute.data.foreach_set(
        "color", _colours(len(attribute.data), _linear_rgba(colour))
    )
    return True