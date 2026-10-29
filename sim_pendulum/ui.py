"""侧栏面板：``View3D > 侧栏 > Pendulum``。

面板只负责显示与调用算子：物理参数改完**下一帧**就生效（播放或拖时间轴即可看到），
显示参数当场重刷，几何 / 后端要重建（改了下一帧会自动重建，也可以手动点）。
"""

import bpy

from . import field, sim, viewport

__all__ = ["PEND_PT_main"]


def _stuck_solid_views(context) -> int:
    """还停在「Solid + 着色颜色 = Material」的 3D 视图数量（这些视图里线是全黑的）。"""
    return sum(
        1
        for space in viewport.solid_spaces(context)
        if space.shading.type == viewport.SOLID
        and space.shading.color_type == viewport.MATERIAL
    )


def _realtime(scene, settings) -> float:
    """物理时间 / 真实时间：1.0 就是实时，0.6 就是慢放（24 fps 下缺省值）。"""
    fps = float(getattr(scene.render, "fps", 24) or 24)
    return float(settings.dt) * max(1, int(settings.substeps)) * fps


class PEND_PT_main(bpy.types.Panel):
    bl_idname = "PEND_PT_main"
    bl_label = "Sim Pendulum"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Pendulum"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        settings = scene.sim_pendulum
        backend = sim.backend_of(settings)
        obj = backend.find_object()
        info = sim.stats()

        # --- 动作 ---------------------------------------------------------
        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator(
            "sim_pendulum.build",
            text="重建" if obj is not None else "构建",
            icon="MOD_BUILD",
        )
        row = layout.row(align=True)
        row.operator("sim_pendulum.reset", text="重置", icon="LOOP_BACK")
        row.operator("sim_pendulum.step", text="+1 帧", icon="NEXT_KEYFRAME")
        layout.operator("sim_pendulum.setup_scene", text="场景预设", icon="CAMERA_DATA")

        if obj is None:
            layout.label(text="还没有阵列对象：先点构建", icon="INFO")
            return

        stuck = _stuck_solid_views(context)
        if stuck:
            row = layout.row(align=True)
            row.alert = True
            row.operator("sim_pendulum.fix_viewport", icon="COLOR")
            layout.label(
                text="Solid 模式线是全黑的：着色颜色得用 Attribute", icon="ERROR"
            )

        # --- 阵列 / 精度 ---------------------------------------------------
        box = layout.box()
        box.label(text="阵列 / 精度", icon="GRID")
        row = box.row(align=True)
        row.prop(settings, "n_grid")
        row.prop(settings, "spacing")
        box.prop(settings, "init_mode")
        if str(settings.init_mode) == field.INIT_PHASE:
            box.prop(settings, "spin_max")
        box.prop(settings, "layout")
        row = box.row(align=True)
        row.prop(settings, "output")
        row.prop(settings, "backend")
        box.prop(settings, "substeps")
        if str(settings.backend) == field.BACKEND_NUMPY and int(settings.n_grid) > 128:
            box.label(
                text=f"NumPy 后端在 N={int(settings.n_grid)} 会很慢：切「自动」用 GPU",
                icon="ERROR",
            )
        box.label(
            text=f"{info['pendulums']} 个摆 / {info['points']} 个笔划点 · "
            f"物理时间 {float(settings.dt) * max(1, int(settings.substeps)):.3f} s/帧"
            f"（{_realtime(scene, settings):.2f}× 实时）",
            icon="INFO",
        )
        if settings.dirty:
            box.label(text="参数已改：下一帧自动重建", icon="ERROR")
        elif info["build_ms"]:
            box.label(text=f"上次构建 {info['build_ms']:.0f} ms", icon="MOD_BUILD")

        # --- 物理 ---------------------------------------------------------
        box = layout.box()
        box.label(text="物理", icon="PHYSICS")
        row = box.row(align=True)
        row.prop(settings, "l1")
        row.prop(settings, "l2")
        row = box.row(align=True)
        row.prop(settings, "m1")
        row.prop(settings, "m2")
        row = box.row(align=True)
        row.prop(settings, "gravity")
        row.prop(settings, "dt")
        box.label(
            text="RK4 无阻尼：能量应当守恒，读数里的漂移就是积分误差", icon="INFO"
        )

        # --- 外观 ---------------------------------------------------------
        box = layout.box()
        box.label(text="外观", icon="SHADING_TEXTURE")
        box.prop(settings, "colour_mode", expand=True)
        if str(settings.colour_mode) == "SPEED":
            box.prop(settings, "speed_max")
        elif str(settings.colour_mode) == "DUAL":
            row = box.row(align=True)
            row.prop(settings, "dual_upper")
            row.prop(settings, "dual_lower")
        row = box.row(align=True)
        row.prop(settings, "pen_radius")
        row.prop(settings, "dot_scale")
        if settings.dot_scale <= 1.0:
            box.label(
                text="扣子比杆细：会收在两根杆的圆头里，只在接缝外缘露一点",
                icon="INFO",
            )
        else:
            box.label(text="扣子比杆粗：像一颗扣住接缝的铆钉", icon="INFO")

        # --- 读数 ---------------------------------------------------------
        box = layout.box()
        box.label(text="读数", icon="INFO")
        box.label(
            text=f"第 {info['frame']} 步 · 角速度峰值 {info['speed_peak']:.2f} rad/s"
        )
        box.label(text=f"平均能量 {info['energy']:.4f} J · 漂移 {info['drift']:+.2e}")
        cost = f"{info['ms']:.1f} ms/帧" if info["ms"] else "待测（播一帧）"
        solver = info["note"] or info["solver"] or "-"
        box.label(text=f"{info['output']} · {solver} · {cost}", icon="TIME")
        if info["ms"]:
            box.label(
                text=f"积分 {info['step_ms']:.2f} ms · 摊缓冲 {info['buffer_ms']:.2f} ms"
            )
            box.label(text=f"写后端 {info['sync_ms']:.2f} ms")
        bench_gp = sim.STATE.get("bench_gp_ms")
        bench_mesh = sim.STATE.get("bench_mesh_ms")
        if bench_gp and bench_mesh:
            box.label(
                text=f"对照：GP {bench_gp:.2f} ms / 网格 {bench_mesh:.2f} ms",
                icon="SORTTIME",
            )
        box.operator("sim_pendulum.benchmark", icon="SORTTIME")

        # --- 图像 / 数据接口 ----------------------------------------------
        if str(settings.output) == sim.OUTPUT_IMAGE:
            box = layout.box()
            box.label(text="结果图像", icon="IMAGE_DATA")
            side = max(2, round(float(info["pendulums"]) ** 0.5))
            box.label(text=f"{side}×{side} 像素 · 一像素一个双摆")
            box.operator("sim_pendulum.open_image", icon="FILE_IMAGE")
            box.label(text="按 Shift+F10 开一个图像编辑器即可实时看", icon="INFO")
            layout.separator()
            layout.label(text="播放或拖时间轴即实时演算；回退会重算", icon="PLAY")
            if info["error"]:
                layout.label(text=info["error"], icon="ERROR")
            layout.operator("sim_pendulum.delete", icon="TRASH")
            return

        # --- 数据接口 -----------------------------------------------------
        box = layout.box()
        box.label(text="数据接口", icon="SPREADSHEET")
        box.scale_y = 0.7
        if str(settings.output) == sim.OUTPUT_MESH:
            entries = (
                ("ink_color", "FLOAT_COLOR / POINT"),
                ("radius", "FLOAT / POINT"),
                ("theta1", "FLOAT / POINT"),
                ("theta2", "FLOAT / POINT"),
                ("speed", "FLOAT / POINT"),
                ("speed_upper", "FLOAT / POINT"),
                ("speed_lower", "FLOAT / POINT"),
                ("energy", "FLOAT / POINT"),
            )
        else:
            entries = (
                ("position", "FLOAT_VECTOR / POINT"),
                ("radius", "FLOAT / POINT"),
                ("vertex_color", "FLOAT_COLOR / POINT"),
                ("theta1", "FLOAT / POINT"),
                ("theta2", "FLOAT / POINT"),
                ("speed", "FLOAT / POINT"),
                ("speed_upper", "FLOAT / POINT"),
                ("speed_lower", "FLOAT / POINT"),
                ("energy", "FLOAT / POINT"),
            )
        for name, note in entries:
            row = box.row(align=True)
            row.label(text=name)
            row.label(text=note)

        layout.separator()
        layout.label(text="播放或拖时间轴即实时演算；回退会重算", icon="PLAY")
        if info["error"]:
            layout.label(text=info["error"], icon="ERROR")
        layout.operator("sim_pendulum.delete", icon="TRASH")
