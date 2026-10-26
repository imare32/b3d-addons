"""侧栏面板：``View3D > 侧栏 > Gen Zellij``。

面板只负责显示与调用算子：参数写进 ``scene.gen_zellij``，生成/删除/场景预设
走 ``gen_zellij.*`` 算子，所以同样的操作在搜索菜单（F3）里也能找到。

图层显隐与线宽是**就地生效**的（改 ``layer.hide`` / ``radius`` 属性），
只有改几何的参数才需要点「更新」。
"""

import bpy

from . import gp

__all__ = ["GENZ_PT_main"]


class GENZ_PT_main(bpy.types.Panel):
    bl_idname = "GENZ_PT_main"
    bl_label = "Gen Zellij"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Zellij"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.gen_zellij
        obj = gp.find_object()

        # --- 种子 ---------------------------------------------------------
        row = layout.row(align=True)
        row.prop(settings, "seed")
        row.operator("gen_zellij.random_seed", text="", icon="FILE_REFRESH")
        layout.prop(settings, "random_seed")

        # --- 设计 ---------------------------------------------------------
        box = layout.box()
        box.label(text="设计", icon="GP_SELECT_STROKES")
        column = box.column(align=True)
        column.prop(settings, "density")
        column.prop(settings, "focus")
        column.prop(settings, "shimmer")
        column = box.column(align=True)
        column.prop(settings, "canvas")
        column.prop(settings, "margin")

        # --- 动作 ---------------------------------------------------------
        row = layout.row(align=True)
        row.scale_y = 1.4
        text = "更新" if obj is not None else "生成"
        row.operator("gen_zellij.generate", text=text, icon="MOD_BUILD")
        if settings.dirty and obj is not None:
            layout.label(text="参数已改，需重新生成", icon="INFO")
        if settings.show_interlace:
            layout.label(text="编织的缺口是几何：改带宽/间隙要重建", icon="INFO")

        # --- 图层 ---------------------------------------------------------
        box = layout.box()
        box.label(text="图层", icon="LAYER_USED")

        row = box.row(align=True)
        row.prop(settings, "show_fills", text="块填充")

        row = box.row(align=True)
        row.prop(settings, "show_outlines", text="块轮廓")
        sub = row.row(align=True)
        sub.enabled = settings.show_outlines
        sub.prop(settings, "outline_radius", text="")

        row = box.row(align=True)
        row.prop(settings, "show_interlace", text="编织")
        sub = row.row(align=True)
        sub.enabled = settings.show_interlace
        sub.prop(settings, "interlace_radius", text="")
        sub.prop(settings, "interlace_gap", text="")
        sub.prop(settings, "interlace_color", text="")

        row = box.row(align=True)
        row.prop(settings, "show_tiling", text="瓷砖线")
        sub = row.row(align=True)
        sub.enabled = settings.show_tiling
        sub.prop(settings, "tiling_radius", text="")
        sub.prop(settings, "tiling_color", text="")

        row = box.row(align=True)
        row.prop(settings, "show_lines", text="构造线")
        sub = row.row(align=True)
        sub.enabled = settings.show_lines
        sub.prop(settings, "line_radius", text="")
        sub.prop(settings, "line_color", text="")
        sub.prop(settings, "dashed", text="", icon="IPO_CONSTANT")

        # --- 工具 ---------------------------------------------------------
        layout.separator()
        row = layout.row(align=True)
        row.operator("gen_zellij.setup_scene", text="场景预设", icon="CAMERA_DATA")
        row.operator("gen_zellij.sync_from_object", text="回读参数", icon="IMPORT")
        layout.operator("gen_zellij.delete", icon="TRASH")

        # --- 状态 ---------------------------------------------------------
        if settings.last_shapes:
            box = layout.box()
            box.label(text=f"seed {settings.last_seed} · {settings.last_shapes} 形状")
            if settings.last_layers:
                box.label(text=settings.last_layers)
            note = f"{settings.last_ms:.0f} ms"
            if settings.last_note:
                note = f"{note} · {settings.last_note}"
            box.label(text=note)

        if settings.error:
            layout.label(text=settings.error, icon="ERROR")

        if obj is not None:
            layout.label(
                text=f"{len(obj.data.layers)} 图层 / {len(obj.data.attributes)} 属性",
                icon="OUTLINER_DATA_GREASEPENCIL",
            )
