"""写 Grease Pencil：点集 → 一个 GP 对象，**每个整数一条单顶点笔划**。

为什么一个点要单独一条笔划
    GP 的笔划是"折线"：把一万个点塞进一条笔划，GP 会老老实实把它们连成一万段线，
    画出来是一张蛛网。要把点画成点，只能一点一笔 —— 实测 Blender 5.2 里**单顶点笔划
    确实渲染成圆点**（半径由 ``radius`` 属性决定），这也是这个插件选 GP 当输出的底气。

属性（都是 ``POINT`` 域，和仓库其它 ``gen_*`` 的写法一致）::

    position      float3      点坐标（GP 自带）
    radius        float       点半径 = dot_radius
    vertex_color  color       线色（**线性**存）
    index         int         这个点是哪一号整数（0 .. |D|^places-1，地址本身）
    digit_slot    int         它用哪一号颜色（= 最高位 / 指定位的编号）

``index`` 与 ``digit_slot`` 是**数据接口**：几何算完就写死在那里，之后想按位筛选、
做动画、或换成几何节点驱动，都不用重算点集。
"""

from __future__ import annotations

import bpy
import numpy as np

from . import palette
from .i18n import tfmt

__all__ = ["LAYER_NAME", "OBJECT_NAME", "build", "find_object", "remove_object"]

#: 生成对象名（固定名，反复生成不会堆一地对象）。
OBJECT_NAME = "Imaginary Base"
LAYER_NAME = "Points"
#: 包围盒记在对象上的自定义属性名（「场景预设」取景用）。
BOUNDS_MIN = "gen_imaginary_base_bounds_min"
BOUNDS_MAX = "gen_imaginary_base_bounds_max"

_POSITION = "position"
_RADIUS = "radius"
_COLOUR = "vertex_color"
_INDEX = "index"
_SLOT = "digit_slot"
_POINT = "POINT"


def _grease_pencils():
    """GP 数据块集合：4.3 / 4.4 叫 ``grease_pencils_v3``，5.0 起改回 ``grease_pencils``（此时已是 v3）。

    4.2 的 ``bpy.data.grease_pencils`` 是**旧版** GP：有 layers / frames，但 frame 上
    没有 ``drawing``，也没有点域属性层。所以这里不做「找不到 v3 就退回 grease_pencils」
    的兜底 —— 那会一路走到 ``frame.drawing`` 才抛 AttributeError，报错完全看不出原因。
    版本下限已经写在 manifest 里（4.3.0），这里只是让越界时给一句话说得清的错。
    """
    collection = getattr(bpy.data, "grease_pencils_v3", None)
    if collection is not None:
        return collection
    if bpy.app.version < (4, 3, 0):
        raise RuntimeError(
            "Gen Imaginary Base 需要 Grease Pencil v3（Blender 4.3 及以上）；"
            f"当前是 {bpy.app.version_string}。"
        )
    return bpy.data.grease_pencils


def find_object(name: str = OBJECT_NAME):
    obj = bpy.data.objects.get(name)
    return obj if obj is not None and obj.type == "GREASEPENCIL" else None


def remove_object(name: str = OBJECT_NAME) -> bool:
    obj = bpy.data.objects.get(name)
    if obj is None:
        return False
    data = obj.data if obj.type == "GREASEPENCIL" else None
    bpy.data.objects.remove(obj, do_unlink=True)
    if data is not None and data.users == 0:
        _grease_pencils().remove(data)
    return True


def _ensure_object(collection=None):
    obj = bpy.data.objects.get(OBJECT_NAME)
    if obj is not None and obj.type != "GREASEPENCIL":
        # 别的类型占了这个名字：删掉重来
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
    return obj


def _ensure_attribute(drawing, name: str, dtype: str, count: int):
    """按当前点数取一条属性，长度 / 类型不对就重建。"""
    attribute = drawing.attributes.get(name)
    if (
        attribute is None
        or attribute.domain != _POINT
        or attribute.data_type != dtype
        or len(attribute.data) != count
    ):
        if attribute is not None:
            drawing.attributes.remove(attribute)
        attribute = drawing.attributes.new(name, dtype, _POINT)
    return attribute


def _colours(slots: np.ndarray, shades: np.ndarray) -> np.ndarray:
    """每个点的线性 RGBA：数字色 → 往纸色里退（越靠近表层越淡）。

    ``shade = 1`` 是纯正的数字色，``0.8`` 时混进 20% 的纸色。这样同一号颜色在图上会
    呈现"一簇簇的浓淡"，而不是一整片死板的同色 —— 参考实现也是这个意思。
    """
    count = len(slots)
    if not count:
        return np.zeros(0, np.float32)
    paper = np.asarray(palette.hex_to_linear(palette.SILK), dtype=np.float32)
    out = np.empty((count, 4), dtype=np.float32)
    cache: dict[int, np.ndarray] = {}
    unique = {int(value) for value in np.unique(slots)}
    for slot in unique:
        cache[slot] = np.asarray(palette.digit_colour_linear(slot), dtype=np.float32)
    fade = (1.0 - np.clip(shades, 0.0, 1.0)).astype(np.float32).reshape(-1, 1)
    for slot in unique:
        mask = slots == slot
        base = cache[slot]
        out[mask, 0:3] = base + (paper - base) * (fade[mask] * 0.55)
        out[mask, 3] = 1.0
    return out


def build(settings, collection=None) -> dict:
    """按当前参数重写 GP 对象，返回一份摘要（状态栏用）。"""
    data = settings.point_data()
    xy = data["xy"]
    obj = _ensure_object(collection=collection)
    layer_data = obj.data
    while layer_data.layers:
        layer_data.layers.remove(layer_data.layers[0])
    layer_data.materials.clear()

    count = len(xy)
    if not count:
        for key in (BOUNDS_MIN, BOUNDS_MAX):
            if key in obj:
                del obj[key]
        return {"object": obj, "count": 0, "places": 0, "check": settings.canonical()}

    layer = layer_data.layers.new(LAYER_NAME, set_active=True)
    # 实测：新图层默认 use_lights=True，没灯时颜色只剩四分之一亮度
    layer.use_lights = False
    layer.frames.new(1)
    drawing = layer.frames[0].drawing
    drawing.add_strokes([1] * count)
    # 结构性改动后旧指针失效，必须重新取（见 gen_lsystem/gp.py 的同款注释）
    drawing = layer.frames[0].drawing

    positions = np.zeros((count, 3), dtype=np.float32)
    positions[:, :2] = xy
    drawing.attributes[_POSITION].data.foreach_set("vector", positions.reshape(-1))
    _ensure_attribute(drawing, _RADIUS, "FLOAT", count).data.foreach_set(
        "value", np.full(count, float(settings.dot_radius), np.float32)
    )
    _ensure_attribute(drawing, _COLOUR, "FLOAT_COLOR", count).data.foreach_set(
        "color", _colours(data["slot"], data["shade"]).reshape(-1)
    )
    _ensure_attribute(drawing, _INDEX, "INT", count).data.foreach_set(
        "value", data["index"].astype(np.int32)
    )
    _ensure_attribute(drawing, _SLOT, "INT", count).data.foreach_set(
        "value", data["slot"].astype(np.int32)
    )

    low = positions.min(axis=0)
    high = positions.max(axis=0)
    obj[BOUNDS_MIN] = [float(value) for value in low]
    obj[BOUNDS_MAX] = [float(value) for value in high]

    return {
        "object": obj,
        "count": count,
        "places": data["places"],
        "check": settings.canonical(),
        "message": tfmt(
            "Places {places} · {count} points",
            places=data["places"],
            count=count,
        ),
    }
