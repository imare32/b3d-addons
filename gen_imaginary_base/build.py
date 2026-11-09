"""写 Grease Pencil：点集 → 一个 GP 对象。

两种输出形态，由 ``scene.imaginarybase.output_mode`` 决定：

``DOTS`` —— 圆点（默认）
    每个整数一条**单顶点笔划**。GP 的笔划是"折线"：把一万个点塞进一条笔划，
    GP 会老老实实把它们连成一万段线，画出来是一张蛛网。要把点画成点，只能一点一笔
    —— 实测 Blender 5.2 里单顶点笔划确实渲染成圆点（半径由 ``radius`` 决定）。

``CELLS`` —— 实心格
    每个整数一条**闭合笔划**：``I`` 域 4 点方格、``W`` 域 6 点六边形，形状正好是
    各自点阵的胞腔（见 :func:`ring.cell_polygon`），所以铺开来是一张严丝合缝的瓦片图。
    填充靠两条 **CURVE（逐笔划）域**属性 —— ``fill_id`` 非 0 才填充、``fill_color``
    给填充色；描边用 ``hide_stroke`` 逐笔划关掉，于是产物**只有填充、没有描边**，
    与参考项目的画布形态一致。

    这两条属性的语义是 ``gen_zellij`` 在 5.2.2 上摸出来的（那边连材质都不建），
    这里照搬并复核过：不建材质、``layer.use_lights = False``，填充色所见即所得。

属性（``POINT`` 域，和仓库其它 ``gen_*`` 的写法一致）::

    position      float3      点坐标（GP 自带）
    radius        float       点半径 = dot_radius（实心格模式下是一条极细线，见下）
    vertex_color  color       线色（**线性**存）
    index         int         这个点是哪一号整数（0 .. |D|^places-1，地址本身）
    digit_slot    int         它用哪一号颜色（= 最高位 / 指定位的编号）

``CELLS`` 模式额外的 ``CURVE`` 域::

    fill_id       int         逐笔划非 0 → 这一格要填充
    fill_color    color       填充色（与 vertex_color 同色）
    hide_stroke   bool        恒为 True：只留填充，不要描边
    cyclic        bool        恒为 True：闭合，填充才有边界

``index`` 与 ``digit_slot`` 是**数据接口**：几何算完就写死在那里，之后想按位筛选、
做动画、或换成几何节点驱动，都不用重算点集。实心格模式下它们按顶点重复（一格的
每个顶点都记着"我属于哪一号整数"），所以按属性筛一格仍然是整格。
"""

from __future__ import annotations

import bpy
import numpy as np

from . import palette, ring
from .i18n import tfmt

__all__ = [
    "CELL_STROKE_RADIUS",
    "LAYER_NAME",
    "OBJECT_NAME",
    "build",
    "colours_for",
    "find_object",
    "remove_object",
    "set_curve_attr",
    "set_point_attr",
]

#: 生成对象名（固定名，反复生成不会堆一地对象）。
OBJECT_NAME = "Imaginary Base"
LAYER_NAME = "Points"
#: 包围盒记在对象上的自定义属性名（「场景预设」取景用）。
BOUNDS_MIN = "gen_imaginary_base_bounds_min"
BOUNDS_MAX = "gen_imaginary_base_bounds_max"

#: 实心格模式下笔划的半径。描边已经被 ``hide_stroke`` 关掉了，这个值只是为了让
#: 数据自洽 —— 万一有人把 ``hide_stroke`` 改回 False，得到的是一条发丝细的轮廓线，
#: 而不是一整团糊掉形状的粗边。
CELL_STROKE_RADIUS = 0.0015

_POSITION = "position"
_RADIUS = "radius"
_COLOUR = "vertex_color"
_INDEX = "index"
_SLOT = "digit_slot"
_FILL_ID = "fill_id"
_FILL_COLOUR = "fill_color"
_HIDE_STROKE = "hide_stroke"
_CYCLIC = "cyclic"
_POINT = "POINT"
_CURVE = "CURVE"


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


def _ensure_attribute(drawing, name: str, dtype: str, count: int, domain: str = _POINT):
    """按当前点数取一条属性，长度 / 类型 / 域不对就重建。"""
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


def set_point_attr(drawing, name, dtype, key, values, count) -> None:
    """写一条 ``POINT`` 域属性（``count`` 是点数，不是整数个数）。"""
    _ensure_attribute(drawing, name, dtype, count).data.foreach_set(key, values)


def set_curve_attr(drawing, name, dtype, key, values) -> None:
    """写一条 ``CURVE``（逐笔划）域属性。``values`` 的长度必须等于笔划数。"""
    count = len(values)
    _ensure_attribute(drawing, name, dtype, count, _CURVE).data.foreach_set(key, values)


def colours_for(slots: np.ndarray, shades: np.ndarray) -> np.ndarray:
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


# ---------------------------------------------------------------------------
# 两种形态
# ---------------------------------------------------------------------------


def _write_dots(layer, settings, data, colours) -> np.ndarray:
    """圆点：一个整数一条单顶点笔划。返回所有顶点的坐标 ``(n, 3)``。"""
    count = len(data["xy"])
    layer.frames[0].drawing.add_strokes([1] * count)
    # 结构性改动后旧指针失效，必须重新取（见 gen_lsystem/gp.py 的同款注释）
    drawing = layer.frames[0].drawing

    positions = np.zeros((count, 3), dtype=np.float32)
    positions[:, :2] = data["xy"]
    drawing.attributes[_POSITION].data.foreach_set("vector", positions.reshape(-1))

    set_point_attr(
        drawing,
        _RADIUS,
        "FLOAT",
        "value",
        np.full(count, float(settings.dot_radius), np.float32),
        count,
    )
    set_point_attr(drawing, _COLOUR, "FLOAT_COLOR", "color", colours.reshape(-1), count)
    set_point_attr(
        drawing, _INDEX, "INT", "value", data["index"].astype(np.int32), count
    )
    set_point_attr(
        drawing, _SLOT, "INT", "value", data["slot"].astype(np.int32), count
    )
    return positions


def _write_cells(layer, settings, data, colours) -> np.ndarray:
    """实心格：一个整数一条闭合笔划（方格 / 六边形），只填不描。返回所有顶点的坐标。"""
    xy = data["xy"]
    count = len(xy)
    corners = ring.cell_polygon(settings.domain, float(settings.cell_gap))
    sides = len(corners)

    drawing = layer.frames[0].drawing
    drawing.add_strokes([sides] * count)
    # 同上：add_strokes 之后必须重新取 drawing
    drawing = layer.frames[0].drawing

    # (count, sides, 2) → (count * sides, 3)：每格的每个角点都平移到位
    cell = xy[:, None, :] + corners[None, :, :]
    positions = np.zeros((count * sides, 3), dtype=np.float32)
    positions[:, :2] = cell.reshape(-1, 2)
    drawing.attributes[_POSITION].data.foreach_set("vector", positions.reshape(-1))

    total = count * sides
    set_point_attr(
        drawing,
        _RADIUS,
        "FLOAT",
        "value",
        np.full(total, CELL_STROKE_RADIUS, np.float32),
        total,
    )
    # 逐顶点的属性按整格重复：一格里的每个角点都记着"我属于哪一号整数"
    set_point_attr(
        drawing,
        _COLOUR,
        "FLOAT_COLOR",
        "color",
        np.repeat(colours, sides, axis=0).reshape(-1),
        total,
    )
    set_point_attr(
        drawing,
        _INDEX,
        "INT",
        "value",
        np.repeat(data["index"].astype(np.int32), sides),
        total,
    )
    set_point_attr(
        drawing,
        _SLOT,
        "INT",
        "value",
        np.repeat(data["slot"].astype(np.int32), sides),
        total,
    )

    # --- 只填不描 -------------------------------------------------------
    # fill_id 逐笔划非 0（每条笔划自己的 id，相邻格不会被合并成一个连通区域），
    # fill_color 与顶点色同色；hide_stroke 关掉描边 —— 于是产物只有填充。
    set_curve_attr(
        drawing, _FILL_ID, "INT", "value", np.arange(1, count + 1, dtype=np.int32)
    )
    set_curve_attr(
        drawing, _FILL_COLOUR, "FLOAT_COLOR", "color", colours.reshape(-1)
    )
    set_curve_attr(
        drawing, _HIDE_STROKE, "BOOLEAN", "value", np.ones(count, dtype=bool)
    )
    set_curve_attr(drawing, _CYCLIC, "BOOLEAN", "value", np.ones(count, dtype=bool))
    return positions


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def build(settings, collection=None) -> dict:
    """按当前参数重写 GP 对象，返回一份摘要（状态栏用）。"""
    data = settings.point_data()
    obj = _ensure_object(collection=collection)
    layer_data = obj.data
    while layer_data.layers:
        layer_data.layers.remove(layer_data.layers[0])
    layer_data.materials.clear()

    count = len(data["xy"])
    if not count:
        for key in (BOUNDS_MIN, BOUNDS_MAX):
            if key in obj:
                del obj[key]
        return {"object": obj, "count": 0, "places": 0, "check": settings.canonical()}

    mode = settings.output_mode
    layer = layer_data.layers.new(LAYER_NAME, set_active=True)
    # 实测：新图层默认 use_lights=True，没灯时颜色只剩四分之一亮度
    layer.use_lights = False
    layer.frames.new(1)

    colours = colours_for(data["slot"], data["shade"])
    if mode == "CELLS":
        positions = _write_cells(layer, settings, data, colours)
    else:
        positions = _write_dots(layer, settings, data, colours)

    low = positions.min(axis=0)
    high = positions.max(axis=0)
    obj[BOUNDS_MIN] = [float(value) for value in low]
    obj[BOUNDS_MAX] = [float(value) for value in high]

    return {
        "object": obj,
        "count": count,
        "places": data["places"],
        "mode": mode,
        "check": settings.canonical(),
        "message": tfmt(
            "Places {places} · {count} cells"
            if mode == "CELLS"
            else "Places {places} · {count} points",
            places=data["places"],
            count=count,
        ),
    }
