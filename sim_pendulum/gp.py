"""Grease Pencil v3 后端：一个双摆三笔（下摆 / 上摆 / 扣子）→ 分层 GP 对象。

只认 :class:`sim_pendulum.strokes.Strokes` 契约（笔划点数 / 点列 / 逐点半径 / 逐点颜色 /
额外标量），与物理完全解耦。以下行为是 **Blender 5.2.2 实测**结论（与 ``sim_hilbert/gp.py``
同源，改动前先读注释）：

* 颜色不用材质：``vertex_color``（POINT 域，FLOAT_COLOR）按**线性**存储，
  所以 sRGB 0..1 必须先转线性，否则整体发灰发白；
* ``layer.use_lights`` 新建时默认 ``True``，无光照层（``use_lights = False``）
  颜色所见即所得 —— 线稿就该这样；
* **逐点半径是真逐点**：``radius`` 是 POINT 域 FLOAT，一个点一份，``foreach_set("value", ...)``
  直接给整条缓冲。杆两端是**圆头**（GP 笔划天生如此），所以等宽的三笔看上去就是
  "两根两端圆头的杆 + 一颗扣住接缝的圆点"；
* **一笔之内颜色是插值的**：想"上摆一个色、下摆另一个色"，只能拆成两条笔划 ——
  一条 3 点折线渲染出来是从悬挂点到摆锤的渐变，两根杆的结构就糊了；
* 逐帧改位置：``foreach_set("vector", flat)`` 之后必须 ``tag_positions_changed()``，
  否则视口拿到的是旧点列；
* ``foreach_set`` 的长度必须精确：``FLOAT_COLOR`` 是 4 个分量、``FLOAT_VECTOR`` 是 3 个，
  算错就报 ``RuntimeError: internal error setting the array``。本后端建完属性直接写满整条
  缓冲，不在建属性时塞"默认值 × 点数"；
* ``curve_type`` 是 CURVE 域的 INT8 属性（``1 = POLY``、``2 = BEZIER``），
  改类型走 ``drawing.set_types(type=...)`` 且 ``type`` 必须**关键字**传参；
* ``stroke_depth_order = '2D'`` 才是这套画法要的：绘制顺序 = 笔划顺序，于是"扣子"压在
  "上摆"上、"上摆"压在"下摆"上（``'3D'`` 是逐像素比深度，全部笔划都在 Y=0 的同一平面上，
  谁压谁变成随机的，接头会被切开）。

**同步效率**（5.2.2 实测，见 README）：``5S`` 个点的位置 + 半径 + 颜色 + 6 个标量一共
``5S×(3+1+4+6) = 70S`` 个 float32，而网格后端同样的 5 顶点 / 摆是
``5S×(3+4+1) + 5S×6 = 70S`` —— 数据量一样，差的是写回速度（GP 快一个数量级）。
"""

from __future__ import annotations

import bpy
import numpy as np

__all__ = [
    "OBJECT_NAME",
    "find_object",
    "refresh",
    "remove_object",
    "ensure_object",
    "write",
]

#: 生成结果固定写进这一个对象，重复构建不会堆一地对象。
OBJECT_NAME = "Sim Pendulum"

_CURVE_TYPE_POLY = 1

_ATTR_POSITION = "position"
_ATTR_RADIUS = "radius"
_ATTR_OPACITY = "opacity"
_ATTR_VERTEX_COLOR = "vertex_color"


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


def ensure_object(name: str = OBJECT_NAME, collection=None, scene=None):
    """拿 GP 对象，没有就建（同名但类型不对的旧对象会被替换）。

    对象进**当前激活的** collection（``context.collection``）；后台 / handler 里取不到时
    才回退到场景根集合。给了 ``scene`` 还会补一次"必须在目标场景里"的检查 ——
    ``bpy.context.collection`` 可能还停在上一个场景（同一个脚本里刚切过场景时就是这样），
    那样对象会被链进**别的场景**，渲染出来一片空白。
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
    if scene is not None and obj.name not in scene.objects:
        scene.collection.objects.link(obj)
    return obj


def _layer_name(strokes) -> str:
    return strokes.layer


def _drawing(layer):
    """图层当前帧的 drawing（没有帧时返回 ``None``）。"""
    if layer is None or not layer.frames:
        return None
    return layer.frames[0].drawing


def _attribute(drawing, name, data_type, domain, key, values):
    """建（或复用）属性并把整条缓冲写满。

    注意：**不要**在建属性的同时塞一个"默认值 × 点数"的初始化 —— 那要先算对每个
    属性的分量数（``FLOAT_COLOR`` 是 4、``FLOAT_VECTOR`` 是 3），算错就是
    ``RuntimeError: internal error setting the array``。下面一定会写满整条缓冲。
    """
    attribute = drawing.attributes.get(name)
    if attribute is None:
        attribute = drawing.attributes.new(name, data_type, domain)
    attribute.data.foreach_set(key, values)
    return attribute


def _write_buffers(drawing, strokes, positions: bool = True) -> None:
    """点列 / 逐点半径 / 逐点颜色 / 额外标量（拓扑已存在时用，逐帧走这里）。

    ``positions=False`` 只在**逐帧刷新**时用（点阵画法的位置是静态的，建的时候必须写一次）。
    """
    total = len(strokes.points)
    if len(drawing.attributes[_ATTR_POSITION].data) != total:
        # 拓扑和点列对不上（例如网格边长改过）—— 调用方应该走 write() 重建
        raise ValueError("stroke topology mismatch, rebuild the object")

    if positions:
        drawing.attributes[_ATTR_POSITION].data.foreach_set(
            "vector", strokes.points.reshape(-1)
        )
    _attribute(drawing, _ATTR_RADIUS, "FLOAT", "POINT", "value", strokes.radii)
    _attribute(
        drawing,
        _ATTR_VERTEX_COLOR,
        "FLOAT_COLOR",
        "POINT",
        "color",
        strokes.colours.reshape(-1),
    )
    for name, values in strokes.extras:
        _attribute(drawing, name, "FLOAT", "POINT", "value", values)
    drawing.tag_positions_changed()


def write(obj, strokes, frame: int = 1) -> int:
    """按 ``strokes`` 重建对象的图层与全部笔划（改网格边长 / 初值后调它）。

    返回笔划数。
    """
    data = obj.data
    while data.layers:
        data.layers.remove(data.layers[0])
    data.materials.clear()
    # 2D 排序 = 按笔划顺序画：扣子压上摆、上摆压下摆（'3D' 在同一平面上谁压谁是随机的）
    data.stroke_depth_order = "2D"

    layer = data.layers.new(_layer_name(strokes), set_active=True)
    layer.use_lights = False  # 实测默认 True，无灯时颜色只剩约 25% 亮度
    drawing = layer.frames.new(int(frame)).drawing
    drawing.add_strokes(np.asarray(strokes.sizes, np.int32).tolist())
    drawing.set_types(type="POLY")
    _attribute(
        drawing,
        _ATTR_OPACITY,
        "FLOAT",
        "POINT",
        "value",
        np.ones(len(strokes.points), np.float32),
    )
    _write_buffers(drawing, strokes)
    # 闭合标记整层一次写完：``cyclic`` 是 CURVE 域的 BOOLEAN 属性。
    # 逐笔划写（``stroke.cyclic = ...``）是 RNA 调用，上万笔时是几十毫秒的纯开销。
    _attribute(
        drawing,
        "cyclic",
        "BOOLEAN",
        "CURVE",
        "value",
        np.array(strokes.cyclic, dtype=bool),
    )

    view_layer = bpy.context.view_layer
    obj.select_set(False)
    if view_layer.objects.active is obj:
        view_layer.objects.active = None
    return len(strokes.sizes)


def refresh(obj, strokes) -> bool:
    """逐帧就地更新点列 / 半径 / 颜色（拓扑不变）。返回是否成功。

    返回 ``False`` 时调用方走 :func:`write` 重建：图层被删 / 图层集合对不上 /
    点数与拓扑不符 / 曲线类型被改过。
    """
    layers = obj.data.layers
    if len(layers) != 1 or layers[0].name != _layer_name(strokes):
        return False
    drawing = _drawing(layers[0])
    if drawing is None:
        return False
    if drawing.strokes and drawing.strokes[0].curve_type != _CURVE_TYPE_POLY:
        return False
    try:
        # 点阵画法的位置钉死在网格上：百万点时这 12 MB/帧 就省掉了
        _write_buffers(drawing, strokes, positions=not strokes.static_points)
    except (KeyError, ValueError):
        return False
    return True
