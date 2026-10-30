"""配色预设：参考实现的六套 + 项目色板。

一套预设 = 画布底色 + 本体色 + 描边色 + 填充色（+ 可选的"按砖的边数分色"表）。
切预设时把这几个值一起写进面板，观感立刻变；几何一个点都不用重算。

**为什么单独一个文件**：``scene`` 里只用得到"按边数分色"那张表，而面板要用到整组；
放在这里两边都能 import，且不把 ``scene``（纯算法层）和 ``props``（RNA）绑在一起。

**这里的颜色属于"作品配色"，不属于项目色板**。根 ``README`` 的规则是"全项目一套色板"，
这六套是参考实现自带的成品配色（``Zellige`` / ``Gold on night`` …），照搬是为了对着
网页版看效果；默认仍是 ``PROJECT``（项目色板），不是它们。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .palette import AMBER, INK, OCHRE, PAPER, hex_to_rgb
from .scene import FILL_BY_SIDES

__all__ = [
    "PRESETS",
    "PRESET_ITEMS",
    "Preset",
    "preset_of",
]

RGBF = tuple[float, float, float]

#: 项目色板那一档的键：默认值，也是"没有分色表"的那几套的落点。
PRESET_PROJECT = "PROJECT"


@dataclass(frozen=True, slots=True)
class Preset:
    """一套配色。``by_sides`` 为 ``None`` 表示这套不按边数分色。"""

    key: str
    label: str
    canvas: RGBF
    body: RGBF
    case: RGBF
    fill: RGBF
    by_sides: dict[int, RGBF] = field(default_factory=dict)


def _rgb(value: str) -> RGBF:
    return hex_to_rgb(value)


def _sides(mapping: dict[int, str]) -> dict[int, RGBF]:
    return {sides: _rgb(value) for sides, value in mapping.items()}


#: 六套照搬参考实现（``js/girih.js`` 的 ``PALETTES``），另加项目色板档。
#:
#: ``canvas`` = 画布底色，``body`` = 本体（band），``case`` = 描边（edge），
#: ``fill`` = 星形填充的单色（不按边数分色时用），``by_sides`` = 分色表。
PRESETS: dict[str, Preset] = {
    PRESET_PROJECT: Preset(
        PRESET_PROJECT,
        "项目色板",
        _rgb(PAPER),
        _rgb(AMBER),
        _rgb(INK),
        _rgb(OCHRE),
        dict(FILL_BY_SIDES),
    ),
    "ZELLIGE": Preset(
        "ZELLIGE",
        "Zellige · 米底彩星",
        _rgb("#efe4cc"),
        _rgb("#fbf6ea"),
        _rgb("#2f261c"),
        _rgb("#d29b3e"),
        _sides({3: "#d29b3e", 4: "#b5452f", 6: "#2b7a5f", 8: "#1d5a85", 12: "#1d5a85"}),
    ),
    "NIGHT": Preset(
        "NIGHT",
        "Gold on night · 夜底金线",
        _rgb("#0f1b2d"),
        _rgb("#d9b54a"),
        _rgb("#070d18"),
        _rgb("#d9b54a"),
        _sides({3: "#1b3352", 4: "#22436b", 6: "#173a5e", 8: "#1d4c7a", 12: "#1d4c7a"}),
    ),
    "ISFAHAN": Preset(
        "ISFAHAN",
        "Isfahan · 青底乳白",
        _rgb("#156a83"),
        _rgb("#f4ead2"),
        _rgb("#0b3442"),
        _rgb("#e1b54c"),
        _sides({3: "#e1b54c", 4: "#e1b54c", 6: "#0e4f63", 8: "#0d4357", 12: "#0d4357"}),
    ),
    "TERRACOTTA": Preset(
        "TERRACOTTA",
        "Terracotta · 赤陶",
        _rgb("#c56a3e"),
        _rgb("#f6e7cf"),
        _rgb("#4a2414"),
        _rgb("#e0a25a"),
        _sides({3: "#2f5d50", 4: "#8f3b1f", 6: "#e0a25a", 8: "#7c321a", 12: "#7c321a"}),
    ),
    "INK": Preset(
        "INK",
        "Ink on paper · 纸白墨线",
        _rgb("#f7f3ea"),
        _rgb("#ffffff"),
        _rgb("#1d1d1d"),
        _rgb("#ffffff"),
        {},
    ),
    "CHALK": Preset(
        "CHALK",
        "Chalk on slate · 石板粉笔",
        _rgb("#2c3238"),
        _rgb("#3a4148"),
        _rgb("#e9e6df"),
        _rgb("#3a4148"),
        {},
    ),
}

#: 面板下拉项：``(标识符, 显示名, 说明)``。
PRESET_ITEMS = [(preset.key, preset.label, "") for preset in PRESETS.values()]


def preset_of(key: str) -> Preset:
    """按键取预设，未知键退回项目色板（不抛异常，面板永远有东西可用）。"""
    return PRESETS.get(key, PRESETS[PRESET_PROJECT])
