"""侧栏面板：``View3D > 侧栏 > Dendrite``。

面板只负责显示与调用算子：物理参数改完**下一帧**就生效（播放或拖时间轴即可看到），
显示参数当场重刷，几何 / 初始条件要重建（改了下一帧会自动重建，也可以手动点）。
"""

import bpy

from . import mesh, sim, viewport

__all__ = ["SDEN_PT_main"]


def _stuck_solid_views(context) -> int:
    """还停在「Solid + 着色颜色 = Material」的 3D 视图数量（这些视图里看不出相场色）。"""
    return sum(
        1
        for space in viewport.solid_spaces(context)
        if space.shading.type == viewport.SOLID
        and space.shading.color_type == viewport.MATERIAL
    )


class SDEN_PT_main(bpy.types.Panel):
    bl_idname = "SDEN_PT_main"
    bl_label = "Sim Dendrite"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Dendrite"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.sim_dendrite
        obj = mesh.find_object()
        info = sim.stats()

        # --- 动作 ---------------------------------------------------------
        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator(
            "sim_dendrite.build",
            text="重建" if obj is not None else "构建",
            icon="MOD_BUILD",
        )
        row = layout.row(align=True)
        row.operator("sim_dendrite.reset", text="重置到晶核", icon="LOOP_BACK")
        row.operator("sim_dendrite.step", text="+1 帧", icon="NEXT_KEYFRAME")
        layout.operator("sim_dendrite.setup_scene", text="场景预设", icon="CAMERA_DATA")

        if obj is None:
            layout.label(text="还没有网格对象：先点构建", icon="INFO")
            return

        stuck = _stuck_solid_views(context)
        if stuck:
            row = layout.row(align=True)
            row.alert = True
            row.operator("sim_dendrite.fix_viewport", icon="COLOR")
            layout.label(
                text="Solid 模式看不出相场色：着色颜色得用 Attribute", icon="ERROR"
            )

        # --- 初始条件 -----------------------------------------------------
        box = layout.box()
        box.label(text="初始条件", icon="OUTLINER_OB_MESH")
        box.prop(settings, "seed_ref")
        row = box.row(align=True)
        row.prop(settings, "seed_radius")
        row.prop(settings, "initial_dT")

        # --- 网格 / 精度 ---------------------------------------------------
        box = layout.box()
        box.label(text="网格 / 精度", icon="GRID")
        row = box.row(align=True)
        row.prop(settings, "nx")
        row.prop(settings, "ny")
        row = box.row(align=True)
        row.prop(settings, "dx")
        row.prop(settings, "dy")
        box.prop(settings, "backend")
        box.prop(settings, "substeps")
        box.label(text="子步 = 生长速度：GPU 200 也实时，NumPy 建议 ≤25", icon="INFO")
        cost = f"约 {info['ms']:.1f} ms/帧" if info["ms"] else "待测（播一帧）"
        box.label(text=f"{info['backend'] or '-'} · {cost}", icon="TIME")
        if settings.dirty:
            box.label(text="参数已改：下一帧自动重建", icon="ERROR")
        elif info["build_ms"]:
            box.label(text=f"上次构建 {info['build_ms']:.0f} ms", icon="MOD_BUILD")

        # --- 物理 ---------------------------------------------------------
        box = layout.box()
        box.label(text="物理", icon="PHYSICS")
        row = box.row(align=True)
        row.prop(settings, "N_sym")
        row.prop(settings, "c_ani")
        row = box.row(align=True)
        row.prop(settings, "DT_heat")
        row.prop(settings, "dt")
        box.label(text="6 = 雪花，4 = 十字；c_ani = 0 会退回各向同性圆团", icon="INFO")
        advanced = box.box()
        advanced.label(text="界面 / 耦合", icon="MOD_MESHDEFORM")
        row = advanced.row(align=True)
        row.prop(settings, "alpha")
        row.prop(settings, "theta_off")
        row = advanced.row(align=True)
        row.prop(settings, "tau")
        row.prop(settings, "kappa1")
        advanced.prop(settings, "kappa2")

        # --- 显示 ---------------------------------------------------------
        box = layout.box()
        box.label(text="外观", icon="SHADING_TEXTURE")
        box.prop(settings, "z_scale")
        row = box.row(align=True)
        row.prop(settings, "colour_mode", expand=True)

        # --- 读数 ---------------------------------------------------------
        box = layout.box()
        box.label(text="读数", icon="INFO")
        box.label(text=f"当前帧 {info['frame']} · 固相占比 {info['solid'] * 100:.1f}%")
        box.label(text=f"ΔT {info['dT_low']:+.4f} ~ {info['dT_high']:+.4f}")
        if info["note"]:
            box.label(text=info["note"], icon="INFO")

        # --- 数据接口 -----------------------------------------------------
        box = layout.box()
        box.label(text="数据接口", icon="SPREADSHEET")
        box.scale_y = 0.7
        for name, note in (
            ("phi", "FLOAT / POINT"),
            ("dT", "FLOAT / POINT"),
            ("interface", "FLOAT / POINT"),
            ("color", "FLOAT_COLOR / POINT"),
        ):
            row = box.row(align=True)
            row.label(text=name)
            row.label(text=note)

        layout.separator()
        layout.label(text="播放或拖时间轴即实时演算；回退会重置", icon="PLAY")
        if info["error"]:
            layout.label(text=info["error"], icon="ERROR")
        layout.operator("sim_dendrite.delete", icon="TRASH")
