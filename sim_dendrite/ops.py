"""算子：构建 / 重置 / 步进 / 场景预设 / 删除。

算子只做编排与参数校验，物理与写网格都在 :mod:`sim` 里；能失败的地方收成 ``report``，
不把 traceback 甩给用户。
"""

import bpy

from . import mesh, sim, viewport
from . import scene as scene_mod

__all__ = [
    "SDEN_OT_build",
    "SDEN_OT_delete",
    "SDEN_OT_fix_viewport",
    "SDEN_OT_reset",
    "SDEN_OT_setup_scene",
    "SDEN_OT_step",
]


def _viewport_note(status: str, fixed: int) -> str:
    """把视口着色检查结果拼成报告尾巴（一切正常时不加噪音）。

    report 文本保持纯 ASCII：终端/任务输出的编码不一定支持中文，混进去会乱码。
    """
    if status == viewport.FIXED:
        return f" | viewport shading set to Attribute ({fixed} view(s))"
    if status == viewport.NO_VIEW:
        return (
            " | tip: in Solid mode set viewport color to Attribute, else it stays flat"
        )
    return ""


def _start_frame(scene) -> None:
    """把时间轴放回起始帧：构建 / 重置之后画面停在「初始核」。"""
    scene.frame_set(int(scene.frame_start))


class SDEN_OT_build(bpy.types.Operator):
    """按当前参数构建（或重建）相场晶枝模拟"""

    bl_idname = "sim_dendrite.build"
    bl_label = "构建模拟"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.sim_dendrite
        obj = mesh.find_object()
        if obj is not None and obj.mode != "OBJECT":
            self.report({"ERROR"}, "请先退出编辑模式：重建会替换整个网格")
            return {"CANCELLED"}

        # 颜色只写在顶点属性里，Solid 用 Material 着色时看不出相场色（见 viewport 文档）
        status, fixed = viewport.ensure_vertex_colour(context)

        try:
            info = sim.build(settings, context.scene)
        except Exception as exc:
            self.report({"ERROR"}, f"构建失败: {exc}")
            return {"CANCELLED"}

        settings.dirty = False
        _start_frame(context.scene)
        message = (
            f"sim_dendrite: {settings.nx}x{settings.ny} / {info['backend']} / "
            f"{info['vertices']} vertices / {info['ms']:.0f} ms"
        )
        message += _viewport_note(status, fixed)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class SDEN_OT_reset(bpy.types.Operator):
    """重置到初始晶核（第 0 帧），不重建网格"""

    bl_idname = "sim_dendrite.reset"
    bl_label = "重置模拟"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.sim_dendrite
        if sim.STATE.get("solver") is None:
            return bpy.ops.sim_dendrite.build()

        sim.reset_fields(settings)
        sim.STATE["frame"] = 0
        sim.sync(settings)
        _start_frame(context.scene)
        self.report({"INFO"}, "已重置到初始晶核")
        return {"FINISHED"}


class SDEN_OT_step(bpy.types.Operator):
    """前进一帧（时间轴 +1，物理按子步推进）"""

    bl_idname = "sim_dendrite.step"
    bl_label = "步进一帧"
    bl_options = {"REGISTER"}

    def execute(self, context):
        if sim.STATE.get("solver") is None:
            return bpy.ops.sim_dendrite.build()
        scene = context.scene
        scene.frame_set(scene.frame_current + 1)
        return {"FINISHED"}


class SDEN_OT_setup_scene(bpy.types.Operator):
    """铺项目画布底色、建斜俯视正交相机、切 Standard 色彩"""

    bl_idname = "sim_dendrite.setup_scene"
    bl_label = "场景预设"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.sim_dendrite
        span = scene_mod.span_of(
            int(settings.nx), int(settings.ny), float(settings.dx), float(settings.dy)
        )
        status, fixed = viewport.ensure_vertex_colour(context)
        scene_mod.setup(context.scene, span, float(settings.z_scale) * 0.4)
        message = f"ortho camera span={span:.3f}, {scene_mod.RESOLUTION[0]}x{scene_mod.RESOLUTION[1]}, Standard"
        message += _viewport_note(status, fixed)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class SDEN_OT_fix_viewport(bpy.types.Operator):
    """把 Solid 视图的着色颜色切成「Attribute / 顶点色」"""

    bl_idname = "sim_dendrite.fix_viewport"
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


class SDEN_OT_delete(bpy.types.Operator):
    """删除生成的高度场对象"""

    bl_idname = "sim_dendrite.delete"
    bl_label = "删除网格对象"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        removed = mesh.remove_object()
        sim.STATE.update(
            solver=None,
            signature=None,
            topology=None,
            frame=0,
            backend="",
            note="",
            solid=0.0,
            dT_range=(0.0, 0.0),
            ms=0.0,
            error="",
        )
        context.scene.sim_dendrite.dirty = True
        if not removed:
            self.report({"WARNING"}, "没有找到生成的对象")
            return {"CANCELLED"}
        return {"FINISHED"}
