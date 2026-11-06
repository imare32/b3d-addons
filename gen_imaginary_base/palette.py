"""色板：项目七色 + 数字集的配色 + sRGB ↔ 线性换算。

两条规矩（与仓库其它插件一致）：

* 颜色值一律写成 sRGB 十六进制；
* 交给 Blender 当**颜色属性**（``vertex_color``、材质、世界背景）之前必须转线性，
  否则渲染出来发灰 —— 用 :func:`hex_to_linear`。

HUD 是例外：它是 2D 覆盖层，不走色彩管理，所以那边的颜色（含 ``hud_layout`` 的
theme）保持 sRGB。

数字集的上色
    参考项目按数字的极角分配颜色（``|D| > 8`` 时绕色轮一圈），好处是几何上相邻的
    数字颜色也接近。这里换个更省事的做法：前 8 个手挑（保证小数字集好看），之后按
    黄金角散开色相 —— 无论数字集多大都不会撞色，也不必先把它们排序。
"""

from __future__ import annotations

import colorsys

__all__ = [
    "AMBER",
    "AZURITE",
    "BACKGROUND",
    "CINNABAR",
    "INK",
    "MOSS",
    "OCHRE",
    "PALETTE",
    "PAPER",
    "SILK",
    "digit_colour",
    "digit_colour_linear",
    "hex_to_linear",
    "hex_to_rgb",
    "mix",
    "srgb_to_linear",
]

INK = "#2B2B2B"  # 墨玄
CINNABAR = "#C85A4B"  # 朱砂
AZURITE = "#3E5C6B"  # 石青
OCHRE = "#C49A45"  # 赭石
AMBER = "#E8B362"  # 琥珀
MOSS = "#5A7052"  # 苔绿
SILK = "#F4F0EA"  # 绢白
BACKGROUND = "#E4D9D0"
PAPER = "#F9F7F3"

PALETTE = {
    "INK": INK,
    "CINNABAR": CINNABAR,
    "AZURITE": AZURITE,
    "OCHRE": OCHRE,
    "AMBER": AMBER,
    "MOSS": MOSS,
    "SILK": SILK,
    "BACKGROUND": BACKGROUND,
    "PAPER": PAPER,
}

#: 前 8 个数字用手挑的颜色（0 号是"零"，刻意压暗，让非零数字跳出来）。
_HEAD = (
    "#8C8A85",  # 0 —— 零：中性灰
    AZURITE,  # 1
    CINNABAR,  # 2
    MOSS,  # 3
    OCHRE,  # 4
    "#7F77DD",  # 5 紫
    "#2E9E8F",  # 6 青
    "#C4649B",  # 7 品红
)

#: 黄金角：0.618 圈，第 k 个色相 = k × 0.618（取小数部分）。
_GOLDEN = 0.6180339887498949


def hex_to_rgb(value: str) -> tuple[float, float, float]:
    """``"#RRGGBB"`` → sRGB 0..1 三元组（``#`` 可省，大小写不限）。"""
    text = value.lstrip("#")
    if len(text) != 6:
        raise ValueError(f"expected #RRGGBB, got {value!r}")
    return tuple(int(text[index : index + 2], 16) / 255.0 for index in (0, 2, 4))


def srgb_to_linear(component: float) -> float:
    """单个 sRGB 分量 → 线性（Blender 的颜色属性按线性存储）。"""
    if component <= 0.04045:
        return component / 12.92
    return ((component + 0.055) / 1.055) ** 2.4


def hex_to_linear(value: str) -> tuple[float, float, float]:
    """``"#RRGGBB"`` → 线性 0..1 三元组，可直接写进 Blender 的颜色属性。"""
    return tuple(srgb_to_linear(component) for component in hex_to_rgb(value))


def mix(first, second, amount: float):
    """sRGB 线性插值（``amount`` = 0 取 first，1 取 second）。"""
    amount = max(0.0, min(1.0, float(amount)))
    return tuple(
        first[index] + (second[index] - first[index]) * amount for index in range(3)
    )


def digit_colour(index: int) -> str:
    """第 ``index`` 个数字的 sRGB 色值；下标不设上限（色相按黄金角散开）。"""
    index = max(0, int(index))
    if index < len(_HEAD):
        return _HEAD[index]
    hue = (index * _GOLDEN) % 1.0
    # 明度略微随下标起伏，纯色轮在几十个数字时会显得过于均匀
    value = 0.68 + 0.12 * ((index * 0.37) % 1.0)
    red, green, blue = colorsys.hsv_to_rgb(hue, 0.72, value)
    return f"#{int(red * 255):02X}{int(green * 255):02X}{int(blue * 255):02X}"


def digit_colour_linear(index: int) -> tuple[float, float, float]:
    """同 :func:`digit_colour`，但返回线性值（写 ``vertex_color`` 用）。"""
    return hex_to_linear(digit_colour(index))
