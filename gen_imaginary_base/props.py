"""``scene.imaginarybase``：一次生成所需的全部输入，以及它们的联动规则。

这一层只做三件事：**存参数**、**把参数变成环元素**、**在参数变化时通知预览 / HUD**。
真正的计算都在 :mod:`ring`（纯算法）与 :mod:`build`（写 GP）里。

几个刻意的设计：

* 底数与数字都存成 ``(a, b)`` 两个整数（而不是字符串）—— HUD 拖动时每帧都在改它们，
  走字符串要来回解析，而且会丢掉"这个格点本来在哪"。
* ``places``（位数）有两条上限：滑杆的 16，以及点数预算算出来的那条。改数字集时自动
  往下夹一次，否则把 4 个数字换成 40 个数字会瞬间炸出几百万个点。
* 点集算一次就缓存（``_POINT_CACHE``）：HUD 每帧都要画它，但只有参数变了才需要重算。
  缓存键里带着全部影响结果的字段，所以不会出现"改了颜色却拿到旧点集"这种问题。
"""

from __future__ import annotations

import bpy
import numpy as np

from . import presets as _presets
from . import ring
from .i18n import t, tfmt

__all__ = [
    "DigitItem",
    "ImaginaryBaseSettings",
    "register",
    "unregister",
]

#: 点数预算的硬边界。下限是不让画面空掉，上限是 GP 的笔划数（一个点一笔）。
BUDGET_MIN = 512
BUDGET_MAX = 120_000
#: 默认点数预算：GP 一个点一条笔划，两万笔在视口里还能拖动。
BUDGET_DEFAULT = 8_000
#: 点半径（场景单位，格距为 1）。参考项目画的其实是"每个整数一个格子"（边长约 0.9），
#: 所以默认值取 0.42 —— 相邻格点上的点刚好挨在一起连成面，形状才读得出来；
#: 调小它就会退化成一层散点。
RADIUS_DEFAULT = 0.42

#: 最近一次算好的点集：``key -> ring.point_set(...)`` 的结果。
_POINT_CACHE: dict = {}
_CACHE_LIMIT = 8


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _settings(context=None):
    context = context or bpy.context
    scene = getattr(context, "scene", None)
    return getattr(scene, "imaginarybase", None) if scene is not None else None


def _tag_redraw() -> None:
    """参数变了：点集缓存作废，预览与 HUD 都重画。"""
    _POINT_CACHE.clear()
    try:
        from . import hud, preview

        preview.tag_redraw()
        hud.tag_redraw()
    except (ImportError, AttributeError):
        pass


def _mark_dirty(self, context=None) -> None:
    _tag_redraw()


def _clamp_places(settings) -> None:
    """把位数夹到「点数预算装得下」的范围里（改数字集 / 改预算时调用）。"""
    count = len(settings.digits)
    if count < 2:
        return
    ceiling = ring.max_places(count, settings.point_budget)
    settings.places = min(settings.places, ceiling)


# ---------------------------------------------------------------------------
# 数字
# ---------------------------------------------------------------------------


class DigitItem(bpy.types.PropertyGroup):
    """数字集里的一个数字 ``a + b·ω``。"""

    a: bpy.props.IntProperty(
        name="A",
        description="Real part (coefficient of 1)",
        default=0,
        soft_min=-16,
        soft_max=16,
        update=_mark_dirty,
    )

    b: bpy.props.IntProperty(
        name="B",
        description="Coefficient of the imaginary unit (i or omega)",
        default=0,
        soft_min=-16,
        soft_max=16,
        update=_mark_dirty,
    )


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------


def _on_preset(self, context=None) -> None:
    """选了一条预设：把底数与数字整体换掉（之后照样可以手动改）。"""
    data = _presets.preset_by_name(self.preset)
    if data is None:
        return
    self.domain = data["domain"]
    self.base_a, self.base_b = data["base"]
    self.digits.clear()
    for a, b in data["digits"]:
        item = self.digits.add()
        item.a, item.b = a, b
    self.colour_mode = data["colour_mode"]
    self.colour_place = data["colour_place"]
    _clamp_places(self)
    _tag_redraw()


# ---------------------------------------------------------------------------
# 设置组
# ---------------------------------------------------------------------------


class ImaginaryBaseSettings(bpy.types.PropertyGroup):
    """``scene.imaginarybase``。"""

    # --- 环 ---------------------------------------------------------------

    domain: bpy.props.EnumProperty(
        name="Ring",
        description="Which quadratic integer ring the base lives in",
        items=[
            ("I", "i  (ω² = −1)", "Gaussian integers: square lattice", "MESH_GRID", 0),
            (
                "W",
                "ω  (ω² = −1−ω)",
                "Eisenstein integers: triangular lattice",
                "MESH_GRID",
                1,
            ),
        ],
        default="I",
        update=_mark_dirty,
    )

    # --- 底数 -------------------------------------------------------------

    base_a: bpy.props.IntProperty(
        name="Base Real",
        description="Real part of the base (the scaling factor is |base|)",
        default=0,
        soft_min=-24,
        soft_max=24,
        update=_mark_dirty,
    )

    base_b: bpy.props.IntProperty(
        name="Base Imaginary",
        description="Coefficient of the imaginary unit in the base",
        default=2,
        soft_min=-24,
        soft_max=24,
        update=_mark_dirty,
    )

    # --- 数字集 -----------------------------------------------------------

    digits: bpy.props.CollectionProperty(type=DigitItem)
    active_digit: bpy.props.IntProperty(name="Active Digit", default=0, min=0)

    preset: bpy.props.EnumProperty(
        name="Preset",
        description="Classic numeral systems, ported from TheGrayCuber's imaginary_bases",
        items=_presets.PRESET_ENUM_ITEMS,
        update=_on_preset,
    )

    # --- 位数与上色 -------------------------------------------------------

    places: bpy.props.IntProperty(
        name="Places",
        description="How many base-b places to enumerate; the point count is |digits|^places "
        "(capped by the point budget)",
        default=6,
        min=1,
        max=ring.MAX_PLACES,
        update=_mark_dirty,
    )

    colour_mode: bpy.props.EnumProperty(
        name="Colour By",
        description="How each integer in the point set is coloured",
        items=[
            (
                "AGGREGATE",
                "Aggregate",
                (
                    "Colour by the leading digit, with the lower places mixed in — every "
                    "digit shows up again at every scale"
                ),
            ),
            ("LEAD", "Leading Digit", "One flat colour per leading digit"),
            ("PLACE", "Place", "One flat colour per digit at a chosen place"),
        ],
        default="AGGREGATE",
        update=_mark_dirty,
    )

    colour_place: bpy.props.IntProperty(
        name="Place",
        description="Which place to colour by in Place mode (1 = least significant)",
        default=1,
        min=1,
        max=ring.MAX_PLACES,
        update=_mark_dirty,
    )

    # --- 输出 -------------------------------------------------------------

    point_budget: bpy.props.IntProperty(
        name="Point Budget",
        description="Upper bound on the number of points; each point is one grease pencil "
        "stroke, so this is the knob that decides whether the viewport stays smooth",
        default=BUDGET_DEFAULT,
        min=BUDGET_MIN,
        max=BUDGET_MAX,
        update=lambda self, context: (_clamp_places(self), _tag_redraw()),
    )

    dot_radius: bpy.props.FloatProperty(
        name="Dot Radius",
        description="Radius of one dot in scene units (the lattice spacing is 1)",
        default=RADIUS_DEFAULT,
        min=0.005,
        max=0.6,
        step=1,
        precision=3,
        update=_mark_dirty,
    )

    # --- 预览 -------------------------------------------------------------

    show_preview: bpy.props.BoolProperty(
        name="Viewport Preview",
        description="Draw the point set directly in the viewport while you edit, without "
        "creating any object",
        default=True,
        update=_mark_dirty,
    )

    preview_colour: bpy.props.FloatVectorProperty(
        name="Preview Colour",
        description="Point colour in the viewport preview (sRGB)",
        size=3,
        subtype="COLOR_GAMMA",
        default=(0.784, 0.353, 0.294),
        min=0.0,
        max=1.0,
        update=_mark_dirty,
    )

    notice: bpy.props.StringProperty(name="Notice", default="")

    # --- 派生 -------------------------------------------------------------

    def base(self) -> ring.QInt:
        return ring.QInt(self.base_a, self.base_b, self.domain)

    def digit_list(self) -> list[tuple[int, int]]:
        return [(item.a, item.b) for item in self.digits]

    def digit_qints(self) -> list[ring.QInt]:
        return [ring.QInt(item.a, item.b, self.domain) for item in self.digits]

    def canonical(self) -> dict:
        """当前数系是否有效（见 :func:`ring.canonical_check`）。"""
        return ring.canonical_check(self.domain, self.base(), self.digit_qints())

    def point_key(self) -> tuple:
        """点集缓存的键：**所有影响几何与上色的字段**都得进来。"""
        return (
            self.domain,
            self.base_a,
            self.base_b,
            tuple(self.digit_list()),
            int(self.places),
            int(self.point_budget),
            self.colour_mode,
            int(self.colour_place),
        )

    def point_data(self):
        """当前点集（带缓存）。参数非法时返回空集，不抛异常 —— HUD 每帧都要画它。"""
        key = self.point_key()
        cached = _POINT_CACHE.get(key)
        if cached is not None:
            return cached
        digits = self.digit_list()
        if len(digits) < 2 or (self.base_a == 0 and self.base_b == 0):
            empty = {
                "xy": np.zeros((0, 2), np.float32),
                "slot": np.zeros(0, np.int32),
                "shade": np.zeros(0, np.float32),
                "places": 0,
                "count": 0,
                "digit_count": len(digits),
            }
            return empty
        data = ring.point_set(
            self.domain,
            (self.base_a, self.base_b),
            digits,
            int(self.places),
            limit=int(self.point_budget),
            colour_mode=self.colour_mode,
            colour_place=int(self.colour_place),
        )
        if len(_POINT_CACHE) >= _CACHE_LIMIT:
            _POINT_CACHE.clear()
        _POINT_CACHE[key] = data
        return data

    def summary(self) -> str:
        """面板底部那一行状态：位数、点数、是否有效数系。"""
        data = self.point_data()
        check = self.canonical()
        pieces = [
            tfmt(
                "places {places} · {count} points",
                places=data["places"],
                count=data["count"],
            )
        ]
        if check["ok"]:
            pieces.append(t("canonical numeral system"))
        elif check["reason"]:
            pieces.append(check["reason"])
        return "  |  ".join(piece for piece in pieces if piece)

    # --- 编辑 -------------------------------------------------------------

    def apply_digits(self, pairs) -> None:
        """整体替换数字集（HUD 加 / 删一个数字时用，保留其余部分）。"""
        self.digits.clear()
        for a, b in pairs:
            item = self.digits.add()
            item.a, item.b = a, b
        _clamp_places(self)
        _tag_redraw()

    def set_base(self, a: int, b: int) -> None:
        self.base_a, self.base_b = int(a), int(b)

    def move_digit(self, index: int, a: int, b: int) -> bool:
        """把第 ``index`` 个数字挪到 ``(a, b)``；撞上别的数字或引起零向量时返回 False。

        参考实现允许任意拖动、撞了就画重影；这里拒掉重复 —— 重影在点集上就是"两个数字
        同一个位置"，除了让图糊掉没有任何好处，而"是否有效数系"那行提示会一直红着。
        """
        if not 0 <= index < len(self.digits):
            return False
        target = (int(a), int(b))
        if index == 0:
            target = (0, 0)  # 0 号数字恒为零：数系里必须有 0，否则原点不在集合里
        for other, item in enumerate(self.digits):
            if other == index:
                continue
            if (item.a, item.b) == target:
                return False
        item = self.digits[index]
        item.a, item.b = target
        _clamp_places(self)
        _tag_redraw()
        return True


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------


def register():
    bpy.types.Scene.imaginarybase = bpy.props.PointerProperty(
        type=ImaginaryBaseSettings
    )


def unregister():
    # 幂等：重载插件或开发时手动 unregister 之后再被注销一次，不该炸
    if hasattr(bpy.types.Scene, "imaginarybase"):
        del bpy.types.Scene.imaginarybase


def bootstrap_default() -> None:
    """首次启用时铺一套能看的默认值（只在一个数字都没有时动手）。"""
    scene = getattr(bpy.context, "scene", None)
    settings = getattr(scene, "imaginarybase", None) if scene is not None else None
    if settings is None or len(settings.digits):
        return
    data = _presets.parse_preset("base 2i with digits 0, 1, i, and -1+i color by 1st")
    if data is None:
        return
    settings.domain = data["domain"]
    settings.base_a, settings.base_b = data["base"]
    settings.digits.clear()
    for a, b in data["digits"]:
        item = settings.digits.add()
        item.a, item.b = a, b
    settings.places = 6
    settings.colour_mode = "AGGREGATE"
