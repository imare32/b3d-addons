"""侧栏面板：``View3D > 侧栏 > Interlace``。

面板只负责显示与调用算子：参数写进 ``scene.gen_interlace``，生成 / 撒一把 / 删除 /
场景预设走 ``gen_interlace.*`` 算子，所以同样的操作在搜索菜单（F3）里也能找到。
画界面不碰几何，也不在绘制时比对参数（「待生成」直接读 ``settings.dirty``）。
"""

from __future__ import annotations

import bpy

from . import gp, live, strands
from .ops import (
    GENI_OT_delete,
    GENI_OT_edit_source,
    GENI_OT_generate,
    GENI_OT_scatter,
    GENI_OT_setup_scene,
    GENI_OT_sync_from_object,
    GENI_OT_toggle_source_wire,
)

__all__ = ["GENI_PT_main"]


def _box(layout, label: str, icon: str = "NONE"):
    box = layout.box()
    box.label(text=label, icon=icon)
    return box


def _source(layout, settings, source) -> None:
    """源选择器 + 三个小按钮，**占满整行**。

    这一行不走 ``use_property_split`` 的「标签栏 | 控件栏」两栏：源是本面板的头等输入，
    两栏会把标签右对齐顶到分割线上，左边空出半行、字段只剩一半宽度。单独关掉
    split，标签贴着左边，字段一路撑到右边那三个按钮。
    """
    row = layout.row(align=True)
    row.use_property_split = False
    row.prop(settings, "source")
    if source is not None and source.type == strands.CURVE:
        row.operator(
            GENI_OT_toggle_source_wire.bl_idname,
            text="",
            icon="SHADING_WIRE",
            depress=source.display_type == "WIRE",
        )
    if source is not None:
        row.operator(
            GENI_OT_edit_source.bl_idname,
            text="",
            icon="EDITMODE_HLT",
            depress=source.mode != "OBJECT",
        )
    row.operator(GENI_OT_scatter.bl_idname, text="", icon="RNDCURVE")


def _status(layout, settings, obj, source) -> None:
    box = _box(layout, "状态", "INFO")

    if settings.error:
        box.label(text=settings.error, icon="ERROR")
    elif obj is None:
        box.label(text="还没有编织对象，点「生成」建一个", icon="INFO")
    elif settings.dirty:
        box.label(text="参数改过，正在自动更新…", icon="TIME")
    else:
        box.label(text="已生成", icon="CHECKMARK")

    if obj is not None:
        box.label(
            text=f"{settings.last_strands} 股线 · {settings.last_crossings} 交叉 · "
            f"{settings.last_pieces} 截 / {settings.last_patches} 补片 · "
            f"{settings.last_ms:.0f} ms",
            icon="PRESET",
        )
    if settings.last_note:
        box.label(text=settings.last_note, icon="SHADING_SOLID")

    total = strands.count(source) if strands.is_source(source) else 0
    limit = live.limit_for(settings)
    if total > limit:
        box.label(
            text=f"股线 {total} 条，实时更新已停（超过 {limit}）",
            icon="ERROR",
        )
    elif total:
        box.label(text=f"源上 {total} 条股线", icon="CURVE_DATA")


class GENI_PT_main(bpy.types.Panel):
    """侧栏面板。"""

    bl_idname = "GENI_PT_main"
    bl_label = "Gen Interlace"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Interlace"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.gen_interlace
        obj = gp.find_object()
        source = settings.source

        layout.use_property_split = True
        layout.use_property_decorate = False

        head = layout.row(align=True)
        # 这一行全是按钮、没有「标签 | 控件」可言：不关掉 split 的话，``prop`` 的
        # ``toggle`` 会被排成「左标签栏 + 右按钮」，按钮左边白白空出一段。
        head.use_property_split = False
        head.scale_y = 1.4
        # 宽度按比例切：主操作「生成」拿六成，剩下四成给「实时」与删除。
        # 右栏必须再开一个 ``row``：直接把两个控件丢给 split，两个会被对半分宽，
        # 只带图标的「删除」也摊到 50 px（图标按钮本该是 33 px 的固有宽度）。
        # 放进 row 里就回到正常规则 —— 带文字的「实时」可撑开，图标按钮只占自身宽度。
        split = head.split(factor=0.62, align=True)
        split.operator(
            GENI_OT_generate.bl_idname,
            text="生成" if obj is None else "更新",
            icon="PLAY",
        )
        right = split.row(align=True)
        right.prop(settings, "live", text="实时", toggle=True)
        right.operator(GENI_OT_delete.bl_idname, text="", icon="TRASH")

        _source(layout, settings, source)
        if source is None:
            layout.label(
                text="没选源：点右边的骰子撒一把股线，或选一个曲线 / GP 物体",
                icon="INFO",
            )

        # --- 编织 ---------------------------------------------------------
        box = _box(layout, "编织", icon="CURVE_DATA")
        box.prop(settings, "rule")
        box.prop(settings, "width")
        box.prop(settings, "step")
        box.prop(settings, "tuck")
        if settings.tuck:
            # 平贴模式：缝宽 + 浅角钳制（这时候没有「圆头」和「间隙」）
            box.prop(settings, "seam")
            box.prop(settings, "min_sin")
            box.label(text="端头一律平头，斜缺口由补片裁平", icon="INFO")
        else:
            box.prop(settings, "air")
            box.prop(settings, "min_sin")
            box.prop(settings, "round_caps")

        # --- 外观 ---------------------------------------------------------
        box = _box(layout, "外观", icon="LINE_DATA")
        box.prop(settings, "colour")
        box.prop(settings, "opacity")

        # --- 工具 ---------------------------------------------------------
        row = layout.row(align=True)
        row.operator(GENI_OT_setup_scene.bl_idname, text="场景预设", icon="CAMERA_DATA")
        row.operator(GENI_OT_sync_from_object.bl_idname, text="回读参数", icon="IMPORT")

        _status(layout, settings, obj, source)

        # --- 数据接口 -----------------------------------------------------
        box = _box(layout, "数据接口", icon="PROPERTIES")
        box.label(text="编织对象上逐点的属性（POINT 域）", icon="INFO")
        box.label(text="strand_index  int · 这个点来自第几条股线")
        box.label(text="逐笔划：piece_index（第几截，补片是 -1）", icon="CHECKMARK")
        box.label(text="补片：fill_id ≠ 0 + fill_color，只填不描")
