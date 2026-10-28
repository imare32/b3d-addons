"""侧栏面板：``View3D > 侧栏 > Colonize``。

面板只负责显示与调用算子：算法参数改完**下一帧**就生效（播放或拖时间轴即可看到），
显示参数当场重刷，输入 / 取样要重建（改了下一帧会自动重建，也可以手动点）。
"""

import bpy

from . import gp, sim, viewport
from . import surface as surface_mod

__all__ = ["SSCO_PT_main"]


def _stuck_views(context) -> int:
    """还停在「Solid + 着色颜色 = Material」的 3D 视图数量（这些视图里看不出深度色）。"""
    return sum(
        1
        for space in viewport.solid_spaces(context)
        if space.shading.type == viewport.SOLID
        and space.shading.color_type == viewport.MATERIAL
    )


class SSCO_PT_main(bpy.types.Panel):
    bl_idname = "SSCO_PT_main"
    bl_label = "Sim Space Colonization"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Colonize"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.sim_space_colonization
        obj = gp.find_object()
        info = sim.stats()

        # --- 动作 ---------------------------------------------------------
        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator(
            "sim_space_colonization.build",
            text="重建" if obj is not None else "构建生长",
            icon="MOD_BUILD",
        )
        row = layout.row(align=True)
        row.operator("sim_space_colonization.reset", text="从头再长", icon="LOOP_BACK")
        row.operator("sim_space_colonization.step", text="+1 帧", icon="NEXT_KEYFRAME")
        layout.operator(
            "sim_space_colonization.setup_scene", text="场景预设", icon="CAMERA_DATA"
        )

        # --- 输入 ---------------------------------------------------------
        box = layout.box()
        box.label(text="输入网格", icon="OUTLINER_OB_MESH")
        row = box.row(align=True)
        row.prop(settings, "target")
        # 线框开关：目标网格套在藤蔓外面时，切成线框才看得见里面的笔划
        row.operator(
            "sim_space_colonization.toggle_target_wire",
            text="",
            icon="SHADING_WIRE",
            depress=settings.target is not None
            and settings.target.display_type == "WIRE",
        )
        if settings.target is None:
            box.label(text="留空 = 构建时自动建缺省网格", icon="INFO")
        else:
            group = str(settings.root_group)
            has_group = bool(surface_mod.root_group_name(settings.target, group))
            row = box.row(align=True)
            row.prop(settings, "root_group", text="根顶点组")
            row.operator(
                "sim_space_colonization.make_root",
                text="",
                icon="ADD" if not has_group else "FILE_REFRESH",
            )
            if has_group:
                box.label(text=f"'{group}'：用组里的顶点当根", icon="GROUP_VERTEX")
            else:
                box.label(text="没有这个组：退化成最低点当根", icon="INFO")
        box.prop(settings, "mode")

        if obj is None:
            layout.label(text="还没有结果对象：先点构建", icon="INFO")
            return

        stuck = _stuck_views(context)
        if stuck:
            row = layout.row(align=True)
            row.alert = True
            row.operator("sim_space_colonization.fix_viewport", icon="COLOR")
            layout.label(
                text="Solid 模式看不出深度色：着色颜色得用 Attribute", icon="ERROR"
            )

        # --- 吸收点 / 搜索 --------------------------------------------------
        box = layout.box()
        box.label(text="吸收点 / 搜索", icon="WORLD")
        row = box.row(align=True)
        row.prop(settings, "max_attractors")
        row.prop(settings, "seed")
        box.prop(settings, "backend")
        box.prop(settings, "substeps")
        cost = f"约 {info['ms']:.1f} ms/帧" if info["ms"] else "待测（播一帧）"
        box.label(text=f"{info['backend'] or '-'} · {cost}", icon="TIME")
        if settings.dirty:
            box.label(text="参数已改：下一帧自动重建", icon="ERROR")
        elif info["build_ms"]:
            box.label(text=f"上次构建 {info['build_ms']:.0f} ms", icon="MOD_BUILD")

        # --- 算法 ---------------------------------------------------------
        box = layout.box()
        box.label(text="算法（相对对角线）", icon="PHYSICS")
        row = box.row(align=True)
        row.prop(settings, "influence_radius")
        row.prop(settings, "kill_radius")
        row = box.row(align=True)
        row.prop(settings, "growth_step")
        row.prop(settings, "dir_persistence")
        advanced = box.box()
        advanced.label(text="抖动 / 表面 / 上限", icon="MOD_MESHDEFORM")
        row = advanced.row(align=True)
        row.prop(settings, "jitter")
        row.prop(settings, "surface_offset")
        row = advanced.row(align=True)
        row.prop(settings, "max_nodes")
        advanced.label(text="消耗半径 < 吸引半径；子步 = 生长速度", icon="INFO")

        # --- 外观 ---------------------------------------------------------
        box = layout.box()
        box.label(text="外观（逐点，GP 笔划）", icon="SHADING_TEXTURE")
        row = box.row(align=True)
        row.prop(settings, "thickness")
        row.prop(settings, "taper")
        box.prop(settings, "radius_scale")
        row = box.row(align=True)
        row.prop(settings, "colour_mode", expand=True)

        # --- 读数 ---------------------------------------------------------
        box = layout.box()
        box.label(text="读数", icon="INFO")
        box.label(
            text=f"当前帧 {info['frame']} · 节点 {info['nodes']} · 笔划 {info['strokes']}"
        )
        box.label(text=f"剩余吸收点 {info['attractors']} 个（吃完就停）")
        if info["note"]:
            box.label(text=info["note"], icon="INFO")

        # --- 数据接口 -----------------------------------------------------
        box = layout.box()
        box.label(text="数据接口（GP 属性）", icon="SPREADSHEET")
        box.scale_y = 0.7
        for name, note in (
            (gp.ATTR_POSITION, "FLOAT_VECTOR / POINT · 世界坐标"),
            (gp.ATTR_RADIUS, "FLOAT / POINT · 笔划半径（根粗梢细）"),
            (gp.ATTR_COLOUR, "FLOAT_COLOR / POINT · 线性 RGBA（深度渐变）"),
            (gp.ATTR_OPACITY, "FLOAT / POINT · 不透明度"),
        ):
            row = box.row(align=True)
            row.label(text=name)
            row.label(text=note)

        layout.separator()
        layout.label(text="播放或拖时间轴即实时演算；回退会从头重算", icon="PLAY")
        if info["error"]:
            layout.label(text=info["error"], icon="ERROR")
        layout.operator("sim_space_colonization.delete", icon="TRASH")
