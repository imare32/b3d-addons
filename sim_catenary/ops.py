"""算子：构建 / 重置 / 场景预设 / 删除。

算子只做编排与参数校验，物理与写 GP 都在 :mod:`sim` 里；能失败的地方收成
``report``，不把 traceback 甩给用户。
"""

import bpy

from . import chain, gp, sim, viewport
from . import scene as scene_mod

__all__ = [
    "SCAT_OT_build",
    "SCAT_OT_delete",
    "SCAT_OT_fix_viewport",
    "SCAT_OT_reset",
    "SCAT_OT_setup_scene",
]


def _viewport_note(status: str, fixed: int) -> str:
    """把视口着色检查结果拼成报告尾巴（一切正常时不加噪音）。

    report 文本保持纯 ASCII：终端/任务输出的编码不一定支持中文，混进去会乱码。
    """
    if status == viewport.FIXED:
        return f" | viewport shading set to Attribute ({fixed} view(s))"
    if status == viewport.NO_VIEW:
        return (
            " | tip: in Solid mode set viewport color to Attribute, else it stays black"
        )
    return ""


def _extent(settings) -> tuple[float, float]:
    """链条的 (x_end, z_end)：已建好就用现成的，否则按参数先算一遍。"""
    state = sim.STATE
    if state.get("solver") is not None:
        return float(state["x_end"]), float(state["z_end"])
    pitch = chain.pitch_for(
        settings.ring_major, settings.ring_minor, settings.ring_straight
    )
    _, _, x_end, z_end = chain.arch_chain(
        int(settings.n_rings) + 1, float(settings.arch_a), pitch
    )
    return float(x_end), float(z_end)


class SCAT_OT_build(bpy.types.Operator):
    """按当前参数构建（或重建）铁环悬链线模拟"""

    bl_idname = "sim_catenary.build"
    bl_label = "构建模拟"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.sim_catenary
        if settings.ring_minor >= settings.ring_major:
            self.report({"ERROR"}, "环管半径 r 必须小于环主半径 R")
            return {"CANCELLED"}

        # 先确认 Solid 模式看得见顶点色，否则布好链条也是一片黑（见 viewport 文档）
        view_status, view_fixed = viewport.ensure_vertex_colour(context)

        try:
            info = sim.build(settings, context.scene)
        except Exception as exc:
            self.report({"ERROR"}, f"构建失败: {exc}")
            return {"CANCELLED"}

        settings.dirty = False
        # 让画面跟当前帧对上（构建后状态是第 0 帧的初始拱形）
        context.scene.frame_set(context.scene.frame_current)
        message = (
            f"sim_catenary: {settings.n_rings} 环 / {info['strokes']} 笔划 / "
            f"{info['points']} 点 / {sim.STATE['ms']:.0f} ms"
        )
        message += _viewport_note(view_status, view_fixed)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class SCAT_OT_reset(bpy.types.Operator):
    """重置到初始的上拱形态（第 0 帧）"""

    bl_idname = "sim_catenary.reset"
    bl_label = "重置模拟"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.sim_catenary
        if sim.STATE.get("solver") is None:
            return bpy.ops.sim_catenary.build()
        sim.reset(settings, context.scene)
        context.scene.frame_set(0)
        self.report({"INFO"}, "重置到初始拱形")
        return {"FINISHED"}


class SCAT_OT_setup_scene(bpy.types.Operator):
    """建正交相机、铺项目画布底色、切 Standard 色彩并打开 GP 抗锯齿"""

    bl_idname = "sim_catenary.setup_scene"
    bl_label = "场景预设"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.sim_catenary
        x_end, z_end = _extent(settings)
        view_status, view_fixed = viewport.ensure_vertex_colour(context)
        scene_mod.setup(context.scene, x_end, z_end)
        _centre, span = scene_mod.frame_box(x_end, z_end)
        message = f"正交相机 ortho_scale={span:.3f}, 1600x1000, Standard"
        message += _viewport_note(view_status, view_fixed)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class SCAT_OT_fix_viewport(bpy.types.Operator):
    """把 Solid 视图的着色颜色切成「Attribute / 顶点色」"""

    bl_idname = "sim_catenary.fix_viewport"
    bl_label = "视口改用顶点色"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return bool(viewport.solid_spaces(context))

    def execute(self, context):
        status, fixed = viewport.ensure_vertex_colour(context)
        if status == viewport.FIXED:
            self.report({"INFO"}, f"viewport shading set to Attribute ({fixed} view)")
            return {"FINISHED"}
        if status == viewport.ALREADY:
            self.report({"INFO"}, "viewport already shows vertex colours")
            return {"CANCELLED"}
        if status == viewport.NOT_SOLID:
            self.report(
                {"INFO"}, "no Solid view; material/render preview already shows colours"
            )
            return {"CANCELLED"}
        self.report(
            {"WARNING"},
            "viewport colour is a custom mode; set it to Attribute by hand",
        )
        return {"CANCELLED"}


class SCAT_OT_delete(bpy.types.Operator):
    """删除生成的 GP 对象"""

    bl_idname = "sim_catenary.delete"
    bl_label = "删除链条对象"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        removed = gp.remove_object()
        sim.STATE.update(solver=None, signature=None, rings=None, spine=None, pos=None)
        context.scene.sim_catenary.dirty = True
        if not removed:
            self.report({"WARNING"}, "没有找到生成的对象")
            return {"CANCELLED"}
        return {"FINISHED"}
