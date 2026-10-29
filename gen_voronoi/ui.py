"""侧栏面板：``View3D > 侧栏 > Voronoi``。

面板只负责显示与调用算子：参数写进 ``scene.gen_voronoi``，生成 / 撒点 / 删除 / 场景
预设走 ``gen_voronoi.*`` 算子，所以同样的操作在搜索菜单（F3）里也能找到。
画界面不碰几何，也不在绘制时比对参数（「待生成」直接读 ``settings.dirty``）。
"""

from __future__ import annotations

import bpy

from . import gp, live, seeds
from .ops import (
    GENV_OT_delete,
    GENV_OT_edit_source,
    GENV_OT_generate,
    GENV_OT_scatter,
    GENV_OT_setup_scene,
    GENV_OT_sync_from_object,
    GENV_OT_toggle_source_wire,
)

__all__ = ["GENV_PT_main"]


def _box(layout, label: str, icon: str = "NONE"):
    box = layout.box()
    box.label(text=label, icon=icon)
    return box


def _source(layout, settings, source) -> None:
    row = layout.row(align=True)
    row.prop(settings, "source")
    if source is not None and source.type == "MESH":
        row.operator(
            GENV_OT_toggle_source_wire.bl_idname,
            text="",
            icon="SHADING_WIRE",
            depress=source.display_type == "WIRE",
        )
    if source is not None:
        row.operator(
            GENV_OT_edit_source.bl_idname,
            text="",
            icon="EDITMODE_HLT",
            depress=source.mode != "OBJECT",
        )
    row.operator(GENV_OT_scatter.bl_idname, text="", icon="RNDCURVE")


def _status(layout, settings, obj, source) -> None:
    box = _box(layout, "状态", "INFO")

    if settings.error:
        box.label(text=settings.error, icon="ERROR")
    elif obj is None:
        box.label(text="还没有单元对象，点「生成」建一个", icon="INFO")
    elif settings.dirty:
        box.label(text="参数改过，正在自动更新…", icon="TIME")
    else:
        box.label(text="已生成", icon="CHECKMARK")

    if obj is not None:
        box.label(
            text=f"{settings.last_cells} 单元 · {settings.last_points} 点 · "
            f"{settings.last_ms:.0f} ms",
            icon="PRESET",
        )
    if settings.last_note:
        box.label(text=settings.last_note, icon="SHADING_SOLID")

    total = seeds.count(source) if seeds.is_source(source) else 0
    if total > live.SEED_LIMIT:
        box.label(
            text=f"种子 {total} 个，实时更新已停（超过 {live.SEED_LIMIT}）",
            icon="ERROR",
        )
    elif total:
        box.label(text=f"源上 {total} 个种子", icon="POINTCLOUD_POINT")


class GENV_PT_main(bpy.types.Panel):
    """侧栏面板。"""

    bl_idname = "GENV_PT_main"
    bl_label = "Gen Voronoi"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Voronoi"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.gen_voronoi
        obj = gp.find_object()
        source = settings.source

        layout.use_property_split = True
        layout.use_property_decorate = False

        head = layout.row(align=True)
        head.scale_y = 1.4
        head.operator(
            GENV_OT_generate.bl_idname,
            text="生成" if obj is None else "更新",
            icon="PLAY",
        )
        head.prop(settings, "live", text="实时", toggle=True)
        head.operator(GENV_OT_delete.bl_idname, text="", icon="TRASH")

        _source(layout, settings, source)
        if source is None:
            layout.label(
                text="没选源：点右边的骰子撒一把种子，或选一个网格", icon="INFO"
            )

        # --- 单元 ---------------------------------------------------------
        box = _box(layout, "单元", icon="MESH_GRID")
        box.prop(settings, "shape")
        box.prop(settings, "spacing")
        if settings.shape == "CUBIC":
            box.prop(settings, "min_edge")
        if settings.shape != "EDGES":
            box.prop(settings, "resolution")

        # --- 气泡 ---------------------------------------------------------
        box = _box(layout, "气泡", icon="SPHERE")
        box.prop(settings, "use_max_radius")
        column = box.column()
        column.enabled = settings.use_max_radius
        column.prop(settings, "max_radius")

        # --- 边界 ---------------------------------------------------------
        box = _box(layout, "边界", icon="MOD_BOOLEAN")
        box.prop(settings, "boundary", text="")
        if settings.boundary == "AUTO":
            box.prop(settings, "clip_margin")
        elif settings.boundary == "FIXED":
            row = box.row(align=True)
            row.prop(settings, "boundary_width")
            row.prop(settings, "boundary_height")

        # --- 外观 ---------------------------------------------------------
        box = _box(layout, "外观", icon="LINE_DATA")
        box.prop(settings, "width")
        box.prop(settings, "colour")
        box.prop(settings, "fill")
        column = box.column()
        column.enabled = settings.fill
        column.prop(settings, "fill_colour")
        column.prop(settings, "fill_alpha")

        # --- 工具 ---------------------------------------------------------
        row = layout.row(align=True)
        row.operator(GENV_OT_setup_scene.bl_idname, text="场景预设", icon="CAMERA_DATA")
        row.operator(GENV_OT_sync_from_object.bl_idname, text="回读参数", icon="IMPORT")

        _status(layout, settings, obj, source)

        # --- 数据接口 -----------------------------------------------------
        box = _box(layout, "数据接口", icon="PROPERTIES")
        box.label(text="单元对象上逐点的属性（POINT 域）", icon="INFO")
        box.label(text="cell_index  int · 这个点属于第几个种子")
        box.label(text="逐笔划：fill_id / fill_color（填充）", icon="CHECKMARK")
