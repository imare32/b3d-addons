"""侧栏（N 面板）「L-System」：生成按钮 + 预设 + 规则 + 解释参数 + 状态。

面板只读 ``scene.gen_lsystem`` 与生成对象上的自定义属性，不做任何几何计算；
「待重新生成」也是从 ``settings.dirty`` 来的，不是在绘制时去比对参数 —— 画界面不该有副作用。
"""

from __future__ import annotations

import bpy

from . import gp, presets
from .ops import (
    GENLS_OT_apply_preset,
    GENLS_OT_delete,
    GENLS_OT_generate,
    GENLS_OT_rule_add,
    GENLS_OT_rule_remove,
    GENLS_OT_setup_scene,
    GENLS_OT_sync_from_object,
)

__all__ = ["GENLS_PT_panel", "GENLS_UL_rules"]

#: 侧栏标签页名。
CATEGORY = "L-System"

#: 符号速查（面板底部摊开，省得每次都翻 README）。
SYMBOLS = (
    "F G 前进并画线　f 只前进　+ - 左右转　^ & 俯仰　\\ / 翻滚",
    "| 掉头　$ 扶正　[ ] 分支　! 变细　# 变粗　¤ 设半径　s 缩放　£ 随机角　% 剪断",
)


def _box(layout, label: str, icon: str = "NONE"):
    """一个盒子：首行是标题，后面照 ``GENLS_PT_panel`` 的排版写属性。"""
    box = layout.box()
    box.use_property_split = True
    box.use_property_decorate = False
    box.label(text=label, icon=icon)
    return box


def _status(layout, settings, obj) -> None:
    box = _box(layout, "状态", "INFO")

    if settings.auto_update:
        box.label(text="自动更新：改参数即刻重算", icon="PLAY")
    else:
        box.label(text="自动更新已关（改完点「生成」）", icon="INFO")

    if settings.error:
        box.label(text=settings.error, icon="ERROR")
    elif obj is None:
        box.label(text="还没有生成对象，点「生成」建一个", icon="INFO")
    elif settings.dirty:
        box.label(text="参数改过，正在自动更新…", icon="TIME")
    else:
        box.label(text="已生成", icon="CHECKMARK")

    if obj is None:
        return

    box.label(text=f"{settings.last_paths} 笔 · {settings.last_points} 点", icon="PRESET")
    box.label(
        text=f"{settings.last_symbols} 模块 · {settings.last_ms:.0f} ms · "
        f"深度 {settings.last_depth}",
        icon="TIME",
    )
    if settings.last_note:
        box.label(text=settings.last_note, icon="INFO")


def _preset(layout, settings) -> None:
    box = _box(layout, "预设", "PRESET")
    box.prop(settings, "preset", text="")
    box.operator(GENLS_OT_apply_preset.bl_idname, icon="IMPORT")
    preset = presets.by_key(settings.preset)
    if preset is None:
        return
    column = box.column(align=True)
    column.label(text=preset.note, icon="DOT")
    column.label(text=preset.source, icon="NONE")


def _system(layout, settings) -> None:
    box = _box(layout, "系统", "FILE_TEXT")
    box.prop(settings, "axiom")
    box.prop(settings, "dimension")
    box.prop(settings, "iterations")
    box.prop(settings, "seed")


def _rules(layout, settings) -> None:
    box = _box(layout, "产生式", "DRIVER")
    row = box.row()
    row.template_list(
        GENLS_UL_rules.bl_idname,
        "",
        settings,
        "rules",
        settings,
        "active_rule",
        rows=4,
    )
    column = row.column(align=True)
    column.operator(GENLS_OT_rule_add.bl_idname, icon="ADD", text="")
    column.operator(GENLS_OT_rule_remove.bl_idname, icon="REMOVE", text="")

    index = int(settings.active_rule)
    if 0 <= index < len(settings.rules):
        item = settings.rules[index]
        detail = box.column()
        detail.use_property_split = True
        detail.use_property_decorate = False
        detail.prop(item, "condition")
        detail.prop(item, "result")
        box.label(text="左侧在列表里直接改；条件留空 = 永远成立", icon="INFO")
    else:
        box.label(text="还没有规则：右上「应用预设」填一套，或点 + 手写", icon="INFO")


def _interpretation(layout, settings) -> None:
    box = _box(layout, "解释", "CON_FOLLOWPATH")
    box.prop(settings, "angle")
    box.prop(settings, "length")
    box.prop(settings, "radius")
    box.prop(settings, "radius_decay")
    box.prop(settings, "radius_fat")
    box.prop(settings, "tropism")
    box.label(text="! / # / ¤ 改的是几何（逐点半径）；向重力 0 = 关", icon="INFO")


def _look(layout, settings) -> None:
    box = _box(layout, "外观（就地）", "LINE_DATA")
    box.prop(settings, "width_scale")
    box.prop(settings, "colour")
    box.prop(settings, "gradient")
    column = box.column()
    column.enabled = settings.gradient != "NONE"
    column.prop(settings, "tip_colour")
    box.prop(settings, "opacity")
    box.label(text="这一组只重写 GP 属性，拖滑块是实时的", icon="INFO")


def _tools(layout, settings) -> None:
    box = _box(layout, "工具", "TOOL_SETTINGS")
    box.operator(GENLS_OT_setup_scene.bl_idname, icon="CAMERA_DATA")
    box.operator(GENLS_OT_sync_from_object.bl_idname, icon="IMPORT")
    box.operator(GENLS_OT_delete.bl_idname, icon="TRASH")


def _interface(layout, settings) -> None:
    box = _box(layout, "数据接口与符号", "PROPERTIES")
    box.label(text="POINT 域：radius / base_radius / taper / depth / vertex_color", icon="NONE")
    for line in SYMBOLS:
        box.label(text=line, icon="NONE")


class GENLS_UL_rules(bpy.types.UIList):
    """规则列表：左侧 → 右侧；带条件的条目右上角有个小标记。"""

    bl_idname = "GENLS_UL_rules"

    def draw_item(
        self, context, layout, data, item, icon, active_data, active_propname, index
    ):
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            row = layout.row(align=True)
            row.prop(item, "pattern", text="", emboss=False)
            row.label(text="→")
            row.prop(item, "result", text="", emboss=False)
            if item.condition.strip():
                row.label(text="", icon="DRIVER")
        else:
            layout.alignment = "CENTER"
            layout.label(text=item.label())


class GENLS_PT_panel(bpy.types.Panel):
    """侧栏面板。"""

    bl_idname = "GENLS_PT_panel"
    bl_label = "Gen L-System"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY

    def draw(self, context):
        settings = context.scene.gen_lsystem
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        head = layout.row(align=True)
        head.scale_y = 1.4
        head.operator(GENLS_OT_generate.bl_idname, icon="PLAY", text="生成")
        # 自动更新：几何参数一改就重算，不用再点「生成」
        head.prop(settings, "auto_update", text="自动", toggle=True)

        _status(layout, settings, gp.find_object())
        _preset(layout, settings)
        _system(layout, settings)
        _rules(layout, settings)
        _interpretation(layout, settings)
        _look(layout, settings)
        _tools(layout, settings)
        _interface(layout, settings)
