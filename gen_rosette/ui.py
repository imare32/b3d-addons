"""侧栏面板：``View3D > 侧栏 > Rosette``。

面板只负责显示与调用算子：参数写进 ``scene.gen_rosette``，生成/删除/场景预设
走 ``gen_rosette.*`` 算子，所以同样的操作在搜索菜单（F3）里也能找到。

图层显隐与辅助线宽是**就地生效**的（改 ``layer.hide`` / ``radius`` 属性），
只有改几何的参数（花型、画布、编织带宽 / 描边倍数 / 间隙 / 平头开关）才需要重新生成。
"""

import bpy

from . import gp
from .rosette import realizable_level

__all__ = ["GENR_PT_main"]


class GENR_PT_main(bpy.types.Panel):
    bl_idname = "GENR_PT_main"
    bl_label = "Gen Rosette"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Rosette"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.gen_rosette
        obj = gp.find_object()

        # --- 种子 ---------------------------------------------------------
        row = layout.row(align=True)
        row.prop(settings, "seed")
        row.operator("gen_rosette.random_seed", text="", icon="FILE_REFRESH")
        layout.prop(settings, "random_seed")
        layout.label(text="种子只决定配色，花型由下面三项决定", icon="INFO")

        # --- 花型 ---------------------------------------------------------
        box = layout.box()
        box.label(text="花型", icon="GP_SELECT_STROKES")
        column = box.column(align=True)
        column.prop(settings, "petals")
        column.prop(settings, "star_type")
        column.prop(settings, "level")

        top = realizable_level(int(settings.petals), str(settings.star_type))
        if settings.level > top:
            box.label(text=f"该组合最高 {top} 阶，生成时会收回到 {top}", icon="ERROR")
        else:
            box.label(text=f"该组合最高 {top} 阶")

        column = box.column(align=True)
        column.prop(settings, "canvas")
        column.prop(settings, "margin")

        # --- 动作 ---------------------------------------------------------
        row = layout.row(align=True)
        row.scale_y = 1.4
        text = "更新" if obj is not None else "生成"
        row.operator("gen_rosette.generate", text=text, icon="MOD_BUILD")

        # --- 图层 ---------------------------------------------------------
        box = layout.box()
        box.label(text="图层", icon="LAYERGROUP_COLOR_04")

        row = box.row(align=True)
        row.prop(settings, "show_faces", text="色块")

        row = box.row(align=True)
        row.prop(settings, "show_band", text="编织")
        sub = row.row(align=True)
        sub.enabled = settings.show_band
        sub.prop(settings, "band_radius", text="")
        sub.prop(settings, "band_color", text="")

        row = box.row(align=True)
        row.prop(settings, "show_case", text="描边")
        sub = row.row(align=True)
        sub.enabled = settings.show_case
        sub.prop(settings, "casing", text="x")
        sub.prop(settings, "case_color", text="")

        row = box.row(align=True)
        row.enabled = settings.show_band
        row.prop(settings, "tuck", text="平头交叉")
        row.prop(settings, "gap", text="间隙")

        row = box.row(align=True)
        row.prop(settings, "show_guides", text="辅助线")
        sub = row.row(align=True)
        sub.enabled = settings.show_guides
        sub.prop(settings, "guide_radius", text="")

        if settings.dirty and obj is not None:
            layout.label(text="参数已改，需重新生成", icon="INFO")
        if settings.show_band:
            layout.label(text="编织的缺口是几何：改带宽/描边/间隙要重建", icon="INFO")

        # --- 工具 ---------------------------------------------------------
        layout.separator()
        row = layout.row(align=True)
        row.operator("gen_rosette.setup_scene", text="场景预设", icon="CAMERA_DATA")
        row.operator("gen_rosette.sync_from_object", text="回读参数", icon="IMPORT")
        layout.operator("gen_rosette.delete", icon="TRASH")

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
