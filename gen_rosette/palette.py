"""项目统一色板（唯一来源）。

色板规则见仓库根 ``README.md`` 的「颜色偏好」：

* 色值一律以 sRGB 十六进制书写；
* 写进 Blender 的颜色属性（``vertex_color``、材质、世界背景）前必须转线性，
  否则渲染出来整体发白 —— 用 :func:`hex_to_linear`。

**打包说明**：Blender 扩展必须自包含，所以 ``gen_zellij/palette.py``、
``gen_rosette/palette.py``、``sim_catenary/palette.py``、``gen_celtic/palette.py``
都是本文件的**逐字副本**（随各自的 zip 打包）。改色板时五份一起改，用
``Get-FileHash`` 对比即可确认同步。
"""

from __future__ import annotations

__all__ = [
    "AZURITE",
    "BACKGROUND",
    "CINNABAR",
    "INK",
    "MOSS",
    "OCHRE",
    "PALETTE",
    "SILK",
    "hex_to_linear",
    "hex_to_rgb",
    "srgb_to_linear",
]

INK = "#2B2B2B"  # 墨玄
CINNABAR = "#C85A4B"  # 朱砂
AZURITE = "#3E5C6B"  # 石青
OCHRE = "#C49A45"  # 赭石
MOSS = "#5A7052"  # 苔绿
SILK = "#F4F0EA"  # 绢白

#: 画布底色 —— 作品统一用它，避免每个后端各写一个白。
BACKGROUND = "#E4D9D0"

#: 常量名 → 色值，给需要遍历色板的场合用（导出色卡、做一个配色面板）。
PALETTE = {
    "INK": INK,
    "CINNABAR": CINNABAR,
    "AZURITE": AZURITE,
    "OCHRE": OCHRE,
    "MOSS": MOSS,
    "SILK": SILK,
    "BACKGROUND": BACKGROUND,
}


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
