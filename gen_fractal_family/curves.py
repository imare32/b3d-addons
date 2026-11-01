"""曲线装配：把点列写成 Blender 的曲线数据块（折线 / 平滑两种），以及 Bezier 保形细分。

细分那段照搬参考实现（`stackoverflow.com/a/879213` 的做法）：把每段 Bezier 按参数切成
``n`` 小段、逐段重算控制点，形状一点不变，只是点数变多 —— 目的是让**各等级的曲线顶点数
相等**，形态键（shape keys）才能逐点对应。

为什么形态键需要"顶点数相等"：等级 0 只有 2 个点，等级 4 可能有 256 个。原版用
``每段细分 = 段数 ** (最高级 - 本级)`` 把每一级都补到同一个顶点数，于是"从初始线段长成
分形"这个动画就是逐点线性插值，不需要任何重拓扑。

**光有相等的顶点数还不够**：曲线形态键只存 ``co``，切线方向存在基形曲线的**手柄**里
（不是每个形态各存一份）。所以做形态键时基形必须用 ``AUTO`` 柄，让 Blender 按插值后的
``co`` 重算切线 —— 细分曲线自带的是 ``FREE`` 柄（形状定义用的），拿它当基形的话每个形态
都会沿用"第 0 级直线"的切线。见 :func:`set_handle_type`。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import bpy
import numpy as np
from mathutils import Vector

__all__ = [
    "create_curve_poly",
    "create_curve_smooth",
    "set_handle_type",
    "subdivide_curve",
]


def _vector(row) -> Vector:
    """``(x, y)`` / ``(x, y, z)`` → mathutils 向量。"""
    values = [float(component) for component in row]
    if len(values) == 2:
        return Vector((values[0], values[1], 0.0))
    return Vector(values[:3])


def set_handle_type(curve, handle_type: str = "AUTO") -> None:
    """把第一条样条所有 Bezier 点的左右手柄类型都设成 ``handle_type``。

    **形态键必须用 AUTO 柄**：曲线形态键只存 ``co``，切线方向来自基形曲线数据里的手柄
    （不是每个形态各自存一份）。:func:`subdivide_curve` 出来的点全是 ``FREE`` 柄、指向
    第 0 级的直线方向，于是形态键把 ``co`` 拖到哪里，切线都还指着那条直线 —— 平滑分形
    被画成一堆"直切线"的怪形状。设成 ``AUTO`` 后 Blender 会按插值后的 ``co`` 重算切线，
    形状键才真的平滑。
    """
    if not curve.splines:
        return
    for point in curve.splines[0].bezier_points:
        point.handle_left_type = handle_type
        point.handle_right_type = handle_type


def create_curve_poly(points, name: str = "Fractal", num_segments: int = 1, closed: bool = False):
    """折线曲线：每个点一个 Bezier 点，手柄设成 VECTOR（= 直线段）。

    :param points: 点列（``(n, 2)`` 数组或点列表）。
    :param num_segments: 每段再线性细分几段（形态键需要各级点数一致时用）。
    :param closed: 是否闭合。
    """
    array = np.asarray(points, dtype=float).reshape(-1, 2)
    num_segments = max(1, int(num_segments))

    subdivided = []
    if closed:
        for index in range(len(array)):
            start = array[index]
            end = array[(index + 1) % len(array)]
            for step in range(num_segments):
                subdivided.append(start + (end - start) * (step / num_segments))
    else:
        for index in range(len(array) - 1):
            start, end = array[index], array[index + 1]
            for step in range(num_segments):
                subdivided.append(start + (end - start) * (step / num_segments))
        if len(array):
            subdivided.append(array[-1])

    curve = bpy.data.curves.new(name=name, type="CURVE")
    spline = curve.splines.new("BEZIER")
    spline.bezier_points.add(len(subdivided) - 1)
    packed = list(itertools.chain.from_iterable(tuple(point) + (0.0,) for point in subdivided))
    spline.bezier_points.foreach_set("co", packed)
    for point in spline.bezier_points:
        point.handle_left_type = "VECTOR"
        point.handle_right_type = "VECTOR"
    spline.use_cyclic_u = bool(closed)
    return curve


def create_curve_smooth(
    points, name: str = "Fractal", num_segments: int = 1, closed: bool = False
):
    """平滑曲线：Bezier + AUTO 手柄，再按 ``num_segments`` 保形细分。"""
    array = np.asarray(points, dtype=float).reshape(-1, 2)
    curve = bpy.data.curves.new(name=name, type="CURVE")
    spline = curve.splines.new("BEZIER")
    spline.use_cyclic_u = bool(closed)
    spline.bezier_points.add(len(array) - 1)
    for index, point in enumerate(array):
        bezier_point = spline.bezier_points[index]
        bezier_point.co = _vector(point)
        bezier_point.handle_left_type = "AUTO"
        bezier_point.handle_right_type = "AUTO"
    subdivide_curve(curve, max(1, int(num_segments)))
    return curve


@dataclass
class _Segment:
    """一段三次 Bezier：起点、两个控制点、终点。"""

    start: Vector
    ctrl1: Vector
    ctrl2: Vector
    end: Vector

    def partial(self, t0: float = 0.0, t1: float = 1.0) -> "_Segment":
        """取 ``[t0, t1]`` 那一小段（de Casteljau 展开后的闭式）。"""
        p0, p1, p2, p3 = self.start, self.ctrl1, self.ctrl2, self.end
        if t0 > t1:
            t0, t1 = t1, t0
        if p0 == p1 and p2 == p3:  # 已经是直线段：直接线性插值
            return _Segment(
                p0 * (1 - t0) + p2 * t0, p0 * (1 - t0) + p2 * t0,
                p0 * (1 - t1) + p2 * t1, p0 * (1 - t1) + p2 * t1,
            )
        u0, u1 = 1.0 - t0, 1.0 - t1
        qa = p0 * (u0 * u0) + p1 * (2 * t0 * u0) + p2 * (t0 * t0)
        qb = p0 * (u1 * u1) + p1 * (2 * t1 * u1) + p2 * (t1 * t1)
        qc = p1 * (u0 * u0) + p2 * (2 * t0 * u0) + p3 * (t0 * t0)
        qd = p1 * (u1 * u1) + p2 * (2 * t1 * u1) + p3 * (t1 * t1)
        return _Segment(
            qa * u0 + qc * t0,
            qa * u1 + qc * t1,
            qb * u0 + qd * t0,
            qb * u1 + qd * t1,
        )


def _spline_segments(spline) -> list[_Segment]:
    """取出样条的所有段（闭合时补上"末点 → 首点"那段）。"""
    points = spline.bezier_points
    segments = [
        _Segment(points[index - 1].co, points[index - 1].handle_right,
                 points[index].handle_left, points[index].co)
        for index in range(1, len(points))
    ]
    if spline.use_cyclic_u and len(points) > 1:
        segments.append(_Segment(points[-1].co, points[-1].handle_right,
                                 points[0].handle_left, points[0].co))
    return segments


def subdivide_curve(curve, no_segs: int = 1) -> None:
    """把曲线的第一条样条保形细分：每段切成 ``no_segs`` 小段，形状不变、点数变多。"""
    if no_segs < 2 or not curve.splines:
        return
    original = curve.splines[0]
    is_cyclic = original.use_cyclic_u

    pieces: list[_Segment] = []
    for segment in _spline_segments(original):
        pieces.extend(
            segment.partial(index / no_segs, (index + 1) / no_segs) for index in range(no_segs)
        )
    if not pieces:
        return

    points = []
    previous = None
    for index, piece in enumerate(pieces):
        if index == 0:
            handle_left = pieces[-1].ctrl2 if is_cyclic else piece.start
        else:
            handle_left = previous.ctrl2
        points.append([piece.start, handle_left, piece.ctrl1])
        previous = piece
    if is_cyclic:
        points[-1][2] = pieces[-1].ctrl1
    else:
        points.append([previous.end, previous.ctrl2, previous.end])

    spline = curve.splines.new("BEZIER")
    spline.use_cyclic_u = is_cyclic
    spline.bezier_points.add(len(points) - 1)
    for index, (co, handle_left, handle_right) in enumerate(points):
        bezier_point = spline.bezier_points[index]
        bezier_point.co = co
        bezier_point.handle_left = handle_left
        bezier_point.handle_right = handle_right
        bezier_point.handle_right_type = "FREE"
    curve.splines.remove(original)
