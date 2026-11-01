"""生成入口：算 Teragon → 写成曲线对象 → 把参数记回对象。

手动「生成」与自动更新（:mod:`live`）走**同一条**路，所以面板上看到的预览与场景里的
对象永远是同一份算法的结果（预览只是用 ``gpu`` 把它画在视口里，不落数据块）。

三种输出模式（沿用参考实现的叫法）：

``EACH_LEVEL``
    每一级一个对象。等级 ``i`` 的曲线按 ``段数 ** (最高级 - i)`` 线性细分，于是**所有等级的
    顶点数相同** —— 这是给形态键铺路，也让"逐级堆叠"的图能一眼对上。
``LAST_LEVEL``
    只留最高级一个对象，点数最少。
``SHAPE_KEYS``
    一个对象 + 每级一个形态键，关闭相对模式，拖 ``Evaluation Time`` 就是"生长"动画。
    **曲线形态键只存顶点坐标、不存手柄**，切线来自基形曲线，所以基形要显式换成 ``AUTO``
    柄（见 :func:`curves.set_handle_type`），否则平滑样条的每个形态都长着"直线切线"。

对象命名固定（``Fractal L{等级}``），重复生成会**就地更新**同名对象的数据块，不会每点一次
就多出一堆对象；换了等级后多出来的旧对象会被清掉（只清本插件打的标）。
"""

from __future__ import annotations

import time

import bpy
import numpy as np

from . import curves
from .lattice import FractalGenerator, teragon
from .props import store_design

__all__ = [
    "LEVEL_KEY",
    "MODES",
    "OWNER_KEY",
    "delete_generated",
    "generated",
    "rebuild",
]

#: 生成物上的标记：哪个插件、哪一级（删旧对象只认这个标）。
OWNER_KEY = "gen_fractal_family_owner"
LEVEL_KEY = "gen_fractal_family_level"

#: 三种输出模式（与算子的 ``mode`` 枚举一致）。
MODES = ("EACH_LEVEL", "LAST_LEVEL", "SHAPE_KEYS")

#: 对象名前缀。
NAME_PREFIX = "Fractal L"


def _target_collection(collection=None):
    """新对象链到哪：参数给的 > 当前激活的 collection > 场景根。"""
    return collection or bpy.context.collection or bpy.context.scene.collection


def generated() -> list:
    """本插件生成过的所有对象。"""
    return [
        obj for obj in bpy.data.objects
        if obj.get(OWNER_KEY) is not None and obj.type in {"CURVE", "MESH"}
    ]


def delete_generated() -> int:
    """删掉本插件生成的所有对象（连带数据块），返回删了几个。"""
    doomed = generated()
    for obj in doomed:
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0:
            if isinstance(data, bpy.types.Curve):
                bpy.data.curves.remove(data)
            elif isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
    return len(doomed)


def initiator_points(settings):
    """初始折线的点列 ``(numpy (n, 2), closed)``；没指定曲线时返回 ``(None, False)``。"""
    curve = settings.initiator_curve
    if curve is None or not getattr(curve, "splines", None) or not curve.splines:
        return None, False
    spline = curve.splines[0]
    points = []
    if spline.type == "BEZIER" and len(spline.bezier_points) > 1:
        points = [(point.co[0], point.co[1]) for point in spline.bezier_points]
    elif len(spline.points) > 1:
        points = [(point.co[0], point.co[1]) for point in spline.points]
    if len(points) < 2:
        return None, False
    if settings.initiator_reverse:
        points.reverse()
    return np.asarray(points, dtype=float), bool(spline.use_cyclic_u)


def _ensure_object(name: str, curve, collection=None):
    """同名对象就地换数据块（保留对象上的动画 / 修改器 / 自定义属性）。"""
    obj = bpy.data.objects.get(name)
    if obj is not None and obj.type == "CURVE":
        previous = obj.data
        obj.data = curve
        if previous is not None and previous.users == 0:
            bpy.data.curves.remove(previous)
        return obj
    if obj is not None:
        # 被别的类型占了名字：删掉重来
        bpy.data.objects.remove(obj, do_unlink=True)
    obj = bpy.data.objects.new(name, curve)
    _target_collection(collection).objects.link(obj)
    return obj


def _make_curve(points, name: str, num_segments: int, closed: bool, spline_type: str):
    if spline_type == "SMOOTH":
        return curves.create_curve_smooth(points, name=name, num_segments=num_segments, closed=closed)
    return curves.create_curve_poly(points, name=name, num_segments=num_segments, closed=closed)


def _build_shape_keys(settings, lines, closed: bool, collection=None):
    """一个对象 + 每级一个形态键；关闭相对模式，靠 Evaluation Time 驱动。"""
    level = int(settings.level)
    subdivision = max(2, len(settings.items))
    spline_type = settings.spline_type

    curve_list = []
    for index, line in enumerate(lines):
        num_segments = subdivision ** max(0, level - index)
        curve_list.append(_make_curve(
            line, f"{NAME_PREFIX}{index}", num_segments, closed, spline_type
        ))

    obj = _ensure_object(f"{NAME_PREFIX}{level}", curve_list[0], collection)
    if spline_type == "SMOOTH":
        # **形态键的切线只能来自基形**（键里只存 co，没有手柄）：基形必须是 AUTO 柄，
        # 让 Blender 按插值后的 co 重算切线。默认的细分曲线是 FREE 柄、指向第 0 级的
        # 直线方向，直接拿来当基形的话，平滑分形每一帧都长着"直线切线"，形状全错。
        curves.set_handle_type(obj.data, "AUTO")
    try:
        obj.shape_key_add(name="Basis")
    except (RuntimeError, TypeError) as exc:
        for curve in curve_list:
            if curve.users == 0:
                bpy.data.curves.remove(curve)
        raise ValueError(f"这个对象类型不支持形态键：{exc}") from exc
    if obj.data.shape_keys is not None:
        # 关掉相对模式：形态键就变成"按 Evaluation Time 在同一串形状之间插值"
        obj.data.shape_keys.use_relative = False

    for index, curve in enumerate(curve_list):
        if index == 0:
            continue
        key = obj.shape_key_add(name=f"Level {index}")
        if key is None or not len(curve.splines):
            continue
        source = curve.splines[0].bezier_points
        # 曲线形态键逐点只存 co（手柄由基形与手柄类型决定），所以只管坐标
        for point_index, point in enumerate(source):
            if point_index >= len(key.data):
                break
            key.data[point_index].co = point.co
    # 中间曲线用完就丢：形态键已经把点抄走了
    for curve in curve_list:
        if curve.users == 0:
            bpy.data.curves.remove(curve)
    return [obj]


def rebuild(settings, *, mode: str | None = None, collection=None) -> dict:
    """按参数重算并写成曲线对象。

    :param mode: 输出模式；``None`` = 沿用上一次的模式（``settings.output_mode``）。
    :returns: ``{"objects", "points", "level", "ms"}``。
    :raises ValueError: 基因无效（段数不足 / 总向量为零 / 有零长度段）。
    """
    started = time.perf_counter()
    elements = settings.elements()
    if len(elements) < 2:
        raise ValueError("基因至少要两段")
    if any(element.norm == 0 for element, _flags in elements):
        raise ValueError("基因里有零长度段（相邻节点重合）")
    if not settings.is_valid():
        raise ValueError("基因无效：总向量为零（分形无处展开）")

    generator = FractalGenerator(elements)
    level = max(1, int(settings.level))
    initiator, closed = initiator_points(settings)
    lines = teragon(generator, level, initiator, closed)

    mode = mode or getattr(settings, "output_mode", "LAST_LEVEL")
    if mode not in MODES:
        mode = "LAST_LEVEL"
    settings.output_mode = mode

    if mode == "SHAPE_KEYS":
        objects = _build_shape_keys(settings, lines, closed, collection)
    elif mode == "EACH_LEVEL":
        subdivision = max(2, len(elements))
        objects = []
        for index, line in enumerate(lines):
            num_segments = subdivision ** max(0, level - index)
            curve = _make_curve(line, f"{NAME_PREFIX}{index}", num_segments, closed,
                                settings.spline_type)
            objects.append(_ensure_object(f"{NAME_PREFIX}{index}", curve, collection))
    else:
        curve = _make_curve(lines[-1], f"{NAME_PREFIX}{level}", 1, closed, settings.spline_type)
        objects = [_ensure_object(f"{NAME_PREFIX}{level}", curve, collection)]

    # 打标
    for obj in objects:
        obj[OWNER_KEY] = mode
        obj[LEVEL_KEY] = level
        store_design(obj, settings)

    # 清掉本次没生成的旧对象（等级调小之后留下来的那些）
    keep = {obj.name for obj in objects}
    for obj in generated():
        if obj.name in keep:
            continue
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if isinstance(data, bpy.types.Curve) and data.users == 0:
            bpy.data.curves.remove(data)

    for obj in objects:
        obj.select_set(True)
    if objects:
        bpy.context.view_layer.objects.active = objects[0]

    settings.last_points = int(len(lines[-1]))
    settings.last_level = level
    settings.last_ms = (time.perf_counter() - started) * 1000.0
    settings.dirty = False
    return {
        "objects": objects,
        "points": int(len(lines[-1])),
        "level": level,
        "ms": settings.last_ms,
    }
