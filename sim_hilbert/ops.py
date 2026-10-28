"""算子：构建 / 重置 / 步进 / 场景预设 / 视口修正 / 删除。

算子只做编排与参数校验，物理与写笔划都在 :mod:`sim` 里；能失败的地方收成 ``report``，
不把 traceback 甩给用户。
"""

import bpy

from . import gp, sim, viewport
from . import scene as scene_mod

__all__ = [
    "SHIL_OT_build",
    "SHIL_OT_delete",
    "SHIL_OT_fix_viewport",
    "SHIL_OT_reset",
    "SHIL_OT_setup_scene",
    "SHIL_OT_step",
]


def _viewport_note(status: str, fixed: int) -> str:
    """把视口着色检查结果拼成报告尾巴（一切正常时不加噪音）。

    report 文本保持纯 ASCII：终端/任务输出的编码不一定支持中文，混进去会乱码。
    """
    if status == viewport.FIXED:
        return f" | viewport shading set to Attribute ({fixed} view(s))"
    if status == viewport.NO_VIEW:
        return " | tip: in Solid mode set viewport color to Attribute, else the ink is black"
    return ""


def _start_frame(scene) -> None:
    """把时间轴放回起始帧：构建 / 重置之后画面停在「初始格子」。"""
    scene.frame_set(int(scene.frame_start))


def _clear_state() -> None:
    """清掉会话状态：对象没了，旧求解器 / 旧拓扑不该被复用。"""
    sim.STATE.update(
        network=None,
        solver=None,
        signature=None,
        frame=0,
        backend="",
        note="",
        speed_peak=0.0,
        strain_mean=0.0,
        strain_peak=0.0,
        ms=0.0,
        error="",
        busy=False,
    )


class SHIL_OT_build(bpy.types.Operator):
    """按当前参数构建（或重建）3D 希尔伯特曲线弹簧质点模拟"""

    bl_idname = "sim_hilbert.build"
    bl_label = "构建模拟"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.sim_hilbert
        obj = gp.find_object()
        if obj is not None and obj.mode != "OBJECT":
            self.report({"ERROR"}, "请先退出 GP 编辑模式：重建会替换整条笔划")
            return {"CANCELLED"}

        # 颜色只写在 vertex_color 属性里，Solid 用 Material 着色时线是全黑的（见 viewport 文档）
        status, fixed = viewport.ensure_vertex_colour(context)

        try:
            info = sim.build(settings, context.scene)
        except Exception as exc:
            self.report({"ERROR"}, f"构建失败: {exc}")
            return {"CANCELLED"}

        settings.dirty = False
        _start_frame(context.scene)
        message = (
            f"sim_hilbert: {info['vertices']} verts / {info['springs']} springs / "
            f"{info['backend']} / {info['ms']:.0f} ms"
        )
        message += _viewport_note(status, fixed)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class SHIL_OT_reset(bpy.types.Operator):
    """重置到初始格子（第 0 帧），不重建拓扑"""

    bl_idname = "sim_hilbert.reset"
    bl_label = "重置模拟"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.sim_hilbert
        if sim.STATE.get("solver") is None:
            return bpy.ops.sim_hilbert.build()

        sim.reset(settings, context.scene)
        _start_frame(context.scene)
        self.report({"INFO"}, "已重置到初始格子")
        return {"FINISHED"}


class SHIL_OT_step(bpy.types.Operator):
    """前进一帧（时间轴 +1，物理按子步推进）"""

    bl_idname = "sim_hilbert.step"
    bl_label = "步进一帧"
    bl_options = {"REGISTER"}

    def execute(self, context):
        if sim.STATE.get("solver") is None:
            return bpy.ops.sim_hilbert.build()
        scene = context.scene
        scene.frame_set(scene.frame_current + 1)
        return {"FINISHED"}


class SHIL_OT_setup_scene(bpy.types.Operator):
    """铺项目画布底色、建斜俯视正交相机与地面纸面、切 Standard 色彩"""

    bl_idname = "sim_hilbert.setup_scene"
    bl_label = "场景预设"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.sim_hilbert
        status, fixed = viewport.ensure_vertex_colour(context)
        scene_mod.setup(
            context.scene,
            float(settings.size),
            float(settings.drop_height),
            float(settings.ground_height),
        )
        span, _target = scene_mod.frame_of(
            float(settings.size), float(settings.drop_height)
        )
        message = (
            f"ortho span={span:.2f}, "
            f"{scene_mod.RESOLUTION[0]}x{scene_mod.RESOLUTION[1]}, Standard"
        )
        message += _viewport_note(status, fixed)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class SHIL_OT_fix_viewport(bpy.types.Operator):
    """把 Solid 视图的着色颜色切成「Attribute / 顶点色」"""

    bl_idname = "sim_hilbert.fix_viewport"
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


class SHIL_OT_delete(bpy.types.Operator):
    """删除生成的曲线对象与地面纸面"""

    bl_idname = "sim_hilbert.delete"
    bl_label = "删除曲线对象"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        removed = gp.remove_object()
        removed = scene_mod.remove_ground() or removed
        _clear_state()
        context.scene.sim_hilbert.dirty = True
        if not removed:
            self.report({"WARNING"}, "没有找到生成的对象")
            return {"CANCELLED"}
        return {"FINISHED"}
