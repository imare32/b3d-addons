"""侧栏面板：``View3D > 侧栏 > Differential``。

面板只负责显示与调用算子：物理参数改完**下一帧**就生效（播放或拖时间轴即可看到），
显示参数当场重刷，几何 / 初始条件要重建（改了下一帧会自动重建，也可以手动点）。
"""

import bpy

from . import colour, gp, sim, viewport

__all__ = ["SDIF_PT_main"]


def _stuck_solid_views(context) -> int:
    """还停在「Solid + 着色颜色 = Material」的 3D 视图数量（这些视图里线是全黑的）。"""
    return sum(
        1
        for space in viewport.solid_spaces(context)
        if space.shading.type == viewport.SOLID
        and space.shading.color_type == viewport.MATERIAL
    )


class SDIF_PT_main(bpy.types.Panel):
    bl_idname = "SDIF_PT_main"
    bl_label = "Sim Differential"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Differential"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.sim_differential
        obj = gp.find_object()
        info = sim.stats()

        # --- 动作 ---------------------------------------------------------
        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator(
            "sim_differential.build",
            text="重建" if obj is not None else "构建",
            icon="MOD_BUILD",
        )
        row = layout.row(align=True)
        row.operator("sim_differential.reset", text="重置", icon="LOOP_BACK")
        row.operator("sim_differential.step", text="+1 帧", icon="NEXT_KEYFRAME")
        layout.operator("sim_differential.setup_scene", text="场景预设", icon="CAMERA_DATA")

        if obj is None:
            layout.label(text="还没有曲线对象：先点构建", icon="INFO")
            return

        stuck = _stuck_solid_views(context)
        if stuck:
            row = layout.row(align=True)
            row.alert = True
            row.operator("sim_differential.fix_viewport", icon="COLOR")
            layout.label(
                text="Solid 模式线是全黑的：着色颜色得用 Attribute", icon="ERROR"
            )

        # --- 初始条件 -----------------------------------------------------
        box = layout.box()
        box.label(text="初始条件", icon="CURVE_PATH")
        row = box.row(align=True)
        row.prop(settings, "init_segments")
        row.prop(settings, "init_radius")
        row = box.row(align=True)
        row.prop(settings, "jitter")
        row.prop(settings, "seed")
        row = box.row(align=True)
        row.prop(settings, "max_nodes")
        row.prop(settings, "backend")

        # --- 物理 ---------------------------------------------------------
        box = layout.box()
        box.label(text="物理", icon="PHYSICS")
        row = box.row(align=True)
        row.prop(settings, "rep_radius")
        row.prop(settings, "split_dist")
        row = box.row(align=True)
        row.prop(settings, "rep_weight")
        row.prop(settings, "smooth_weight")
        row = box.row(align=True)
        row.prop(settings, "bound_radius")
        row.prop(settings, "bound_weight")
        row = box.row(align=True)
        row.prop(settings, "growth_rate")
        row.prop(settings, "inertia")
        row = box.row(align=True)
        row.prop(settings, "substeps")
        row.prop(settings, "split_noise")
        box.prop(settings, "flatten")
        box.label(
            text="生长速率 = 每帧切几条边；惯性越大越顺；边界越小褶子越紧", icon="INFO"
        )

        # --- 外观 ---------------------------------------------------------
        box = layout.box()
        box.label(text="外观", icon="SHADING_TEXTURE")
        box.prop(settings, "colour_mode", expand=True)
        box.prop(settings, "line_width")
        if str(settings.colour_mode) == colour.MODE_MONO:
            box.prop(settings, "mono_colour")

        # --- 读数 ---------------------------------------------------------
        box = layout.box()
        box.label(text="读数", icon="INFO")
        box.label(
            text=f"第 {info['frame']} 帧 · 节点 {info['nodes']} / {info['capacity']}"
        )
        box.label(
            text=f"拉伸比 {info['stretch_mean']:.2f} · "
            f"最大转角 {info['curvature_peak'] * 180:.0f}°"
        )
        cost = f"约 {info['ms']:.1f} ms/帧" if info["ms"] else "待测（播一帧）"
        box.label(text=f"{info['backend'] or '-'} · {cost}", icon="TIME")
        if settings.dirty:
            box.label(text="参数已改：下一帧自动重建", icon="ERROR")
        elif info["build_ms"]:
            box.label(text=f"上次构建 {info['build_ms']:.0f} ms", icon="MOD_BUILD")
        if info["note"]:
            box.label(text=info["note"], icon="INFO")

        # --- 数据接口 -----------------------------------------------------
        box = layout.box()
        box.label(text="数据接口", icon="SPREADSHEET")
        box.scale_y = 0.7
        for name, note in (
            ("position", "FLOAT_VECTOR / POINT"),
            ("radius", "FLOAT / POINT"),
            ("opacity", "FLOAT / POINT"),
            ("vertex_color", "FLOAT_COLOR / POINT"),
            ("stretch", "FLOAT / POINT"),
            ("curvature", "FLOAT / POINT"),
        ):
            row = box.row(align=True)
            row.label(text=name)
            row.label(text=note)

        layout.separator()
        layout.label(text="播放或拖时间轴即实时生长；回退会重算", icon="PLAY")
        if info["error"]:
            layout.label(text=info["error"], icon="ERROR")
        layout.operator("sim_differential.delete", icon="TRASH")
