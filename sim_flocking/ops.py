"""算子：构建 / 重置 / 步进 / 撒鸟（模态）/ 场景预设 / 视口修正 / 删除。

算子只做编排与参数校验，演化与写网格都在 :mod:`sim` 里；能失败的地方收成 ``report``，
不把 traceback 甩给用户。
"""

import time

import bpy
from bpy_extras import view3d_utils

from . import mesh, sim, viewport
from . import scene as scene_mod

__all__ = [
    "FLOCK_OT_build",
    "FLOCK_OT_delete",
    "FLOCK_OT_fix_viewport",
    "FLOCK_OT_paint",
    "FLOCK_OT_reset",
    "FLOCK_OT_setup_scene",
    "FLOCK_OT_step",
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
    """把时间轴放回起始帧：构建 / 重置之后画面停在第 0 帧。"""
    scene.frame_set(int(scene.frame_start))


class FLOCK_OT_build(bpy.types.Operator):
    """按当前参数构建（或重建）鸟群模拟"""

    bl_idname = "sim_flocking.build"
    bl_label = "构建模拟"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.sim_flocking
        obj = mesh.find_object()
        if obj is not None and obj.mode != "OBJECT":
            self.report({"ERROR"}, "请先退出编辑模式：几何与属性都写在网格数据上")
            return {"CANCELLED"}

        # 颜色只写在角点属性里，Solid 用 Material 着色时看不出速度色（见 viewport 文档）
        status, fixed = viewport.ensure_vertex_colour(context)

        try:
            info = sim.build(settings, context.scene)
        except Exception as exc:
            self.report({"ERROR"}, f"构建失败: {exc}")
            return {"CANCELLED"}

        settings.dirty = False
        _start_frame(context.scene)
        message = (
            f"sim_flocking: {info['count']} boids / {info['backend']} / "
            f"{info['ms']:.0f} ms"
        )
        message += _viewport_note(status, fixed)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class FLOCK_OT_reset(bpy.types.Operator):
    """按当前种子重新撒一遍（不重建求解器）"""

    bl_idname = "sim_flocking.reset"
    bl_label = "重新撒点"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.sim_flocking
        if sim.STATE.get("solver") is None:
            return bpy.ops.sim_flocking.build()
        if not sim.reset(settings, context.scene):
            self.report({"ERROR"}, "还没有鸟群对象：先点构建")
            return {"CANCELLED"}
        _start_frame(context.scene)
        self.report({"INFO"}, "已重新撒点")
        return {"FINISHED"}


class FLOCK_OT_step(bpy.types.Operator):
    """前进一帧"""

    bl_idname = "sim_flocking.step"
    bl_label = "步进一帧"
    bl_options = {"REGISTER"}

    def execute(self, context):
        if sim.STATE.get("solver") is None:
            return bpy.ops.sim_flocking.build()
        scene = context.scene
        scene.frame_set(scene.frame_current + 1)
        return {"FINISHED"}


class FLOCK_OT_setup_scene(bpy.types.Operator):
    """铺项目画布底色、按模拟区域取景、切 Standard 色彩"""

    bl_idname = "sim_flocking.setup_scene"
    bl_label = "场景预设"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.sim_flocking
        if sim.STATE.get("solver") is None:
            self.report({"ERROR"}, "还没有鸟群：先点构建")
            return {"CANCELLED"}

        status, fixed = viewport.ensure_vertex_colour(context)
        scene_mod.setup(context.scene, float(settings.width), float(settings.height))
        message = (
            f"camera framed on {float(settings.width):.0f}x{float(settings.height):.0f} "
            f"area, {scene_mod.RESOLUTION[0]}x{scene_mod.RESOLUTION[1]}, Standard"
        )
        message += _viewport_note(status, fixed)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class FLOCK_OT_fix_viewport(bpy.types.Operator):
    """把 Solid 视图的着色颜色切成「Attribute / 顶点色」"""

    bl_idname = "sim_flocking.fix_viewport"
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


class FLOCK_OT_delete(bpy.types.Operator):
    """删除插件自建的鸟群对象"""

    bl_idname = "sim_flocking.delete"
    bl_label = "删除鸟群对象"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        removed = mesh.remove_object()
        sim.STATE.update(
            solver=None,
            signature=None,
            frame=0,
            capacity=0,
            backend="",
            note="",
            count=0,
            neighbours=0.0,
            speed=0.0,
            ms=0.0,
            error="",
        )
        if not removed:
            self.report({"WARNING"}, "没有找到插件自建的鸟群对象")
            return {"CANCELLED"}
        context.scene.sim_flocking.dirty = True
        return {"FINISHED"}


# --- 撒鸟（模态） -----------------------------------------------------------


def _plane_hit(settings, region, rv3d, mouse_x: int, mouse_y: int):
    """鼠标射线与 Z = 0 平面的交点；不在视口 / 不在模拟区域里就返回 ``None``。"""
    if region is None or rv3d is None:
        return None
    if not (
        region.x <= mouse_x <= region.x + region.width
        and region.y <= mouse_y <= region.y + region.height
    ):
        return None

    coord = (mouse_x - region.x, mouse_y - region.y)
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
    if abs(direction.z) < 1e-6:
        # 视线与平面平行（正好从侧面看）：没有交点
        return None

    distance = -origin.z / direction.z
    if distance <= 0.0:
        return None  # 平面在相机背后

    point = origin + direction * distance
    if (
        abs(point.x) > float(settings.width) * 0.5
        or abs(point.y) > float(settings.height) * 0.5
    ):
        return None
    return (float(point.x), float(point.y))


class FLOCK_OT_paint(bpy.types.Operator):
    """在 3D 视口里按住左键朝模拟平面撒鸟（ESC / 右键退出）"""

    bl_idname = "sim_flocking.paint"
    bl_label = "撒鸟"
    bl_options = {"REGISTER", "UNDO"}

    #: 两次落笔的最小间隔（秒）：鼠标事件比物理帧密得多，不节流会一秒撒出几百只
    _last_spawn = 0.0

    def _spawn(self, context, settings, hit) -> None:
        sim.spawn(
            settings,
            hit,
            radius=float(settings.brush_radius),
            number=int(settings.brush_count),
        )

    def modal(self, context, event):
        settings = getattr(context.scene, "sim_flocking", None)
        if settings is None or context.area is None:
            return {"CANCELLED"}

        context.area.tag_redraw()

        # ESC / 右键 / 面板里再点一次：退出撒鸟
        if not settings.painting or event.type in {"ESC", "RIGHTMOUSE"}:
            settings.painting = False
            settings.dragging = False
            return {"FINISHED"}

        region = next((r for r in context.area.regions if r.type == "WINDOW"), None)
        rv3d = getattr(context.space_data, "region_3d", None)
        hit = _plane_hit(settings, region, rv3d, event.mouse_x, event.mouse_y)

        if event.type == "LEFTMOUSE":
            if event.value == "PRESS":
                if hit is not None:
                    # 只在模拟区域内拦截点击，区域外照常选择物体
                    settings.dragging = True
                    self._spawn(context, settings, hit)
                    return {"RUNNING_MODAL"}
            elif settings.dragging:
                settings.dragging = False
                return {"RUNNING_MODAL"}
            return {"PASS_THROUGH"}

        if event.type == "MOUSEMOVE" and settings.dragging and hit is not None:
            now = time.time()
            if now - self.__class__._last_spawn > 0.04:
                self.__class__._last_spawn = now
                self._spawn(context, settings, hit)
            return {"RUNNING_MODAL"}

        return {"PASS_THROUGH"}

    def invoke(self, context, event):
        if getattr(context.space_data, "type", None) != "VIEW_3D":
            self.report({"WARNING"}, "请在 3D 视口里使用撒鸟")
            return {"CANCELLED"}

        settings = context.scene.sim_flocking
        if sim.STATE.get("solver") is None:
            # 没有求解器就先构建：没有目标就撒鸟是说不通的
            result = bpy.ops.sim_flocking.build()
            if "FINISHED" not in result:
                return {"CANCELLED"}

        settings.painting = True
        settings.dragging = False
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}
