"""侧栏面板：``View3D > 侧栏 > Dome``。

面板只负责显示与调用算子：参数写进 ``scene.gen_dome``，生成 / 删除 / 回读 / 场景预设
走 ``gen_dome.*`` 算子，所以同样的操作在搜索菜单（F3）里也能找到。

三档参数按生效方式分块摆：几何（改完要点生成）、生长（跟时间轴的那几个是实时的）、
修改器（厚度 / 立柱 / 颜色，也是实时的）。
"""

import bpy

from . import mesh

__all__ = ["GEND_PT_main"]


class GEND_PT_main(bpy.types.Panel):
    bl_idname = "GEND_PT_main"
    bl_label = "Gen Dome"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Dome"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.gen_dome
        obj = mesh.find_object()

        # --- 几何（改完要重新生成） ---------------------------------------
        box = layout.box()
        box.label(text="穹顶", icon="MESH_ICOSPHERE")
        column = box.column(align=True)
        column.prop(settings, "count")
        column.prop(settings, "radius")
        column.prop(settings, "height")
        column.prop(settings, "gap")
        box.prop(settings, "close_bottom")
        box.label(
            text=f"顶点高 {settings.apex_height:.4g} · 直径 {settings.radius * 2:.4g}",
            icon="DRIVER_DISTANCE",
        )

        # --- 生长 ---------------------------------------------------------
        box = layout.box()
        box.label(text="生长", icon="PLAY")
        box.prop(settings, "animate")
        if settings.animate:
            row = box.row(align=True)
            row.prop(settings, "start_frame")
            row.prop(settings, "length")
            box.label(
                text=f"第 {settings.start_frame}→"
                f"{settings.start_frame + settings.length} 帧长满",
                icon="TIME",
            )
        else:
            box.prop(settings, "grow", slider=True)
        generations = settings.generation_count()
        box.label(
            text=f"3 → {int(settings.count)} 点 · 每代 +"
            f"{settings.points_per_generation():.1f} 点"
        )
        if generations > 1:
            box.label(
                text=f"烘 {generations} 代 / {int(settings.length)} 帧 · "
                f"每代停 {settings.frames_per_generation():.1f} 帧"
            )
        else:
            box.label(text="烘 1 代（静态）")
        box.label(text=f"约 {settings.estimated_faces()} 面")
        if settings.animate and generations < min(
            int(settings.length), int(settings.count) - 2
        ):
            box.label(text="代数被面数预算收过：生长会跳代", icon="INFO")

        # --- 几何节点（就地生效） -----------------------------------------
        box = layout.box()
        box.label(text="面板与立柱", icon="OUTLINER_OB_MESH")
        row = box.row(align=True)
        row.prop(settings, "studs", text="立柱")
        sub = row.row(align=True)
        sub.enabled = settings.studs
        sub.prop(settings, "stud_radius", text="")
        row = box.row(align=True)
        row.prop(settings, "panel_color", text="面板")
        row.prop(settings, "stud_color", text="立柱")
        box.label(text="厚度 / 平滑自己加修改器", icon="INFO")

        # --- 动作 ---------------------------------------------------------
        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator(
            "gen_dome.generate",
            text="重新烘焙" if obj is not None else "生成",
            icon="MOD_BUILD",
        )
        row.operator("gen_dome.delete", text="", icon="TRASH")

        if settings.dirty and obj is not None:
            layout.label(text="几何参数已改，需重新生成", icon="INFO")

        # --- 工具 ---------------------------------------------------------
        row = layout.row(align=True)
        row.operator("gen_dome.setup_scene", text="场景预设", icon="CAMERA_DATA")
        row.operator("gen_dome.sync_from_object", text="回读参数", icon="IMPORT")

        self._draw_status(layout, settings, obj)
        self._draw_interface(layout, obj)

    @staticmethod
    def _draw_interface(layout, obj):
        """数据接口：网格上那三个逐面属性（几何节点与自有工具都按名字取）。"""
        if obj is None:
            return
        box = layout.box()
        box.label(text="数据接口", icon="SPREADSHEET")
        body = box.box()
        body.scale_y = 0.6
        for name, domain in (
            ("seed_idx", "face / int"),
            ("seed_pos", "face / vector"),
            ("gen", "face / int"),
        ):
            drawn = name in obj.data.attributes
            row = body.row(align=True)
            row.label(text=name, icon="NONE" if drawn else "ERROR")
            row.label(text=domain)

    @staticmethod
    def _draw_status(layout, settings, obj):
        """统计信息：代数 / 面数 / 顶点数 / 耗时，以及错误。"""
        if settings.last_generations:
            box = layout.box()
            baked = mesh.stored_generations(obj) if obj is not None else 0
            box.label(
                text=f"{baked or settings.last_generations} 代 · "
                f"{settings.last_faces} 面 · {settings.last_verts} 顶点"
            )
            note = f"{settings.last_ms:.0f} ms"
            if settings.last_note:
                note = f"{note} · {settings.last_note}"
            box.label(text=note)

        if settings.error:
            layout.label(text=settings.error, icon="ERROR")
        elif obj is not None:
            layout.label(
                text=f"{obj.name} · {len(obj.data.attributes)} 属性", icon="MESH_DATA"
            )
