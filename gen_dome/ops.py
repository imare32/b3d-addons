"""算子：生成 / 删除 / 从对象回读参数 / 场景预设。

生成流程是「:mod:`dome` 算多边形 → :mod:`mesh` 写对象」，算子只做编排与参数校验；
能失败的地方都收成 ``report``，不把 traceback 甩给用户。
"""

from __future__ import annotations

import math
import time

import bpy

from . import dome, mesh
from .palette import BACKGROUND, hex_to_linear
from .props import DESIGN_KEYS, match_frame_end

__all__ = [
    "GEND_OT_delete",
    "GEND_OT_generate",
    "GEND_OT_setup_scene",
    "GEND_OT_sync_from_object",
]


def _paint_canvas(scene) -> None:
    """世界背景铺项目色板的画布底色（``BACKGROUND``），转线性后写进节点。"""
    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("Gen Dome World")
        scene.world = world

    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    colour = hex_to_linear(BACKGROUND)
    if background is not None:
        background.inputs[0].default_value = (*colour, 1.0)
        # 世界光同时当环境光：给太亮白色面板会直接过曝，0.35 刚好把暗部提起来
        background.inputs[1].default_value = 0.35
    # 视口 Solid 模式的"世界底色"是另一份
    world.color = colour


def _bounding_sphere(obj):
    """对象的世界包围盒：``(中心, 半径, 八角点)``；拿不到包围盒时返回 ``None``。

    算的是**网格数据**的包围盒，不是 ``obj.bound_box`` —— 后者跟着当前帧求值后的几何走，
    生长到第 1 代时只有顶上一顶小帽子，取景会缩到帽子那么大。烘进去的网格本身就横跨所有代，
    用它取景，整段动画的取景才稳。
    """
    if obj is None or obj.type != "MESH" or not len(obj.data.vertices):
        return None
    import numpy as np
    from mathutils import Vector

    co = np.zeros(len(obj.data.vertices) * 3)
    obj.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    low, high = co.min(axis=0), co.max(axis=0)
    corners = [
        obj.matrix_world @ Vector((x, y, z))
        for x in (low[0], high[0])
        for y in (low[1], high[1])
        for z in (low[2], high[2])
    ]
    centre = sum(corners, Vector()) / len(corners)
    radius = max((corner - centre).length for corner in corners)
    if radius < 1e-6:
        return None
    return centre, radius, corners


def _frame_distance(corners, centre, direction, fov, margin=1.06):
    """相机退到多远刚好把包围盒装进画面（逐角点算，不用包围球）。

    包围球取景对扁平的穹顶会在四周空一大圈；这里让每个角点的横向余量都必须落在
    它自己那个深度对应的半视场里，穹顶就填得满又不切边。
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
    light = bpy.data.objects.get("Dome Key")
    if light is None:
        data = bpy.data.lights.new("Dome Key", "SUN")
        light = bpy.data.objects.new("Dome Key", data)
        # 加进当前激活的 collection（不是场景根），与生成对象一致
        (bpy.context.collection or scene.collection).objects.link(light)

    from mathutils import Vector

    destination = Vector((0.8, -0.5, 1.0))
    # 面板默认是绢白：太阳给 2.0 就够亮，3.0 以上白色会直接顶到 1.0（Standard 色彩下过曝）
    light.data.energy = 2.0
    light.location = centre + destination * (radius * 3.0)
    # 灯照的是 -Z：让 -Z 指向中心（灯在球外，朝里打）
    aim = (centre - light.location).normalized()
    light.rotation_euler = aim.to_track_quat("-Z", "Y").to_euler()


def _store(settings, obj) -> None:
    """把输入参数写进对象：换机器、换文件后还能取回这次生成的依据。"""
    obj[DESIGN_KEYS["count"]] = int(settings.count)
    obj[DESIGN_KEYS["radius"]] = float(settings.radius)
    obj[DESIGN_KEYS["height"]] = float(settings.height)
    obj[DESIGN_KEYS["gap"]] = float(settings.gap)
    obj[DESIGN_KEYS["close_bottom"]] = bool(settings.close_bottom)
    obj[DESIGN_KEYS["studs"]] = bool(settings.studs)
    obj[DESIGN_KEYS["stud_radius"]] = float(settings.stud_radius)
    obj[DESIGN_KEYS["animate"]] = bool(settings.animate)
    obj[DESIGN_KEYS["start_frame"]] = int(settings.start_frame)
    obj[DESIGN_KEYS["length"]] = int(settings.length)


class _Progress:
    """烘代时的进度条（画在鼠标旁边；拿不到界面时静默跳过）。

    进度条靠 ``bpy.ops`` 刷，本身有几十毫秒的开销 —— 所以**节流**成最多刷二十来次，
    否则刷进度条比算几何还贵（72 代实测会多花 1 秒以上）。
    """

    def __init__(self, total: int):
        self.total = max(1, int(total))
        self.step = max(1, self.total // 20)
        self.active = False
        try:
            bpy.ops.wm.progress_begin(0, self.total)
            self.active = True
        except Exception:
            # 后台模式 / 没有界面：进度条本来就没有意义，静默退回「不显示」
            self.active = False

    def __call__(self, done, total) -> None:
        if not self.active:
            return
        done = int(done)
        if done % self.step and done < self.total:
            return
        try:
            bpy.ops.wm.progress_update(done)
        except Exception:
            self.active = False

    def close(self) -> None:
        if not self.active:
            return
        try:
            bpy.ops.wm.progress_end()
        except Exception:
            self.active = False
        self.active = False


class GEND_OT_generate(bpy.types.Operator):
    """按面板参数烘一座斐波那契穹顶（每一代都是完整的球面 Voronoi 划分）"""

    bl_idname = "gen_dome.generate"
    bl_label = "生成穹顶"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.gen_dome
        settings.error = ""
        params = settings.geometry_params()
        started = time.perf_counter()
        progress = _Progress(params["generations"])
        try:
            panels = dome.build_generations(**params, progress=progress)
        except Exception as exc:
            settings.error = str(exc)
            settings.dirty = True
            self.report({"ERROR"}, f"dome failed: {exc}")
            return {"CANCELLED"}
        finally:
            progress.close()

        try:
            obj = mesh.build_object(panels, settings, collection=context.collection)
        except Exception as exc:
            settings.error = str(exc)
            self.report({"ERROR"}, f"mesh write failed: {exc}")
            return {"CANCELLED"}

        _store(settings, obj)
        elapsed = (time.perf_counter() - started) * 1000.0
        settings.last_faces = panels.face_count
        settings.last_verts = panels.vert_count
        settings.last_generations = panels.generations
        settings.last_ms = elapsed
        settings.last_note = ""
        if panels.dropped:
            settings.last_note = f"{panels.dropped} 块面板被缝吃掉"
        settings.dirty = False

        message = (
            f"gen_dome: {panels.generations} generations, {panels.face_count} faces, "
            f"{panels.vert_count} verts, {elapsed:.0f} ms"
        )
        if panels.dropped:
            message += f" ({panels.dropped} panels vanished)"
        self.report({"INFO"}, message)
        return {"FINISHED"}


class GEND_OT_delete(bpy.types.Operator):
    """删除生成的穹顶对象"""

    bl_idname = "gen_dome.delete"
    bl_label = "删除穹顶对象"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if not mesh.remove_object():
            self.report({"WARNING"}, "no generated object found")
            return {"CANCELLED"}
        context.scene.gen_dome.dirty = True
        return {"FINISHED"}


class GEND_OT_sync_from_object(bpy.types.Operator):
    """把穹顶对象上记录的参数读回面板"""

    bl_idname = "gen_dome.sync_from_object"
    bl_label = "从对象回读参数"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = mesh.find_object()
        if obj is None:
            self.report({"WARNING"}, "no generated object found")
            return {"CANCELLED"}
        if DESIGN_KEYS["count"] not in obj:
            self.report({"WARNING"}, "object has no stored generation parameters")
            return {"CANCELLED"}

        settings = context.scene.gen_dome
        keys = DESIGN_KEYS
        settings.count = int(obj.get(keys["count"], settings.count))
        settings.radius = float(obj.get(keys["radius"], settings.radius))
        settings.height = float(obj.get(keys["height"], settings.height))
        settings.gap = float(obj.get(keys["gap"], settings.gap))
        settings.close_bottom = bool(
            obj.get(keys["close_bottom"], settings.close_bottom)
        )
        settings.studs = bool(obj.get(keys["studs"], settings.studs))
        settings.stud_radius = float(obj.get(keys["stud_radius"], settings.stud_radius))
        settings.animate = bool(obj.get(keys["animate"], settings.animate))
        settings.start_frame = int(obj.get(keys["start_frame"], settings.start_frame))
        settings.length = int(obj.get(keys["length"], settings.length))
        settings.dirty = False
        self.report(
            {"INFO"},
            f"read back count={settings.count} radius={settings.radius:.3g} "
            f"height={settings.height:.2f} gap={settings.gap:.3g}",
        )
        return {"FINISHED"}


class GEND_OT_setup_scene(bpy.types.Operator):
    """建 3/4 视角相机与主光、铺项目画布底色、切 Standard 色彩"""

    bl_idname = "gen_dome.setup_scene"
    bl_label = "场景预设"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        settings = scene.gen_dome
        frame = _bounding_sphere(mesh.find_object())
        if frame is None:
            self.report({"WARNING"}, "nothing to frame; generate a dome first")
            return {"CANCELLED"}
        centre, radius, corners = frame

        camera = scene.camera
        if camera is None or camera.type != "CAMERA":
            data = bpy.data.cameras.new("Gen Dome Camera")
            camera = bpy.data.objects.new("Gen Dome Camera", data)
            (context.collection or scene.collection).objects.link(camera)
            scene.camera = camera

        from mathutils import Vector

        # 穹顶是立体物：给 3/4 视角的透视相机（不是 zellij 那种正视正交）
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

        # 跟时间轴生长时顺手把播放区间对到生长区间：起始帧只往前扩、结束帧只往后拉，
        # 不缩短用户自己的区间
        if settings.animate:
            scene.frame_start = min(scene.frame_start, int(settings.start_frame))
            match_frame_end(settings, context)

        _paint_canvas(scene)

        self.report(
            {"INFO"},
            f"camera 3/4 view, distance {distance:.2f}, 2048x2048, "
            f"Standard view transform, background {BACKGROUND}",
        )
        return {"FINISHED"}
