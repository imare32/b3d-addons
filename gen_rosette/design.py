"""设计层：一个种子 + 一组花型参数 → 可复现的``曲线 + 配色``。

这一层只回答"画什么"，不 import bpy、也不知道画布多大：

* 花型（花瓣数 / 阶数 / 星型）由面板显式给出，**不参与随机**——玫瑰线是数学图形，
  随机化它只会得到"另一朵花"，不像 zellij 那样有构图意义；
* 种子只决定**配色**：5 色色板与"三色色带"的次序。所以换种子＝换一套配色，
  花型保持不变，便于对比同一个花型在不同配色下的效果；
* 色板协议与 ``gen_zellij`` 一致（同一套 :func:`init_colours` 规则），两个插件出的
  作品看起来是一套东西。

"三色色带"指色块（:mod:`faces`）的填色按**质心半径**分三圈，从内到外依次取色板里的
三个中间色——旋转对称因此被保住，不会像逐块随机那样把一朵花染成噪点。
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass

from .rng import Mulberry32
from .rosette import (
    Curve,
    Point,
    cal_kernels,
    get_petals_by_kernel,
    merge_adjacent_petals,
    realizable_level,
    sort_petals,
)

__all__ = [
    "FACE_BANDS",
    "FACE_SLOTS",
    "Design",
    "build_design",
    "colour_for_radius",
    "init_colours",
]

RGB = tuple[int, int, int]

#: 色板里的哪 3 个槽给色块用（0 = 暗底、1 = 亮底，留给带子与描边）。
FACE_SLOTS: tuple[int, ...] = (2, 3, 4)

#: 由内向外分几圈配色。3 圈正好把 3 个中间色各用一次：内圈 / 中圈 / 外圈。
FACE_BANDS = 3


@dataclass
class Design:
    """一次完整的设计结果（纯数据，与任何渲染后端无关）。"""

    seed: int
    #: 花瓣数（玫瑰线的角数）。
    petals: int
    #: 实际用的阶数；面板请求的阶不可实现时会被 :func:`rosette.realizable_level` 收回来。
    level: int
    star_type: str
    #: 面板上原本请求的阶数（``level`` 被收回来时两者不同，算子据此给提示）。
    requested_level: int
    colours: list[RGB]
    #: 串好的曲线：每条是一段连续的玫瑰线（闭合环）。
    curves: list[Curve]
    #: 第 ``level`` 阶的核（construction 骨架，``Guides`` 图层要画的一条）。
    kernel: list[Point]
    #: ``n`` 片原始花瓣（``get_petals_by_kernel`` 的原始输出，**逐片断开**）。
    #: ``Guides`` 图层画的是合并后的 :attr:`curves`；这份留着给"按片驱动"的脚本用
    #: （例如想按 0, 1, 2 … 的顺序逐片长出来，就自己按这个列表建笔划）。
    petal_lines: list[list[Point]]
    #: 色块按半径分圈时的取色次序（种子决定，见模块文档）。
    face_slots: tuple[int, ...]

    @property
    def level_clamped(self) -> bool:
        return self.level != self.requested_level


# --- 颜色 -----------------------------------------------------------------


def _hsb_to_rgb(h: float, s: float, b: float, hue_max: float = 100.0) -> RGB:
    """HSB→RGB（分量 0-255）；三个分量都按 ``hue_max`` 归一化。"""
    r, g, bl = colorsys.hsv_to_rgb((h % hue_max) / hue_max, s / hue_max, b / hue_max)
    return (round(r * 255), round(g * 255), round(bl * 255))


def init_colours(rng: Mulberry32) -> list[RGB]:
    """生成 5 色色板：暗底 / 亮底 / 三个中间色（与 ``gen_zellij`` 同一套协议）。"""
    return [
        _hsb_to_rgb(rng.random() * 100, rng.random() * 100, rng.random() * 30),
        _hsb_to_rgb(rng.random() * 100, rng.random() * 10, 100 - rng.random() * 5),
        _hsb_to_rgb(rng.random() * 100, rng.random() * 50 + 25, rng.random() * 50 + 25),
        _hsb_to_rgb(rng.random() * 100, rng.random() * 50 + 25, rng.random() * 50 + 25),
        _hsb_to_rgb(rng.random() * 100, rng.random() * 50 + 25, rng.random() * 50 + 25),
    ]


def colour_for_radius(
    radius: float, max_radius: float, colours: list[RGB], slots: tuple[int, ...]
) -> RGB:
    """按质心半径落在第几圈来取色（最外圈永远用最后一个槽，不会被截断到内圈色）。"""
    if max_radius <= 1e-9 or not slots:
        return colours[slots[0] if slots else 0]
    ratio = min(max(radius / max_radius, 0.0), 1.0)
    band = min(int(ratio * FACE_BANDS), FACE_BANDS - 1)
    return colours[slots[band % len(slots)]]


# --- 顶层 -----------------------------------------------------------------


def build_design(
    seed: int, *, petals: int = 10, level: int = 2, star_type: str = "PA"
) -> Design:
    """按种子与花型参数生成一份设计。

    ``level`` 超过该 ``(petals, star_type)`` 可实现的上限时**自动收回到上限**，
    并把请求值留在 ``requested_level`` 里（算子据此 report 一句提示），而不是抛异常——
    面板上拖滑块的过程中越界太常见了。

    异常:
        ValueError: ``petals < MIN_PETALS``、``level < 0``、``star_type`` 不被支持，
            或该 ``(petals, star_type)`` 连第 0 阶都不成立。
    """
    if level < 0:
        raise ValueError(f"level must be >= 0, got {level}")

    top = realizable_level(petals, star_type)
    used = min(int(level), top)

    rng = Mulberry32(seed)
    colours = init_colours(rng)
    # 三个中间色的使用次序也由种子决定：内圈不永远是同一个槽
    shift = int(rng.random() * len(FACE_SLOTS)) % len(FACE_SLOTS)
    face_slots = FACE_SLOTS[shift:] + FACE_SLOTS[:shift]

    kernel = cal_kernels(petals, used, star_type)[used]
    petal_lines = get_petals_by_kernel(kernel, petals)
    curves = merge_adjacent_petals(sort_petals(petal_lines, used))

    return Design(
        seed=seed,
        petals=petals,
        level=used,
        star_type=star_type,
        requested_level=int(level),
        colours=colours,
        curves=curves,
        kernel=kernel,
        petal_lines=petal_lines,
        face_slots=face_slots,
    )
