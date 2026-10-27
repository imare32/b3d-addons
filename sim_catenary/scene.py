"""场景装配：世界底色、正交相机、渲染设置（只管"看"，不碰链条本身）。

链条铺在 **XZ 平面**（y = 0），所以缺省给一台从 -Y 方向朝 +Y 看的正交相机 —— 这个
角度最能看清"偶环正圆 / 奇环边对边"的相扣。因为遮挡走 3D 深度（不依赖视角），
换成透视或斜视角也都成立，只是构图不同。
"""

from __future__ import annotations

import math

import bpy

from .palette import BACKGROUND, hex_to_linear

__all__ = [
    "CAMERA_NAME",
    "apply_render",
    "apply_world",
    "ensure_camera",
    "frame_box",
    "setup",
]

CAMERA_NAME = "Sim Catenary Camera"

#: 取景留白（相对链条包围盒）。
_MARGIN = 1.12


def apply_world(scene) -> None:
    """世界背景铺项目色板的画布底色（``BACKGROUND``），转线性后写进节点。"""
    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("Sim Catenary World")
        scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    colour = hex_to_linear(BACKGROUND)
    if background is not None:
        background.inputs[0].default_value = (*colour, 1.0)
    world.color = colour


def frame_box(x_end: float, z_end: float) -> tuple[tuple[float, float], float]:
    """链条的取景范围：返回 ``((中心 x, 中心 z), 边长)``。"""
    span = max(2.0 * float(x_end), float(z_end)) * _MARGIN
    return (0.0, float(z_end) * 0.5), span


def ensure_camera(scene, x_end: float, z_end: float, force: bool = False):
    """没有相机就按链条尺寸建一台正交相机；``force=True`` 时重新取景。"""
    camera = scene.camera
    if camera is not None and camera.type == "CAMERA" and not force:
        return camera

    if camera is None or camera.type != "CAMERA":
        data = bpy.data.cameras.new(CAMERA_NAME)
        camera = bpy.data.objects.new(CAMERA_NAME, data)
        scene.collection.objects.link(camera)
        scene.camera = camera

    centre, span = frame_box(x_end, z_end)
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = span
    # 链条在 z=0 上下的 XZ 平面里：+Y 看过去，屏幕 x = 世界 x、屏幕 y = 世界 z。
    camera.location = (centre[0], -span * 2.0, centre[1])
    camera.rotation_euler = (math.pi * 0.5, 0.0, 0.0)
    return camera


def apply_render(scene) -> None:
    """渲染设置：Standard 色彩（色板所见即所得）+ GP 抗锯齿。"""
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.render.film_transparent = False
    scene.render.resolution_x = 1600
    scene.render.resolution_y = 1000
    scene.render.image_settings.file_format = "PNG"
    gp_settings = getattr(scene, "grease_pencil_settings", None)
    if gp_settings is not None and hasattr(gp_settings, "aa_samples"):
        gp_settings.aa_samples = 16


def setup(scene, x_end: float, z_end: float) -> None:
    """场景预设：世界底色 + 正交相机 + 渲染设置。"""
    apply_world(scene)
    ensure_camera(scene, x_end, z_end, force=True)
    apply_render(scene)
