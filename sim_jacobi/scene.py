"""场景装配：世界底色、3/4 视角透视相机、渲染设置（只管"看"，不碰场本身）。

环面是立体的，缺省给一台**绕 Z 轴 35°、俯角 28°** 的透视相机（50mm），取景按环面外径
``major_r + minor_r`` 换算 —— 能同时看清环面内侧的凹陷和外侧的格纹。
"""

from __future__ import annotations

import math

import bpy
from mathutils import Vector

from .palette import BACKGROUND, hex_to_linear

__all__ = [
    "AZIMUTH",
    "CAMERA_NAME",
    "ELEVATION",
    "RESOLUTION",
    "apply_render",
    "apply_world",
    "distance_of",
    "ensure_camera",
    "outer_radius",
    "setup",
]

CAMERA_NAME = "Sim Jacobi Camera"

#: 相机仰角（与地平面夹角）与绕 Z 的方位角。
ELEVATION = math.radians(28.0)
AZIMUTH = math.radians(35.0)

#: 场景预设的分辨率（正方形，环面投影接近圆）。
RESOLUTION = (1280, 1280)

#: 焦距 / 传感器宽度，只用来算取景距离。
LENS = 50.0
SENSOR = 36.0

#: 取景留白（相对外径）。
_MARGIN = 1.18


def outer_radius(major_r: float, minor_r: float) -> float:
    """环面外径：相机取景、裁剪面都按它换算。"""
    return float(major_r) + float(minor_r)


def distance_of(radius: float) -> float:
    """把半径 ``radius`` 装进画面所需的相机距离（水平半视角）。"""
    half_fov = math.atan(SENSOR * 0.5 / LENS)
    return max(float(radius), 1e-3) * _MARGIN / math.tan(half_fov)


def apply_world(scene) -> None:
    """世界背景铺项目色板的画布底色（``BACKGROUND``），转线性后写进节点。"""
    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("Sim Jacobi World")
        scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    colour = hex_to_linear(BACKGROUND)
    if background is not None:
        background.inputs[0].default_value = (*colour, 1.0)
    world.color = colour


def ensure_camera(scene, radius: float, force: bool = False):
    """没有相机就按外径建一台 3/4 视角透视相机；``force=True`` 时重新取景。"""
    camera = scene.camera
    if camera is not None and camera.type == "CAMERA" and not force:
        return camera

    if camera is None or camera.type != "CAMERA":
        data = bpy.data.cameras.new(CAMERA_NAME)
        camera = bpy.data.objects.new(CAMERA_NAME, data)
        # 与生成对象一致：加进当前激活的 collection
        (bpy.context.collection or scene.collection).objects.link(camera)
        scene.camera = camera

    distance = distance_of(radius)
    location = Vector(
        (
            distance * math.cos(ELEVATION) * math.sin(AZIMUTH),
            -distance * math.cos(ELEVATION) * math.cos(AZIMUTH),
            distance * math.sin(ELEVATION),
        )
    )

    camera.data.type = "PERSP"
    camera.data.lens = LENS
    camera.data.sensor_width = SENSOR
    camera.data.clip_start = max(distance * 0.01, 0.01)
    camera.data.clip_end = distance * 8.0
    camera.location = location
    # 看向原点：相机 -Z 对准目标，+Y 作为上方向
    camera.rotation_euler = (-location).to_track_quat("-Z", "Y").to_euler()
    return camera


def apply_render(scene) -> None:
    """渲染设置：Standard 色彩（色板所见即所得）+ 场景预设分辨率。"""
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.render.film_transparent = False
    scene.render.resolution_x = RESOLUTION[0]
    scene.render.resolution_y = RESOLUTION[1]
    scene.render.image_settings.file_format = "PNG"


def setup(scene, radius: float) -> None:
    """场景预设：世界底色 + 3/4 视角相机 + 渲染设置。"""
    apply_world(scene)
    ensure_camera(scene, radius, force=True)
    apply_render(scene)
