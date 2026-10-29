"""环面 (p, q) 结的几何核心：纯 numpy，不 import bpy（可以脱离 Blender 单独跑）。

参数化照搬参考实现 ``ga4/addons/torus_knot.py`` 的 Taichi kernel，只是去掉了并行::

    theta = p · t · u_mult + rPhase
    phi   = q · t · v_mult + sPhase
    x = (R + r·cos φ)·cos θ
    y = (R + r·cos φ)·sin θ
    z = r·sin φ · h

一根**股**（strand）就是 t ∈ [0, 2π / links) 的一段曲线，采 ``resolution - 1`` 个点；
最后一个点不重复首点（差一个步长），因为笔划是**闭合**的、几何节点扫管也按闭合走。
``links > 1`` 时各股整体差一个 ``2π / q`` 的相位，绕在环面上正好铺满 —— 这些股各自
闭合，互不相交（p、q 互质时才是真结，否则退化成几条同轴的圈）。

每个点带两条 **vertex（POINT）域** 属性，它们就是这里说的「几何核心」：

* :data:`ATTR_U` ``u_map`` —— θ 归一化到 ``[0, 1)``（绕主圆一周的进度）；
* :data:`ATTR_V` ``v_map`` —— φ 归一化到 ``[0, 1)``（绕副圆一周的进度）。

两值都取 ``角度 / 2π`` 再减去自身下取整（与参考实现一致），所以跨圈时不会累积成
1、2、3……，拿去做颜色 / 动画驱动都不用再取模。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import gcd, pi, sqrt

import numpy as np

__all__ = [
    "ADAPTIVE_DENSITY",
    "ATTR_U",
    "ATTR_V",
    "MIN_RESOLUTION",
    "TorusKnot",
    "adaptive_resolution",
    "build",
    "link_count",
]

#: 两条核心属性的名字（GP 笔划 / 网格顶点上都叫这个，两边对得上）。
ATTR_U = "u_map"
ATTR_V = "v_map"

#: ``resolution`` 的下限：每股 ``resolution - 1`` 个点，少于 2 个点不成为一段曲线。
MIN_RESOLUTION = 3

#: 自动分段：按平均周长每「段」切 :data:`ADAPTIVE_DENSITY` 个点（参考实现的 ×8）。
ADAPTIVE_DENSITY = 8

_FULL_TURN = 2.0 * pi


@dataclass(frozen=True, slots=True)
class TorusKnot:
    """一根结的几何核心：点、每点的两条属性、每股的点数。"""

    #: ``(n, 3)`` float32，世界坐标（已含 ``scale``）。
    points: np.ndarray
    #: ``(n,)`` float32 ∈ ``[0, 1)``，绕主圆的归一化角度。
    u_map: np.ndarray
    #: ``(n,)`` float32 ∈ ``[0, 1)``，绕副圆的归一化角度。
    v_map: np.ndarray
    #: 每股的点数（``links`` 个，每个都是 ``resolution - 1``）。
    counts: tuple[int, ...]
    #: 股数。
    links: int
    #: 实际用的分段（每股点数 + 1）。
    resolution: int
    #: 每股是否闭合（环面结恒为 ``True``）。
    closed: bool = True

    @property
    def vertex_count(self) -> int:
        """总点数。"""
        return int(self.points.shape[0])

    @property
    def strand_count(self) -> int:
        """股数。"""
        return len(self.counts)


def link_count(p: int, q: int, multiple_links: bool = True) -> int:
    """股数：``p``、``q`` 不互质时结会裂成 ``gcd(p, q)`` 条同轴的股。

    关掉「多股」就恒为 1（此时图形不变，只是把所有的股连成一条来回绕的曲线）。
    """
    if not multiple_links:
        return 1
    return max(1, gcd(abs(int(p)), abs(int(q))))


def adaptive_resolution(p: int, q: int, major: float, minor: float, links: int = 1) -> int:
    """按结的平均周长定分段（公式照搬参考实现）。

    结曲线既含 ``p`` 圈主圆也含 ``q`` 圈副圆，最长的走法和最短的走法差在
    ``(R ± r)`` 上，取两者平均当代表长度；再按「每股平均长度」每单位 8 个点折算。
    分段下限 :data:`MIN_RESOLUTION`。
    """
    p_abs, q_abs = abs(int(p)), abs(int(q))
    outer = _FULL_TURN * sqrt(p_abs**2 * (major + minor) ** 2 + q_abs**2 * minor**2)
    inner = _FULL_TURN * sqrt(p_abs**2 * (major - minor) ** 2 + q_abs**2 * minor**2)
    average = 0.5 * (inner + outer)
    return max(MIN_RESOLUTION, int(average / max(1, links)) * ADAPTIVE_DENSITY)


def _wrap(turns: np.ndarray) -> np.ndarray:
    """整圈数折算成 ``[0, 1)``：减去自身下取整（与参考实现的 ``floor`` 同义）。"""
    return turns - np.floor(turns)


def build(
    *,
    p: int = 2,
    q: int = 3,
    u_mult: int = 1,
    v_mult: int = 1,
    major: float = 1.0,
    minor: float = 0.25,
    height: float = 1.0,
    scale: float = 1.0,
    revolution_phase: float = 0.0,
    spin_phase: float = 0.0,
    links: int = 1,
    resolution: int = 100,
) -> TorusKnot:
    """算一根环面结。

    :param p: 绕主圆的圈数；**带符号** —— 负数就是参考实现里的「flip p」。
    :param q: 绕副圆的圈数；带符号同上。
    :param major: 主半径 ``R``（环面管轴到中心）。
    :param minor: 副半径 ``r``（管的粗细）。
    :param height: z 方向的拉伸（1 = 正圆截面，0 = 压成平面曲线）。
    :param scale: 整体缩放（同时乘 ``major`` / ``minor``）。
    :param revolution_phase: ``theta`` 的起始相位（弧度）。
    :param spin_phase: ``phi`` 的起始相位（弧度）。
    :param links: 股数；用 :func:`link_count` 算。
    :param resolution: 分段（每股 ``resolution - 1`` 个点）。
    :raises ValueError: ``p`` / ``q`` 为 0，或 ``resolution`` 小于 :data:`MIN_RESOLUTION`。
    """
    p, q = int(p), int(q)
    if p == 0 or q == 0:
        raise ValueError("p、q 都不能为 0（会退化成一条重叠的圈）")

    resolution = int(resolution)
    if resolution < MIN_RESOLUTION:
        raise ValueError(f"分段至少 {MIN_RESOLUTION}（每股才够 2 个点成一段曲线）")

    links = max(1, int(links))
    per_strand = resolution - 1

    # (links, per_strand)：股号决定起始相位，步号决定 t
    strands = np.arange(links, dtype=np.float64)[:, None]
    steps = np.arange(per_strand, dtype=np.float64)[None, :]

    step_angle = _FULL_TURN / links / per_strand
    t = steps * step_angle
    theta = p * t * float(u_mult) + float(revolution_phase) + (_FULL_TURN / q) * strands
    phi = q * t * float(v_mult) + float(spin_phase)

    ring = float(major) * float(scale) + float(minor) * float(scale) * np.cos(phi)
    points = np.empty((links, per_strand, 3), dtype=np.float64)
    points[..., 0] = ring * np.cos(theta)
    points[..., 1] = ring * np.sin(theta)
    points[..., 2] = float(minor) * float(scale) * np.sin(phi) * float(height)

    # 实测坑：phi 与股号无关（形如 (1, per_strand)），不广播到 (links, per_strand) 的话
    # v_map 只有 per_strand 个数 —— 多股时属性长度对不上点，写进 GP 会直接报
    # "Array length mismatch"。
    shape = (links, per_strand)
    return TorusKnot(
        points=np.ascontiguousarray(points.reshape(-1, 3), dtype=np.float32),
        u_map=np.ascontiguousarray(
            np.broadcast_to(_wrap(theta / _FULL_TURN), shape).reshape(-1), dtype=np.float32
        ),
        v_map=np.ascontiguousarray(
            np.broadcast_to(_wrap(phi / _FULL_TURN), shape).reshape(-1), dtype=np.float32
        ),
        counts=(per_strand,) * links,
        links=links,
        resolution=resolution,
    )