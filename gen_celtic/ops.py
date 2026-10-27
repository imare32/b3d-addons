"""算子：编结 / 删除 / 从对象回读参数 / 场景预设。

编结的流程全在 :mod:`weave` 里（实时处理器走同一条路），算子只做编排与参数校验；
能失败的地方都收成 ``report``，不把 traceback 甩给用户。
"""

from __future__ import annotations

import math
import time

import bpy

from . import tube, weave
from .palette import BACKGROUND, hex_to_linear
from .props import DESIGN_KEYS

__all__ = [
    "DISPLAY_KEY",
    "GENC_OT_delete",
    "GENC_OT_generate",
    "GENC_OT_setup_scene",
    "GENC_OT_sync_from_object",
    "GENC_OT_toggle_source_wire",
]

#: 切到线框前，源网格原来的「显示为」记在这儿（切回来照原样还原，不靠猜）。
DISPLAY_KEY = "gen_celtic_display_type"


def _paint_canvas(scene) -> None:
    """世界背景铺项目色板的画布底色（``BACKGROUND``），转线性后写进节点。"""
    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("Gen Celtic World")
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

    包围球取景对扁平的结会在四周空一大圈；这里让每个角点的横向余量都必须落在
    它自己那个深度对应的半视场里，结就填得满又不切边。
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
    light = bpy.data.objects.get("Celtic Key")
    if light is None:
        data = bpy.data.lights.new("Celtic Key", "SUN")
        light = bpy.data.objects.new("Celtic Key", data)
        scene.collection.objects.link(light)

    from mathutils import Vector

    destination = Vector((0.8, -0.5, 1.0))
    light.data.energy = 3.0
    light.location = centre + destination * (radius * 3.0)
    # 灯照的是 -Z：让 -Z 指向中心（灯在球外，朝里打）
    aim = (centre - light.location).normalized()
    light.rotation_euler = aim.to_track_quat("-Z", "Y").to_euler()


class GENC_OT_generate(bpy.types.Operator):
    """按面板参数把源网格编成凯尔特结（曲线对象 + 绳管修改器）"""

    bl_idname = "gen_celtic.generate"
    bl_label = "编结"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.gen_celtic
        settings.error = ""
        started = time.perf_counter()
        try:
            strands, _rebuilt = weave.apply(
                settings,
                collection=context.collection,
                force=True,
            )
        except Exception as exc:
            settings.error = str(exc)
            settings.dirty = True
            self.report({"ERROR"}, f"celtic knot failed: {exc}")
            return {"CANCELLED"}

        obj = tube.find_object()
        elapsed = (time.perf_counter() - started) * 1000.0
        settings.dirty = False
        message = (
            f"gen_celtic: {strands.stats['points']} points, "
            f"{strands.stats['strands']} strands, "
            f"{strands.stats['broken']} broken strand ends, "
            f"{elapsed:.0f} ms"
        )
        if obj is not None:
            message += f" | {len(obj.data.splines)} splines on {obj.name!r}"
        self.report({"INFO"}, message)
        return {"FINISHED"}


class GENC_OT_delete(bpy.types.Operator):
    """删除编出来的结对象"""

    bl_idname = "gen_celtic.delete"
    bl_label = "删除结对象"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if not tube.remove_object():
            self.report({"WARNING"}, "no generated object found")
            return {"CANCELLED"}
        context.scene.gen_celtic.dirty = True
        return {"FINISHED"}


class GENC_OT_sync_from_object(bpy.types.Operator):
    """把结对象上记录的参数读回面板"""

    bl_idname = "gen_celtic.sync_from_object"
    bl_label = "从对象回读参数"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = tube.find_object()
        if obj is None:
            self.report({"WARNING"}, "no generated object found")
            return {"CANCELLED"}
        if DESIGN_KEYS["source"] not in obj:
            self.report({"WARNING"}, "object has no stored generation parameters")
            return {"CANCELLED"}

        settings = context.scene.gen_celtic
        keys = DESIGN_KEYS
        source = bpy.data.objects.get(str(obj[keys["source"]]))
        if source is not None and source.type == "MESH":
            settings.source = source
        settings.lift = float(obj.get(keys["lift"], settings.lift))

        # 对象上记的半径是世界单位，面板要的是比例：按当时的平均边长还原
        mean = tube.stored_mean(obj)
        radius = float(obj.get(keys["radius"], 0.0))
        if mean > 1e-9 and radius > 0.0:
            settings.radius = radius / mean
        resolution = int(obj.get(keys["resolution"], settings.resolution))
        settings.resolution = max(3, min(32, resolution))
        spline_resolution = int(
            obj.get(keys["spline_resolution"], settings.spline_resolution)
        )
        settings.spline_resolution = max(1, min(32, spline_resolution))
        settings.dirty = False
        self.report(
            {"INFO"},
            f"read back lift={settings.lift:.2f} radius={settings.radius:.2f} "
            f"resolution={settings.resolution} "
            f"splines={settings.spline_resolution}",
        )
        return {"FINISHED"}


class GENC_OT_toggle_source_wire(bpy.types.Operator):
    """源网格在线框与实体之间切换（线框能看穿源网格，方便核对结怎么绕着它走）"""

    bl_idname = "gen_celtic.toggle_source_wire"
    bl_label = "源网格线框显示"
    bl_options = {"REGISTER", "UNDO"}

    #: ``Object.display_type`` 的合法值；还原时拿到别的值就退回实体。
    _DISPLAY_TYPES = {"BOUNDS", "SOLID", "TEXTURED", "WIRE"}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "gen_celtic", None)
        return settings is not None and settings.source is not None

    def execute(self, context):
        source = context.scene.gen_celtic.source
        if source.display_type == "WIRE":
            previous = str(source.get(DISPLAY_KEY, "TEXTURED"))
            source.display_type = previous if previous in self._DISPLAY_TYPES else "TEXTURED"
            if DISPLAY_KEY in source:
                del source[DISPLAY_KEY]
            self.report({"INFO"}, f"{source.name}: {source.display_type.lower()} display")
        else:
            # 记下用户自己的显示方式（BOUNDS / SOLID / TEXTURED），切回来时原样还给他
            source[DISPLAY_KEY] = source.display_type
            source.display_type = "WIRE"
            self.report({"INFO"}, f"{source.name}: wireframe display")
        return {"FINISHED"}


class GENC_OT_setup_scene(bpy.types.Operator):
    """建 3/4 视角相机与主光、铺项目画布底色、切 Standard 色彩"""

    bl_idname = "gen_celtic.setup_scene"
    bl_label = "场景预设"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        settings = scene.gen_celtic
        frame = _bounding_sphere(tube.find_object())
        if frame is None:
            frame = _bounding_sphere(settings.source)
        if frame is None:
            self.report({"WARNING"}, "nothing to frame; generate a knot first")
            return {"CANCELLED"}
        centre, radius, corners = frame

        camera = scene.camera
        if camera is None or camera.type != "CAMERA":
            data = bpy.data.cameras.new("Gen Celtic Camera")
            camera = bpy.data.objects.new("Gen Celtic Camera", data)
            context.collection.objects.link(camera)
            scene.camera = camera

        from mathutils import Vector

        # 结是立体的，所以给的是 3/4 视角的透视相机（不是 zellij 那种正视正交）
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

        self.report(
            {"INFO"},
            f"camera 3/4 view, distance {distance:.2f}, 2048x2048, "
            f"Standard view transform, background {BACKGROUND}",
        )
        return {"FINISHED"}