"""Grease Pencil v3 后端：单元列表 → 一个 GP 对象（每格一条闭合笔划）。

写进 drawing 的属性：:

    POINT  position      float3  点坐标（GP 自带）
    POINT  radius        float   线宽
    POINT  opacity       float   不透明度
    POINT  vertex_color  color   线色（**线性**存，见 :func:`_linear_rgba`）
    POINT  cell_index    int     这个点属于第几个种子（数据接口，跟参考实现同名）
    CURVE  fill_id       int     非 0 = 填充，逐格一个 id（相邻格不会被合并成一整片）
    CURVE  fill_color    color   填充色
    CURVE  cyclic                每条笔划闭合

**颜色不用材质**：``vertex_color`` 就够，:func:`build_object` 还会 ``materials.clear()``
（GP 的材质槽留着反而让人以为颜色由材质决定）。所以 3D 视图 Solid 模式得把
「着色 → 颜色」切到 Attribute 才看得见 —— 生成算子会顺手切一次（``viewport``）。

**实测坑（5.2.2）**：

* 新建图层 ``use_lights`` 默认 ``True``，没有灯时颜色只剩约四分之一亮度；
  本后端一律建**无光照层**（``use_lights = False``），颜色所见即所得；
* 不给 ``miter_angle`` 属性 —— 没有它就按**圆角**连接拐角（有它才是斜接），
  圆润的单元轮廓不会崩出尖角；
* ``add_strokes`` / ``remove_strokes`` 是**结构性**改动：改完手里那个 drawing 指针
  就失效了（再往上 ``foreach_set`` 会报 ``internal error setting the array``），
  必须重新从 ``layer.frames[...].drawing`` 取一次；
* ``foreach_set`` 的长度对不上时 Blender 只报一句 ``internal error setting the
  array``，不说是哪条属性 —— 所以每次写之前都按 ``_ensure`` 里的领域长度对一遍；
* ``layer.frames.remove()`` 收的是**帧号**（int），不是 ``GreasePencilFrame`` 对象；
* ``fill_color`` 的 alpha 是**预乘**的：乘在颜色上，不会被当透明度去透背景
  （实测采样见 ``README.md``），所以面板上那一项叫「填充浓度」。
"""

from __future__ import annotations

import bpy
import numpy as np

from .palette import srgb_to_linear

__all__ = [
    "LAYER_NAME",
    "OBJECT_NAME",
    "SEEDS_LAYER",
    "build_object",
    "clear_object",
    "create_object",
    "find_object",
    "remove_object",
    "set_colour",
    "set_fill",
    "set_layer_radius",
    "write_dots",
]

#: 生成结果固定写进这一个对象，反复生成不会堆一地对象。
OBJECT_NAME = "Voronoi Cells"
#: 图层名。
LAYER_NAME = "Cells"
#: 撒种子用的图层名（种子对象是用户自己编辑的东西，跟生成物分开）。
SEEDS_LAYER = "Seeds"

_ATTR_POSITION = "position"
_ATTR_RADIUS = "radius"
_ATTR_OPACITY = "opacity"
_ATTR_VERTEX_COLOR = "vertex_color"
_ATTR_CELL_INDEX = "cell_index"
_ATTR_FILL_ID = "fill_id"
_ATTR_FILL_COLOR = "fill_color"

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
        # 别的类型的对象占了这个名字：删掉重来
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


# --- 图层 / drawing -------------------------------------------------------


def _layer(obj, name: str = LAYER_NAME):
    data = getattr(obj, "data", None)
    return None if data is None else data.layers.get(name)


def _drawing(layer):
    """图层当前的 drawing；图层是空的就返回 ``None``。"""
    if layer is None or not len(layer.frames):
        return None
    return layer.frames[0].drawing


def _prepare(obj, frame: int, name: str = LAYER_NAME):
    """拿到（必要时建好）图层并返回它的 drawing。

    图层固定只有一帧，并且**每轮都挪到当前帧上**：GP 显示的是「不晚于当前帧的最后
    一帧」，只有一帧停在别的帧号上时，用户换了当前帧就会看不见东西。
    """
    layer = _layer(obj, name)
    if layer is None:
        layer = obj.data.layers.new(name, set_active=True)
    # 实测：新图层默认 use_lights=True，没灯时颜色只剩约四分之一亮度
    layer.use_lights = False

    for item in list(layer.frames):
        if item.frame_number != int(frame):
            # 实测：`frames.remove()` 收的是**帧号**，不是 GreasePencilFrame 对象
            layer.frames.remove(item.frame_number)
    if not len(layer.frames):
        layer.frames.new(int(frame))
    return layer.frames[0].drawing


def create_object(name: str, collection=None):
    """新建一个空的 GP 对象（数据块同名），进当前激活的 collection。"""
    datas = _grease_pencils()
    data = datas.new(name)
    obj = bpy.data.objects.new(name, data)
    target = collection or bpy.context.collection or bpy.context.scene.collection
    target.objects.link(obj)
    return obj


# --- 属性写入 -------------------------------------------------------------


def _ensure(drawing, name: str, dtype: str, domain: str):
    """按当前长度 / 领域 / 类型取一条属性，不合适就重建。"""
    count = (
        len(drawing.attributes[_ATTR_POSITION].data)
        if domain == _POINT
        else len(drawing.strokes)
    )
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


def _write_points(drawing, name: str, key: str, values) -> None:
    _ensure(drawing, name, "FLOAT", _POINT).data.foreach_set(key, values)


def _linear_rgba(colour, alpha: float = 1.0) -> list[float]:
    """面板存的是 sRGB（``COLOR_GAMMA``），写进属性前转线性。"""
    if colour is None:
        return [0.0, 0.0, 0.0, alpha]
    return [
        srgb_to_linear(float(colour[0])),
        srgb_to_linear(float(colour[1])),
        srgb_to_linear(float(colour[2])),
        float(alpha),
    ]


def _tile(rgba, count: int) -> np.ndarray:
    return np.tile(np.asarray(rgba, dtype=np.float32), (count, 1)).reshape(-1)


# --- 写入 -----------------------------------------------------------------


def build_object(
    cells,
    *,
    width: float,
    colour=(0.0, 0.0, 0.0),
    fill=None,
    fill_alpha: float = 0.0,
    frame: int = 1,
    collection=None,
    name: str = OBJECT_NAME,
):
    """把单元写进生成对象，返回对象；``cells`` 是 ``voronoi.Cell`` 的序列。

    对象已存在时就地更新：图层复用（不重建，图层的显示设置与修改器都不动），只把
    笔划清掉重写。这样实时更新时不会每帧重建数据块，编辑模式里也不会打断编辑。
    """
    obj = _ensure_object(name, collection)
    data = obj.data
    data.materials.clear()

    drawing = _prepare(obj, frame)
    if len(drawing.strokes):
        drawing.remove_strokes(indices=list(range(len(drawing.strokes))))
        # 结构性改动之后旧指针失效，重新取一次（见模块说明）
        drawing = _prepare(obj, frame)

    counts = [len(cell.points) for cell in cells]
    drawing.add_strokes(counts)
    drawing = _prepare(obj, frame)
    total = int(sum(counts))

    positions = np.empty((total, 3), dtype=np.float32)
    indices = np.empty(total, dtype=np.int32)
    cursor = 0
    for cell in cells:
        size = len(cell.points)
        positions[cursor : cursor + size, :2] = cell.points
        positions[cursor : cursor + size, 2] = cell.z
        indices[cursor : cursor + size] = cell.index
        cursor += size

    drawing.attributes[_ATTR_POSITION].data.foreach_set("vector", positions.reshape(-1))
    _write_points(
        drawing, _ATTR_RADIUS, "value", np.full(total, float(width), np.float32)
    )
    _write_points(drawing, _ATTR_OPACITY, "value", np.ones(total, np.float32))
    _ensure(drawing, _ATTR_VERTEX_COLOR, "FLOAT_COLOR", _POINT).data.foreach_set(
        "color", _tile(_linear_rgba(colour), total)
    )
    _ensure(drawing, _ATTR_CELL_INDEX, "INT", _POINT).data.foreach_set("value", indices)

    filled = fill is not None and fill_alpha > 0.0
    ids = [index + 1 for index in range(len(cells))] if filled else [0] * len(cells)
    _ensure(drawing, _ATTR_FILL_ID, "INT", _CURVE).data.foreach_set("value", ids)
    if filled:
        rgba = _linear_rgba(fill, fill_alpha)
        _ensure(drawing, _ATTR_FILL_COLOR, "FLOAT_COLOR", _CURVE).data.foreach_set(
            "color", np.tile(np.asarray(rgba, np.float32), (len(cells), 1)).reshape(-1)
        )

    for stroke in drawing.strokes:
        stroke.cyclic = True

    return obj


def write_dots(
    obj,
    points,
    *,
    width: float = 0.02,
    colour=(0.0, 0.0, 0.0),
    frame: int = 1,
    layer_name: str = SEEDS_LAYER,
) -> int:
    """往对象里撒一串**单点笔划**（一个种子一条），返回写了几个。

    单点笔划是给 GP 编辑模式准备的：每个种子是独立的一笔，框选 / 移动都按点来；
    塞进一条笔划里的话，选全体只能整体挪。

    点坐标是**对象局部**的（调用方保证对象变换是单位阵，见 ``ops.scatter``）。
    """
    drawing = _prepare(obj, frame, layer_name)
    if len(drawing.strokes):
        drawing.remove_strokes(indices=list(range(len(drawing.strokes))))
        drawing = _prepare(obj, frame, layer_name)

    total = len(points)
    if not total:
        return 0
    drawing.add_strokes([1] * total)
    drawing = _prepare(obj, frame, layer_name)

    flat = np.asarray(points, dtype=np.float32).reshape(-1)
    drawing.attributes[_ATTR_POSITION].data.foreach_set("vector", flat)
    _write_points(
        drawing, _ATTR_RADIUS, "value", np.full(total, float(width), np.float32)
    )
    _write_points(drawing, _ATTR_OPACITY, "value", np.ones(total, np.float32))
    _ensure(drawing, _ATTR_VERTEX_COLOR, "FLOAT_COLOR", _POINT).data.foreach_set(
        "color", _tile(_linear_rgba(colour), total)
    )
    return total


# --- 就地改（不重建几何） -------------------------------------------------


def set_layer_radius(obj, width: float) -> bool:
    """就地改线宽：只重写 ``radius`` 属性。"""
    drawing = _drawing(_layer(obj))
    if drawing is None or _ATTR_RADIUS not in drawing.attributes:
        return False
    attribute = drawing.attributes[_ATTR_RADIUS]
    attribute.data.foreach_set(
        "value", np.full(len(attribute.data), float(width), np.float32)
    )
    return True


def set_colour(obj, colour) -> bool:
    """就地改线色：只重写 ``vertex_color`` 属性。"""
    drawing = _drawing(_layer(obj))
    if drawing is None or _ATTR_VERTEX_COLOR not in drawing.attributes:
        return False
    attribute = drawing.attributes[_ATTR_VERTEX_COLOR]
    attribute.data.foreach_set(
        "color", _tile(_linear_rgba(colour), len(attribute.data))
    )
    return True


def set_fill(obj, enabled: bool, colour=None, alpha: float = 0.0) -> bool:
    """就地开 / 关填充：重写两条 ``CURVE`` 域属性，笔划点数一点不动。"""
    drawing = _drawing(_layer(obj))
    if drawing is None or not len(drawing.strokes):
        return False
    filled = bool(enabled) and alpha > 0.0
    count = len(drawing.strokes)
    ids = [index + 1 for index in range(count)] if filled else [0] * count
    _ensure(drawing, _ATTR_FILL_ID, "INT", _CURVE).data.foreach_set("value", ids)
    if filled and colour is not None:
        rgba = _linear_rgba(colour, alpha)
        _ensure(drawing, _ATTR_FILL_COLOR, "FLOAT_COLOR", _CURVE).data.foreach_set(
            "color",
            np.tile(np.asarray(rgba, np.float32), (len(ids), 1)).reshape(-1),
        )
    return True
