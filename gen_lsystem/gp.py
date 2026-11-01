"""Grease Pencil v3 后端：路径列表 → 一个 GP 对象（每条路径一笔）。

写进 drawing 的属性::

    POINT  position      float3      点坐标（GP 自带）
    POINT  radius        float       最终线宽 = base_radius × 宽度倍数（GP 就认这条）
    POINT  base_radius   float       几何算出来的粗细（``!`` / ``#`` / ``¤`` 的结果）
    POINT  taper         float       归一化粗细 0..1（1 = 最细），渐变配色按它插值
    POINT  progress      float       沿这一笔的进度 0..1（按弧长，每笔各自重来）
    POINT  opacity       float       不透明度
    POINT  vertex_color  color       线色（**线性**存，见 :func:`_linear_rgba`）
    POINT  depth         int         分支深度（``[`` 的层数）—— 数据接口，可拿去做颜色 / 动画

三条渐变档（``RADIUS`` / ``DEPTH`` / ``PROGRESS``）用的都是这些**已经写好的**属性：
切档只是按另一条重算一遍颜色，路径与几何一概不动。

为什么要把 ``radius`` 拆成 ``base_radius`` + 宽度倍数：L-system 的粗细是**几何**（``!`` 逐级
变细、``¤`` 直接设值），但「整体粗一点」是**外观**。拆开以后拖「宽度倍数」只是拿
``base_radius`` 重算一遍 ``radius`` 属性，路径一个点都不用重算。

**颜色不用材质**：``vertex_color`` 就够，:func:`build_object` 还会 ``materials.clear()``
（GP 的材质槽留着反而让人以为颜色由材质决定）。所以 3D 视图 Solid 模式得把
「着色 → 颜色」切到 Attribute 才看得见 —— 生成算子会顺手切一次（``viewport``）。

**实测坑（5.2.2）**：

* 新建图层 ``use_lights`` 默认 ``True``，没有灯时颜色只剩约四分之一亮度；
  本后端一律建**无光照层**（``use_lights = False``）；
* ``add_strokes`` 是**结构性**改动：改完手里那个 drawing 指针就失效了（再往上
  ``foreach_set`` 会报 ``internal error setting the array``），必须重新从
  ``layer.frames[0].drawing`` 取一次；
* ``foreach_set`` 的长度对不上时 Blender 只报一句 ``internal error setting the array``，
  不说是哪条属性 —— 所以 :func:`_ensure` 每次按领域长度自己比一遍；
* 不给 ``miter_angle`` 属性：没有它拐角按**圆角**连，折线不会崩出尖角。
"""

from __future__ import annotations

import bpy
import numpy as np

from .palette import srgb_to_linear
from .turtle import Skeleton

__all__ = [
    "LAYER_NAME",
    "OBJECT_NAME",
    "build_object",
    "clear_object",
    "find_object",
    "read_bounds",
    "remove_object",
    "set_colours",
    "set_opacity",
    "set_width_scale",
    "write_bounds",
]

#: 生成对象的名字（固定名，方便找回来；反复生成不会堆一地对象）。
OBJECT_NAME = "L-System"
#: 图层名。
LAYER_NAME = "Branches"

#: 包围盒记在对象上的自定义属性名（``setup_scene`` 取景用；GP 对象的 ``bound_box``
#: 在数据刚写完时可能还是旧的）。
BOUNDS_MIN = "gen_lsystem_bounds_min"
BOUNDS_MAX = "gen_lsystem_bounds_max"

_ATTR_POSITION = "position"
_ATTR_RADIUS = "radius"
_ATTR_BASE_RADIUS = "base_radius"
_ATTR_TAPER = "taper"
_ATTR_PROGRESS = "progress"
_ATTR_OPACITY = "opacity"
_ATTR_VERTEX_COLOR = "vertex_color"
_ATTR_DEPTH = "depth"

_POINT = "POINT"


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


# --- 属性写入 ---------------------------------------------------------------


def _ensure(drawing, name: str, dtype: str, domain: str):
    """按当前长度 / 领域 / 类型取一条属性，不合适就重建。"""
    count = (
        len(drawing.attributes[_ATTR_POSITION].data) if domain == _POINT else len(drawing.strokes)
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


def _tint(base: np.ndarray) -> np.ndarray:
    """归一化粗细：最粗 = 0，最细 = 1（``按粗细`` 渐变按它插值）。"""
    if not len(base):
        return base
    widest = float(base.max())
    if widest <= 0.0:
        return np.zeros_like(base)
    return np.clip(1.0 - base / widest, 0.0, 1.0).astype(np.float32)


def _depth_tint(depths: np.ndarray) -> np.ndarray:
    """归一化分支深度：主干 = 0，最深的枝梢 = 1（``按深度`` 渐变按它插值）。"""
    if not len(depths):
        return np.zeros(0, np.float32)
    deepest = int(depths.max())
    if deepest <= 0:
        return np.zeros(len(depths), np.float32)
    return (depths.astype(np.float32) / float(deepest)).astype(np.float32)


def _shade(mode: str, base: np.ndarray, depths: np.ndarray, progress=None):
    """渐变参数：``RADIUS`` / ``DEPTH`` / ``PROGRESS`` 各给一条 0..1，其余返回 ``None``（单色）。

    渐变是**外观**，不动几何：三条原始数据（``base_radius`` / ``depth`` / ``progress``）都在
    属性里存着，切模式只是按另一条重算一遍颜色。“按进度”直接用 turtle 层烘好的 0..1，
    不再归一化 —— 单笔图案（龙形 / 科赫 / 希尔伯特）就是「从起点扫到终点」。
    """
    if mode == "RADIUS":
        return _tint(base)
    if mode == "DEPTH":
        return _depth_tint(depths)
    if mode == "PROGRESS":
        if progress is None:
            return np.zeros(len(base), np.float32)
        return np.clip(np.asarray(progress, np.float32), 0.0, 1.0)
    return None


def _colour_array(count: int, colour, tip=None, shade=None) -> np.ndarray:
    """逐点颜色：给了 ``shade`` 且给了末色时，在主色与末色之间按它插值。"""
    if not count:
        return np.zeros(0, np.float32)
    main = np.asarray(_linear_rgba(colour), dtype=np.float32)
    if tip is not None and shade is not None and len(shade) == count:
        end = np.asarray(_linear_rgba(tip), dtype=np.float32)
        return (main + (end - main) * shade.reshape(-1, 1)).reshape(-1).astype(np.float32)
    return np.tile(main, (count, 1)).reshape(-1)


# --- 写入 -------------------------------------------------------------------


def build_object(
    skeleton: Skeleton,
    *,
    width_scale: float = 1.0,
    colour=(0.0, 0.0, 0.0),
    tip_colour=None,
    gradient: str = "NONE",
    opacity: float = 1.0,
    frame: int = 1,
    collection=None,
):
    """把路径写进生成对象（图层 / 笔划 / 属性），返回对象。

    对象已存在时就**清空图层重建**（与 ``gen_zellij`` 一路）：路径数是几何的一部分，
    复用图层只会让「上一版多出来的笔划」留在原地。
    """
    obj = _ensure_object(collection=collection)
    data = obj.data
    while data.layers:
        data.layers.remove(data.layers[0])
    data.materials.clear()

    paths = [path for path in skeleton.paths if len(path.points) >= 2]
    write_bounds(obj, skeleton)
    if not paths:
        return obj

    layer = data.layers.new(LAYER_NAME, set_active=True)
    # 实测：新图层默认 use_lights=True，没灯时颜色只剩约四分之一亮度
    layer.use_lights = False

    layer.frames.new(int(frame))
    drawing = layer.frames[0].drawing
    counts = [len(path.points) for path in paths]
    drawing.add_strokes(counts)
    # 结构性改动之后旧指针失效，重新取一次（见模块说明）
    drawing = layer.frames[0].drawing
    total = int(sum(counts))

    positions = np.asarray([point for path in paths for point in path.points], dtype=np.float32)
    base = np.asarray([radius for path in paths for radius in path.radii], dtype=np.float32)
    depths = np.asarray(
        [path.depth for path in paths for _point in path.points], dtype=np.int32
    )
    progress = np.asarray(
        [
            value
            for path in paths
            for value in (path.progress or (0.0,) * len(path.points))
        ],
        dtype=np.float32,
    )
    tint = _tint(base)
    scale = float(width_scale)

    drawing.attributes[_ATTR_POSITION].data.foreach_set("vector", positions.reshape(-1))
    _ensure(drawing, _ATTR_BASE_RADIUS, "FLOAT", _POINT).data.foreach_set("value", base)
    _ensure(drawing, _ATTR_RADIUS, "FLOAT", _POINT).data.foreach_set(
        "value", np.maximum(base * scale, 0.0)
    )
    _ensure(drawing, _ATTR_TAPER, "FLOAT", _POINT).data.foreach_set("value", tint)
    _ensure(drawing, _ATTR_PROGRESS, "FLOAT", _POINT).data.foreach_set("value", progress)
    _ensure(drawing, _ATTR_OPACITY, "FLOAT", _POINT).data.foreach_set(
        "value", np.full(total, float(opacity), np.float32)
    )
    _ensure(drawing, _ATTR_DEPTH, "INT", _POINT).data.foreach_set("value", depths)
    _ensure(drawing, _ATTR_VERTEX_COLOR, "FLOAT_COLOR", _POINT).data.foreach_set(
        "color",
        _colour_array(
            total, colour, tip_colour, _shade(str(gradient), base, depths, progress)
        ),
    )
    return obj


def write_bounds(obj, skeleton: Skeleton) -> bool:
    """把包围盒记到对象上，``setup_scene`` 据此取景。没有点时清掉旧的。"""
    bounds = skeleton.bounds()
    if bounds is None:
        for key in (BOUNDS_MIN, BOUNDS_MAX):
            if key in obj:
                del obj[key]
        return False
    low, high = bounds
    obj[BOUNDS_MIN] = [float(value) for value in low]
    obj[BOUNDS_MAX] = [float(value) for value in high]
    return True


def read_bounds(obj):
    """从对象上取回包围盒：``(最小角, 最大角)``；没记过就退回 ``bound_box``。

    还没生成对象时 ``obj is None`` —— 返回 ``None``，「场景预设」据此提示「先点生成」。
    """
    if obj is None:
        return None
    low = obj.get(BOUNDS_MIN)
    high = obj.get(BOUNDS_MAX)
    if low is not None and high is not None and len(low) == 3 and len(high) == 3:
        return tuple(float(value) for value in low), tuple(float(value) for value in high)
    if not len(obj.bound_box):
        return None
    from mathutils import Vector

    corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    return (
        tuple(min(corner[axis] for corner in corners) for axis in range(3)),
        tuple(max(corner[axis] for corner in corners) for axis in range(3)),
    )


# --- 就地改（不重算路径） ---------------------------------------------------


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


def _values(drawing, name: str, dtype=np.float32):
    """取一条 POINT 域属性的值（numpy 数组）；没有就返回 ``None``。"""
    attribute = None if drawing is None else drawing.attributes.get(name)
    if attribute is None:
        return None
    values = np.empty(len(attribute.data), dtype=dtype)
    attribute.data.foreach_get("value", values)
    return values


def set_width_scale(obj, scale: float) -> bool:
    """就地改整体粗细：拿 ``base_radius`` 重算 ``radius``，路径一点不动。"""
    drawing = _drawing(obj)
    base = _values(drawing, _ATTR_BASE_RADIUS)
    if base is None:
        return False
    drawing.attributes[_ATTR_RADIUS].data.foreach_set(
        "value", np.maximum(base * float(scale), 0.0)
    )
    return True


def set_colours(obj, colour, tip=None, gradient: str = "NONE") -> bool:
    """就地改线色 / 渐变色：只重写 ``vertex_color``。

    渐变的三条原始数据（``base_radius`` / ``depth`` / ``progress``）都在属性里，切模式只是
    换个算法重算，不碰几何。
    """
    drawing = _drawing(obj)
    base = _values(drawing, _ATTR_BASE_RADIUS)
    depths = _values(drawing, _ATTR_DEPTH, np.int32)
    if base is None or depths is None:
        return False
    # 老对象（这次改动之前生成的）没有 progress：按 0 处理，等下次「生成」补上
    progress = _values(drawing, _ATTR_PROGRESS)
    drawing.attributes[_ATTR_VERTEX_COLOR].data.foreach_set(
        "color",
        _colour_array(
            len(base), colour, tip, _shade(str(gradient), base, depths, progress)
        ),
    )
    return True


def set_opacity(obj, opacity: float) -> bool:
    """就地改不透明度：只重写 ``opacity``。"""
    drawing = _drawing(obj)
    if drawing is None or _ATTR_OPACITY not in drawing.attributes:
        return False
    attribute = drawing.attributes[_ATTR_OPACITY]
    attribute.data.foreach_set(
        "value", np.full(len(attribute.data), float(opacity), np.float32)
    )
    return True
