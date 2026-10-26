"""场景参数：``scene.gen_zellij``。

面板上所有可调项都在这里，算子只读这套值，渲染后端只认算子算好的宽度。
**不要**加 ``from __future__ import annotations``：``bpy.props`` 的注解必须是
真实的 ``Property`` 对象，PEP 563 的字符串注解会让注册失败。
"""

import bpy

from . import gp
from .design import FOCUS_VALUES
from .rng import SEED_MAX
from .scene import LAYER_FILLS, LAYER_LINES, LAYER_OUTLINES, LAYER_TILING

__all__ = [
    "DENSITY_ITEMS",
    "DESIGN_KEYS",
    "FOCUS_LABELS",
    "LAYER_VISIBILITY",
    "GenZellijSettings",
    "focus_items",
    "register",
    "unregister",
]

#: Density 档位（面板顺序）。值以字符串存进枚举，取用时 ``int()``。
DENSITY_ITEMS = [
    ("4", "4", "极疏：4×4 交点网格"),
    ("6", "6", "疏：6×6"),
    ("8", "8", "偏疏：8×8"),
    ("10", "10", "标准：10×10（默认）"),
    ("12", "12", "偏密：12×12"),
    ("16", "16", "密：16×16"),
    ("20", "20", "很密：20×20"),
    ("24", "24", "极密：24×24，生成约 0.5 s"),
]

#: 写到生成对象上的自定义属性（换文件/换场景后仍能取回参数）。
DESIGN_KEYS = (
    "gen_zellij_seed",
    "gen_zellij_density",
    "gen_zellij_focus",
    "gen_zellij_shimmer",
    "gen_zellij_canvas",
    "gen_zellij_margin",
    "gen_zellij_outline_radius",
)

#: 焦点特征的**显示名**：标识（存进文件、写进对象参数）保持 ASCII 稳定，
#: 下拉里显示更直观的写法。
FOCUS_LABELS = {
    "Block3x3": "Block 3×3",
    "Block4x4": "Block 4×4",
}

#: 图层 → 控制可见性的属性名（四个图层永远都写进对象，只是隐藏与否）。
LAYER_VISIBILITY = {
    LAYER_FILLS: "show_fills",
    LAYER_OUTLINES: "show_outlines",
    LAYER_TILING: "show_tiling",
    LAYER_LINES: "show_lines",
}


def focus_items(self, context):
    descriptions = {
        "None": "不强制焦点特征（75% 的随机结果都是这样）",
        "Eight": "2×2 的八线焦点（要求 density ≥ 6）",
        "Sixteen": "星形十六线焦点（要求 density ≥ 6）",
        "Block3x3": "把 3×3 共 9 块相邻瓷砖并成一块空心域（并块后至少留 3 块正常瓷砖）",
        "Block4x4": "把 4×4 共 16 块相邻瓷砖并成一块空心域",
    }
    return [
        (name, FOCUS_LABELS.get(name, name), descriptions.get(name, ""))
        for name in FOCUS_VALUES
    ]


def _mark_dirty(self, context):
    """几何参数变了：设计必须重建（会改坐标的东西都在这一档）。"""
    self.dirty = True


def _apply_stroke_widths(self, context):
    """轮廓/瓷砖/构造线的线宽不影响几何，直接就地改半径，不重建设计。"""
    obj = gp.find_object()
    if obj is None:
        return
    gp.set_layer_radius(obj, LAYER_OUTLINES, self.outline_radius)
    gp.set_layer_radius(obj, LAYER_TILING, self.tiling_radius)
    gp.set_layer_radius(obj, LAYER_LINES, self.line_radius)
    if DESIGN_KEYS[6] in obj:
        # 线宽不进几何，但对象上记录的参数要跟着更新，回读才不会拿到旧值
        obj[DESIGN_KEYS[6]] = float(self.outline_radius)


def _apply_visibility(self, context):
    """图层显隐只是 ``layer.hide``，不重建任何东西。"""
    obj = gp.find_object()
    if obj is None:
        return
    for layer_name, prop_name in LAYER_VISIBILITY.items():
        gp.set_layer_visible(obj, layer_name, getattr(self, prop_name))


def _apply_dash(self, context):
    obj = gp.find_object()
    if obj is None:
        return
    modifier = obj.modifiers.get(gp.DASH_MODIFIER_NAME)
    if modifier is not None:
        modifier.show_viewport = bool(self.dashed)
        modifier.show_render = bool(self.dashed)


class GenZellijSettings(bpy.types.PropertyGroup):
    """``scene.gen_zellij``：一次生成所需的全部输入。"""

    # --- 设计 -------------------------------------------------------------

    seed: bpy.props.IntProperty(  # type: ignore[valid-type]
        name="种子",
        description="同一 seed + 同一参数必然生成同一张图；改它就换一张",
        default=1,
        min=0,
        # IntProperty 是 32 位有符号，这里跟 rng.SEED_MAX 对齐，越界赋值会抛 ValueError
        max=SEED_MAX,
        soft_max=99999,
    )

    random_seed: bpy.props.BoolProperty(  # type: ignore[valid-type]
        name="每次换种子",
        description="每次生成都抽一个新种子（抽到的值会写回种子框，便于复现）",
        default=False,
    )

    density: bpy.props.EnumProperty(  # type: ignore[valid-type]
        name="密度",
        description="构造线密度：同时决定交点网格边长与保留的构造线条数",
        items=DENSITY_ITEMS,
        default="10",
        update=_mark_dirty,
    )

    focus: bpy.props.EnumProperty(  # type: ignore[valid-type]
        name="焦点",
        description="强制出现的焦点特征（随机结果多半没有焦点）",
        items=focus_items,
        update=_mark_dirty,
    )

    shimmer: bpy.props.BoolProperty(  # type: ignore[valid-type]
        name="颜色抖动",
        description="对 2 号以后的色板做明度抖动：同一基色出现多种相近色",
        default=False,
        update=_mark_dirty,
    )

    canvas: bpy.props.FloatProperty(  # type: ignore[valid-type]
        name="画布",
        description="设计画布边长（世界单位），图案正好落在这个正方形内",
        default=10.0,
        min=0.01,
        soft_max=100.0,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_mark_dirty,
    )

    margin: bpy.props.FloatProperty(  # type: ignore[valid-type]
        name="边距",
        description="画布内留白（世界单位）；0 = 图案贴住原点",
        default=0.0,
        min=0.0,
        soft_max=2.0,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_mark_dirty,
    )

    # --- 图层 -------------------------------------------------------------

    show_fills: bpy.props.BoolProperty(  # type: ignore[valid-type]
        name="填充图案",
        description="成品马赛克：每条填充形状就是一块色块（只有填充，不带描边）",
        default=True,
        update=_apply_visibility,
    )

    show_outlines: bpy.props.BoolProperty(  # type: ignore[valid-type]
        name="块轮廓",
        description="与填充同一套几何的描边层（颜色由填充色派生）；可以单独隐藏或替换",
        default=True,
        update=_apply_visibility,
    )

    show_tiling: bpy.props.BoolProperty(  # type: ignore[valid-type]
        name="瓷砖分界线",
        description="去重后的瓷砖边网络（每条分界线只画一次）",
        default=False,
        update=_apply_visibility,
    )

    show_lines: bpy.props.BoolProperty(  # type: ignore[valid-type]
        name="构造线",
        description="裁到拼贴区凸包内的构造线，等弧长采样，可虚线化",
        default=False,
        update=_apply_visibility,
    )

    # --- 线宽与配色 -------------------------------------------------------

    outline_radius: bpy.props.FloatProperty(  # type: ignore[valid-type]
        name="块轮廓宽",
        description="块轮廓的笔划半径（世界单位，总宽 = 2×半径）；不影响几何，改它不用重新生成",
        default=0.01,
        min=0.0,
        precision=4,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_apply_stroke_widths,
    )

    tiling_radius: bpy.props.FloatProperty(  # type: ignore[valid-type]
        name="瓷砖线宽",
        description="瓷砖分界线的笔划半径，改它不用重新生成",
        default=0.006,
        min=0.0,
        precision=4,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_apply_stroke_widths,
    )

    line_radius: bpy.props.FloatProperty(  # type: ignore[valid-type]
        name="构造线宽",
        description="构造细线的笔划半径，改它不用重新生成",
        default=0.0035,
        min=0.0,
        precision=4,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_apply_stroke_widths,
    )

    tiling_color: bpy.props.FloatVectorProperty(  # type: ignore[valid-type]
        name="瓷砖线色",
        description="瓷砖分界线的颜色（默认红，当构造视图看）",
        size=3,
        subtype="COLOR_GAMMA",
        default=(0.85, 0.12, 0.12),
        min=0.0,
        max=1.0,
        update=_mark_dirty,
    )

    line_color: bpy.props.FloatVectorProperty(  # type: ignore[valid-type]
        name="构造线色",
        description="构造线的颜色（默认浅灰，深色画布上也看得见）",
        size=3,
        subtype="COLOR_GAMMA",
        default=(0.92, 0.92, 0.92),
        min=0.0,
        max=1.0,
        update=_mark_dirty,
    )

    dashed: bpy.props.BoolProperty(  # type: ignore[valid-type]
        name="Dot Dash 修改器",
        description="用 Dot Dash 修改器处理构造线；图案见 gp.DASH_PATTERN，"
        "默认 (2, 0) = 1:1 虚线，(1, 0) 则是实线",
        default=True,
        update=_apply_dash,
    )

    # --- 状态（只读） -----------------------------------------------------

    dirty: bpy.props.BoolProperty(  # type: ignore[valid-type]
        name="待更新",
        description="参数改过但还没重新生成",
        default=True,
    )

    last_seed: bpy.props.IntProperty(  # type: ignore[valid-type]
        name="上次种子",
        default=-1,
    )

    last_shapes: bpy.props.IntProperty(  # type: ignore[valid-type]
        name="形状数",
        default=0,
    )

    last_ms: bpy.props.FloatProperty(  # type: ignore[valid-type]
        name="耗时",
        default=0.0,
    )

    last_layers: bpy.props.StringProperty(  # type: ignore[valid-type]
        name="分层",
        default="",
    )

    last_note: bpy.props.StringProperty(  # type: ignore[valid-type]
        name="提示",
        default="",
    )

    error: bpy.props.StringProperty(  # type: ignore[valid-type]
        name="错误",
        default="",
    )

    # --- 派生量 -----------------------------------------------------------

    def design_params(self) -> dict:
        """转成 ``build_design`` 的关键字参数。"""
        return {
            "density": int(self.density),
            "focus": None if self.focus == "None" else self.focus,
            "shimmer": bool(self.shimmer),
        }

    def layer_widths(self) -> dict:
        """``{图层名: 笔划半径}``；填充层不画描边，半径给 0。"""
        return {
            LAYER_FILLS: 0.0,
            LAYER_OUTLINES: float(self.outline_radius),
            LAYER_TILING: float(self.tiling_radius),
            LAYER_LINES: float(self.line_radius),
        }

    def layer_colors(self) -> dict:
        """调试图层（Tiling / Lines）的描边色覆盖。"""
        return {
            LAYER_TILING: tuple(self.tiling_color),
            LAYER_LINES: tuple(self.line_color),
        }


def register():
    bpy.types.Scene.gen_zellij = bpy.props.PointerProperty(type=GenZellijSettings)


def unregister():
    del bpy.types.Scene.gen_zellij
