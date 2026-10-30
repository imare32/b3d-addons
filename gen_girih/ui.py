"""侧栏面板：``View3D > 侧栏 > Girih``。

面板只负责显示与调用算子：参数写进 ``scene.gen_girih``，生成 / 删除 / 场景预设走
``gen_girih.*`` 算子，所以同样的操作在搜索菜单（F3）里也能找到。

线宽 / 描边倍数 / 两个颜色 / 不透明度 / 图层显隐是**就地生效**的；改砖的摆法与射线
（镶嵌 / 样式 / 接触角 / 间隙 / 边长 / 画布）才需要点「更新」。
"""

import bpy

from . import gp
from .scene import STYLE_BANDS, STYLE_INTERLACE

__all__ = ["GIRIH_PT_main"]


class GIRIH_PT_main(bpy.types.Panel):
    bl_idname = "GIRIH_PT_main"
    bl_label = "Gen Girih"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Girih"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.gen_girih
        obj = gp.find_object()

        # --- 配色 ---------------------------------------------------------
        box = layout.box()
        box.label(text="配色", icon="COLOR")
        row = box.row(align=True)
        row.prop(settings, "palette", text="")
        row.prop(settings, "canvas_colour", text="")

        # --- 图案 ---------------------------------------------------------
        box = layout.box()
        box.label(text="图案", icon="MESH_CIRCLE")
        column = box.column(align=True)
        column.prop(settings, "tiling")
        column.prop(settings, "style")
        column.prop(settings, "angle", slider=True)
        column.prop(settings, "gap")
        if settings.style == STYLE_INTERLACE:
            column.prop(settings, "seam")
        column = box.column(align=True)
        column.prop(settings, "size")
        column.prop(settings, "canvas")

        # --- 动作 ---------------------------------------------------------
        row = layout.row(align=True)
        row.scale_y = 1.4
        text = "更新" if obj is not None else "生成"
        row.operator("gen_girih.generate", text=text, icon="MOD_BUILD")
        row = layout.row(align=True)
        row.prop(settings, "auto_rebuild", toggle=True)
        if not settings.auto_rebuild:
            row.label(text="改完记得点更新", icon="INFO")

        # --- 填充与调试 ---------------------------------------------------
        box = layout.box()
        box.label(text="填充", icon="COLOR")
        row = box.row(align=True)
        row.prop(settings, "fills", toggle=True)
        sub = row.row(align=True)
        sub.active = settings.fills
        sub.prop(settings, "fill_by_sides", toggle=True)
        sub.prop(settings, "fill_colour", text="")
        box.prop(settings, "show_tiles", toggle=True)

        # --- 线条（就地生效） ---------------------------------------------
        box = layout.box()
        box.label(text="线条", icon="GP_SELECT_STROKES")
        row = box.row(align=True)
        row.prop(settings, "width")
        row.prop(settings, "colour", text="")
        box.prop(settings, "opacity")

        if settings.style in {STYLE_BANDS, STYLE_INTERLACE}:
            column = box.column(align=True)
            row = column.row(align=True)
            row.prop(settings, "casing")
            row.prop(settings, "case_colour", text="")
            row = column.row(align=True)
            row.prop(settings, "show_band", toggle=True)
            row.prop(settings, "show_case", toggle=True)
            if settings.style == STYLE_INTERLACE:
                # 交织的缺口按可见半宽切，宽度与描边都是几何参数
                column.label(text="交织样式：线宽 / 描边改了要重新生成", icon="INFO")
                if settings.casing < 1.5:
                    column.label(text="描边倍数 ≥ 1.6 时上下关系更清楚", icon="BLANK1")

        if settings.dirty and obj is not None:
            layout.label(text="图案参数已改，需重新生成", icon="INFO")

        # --- 接触角扫掠 ---------------------------------------------------
        box = layout.box()
        box.label(text="接触角扫掠", icon="RENDER_ANIMATION")
        row = box.row(align=True)
        row.prop(settings, "sweep_from")
        row.prop(settings, "sweep_to")
        row = box.row(align=True)
        row.prop(settings, "sweep_frames")
        row.operator("gen_girih.sweep", text="烘焙", icon="PLAY")
        box.label(text="烘焙成 GP 多帧，播放时间线即可", icon="BLANK1")

        # --- 工具 ---------------------------------------------------------
        layout.separator()
        row = layout.row(align=True)
        row.operator("gen_girih.setup_scene", text="场景预设", icon="CAMERA_DATA")
        row.operator("gen_girih.sync_from_object", text="回读参数", icon="IMPORT")
        layout.operator("gen_girih.delete", icon="TRASH")

        # --- 状态 ---------------------------------------------------------
        if settings.last_strands:
            box = layout.box()
            box.label(
                text=f"{settings.last_tiles} 砖 · {settings.last_strands} 股线 · "
                f"{settings.last_crossings} 交叉",
                icon="INFO",
            )
            if settings.last_cuts:
                box.label(
                    text=f"{settings.last_cuts} 切口 · {settings.last_patches} 补片"
                    + (f" · 收刀 {settings.last_pruned}" if settings.last_pruned else "")
                    + (
                        f" · 非交替 {settings.last_mismatches}"
                        if settings.last_mismatches
                        else ""
                    )
                )
            if settings.fills:
                box.label(text=f"{settings.last_fills} 块星形填充")
            box.label(text=f"{settings.last_points} 点 · {settings.last_ms:.0f} ms")

        if settings.last_note:
            layout.label(text=settings.last_note, icon="ERROR")

        if settings.error:
            layout.label(text=settings.error, icon="ERROR")

        if obj is not None:
            layout.label(
                text=f"{len(obj.data.layers)} 图层 / {len(obj.data.attributes)} 属性",
                icon="OUTLINER_DATA_GREASEPENCIL",
            )
