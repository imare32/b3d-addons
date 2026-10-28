"""Grease Pencil v3 渲染后端：笔划集合 → 图层 / 逐帧重写。

为什么用 GP 而不是线框 mesh：弦线是"一笔一笔"的线，GP 自带笔划、顶点色和逐点不透明度，
和这套图案"每根弦一条墨线"的形态对得上（参考脚本走的是 mesh + 几何节点
``Curves to Grease Pencil``，等于绕远路回到同一个地方）。

写进去的数据（属性名都是 GP v3 内置的，实测于 Blender 5.2）：

* ``position`` —— ``FLOAT_VECTOR`` / POINT，世界坐标；
* ``radius`` —— ``FLOAT`` / POINT，笔划半径（世界单位）；
* ``opacity`` —— ``FLOAT`` / POINT，**逐点不透明度**（弦线族的淡出写在这里，实测它是
  唯一真正影响渲染透明度的通道）；
* ``vertex_color`` —— ``FLOAT_COLOR`` / POINT，线性 RGB，alpha 恒写 1（渲染时被忽略）。

逐帧重写很便宜：3000 根笔划（6000 点）清空 + 重写约 0.14 ms（实测），所以参数一改就
当场重算，不缓存几何 —— 这正是"生成式"（闭式、无状态）带来的好处。

除了几何，这里还管**光照与效果器**：

* ``obj.use_grease_pencil_lights = False`` —— GP 笔划**默认吃场景光**，环境项就是
  ``world.color``（世界背景的"视图显示"色，不是 Background 节点）。深底那条墨色
  (线性 0.024) 会把线色乘小四十倍，渲出来只剩满屏灰雾 —— 所以生成时直接关掉灯光，
  让顶点色原样落到画面上（实测线芯 0.905 就是写进去的墨色）。
* ``FX_GLOW`` —— GP v2 的那套 Effects 在 v3 里并没有消失（``shader_effects`` 仍然
  存在且参与渲染），所以发光用 GP 自带的效果器（:func:`apply_glow`）就够，不需要
  合成器。深底 + 亮线 + 辉光，才是这个图案该有的样子。
"""

from __future__ import annotations

import numpy as np

from .scene import StrokeSet

__all__ = [
    "GLOW_NAME",
    "OBJECT_NAME",
    "apply_glow",
    "clear",
    "find",
    "remove",
    "set_visible",
    "update",
    "write",
]

#: 生成出来的对象名（生成是就地重算，所以名字固定）。
OBJECT_NAME = "Tsubayaki"

#: 辉光效果器的名字（``shader_effects`` 是对象级的，一个对象只挂一份）。
GLOW_NAME = "Glow"

_ATTR_POSITION = "position"
_ATTR_RADIUS = "radius"
_ATTR_OPACITY = "opacity"
_ATTR_COLOR = "vertex_color"


def find():
    """当前数据里由本扩展生成的对象（没有就返回 ``None``）。"""
    import bpy

    obj = bpy.data.objects.get(OBJECT_NAME)
    if obj is None or obj.type != "GREASEPENCIL":
        return None
    return obj


def remove() -> bool:
    """删掉生成的对象（连带它自己那份 GP 数据）。"""
    import bpy

    obj = bpy.data.objects.get(OBJECT_NAME)
    if obj is None:
        return False
    data = obj.data if obj.type == "GREASEPENCIL" else None
    bpy.data.objects.remove(obj, do_unlink=True)
    if data is not None and data.users == 0:
        bpy.data.grease_pencils.remove(data)
    return True


def clear(obj) -> None:
    """清空已有图层与材质槽（重新长一遍时用）。"""
    data = obj.data
    while len(data.layers):
        data.layers.remove(data.layers[0])
    data.materials.clear()


def write(
    obj,
    stroke_sets: list[StrokeSet],
    *,
    frame: int,
    colour: tuple[float, float, float],
    depth_order: str = "3D",
) -> dict:
    """重建整个对象：图层全清后按 :data:`~gen_tsubayaki.scene.LAYER_ORDER` 的顺序写一遍。

    :param frame: 笔划所在的帧号。**只写一帧** —— GP 会把最后一张画保持到后面的帧，
    逐帧重算交给 :mod:`gen_tsubayaki.anim` 的处理器，而不是每帧存一张画（250 帧就是
    250 份几何，而且跳帧还得先把前面顺序算出来）。
    :param colour: 墨色（线性 RGB）。
    :param depth_order: ``'2D'``（共面图案，按存储顺序画）或 ``'3D'``（有 Z 波动时做遮挡）。
    """
    clear(obj)
    return _pump(
        obj,
        stroke_sets,
        frame=frame,
        colour=colour,
        depth_order=depth_order,
        replace=False,
    )


def update(
    obj,
    stroke_sets: list[StrokeSet],
    *,
    frame: int,
    colour: tuple[float, float, float],
    depth_order: str = "3D",
) -> dict:
    """把一帧的笔划写回已有图层（**拓扑可以变**：弦数随 ``n`` 增长而增加）。

    弦数是逐帧变的（``2n``），所以这里按需重建笔划；同拓扑时只改属性，省掉重建。
    """
    if obj is None:
        return {"frame": int(frame), "strokes": 0, "layers": 0}
    return _pump(
        obj,
        stroke_sets,
        frame=frame,
        colour=colour,
        depth_order=depth_order,
        replace=True,
    )


def set_visible(obj, layer: str, visible: bool) -> bool:
    """只切图层显隐（面板上的显隐开关，不动几何）。"""
    if obj is None:
        return False
    found = obj.data.layers.get(layer)
    if found is None:
        return False
    found.hide = not visible
    return True


def apply_glow(
    obj,
    *,
    enabled: bool,
    size: float = 24.0,
    samples: int = 32,
    opacity: float = 1.0,
    colour: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> bool:
    """挂 / 摘 GP 自带的辉光效果器（``FX_GLOW``），返回"现在挂着没有"。

    GP v2 的 Effects 面板（Blur / Colorize / Flip / Glow / Pixelate / Rim / Shadow /
    Swirl / Wave）在 GP v3 里**没有被删掉**：``object.shader_effects`` 仍然存在，而且
    确实参与 EEVEE 渲染（实测 5.2.2：开 / 关 Glow 逐像素比对，光晕半径与强度都随参数
    变化）——所以本扩展用它做发光，不去动合成器。

    :param size: 光晕半径，单位是**渲染像素**，不随分辨率缩放：2048 渲染下 40 是贴边的
        细光晕，128 渲染下 40 就是一大片。给太大而采样不足会把光晕摊薄成一层灰雾。
    :param colour: 光晕颜色（线性 RGB）；本扩展直接跟着墨色走。
    """
    if obj is None:
        return False

    effects = obj.shader_effects
    glow = next((effect for effect in effects if effect.name == GLOW_NAME), None)
    if not enabled:
        if glow is not None:
            effects.remove(glow)
        return False

    if glow is None:
        glow = effects.new(name=GLOW_NAME, type="FX_GLOW")
    glow.mode = "LUMINANCE"  # 按亮度挑笔划
    glow.threshold = 0.0  # 阈值 0 = 所有笔划都发光
    glow.size = (float(size), float(size))
    glow.samples = int(samples)
    glow.opacity = float(opacity)
    glow.blend_mode = "ADD"  # 叠加才有"发光"味；REGULAR 更像糊了一层
    glow.use_glow_under = False
    glow.glow_color = tuple(colour)
    glow.show_viewport = True
    glow.show_render = True
    return True


# --------------------------------------------------------------------------- #
# 内部
# --------------------------------------------------------------------------- #
def _pump(obj, stroke_sets, *, frame, colour, depth_order, replace) -> dict:
    obj.data.stroke_depth_order = depth_order
    # GP 笔划默认吃场景光（环境项 = world.color），深底会把线色乘成灰雾；
    # 关掉之后顶点色才是最终颜色，这是"深底亮线"能看见的前提。
    obj.use_grease_pencil_lights = False
    strokes = 0
    for stroke_set in stroke_sets:
        strokes += _write_set(
            obj, stroke_set, frame=frame, colour=colour, replace=replace
        )
    return {"frame": int(frame), "strokes": strokes, "layers": len(obj.data.layers)}


def _write_set(obj, stroke_set: StrokeSet, *, frame: int, colour, replace: bool) -> int:
    data = obj.data
    layer = data.layers.get(stroke_set.layer)
    if layer is None:
        layer = data.layers.new(stroke_set.layer, set_active=True)
        replace = False

    drawing = _drawing(layer, frame)
    if drawing is None:  # 理论上不会发生：上面刚建过图层
        return 0

    counts = np.asarray(stroke_set.counts, dtype=np.int32)
    if replace and counts.size and len(drawing.strokes) != counts.size:
        # 弦数变了（n 长的每一帧都会变）→ 清空重建；同拓扑时只改属性。
        drawing.remove_strokes(indices=list(range(len(drawing.strokes))))
    if not len(drawing.strokes):
        drawing.add_strokes(counts.tolist())
        drawing.set_types(type="POLY")

    total = int(counts.sum())
    drawing.attributes[_ATTR_POSITION].data.foreach_set(
        "vector", np.asarray(stroke_set.points, dtype=np.float32).ravel()
    )
    _scalar(drawing, _ATTR_RADIUS, total, float(stroke_set.radius))
    # 逐点不透明度：一根弦两个点取同一个值（参考的 Color Ramp 逐弦着色）。
    _scalar(drawing, _ATTR_OPACITY, total, np.repeat(stroke_set.alpha, 2))
    _colour(drawing, total, colour)

    drawing.tag_positions_changed()
    return int(counts.size)


def _drawing(layer, frame: int):
    """图层里的那一张画：没有就新建（**只留一帧**，逐帧重写同一张）。"""
    if layer is None:
        return None
    if len(layer.frames):
        return layer.frames[0].drawing
    return layer.frames.new(int(frame)).drawing


def _scalar(drawing, name: str, total: int, value) -> None:
    """写一个逐点标量属性（``radius`` / ``opacity``）：标量或已按点展开的数组。"""
    attr = _attr(drawing, name, "FLOAT")
    if attr is None:
        return
    if np.isscalar(value):
        buffer = np.full(total, float(value), dtype=np.float32)
    else:
        buffer = np.asarray(value, dtype=np.float32)
        if buffer.size != total:
            fill = float(buffer.flat[0]) if buffer.size else 1.0
            buffer = np.full(total, fill, dtype=np.float32)
    attr.data.foreach_set("value", buffer)


def _colour(drawing, total: int, colour) -> None:
    """顶点色：线性 RGB + alpha 恒 1（透明度交给 ``opacity``）。"""
    attr = _attr(drawing, _ATTR_COLOR, "FLOAT_COLOR")
    if attr is None:
        return
    rgb = np.asarray(colour, dtype=np.float32).reshape(3)
    rgba = np.empty(total * 4, dtype=np.float32)
    rgba[0::4] = rgb[0]
    rgba[1::4] = rgb[1]
    rgba[2::4] = rgb[2]
    rgba[3::4] = 1.0
    attr.data.foreach_set("color", rgba)


def _attr(drawing, name: str, data_type: str):
    attr = drawing.attributes.get(name)
    if attr is None:
        attr = drawing.attributes.new(name, data_type, "POINT")
    return attr
