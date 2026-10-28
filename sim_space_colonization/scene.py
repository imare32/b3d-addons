"""场景装配：世界底色、环绕正交相机、渲染设置（只管「看」，不碰生长）。

枝条长在输入网格上、方向是三维的，所以缺省给一台**斜 3/4 视角的正交相机**：方位角 35°、
仰角 30°，取景尺寸按目标网格的包围盒对角线算。换成透视或任意角度也都成立，只是构图不同。
"""

from __future__ import annotations

import math

import bpy
from mathutils import Vector

from .palette import BACKGROUND, hex_to_linear

__all__ = [
    "CAMERA_NAME",
    "AZIMUTH",
    "ELEVATION",
    "apply_render",
    "apply_world",
    "ensure_camera",
    "setup",
    "span_of",
]

CAMERA_NAME = "Space Colonization Camera"

#: 相机方位角（绕 Z）与仰角。
AZIMUTH = math.radians(35.0)
ELEVATION = math.radians(30.0)

#: 取景留白（相对目标网格包围盒对角线）。
_MARGIN = 1.45

#: 场景预设的分辨率。
RESOLUTION = (1600, 1000)


def apply_world(scene) -> None:
    """世界背景铺项目色板的画布底色（``BACKGROUND``），转线性后写进节点。"""
    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("Space Colonization World")
        scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    colour = hex_to_linear(BACKGROUND)
    if background is not None:
        background.inputs[0].default_value = (*colour, 1.0)
    world.color = colour


def span_of(unit: float) -> float:
    """正交相机的取景尺寸：包围盒对角线 × 留白。"""
    return float(unit) * _MARGIN


def _view_direction() -> Vector:
    """从目标指向相机的单位向量。"""
    return Vector(
        (
            math.cos(ELEVATION) * math.sin(AZIMUTH),
            -math.cos(ELEVATION) * math.cos(AZIMUTH),
            math.sin(ELEVATION),
        )
    )


def ensure_camera(scene, center, span: float, force: bool = False):
    """没有相机就按取景尺寸建一台环绕正交相机；``force=True`` 时重新取景。"""
    camera = scene.camera
    if camera is not None and camera.type == "CAMERA" and not force:
        return camera

    if camera is None or camera.type != "CAMERA":
        data = bpy.data.cameras.new(CAMERA_NAME)
        camera = bpy.data.objects.new(CAMERA_NAME, data)
        # 与结果对象一致：加进当前激活的 collection
        (bpy.context.collection or scene.collection).objects.link(camera)
        scene.camera = camera

    direction = _view_direction()
    distance = float(span) * 3.0
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = float(span)
    camera.data.clip_start = 0.01
    camera.data.clip_end = distance * 4.0
    camera.location = Vector(center) + direction * distance
    camera.rotation_euler = direction.to_track_quat("Z", "Y").to_euler()
    return camera


def apply_render(scene) -> None:
    """渲染设置：Standard 色彩（色板所见即所得）+ 场景预设分辨率。"""
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.render.film_transparent = False
    scene.render.resolution_x = RESOLUTION[0]
    scene.render.resolution_y = RESOLUTION[1]
    scene.render.image_settings.file_format = "PNG"


def setup(scene, center, unit: float) -> None:
    """场景预设：世界底色 + 环绕正交相机 + 渲染设置。"""
    span = span_of(unit)
    apply_world(scene)
    ensure_camera(scene, center, span, force=True)
    apply_render(scene)
