"""算子：生成 / 删除 / 从对象回读参数 / 场景预设。

生成的流程是「算法层算点 → ``gp`` 写笔划与属性 → ``tube`` 挂扫管修改器」，这一段收在
:func:`gen_torus_knot.builder.rebuild` 里（自动更新也走它），算子只做编排与参数校验；
能失败的地方（p、q 为 0，分段太小）都收成 ``report``，不把 traceback 甩给用户。
"""

from __future__ import annotations

import math
import time

import bpy

from . import builder, gp, viewport
from .palette import BACKGROUND, hex_to_linear
from .props import read_design, suspend_auto_update

__all__ = [
    "GENTK_OT_delete",
    "GENTK_OT_generate",
    "GENTK_OT_setup_scene",
    "GENTK_OT_sync_from_object",
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
    """世界背景铺项目色板的画布底色（``BACKGROUND``），转线性后写进节点。"""
    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("Gen Torus Knot World")
        scene.world = world

    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    colour = hex_to_linear(BACKGROUND)
    if background is not None:
        background.inputs[0].default_value = (*colour, 1.0)
        background.inputs[1].default_value = 0.6
    # 视口 Solid 模式的"世界底色"是另一份
    world.color = colour


def _bounding_sphere(obj):
    """对象的世界包围盒：``(中心, 半径, 八角点)``；拿不到包围盒时返回 ``None``。"""
    if obj is None or not len(obj.bound_box):
        return None
    from mathutils import Vector

    corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    centre = sum(corners, Vector()) / len(corners)
    radius = max((corner - centre).length for corner in corners)
    if radius < 1e-6:
        return None
    return centre, radius, corners


def _frame_distance(corners, centre, direction, fov, margin=1.06):
    """相机退到多远刚好把包围盒装进画面（逐角点算，不用包围球）。

    结是立体的、绕来绕去，包围球取景会在四周空一大圈；这里让每个角点的横向余量都必须
    落在它自己那个深度对应的半视场里，结就填得满又不切边。
    """
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


def _key_light(scene, centre, radius):
    """起一盏（没有的话）三点式的简化主光：球面上 3/4 方位的太阳光。"""
    light = bpy.data.objects.get("Torus Knot Key")
    if light is None:
        data = bpy.data.lights.new("Torus Knot Key", "SUN")
        light = bpy.data.objects.new("Torus Knot Key", data)
        # 加进当前激活的 collection（不是场景根），与生成对象一致
        (bpy.context.collection or scene.collection).objects.link(light)

    from mathutils import Vector

    destination = Vector((0.8, -0.5, 1.0))
    light.data.energy = 3.0
    light.location = centre + destination * (radius * 3.0)
    # 灯照的是 -Z：让 -Z 指向中心（灯在球外，朝里打）
    aim = (centre - light.location).normalized()
    light.rotation_euler = aim.to_track_quat("-Z", "Y").to_euler()


class GENTK_OT_generate(bpy.types.Operator):
    """按面板参数生成环面结（GP 笔划 + POINT 域属性，可选扫成实体管）"""

    bl_idname = "gen_torus_knot.generate"
    bl_label = "生成"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.gen_torus_knot
        settings.error = ""
        started = time.perf_counter()
        # 手动生成与自动更新（``live``）走同一条路：这里算完就不必再让排队的重算跑一遍
        from . import live

        live.cancel()
        # 参数问题（p、q 为 0、分段太小）与数据写入问题都收成同一条报错：出错的要么是
        # 算点、要么是写属性，都给面板一句话，别把 traceback 甩到操作上
        try:
            points, strands, resolution = builder.rebuild(
                settings, collection=context.collection
            )
        except Exception as exc:
            settings.error = str(exc)
            settings.dirty = True
            self.report({"ERROR"}, f"torus knot failed: {exc}")
            return {"CANCELLED"}

        settings.last_points = points
        settings.last_strands = strands
        settings.last_resolution = resolution
        settings.last_ms = (time.perf_counter() - started) * 1000.0

        state, _count = viewport.ensure_vertex_colour(context)
        note = VIEWPORT_NOTES.get(state, "")
        settings.last_note = note
        settings.dirty = False

        self.report(
            {"INFO"},
            f"gen_torus_knot: {points} points, {strands} strands, "
            f"resolution {resolution}, {settings.last_ms:.0f} ms"
            f"{' | tube on' if settings.tube else ''}",
        )
        return {"FINISHED"}


class GENTK_OT_delete(bpy.types.Operator):
    """删除生成的对象"""

    bl_idname = "gen_torus_knot.delete"
    bl_label = "删除结对象"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        # 别让排队中的自动重算在删完之后又跑一遍
        from . import live

        live.cancel()
        if not gp.remove_object():
            self.report({"WARNING"}, "no generated object found")
            return {"CANCELLED"}
        context.scene.gen_torus_knot.dirty = True
        return {"FINISHED"}


class GENTK_OT_sync_from_object(bpy.types.Operator):
    """把生成对象上记录的参数读回面板"""

    bl_idname = "gen_torus_knot.sync_from_object"
    bl_label = "从对象回读参数"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = gp.find_object()
        if obj is None:
            self.report({"WARNING"}, "no generated object found")
            return {"CANCELLED"}

        settings = context.scene.gen_torus_knot
        # 回读的值就是这一根生成时用的值，几何与之相符：回读期间挂起自动更新，
        # 免得刚读完又重算一遍
        with suspend_auto_update():
            found = read_design(obj, settings)
        if not found:
            self.report({"WARNING"}, "object has no stored generation parameters")
            return {"CANCELLED"}

        # 上面 setattr 触发的 update 回调会把 dirty 标起来，这里按实情落回 False
        settings.dirty = False
        self.report(
            {"INFO"},
            f"read back {found} parameters: p={settings.p} q={settings.q} "
            f"resolution={settings.resolution} tube={settings.tube}",
        )
        return {"FINISHED"}


class GENTK_OT_setup_scene(bpy.types.Operator):
    """建 3/4 视角相机与主光、铺项目画布底色、切 Standard 色彩"""

    bl_idname = "gen_torus_knot.setup_scene"
    bl_label = "场景预设"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        frame = _bounding_sphere(gp.find_object())
        if frame is None:
            self.report({"WARNING"}, "nothing to frame; generate a knot first")
            return {"CANCELLED"}
        centre, radius, corners = frame

        camera = scene.camera
        if camera is None or camera.type != "CAMERA":
            data = bpy.data.cameras.new("Gen Torus Knot Camera")
            camera = bpy.data.objects.new("Gen Torus Knot Camera", data)
            (context.collection or scene.collection).objects.link(camera)
            scene.camera = camera

        from mathutils import Vector

        # 结是立体的（z 由高度与副半径决定），所以给的是 3/4 视角的透视相机
        camera.data.type = "PERSP"
        camera.data.angle = math.radians(40.0)
        direction = Vector((1.0, -1.0, 0.55)).normalized()
        distance = _frame_distance(corners, centre, direction, camera.data.angle)
        camera.location = centre + direction * distance
        camera.rotation_euler = direction.to_track_quat("Z", "Y").to_euler()

        _key_light(scene, centre, radius)

        scene.render.resolution_x = 2048
        scene.render.resolution_y = 2048
        scene.render.film_transparent = False
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"

        _paint_canvas(scene)
        viewport.ensure_vertex_colour(context)

        self.report(
            {"INFO"},
            f"camera 3/4 view, distance {distance:.2f}, 2048x2048, "
            f"Standard view transform, background {BACKGROUND}",
        )
        return {"FINISHED"}