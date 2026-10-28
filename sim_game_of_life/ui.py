"""侧栏面板：``View3D > 侧栏 > GOL``（标签页用缩写，省竖向空间）。

面板只负责显示与调用算子：节奏 / 规则 / 显示参数改完**下一帧**就生效（播放或拖时间轴
即可看到），拓扑 / 初始条件要重建或重置（改了下一帧会自动处理，也可以手动点）。
"""

import bpy

from . import colour, sim, viewport

__all__ = ["GOL_PT_main"]


def _stuck_solid_views(context) -> int:
    """还停在「Solid + 着色颜色 = Material」的 3D 视图数量（这些视图里看不到生死色）。"""
    return sum(
        1
        for space in viewport.solid_spaces(context)
        if space.shading.type == viewport.SOLID
        and space.shading.color_type == viewport.MATERIAL
    )


class GOL_PT_main(bpy.types.Panel):
    bl_idname = "GOL_PT_main"
    bl_label = "Game of Life"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GOL"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.sim_game_of_life
        obj = sim.target_object(settings)
        info = sim.stats()

        # --- 动作 ---------------------------------------------------------
        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator(
            "sim_game_of_life.build",
            text="重建" if obj is not None else "构建",
            icon="MOD_BUILD",
        )
        row = layout.row(align=True)
        row.operator("sim_game_of_life.reset", text="第 0 代", icon="LOOP_BACK")
        row.operator("sim_game_of_life.step", text="+1 代", icon="NEXT_KEYFRAME")
        row.operator("sim_game_of_life.setup_scene", text="", icon="CAMERA_DATA")

        if obj is None:
            layout.label(text="还没有目标网格：先点构建", icon="INFO")
            layout.operator("sim_game_of_life.delete", icon="TRASH")
            return

        stuck = _stuck_solid_views(context)
        if stuck:
            row = layout.row(align=True)
            row.alert = True
            row.operator("sim_game_of_life.fix_viewport", icon="COLOR")
            layout.label(
                text="Solid 模式看不出生死色：着色颜色得用 Attribute", icon="ERROR"
            )

        # --- 目标 / 规则 --------------------------------------------------
        box = layout.box()
        box.label(text="目标 / 规则", icon="MESH_DATA")
        box.prop(settings, "target")
        box.prop(settings, "rule")
        row = box.row(align=True)
        row.scale_y = 0.7
        row.label(text=f"{info['cells']} 面")
        row.label(text=f"{info['standard'] * 100:.0f}% 是 8 邻域")
        row.label(text=info["backend"] or "未构建")
        if info["cells"] and info["standard"] < 0.8:
            # 康威规则假设的是 8 邻域：三角面（11~12）、边界面（3~5）、高价顶点都会
            # 让同一个规则在不同面上"方言"不同，图案会明显更碎（README 有实测）。
            column = box.column(align=True)
            column.alert = True
            column.label(
                text=f"邻居数 {info['neighbours_min']}~{info['neighbours_max']}：图案会更碎",
                icon="ERROR",
            )
            column.label(
                text="想要棋盘效果：先给目标加一级 Subdivision", icon="MOD_SUBSURF"
            )

        # --- 演化 ---------------------------------------------------------
        box = layout.box()
        box.label(text="演化", icon="SEQUENCE")
        row = box.row(align=True)
        row.prop(settings, "density")
        row.prop(settings, "seed")
        row = box.row(align=True)
        row.prop(settings, "frames_per_gen")
        row.prop(settings, "transition_frames")
        box.prop(settings, "interpolation_mode")
        box.prop(settings, "backend")

        # --- 外观 ---------------------------------------------------------
        box = layout.box()
        box.label(text="外观", icon="SHADING_TEXTURE")
        row = box.row(align=True)
        row.prop(settings, "colour_mode", expand=True)
        if settings.colour_mode == colour.MODE_AGE:
            box.prop(settings, "age_scale")

        # --- 读数 ---------------------------------------------------------
        box = layout.box()
        box.label(text="读数", icon="INFO")
        box.label(text=f"第 {info['generation']} 代 · 存活 {info['alive'] * 100:.1f}%")
        cost = f"约 {info['ms']:.1f} ms/代" if info["ms"] else "待测（播一帧）"
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
            ("alive", "FLOAT / FACE"),
            ("age", "FLOAT / FACE"),
            ("color", "FLOAT_COLOR / CORNER"),
        ):
            row = box.row(align=True)
            row.label(text=name)
            row.label(text=note)

        layout.separator()
        layout.label(text="播放或拖时间轴即实时演化；回退会从第 0 代重算", icon="PLAY")
        if info["error"]:
            layout.label(text=info["error"], icon="ERROR")
        layout.operator("sim_game_of_life.delete", icon="TRASH")
