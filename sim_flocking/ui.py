"""侧栏面板：``View3D > 侧栏 > FLOCK``（标签页用缩写，省竖向空间）。

面板只负责显示与调用算子：物理 / 边界 / 配色改完**下一帧**就生效（播放或拖时间轴即可
看到），鸟数 / 箭头长度 / 配色连下一帧都不用等（点完立刻重画）。
"""

import bpy

from . import colour, mesh, sim, viewport

__all__ = ["FLOCK_PT_main"]


def _stuck_solid_views(context) -> int:
    """还停在「Solid + 着色颜色 = Material」的 3D 视图数量（这些视图里看不到速度色）。"""
    return sum(
        1
        for space in viewport.solid_spaces(context)
        if space.shading.type == viewport.SOLID
        and space.shading.color_type == viewport.MATERIAL
    )


class FLOCK_PT_main(bpy.types.Panel):
    bl_idname = "FLOCK_PT_main"
    bl_label = "Flocking"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "FLOCK"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.sim_flocking
        obj = mesh.find_object()
        info = sim.stats()

        # --- 动作 ---------------------------------------------------------
        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator(
            "sim_flocking.build",
            text="重建" if obj is not None else "构建",
            icon="MOD_BUILD",
        )
        row = layout.row(align=True)
        row.operator("sim_flocking.reset", text="重新撒点", icon="LOOP_BACK")
        row.operator("sim_flocking.step", text="+1 帧", icon="NEXT_KEYFRAME")
        row.operator("sim_flocking.setup_scene", text="", icon="CAMERA_DATA")

        if obj is None:
            layout.label(text="还没有鸟群：先点构建", icon="INFO")
            return

        stuck = _stuck_solid_views(context)
        if stuck:
            row = layout.row(align=True)
            row.alert = True
            row.operator("sim_flocking.fix_viewport", icon="COLOR")
            layout.label(
                text="Solid 模式看不出速度色：着色颜色得用 Attribute", icon="ERROR"
            )

        # --- 撒鸟 ---------------------------------------------------------
        box = layout.box()
        box.label(text="撒鸟（在视口里按住左键）", icon="BRUSH_DATA")
        row = box.row(align=True)
        row.scale_y = 1.25
        if not settings.painting:
            row.operator("sim_flocking.paint", text="开始撒鸟", icon="PLAY")
        else:
            row.prop(
                settings, "painting", text="退出撒鸟 (ESC)", icon="CANCEL", toggle=True
            )
        if settings.painting:
            box.label(
                text="按住左键拖拽：落点即位置"
                if not settings.dragging
                else "正在撒鸟…",
                icon="MOUSE_MOVE" if settings.dragging else "MOUSE_LMB",
            )
        row = box.row(align=True)
        row.prop(settings, "brush_radius")
        row.prop(settings, "brush_count")

        # --- 群体 / 空间 --------------------------------------------------
        box = layout.box()
        box.label(text="群体 / 空间", icon="OUTLINER_OB_GROUP_INSTANCE")
        row = box.row(align=True)
        row.prop(settings, "count")
        row.prop(settings, "seed")
        row = box.row(align=True)
        row.prop(settings, "width")
        row.prop(settings, "height")
        box.prop(settings, "boundary")

        # --- 行为 ---------------------------------------------------------
        box = layout.box()
        box.label(text="行为权重", icon="FORCE_CURVE")
        row = box.row(align=True)
        row.prop(settings, "sep_weight")
        row.prop(settings, "ali_weight")
        row.prop(settings, "coh_weight")
        column = box.column(align=True)
        column.prop(settings, "sep_rad")
        column.prop(settings, "ali_rad")
        column.prop(settings, "coh_rad")

        # --- 运动 ---------------------------------------------------------
        box = layout.box()
        box.label(text="运动", icon="PLAY")
        row = box.row(align=True)
        row.prop(settings, "max_speed")
        row.prop(settings, "max_force")
        row = box.row(align=True)
        row.prop(settings, "time_step")
        row.prop(settings, "substeps")
        box.prop(settings, "noise")
        box.prop(settings, "backend")

        # --- 外观 ---------------------------------------------------------
        box = layout.box()
        box.label(text="外观", icon="SHADING_TEXTURE")
        box.row(align=True).prop(settings, "colour_mode", expand=True)
        box.prop(settings, "size")

        # --- 读数 ---------------------------------------------------------
        box = layout.box()
        box.label(text="读数", icon="INFO")
        box.label(
            text=f"第 {info['frame']} 帧 · {info['count']} 只 · "
            f"邻居 {info['neighbours']:.1f} · 速率 {info['speed']:.2f}"
        )
        cost = f"约 {info['ms']:.1f} ms/帧" if info["ms"] else "待测（播一帧）"
        if info["build_ms"]:
            cost += f" · 上次构建 {info['build_ms']:.0f} ms"
        box.label(text=cost, icon="TIME")
        if settings.dirty:
            box.label(text="参数已改：下一帧自动生效", icon="ERROR")
        elif info["note"]:
            box.label(text=info["note"], icon="INFO")

        # --- 数据接口 -----------------------------------------------------
        box = layout.box()
        box.label(text="数据接口", icon="SPREADSHEET")
        box.scale_y = 0.7
        for name, note in (
            ("velocity", "FLOAT_VECTOR / POINT"),
            ("speed", "FLOAT / POINT"),
            ("color", "FLOAT_COLOR / CORNER"),
        ):
            row = box.row(align=True)
            row.label(text=name)
            row.label(text=note)

        layout.separator()
        layout.label(text="播放或拖时间轴即实时演化；回退会从第 0 帧重算", icon="PLAY")
        if settings.colour_mode == colour.MODE_SPEED:
            layout.label(text="「速度」配色以速度上限为满格", icon="COLOR")
        if info["error"]:
            layout.label(text=info["error"], icon="ERROR")
        layout.operator("sim_flocking.delete", icon="TRASH")
