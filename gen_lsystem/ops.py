"""算子：生成 / 删除 / 从对象回读参数 / 场景预设 / 规则增删。

生成的流程是「算法层算路径 → ``gp`` 写笔划与属性 → 记参数与包围盒」，这一段收在
:func:`gen_lsystem.builder.rebuild` 里（自动更新也走它），算子只做编排与参数校验；
能失败的地方（规则写错、参数不是数字、展开爆掉）都收成 ``report`` + 面板提示，
不把 traceback 甩给用户。
"""

from __future__ import annotations

import math
import time

import bpy

from . import builder, gp, presets, viewport
from .palette import SILK, hex_to_linear
from .props import read_design, suspend_auto_update

__all__ = [
    "GENLS_OT_apply_preset",
    "GENLS_OT_delete",
    "GENLS_OT_generate",
    "GENLS_OT_rule_add",
    "GENLS_OT_rule_remove",
    "GENLS_OT_setup_scene",
    "GENLS_OT_sync_from_object",
    "VIEWPORT_NOTES",
]

#: 视口着色检查的结果 → 面板提示（``viewport`` 的返回值）。
VIEWPORT_NOTES = {
    viewport.FIXED: "视口已切到顶点色（Solid）",
    viewport.ALREADY: "视口本来就在顶点色（Solid）",
    viewport.OTHER: "视口着色是你自己选的，没动（Solid 下想看得见请选 Attribute）",
    viewport.NOT_SOLID: "视口不是 Solid 模式，没动着色设置",
    viewport.NO_VIEW: "没有 3D 视图（后台运行），没动着色设置",
}


def _paint_canvas(scene) -> None:
    """世界背景铺项目色板的**绢白**（``SILK``），转线性后写进节点。

    线稿插件用比 ``BACKGROUND`` 更亮的一档：墨线 / 朱砂细线在绢白上反差最大，
    而这里不出填色块，不怕"绢白压不出边界"（见 ``palette.py`` 的 ``PAPER`` 注释）。
    背景强度给满 1.0 —— 渲染出来的底色就是色板上的那个十六进制值。
    """
    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("Gen L-System World")
        scene.world = world

    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    colour = hex_to_linear(SILK)
    if background is not None:
        background.inputs[0].default_value = (*colour, 1.0)
        background.inputs[1].default_value = 1.0
    # 视口 Solid 模式的"世界底色"是另一份
    world.color = colour


def _sorted_bounds(obj):
    """生成物包围盒 → ``(中心, 半径, 八角点, 最小角, 最大角)``；拿不到就返回 ``None``。"""
    found = gp.read_bounds(obj)
    if found is None:
        return None
    low, high = found
    from mathutils import Vector

    centre = (Vector(low) + Vector(high)) * 0.5
    corners = []
    for index in range(8):
        corner = []
        for axis in range(3):
            half = (high[axis] - low[axis]) * 0.5
            corner.append(centre[axis] + (half if index & (1 << axis) else -half))
        corners.append(Vector(corner))
    radius = max((corner - centre).length for corner in corners)
    if radius < 1e-6:
        return None
    return centre, radius, corners, low, high


def _frame_distance(corners, centre, direction, fov, margin=1.06):
    """相机退到多远刚好把八角点装进画面（逐角点算，不用包围球）。"""
    from mathutils import Vector

    forward = -direction
    right = forward.cross(Vector((0.0, 0.0, 1.0)))
    if right.length < 1e-6:  # 正上正下看时的兜底
        right = Vector((1.0, 0.0, 0.0))
    right.normalize()
    up = right.cross(forward).normalized()
    tangent = math.tan(fov * 0.5)

    distance = 0.0
    for corner in corners:
        offset = corner - centre
        reach = max(abs(offset.dot(right)), abs(offset.dot(up))) * margin
        distance = max(distance, reach / tangent - offset.dot(forward))
    return distance


def _camera(context):
    """拿场景相机；没有就建一个（进当前激活的 collection）。"""
    scene = context.scene
    camera = scene.camera
    if camera is not None and camera.type == "CAMERA":
        return camera
    data = bpy.data.cameras.new("Gen L-System Camera")
    camera = bpy.data.objects.new("Gen L-System Camera", data)
    (context.collection or scene.collection).objects.link(camera)
    scene.camera = camera
    return camera


def _frame_2d(camera, centre, low, high) -> float:
    """俯视正交：图案躺平在 XY 平面，按包围框取景（2D 图案没有透视意义）。"""
    from mathutils import Vector

    camera.data.type = "ORTHO"
    span_x = max(high[0] - low[0], 1e-3)
    span_y = max(high[1] - low[1], 1e-3)
    camera.data.ortho_scale = max(span_x, span_y) * 1.15
    camera.location = centre + Vector((0.0, 0.0, max(span_x, span_y, 1.0) * 2.0))
    camera.rotation_euler = (0.0, 0.0, 0.0)  # 相机默认朝 -Z，俯视正好
    return float(camera.data.ortho_scale)


class GENLS_OT_generate(bpy.types.Operator):
    """按面板参数生成 L-system（每条路径一笔 GP 笔划 + 逐点半径）"""

    bl_idname = "gen_lsystem.generate"
    bl_label = "生成"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.gen_lsystem
        settings.error = ""
        started = time.perf_counter()
        # 手动生成与自动更新（``live``）走同一条路：这里算完就不必再让排队的重算跑一遍
        from . import live

        live.cancel()
        # 规则问题（括号不配对 / 参数不是数字）与数据写入问题都收成同一条报错
        try:
            skeleton = builder.rebuild(settings, collection=context.collection)
        except Exception as exc:
            settings.error = str(exc)
            settings.dirty = True
            self.report({"ERROR"}, f"lsystem failed: {exc}")
            return {"CANCELLED"}

        settings.last_symbols = skeleton.symbols
        settings.last_paths = skeleton.path_count
        settings.last_points = skeleton.point_count
        settings.last_depth = skeleton.depth
        settings.last_ms = (time.perf_counter() - started) * 1000.0

        state, _count = viewport.ensure_vertex_colour(context)
        settings.last_note = VIEWPORT_NOTES.get(state, "")
        if skeleton.ignored:
            extra = "忽略的符号：" + " ".join(skeleton.ignored)
            settings.last_note = f"{settings.last_note}｜{extra}" if settings.last_note else extra
        settings.dirty = False

        self.report(
            {"INFO"},
            f"gen_lsystem: {skeleton.path_count} strokes, {skeleton.point_count} points, "
            f"{skeleton.symbols} modules, {settings.last_ms:.0f} ms",
        )
        return {"FINISHED"}


class GENLS_OT_delete(bpy.types.Operator):
    """删除生成的对象"""

    bl_idname = "gen_lsystem.delete"
    bl_label = "删除对象"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        # 别让排队中的自动重算在删完之后又跑一遍
        from . import live

        live.cancel()
        if not gp.remove_object():
            self.report({"WARNING"}, "no generated object found")
            return {"CANCELLED"}
        context.scene.gen_lsystem.dirty = True
        return {"FINISHED"}


class GENLS_OT_sync_from_object(bpy.types.Operator):
    """把生成对象上记录的参数读回面板"""

    bl_idname = "gen_lsystem.sync_from_object"
    bl_label = "从对象回读参数"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = gp.find_object()
        if obj is None:
            self.report({"WARNING"}, "no generated object found")
            return {"CANCELLED"}

        settings = context.scene.gen_lsystem
        # 回读的值就是这一张图生成时用的值，几何与之相符：回读期间挂起自动更新
        with suspend_auto_update():
            found = read_design(obj, settings)
        if not found:
            self.report({"WARNING"}, "object has no stored generation parameters")
            return {"CANCELLED"}

        settings.dirty = False
        self.report(
            {"INFO"},
            f"read back {found} parameters: {len(settings.rules)} rules, "
            f"iterations={settings.iterations}, preset={settings.preset}",
        )
        return {"FINISHED"}


class GENLS_OT_setup_scene(bpy.types.Operator):
    """建相机、铺项目画布底色、切 Standard 色彩（2D 俯视正交 / 3D 3/4 透视）"""

    bl_idname = "gen_lsystem.setup_scene"
    bl_label = "场景预设"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        settings = scene.gen_lsystem
        found = _sorted_bounds(gp.find_object())
        if found is None:
            self.report({"WARNING"}, "nothing to frame; generate a system first")
            return {"CANCELLED"}
        centre, _radius, corners, low, high = found

        camera = _camera(context)
        if settings.dimension == "2D":
            distance = _frame_2d(camera, centre, low, high)
            note = f"ortho top view, scale {distance:.2f}"
        else:
            from mathutils import Vector

            camera.data.type = "PERSP"
            camera.data.angle = math.radians(40.0)
            direction = Vector((1.0, -1.0, 0.55)).normalized()
            distance = _frame_distance(corners, centre, direction, camera.data.angle)
            camera.location = centre + direction * distance
            camera.rotation_euler = direction.to_track_quat("Z", "Y").to_euler()
            note = f"3/4 perspective, distance {distance:.2f}"

        scene.render.resolution_x = 2048
        scene.render.resolution_y = 2048
        scene.render.film_transparent = False
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"

        _paint_canvas(scene)
        viewport.ensure_vertex_colour(context)
        viewport.ensure_world_background(context)

        self.report(
            {"INFO"},
            f"{note}, 2048x2048, Standard view transform, background {SILK} (silk white)",
        )
        return {"FINISHED"}


class GENLS_OT_apply_preset(bpy.types.Operator):
    """把当前预设整组写进面板（公理 / 规则 / 解释参数 / 维度）并重算"""

    bl_idname = "gen_lsystem.apply_preset"
    bl_label = "应用预设"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.gen_lsystem
        preset = presets.by_key(settings.preset)
        if preset is None:
            self.report({"WARNING"}, "unknown preset")
            return {"CANCELLED"}
        from .props import apply_preset

        count = apply_preset(settings, preset)
        self.report({"INFO"}, f"{preset.name}：写入 {count} 项（{preset.source}）")
        return {"FINISHED"}


class GENLS_OT_rule_add(bpy.types.Operator):
    """加一条产生式"""

    bl_idname = "gen_lsystem.rule_add"
    bl_label = "加规则"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.gen_lsystem
        item = settings.rules.add()
        # 新规则一开始是空的（左侧 / 右侧为空的行在生成时被跳过），光标落上去直接填
        item.pattern = ""
        item.condition = ""
        item.result = ""
        settings.active_rule = len(settings.rules) - 1
        settings.dirty = True
        return {"FINISHED"}


class GENLS_OT_rule_remove(bpy.types.Operator):
    """删掉选中的那条产生式"""

    bl_idname = "gen_lsystem.rule_remove"
    bl_label = "删规则"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.gen_lsystem
        index = int(settings.active_rule)
        if not (0 <= index < len(settings.rules)):
            self.report({"WARNING"}, "no rule selected")
            return {"CANCELLED"}
        settings.rules.remove(index)
        settings.active_rule = max(0, min(index, len(settings.rules) - 1))
        settings.dirty = True
        # 删一条规则不触发任何 update 回调：手动排一次重算
        if settings.auto_update:
            from . import live

            live.request(settings)
        return {"FINISHED"}

