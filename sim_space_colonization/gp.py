"""Grease Pencil v3 后端：节点树 → 一丛笔划（不再生成网格）。

节点树是图，GP 笔划是折线，所以先把树拆成**链**（:func:`solver.branch_chains`）：
一条链 = 从根或某个分叉点走到下一个分叉点或枝尖。分叉点在相邻两条链里各出现一次，
端点重合；配上圆头笔帽，接头看起来就是一整根藤分出来的。逐点半径让枝条从根到梢自然
收细，逐点颜色给根→梢的渐变。

以下都是 **Blender 5.2.2 实测**结论（与 ``sim_hilbert/gp.py``、``gen_zellij/gp.py`` 同源，
改之前先读注释）：

* 颜色不靠材质：``vertex_color``（POINT 域，FLOAT_COLOR）按**线性**存储，色板的 sRGB
  必须先转线性，否则整体发灰发白；
* ``layer.use_lights`` 新建时默认 ``True``，无光照层（``False``）才是所见即所得 ——
  线稿就该这样；
* 点坐标是真的 3D，``stroke_depth_order = '3D'`` 是逐像素深度遮挡：藤蔓自己穿过自己时
  靠前的段会切掉靠后的一段，切口就是笔划自身的圆头轮廓；
* 逐帧写完 ``foreach_set`` 必须 ``tag_positions_changed()``，否则视口拿到的是旧点列；
* ``foreach_set`` 的分量数必须精确：``FLOAT_COLOR`` 是 4、``FLOAT_VECTOR`` 是 3，
  写错报 ``RuntimeError: internal error setting the array``；
* 每帧拓扑都在变（枝条在长），所以每帧 ``remove_strokes`` + ``add_strokes`` 重建：
  实测 2900 笔划 / 7000 点约 0.3 ms，比扫管网格便宜一个数量级。

对象名 ``Space Colonization Vine``。上一版是网格对象（``Space Colonization Tree`` + 几何
节点扫成圆管），:func:`ensure_object` 会把那个旧对象连同节点组、材质一起清掉。
"""

from __future__ import annotations

import bpy
import numpy as np

__all__ = [
    "ATTR_COLOUR",
    "ATTR_OPACITY",
    "ATTR_POSITION",
    "ATTR_RADIUS",
    "LAYER_VINE",
    "OBJECT_NAME",
    "ensure_object",
    "find_object",
    "is_result",
    "remove_legacy",
    "remove_object",
    "write",
]

#: 生成结果固定写进这一个对象，重复构建不会堆一地对象。
OBJECT_NAME = "Space Colonization Vine"
#: 图层名（一条藤一个图层；以后要加辅助层时，索引越大越靠上）。
LAYER_VINE = "Vine"
#: 结果对象的标记：面板「目标网格」的 poll / 自动填充要把它挡掉（它有 ``object`` 类型，
#: 用自定义属性而不是名字判断，用户改名之后照样认得出来）。
RESULT_FLAG = "sim_space_colonization_result"

#: 上一版的网格结果对象 / 节点组 / 材质，第一次构建时顺手清掉。
LEGACY_OBJECT_NAME = "Space Colonization Tree"
LEGACY_GROUP_NAME = "Space Colonization Branch Tubes"
LEGACY_MATERIAL_NAME = "Space Colonization Branch"

ATTR_POSITION = "position"
ATTR_RADIUS = "radius"
ATTR_OPACITY = "opacity"
ATTR_COLOUR = "vertex_color"

_CURVE_TYPE_POLY = 1


def _grease_pencils():
    """4.2 之后 GP 数据块统一叫 ``grease_pencils``（旧分支留兜底）。"""
    return getattr(bpy.data, "grease_pencils_v3", bpy.data.grease_pencils)


def is_result(obj) -> bool:
    """是不是本插件生成的结果对象（不能拿来当输入网格）。"""
    return obj is not None and bool(obj.get(RESULT_FLAG))


def find_object(name: str = OBJECT_NAME):
    """返回生成对象；不存在或类型不对时返回 ``None``。"""
    obj = bpy.data.objects.get(name)
    if obj is None or obj.type != "GREASEPENCIL":
        return None
    return obj


def remove_object(name: str = OBJECT_NAME) -> bool:
    """删除生成对象（连带数据块）。返回是否真的删了。"""
    obj = bpy.data.objects.get(name)
    if obj is None:
        return False
    data = obj.data if obj.type == "GREASEPENCIL" else None
    bpy.data.objects.remove(obj, do_unlink=True)
    if data is not None and data.users == 0:
        _grease_pencils().remove(data)
    return True


def remove_legacy() -> bool:
    """清掉上一版的网格结果对象（连带网格）。返回是否真的删了东西。

    **只删对象**，不碰那个几何节点组和材质：它们可能正被节点编辑器 / 材质编辑器打开着，
    在 handler 里删掉这类数据块容易把 Blender 带进死锁（实测踩过一次，整个进程挂在
    单核 100%）。留在文件里只是几十 KB 的杂物，不值得冒这个险。
    """
    obj = bpy.data.objects.get(LEGACY_OBJECT_NAME)
    if obj is None:
        return False
    mesh = obj.data if obj.type == "MESH" else None
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)
    return True


def ensure_object(collection=None):
    """拿 GP 对象，没有就建（同名但类型不对的旧对象会被替换）。

    对象进**当前激活的** collection（``context.collection``）；后台 / handler 里取不到时
    才回退到场景根集合。
    """
    obj = bpy.data.objects.get(OBJECT_NAME)
    if obj is not None and obj.type != "GREASEPENCIL":
        bpy.data.objects.remove(obj, do_unlink=True)
        obj = None
    if obj is None:
        datas = _grease_pencils()
        data = datas.get(OBJECT_NAME) or datas.new(OBJECT_NAME)
        obj = bpy.data.objects.new(OBJECT_NAME, data)
        target = collection or bpy.context.collection or bpy.context.scene.collection
        target.objects.link(obj)
    elif collection is not None and obj.name not in collection.objects:
        collection.objects.link(obj)
    obj[RESULT_FLAG] = True
    remove_legacy()
    return obj


def _drawing(layer):
    """图层当前帧的 drawing（没有帧时返回 ``None``）。"""
    if layer is None or not layer.frames:
        return None
    return layer.frames[0].drawing


def _attribute(drawing, name, data_type, domain, key, values):
    """建（或复用）属性并把整条缓冲写满。

    **不要**在建属性的同时塞「默认值 × 点数」的初始化 —— 那要先算对每个属性的分量数
    （FLOAT_COLOR 是 4、FLOAT_VECTOR 是 3），算错就是
    ``RuntimeError: internal error setting the array``；反正下面一定会写满。
    """
    attribute = drawing.attributes.get(name)
    if attribute is None:
        attribute = drawing.attributes.new(name, data_type, domain)
    attribute.data.foreach_set(key, values)
    return attribute


def write(obj, counts, points, radius, colours, frame: int = 1) -> dict:
    """按笔划拓扑重画整棵树（每帧都走这里：枝条在长，拓扑一直在变）。

    ``counts[S]`` 是每条笔划的点数，``points`` / ``radius`` / ``colours`` 已按笔划顺序
    展平（``M = sum(counts)``；颜色是**线性** RGBA）。返回 ``{strokes, points}``。
    """
    data = obj.data
    data.stroke_depth_order = "3D"

    layer = data.layers.get(LAYER_VINE)
    if layer is None:
        layer = data.layers.new(LAYER_VINE, set_active=True)
    layer.use_lights = False

    drawing = _drawing(layer)
    if drawing is None:
        drawing = layer.frames.new(int(frame)).drawing
    elif drawing.strokes:
        drawing.remove_strokes(indices=list(range(len(drawing.strokes))))

    counts = np.ascontiguousarray(counts, np.int32)
    drawing.add_strokes(counts.tolist())
    drawing.set_types(type="POLY")

    _attribute(
        drawing,
        ATTR_OPACITY,
        "FLOAT",
        "POINT",
        "value",
        np.ones(len(points), np.float32),
    )
    _attribute(
        drawing,
        ATTR_POSITION,
        "FLOAT_VECTOR",
        "POINT",
        "vector",
        np.ascontiguousarray(points, np.float32).reshape(-1),
    )
    _attribute(
        drawing,
        ATTR_RADIUS,
        "FLOAT",
        "POINT",
        "value",
        np.ascontiguousarray(radius, np.float32),
    )
    _attribute(
        drawing,
        ATTR_COLOUR,
        "FLOAT_COLOR",
        "POINT",
        "color",
        np.ascontiguousarray(colours, np.float32).reshape(-1),
    )
    drawing.tag_positions_changed()

    return {"strokes": len(counts), "points": len(points)}
