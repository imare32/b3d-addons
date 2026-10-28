"""场景装配：世界底色、取景相机、渲染设置（只管"看"，不碰模拟）。

细胞跑在**目标网格自己的曲面**上，位置由网格决定，所以相机只做一件事：按目标的
世界空间包围盒摆一台 3/4 视角的透视相机。场景预设（:func:`setup`）会重新取景，
构建（:func:`ensure_camera`）只在场景还没有相机时建一台，不抢用户已经摆好的机位。
"""

from __future__ import annotations

import math

import bpy
from mathutils import Vector

from .palette import BACKGROUND, hex_to_linear

__all__ = [
    "CAMERA_NAME",
    "RESOLUTION",
    "apply_render",
    "apply_world",
    "ensure_camera",
    "setup",
]

CAMERA_NAME = "Sim GOL Camera"

#: 场景预设的分辨率。
RESOLUTION = (1600, 1000)

#: 相机相对目标中心的观察方向（右前上方）。
_DIRECTION = Vector((0.9, -1.15, 0.75)).normalized()

#: 取景留白。
_MARGIN = 1.18


def apply_world(scene) -> None:
    """世界背景铺项目色板的画布底色（``BACKGROUND``），转线性后写进节点。"""
    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("Sim GOL World")
        scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    colour = hex_to_linear(BACKGROUND)
    if background is not None:
        background.inputs[0].default_value = (*colour, 1.0)
    world.color = colour


def apply_render(scene) -> None:
    """渲染设置：Standard 色彩（色板所见即所得）+ 场景预设分辨率。"""
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.render.film_transparent = False
    scene.render.resolution_x = RESOLUTION[0]
    scene.render.resolution_y = RESOLUTION[1]
    scene.render.image_settings.file_format = "PNG"


def _bounds(obj) -> tuple[Vector, float]:
    """目标的世界空间包围盒 → ``(中心, 外接球半径)``。"""
    matrix = obj.matrix_world
    corners = [matrix @ Vector(corner) for corner in obj.bound_box]
    center = sum(corners, Vector()) / len(corners)
    radius = max((corner - center).length for corner in corners)
    return center, max(radius, 1e-3)


def ensure_camera(scene, obj, force: bool = False):
    """按目标的包围盒摆一台 3/4 视角透视相机；``force=True`` 时重新取景。"""
    camera = scene.camera
    if camera is not None and camera.type == "CAMERA" and not force:
        return camera

    if camera is None or camera.type != "CAMERA":
        data = bpy.data.cameras.new(CAMERA_NAME)
        camera = bpy.data.objects.new(CAMERA_NAME, data)
        # 与缺省网格一样，加进当前激活的 collection
        (bpy.context.collection or scene.collection).objects.link(camera)
        scene.camera = camera

    center, radius = _bounds(obj)
    camera.data.type = "PERSP"
    camera.data.lens = 50.0
    camera.data.sensor_fit = "AUTO"

    # 按外接球取景，且用**较小**的那个视场角：画幅横比纵宽时，裁掉内容的是纵向
    # （``angle`` 只给横向，用它算会把上下切掉）。
    half_angle = min(camera.data.angle_x, camera.data.angle_y) * 0.5
    distance = radius / max(math.sin(half_angle), 1e-3) * _MARGIN
    camera.data.clip_start = max(radius * 0.01, 1e-3)
    camera.data.clip_end = distance * 8.0
    camera.location = center + _DIRECTION * distance
    camera.rotation_euler = (
        (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    )
    return camera


def setup(scene, obj) -> None:
    """场景预设：世界底色 + 重新取景的相机 + 渲染设置。"""
    apply_world(scene)
    apply_render(scene)
    ensure_camera(scene, obj, force=True)
