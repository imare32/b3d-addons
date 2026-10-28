"""算子：构建 / 重置 / 步进 / 场景预设 / 视口修正 / 删除。

算子只做编排与参数校验，演化与写网格都在 :mod:`sim` 里；能失败的地方收成 ``report``，
不把 traceback 甩给用户。
"""

import bpy

from . import mesh, sim, viewport
from . import scene as scene_mod

__all__ = [
    "GOL_OT_build",
    "GOL_OT_delete",
    "GOL_OT_fix_viewport",
    "GOL_OT_reset",
    "GOL_OT_setup_scene",
    "GOL_OT_step",
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
    """把时间轴放回起始帧：构建 / 重置之后画面停在第 0 代。"""
    scene.frame_set(int(scene.frame_start))


class GOL_OT_build(bpy.types.Operator):
    """按当前参数构建（或重建）生命游戏模拟"""

    bl_idname = "sim_game_of_life.build"
    bl_label = "构建模拟"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.sim_game_of_life
        obj = sim.target_object(settings)
        if obj is not None and obj.mode != "OBJECT":
            self.report({"ERROR"}, "请先退出编辑模式：拓扑与面属性都写在网格数据上")
            return {"CANCELLED"}

        # 颜色只写在面角点属性里，Solid 用 Material 着色时看不出生死色（见 viewport 文档）
        status, fixed = viewport.ensure_vertex_colour(context)

        try:
            info = sim.build(settings, context.scene, obj)
        except Exception as exc:
            self.report({"ERROR"}, f"构建失败: {exc}")
            return {"CANCELLED"}

        settings.dirty = False
        _start_frame(context.scene)
        message = (
            f"sim_game_of_life: {info['cells']} cells / {info['backend']} / "
            f"{info['ms']:.0f} ms"
        )
        message += _viewport_note(status, fixed)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class GOL_OT_reset(bpy.types.Operator):
    """重新播第 0 代（同一颗种子重来一遍），不重建拓扑"""

    bl_idname = "sim_game_of_life.reset"
    bl_label = "重置到第 0 代"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.sim_game_of_life
        if sim.STATE.get("solver") is None:
            return bpy.ops.sim_game_of_life.build()

        sim.reset_fields(settings)
        sim.sync(settings)
        _start_frame(context.scene)
        self.report({"INFO"}, "已回到第 0 代")
        return {"FINISHED"}


class GOL_OT_step(bpy.types.Operator):
    """前进一代（时间轴 +「每代帧数」帧）"""

    bl_idname = "sim_game_of_life.step"
    bl_label = "步进一代"
    bl_options = {"REGISTER"}

    def execute(self, context):
        settings = context.scene.sim_game_of_life
        if sim.STATE.get("solver") is None:
            return bpy.ops.sim_game_of_life.build()
        scene = context.scene
        scene.frame_set(scene.frame_current + max(1, int(settings.frames_per_gen)))
        return {"FINISHED"}


class GOL_OT_setup_scene(bpy.types.Operator):
    """铺项目画布底色、按目标取景、切 Standard 色彩"""

    bl_idname = "sim_game_of_life.setup_scene"
    bl_label = "场景预设"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.sim_game_of_life
        obj = sim.target_object(settings)
        if obj is None:
            self.report({"ERROR"}, "还没有目标网格：先点构建")
            return {"CANCELLED"}

        status, fixed = viewport.ensure_vertex_colour(context)
        scene_mod.setup(context.scene, obj)
        message = (
            f"camera framed on '{obj.name}', "
            f"{scene_mod.RESOLUTION[0]}x{scene_mod.RESOLUTION[1]}, Standard"
        )
        message += _viewport_note(status, fixed)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class GOL_OT_fix_viewport(bpy.types.Operator):
    """把 Solid 视图的着色颜色切成「Attribute / 顶点色」"""

    bl_idname = "sim_game_of_life.fix_viewport"
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


class GOL_OT_delete(bpy.types.Operator):
    """删除插件自建的缺省网格（目标是你自己的网格时不删）"""

    bl_idname = "sim_game_of_life.delete"
    bl_label = "删除缺省网格"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.sim_game_of_life
        removed = mesh.remove_object()
        sim.STATE.update(
            solver=None,
            topology=None,
            neighbours={"faces": 0, "standard": 0.0, "min": 0, "max": 0},
            generation=0,
            frame=0,
            age=None,
            backend="",
            note="",
            alive=0.0,
            ms=0.0,
            error="",
        )
        if not removed:
            self.report({"WARNING"}, "没有找到插件自建的网格对象")
            return {"CANCELLED"}
        if settings.target is not None and settings.target.name == mesh.OBJECT_NAME:
            settings.target = None
        settings.dirty = True
        return {"FINISHED"}
