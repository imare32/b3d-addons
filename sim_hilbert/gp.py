"""Grease Pencil v3 后端：一条折线笔划 → 分层 GP 对象。

只认 ``StrokeSet`` 契约（点列 / 笔划点数 / 是否闭合 / 笔划半径 / 逐点颜色 / 额外标量），
与物理、几何完全解耦；:mod:`sim` 算好什么就画什么。3D 希尔伯特曲线本身就是**一条连续
折线**（顶点按希尔伯特序号排列，``0..N-1`` 依次相连），所以整个对象通常只有一个笔划。

以下行为是 **Blender 5.2.2 实测**结论（与 ``sim_catenary/gp.py`` 同源，改动前先读注释）：

* 颜色不用材质：``vertex_color``（POINT 域，FLOAT_COLOR）按**线性**存储，
  所以 sRGB 0..1 必须先转线性，否则整体发灰发白；
* ``layer.use_lights`` 新建时默认 ``True``，无光照层（``use_lights = False``）
  颜色所见即所得 —— 线稿就该这样；
* 点坐标是真的 3D，z 原样保留（这就是本插件能画 3D 格子的原因）；
* ``stroke_depth_order = '3D'`` 是**逐像素深度遮挡**：曲线自己穿过自己时，靠前的段会把
  靠后的段切掉一段，切口是笔划自身的圆头轮廓 —— 不需要按视角手工拆笔划；
* 逐帧改位置：``foreach_set("vector", flat)`` 之后必须 ``tag_positions_changed()``，
  否则视口拿到的是旧点列；
* ``foreach_set`` 的长度必须精确：建属性时顺手塞"默认值 × 点数"很容易算错分量数
  （``FLOAT_COLOR`` 是 4、``FLOAT_VECTOR`` 是 3），报的是
  ``RuntimeError: internal error setting the array`` —— 本后端建完属性直接写满整条缓冲；
* 曲线类型 ``curve_type`` 是 CURVE 域的 INT8 属性（``1 = POLY``、``2 = BEZIER``），
  改类型走 ``drawing.set_types(type=...)`` 且 ``type`` 必须**关键字**传参。
  本插件只需要折线，建笔划后就定型，不做类型切换。
"""

from __future__ import annotations

from dataclasses import dataclass

import bpy
import numpy as np

__all__ = [
    "LAYER_CURVE",
    "OBJECT_NAME",
    "StrokeSet",
    "ensure_object",
    "find_object",
    "refresh",
    "remove_object",
    "write",
]

#: 生成结果固定写进这一个对象，重复构建不会堆一地对象。
OBJECT_NAME = "Sim Hilbert"

#: 图层名（一条曲线一个图层；有空加"初始格子"之类的辅助层时，索引越大越靠上）。
LAYER_CURVE = "Hilbert"

_CURVE_TYPE_POLY = 1

_ATTR_POSITION = "position"
_ATTR_RADIUS = "radius"
_ATTR_OPACITY = "opacity"
_ATTR_VERTEX_COLOR = "vertex_color"


@dataclass(frozen=True)
class StrokeSet:
    """一个图层要画的一组笔划（拓扑 + 点列 + 逐点颜色 + 额外的标量属性）。"""

    layer: str
    #: (S,) 每条笔划的点数，相加 = ``len(points)``。
    counts: np.ndarray
    #: (M,3) 世界坐标点列（顶点顺序 = 希尔伯特序号）。
    points: np.ndarray
    #: 笔划半径（世界单位，GP 的 ``radius``）。
    radius: float
    #: (M,4) **线性** RGBA，逐点。
    colours: np.ndarray
    #: (S,) 是否闭合。
    cyclic: np.ndarray
    #: ``((名字, (M,) float32), ...)``：额外的 POINT 域 FLOAT 属性（数据接口）。
    extras: tuple = ()


def _grease_pencils():
    """4.2 之后 GP 数据块统一叫 ``grease_pencils``（旧分支留兜底）。"""
    return getattr(bpy.data, "grease_pencils_v3", bpy.data.grease_pencils)


def find_object(name: str = OBJECT_NAME):
    """返回生成对象；不存在或类型不对时返回 ``None``。"""
    obj = bpy.data.objects.get(name)
    if obj is None or obj.type != "GREASEPENCIL":
        return None
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


def ensure_object(name: str = OBJECT_NAME, collection=None):
    """拿 GP 对象，没有就建（同名但类型不对的旧对象会被替换）。

    对象进**当前激活的** collection（``context.collection``）；后台 / handler 里取不到时
    才回退到场景根集合。
    """
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


def _drawing(layer):
    """图层当前帧的 drawing（没有帧时返回 ``None``）。"""
    if layer is None or not layer.frames:
        return None
    return layer.frames[0].drawing


def _attribute(drawing, name, data_type, domain, key, values):
    """建（或复用）属性并把整条缓冲写满。

    注意：**不要**在建属性的同时塞一个"默认值 × 点数"的初始化 —— 那要先算对每个
    属性的分量数（FLOAT_COLOR 是 4、FLOAT_VECTOR 是 3），算错就是
    ``RuntimeError: internal error setting the array``。反正下面一定会写满整条缓冲。
    """
    attribute = drawing.attributes.get(name)
    if attribute is None:
        attribute = drawing.attributes.new(name, data_type, domain)
    attribute.data.foreach_set(key, values)
    return attribute


def _write_points(drawing, stroke_set: StrokeSet) -> None:
    """点列 / 半径 / 颜色 / 额外标量（拓扑已存在时用，逐帧走这里）。"""
    points = np.ascontiguousarray(stroke_set.points, np.float32)
    count = len(points)
    if len(drawing.attributes[_ATTR_POSITION].data) != count:
        # 拓扑和点列对不上（例如阶数改过）—— 调用方应该走 write() 重建
        raise ValueError("stroke topology mismatch, rebuild the object")
    drawing.attributes[_ATTR_POSITION].data.foreach_set("vector", points.reshape(-1))
    _attribute(
        drawing,
        _ATTR_RADIUS,
        "FLOAT",
        "POINT",
        "value",
        np.full(count, stroke_set.radius, np.float32),
    )
    _attribute(
        drawing,
        _ATTR_VERTEX_COLOR,
        "FLOAT_COLOR",
        "POINT",
        "color",
        np.ascontiguousarray(stroke_set.colours, np.float32).reshape(-1),
    )
    for name, values in stroke_set.extras:
        _attribute(
            drawing, name, "FLOAT", "POINT", "value", np.asarray(values, np.float32)
        )
    drawing.tag_positions_changed()


def write(obj, stroke_sets, frame: int = 1) -> dict:
    """按 ``stroke_sets`` 重建对象的全部图层与笔划（改几何参数后调它）。

    图层按 ``stroke_sets`` 的顺序创建（越靠后越在上面），返回 ``{图层名: 笔划数}``。
    """
    if not stroke_sets:
        raise ValueError("no strokes to write")

    data = obj.data
    while data.layers:
        data.layers.remove(data.layers[0])
    data.materials.clear()
    # 3D 位置排序 = 逐像素比深度：曲线自交处谁压谁交给渲染器
    data.stroke_depth_order = "3D"

    counts: dict[str, int] = {}
    for stroke_set in stroke_sets:
        layer = data.layers.new(stroke_set.layer, set_active=True)
        layer.use_lights = False  # 实测默认 True，无灯时颜色只剩约 25% 亮度
        drawing = layer.frames.new(int(frame)).drawing
        drawing.add_strokes(np.asarray(stroke_set.counts, np.int32).tolist())
        drawing.set_types(type="POLY")
        _attribute(
            drawing,
            _ATTR_OPACITY,
            "FLOAT",
            "POINT",
            "value",
            np.ones(len(stroke_set.points), np.float32),
        )
        _write_points(drawing, stroke_set)
        for index, closed in enumerate(stroke_set.cyclic):
            drawing.strokes[index].cyclic = bool(closed)
        counts[stroke_set.layer] = len(stroke_set.counts)

    view_layer = bpy.context.view_layer
    obj.select_set(False)
    if view_layer.objects.active is obj:
        view_layer.objects.active = None
    return counts


def refresh(obj, stroke_sets) -> bool:
    """逐帧就地更新点列与颜色（拓扑不变）。返回是否成功。

    下列情况返回 ``False``，由调用方走 :func:`write` 重建：图层被用户删掉、图层集合与
    ``stroke_sets`` 不一致、点数与拓扑对不上、曲线类型被改过。
    """
    wanted = [stroke_set.layer for stroke_set in stroke_sets]
    if [layer.name for layer in obj.data.layers] != wanted:
        return False
    for stroke_set in stroke_sets:
        drawing = _drawing(obj.data.layers.get(stroke_set.layer))
        if drawing is None:
            return False
        if drawing.strokes and drawing.strokes[0].curve_type != _CURVE_TYPE_POLY:
            return False
        try:
            _write_points(drawing, stroke_set)
        except (KeyError, ValueError):
            return False
    return True
