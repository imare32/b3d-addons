"""侧栏面板：``View3D > 侧栏 > Hilbert``。

面板只负责显示与调用算子：物理参数改完**下一帧**就生效（播放或拖时间轴即可看到），
显示参数当场重刷，几何 / 后端要重建（改了下一帧会自动重建，也可以手动点）。
"""

import bpy

from . import gp, sim, viewport

__all__ = ["SHIL_PT_main"]


def _stuck_solid_views(context) -> int:
    """还停在「Solid + 着色颜色 = Material」的 3D 视图数量（这些视图里线是全黑的）。"""
    return sum(
        1
        for space in viewport.solid_spaces(context)
        if space.shading.type == viewport.SOLID
        and space.shading.color_type == viewport.MATERIAL
    )


def _realtime(scene, settings) -> float:
    """物理时间 / 真实时间：1.0 就是实时，0.72 就是慢放（24 fps 下缺省值）。"""
    fps = float(getattr(scene.render, "fps", 24) or 24)
    return float(settings.dt) * max(1, int(settings.substeps)) * fps


class SHIL_PT_main(bpy.types.Panel):
    bl_idname = "SHIL_PT_main"
    bl_label = "Sim Hilbert"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Hilbert"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        settings = scene.sim_hilbert
        obj = gp.find_object()
        info = sim.stats()

        # --- 动作 ---------------------------------------------------------
        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator(
            "sim_hilbert.build",
            text="重建" if obj is not None else "构建",
            icon="MOD_BUILD",
        )
        row = layout.row(align=True)
        row.operator("sim_hilbert.reset", text="重置", icon="LOOP_BACK")
        row.operator("sim_hilbert.step", text="+1 帧", icon="NEXT_KEYFRAME")
        layout.operator("sim_hilbert.setup_scene", text="场景预设", icon="CAMERA_DATA")

        if obj is None:
            layout.label(text="还没有曲线对象：先点构建", icon="INFO")
            return

        stuck = _stuck_solid_views(context)
        if stuck:
            row = layout.row(align=True)
            row.alert = True
            row.operator("sim_hilbert.fix_viewport", icon="COLOR")
            layout.label(
                text="Solid 模式线是全黑的：着色颜色得用 Attribute", icon="ERROR"
            )

        # --- 曲线 / 精度 ---------------------------------------------------
        box = layout.box()
        box.label(text="曲线 / 精度", icon="CURVE_PATH")
        row = box.row(align=True)
        row.prop(settings, "order")
        row.prop(settings, "neighbours")
        row = box.row(align=True)
        row.prop(settings, "size")
        row.prop(settings, "drop_height")
        box.prop(settings, "backend")
        box.prop(settings, "substeps")
        box.label(
            text=f"{info['vertices']} 点 / {info['springs']} 弹簧 · "
            f"物理时间 {float(settings.dt) * max(1, int(settings.substeps)):.3f} s/帧"
            f"（{_realtime(scene, settings):.2f}× 实时）",
            icon="INFO",
        )
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
        row.prop(settings, "stiffness")
        row.prop(settings, "damping")
        row = box.row(align=True)
        row.prop(settings, "gravity")
        row.prop(settings, "mass")
        row = box.row(align=True)
        row.prop(settings, "dt")
        box.label(
            text="刚度越小落地坍缩越明显；dt 减小 = 更稳，子步增加 = 推进更快",
            icon="INFO",
        )
        advanced = box.box()
        advanced.label(text="地面 / 碰撞", icon="MESH_PLANE")
        row = advanced.row(align=True)
        row.prop(settings, "ground_height")
        row.prop(settings, "show_ground")
        row = advanced.row(align=True)
        row.prop(settings, "restitution")
        row.prop(settings, "friction")

        # --- 外观 ---------------------------------------------------------
        box = layout.box()
        box.label(text="外观", icon="SHADING_TEXTURE")
        box.prop(settings, "colour_mode", expand=True)
        if str(settings.colour_mode) == "SPEED":
            box.prop(settings, "speed_max")
        elif str(settings.colour_mode) == "STRAIN":
            box.prop(settings, "strain_max")
        else:
            box.prop(settings, "mono_colour")
        box.prop(settings, "line_width")

        # --- 读数 ---------------------------------------------------------
        box = layout.box()
        box.label(text="读数", icon="INFO")
        box.label(text=f"第 {info['frame']} 帧 · 速度峰值 {info['speed_peak']:.2f} m/s")
        box.label(
            text=f"应变均值 {info['strain_mean'] * 100:.1f}% · "
            f"峰值 {info['strain_peak'] * 100:.1f}%"
        )
        if info["note"]:
            box.label(text=info["note"], icon="INFO")

        # --- 数据接口 -----------------------------------------------------
        box = layout.box()
        box.label(text="数据接口", icon="SPREADSHEET")
        box.scale_y = 0.7
        for name, note in (
            ("position", "FLOAT_VECTOR / POINT"),
            ("radius", "FLOAT / POINT"),
            ("vertex_color", "FLOAT_COLOR / POINT"),
            ("speed", "FLOAT / POINT"),
            ("strain", "FLOAT / POINT"),
        ):
            row = box.row(align=True)
            row.label(text=name)
            row.label(text=note)

        layout.separator()
        layout.label(text="播放或拖时间轴即实时演算；回退会重算", icon="PLAY")
        if info["error"]:
            layout.label(text=info["error"], icon="ERROR")
        layout.operator("sim_hilbert.delete", icon="TRASH")
