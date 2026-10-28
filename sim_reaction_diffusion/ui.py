"""侧栏面板：``View3D > 侧栏 > Reaction``。

面板只负责显示与调用算子：物理参数 / 预设改完**下一帧**就生效（播放或拖时间轴即可看到），
显示参数当场重刷，几何 / 初始条件要重建（改了下一帧会自动重建，也可以手动点）。
"""

import bpy

from . import mesh, sim, solver, viewport

__all__ = ["SRD_PT_main"]


def _stuck_solid_views(context) -> int:
    """还停在「Solid + 着色颜色 = Material」的 3D 视图数量（这些视图里看不出图案色）。"""
    return sum(
        1
        for space in viewport.solid_spaces(context)
        if space.shading.type == viewport.SOLID
        and space.shading.color_type == viewport.MATERIAL
    )


class SRD_PT_main(bpy.types.Panel):
    bl_idname = "SRD_PT_main"
    bl_label = "Sim Reaction Diffusion"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Reaction"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.sim_reaction_diffusion
        obj = mesh.find_object()
        info = sim.stats()

        # --- 动作 ---------------------------------------------------------
        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator(
            "sim_reaction_diffusion.build",
            text="重建" if obj is not None else "构建",
            icon="MOD_BUILD",
        )
        row = layout.row(align=True)
        row.operator(
            "sim_reaction_diffusion.reset", text="重置到种子", icon="LOOP_BACK"
        )
        row.operator("sim_reaction_diffusion.step", text="+1 帧", icon="NEXT_KEYFRAME")
        row = layout.row(align=True)
        row.operator(
            "sim_reaction_diffusion.setup_scene", text="场景预设", icon="CAMERA_DATA"
        )
        row.operator(
            "sim_reaction_diffusion.inject", text="注入种子", icon="BRUSH_DATA"
        )

        if obj is None:
            layout.label(text="还没有网格对象：先点构建", icon="INFO")
            return

        stuck = _stuck_solid_views(context)
        if stuck:
            row = layout.row(align=True)
            row.alert = True
            row.operator("sim_reaction_diffusion.fix_viewport", icon="COLOR")
            layout.label(
                text="Solid 模式看不出图案色：着色颜色得用 Attribute", icon="ERROR"
            )

        # --- 预设 ---------------------------------------------------------
        box = layout.box()
        box.label(text="图案预设", icon="PRESET")
        box.prop(settings, "preset")
        preset = solver.PRESETS.get(str(settings.preset))
        if preset is not None:
            box.label(text=preset["note"], icon="INFO")
            box.label(
                text=f"F = {preset['F']:.4f} · k = {preset['k']:.4f}（换预设就地变形）",
                icon="NONE",
            )

        # --- 初始条件 -----------------------------------------------------
        box = layout.box()
        box.label(text="初始条件", icon="OUTLINER_OB_MESH")
        box.prop(settings, "seed_mode")
        row = box.row(align=True)
        row.prop(settings, "seed_radius")
        if settings.seed_mode == solver.SEED_NOISE:
            row.prop(settings, "seed_random")
        box.prop(settings, "seed_ref")

        # --- 网格 / 精度 ---------------------------------------------------
        box = layout.box()
        box.label(text="网格 / 精度", icon="GRID")
        row = box.row(align=True)
        row.prop(settings, "nx")
        row.prop(settings, "ny")
        row = box.row(align=True)
        row.prop(settings, "width")
        row.prop(settings, "height")
        box.prop(settings, "backend")
        box.prop(settings, "substeps")
        box.label(text="子步 = 生长速度：图案要几千步才成型", icon="INFO")
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
        row.prop(settings, "F")
        row.prop(settings, "k")
        box.label(text="k 越大图案越稀；F 越大铺得越快", icon="INFO")
        advanced = box.box()
        advanced.label(text="扩散 / 时间步", icon="MOD_MESHDEFORM")
        row = advanced.row(align=True)
        row.prop(settings, "Du")
        row.prop(settings, "Dv")
        advanced.prop(settings, "dt")

        # --- 外观 ---------------------------------------------------------
        box = layout.box()
        box.label(text="外观", icon="SHADING_TEXTURE")
        box.prop(settings, "z_scale")
        row = box.row(align=True)
        row.prop(settings, "colour_mode", expand=True)
        box.prop(settings, "contrast")

        # --- 读数 ---------------------------------------------------------
        box = layout.box()
        box.label(text="读数", icon="INFO")
        box.label(text=f"当前帧 {info['frame']} · v 覆盖 {info['cover'] * 100:.1f}%")
        box.label(text=f"v 均值 {info['v_mean']:.3f} · 峰值 {info['v_max']:.3f}")
        if info["note"]:
            box.label(text=info["note"], icon="INFO")

        # --- 数据接口 -----------------------------------------------------
        box = layout.box()
        box.label(text="数据接口", icon="SPREADSHEET")
        box.scale_y = 0.7
        for name, note in (
            ("u", "FLOAT / POINT"),
            ("v", "FLOAT / POINT"),
            ("edge", "FLOAT / POINT"),
            ("color", "FLOAT_COLOR / POINT"),
        ):
            row = box.row(align=True)
            row.label(text=name)
            row.label(text=note)
        box.label(text="Cycles：节点 layer_name 留空 = 取活跃色", icon="INFO")

        layout.separator()
        layout.label(text="播放或拖时间轴即实时演算；回退会重置", icon="PLAY")
        if info["error"]:
            layout.label(text=info["error"], icon="ERROR")
        layout.operator("sim_reaction_diffusion.delete", icon="TRASH")
