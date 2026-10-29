"""Grease Pencil v3 后端：可见片段 → 一个 GP 对象（每截一条笔划）。

写进 drawing 的属性::

    POINT  position      float3  点坐标（GP 自带）
    POINT  radius        float   线宽（= 带宽的一半）
    POINT  opacity       float   不透明度
    POINT  vertex_color  color   线色（**线性**存，见 :func:`_linear_rgba`）
    POINT  strand_index  int     这个点来自第几条股线
    CURVE  piece_index   int     这是那条股线上的第几截（切过几刀就有几截；补片是 -1）
    CURVE  fill_id       int     非 0 = 这块要填充（只有补片用）
    CURVE  fill_color    color   填充色（跟线色同色，所以补片和本体看起来是一根）

**补片（平贴端头的缺口）**：只在 ``fill_id != 0`` 的笔划上填充，并且置
``stroke.hide_stroke = True`` —— 实测（同一张图里放一块只填的、一块又描又填的四边形对照）
这样就是**只填不描**。半径写 0，免得轮廓线上多出一条边。

**颜色不用材质**：``vertex_color`` 就够，:func:`build_object` 还会 ``materials.clear()``。
所以 3D 视图 Solid 模式得把「着色 → 颜色」切到 Attribute 才看得见 —— 生成算子会顺手切一次
（``viewport``，与 ``gen_torus_knot`` 逐字相同）。

**实测坑（5.2.2）**：

* 新图层 ``use_lights`` 默认 ``True``，没有灯时颜色只剩约四分之一亮度 —— 本后端一律建
  **无光照层**；
* 不给 ``miter_angle`` 属性 —— 没有它就按**圆角**连接拐角；
* ``start_cap`` / ``end_cap`` 是 CURVE 域的字节属性：``0`` = 圆头（默认）、``1`` = 平头。
  平头的实测长度正好等于两端点距离（0.9987 ≈ 1.0），圆头会多出两头各一个半径
  （半径 0.05 时 1.0982）—— 所以「平贴端头」模式必须全平头；
* ``add_strokes`` / ``remove_strokes`` 是**结构性**改动：改完手里那个 drawing 指针就失效
  （再往上 ``foreach_set`` 会报 ``internal error setting the array``），必须重新取一次；
* ``foreach_set`` 的长度对不上时 Blender 只报一句 ``internal error setting the array``，
  不说是哪条属性 —— 所以每次写之前按领域长度对一遍（``_ensure``）；
* ``layer.frames.remove()`` 收的是**帧号**（int），不是 ``GreasePencilFrame`` 对象。
"""

from __future__ import annotations

import bpy
import numpy as np

from .palette import srgb_to_linear

__all__ = [
    "LAYER_NAME",
    "OBJECT_NAME",
    "build_object",
    "clear_object",
    "create_object",
    "find_object",
    "remove_object",
    "set_colour",
    "set_layer_radius",
    "set_opacity",
]

#: 生成结果固定写进这一个对象，反复生成不会堆一地对象。
OBJECT_NAME = "Interlace"
#: 图层名。
LAYER_NAME = "Lace"

_ATTR_POSITION = "position"
_ATTR_RADIUS = "radius"
_ATTR_OPACITY = "opacity"
_ATTR_VERTEX_COLOR = "vertex_color"
_ATTR_STRAND = "strand_index"
_ATTR_PIECE = "piece_index"
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
        bpy.data.objects.remove(obj, do_unlink=True)
        obj = None

    if obj is None:
        datas = _grease_pencils()
        data = datas.get(name) or datas.new(name)
        obj = bpy.data.objects.new(name, data)
        target = collection or bpy.context.collection or bpy.context.scene.collection
        target.objects.link(obj)
    data = obj.data
    data.materials.clear()
    return obj


def create_object(name: str, collection=None):
    """新建一个空的 GP 对象（数据块同名），进当前激活的 collection。"""
    data = _grease_pencils().new(name)
    obj = bpy.data.objects.new(name, data)
    target = collection or bpy.context.collection or bpy.context.scene.collection
    target.objects.link(obj)
    return obj


def remove_object(name: str = OBJECT_NAME) -> bool:
    obj = bpy.data.objects.get(name)
    if obj is None:
        return False
    data = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if isinstance(data, bpy.types.GreasePencil) and data.users == 0:
        _grease_pencils().remove(data)
    return True


def clear_object(name: str = OBJECT_NAME, layer_name: str = LAYER_NAME) -> int:
    """把生成对象里的笔划清空（不动对象本身），返回清掉几条。"""
    obj = find_object(name)
    if obj is None:
        return 0
    layer = obj.data.layers.get(layer_name)
    if layer is None or not len(layer.frames):
        return 0
    drawing = layer.frames[0].drawing
    total = len(drawing.strokes)
    if total:
        drawing.remove_strokes(indices=list(range(total)))
    return total


# --- 图层 / drawing -------------------------------------------------------


def _layer(obj, name: str = LAYER_NAME):
    data = getattr(obj, "data", None)
    return None if data is None else data.layers.get(name)


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
        float(srgb_to_linear(colour[0])),
        float(srgb_to_linear(colour[1])),
        float(srgb_to_linear(colour[2])),
        float(alpha),
    ]


def _tile(rgba, total: int) -> np.ndarray:
    return np.tile(np.asarray(rgba, dtype=np.float32), (total, 1)).reshape(-1)


def build_object(
    pieces,
    *,
    width: float = 0.02,
    colour=(0.0, 0.0, 0.0),
    opacity: float = 1.0,
    round_caps: bool = False,
    frame: int = 1,
    collection=None,
    name: str = OBJECT_NAME,
):
    """把可见片段写进生成对象，返回对象；``pieces`` 是 ``interlace.Piece`` 的序列。

    对象已存在时就地更新：图层复用（不重建，图层的显示设置与修改器都不动），只把笔划
    清掉重写。这样实时更新时不会每帧重建数据块，编辑模式里也不会打断编辑。
    """
    obj = _ensure_object(name, collection)

    drawing = _prepare(obj, int(frame))
    if len(drawing.strokes):
        drawing.remove_strokes(indices=list(range(len(drawing.strokes))))
        # 结构性改动之后旧指针失效，重新取一次（见模块说明）
        drawing = _prepare(obj, int(frame))

    counts = [max(2, len(piece.points)) for piece in pieces]
    if not counts:
        return obj
    drawing.add_strokes(counts)
    drawing = _prepare(obj, int(frame))
    total = int(sum(counts))

    positions = np.empty((total, 3), dtype=np.float32)
    strands = np.empty(total, dtype=np.int32)
    radii = np.empty(total, dtype=np.float32)
    cursor = 0
    half = float(width) * 0.5
    for piece in pieces:
        size = len(piece.points)
        positions[cursor : cursor + size] = piece.points
        strands[cursor : cursor + size] = int(piece.strand)
        # 补片只填充、不描边：半径写 0，免得轮廓上多出一条线（见下面的 hide_stroke）
        radii[cursor : cursor + size] = 0.0 if piece.fill else half
        cursor += size

    drawing.attributes[_ATTR_POSITION].data.foreach_set("vector", positions.reshape(-1))
    _write_points(drawing, _ATTR_RADIUS, "value", radii)
    _write_points(
        drawing, _ATTR_OPACITY, "value", np.full(total, float(opacity), np.float32)
    )
    _ensure(drawing, _ATTR_VERTEX_COLOR, "FLOAT_COLOR", _POINT).data.foreach_set(
        "color", _tile(_linear_rgba(colour), total)
    )
    _ensure(drawing, _ATTR_STRAND, "INT", _POINT).data.foreach_set("value", strands)
    _ensure(drawing, _ATTR_PIECE, "INT", _CURVE).data.foreach_set(
        "value", np.asarray([int(piece.piece) for piece in pieces], dtype=np.int32)
    )

    # 填充：补片（``piece.fill``）走 ``fill_id`` + ``fill_color``，其余填 0 = 不填
    rgba = _linear_rgba(colour, float(opacity))
    _ensure(drawing, _ATTR_FILL_ID, "INT", _CURVE).data.foreach_set(
        "value",
        np.asarray(
            [index + 1 if piece.fill else 0 for index, piece in enumerate(pieces)],
            dtype=np.int32,
        ),
    )
    _ensure(drawing, _ATTR_FILL_COLOR, "FLOAT_COLOR", _CURVE).data.foreach_set(
        "color", _tile(rgba, len(pieces))
    )

    # 端帽：CURVE 域的 start_cap / end_cap 是字节属性，实测 0 = 圆头、1 = 平头。
    # 切口用平头（一刀切断的感觉），圆头会往外多画半个带宽 —— 那半边由
    # interlace.cut_pieces 的 round_caps 参数补进切口里。
    cap = 0 if round_caps else 1
    for stroke, piece in zip(drawing.strokes, pieces):
        stroke.cyclic = bool(piece.cyclic)
        stroke.start_cap = cap
        stroke.end_cap = cap
        # 补片只填不描（实测：hide_stroke + fill_id 生效，关掉描边后只剩填充色）
        stroke.hide_stroke = bool(piece.fill)
    return obj


# --- 就地改（不重建几何） -------------------------------------------------


def set_layer_radius(obj, width: float) -> bool:
    """就地改线宽：只重写 ``radius`` 属性。"""
    layer = _layer(obj)
    if layer is None or not len(layer.frames):
        return False
    drawing = layer.frames[0].drawing
    if _ATTR_RADIUS not in drawing.attributes:
        return False
    attribute = drawing.attributes[_ATTR_RADIUS]
    half = float(width) * 0.5
    attribute.data.foreach_set("value", np.full(len(attribute.data), half, np.float32))
    return True


def set_colour(obj, colour) -> bool:
    """就地改线色：只重写 ``vertex_color``（和补片的 ``fill_color``），几何都不动。"""
    layer = _layer(obj)
    if layer is None or not len(layer.frames):
        return False
    drawing = layer.frames[0].drawing
    if _ATTR_VERTEX_COLOR not in drawing.attributes:
        return False
    rgba = _linear_rgba(colour)
    attribute = drawing.attributes[_ATTR_VERTEX_COLOR]
    attribute.data.foreach_set("color", _tile(rgba, len(attribute.data)))
    fill = drawing.attributes.get(_ATTR_FILL_COLOR)
    if fill is not None:
        # 补片是填充，颜色得跟着走一遍，否则改色后补片还是旧色
        fill.data.foreach_set("color", _tile(rgba, len(fill.data)))
    return True


def set_opacity(obj, opacity: float) -> bool:
    """就地改不透明度：只重写 ``opacity`` 属性。"""
    layer = _layer(obj)
    if layer is None or not len(layer.frames):
        return False
    drawing = layer.frames[0].drawing
    if _ATTR_OPACITY not in drawing.attributes:
        return False
    attribute = drawing.attributes[_ATTR_OPACITY]
    attribute.data.foreach_set(
        "value", np.full(len(attribute.data), float(opacity), np.float32)
    )
    return True
