"""Port of ``geom.js`` (points, 3x3 affine matrices, ``fillBox``, ``groupTiles``).

点用 ``(x, y)`` 二元组表示，仿射矩阵用 ``(a, b, c, d, e, f)`` 六元组表示，
与 JS 原实现的 2x3 行主序完全一致::

    [a b c]
    [d e f]

``mul`` 依据操作数长度分派（长度 2 = 点，长度 6 = 矩阵），对应原实现里
``B.hasOwnProperty('x')`` 的分支。
"""

from __future__ import annotations

import math
from typing import NamedTuple, Sequence

__all__ = [
    "TOL",
    "Box",
    "make_affine",
    "point_add",
    "point_sub",
    "point_scale",
    "point_dist",
    "dot",
    "inv",
    "mul",
    "match_seg",
    "match_two_segs",
    "fill_box",
    "group_tiles",
]

#: 原实现里用于点重合判断的容差（``geom.js`` 中的 0.0001）。
TOL = 0.0001

Point = tuple[float, float]
Affine = tuple[float, float, float, float, float, float]


class Box(NamedTuple):
    x: float
    y: float
    w: float
    h: float


def make_affine(a, b, c, d, e, f) -> Affine:
    return (a, b, c, d, e, f)


# --- 点运算 ---------------------------------------------------------------


def point_add(p: Point, q: Point) -> Point:
    return (p[0] + q[0], p[1] + q[1])


def point_sub(p: Point, q: Point) -> Point:
    return (p[0] - q[0], p[1] - q[1])


def point_scale(p: Point, a: float) -> Point:
    return (p[0] * a, p[1] * a)


def dot(p: Point, q: Point) -> float:
    return p[0] * q[0] + p[1] * q[1]


def point_dist(p: Point, q: Point) -> float:
    dx = p[0] - q[0]
    dy = p[1] - q[1]
    return math.sqrt(dx * dx + dy * dy)


# --- 仿射矩阵 -------------------------------------------------------------


def inv(t: Sequence[float]) -> Affine:
    det = t[0] * t[4] - t[1] * t[3]
    return (
        t[4] / det,
        -t[1] / det,
        (t[1] * t[5] - t[2] * t[4]) / det,
        -t[3] / det,
        t[0] / det,
        (t[2] * t[3] - t[0] * t[5]) / det,
    )


def mul(a: Sequence[float], b: Sequence[float]) -> tuple:
    """``a * b``，``b`` 为点（长度 2）或矩阵（长度 6）。"""
    if len(b) == 2:
        return (
            a[0] * b[0] + a[1] * b[1] + a[2],
            a[3] * b[0] + a[4] * b[1] + a[5],
        )
    return (
        a[0] * b[0] + a[1] * b[3],
        a[0] * b[1] + a[1] * b[4],
        a[0] * b[2] + a[1] * b[5] + a[2],
        a[3] * b[0] + a[4] * b[3],
        a[3] * b[1] + a[4] * b[4],
        a[3] * b[2] + a[4] * b[5] + a[5],
    )


def match_seg(p: Point, q: Point) -> Affine:
    """把单位线段 ``(0,0)-(1,0)`` 映到 ``p-q`` 的变换。"""
    return (
        q[0] - p[0],
        p[1] - q[1],
        p[0],
        q[1] - p[1],
        q[0] - p[0],
        p[1],
    )


def match_two_segs(p1: Point, q1: Point, p2: Point, q2: Point) -> Affine:
    return mul(match_seg(p2, q2), inv(match_seg(p1, q1)))


def fill_box(b1: Box, b2: Box, rot: bool) -> Affine:
    """把 ``b1`` 缩放居中填入 ``b2``，必要时旋转 90° 以获得更大比例。"""
    sc = min(b2.w / b1.w, b2.h / b1.h)
    rsc = min(b2.w / b1.h, b2.h / b1.w)

    if not rot or (sc > rsc):
        return mul(
            make_affine(1, 0, b2.x + 0.5 * b2.w, 0, 1, b2.y + 0.5 * b2.h),
            mul(
                make_affine(sc, 0, 0, 0, sc, 0),
                make_affine(1, 0, -(b1.x + 0.5 * b1.w), 0, 1, -(b1.y + 0.5 * b1.h)),
            ),
        )

    return mul(
        make_affine(1, 0, b2.x + 0.5 * b2.w, 0, 1, b2.y + 0.5 * b2.h),
        mul(
            mul(make_affine(rsc, 0, 0, 0, rsc, 0), make_affine(0, -1, 0, 1, 0, 0)),
            make_affine(1, 0, -(b1.x + 0.5 * b1.w), 0, 1, -(b1.y + 0.5 * b1.h)),
        ),
    )


def group_tiles(tiles: Sequence[Sequence[Point]]) -> list[Point]:
    """把相邻瓷砖合并成一条外轮廓（消掉方向相反的重合边）。"""
    segs: list[tuple[Point, Point]] = []
    for t in tiles:
        n = len(t)
        for i in range(n):
            p = t[i]
            q = t[(i + 1) % n]
            found = -1
            for idx, s in enumerate(segs):
                if point_dist(s[0], q) < TOL and point_dist(s[1], p) < TOL:
                    found = idx
                    break
            if found >= 0:
                segs.pop(found)
            else:
                segs.append((p, q))

    if not segs:
        return []

    ret = [segs[0][0]]
    last = segs[0][1]
    segs = segs[1:]

    while segs:
        for idx, s in enumerate(segs):
            if point_dist(s[0], last) < TOL:
                ret.append(s[0])
                last = s[1]
                segs.pop(idx)
                break
        else:
            # 原实现在此会死循环；这里直接结束，避免卡死 Blender。
            break

    return ret
