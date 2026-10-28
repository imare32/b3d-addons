"""场景装配：世界底色、正交相机、渲染设置（只管"看"，不碰场本身）。

高度场铺在 XY 平面（z 是浓度高度），所以缺省给一台**斜俯视的正交相机**：视线与地面
夹 32°（相机绕 X 轴 58°），既能看清图案的平面形状，又能看出 ``z = v·z_scale`` 的浮雕。
换成透视或正俯视也都成立，只是构图不同。
"""

from __future__ import annotations

import math

import bpy

from .palette import BACKGROUND, hex_to_linear

__all__ = [
    "CAMERA_NAME",
    "ELEVATION",
    "TILT",
    "apply_render",
    "apply_world",
    "ensure_camera",
    "setup",
    "span_of",
]

CAMERA_NAME = "Reaction Diffusion Camera"

#: 相机绕 X 轴的旋转角；视线与地面的夹角是 ``90° − TILT`` = 32°。
TILT = math.radians(58.0)
ELEVATION = math.pi * 0.5 - TILT

#: 取景留白（相对场域）。
_MARGIN = 1.12

#: 场景预设的分辨率（也参与取景的宽高比换算）。
RESOLUTION = (1600, 1000)


def apply_world(scene) -> None:
    """世界背景铺项目色板的画布底色（``BACKGROUND``），转线性后写进节点。"""
    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("Reaction Diffusion World")
        scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    colour = hex_to_linear(BACKGROUND)
    if background is not None:
        background.inputs[0].default_value = (*colour, 1.0)
    world.color = colour


def span_of(nx: int, ny: int, dx: float, dy: float) -> float:
    """正交相机的取景尺寸：xy 平面尺寸 + 斜视的纵向压缩 + 留白。"""
    width = float(nx) * float(dx)
    height = float(ny) * float(dy)
    aspect = RESOLUTION[0] / RESOLUTION[1]
    # y 方向被斜视压缩成 height·sin(俯角)，换算到相机纵向再乘宽高比
    return max(width, height * math.sin(ELEVATION) * aspect) * _MARGIN


def ensure_camera(scene, span: float, target_z: float = 0.0, force: bool = False):
    """没有相机就按取景尺寸建一台斜俯视正交相机；``force=True`` 时重新取景。"""
    camera = scene.camera
    if camera is not None and camera.type == "CAMERA" and not force:
        return camera

    if camera is None or camera.type != "CAMERA":
        data = bpy.data.cameras.new(CAMERA_NAME)
        camera = bpy.data.objects.new(CAMERA_NAME, data)
        # 与高度场对象一致：加进当前激活的 collection
        (bpy.context.collection or scene.collection).objects.link(camera)
        scene.camera = camera

    distance = float(span) * 2.0
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = float(span)
    camera.data.clip_start = 0.01
    camera.data.clip_end = distance * 4.0
    camera.rotation_euler = (TILT, 0.0, 0.0)
    camera.location = (
        0.0,
        -math.sin(TILT) * distance,
        float(target_z) + math.cos(TILT) * distance,
    )
    return camera


def apply_render(scene) -> None:
    """渲染设置：Standard 色彩（色板所见即所得）+ 场景预设分辨率。"""
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.render.film_transparent = False
    scene.render.resolution_x = RESOLUTION[0]
    scene.render.resolution_y = RESOLUTION[1]
    scene.render.image_settings.file_format = "PNG"


def setup(scene, span: float, target_z: float = 0.0) -> None:
    """场景预设：世界底色 + 正交相机 + 渲染设置。"""
    apply_world(scene)
    ensure_camera(scene, span, target_z, force=True)
    apply_render(scene)
