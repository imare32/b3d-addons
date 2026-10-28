"""侧栏面板：``View3D > 侧栏 > Jacobi``。

面板只负责显示与调用算子：物理 / 显示参数改完**下一帧**就生效（播放或拖时间轴即可
看到），几何参数（分辨率、后端）要重建（改了下一帧会自动重建，也可以手动点）。
"""

import bpy

from . import mesh, sim, viewport

__all__ = ["SJAC_PT_main"]


def _stuck_solid_views(context) -> int:
    """还停在「Solid + 着色颜色 = Material」的 3D 视图数量（这些视图里看不出配色）。"""
    return sum(
        1
        for space in viewport.solid_spaces(context)
        if space.shading.type == viewport.SOLID
        and space.shading.color_type == viewport.MATERIAL
    )


class SJAC_PT_main(bpy.types.Panel):
    bl_idname = "SJAC_PT_main"
    bl_label = "Sim Jacobi Theta"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Jacobi"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.sim_jacobi
        obj = mesh.find_object()
        info = sim.stats()

        # --- 动作 ---------------------------------------------------------
        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator(
            "sim_jacobi.build",
            text="重建" if obj is not None else "构建",
            icon="MOD_BUILD",
        )
        row = layout.row(align=True)
        row.operator("sim_jacobi.reset", text="重置零点", icon="LOOP_BACK")
        row.operator("sim_jacobi.step", text="+1 帧", icon="NEXT_KEYFRAME")
        layout.operator("sim_jacobi.setup_scene", text="场景预设", icon="CAMERA_DATA")

        if obj is None:
            layout.label(text="还没有环面对象：先点构建", icon="INFO")
            return

        stuck = _stuck_solid_views(context)
        if stuck:
            row = layout.row(align=True)
            row.alert = True
            row.operator("sim_jacobi.fix_viewport", icon="COLOR")
            layout.label(
                text="Solid 模式看不出配色：着色颜色得用 Attribute", icon="ERROR"
            )

        # --- 网格 / 后端 ---------------------------------------------------
        box = layout.box()
        box.label(text="网格 / 后端", icon="GRID")
        row = box.row(align=True)
        row.prop(settings, "nu")
        row.prop(settings, "nv")
        box.prop(settings, "backend")
        cost = f"约 {info['ms']:.1f} ms/帧" if info["ms"] else "待测（播一帧）"
        box.label(text=f"{info['backend'] or '-'} · {cost}", icon="TIME")
        if settings.dirty:
            box.label(text="参数已改：下一帧自动重建", icon="ERROR")
        elif info["build_ms"]:
            box.label(text=f"上次构建 {info['build_ms']:.0f} ms", icon="MOD_BUILD")

        # --- 零点 / 物理 ---------------------------------------------------
        box = layout.box()
        box.label(text="零点 / 物理", icon="PHYSICS")
        row = box.row(align=True)
        row.prop(settings, "num_zeros")
        row.prop(settings, "sphere_radius")
        row = box.row(align=True)
        row.prop(settings, "speed")
        row.prop(settings, "substeps")
        box.prop(settings, "zero_dip_depth")
        box.prop(settings, "z_disp_scale")
        box.label(text="零点个数变了会重铺初值；球半径同时决定碰撞距离", icon="INFO")

        # --- 环面 / 模参数 ---------------------------------------------------
        box = layout.box()
        box.label(text="环面 / τ", icon="MESH_TORUS")
        row = box.row(align=True)
        row.prop(settings, "major_r")
        row.prop(settings, "minor_r")
        row = box.row(align=True)
        row.prop(settings, "tau_re")
        row.prop(settings, "tau_im")
        box.label(text="τ 越接近实轴，θ1 高阶项越强，格纹越碎", icon="INFO")

        # --- 显示 ---------------------------------------------------------
        box = layout.box()
        box.label(text="外观", icon="SHADING_TEXTURE")
        box.prop(settings, "colour_mode")

        # --- 读数 ---------------------------------------------------------
        box = layout.box()
        box.label(text="读数", icon="INFO")
        box.label(text=f"当前帧 {info['frame']} · 零点 {info['zeros']} 个")
        if info["note"]:
            box.label(text=info["note"], icon="INFO")

        # --- 数据接口 -----------------------------------------------------
        box = layout.box()
        box.label(text="数据接口", icon="SPREADSHEET")
        box.scale_y = 0.7
        for name, note in (
            ("amplitude", "FLOAT / POINT"),
            ("phase", "FLOAT / POINT"),
            ("phase_grid", "FLOAT / POINT"),
            ("potential", "FLOAT / POINT"),
            ("color", "FLOAT_COLOR / POINT"),
        ):
            row = box.row(align=True)
            row.label(text=name)
            row.label(text=note)

        layout.separator()
        layout.label(text="播放或拖时间轴即实时演算；回退会重置", icon="PLAY")
        if info["error"]:
            layout.label(text=info["error"], icon="ERROR")
        layout.operator("sim_jacobi.delete", icon="TRASH")
