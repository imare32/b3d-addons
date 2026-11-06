"""内置数系预设：从参考项目（TheGrayCuber · imaginary_bases）的收藏列表移植。

参考项目把一套设置压成一句可分享的话 —— ``"base 2i with digits 0, 1, i, and -1+i
color by 1st"`` —— 这里原样保留这句话，再由 :func:`parse_preset` 解析成结构化参数。
这样做的好处是**原文可追溯**：想核对某条预设和网页版是不是同一套，直接对比字符串即可。

每条预设后面的 ``@name`` 是网页上标注的投稿者，一并保留 —— 那是别人的作品，
搬过来时该带着名字走。

参考实现以 AGPL-3.0 发布（github: TheGrayCuber），这里的预设数据由它的
``favorites`` 数组机械提取；算法本身见 :mod:`ring`（独立实现）。
"""

from __future__ import annotations

import re

from .ring import format_qint, parse_qint

__all__ = [
    "MAX_DIGITS",
    "PRESETS",
    "PRESET_ENUM_ITEMS",
    "parse_preset",
    "preset_by_name",
    "preset_label",
]

#: 数字集上限。位数这一维已经被点数预算压住了，但「拖动数字」是 HUD 里一个个画的，
#: 几十个数字的面板已经很难点。96 是参考项目里最长那条预设（``base 5+7i``，74 个数字）
#: 的余量，不是为了让人真去拖 96 个点。
MAX_DIGITS = 96

#: ``(分享码, 投稿者)``。顺序与参考项目一致。
PRESETS = (
    (
        "base -3+ω with digits 0, 1, ω, -1-ω, -2-3ω, 3+ω, -1+2ω, 1+ω, -ω, -3-ω, 1-2ω, 2+3ω, and -1",
        "",
    ),
    ("base 2i with digits 0, 1, i, and -1+i color by 1st", "@resbertaninon6860"),
    ("base 1+2ω with digits 0, -1+ω, and 2-ω color by lead", "@4.5tc"),
    (
        "base 2+2i with digits 0, i, 1, -1, -i, 6, 1-i, and -1-i color by 1st",
        "@1ne00dred2wenty4our",
    ),
    (
        "base 9+8ω with digits 0, 2, -2, 2ω, 3+3ω, -2ω, -2-2ω, 3ω, -1+2ω, -2+2ω, 1+4ω, 2+2ω, 2+4ω, 2+3ω, 4+3ω, 3+ω, 3, 4+2ω, 3-ω, 2-2ω, 1-2ω, -3ω, -1-4ω, -3-3ω, -2-3ω, -2-4ω, -4-3ω, -3-ω, -4-2ω, -3, -3+ω, -1-5ω, 5+4ω, and -4+ω color by 2nd",
        "@agravitraxpro5933",
    ),
    (
        "base -2-2i with digits 0, 1+i, -i, -1, 1, 2+i, and -1+i color by 3rd",
        "@Not14Redmi-t5o",
    ),
    ("base -1+ω with digits 0, 1, and 1+ω", ""),
    (
        "base 10 with digits 0, -109, 22, 13, 4, 5, 86, -3013, 88, -1",
        "@StephenjamesRidge",
    ),
    (
        "base 7 with digits 0, 2+ω, 1+ω, 1+2ω, -1-ω, 1, -1, ω, -1+ω, -ω, -1-2ω, 1-ω, -2-ω, 3, 4+2ω, 3+3ω, 3ω, -3, -3-3ω, -3ω, -2+2ω, 2+4ω, -2-4ω, 2-2ω, -4-2ω, 1-2ω, -2+ω, -3-ω, 3+2ω, 1+3ω, -2-3ω, -2, 3+ω, and 2-ω",
        "@NaTra2019",
    ),
    (
        "base 7i with digits 0, i, -1, -3i, -i, 1, -2i, 2, -2, -1+i, 1+i, 2i, and -4i color by lead",
        "@dargorioxthekobold",
    ),
    (
        "base -2+2i with digits 0, 2, -2, -1, 1, i, -2i, -i, and 2i color by lead",
        "@KesseliYT-tauloud2",
    ),
    (
        "base -3ω with digits 0, 5ω, 5, -5ω, -5, -5-5ω, 5+5ω, -2-ω, 1+2ω, 2+ω, -1-2ω, 1-ω, -1+ω, 2, 2ω, 2+2ω, -2, -2ω, and -2-2ω color by 1st",
        "@user-tyup252",
    ),
    ("base 2ω with digits 0, -1, -1+2ω, and -1-ω color by 1st", "@a71_special"),
    (
        "base 3 with digits 0, 1, 1+i, -1+2i, -1, -1+i, 1+2i, and 2i color by 1st",
        "@viscovaldirio8224",
    ),
    (
        "base -3+ω with digits 0, 1, 1+ω, ω, -1, 1-ω, -1-2ω, -ω, 1+2ω, 2+ω, -2-ω, -1-ω, and -1+ω color by 2nd",
        "",
    ),
    ("base -1+ω with digits 0, 1, and 2 color by 2nd", "@Franek-n1i"),
    ("base -2 with digits 0, 1, ω, and -1-ω", "@Amipotsophspond"),
    ("base 1+2ω with digits 0, 1, and 3+5ω", "@kinoseidon"),
    (
        "base -6-ω with digits 0, 2+4ω, 4ω, 4+2ω, -4-4ω, -3, 3, -3-3ω, 3ω, 2, -1-ω, -ω, 1+ω, -1, 4, -2ω, -2, 2-2ω, -3ω, -4, 2+2ω, -2-2ω, 1, ω, -2+2ω, 3+3ω, -2-4ω, 2ω, 4+4ω, -4ω, and -4-2ω",
        "",
    ),
    (
        "base 4 with digits 0, 4+3ω, 1-ω, 1, -3+ω, 1+2ω, -2-ω, -1-ω, -2+ω, ω, 3+2ω, -1-4ω, -1-3ω, 2+2ω, -2, and -2ω",
        "@TheAgamemnon911",
    ),
    (
        "base 5+10i with digits 0, 1+2i, 2i, i, 3i, 4i, 2, 2+i, 2+2i, 2+3i, 2+4i, 4, 4+i, 4+2i, 4+3i, 4+4i, 6+4i, 5+4i, 5+2i, 6+2i, 5, 6, 8+4i, 8+3i, 8+2i, 8+i, 8, 9, 10, 12+3i, 12+4i, 12+i, 16+4i, 12+2i, 12, 13, 14, 16+i, 18+2i, 16, 18+3i, 18, 17+4i, 16+2i, 18+i, 17, 18+4i, and 16+3i color by 2nd",
        "@4.5tc",
    ),
    (
        "base 6+i with digits 0, 1, i, -1, -i, 1-4i, 3+i, -4+i, 4, 1+4i, -4, -4-2i, -3-i, 1-3i, 4+2i, 3-2i, -3+2i, 1-i, 1+i, -1+3i, -1-i, 2+3i, 3+2i, -1-4i, -4-i, -1+i, 2-4i, -2+4i, 2-3i, -3-2i, -2-3i, -2+3i, 4+i, 4i, 4-i, -1+4i, and -4i color by 1st",
        "",
    ),
    (
        "base -3+ω with digits 0, 2ω, -2-2ω, -2, 2+2ω, 2, -2ω, 1+2ω, 2+ω, 1-ω, -1-2ω, -2-ω, and -1+ω",
        "@beta_electron",
    ),
    (
        "base 3 with digits 0, 1, 1+i, -1, -i, i, 1-i, -1-i, and -1+i color by 3rd",
        "@theoverlordofforksofficial",
    ),
    (
        "base 3i with digits 0, 1, i, -1, -i, 2+2i, -2-2i, 2-2i, and -2+2i color by 3rd",
        "",
    ),
    ("base 2 with digits 0, 1, and 1+ω color by 1st", "@RodrigoParaNiños"),
    (
        "base 1+6ω with digits 0, 1, ω, -1-ω, -1+ω, -1-2ω, 2+ω, -1-3ω, 3+2ω, -2+ω, 3+3ω, -3, -3ω, -3-ω, 1-2ω, and 2+3ω color by 1st",
        "@supu8599",
    ),
    ("base -2-2ω with digits 0, 1+ω, -ω, and -1 color by 2nd", "@kshamasharma4725"),
    ("base 3 with digits 0, 2, 2+ω, 1-ω, -ω, and 1+ω", "@StephenjamesRidge"),
    (
        "base 3-2i with digits 0, 2+2i, -1+i, 1+i, 4i, -2+2i, 2i, -1+3i, and 1+3i",
        "@ViscoValdirio",
    ),
    (
        "base 5 with digits 0, 1, 1+ω, -1, -ω, ω, -1-2ω, 2+2ω, 2+ω, -1-ω, -2, -1+ω, 2ω, 2, -2ω, 1-ω, -2-ω, -2-2ω, -2-3ω, -2-4ω, and -1-3ω color by 2nd",
        "@noesenard6649",
    ),
    (
        "base -6-ω with digits 0, 2+3ω, -4-4ω, 4+3ω, 4ω, -1+ω, 1-ω, -2-ω, 1+2ω, -4-ω, -3-ω, 1+4ω, 3+2ω, -2+ω, 4, -1+3ω, 4+ω, 3-ω, -1-2ω, -4, -3-4ω, 3+4ω, 1-2ω, -1-3ω, -3+ω, 2+ω, -1-4ω, 1-3ω, 4+4ω, -4ω, and -4-3ω",
        "a family of squirrels",
    ),
    ("base 2+i with digits 0, 1, 2, -1, and 2+2i color by 2nd", "@JHF563"),
    (
        "base -2+3ω with digits 0, -ω, -1-ω, ω, -3-ω, 2+2ω, -1+2ω, -2ω, 1, 1-2ω, 3+ω, -2-3ω, -2, -3-3ω, 2+3ω, 3ω, 1+ω, -1, and 3 color by 2nd",
        "",
    ),
    (
        "base 4 with digits 0, 2+2ω, ω, 1+ω, 3+ω, 1, -1-ω, -ω, 1-ω, -2ω, -1, -2-ω, 2, 1+2ω, -2+ω, and -1-2ω",
        "@Slaydrik",
    ),
    ("base -1+2i with digits 0, 1, i, -1, and -i", ""),
    ("base 2+2ω with digits 0, -ω, 1, and -3ω", "@a71_special"),
    ("base 2 with digits 0, 1, 3+ω, and 2+3ω color by 5th", "@not_threadbox"),
    (
        "base -2+3ω with digits 0, 1, ω, -5-3ω, 2+5ω, -1-ω, 3-2ω, 4+3ω, 3+ω, 2ω, -2-3ω, 3+2ω, -1+2ω, 2, -2+ω, -1-3ω, -2-2ω, -1-4ω, and -3+ω color by 2nd",
        "",
    ),
    (
        "base 10+10i with digits 0, 2, -2, 3+i, 2+2i, -1-i, 2-2i, -3+i, 2i, -2-2i, 1+i, -1+3i, -2+2i, -1+i, -2i, 1+3i, 1-i, -3-i, -1-3i, 1-3i, 3-i, 4i, -4, -4i, 4, -i, i, -1, 1, -3i, 3i, 3, and -3 color by lead",
        "@KesseliYT-tauloud2",
    ),
    (
        "base -4+i with digits 0, 2, -2, 4+3i, 2+2i, -1-i, 2-2i, -4-3i, 2i, -2-2i, 1+i, -3+4i, -2+2i, -1+i, -2i, 3-4i, and 1-i color by lead",
        "",
    ),
    (
        "base 5+7i with digits 0, 1, i, -1, -i, 2+3i, -2-3i, -3+2i, 2+i, -2-i, 1-2i, 6+2i, -1+2i, -1-i, -1+i, -2i, 1-i, 1+i, 2i, 1+2i, 2, 2-i, 2-2i, -2+i, 3-i, -2, -2+2i, -3, 3i, 3+i, -1+3i, -2+3i, 3, 1-3i, -3+i, -1-2i, 2+2i, -3i, 1+3i, 2-3i, -3-i, 3+2i, -1-3i, -2-2i, -4+i, -3+3i, 3+3i, -4, 4i, -3-2i, -4+2i, 5+3i, -4-i, 4+3i, 6, -5-2i, -4-2i, 1+4i, -1+4i, 5, 5-i, -5+i, 4, 4+i, 6+3i, 5+i, 4+2i, 5+2i, 4-i, 3-2i, -5, -5-i, 2+4i, and 6+i color by 1st",
        "@khaledk13",
    ),
    (
        "base 7 with digits 0, 1, -ω, -5, -2+ω, -1-ω, 4ω, -1-2ω, -1, -3, 2+5ω, -1+3ω, 1+4ω, -2, 2+4ω, -6+ω, ω, -2+2ω, 1+6ω, -6+2ω, -5+2ω, -5+ω, -4, -4+ω, -1+ω, 5ω, 1+5ω, -7+2ω, -6+3ω, -7+3ω, -8+2ω, -3+ω, -3+2ω, -2+3ω, -1+4ω, -2+5ω, -2+6ω, -2+4ω, -3+3ω, -4+2ω, -3+4ω, -4+3ω, 2+3ω, -4+4ω, -3+5ω, -3+6ω, -4+5ω, -4+6ω, and -5+6ω color by lead",
        "@ymperal4663",
    ),
)

_UNIT = {"I": "i", "W": "ω"}


def parse_preset(code: str):
    """分享码 → ``{"domain", "base", "digits", "colour_mode", "colour_place"}``。

    认不出的返回 ``None``（面板据此把下拉退回"自定义"）。语法与参考项目一致：

    ``base <底数> with digits <数字, 数字, ...> [color by lead | color by <k>th]``
    """
    text = " ".join(str(code).split())
    if not text:
        return None
    match = re.match(
        r"^base\s+(?P<base>.+?)\s+(?:with\s+)?digits\s+(?P<digits>.+?)"
        r"(?:\s+color\s+by\s+(?P<colour>.+))?$",
        text,
        re.IGNORECASE,
    )
    if match is None:
        return None
    domain = "W" if "ω" in text else "I"
    base = parse_qint(domain, match.group("base"))
    if base == (0, 0):
        return None
    digits = []
    for piece in match.group("digits").split(","):
        piece = piece.strip()
        if piece.lower().startswith("and "):
            piece = piece[4:].strip()
        if not piece:
            continue
        digits.append(parse_qint(domain, piece))
        if len(digits) > MAX_DIGITS:
            return None
    if len(digits) < 2:
        return None
    colour = (match.group("colour") or "").strip().lower()
    mode, place = "AGGREGATE", 1
    if colour.startswith("lead"):
        mode = "LEAD"
    else:
        number = re.match(r"(\d+)\s*(?:st|nd|rd|th)", colour)
        if number:
            mode, place = "PLACE", max(1, int(number.group(1)))
    return {
        "domain": domain,
        "base": base,
        "digits": digits,
        "colour_mode": mode,
        "colour_place": place,
    }


def preset_by_name(name: str):
    """按面板枚举的 id（``"03"``）取一条预设；越界返回 ``None``。"""
    try:
        index = int(name)
    except (TypeError, ValueError):
        return None
    if not 0 <= index < len(PRESETS):
        return None
    return parse_preset(PRESETS[index][0])


def _ordinal(number: int) -> str:
    """``1 -> "1st"``、``2 -> "2nd"``、``3 -> "3rd"``、``11 -> "11th"``。"""
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def preset_label(code: str) -> str:
    """下拉里显示的一行名字：``2i · 4 digits`` / ``-1+i · 2 digits · by lead``。

    原先直接把分享码截断塞进下拉（``base 2i with digits 0, 1, i, an…``），
    每条都长得差不多、还都是半句话 —— 看不出底数是什么、有几个数字。
    这里解析出来按「底数 · 数字个数 · 上色方式」重排，读起来就是一套数系的摘要。
    解析不了（手改过的奇怪字符串）才退回原文。
    """
    data = parse_preset(code)
    if data is None:
        return code[:48]
    base = format_qint(data["domain"], *data["base"])
    label = f"{base} · {len(data['digits'])} digits"
    if data["colour_mode"] == "LEAD":
        return f"{label} · by lead"
    if data["colour_mode"] == "PLACE":
        return f"{label} · by {_ordinal(data['colour_place'])}"
    return label


def _enum_items() -> tuple:
    items = [("CUSTOM", "Custom", "Use the base and digits edited here", "NONE", 0)]
    for index, (code, credit) in enumerate(PRESETS):
        description = code + (f"\n\ncredit: {credit}" if credit else "")
        items.append(
            (
                f"{index:02d}",
                f"{index + 1:02d} · {preset_label(code)}",
                description,
                "PRESET",
                index + 1,
            )
        )
    return tuple(items)


#: 面板下拉的枚举项，**模块级常量** —— 不要换回 ``items=<回调>``。
#:
#: 起因是下拉里 44 条名字全成了乱码。逐项排除后确认：静态枚举 44 条显示完全正常，
#: 回调式 44 条就乱，而回调式只放 3 条也正常 —— 数据本身没问题，出问题的是
#: "在回调里现造字符串"这条路径：菜单拿到的 label 指针活不过回调返回那一刻。
#: 于是干脆在导入时把 44 条一次算好，交给 RNA 在注册时复制走，不再依赖 Python
#: 对象的存活期。预设是写死的常量，本来也不需要每次访问都重算。
PRESET_ENUM_ITEMS = _enum_items()
