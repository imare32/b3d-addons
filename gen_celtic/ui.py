"""侧栏面板：``View3D > 侧栏 > Celtic``。

面板只负责显示与调用算子：参数写进 ``scene.gen_celtic``，编结 / 删除 / 回读 / 场景预设
走 ``gen_celtic.*`` 算子，所以同样的操作在搜索菜单（F3）里也能找到。

抬升与管半径都是**比例**（乘源网格的平均边长），所以面板另给一行折算后的实际值；
管半径、圆周分段、绳路采样密度是就地生效的（改几何节点输入），抬升改的是坐标，必须重编。
"""

from __future__ import annotations

import bpy

from . import live, tube, weave

__all__ = ["GENC_PT_main"]


class GENC_PT_main(bpy.types.Panel):
    bl_idname = "GENC_PT_main"
    bl_label = "Gen Celtic"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Celtic"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.gen_celtic
        obj = tube.find_object()

        # --- 输入 ---------------------------------------------------------
        row = layout.row(align=True)
        row.prop(settings, "source")
        # 线框开关：源网格套在结外面时，切成线框才看得见里面的结
        row.operator(
            "gen_celtic.toggle_source_wire",
            text="",
            icon="SHADING_WIRE",
            depress=settings.source is not None
            and settings.source.display_type == "WIRE",
        )

        if settings.source is None:
            layout.label(text="先选一个网格物体（平面/球/环都行）", icon="INFO")
            box = layout.box()
            box.label(text="试试：添加 > 网格 > 网格，细分 8×8", icon="MESH_GRID")
            self._draw_status(box, settings, obj)
            return

        source = settings.source
        if obj is not None and source == obj:
            layout.label(text="源不能是结自己", icon="ERROR")
            self._draw_status(layout, settings, obj)
            return

        # --- 几何 ---------------------------------------------------------
        box = layout.box()
        box.label(text="编织", icon="MOD_SOLIDIFY")
        column = box.column(align=True)
        column.prop(settings, "lift")
        column.prop(settings, "radius")
        column.prop(settings, "resolution")
        column.prop(settings, "spline_resolution")
        box.prop(settings, "material_color")
        box.prop(settings, "live")

        mean = tube.stored_mean(obj) if obj is not None else 0.0
        if mean > 0.0:
            box.label(
                text=f"边长 {mean:.4g} · 抬升 {mean * settings.lift:.4g} · "
                f"管 {mean * settings.radius:.4g}",
                icon="DRIVER_DISTANCE",
            )
        else:
            box.label(text="比例按源网格的平均边长折算", icon="DRIVER_DISTANCE")

        # --- 动作 ---------------------------------------------------------
        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator(
            "gen_celtic.generate",
            text="更新" if obj is not None else "编结",
            icon="MOD_BUILD",
        )
        row.operator("gen_celtic.delete", text="", icon="TRASH")

        if settings.dirty and obj is not None:
            layout.label(text="参数已改，需重新编结", icon="INFO")

        # --- 实时更新提示 -------------------------------------------------
        # 面数取求值后的：源物体挂的修改器 / 几何节点跑完才是要编的规模
        faces = weave.source_face_count(source)
        if faces > live.LIVE_FACE_LIMIT:
            layout.label(
                text=f"源网格 {faces} 面，实时更新已停（超过 {live.LIVE_FACE_LIMIT}）",
                icon="ERROR",
            )

        # --- 工具 ---------------------------------------------------------
        row = layout.row(align=True)
        row.operator("gen_celtic.setup_scene", text="场景预设", icon="CAMERA_DATA")
        row.operator("gen_celtic.sync_from_object", text="回读参数", icon="IMPORT")

        self._draw_status(layout, settings, obj)

    @staticmethod
    def _draw_status(layout, settings, obj):
        """统计信息：编出来的点数 / 带数 / 耗时，以及错误。"""
        if settings.last_points:
            box = layout.box()
            box.label(
                text=f"{settings.last_points} 交点 · {settings.last_strands} 带"
            )
            if settings.last_note:
                box.label(text=settings.last_note)
            note = f"{settings.last_ms:.0f} ms"
            if settings.last_broken:
                note += f" · 异常收尾 {settings.last_broken}"
            box.label(text=note)

        if settings.error:
            layout.label(text=settings.error, icon="ERROR")
        elif obj is not None:
            if obj.type == "CURVE":
                detail = f"{len(obj.data.splines)} 条样条"
                icon = "OUTLINER_OB_CURVE"
            else:  # 老版本留下的网格对象，还没重新生成
                detail = f"{len(obj.data.edges)} 段"
                icon = "OUTLINER_OB_MESH"
            layout.label(text=f"结对象 {obj.name} · {detail}", icon=icon)