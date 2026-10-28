"""算子：构建 / 重置 / 步进 / 场景预设 / 删除。

算子只做编排与参数校验，物理与写网格都在 :mod:`sim` 里；能失败的地方收成 ``report``，
不把 traceback 甩给用户。
"""

import bpy

from . import mesh, sim, viewport
from . import scene as scene_mod

__all__ = [
    "SJAC_OT_build",
    "SJAC_OT_delete",
    "SJAC_OT_fix_viewport",
    "SJAC_OT_reset",
    "SJAC_OT_setup_scene",
    "SJAC_OT_step",
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
    """把时间轴放回起始帧：构建 / 重置之后画面停在「初始布局」。"""
    scene.frame_set(int(scene.frame_start))


class SJAC_OT_build(bpy.types.Operator):
    """按当前参数构建（或重建）Jacobi theta 除子环面"""

    bl_idname = "sim_jacobi.build"
    bl_label = "构建环面"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.sim_jacobi
        for obj in (mesh.find_object(), mesh.find_zero_object()):
            if obj is not None and obj.mode != "OBJECT":
                self.report({"ERROR"}, "请先退出编辑模式：重建会替换整个网格")
                return {"CANCELLED"}

        # 颜色只写在顶点属性里，Solid 用 Material 着色时看不出来（见 viewport 文档）
        status, fixed = viewport.ensure_vertex_colour(context)

        try:
            info = sim.build(settings, context.scene)
        except Exception as exc:
            self.report({"ERROR"}, f"构建失败: {exc}")
            return {"CANCELLED"}

        settings.dirty = False
        _start_frame(context.scene)
        message = (
            f"sim_jacobi: {settings.nu}x{settings.nv} / {info['backend']} / "
            f"{info['vertices']} vertices / {info['zeros']} zeros / {info['ms']:.0f} ms"
        )
        message += _viewport_note(status, fixed)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class SJAC_OT_reset(bpy.types.Operator):
    """重置零点到初始布局（第 0 帧），不重建网格"""

    bl_idname = "sim_jacobi.reset"
    bl_label = "重置零点"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.sim_jacobi
        if sim.STATE.get("solver") is None:
            return bpy.ops.sim_jacobi.build()

        sim.apply_params(settings)
        sim.reset_particles(settings)
        sim.STATE["frame"] = 0
        sim.sync(settings)
        _start_frame(context.scene)
        self.report({"INFO"}, "已重置到初始布局")
        return {"FINISHED"}


class SJAC_OT_step(bpy.types.Operator):
    """前进一帧（时间轴 +1，零点按子步推进）"""

    bl_idname = "sim_jacobi.step"
    bl_label = "步进一帧"
    bl_options = {"REGISTER"}

    def execute(self, context):
        if sim.STATE.get("solver") is None:
            return bpy.ops.sim_jacobi.build()
        scene = context.scene
        scene.frame_set(scene.frame_current + 1)
        return {"FINISHED"}


class SJAC_OT_setup_scene(bpy.types.Operator):
    """铺项目画布底色、建 3/4 视角相机、切 Standard 色彩"""

    bl_idname = "sim_jacobi.setup_scene"
    bl_label = "场景预设"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.sim_jacobi
        radius = scene_mod.outer_radius(
            float(settings.major_r), float(settings.minor_r)
        )
        status, fixed = viewport.ensure_vertex_colour(context)
        scene_mod.setup(context.scene, radius)
        message = (
            f"camera distance={scene_mod.distance_of(radius):.2f}, "
            f"{scene_mod.RESOLUTION[0]}x{scene_mod.RESOLUTION[1]}, Standard"
        )
        message += _viewport_note(status, fixed)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class SJAC_OT_fix_viewport(bpy.types.Operator):
    """把 Solid 视图的着色颜色切成「Attribute / 顶点色」"""

    bl_idname = "sim_jacobi.fix_viewport"
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


class SJAC_OT_delete(bpy.types.Operator):
    """删除生成的环面与零点小球对象"""

    bl_idname = "sim_jacobi.delete"
    bl_label = "删除环面"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        removed = mesh.remove_objects()
        sim.STATE.update(
            solver=None,
            signature=None,
            frame=0,
            backend="",
            note="",
            zeros=0,
            ms=0.0,
            error="",
        )
        context.scene.sim_jacobi.dirty = True
        if not removed:
            self.report({"WARNING"}, "没有找到生成的对象")
            return {"CANCELLED"}
        return {"FINISHED"}
