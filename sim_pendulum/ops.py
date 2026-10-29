"""算子：构建 / 重置 / 步进 / 场景预设 / 视口修正 / 对照实测 / 删除。

算子只做编排与参数校验，物理与写缓冲都在 :mod:`sim` 里；能失败的地方收成 ``report``，
不把 traceback 甩给用户。

``report`` 文本保持纯 ASCII：终端 / 任务输出的编码不一定支持中文，混进去会乱码。
"""

import bpy

from . import gp, image, mesh, sim, viewport
from . import scene as scene_mod

__all__ = [
    "PEND_OT_benchmark",
    "PEND_OT_build",
    "PEND_OT_delete",
    "PEND_OT_fix_viewport",
    "PEND_OT_reset",
    "PEND_OT_setup_scene",
    "PEND_OT_step",
]


def _viewport_note(status: str, fixed: int) -> str:
    """把视口着色检查结果拼成报告尾巴（一切正常时不加噪音）。"""
    if status == viewport.FIXED:
        return f" | viewport shading set to Attribute ({fixed} view(s))"
    if status == viewport.NO_VIEW:
        return " | tip: in Solid mode set viewport color to Attribute, else the ink is black"
    return ""


def _start_frame(scene) -> None:
    """把时间轴放回起始帧：构建 / 重置之后画面停在「初始阵列」。"""
    scene.frame_set(int(scene.frame_start))


def _clear_state() -> None:
    """清掉会话状态：对象没了，旧场 / 旧拓扑不该被复用。"""
    sim.STATE.update(
        field=None,
        signature=None,
        frame=0,
        output="",
        solver="",
        note="",
        speed_peak=0.0,
        energy=0.0,
        energy0=0.0,
        param_key=None,
        drift=0.0,
        ms=0.0,
        step_ms=0.0,
        buffer_ms=0.0,
        sync_ms=0.0,
        error="",
        busy=False,
    )


class PEND_OT_build(bpy.types.Operator):
    """按当前参数构建（或重建）双摆相空间阵列模拟"""

    bl_idname = "sim_pendulum.build"
    bl_label = "构建模拟"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.sim_pendulum
        backend = sim.backend_of(settings)
        obj = backend.find_object()
        if obj is not None and getattr(obj, "mode", "OBJECT") != "OBJECT":
            self.report({"ERROR"}, "请先退出编辑模式：重建会替换整条笔划")
            return {"CANCELLED"}

        # 颜色只写在属性里，Solid 用 Material 着色时线是全黑的（见 viewport 文档）；
        # 图像后端没有对象也没有属性，不用碰视口
        if str(settings.output) == sim.OUTPUT_IMAGE:
            status, fixed = viewport.ALREADY, 0
        else:
            status, fixed = viewport.ensure_vertex_colour(context)

        try:
            info = sim.build(settings, context.scene)
        except Exception as exc:
            self.report({"ERROR"}, f"构建失败: {exc}")
            return {"CANCELLED"}

        settings.dirty = False
        _start_frame(context.scene)
        message = (
            f"sim_pendulum: {info['pendulums']} pendulums / {info['points']} points / "
            f"{info['output']} / {info['ms']:.0f} ms"
        )
        if str(settings.output) == sim.OUTPUT_IMAGE:
            # 图像后端不该去改视口着色：结果直接推到图像编辑器
            switched = image.show_in_editors(context)
            if switched:
                message += f" | image shown in {switched} Image Editor(s)"
            else:
                message += " | 打开一个 Image Editor（Shift+F10）就能看到结果"
        else:
            message += _viewport_note(status, fixed)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class PEND_OT_reset(bpy.types.Operator):
    """重置到初始阵列（第 0 帧），不重建拓扑"""

    bl_idname = "sim_pendulum.reset"
    bl_label = "重置模拟"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.sim_pendulum
        if sim.STATE.get("field") is None:
            return bpy.ops.sim_pendulum.build()

        sim.reset(settings, context.scene)
        _start_frame(context.scene)
        self.report({"INFO"}, "已重置到初始阵列")
        return {"FINISHED"}


class PEND_OT_step(bpy.types.Operator):
    """前进一帧（时间轴 +1，物理按子步推进）"""

    bl_idname = "sim_pendulum.step"
    bl_label = "步进一帧"
    bl_options = {"REGISTER"}

    def execute(self, context):
        if sim.STATE.get("field") is None:
            return bpy.ops.sim_pendulum.build()
        scene = context.scene
        scene.frame_set(scene.frame_current + 1)
        return {"FINISHED"}


class PEND_OT_setup_scene(bpy.types.Operator):
    """铺项目画布底色、建正面正交相机、切 Standard 色彩"""

    bl_idname = "sim_pendulum.setup_scene"
    bl_label = "场景预设"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.sim_pendulum
        if str(settings.output) == sim.OUTPUT_IMAGE:
            # 图像后端没有 3D 对象：不建相机、不动视口，只把图推到图像编辑器
            switched = image.show_in_editors(context)
            self.report(
                {"INFO"},
                f"图像模式没有相机可摆 | 已在 {switched} 个图像编辑器里显示"
                if switched
                else "图像模式：按 Shift+F10 开一个图像编辑器看结果",
            )
            return {"FINISHED"}

        status, fixed = viewport.ensure_vertex_colour(context)
        active_field = sim.field()
        reach = (
            active_field.reach
            if active_field is not None
            else float(settings.l1) + float(settings.l2)
        )
        resolution = scene_mod.setup(
            context.scene,
            int(settings.n_grid),
            float(settings.spacing),
            reach,
        )
        span, _target = scene_mod.frame_of(
            int(settings.n_grid), float(settings.spacing), reach
        )
        message = f"ortho span={span:.2f}, {resolution[0]}x{resolution[1]}, Standard"
        message += _viewport_note(status, fixed)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class PEND_OT_benchmark(bpy.types.Operator):
    """在同一份缓冲上对比 GP 与网格的逐帧同步耗时"""

    bl_idname = "sim_pendulum.benchmark"
    bl_label = "对照实测同步耗时"
    bl_options = {"REGISTER"}

    def execute(self, context):
        settings = context.scene.sim_pendulum
        if sim.STATE.get("field") is None:
            self.report({"WARNING"}, "先构建模拟，再量同步")
            return {"CANCELLED"}
        try:
            report = sim.benchmark(settings)
        except Exception as exc:
            self.report({"ERROR"}, f"benchmark failed: {exc}")
            return {"CANCELLED"}
        gp_ms = report.get(gp.OBJECT_NAME, 0.0)
        mesh_ms = report.get(mesh.OBJECT_NAME, 0.0)
        sim.STATE["bench_gp_ms"] = gp_ms
        sim.STATE["bench_mesh_ms"] = mesh_ms
        self.report(
            {"INFO"},
            f"sync per frame: GP {gp_ms:.2f} ms | mesh {mesh_ms:.2f} ms",
        )
        return {"FINISHED"}


class PEND_OT_open_image(bpy.types.Operator):
    """把模拟结果推到图像编辑器里显示"""

    bl_idname = "sim_pendulum.open_image"
    bl_label = "在图像编辑器里查看"
    bl_options = {"REGISTER"}

    def execute(self, context):
        result = image.find_object()
        if result is None:
            self.report({"WARNING"}, "还没有结果图像：先构建模拟")
            return {"CANCELLED"}
        switched = image.show_in_editors(context, result)
        if not switched:
            self.report(
                {"INFO"},
                f"没有打开的图像编辑器：按 Shift+F10 开一个，图像叫「{result.name}」",
            )
            return {"CANCELLED"}
        self.report({"INFO"}, f"已在 {switched} 个图像编辑器里显示「{result.name}」")
        return {"FINISHED"}


class PEND_OT_fix_viewport(bpy.types.Operator):
    """把 Solid 视图的着色颜色切成「Attribute / 顶点色」"""

    bl_idname = "sim_pendulum.fix_viewport"
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


class PEND_OT_delete(bpy.types.Operator):
    """删除生成的摆阵列对象（两种后端都清）"""

    bl_idname = "sim_pendulum.delete"
    bl_label = "删除阵列对象"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        removed = gp.remove_object()
        removed = mesh.remove_object() or removed
        removed = image.remove_object() or removed
        _clear_state()
        context.scene.sim_pendulum.dirty = True
        if not removed:
            self.report({"WARNING"}, "没有找到生成的对象")
            return {"CANCELLED"}
        return {"FINISHED"}
