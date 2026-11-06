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
from .props import INITIATOR_SIDES, PREVIEW_MAX_LEVEL

__all__ = ["GFF_PT_panel", "GFF_UL_items"]

#: 侧栏标签页名（短一点，和其他插件的单行标签并列时不挤）。
CATEGORY = "Fractal"

#: 列表的 idname。
LIST_IDNAME = "GFF_UL_items"


def _box(layout, title: str, icon: str = "NONE"):
    """一个盒子：首行是标题，后面照面板的排版写属性。

    **关掉属性分栏**（``use_property_split``）：分栏会把左边约 40% 让给标签列，面板只有
    300 px 宽时标签只用掉一小截、剩下全是空白，控件被挤到只剩半行（长一点的名字就截成
    ``E(3, 0) Snowfl...``）。整个面板统一成「标签: 控件」的内联样式，标签紧贴左边缘、
    控件紧跟在它后面，横向空间全给控件。
    """
    box = layout.box()
    box.use_property_split = False
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
    # 等级用**普通数值控件**，不用 ``slider=True``：数值框两端带 ‹ › 步进箭头
    # （**鼠标悬停时才画**，所以截图里看不到 —— 别当成没生效），点一下 ±1，正好是
    # 等级这种整数步长；滑块（numslider）画出来是一条色带，压根没有箭头，只能拖。
    box.prop(settings, "level")
    if settings.preview_truncated():
        box.label(text=f"视口预览只画到第 {PREVIEW_MAX_LEVEL} 级（输出仍按第 {settings.level} 级）",
                  icon="INFO")
    # 样条 / 输出：**展开式**枚举（分段按钮）——只有两三个选项，展开比下拉少一次点击，
    # 当前值也不用点开才看得见。注意**别传 ``text=""``**：传了空标签，每个按钮的文字会被
    # 吞掉、只剩一片蓝色高亮（5.2 实测）；段数多于两个时也别放回分栏布局，否则会竖着堆。
    for name in ("spline_type", "output_mode"):
        row = box.row(align=True)
        row.prop(settings, name, expand=True)
    # 初始折线：**直线一行、正多边形一行**（现算，不用建曲线）。
    # 多边形那行用短标签「3 边 / 4 边 / …」：五个选项平分一行只有 49 px，
    # 「正三角形」这类四个字的显示名会被截成「正三…」；当前值照样高亮。
    box.label(text="初始折线")
    column = box.column(align=True)
    column.enabled = not settings.initiator_custom
    column.row(align=True).prop_enum(settings, "initiator_shape", "LINE", text="直线")
    polygons = column.row(align=True)
    for identifier, sides in INITIATOR_SIDES.items():
        polygons.prop_enum(settings, "initiator_shape", identifier, text=f"{sides} 边")
    # 自定义曲线是另一条路（用现成的曲线对象），单独一个勾选框；
    # 勾上时上面那列变灰、不再生效，取消勾选就回到原来的形状
    box.prop(settings, "initiator_custom")
    if settings.initiator_custom:
        box.prop(settings, "initiator_curve", text="曲线")
        if settings.initiator_curve is None:
            box.label(text="还没选曲线：当前等于直线", icon="ERROR")
    elif settings.initiator_shape == "LINE":
        box.label(text="直线 = 从原点到总向量", icon="INFO")
    else:
        # 这一行只写得下十来个字：边长规则放枚举的悬停说明里，别再往这里塞
        sides = INITIATOR_SIDES.get(settings.initiator_shape, 0)
        box.label(text=f"{sides} 边形 · 闭合", icon="MOD_SIMPLIFY")
    if settings.initiator_custom or settings.initiator_shape != "LINE":
        box.prop(settings, "initiator_reverse", text="反向")


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
        # 这两句都只有一行：面板内宽约 245 px，十来个汉字以上的句子会被截成"…"
        box.label(text="朱砂粗线 = 第 1 级（骨架）", icon="INFO")
    box.label(text="立即绘制，不建临时对象", icon="CHECKMARK")


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
        # 初始折线可能是多边形 / 曲线（段数 > 1），点数按实际段数估
        count = estimate_points(settings.elements(), level, settings.initiator_segments())
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
        # 面板统一「标签: 控件」内联样式（标签左对齐、控件紧跟其后），见 :func:`_box`
        layout.use_property_split = False
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
            count = len(settings.items) ** int(settings.level) * settings.initiator_segments()
            if count > 2_000_000:
                layout.label(text=f"这一级约 {count:,} 点，生成会很慢", icon="ERROR")
