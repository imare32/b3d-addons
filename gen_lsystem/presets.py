"""预设：一套「公理 + 产生式 + 解释参数」（纯 Python，**不 import bpy**）。

前七个直接对应 ABOP（*The Algorithmic Beauty of Plants*）与参考实现 readme 里的例子，
后几个是为了展示 GP 后端的两件事：**逐点粗细**（``!`` / ``#``）与**向重力**（tropism）。

每条预设都自带 ``iterations`` / ``angle`` / ``length`` / ``radius``，切预设整组写进面板 ——
不是「只换规则」：图案的形状与尺寸由这几项共同决定，只换一半只会得到一坨线。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = [
    "DEFAULT_PRESET",
    "PRESETS",
    "Preset",
    "apply_to",
    "by_key",
    "default",
    "items",
]


@dataclass(frozen=True, slots=True)
class Preset:
    """一套完整的 L-system 参数。规则写成 ``(左侧, 条件, 右侧)``，条件空串 = 恒真。"""

    key: str
    name: str
    axiom: str
    rules: tuple[tuple[str, str, str], ...]
    iterations: int = 5
    #: 默认转角（度）。
    angle: float = 25.0
    length: float = 0.05
    radius: float = 0.01
    radius_decay: float = 0.8
    radius_fat: float = 1.2
    tropism: float = 0.0
    dimension: str = "2D"
    note: str = ""
    source: str = ""


def _rules(*triples: tuple[str, ...]) -> tuple[tuple[str, str, str], ...]:
    """``("X", "F+")`` 补成 ``("X", "", "F+")``；三元组原样保留。"""
    out: list[tuple[str, str, str]] = []
    for triple in triples:
        if len(triple) == 2:
            out.append((triple[0], "", triple[1]))
        elif len(triple) == 3:
            out.append((triple[0], triple[1], triple[2]))
        else:
            raise ValueError(f"规则要写 2 或 3 项：{triple!r}")
    return tuple(out)


PRESETS: tuple[Preset, ...] = (
    Preset(
        key="fractal_plant",
        name="分形植物",
        axiom="X",
        rules=_rules(
            ("X", "F-[[X]+X]+F[+FX]-X"),
            ("F", "FF"),
        ),
        iterations=5,
        angle=25.0,
        length=0.035,
        radius=0.012,
        note="ABOP 图 1.24(f)：最经典的那棵分形植物",
        source="ABOP 1.24(f) / 参考实现 readme",
    ),
    Preset(
        key="tapered_tree",
        name="分叉树（带锥度）",
        axiom="X",
        rules=_rules(
            ("X", "!F[+(25)X][-(25)X]"),
            ("F", "FF"),
        ),
        iterations=6,
        angle=25.0,
        length=0.03,
        radius=0.02,
        radius_decay=0.78,
        note="每条枝 ! 变细一次：GP 的逐点 radius 直接吃到锥度",
        source="本扩展补充（演示 ! 与逐点粗细）",
    ),
    Preset(
        key="sierpinski",
        name="谢尔宾斯基垫片",
        axiom="F",
        rules=_rules(
            ("F", "G+F+G"),
            ("G", "F-G-F"),
        ),
        iterations=6,
        angle=60.0,
        length=0.05,
        radius=0.008,
        note="ABOP 图 1.10(b)：F 与 G 都画线，只有转角不同",
        source="ABOP 1.10(b)",
    ),
    Preset(
        key="koch",
        name="科赫雪花",
        axiom="F--F--F",
        rules=_rules(("F", "F+F--F+F")),
        iterations=4,
        angle=60.0,
        length=0.03,
        radius=0.008,
        note="三条边各是一条科赫曲线，首尾自动接成雪花",
        source="经典（ABOP 1.2）",
    ),
    Preset(
        key="dragon",
        name="龙形曲线",
        axiom="FX",
        rules=_rules(
            ("X", "X+YF+"),
            ("Y", "-FX-Y"),
        ),
        iterations=12,
        angle=90.0,
        length=0.03,
        radius=0.006,
        note="Heighway 龙：迭代 12 = 4096 段，仍然一笔连到底",
        source="经典（Heighway）",
    ),
    Preset(
        key="hilbert",
        name="希尔伯特曲线",
        axiom="A",
        rules=_rules(
            ("A", "-BF+AFA+FB-"),
            ("B", "+AF-BFB-FA+"),
        ),
        iterations=5,
        angle=90.0,
        length=0.09,
        radius=0.008,
        note="A / B 是占位模块，只有 F 落笔 —— 空间填充曲线",
        source="经典（Hilbert）",
    ),
    Preset(
        key="bush3d",
        name="三维灌木",
        axiom="A",
        rules=_rules(
            ("A", "[&FA]////[&FA]////[&FA]"),
            ("F", "FF"),
        ),
        iterations=5,
        angle=22.5,
        length=0.05,
        radius=0.014,
        radius_decay=0.75,
        dimension="3D",
        note="& 压枝 + / 绕枝旋转 90°：三根侧枝各朝一个方向",
        source="ABOP 1.25（简化）",
    ),
    Preset(
        key="monopodial",
        name="单轴树（参数化）",
        axiom="A(0.5,0.02)",
        rules=_rules(
            (
                "A(l,w)",
                (
                    "¤(w)F(l)[&(45)B(mul(l,0.6),mul(w,0.707))]"
                    "/(137.5)A(mul(l,0.9),mul(w,0.707))"
                ),
            ),
            (
                "B(l,w)",
                (
                    "¤(w)F(l)[-(45)$C(mul(l,0.6),mul(w,0.707))]"
                    "C(mul(l,0.9),mul(w,0.707))"
                ),
            ),
            (
                "C(l,w)",
                (
                    "¤(w)F(l)[+(45)$B(mul(l,0.6),mul(w,0.707))]"
                    "B(mul(l,0.9),mul(w,0.707))"
                ),
            ),
        ),
        iterations=8,
        angle=25.0,
        length=0.06,
        radius=0.01,
        radius_decay=0.9,
        tropism=0.05,
        dimension="3D",
        note="ABOP 图 2.6(a)：参数化规则 A(l,w)，137.5° 叶序角",
        source="ABOP 2.6(a)",
    ),
    Preset(
        key="ternary",
        name="三元分叉（向重力）",
        axiom="A",
        rules=_rules(
            ("A", "F[&(19)FA]/(94.74)[&(19)FA]/(132.63)[&(19)FA]"),
            ("F", "FF"),
        ),
        iterations=4,
        angle=19.0,
        length=0.08,
        radius=0.014,
        radius_decay=0.7,
        tropism=0.22,
        dimension="3D",
        note="ABOP 图 2.8：向重力把侧枝越压越垂",
        source="ABOP 2.8",
    ),
    Preset(
        key="space_filling",
        name="空间填充（分形刺）",
        axiom="A(1)",
        rules=_rules(("A(s)", "F(s)[+A(div(s,1.456))][-A(div(s,1.456))]")),
        iterations=5,
        angle=85.0,
        length=1.0,
        radius=0.01,
        note="ABOP 图 1.39：长度按 1/1.456 收缩、85° 张开，枝条互不重叠",
        source="ABOP 1.39",
    ),
)

#: 面板默认选中的预设。
DEFAULT_PRESET = "fractal_plant"

_BY_KEY = {preset.key: preset for preset in PRESETS}


def items() -> list[tuple[str, str, str]]:
    """给 ``EnumProperty`` 的静态条目（模块导入时就能算，不需要 bpy）。"""
    return [(preset.key, preset.name, preset.note) for preset in PRESETS]


def by_key(key: str) -> Preset | None:
    """按 key 取预设；没有就返回 ``None``（面板上还留着旧值时）。"""
    return _BY_KEY.get(key)


def default() -> Preset:
    """默认预设（规则为空时由 :func:`gen_lsystem.builder.rebuild` 兜底套用）。"""
    return _BY_KEY[DEFAULT_PRESET]


def apply_to(settings, preset: Preset) -> int:
    """把一套预设写进参数组（规则集合先清空再重建），返回写了几项。

    这里只碰属性，不 import bpy：``settings`` 是 ``scene.gen_lsystem``，规则集合是
    ``CollectionProperty``，纯属性赋值就能填好。触发重算 / 挂起自动更新是调用方的事
    （见 :func:`gen_lsystem.props.apply_preset`）。
    """
    settings.axiom = preset.axiom
    settings.dimension = preset.dimension
    settings.iterations = preset.iterations
    settings.angle = math.radians(preset.angle)
    settings.length = preset.length
    settings.radius = preset.radius
    settings.radius_decay = preset.radius_decay
    settings.radius_fat = preset.radius_fat
    settings.tropism = preset.tropism

    settings.rules.clear()
    for pattern, condition, result in preset.rules:
        item = settings.rules.add()
        item.pattern = pattern
        item.condition = condition
        item.result = result
    return 1 + 8 + len(preset.rules)
