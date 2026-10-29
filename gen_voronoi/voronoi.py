"""Voronoi 单元：种子点 → 一组凸多边形（不 import bpy，可以脱离界面单独跑）。

种子只用世界坐标的 **XY 投影**：Voronoi 是二维划分，每个单元的 z 取自己种子的 z，
所以把种子撒在起伏的面上，得到的是一层贴着它们高度的平面图案（参考实现
``voronoi_lab.py`` 就是这么做的）。

``build_cells`` 的一条链（只用 Blender 自带的 ``mathutils.geometry``）::

    Delaunay 三角剖分（delaunay_2d_cdt）
      → 每个三角形的外心 = 一个 Voronoi 顶点
      → 围着某个种子的外心按极角排序 = 这个种子的单元

种子的包围盒要往外垫一大圈（:data:`PADDING`）：Delaunay 的**外部**三角形没有外心，
不垫的话最外圈种子的单元是不封闭的。

后面的裁剪 / 内缩 / 采样都是纯二维多边形运算，见各函数说明。全部是确定性的：
同一批种子 + 同一套参数，每次算出来的点一模一样（参考实现里的 taichi 采样只是把
这一步搬到 GPU 上并行 —— 这里用 numpy 一次算完，几万个点也够快，且不用装 taichi）。
"""

from __future__ import annotations

import math
from typing import NamedTuple

import numpy as np
from mathutils import Vector
from mathutils.geometry import delaunay_2d_cdt

__all__ = [
    "ARC_SEGMENTS",
    "Cell",
    "CIRCLE_SEGMENTS",
    "CUBIC",
    "EDGES",
    "MIN_AREA",
    "PADDING",
    "QUADRATIC",
    "SHAPES",
    "build_cells",
    "cell_stroke",
    "circle_polygon",
    "clean_polygon",
    "clip_circle",
    "clip_polygon",
    "clip_rect",
    "inset_polygon",
    "polygon_area",
]

#: 单元形状：直边折线 / 二次曲线 / 三次曲线（后两种把拐角磨圆）。
EDGES = "EDGES"
QUADRATIC = "QUADRATIC"
CUBIC = "CUBIC"
SHAPES = (EDGES, QUADRATIC, CUBIC)

#: 垫在外围的包围盒放大倍数（相对种子包围盒的对角线长度）。
PADDING = 10.0

#: 单元面积小于它就当空单元（内缩过头时多边形会整个翻面）。
MIN_AREA = 1e-9

#: 「整个单元都在圆外」时用正多边形造一个圆 —— 48 条边在视觉上已经是圆了。
CIRCLE_SEGMENTS = 48

#: 圆弧折线化用几段（只影响被圆切出来的那一小段弧）。
ARC_SEGMENTS = 12

#: 采样权重缓存：``{分辨率: (w0, w1, w2, w3)}``（见 :func:`_weights`）。
_WEIGHTS = {}


class Cell(NamedTuple):
    """一个单元的成品：种子序号 + 高度 + 闭合笔划的二维点列 ``(m, 2)``。"""

    index: int
    """种子序号（写进 GP 的 ``cell_index`` 属性，就是源物体的第几个点）。"""

    z: float
    """这个单元的高度（取自己种子的 z）。"""

    points: np.ndarray
    """闭合笔划的点列，``(m, 2)`` 的 float64；首尾**不**重复。"""


# --- 单元 -----------------------------------------------------------------


def circumcenter(a, b, c):
    """三角形的外心；三点共线时退回重心（那样的三角形理论上没有外心）。"""
    d = 2.0 * (a.x * (b.y - c.y) + b.x * (c.y - a.y) + c.x * (a.y - b.y))
    if abs(d) < 1e-12:
        return (a + b + c) / 3.0
    aa = a.x * a.x + a.y * a.y
    bb = b.x * b.x + b.y * b.y
    cc = c.x * c.x + c.y * c.y
    ux = (aa * (b.y - c.y) + bb * (c.y - a.y) + cc * (a.y - b.y)) / d
    uy = (aa * (c.x - b.x) + bb * (a.x - c.x) + cc * (b.x - a.x)) / d
    return Vector((ux, uy))


def build_cells(points, padding: float = PADDING):
    """种子点 → 与种子序号**对齐**的单元列表（拿不到单元的位子是 ``None``）。

    返回的顶点按极角排好（CCW），是还没裁剪的原始 Voronoi 单元：最外圈种子的单元会
    一直伸到垫出来的包围盒外面，要由调用方裁剪（见 :func:`clip_rect` /
    :func:`clip_circle`）。

    重合的种子在 Delaunay 里会被合并成一个顶点（容差 ``1e-5``），它们拿到同一个单元。
    """
    count = len(points)
    cells = [None] * count
    if count == 0:
        return cells

    seeds = [Vector((float(row[0]), float(row[1]))) for row in points]
    low = Vector((min(p.x for p in seeds), min(p.y for p in seeds)))
    high = Vector((max(p.x for p in seeds), max(p.y for p in seeds)))
    margin = ((high - low).length or 1.0) * padding

    corners = [
        Vector((low.x - margin, low.y - margin)),
        Vector((high.x + margin, low.y - margin)),
        Vector((high.x + margin, high.y + margin)),
        Vector((low.x - margin, high.y + margin)),
    ]

    try:
        result = delaunay_2d_cdt(seeds + corners, [], [], 0, 1e-5)
    except (RuntimeError, ValueError):
        # 全部种子重合之类：没有三角形，也就没有单元
        return cells
    verts, _edges, faces, orig_verts, _orig_edges, _orig_faces = result

    owner = {}
    for slot, sources in enumerate(orig_verts):
        for source in sources:
            if source < count:
                owner[source] = slot

    incident = [[] for _ in verts]
    for slot, face in enumerate(faces):
        for vertex in face:
            incident[vertex].append(slot)

    centers = [circumcenter(verts[f[0]], verts[f[1]], verts[f[2]]) for f in faces]

    for index in range(count):
        slot = owner.get(index)
        if slot is None:
            continue
        faces_of_seed = incident[slot]
        if len(faces_of_seed) < 3:
            continue
        origin = verts[slot]
        poly = [centers[face] for face in faces_of_seed]
        poly.sort(key=lambda p: math.atan2(p.y - origin.y, p.x - origin.x))
        cells[index] = poly
    return cells


# --- 多边形工具 -----------------------------------------------------------


def polygon_area(poly) -> float:
    """有向面积（CCW 为正）；顶点不足 3 个算 0。"""
    count = len(poly)
    if count < 3:
        return 0.0
    total = 0.0
    for index in range(count):
        p = poly[index]
        q = poly[(index + 1) % count]
        total += p.x * q.y - q.x * p.y
    return total * 0.5


def clean_polygon(poly, *, ccw: bool = True):
    """去掉相邻重合的顶点（交点算出来的重复点）并统一成 CCW。

    ``ccw=False`` 时不做定向（省掉一次面积计算）：调用方已经知道绕向时用。
    """
    result = []
    for point in poly:
        if result and (point - result[-1]).length < 1e-9:
            continue
        result.append(point)
    while len(result) > 1 and (result[0] - result[-1]).length < 1e-9:
        result.pop()
    if len(result) < 3:
        return []
    if ccw and polygon_area(result) < 0.0:
        result.reverse()
    return result


def _halfplane(poly, nx: float, ny: float, offset: float):
    """保留 ``nx * x + ny * y + offset >= 0`` 的一侧（Sutherland–Hodgman 一步）。"""
    count = len(poly)
    if count == 0:
        return []
    result = []
    for index in range(count):
        p = poly[index]
        q = poly[(index + 1) % count]
        dp = nx * p.x + ny * p.y + offset
        dq = nx * q.x + ny * q.y + offset
        if dp >= 0.0:
            result.append(p)
            if dq < 0.0:
                ratio = dp / (dp - dq)
                result.append(p + (q - p) * ratio)
        elif dq >= 0.0:
            ratio = dp / (dp - dq)
            result.append(p + (q - p) * ratio)
    return result


def clip_rect(poly, low, high):
    """按轴对齐矩形裁剪（``low`` / ``high`` 是 XY 两个角点）。

    整块在框里时原样返回：气泡模式下绝大多数单元都碰不到边界，省掉四趟裁剪。
    """
    if all(low.x <= p.x <= high.x and low.y <= p.y <= high.y for p in poly):
        return poly
    result = _halfplane(poly, 1.0, 0.0, -low.x)
    result = _halfplane(result, -1.0, 0.0, high.x)
    result = _halfplane(result, 0.0, 1.0, -low.y)
    result = _halfplane(result, 0.0, -1.0, high.y)
    return result


def clip_polygon(poly, convex):
    """用凸多边形 ``convex``（CCW）的半平面逐个裁 ``poly``。"""
    result = poly
    count = len(convex)
    if count < 3:
        return []
    for index in range(count):
        a = convex[index]
        b = convex[(index + 1) % count]
        nx = -(b.y - a.y)
        ny = b.x - a.x
        length = math.hypot(nx, ny)
        if length < 1e-12:
            continue
        nx /= length
        ny /= length
        result = _halfplane(result, nx, ny, -(nx * a.x + ny * a.y))
        if len(result) < 3:
            return []
    return result


def circle_polygon(centre, radius: float, segments: int = CIRCLE_SEGMENTS):
    """正 ``segments`` 边形（CCW）的顶点表，用来造一个圆。"""
    step = math.tau / segments
    return [
        Vector(
            (
                centre.x + radius * math.cos(step * index),
                centre.y + radius * math.sin(step * index),
            )
        )
        for index in range(segments)
    ]


def _circle_hit(p, q, centre, radius: float):
    """线段 ``p→q`` 与圆的交点（一端在内、一端在外时只有一个）。"""
    dx = q.x - p.x
    dy = q.y - p.y
    fx = p.x - centre.x
    fy = p.y - centre.y
    a = dx * dx + dy * dy
    b = 2.0 * (fx * dx + fy * dy)
    c = fx * fx + fy * fy - radius * radius
    if a < 1e-18:
        return p.copy()
    disc = b * b - 4.0 * a * c
    if disc < 0.0:
        return (p + q) * 0.5
    root = math.sqrt(disc)
    t1 = (-b - root) / (2.0 * a)
    t2 = (-b + root) / (2.0 * a)
    # 取落在 [0, 1] 里的那个（浮点边界也算）
    for t in (t1, t2):
        if -1e-9 <= t <= 1.0 + 1e-9:
            return p + (q - p) * min(1.0, max(0.0, t))
    return p + (q - p) * min(1.0, max(0.0, t1))


def _arc(start, end, centre, radius: float, segments: int = ARC_SEGMENTS):
    """圆上从 ``start`` 逆时针走到 ``end`` 的中间点（不含两端）。"""
    a0 = math.atan2(start.y - centre.y, start.x - centre.x)
    a1 = math.atan2(end.y - centre.y, end.x - centre.x)
    delta = (a1 - a0) % math.tau
    if delta < 1e-9:
        return []
    points = []
    for step in range(1, segments + 1):
        angle = a0 + delta * step / (segments + 1)
        points.append(
            Vector(
                (
                    centre.x + radius * math.cos(angle),
                    centre.y + radius * math.sin(angle),
                )
            )
        )
    return points


def clip_circle(poly, centre, radius: float, segments: int = ARC_SEGMENTS):
    """凸多边形按圆裁剪，外侧那段折线换成圆弧（圆心一般在多边形里）。

    比「造正 n 边形再逐个半边裁」快一个量级：一趟走完，弧按 ``segments`` 段折线化。
    """
    count = len(poly)
    if count < 3 or radius <= 0.0:
        return []
    squared = radius * radius
    outside = [
        (point.x - centre.x) ** 2 + (point.y - centre.y) ** 2 > squared
        for point in poly
    ]
    if not any(outside):
        return list(poly)
    if all(outside):
        # 整个单元都在圆外：能留的只有这个圆
        return circle_polygon(centre, radius)

    # 从第一个内部顶点出发，保证按边走完一整圈（外侧段是连续的，圆弧起点才不会丢）
    start = outside.index(False)
    result = []
    arc_start = None
    for step in range(count):
        index = (start + step) % count
        following = (index + 1) % count
        p = poly[index]
        q = poly[following]
        if not outside[index]:
            result.append(p)
            if outside[following]:
                arc_start = _circle_hit(p, q, centre, radius)
                result.append(arc_start)
        elif not outside[following]:
            hit = _circle_hit(p, q, centre, radius)
            if arc_start is not None:
                result.extend(_arc(arc_start, hit, centre, radius, segments))
                arc_start = None
            result.append(hit)
    return result


def _line_hit(a1, a2, b1, b2):
    """两条**无限长**直线的交点；平行时返回 ``None``。

    不用 ``mathutils.geometry.intersect_line_line_2d``：它按**线段**判交，内缩后相邻两条
    偏移边的交点常常落在段外（外凸的钝角处），拿回来的 ``None`` 会被迫退回端点，
    多边形就散成星形（实测：缩 0.45 时面积还在、最长半径反而变大）。
    """
    ax, ay = a2.x - a1.x, a2.y - a1.y
    bx, by = b2.x - b1.x, b2.y - b1.y
    denominator = ax * by - ay * bx
    if abs(denominator) < 1e-12:
        return None
    t = ((b1.x - a1.x) * by - (b1.y - a1.y) * bx) / denominator
    return Vector((a1.x + ax * t, a1.y + ay * t))


def _inradius(poly, centre) -> float:
    """``centre`` 到多边形各边（无限长）的最短距离。"""
    best = float("inf")
    count = len(poly)
    for index in range(count):
        p = poly[index]
        q = poly[(index + 1) % count]
        dx = q.x - p.x
        dy = q.y - p.y
        length = math.hypot(dx, dy)
        if length < 1e-12:
            continue
        distance = abs(dx * (centre.y - p.y) - dy * (centre.x - p.x)) / length
        best = min(best, distance)
    return best


def inset_polygon(poly, distance: float, centre=None):
    """凸多边形沿边内缩 ``distance``（负数 = 外扩）；缩没了返回 ``None``。

    每条边平移后再求相邻边（无限长）的交点（参考实现的做法），比逐点算角平分线稳。

    ``centre`` 给了就先做一次**必然缩没**的判断：内缩量超过「它到最近边的距离」时，
    凸多边形一定会翻过来 —— 翻过之后相邻边的交点会重新拼出一个面积极小的多边形，
    光看面积根本判不出来（实测：缩 0.2 判掉了，缩 0.45 反而「又活了」）。Voronoi 单元
    的种子就在多边形里，传它进来即可。
    """
    if abs(distance) < 1e-12:
        return list(poly)  # 不缩：调用方传进来的已经是干净的多边形
    poly = clean_polygon(poly)
    count = len(poly)
    if count < 3:
        return None

    if distance > 0.0:
        pivot = centre
        if pivot is None:
            pivot = sum(poly, Vector((0.0, 0.0))) / count
        if _inradius(poly, pivot) <= distance:
            return None

    lines = []
    for index in range(count):
        p = poly[index]
        q = poly[(index + 1) % count]
        direction = q - p
        length = direction.length
        if length < 1e-12:
            return None
        # CCW 多边形：边方向的左法线指向内部
        normal = Vector((-direction.y / length, direction.x / length))
        lines.append((p + normal * distance, q + normal * distance))

    result = []
    for index in range(count):
        a1, a2 = lines[index - 1]
        b1, b2 = lines[index]
        hit = _line_hit(a1, a2, b1, b2)
        result.append(hit if hit is not None else b1)

    # 这里**不能**让 clean_polygon 顺手把绕向翻正：缩过头时多边形会整个翻面（面积变负），
    # 翻正之后就变成一个「镜像的小多边形」，看着像单元还在，其实是错的
    result = clean_polygon(result, ccw=False)
    if len(result) < 3 or polygon_area(result) <= MIN_AREA:
        return None
    return result


# --- 单元 → 笔划 ----------------------------------------------------------


def _weights(resolution: int):
    """三次贝塞尔的四个权重列（按分辨率缓存：每个单元都重算一遍是白费）。"""
    steps = max(2, int(resolution))
    cached = _WEIGHTS.get(steps)
    if cached is None:
        t = np.linspace(0.0, 1.0, steps)
        u = 1.0 - t
        cached = (
            (u**3)[:, None],
            (3.0 * u * u * t)[:, None],
            (3.0 * u * t * t)[:, None],
            (t**3)[:, None],
        )
        _WEIGHTS[steps] = cached
    return cached


def _sample(segments, resolution: int) -> np.ndarray:
    """逐段采样三次贝塞尔，去掉相邻段之间重复的端点，返回 ``(m, 2)``。

    相邻段共用端点（圆弧一条接一条），采样后必然重复；不去掉的话 GP 里每条笔划
    都会多出一串零长度的段，看着像加粗的结。重复点是**严格相等**的（同一个端点
    会被采两次），所以按二进制相等比较，省掉逐点 ``allclose``。
    """
    data = np.array(
        [[(p.x, p.y) for p in segment] for segment in segments], dtype=np.float64
    )
    w0, w1, w2, w3 = _weights(resolution)
    points = (
        w0 * data[:, 0][:, None, :]
        + w1 * data[:, 1][:, None, :]
        + w2 * data[:, 2][:, None, :]
        + w3 * data[:, 3][:, None, :]
    ).reshape(-1, 2)

    keep = np.ones(len(points), dtype=bool)
    if len(points) > 1:
        keep[1:] = np.any(points[1:] != points[:-1], axis=1)
    points = points[keep]
    # 闭合：最后一段的终点就是第一段的起点，去掉它（GP 自己会收尾）
    if len(points) > 2:
        first = points[0]
        last = points[-1]
        if abs(first[0] - last[0]) < 1e-9 and abs(first[1] - last[1]) < 1e-9:
            points = points[:-1]
    return points


def cell_stroke(poly, shape: str = EDGES, resolution: int = 10, min_edge: float = 0.0):
    """多边形 → 一条闭合笔划的点列（``(m, 2)``）；点太少 / 全被滤掉时返回 ``None``。

    ``shape``：
    * ``EDGES`` —— 沿多边形顶点走一圈（直边，最忠实于 Voronoi 的数学形状）；
    * ``QUADRATIC`` —— 每个拐点一段二次曲线（端点落在两边中点），拐角磨圆；
    * ``CUBIC`` —— 每个拐点一段三次曲线；**短于 ``min_edge`` 的边整条吃掉**，
      两边的中点直接连起来 —— 所以这是「稠密单元也能画干净」的那一档。
    """
    # 绕向由上游（裁剪 / 内缩）保证，这里只剔重复点
    poly = clean_polygon(poly, ccw=False)
    count = len(poly)
    if count < 3:
        return None

    if shape == EDGES:
        return np.array([(point.x, point.y) for point in poly], dtype=np.float64)

    segments = []
    if shape == QUADRATIC:
        for index in range(count):
            start = (poly[index - 1] + poly[index]) * 0.5
            end = (poly[index] + poly[(index + 1) % count]) * 0.5
            segments.append((start, poly[index], poly[index], end))
    else:
        valid = [
            index
            for index in range(count)
            if (poly[(index + 1) % count] - poly[index]).length > min_edge
        ]
        total = len(valid)
        if total < 2:
            return None
        for step in range(total):
            current = valid[step]
            following = valid[(step + 1) % total]
            start = (poly[current] + poly[(current + 1) % count]) * 0.5
            end = (poly[following] + poly[(following + 1) % count]) * 0.5
            segments.append(
                (
                    start,
                    poly[(current + 1) % count],
                    poly[following],
                    end,
                )
            )

    if not segments:
        return None
    points = _sample(segments, resolution)
    return points if len(points) >= 3 else None
