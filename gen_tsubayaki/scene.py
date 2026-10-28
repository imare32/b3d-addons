"""场景装配层：:class:`~gen_tsubayaki.pattern.Pattern`（设计坐标） → 笔划集合（世界坐标）。

设计坐标就是参考脚本的像素坐标（象限边界 ±180、单块 360），这里按仓库惯例统一变换：
**等比缩放到画布 + 居中 + y 翻转**，于是参考里那个 ``1/180 * scale`` 被 ``canvas`` /
``margin`` 取代（``scale = 1.5`` ≈ "画布 3 单位、边距 0"，其余都是同一个等比缩放）。

弦线族关于原点镜像对称，y 翻转只会把 ``VAR_2`` / ``VAR_3`` 对调（同一个图案换个镜像看），
所以照旧走仓库约定。Z 向波动跟着同一个缩放系数走，起伏比例不随画布大小变化。

本模块不 import bpy（只依赖 numpy），渲染约定见 :mod:`gen_tsubayaki.gp`。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = [
    "LAYER_CHORDS",
    "LAYER_ORDER",
    "StrokeSet",
    "build_layers",
    "depth_order",
]

#: 弦线族的图层名（存进文件）。
LAYER_CHORDS = "Chords"

#: 图层顺序（从下到上）。
LAYER_ORDER = (LAYER_CHORDS,)


@dataclass(frozen=True, slots=True)
class StrokeSet:
    """一组同色同宽的笔划：扁平点列 + 逐笔划点数 + 逐笔划不透明度。

    ``points`` 是 GP 后端要的 ``(P, 3)`` 世界坐标（``[x1, y1, z1, x2, y2, z2, ...]``），
    ``counts`` 每根笔划的点数（弦恒为 2），``alpha`` 是每根笔划的不透明度
    （写进 GP 的 ``opacity`` 属性 —— 它是**真的**控制笔划透明度的那一个，
    ``vertex_color`` 的 alpha 通道渲染时被忽略，实测见 README）。
    """

    layer: str
    points: np.ndarray
    counts: tuple[int, ...]
    alpha: np.ndarray
    radius: float

    @property
    def strokes(self) -> int:
        return len(self.counts)


class _Frame:
    """设计坐标 → 世界坐标：等比缩放到画布、居中、y 翻转。"""

    __slots__ = ("scale", "mid_x", "mid_y", "target_x", "target_y")

    def __init__(
        self,
        bounds: tuple[float, float, float, float],
        size: float,
        margin: float,
    ) -> None:
        lo_x, lo_y, hi_x, hi_y = bounds
        width = max(hi_x - lo_x, 1e-9)
        height = max(hi_y - lo_y, 1e-9)
        usable = max(float(size) - 2.0 * float(margin), 1e-3)
        self.scale = usable / max(width, height)
        self.mid_x = 0.5 * (lo_x + hi_x)
        self.mid_y = 0.5 * (lo_y + hi_y)
        self.target_x = 0.5 * float(size)
        self.target_y = 0.5 * float(size)


def build_layers(
    pattern,
    *,
    size: float = 10.0,
    margin: float = 0.5,
    radius: float = 0.008,
) -> list[StrokeSet]:
    """把 :class:`~gen_tsubayaki.pattern.Pattern` 变成 GP 后端能直接写的笔划集合。

    :param size: 画布边长（世界单位，图案被居中放进 ``[0, size]`` 方块）。
    :param margin: 画布内边距（世界单位）。
    :param radius: 笔划半径（世界单位）—— 线宽 = 2 * radius。
    """
    if pattern.chords <= 0:
        return []

    frame = _Frame(pattern.bounds, size, margin)
    src = np.asarray(pattern.points, dtype=np.float64)
    points = np.empty_like(src, dtype=np.float32)
    points[:, 0] = frame.target_x + (src[:, 0] - frame.mid_x) * frame.scale
    points[:, 1] = frame.target_y - (src[:, 1] - frame.mid_y) * frame.scale
    points[:, 2] = src[:, 2] * frame.scale

    return [
        StrokeSet(
            layer=LAYER_CHORDS,
            points=points,
            counts=pattern.counts,
            alpha=np.asarray(pattern.alpha, dtype=np.float32),
            radius=max(1e-6, float(radius)),
        )
    ]


def depth_order(stroke_sets: list[StrokeSet], *, eps: float = 1e-9) -> str:
    """决定 GP 的 ``stroke_depth_order``。

    弦线族是共面图案（``z = 0``），这时用 ``'3D'`` 排序会让深度相同的笔划互相打架
    （谁压谁没定义）；``'2D'`` 按存储顺序画，等于 Processing 的画家算法，稳定可复现。
    开了 Z 向波动就是真 3D 结构，交给 ``'3D'`` 做遮挡。
    """
    if not stroke_sets:
        return "2D"
    peak = max(
        float(np.abs(s.points[:, 2]).max()) for s in stroke_sets if s.points.size
    )
    return "3D" if peak > eps else "2D"
