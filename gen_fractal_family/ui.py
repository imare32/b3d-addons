"""侧栏（N 面板）「Fractal」：生成按钮 + 基因列表 + 预览设置 + 状态。

面板只读 ``scene.gen_fractal_family``，不做几何计算（预览与生成各自有入口）。
基因编辑有两条路，面板里都摆着：

* **HUD**（推荐）：点面板顶部的「HUD 编辑」，在视口里拖节点、点 R/M；
* **列表**（精确 / 批量）：直接填 A、B 两个整数，或者复制粘贴一整条基因字符串。
"""

from __future__ import annotations

import bpy

from . import build, hud, preview
from .lattice import estimate_points
from .ops import (
    GFF_OT_copy_gene,
    GFF_OT_delete,
    GFF_OT_generate,
    GFF_OT_hud,
    GFF_OT_item_add,
    GFF_OT_item_move,
    GFF_OT_item_remove,
    GFF_OT_paste_gene,
    GFF_OT_set_domain,
    GFF_OT_setup_scene,
    GFF_OT_sync_from_object,
)
from .props import PREVIEW_MAX_LEVEL

__all__ = ["GFF_PT_panel", "GFF_UL_items"]

#: 侧栏标签页名（短一点，和其他插件的单行标签并列时不挤）。
CATEGORY = "Fractal"

#: 列表的 idname。
LIST_IDNAME = "GFF_UL_items"


def _box(layout, title: str, icon: str = "NONE"):
    """一个盒子：首行是标题，后面照面板的排版写属性。"""
    box = layout.box()
    box.use_property_split = True
    box.use_property_decorate = False
    box.label(text=title, icon=icon)
    return box


class GFF_UL_items(bpy.types.UIList):
    """基因列表：每行是一段（A、B + 两个变换标记）。"""

    bl_idname = LIST_IDNAME

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index, flt_flag):
        row = layout.row(align=True)
        row.label(text=f"#{index + 1:2d}")
        row.prop(item, "a", text="")
        row.prop(item, "b", text="")
        row.separator()
        row.prop(
            item, "reverse", text="", emboss=False,
            icon="UV_SYNC_SELECT" if item.reverse else "PROP_OFF",
        )
        row.prop(
            item, "mirror", text="", emboss=False,
            icon="MOD_MIRROR" if item.mirror else "PROP_OFF",
        )


def _header(layout, settings):
    head = layout.row(align=True)
    head.scale_y = 1.5
    head.operator(GFF_OT_generate.bl_idname, icon="PLAY", text="生成")
    head.prop(settings, "auto_update", text="自动", toggle=True)


def _hud(layout, settings):
    box = _box(layout, "HUD 编辑", "GREASEPENCIL")
    row = box.row()
    row.scale_y = 1.4
    row.operator(GFF_OT_hud.bl_idname, icon="WORKSPACE",
                 text="关闭 HUD" if hud.session() else "HUD 编辑")
    box.label(text="拖节点改坐标 · R/M 切标记 · 滚轮换等级", icon="INFO")
    box.label(text="Tab 换域 · Shift+滚轮缩放 · F 取景")
    box.label(text="指针在面板上时才接管按键，不挡视口")


def _gene(layout, settings):
    box = _box(layout, "基因", "RNA")
    box.prop(settings, "preset", text="预设")
    # 域：两个普通按钮 + depress 标当前值。
    # 不用 prop(..., expand=True)：5.2 里展开式枚举会画成两块没有文字的按钮（标签被吞）。
    row = box.row(align=True)
    for value, text in (("G", "高斯 G"), ("E", "艾森斯坦 E")):
        row.operator(GFF_OT_set_domain.bl_idname, text=text,
                     depress=settings.domain == value).domain = value

    row = box.row()
    row.template_list(
        LIST_IDNAME, "GFF_ITEMS", settings, "items", settings, "active_index", rows=3
    )
    column = row.column(align=True)
    column.operator(GFF_OT_item_add.bl_idname, icon="ADD", text="")
    column.operator(GFF_OT_item_remove.bl_idname, icon="REMOVE", text="")
    column.separator()
    column.operator(GFF_OT_item_move.bl_idname, icon="TRIA_UP", text="").direction = "UP"
    column.operator(GFF_OT_item_move.bl_idname, icon="TRIA_DOWN", text="").direction = "DOWN"

    row = box.row(align=True)
    row.operator(GFF_OT_copy_gene.bl_idname, icon="COPYDOWN", text="复制基因")
    row.operator(GFF_OT_paste_gene.bl_idname, icon="PASTEDOWN", text="粘贴基因")
    box.label(text=settings.family_name(), icon="FILE_PARENT")

    if not settings.is_valid():
        box.label(text="基因无效：至少要两段、总向量不为零", icon="ERROR")


def _shape(layout, settings):
    box = _box(layout, "等级与输出", "MOD_SUBSURF")
    box.prop(settings, "level", slider=True)
    if settings.preview_truncated():
        box.label(text=f"视口预览只画到第 {PREVIEW_MAX_LEVEL} 级（输出仍按第 {settings.level} 级）",
                  icon="INFO")
    box.prop(settings, "spline_type", text="样条")
    box.prop(settings, "output_mode", text="输出")
    box.prop(settings, "initiator_curve", text="初始曲线")
    if settings.initiator_curve is not None:
        box.prop(settings, "initiator_reverse", text="初始曲线反向")
    else:
        box.label(text="初始曲线留空 = 从原点到总向量的一条直线", icon="INFO")


def _output(layout, settings, context):
    box = _box(layout, "生成", "OUTLINER_OB_CURVE")
    column = box.column(align=True)
    column.operator(GFF_OT_generate.bl_idname, text="生成最后一级",
                    icon="OUTLINER_DATA_CURVE").mode = "LAST_LEVEL"
    column.operator(GFF_OT_generate.bl_idname, text="生成每一级", icon="CURVES").mode = "EACH_LEVEL"
    column.operator(
        GFF_OT_generate.bl_idname, text="生成带形态键的曲线", icon="SHADERFX"
    ).mode = "SHAPE_KEYS"

    active = context.active_object
    if active is not None and active.get(build.LEVEL_KEY) is not None and active.data is not None:
        keys = getattr(active.data, "shape_keys", None)
        if keys is not None:
            box.prop(keys, "eval_time", text="形态键时间")

    row = box.row(align=True)
    row.operator(GFF_OT_sync_from_object.bl_idname, icon="IMPORT", text="回读参数")
    row.operator(GFF_OT_delete.bl_idname, icon="TRASH", text="删除生成物")


def _preview(layout, settings):
    box = _box(layout, "视口预览", "HIDE_OFF")
    box.prop(settings, "show_preview", text="显示预览")
    if not settings.show_preview:
        # 默认就是关的：不知道有这个开关的人，视口里不会凭空多出一条曲线
        box.label(text="默认关闭 · 开「自动」时会一起打开", icon="INFO")
    column = box.column()
    column.enabled = settings.show_preview
    column.prop(settings, "preview_ramp", text="配色")
    if settings.preview_ramp == "MONO":
        column.prop(settings, "preview_colour")
    if settings.show_preview:
        box.label(text="朱砂粗线 = 第 1 级（生成器 / 骨架）", icon="INFO")
    box.label(text="立即模式绘制：不创建任何临时对象", icon="CHECKMARK")


def _status(layout, settings):
    box = _box(layout, "状态", "INFO")
    if settings.error:
        box.label(text=settings.error, icon="ERROR")
    elif settings.dirty:
        box.label(text="参数改过，等待生成 / 自动更新", icon="TIME")
    else:
        box.label(text="已生成", icon="CHECKMARK")

    # 只按公式估点数（不在这里真的展开点列：面板绘制不该做几何计算）
    if settings.is_valid():
        level = settings.preview_level()
        count = estimate_points(settings.elements(), level)
        # 预览关着的时候别说"预览第 N 级"，免得让人以为视口里应该有东西
        if settings.show_preview:
            box.label(text=f"预览第 {level} 级 ≈ {count:,} 点", icon="PRESET")
        else:
            box.label(text=f"第 {level} 级 ≈ {count:,} 点", icon="PRESET")
        # 自相似比：≥1 时等级越高范围越大（G/E 切域时最容易踩到）
        note = settings.contraction_note()
        if note:
            ratio = settings.contraction()
            box.label(text=f"自相似比 {ratio:.2f} · {note}",
                      icon="ERROR" if ratio >= 0.9999 else "INFO")
    # 预览绘制出错时明确说出来：draw handler 里的异常是被兜住的，不显示就成了"什么都没画"
    if settings.show_preview and preview.last_error():
        box.label(text=f"预览绘制失败：{preview.last_error()}", icon="ERROR")
    else:
        box.label(text="没有可预览的内容", icon="INFO")

    if settings.last_points:
        box.label(
            text=f"上次生成：第 {settings.last_level} 级 · {settings.last_points} 点 · "
                 f"{settings.last_ms:.0f} ms",
            icon="TIME",
        )

    row = box.row()
    row.operator(GFF_OT_setup_scene.bl_idname, icon="CAMERA_DATA", text="场景预设")


class GFF_PT_panel(bpy.types.Panel):
    """侧栏面板。"""

    bl_idname = "GFF_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY
    bl_label = "Fractal Family"

    def draw(self, context):
        settings = context.scene.gen_fractal_family
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        _header(layout, settings)
        _hud(layout, settings)
        _gene(layout, settings)
        _shape(layout, settings)
        _output(layout, settings, context)
        _preview(layout, settings)
        _status(layout, settings)

        # 等级的行内提示：从面板上直接能看出"这一级有多重"
        if settings.is_valid() and settings.level > PREVIEW_MAX_LEVEL:
            count = len(settings.items) ** int(settings.level)
            if count > 2_000_000:
                layout.label(text=f"这一级约 {count:,} 点，生成会很慢", icon="ERROR")
