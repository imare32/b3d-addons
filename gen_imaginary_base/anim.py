"""把 :mod:`steps` 的时间轴写成一个**逐帧的 Grease Pencil 对象**。

一帧 = 一张 ``layer.frames`` 里的 drawing，所以**每帧的点数可以不同** —— 这正是这个
动画需要的东西：生育那一步点数翻 ``|D|`` 倍。实测（5.2.2）``add_strokes`` 之后必须重新
取 ``frame.drawing``，否则 ``foreach_set`` 报 internal error；帧是按帧号索引的，取回时
要按 ``frame_number`` 找，不能用 ``frames[i]``（那是集合下标，不是帧号）。

形态与静态输出一致（``output_mode``）：

* ``DOTS``  —— 每个点一条单顶点笔划，半径 = 点半径；
* ``CELLS`` —— 每个点一块实心格（方格 / 正六边形），只填不描。

**格子不跟着转**：格子的身份是"点阵的胞腔"，点阵本身不会被乘底数改变（底数是环元素），
转的是点集的排布。所以整个动画里格子始终轴对齐，最后一帧与静态输出逐点重合。
"""

from __future__ import annotations

import bpy
import numpy as np

from . import build, ring, steps
from .i18n import tfmt

__all__ = ["LAYER_NAME", "OBJECT_NAME", "build_animation", "find_object", "remove_object"]

#: 动画对象名。与静态对象分开，这样两者可以同时存在、各自开关。
OBJECT_NAME = "Imaginary Base Steps"
LAYER_NAME = "Steps"

#: 包围盒（所有帧的并集），供取景使用。
BOUNDS_MIN = "gen_imaginary_base_steps_min"
BOUNDS_MAX = "gen_imaginary_base_steps_max"
#: 记一下生成了多少帧 / 多少级，面板与状态栏读它。
FRAME_COUNT = "gen_imaginary_base_steps_frames"
LEVEL_COUNT = "gen_imaginary_base_steps_levels"


def _grease_pencils():
    collection = getattr(bpy.data, "grease_pencils_v3", None)
    if collection is not None:
        return collection
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


def _frame(layer, number: int):
    """按**帧号**取帧（``layer.frames[i]`` 是集合下标，不是帧号，别混用）。"""
    for frame in layer.frames:
        if frame.frame_number == number:
            return frame
    return None


# ---------------------------------------------------------------------------
# 写一帧
# ---------------------------------------------------------------------------


def _write_dots(layer, record, settings, colours) -> None:
    count = len(record.xy)
    layer.frames.new(record.number)
    frame = _frame(layer, record.number)
    frame.drawing.add_strokes([1] * count)
    drawing = _frame(layer, record.number).drawing

    positions = np.zeros((count, 3), dtype=np.float32)
    positions[:, :2] = record.xy
    drawing.attributes["position"].data.foreach_set("vector", positions.reshape(-1))

    build.set_point_attr(
        drawing,
        "radius",
        "FLOAT",
        "value",
        np.full(count, float(settings.dot_radius), np.float32),
        count,
    )
    build.set_point_attr(
        drawing, "vertex_color", "FLOAT_COLOR", "color", colours.reshape(-1), count
    )
    build.set_point_attr(
        drawing, "index", "INT", "value", record.index.astype(np.int32), count
    )
    build.set_point_attr(
        drawing, "digit_slot", "INT", "value", record.slot.astype(np.int32), count
    )


def _write_cells(layer, record, settings, colours, corners) -> None:
    count = len(record.xy)
    sides = len(corners)
    layer.frames.new(record.number)
    frame = _frame(layer, record.number)
    frame.drawing.add_strokes([sides] * count)
    drawing = _frame(layer, record.number).drawing

    cell = record.xy[:, None, :] + corners[None, :, :]
    total = count * sides
    positions = np.zeros((total, 3), dtype=np.float32)
    positions[:, :2] = cell.reshape(-1, 2)
    drawing.attributes["position"].data.foreach_set("vector", positions.reshape(-1))

    build.set_point_attr(
        drawing,
        "radius",
        "FLOAT",
        "value",
        np.full(total, build.CELL_STROKE_RADIUS, np.float32),
        total,
    )
    build.set_point_attr(
        drawing,
        "vertex_color",
        "FLOAT_COLOR",
        "color",
        np.repeat(colours, sides, axis=0).reshape(-1),
        total,
    )
    build.set_point_attr(
        drawing, "index", "INT", "value", np.repeat(record.index.astype(np.int32), sides), total
    )
    build.set_point_attr(
        drawing, "digit_slot", "INT", "value", np.repeat(record.slot.astype(np.int32), sides), total
    )

    build.set_curve_attr(
        drawing, "fill_id", "INT", "value", np.arange(1, count + 1, dtype=np.int32)
    )
    build.set_curve_attr(drawing, "fill_color", "FLOAT_COLOR", "color", colours.reshape(-1))
    build.set_curve_attr(
        drawing, "hide_stroke", "BOOLEAN", "value", np.ones(count, dtype=bool)
    )
    build.set_curve_attr(drawing, "cyclic", "BOOLEAN", "value", np.ones(count, dtype=bool))


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def build_animation(settings, collection=None) -> dict:
    """按当前参数生成动画对象，并把场景帧范围设成它。返回摘要。"""
    digits = settings.digit_list()
    plan = steps.make_plan(
        settings.domain,
        (settings.base_a, settings.base_b),
        digits,
        int(settings.places),
        per_phase=int(settings.anim_frames),
        hold=int(settings.anim_hold),
    )
    obj = _ensure_object(collection=collection)
    layer_data = obj.data
    while layer_data.layers:
        layer_data.layers.remove(layer_data.layers[0])
    layer_data.materials.clear()

    if plan["frames"] <= 0:
        for key in (BOUNDS_MIN, BOUNDS_MAX, FRAME_COUNT, LEVEL_COUNT):
            if key in obj:
                del obj[key]
        return {"object": obj, "frames": 0, "levels": 0, "plan": plan}

    layer = layer_data.layers.new(LAYER_NAME, set_active=True)
    layer.use_lights = False

    mode = settings.output_mode
    corners = ring.cell_polygon(settings.domain, float(settings.cell_gap))

    low = np.array([np.inf, np.inf], dtype=np.float64)
    high = np.array([-np.inf, -np.inf], dtype=np.float64)
    count = 0
    for record in steps.timeline(
        settings.domain,
        (settings.base_a, settings.base_b),
        digits,
        plan["levels"],
        per_phase=int(settings.anim_frames),
        hold=int(settings.anim_hold),
    ):
        colours = build.colours_for(record.slot, record.shade)
        if mode == "CELLS":
            _write_cells(layer, record, settings, colours, corners)
        else:
            _write_dots(layer, record, settings, colours)
        low = np.minimum(low, record.xy.min(axis=0))
        high = np.maximum(high, record.xy.max(axis=0))
        count = record.number

    pad = (
        float(np.abs(corners).max())
        if mode == "CELLS"
        else float(settings.dot_radius)
    )
    obj[BOUNDS_MIN] = [float(low[0] - pad), float(low[1] - pad), 0.0]
    obj[BOUNDS_MAX] = [float(high[0] + pad), float(high[1] + pad), 0.0]
    obj[FRAME_COUNT] = int(count)
    obj[LEVEL_COUNT] = int(plan["levels"])

    scene = bpy.context.scene
    if scene is not None:
        scene.frame_start = 1
        scene.frame_end = max(1, int(count))

    return {
        "object": obj,
        "frames": int(count),
        "levels": int(plan["levels"]),
        "plan": plan,
        "message": tfmt(
            "{frames} frames · {levels} levels",
            frames=int(count),
            levels=plan["levels"],
        ),
    }
