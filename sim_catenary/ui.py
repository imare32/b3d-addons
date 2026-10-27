"""侧栏面板：``View3D > 侧栏 > Catenary``。

面板只负责显示与调用算子：物理参数改完**下一帧**就生效（播放或拖时间轴即可看到），
显示参数当场重刷，几何参数要重建（改了会自动重建，也可以手动点）。
"""

import bpy

from . import gp, sim, viewport

__all__ = ["SCAT_PT_main"]


def _stuck_solid_views(context) -> int:
    """还停在「Solid + 着色颜色 = Material」的 3D 视图数量（这些视图里链条是黑的）。"""
    return sum(
        1
        for space in viewport.solid_spaces(context)
        if space.shading.type == viewport.SOLID
        and space.shading.color_type == viewport.MATERIAL
    )


class SCAT_PT_main(bpy.types.Panel):
    bl_idname = "SCAT_PT_main"
    bl_label = "Sim Catenary"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Catenary"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.sim_catenary
        obj = gp.find_object()
        info = sim.stats()

        # --- 动作 ---------------------------------------------------------
        row = layout.row(align=True)
        row.scale_y = 1.4
        text = "重建" if obj is not None else "构建"
        row.operator("sim_catenary.build", text=text, icon="MOD_BUILD")
        row = layout.row(align=True)
        row.operator("sim_catenary.reset", text="重置到拱形", icon="LOOP_BACK")
        row.operator("sim_catenary.setup_scene", text="场景预设", icon="CAMERA_DATA")

        if obj is None:
            layout.label(text="还没有链条对象：先点构建", icon="INFO")
            layout.operator("sim_catenary.delete", icon="TRASH")
            return

        # 颜色只写在 vertex_color 属性里；Solid 模式用 Material 着色时画出来是全黑
        stuck = _stuck_solid_views(context)
        if stuck:
            row = layout.row(align=True)
            row.alert = True
            row.operator("sim_catenary.fix_viewport", icon="COLOR")
            layout.label(
                text="Solid 模式现在是全黑：着色颜色得用 Attribute", icon="ERROR"
            )

        # --- 物理 ---------------------------------------------------------
        box = layout.box()
        box.label(text="运动", icon="PHYSICS")
        box.prop(settings, "gravity", slider=True)
        box.prop(settings, "drag", slider=True)
        box.label(text="阻力 0% = 自由落体长摆 | 15% 默认 | 100% ≈ 匀速下落")
        box.prop(settings, "elasticity", slider=True)
        if settings.elasticity > 0:
            box.label(text="弹性 > 0 时链会拉长，下垂比理论更深", icon="INFO")
        box.prop(settings, "bend_limit", slider=True)
        if settings.bend_limit >= 180:
            box.label(text="180 = 不限制：锚点那根杆会甩翻折回来", icon="ERROR")
        elif settings.bend_limit < 100:
            box.label(text="折角上限越小链越硬（不会对折）", icon="INFO")

        box = layout.box()
        box.label(text="精度", icon="MOD_SIMPLIFY")
        row = box.row()
        row.prop(settings, "substeps")
        row.prop(settings, "iterations")
        cost = f"约 {info['ms']:.1f} ms" if info["ms"] else "待测（播一帧）"
        box.label(text=f"实测每帧 {cost}", icon="TIME")

        # --- 几何 ---------------------------------------------------------
        box = layout.box()
        box.label(text="几何", icon="MESH_CIRCLE")
        box.prop(settings, "n_rings")
        box.prop(settings, "arch_a")
        row = box.row()
        row.prop(settings, "ring_major")
        row.prop(settings, "ring_minor")
        box.prop(settings, "ring_straight")
        row = box.row(align=True)
        row.prop(settings, "curve_type", expand=True)
        row = box.row()
        row.enabled = settings.curve_type == "POLY" or settings.ring_straight <= 1e-6
        row.prop(settings, "segments")
        if settings.dirty:
            box.label(text="几何参数已改：下一帧自动重建", icon="ERROR")
        elif obj is not None:
            box.label(
                text=f"{info['strokes']} 笔划 / {info['points']} 点",
                icon="GP_SELECT_STROKES",
            )

        # --- 显示 ---------------------------------------------------------
        box = layout.box()
        box.label(text="外观", icon="SHADING_TEXTURE")
        box.prop(settings, "line_width")
        row = box.row(align=True)
        row.prop(settings, "colour_mode", expand=True)
        if settings.colour_mode == "MONO":
            box.prop(settings, "ring_colour")
        else:
            box.label(text=f"张力色带上限 {info['tmax']:.0f}")
        box.prop(settings, "show_spine")
        if settings.show_spine:
            box.prop(settings, "spine_width")
        row = box.row(align=True)
        row.prop(settings, "smooth_tension")
        sub = row.row(align=True)
        sub.enabled = settings.smooth_tension
        sub.prop(settings, "ema_alpha", text="")

        # --- 读数 ---------------------------------------------------------
        box = layout.box()
        box.label(text="读数", icon="INFO")
        box.label(text=f"当前帧 {info['frame']} · 最低点 z {info['lowest']:+.4f}")
        box.label(text=f"RMSE vs 理论 {info['rmse']:.5f} m")
        ok, total = info["interlock"]
        box.label(text=f"相扣 {ok}/{total} 对")
        if settings.colour_mode == "TENSION":
            box.label(text=f"张力 max {info['tension']:.0f}")

        layout.separator()
        layout.label(text="播放或拖时间轴即实时演算；回退会重置", icon="PLAY")
        if info["error"]:
            layout.label(text=info["error"], icon="ERROR")
        layout.operator("sim_catenary.delete", icon="TRASH")
