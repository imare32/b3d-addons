"""场景装配：世界底色、正俯视正交相机、渲染设置（只管看，不碰物理）。

差分生长的图案铺在 **XY 平面**（锁 Z 时 z 恒为 0，关掉锁 Z 也只是薄薄一层），所以缺省给
一台**正俯视的正交相机**：视线垂直向下，图案不变形、折痕的疏密一眼看清。要 3D 版本时
自己换透视 / 斜视相机即可 —— 场景预设只负责一次构图。

取景按「边界半径」算：培养皿半径 ``R_b`` 就是这个图案的外接半径。``ortho_scale`` 作用在
**长边**上（1600×1000 的竖向视野只有 ``span / 1.6``），所以横向要留出 ``2·R_b·宽高比``。
"""

from __future__ import annotations

import bpy

from .palette import BACKGROUND, hex_to_linear

__all__ = [
    "CAMERA_NAME",
    "MARGIN",
    "RESOLUTION",
    "apply_render",
    "apply_world",
    "ensure_camera",
    "setup",
    "span_of",
]

CAMERA_NAME = "Sim Differential Camera"

#: 取景留白（相对边界直径）。
MARGIN = 1.12

#: 场景预设的分辨率（也参与取景的宽高比换算）。
RESOLUTION = (1600, 1000)

#: ``bound_radius`` 为 0（不设边界）时的兜底取景半径。
FALLBACK_HALF = 2.0


def span_of(bound_radius: float) -> float:
    """正交相机的取景尺寸：装下半径 ``bound_radius`` 的培养皿 + 留白。"""
    half = float(bound_radius) if float(bound_radius) > 0.0 else FALLBACK_HALF
    aspect = RESOLUTION[0] / RESOLUTION[1]
    return 2.0 * half * aspect * MARGIN


def apply_world(scene) -> None:
    """世界背景铺项目色板的画布底色（``BACKGROUND``），转线性后写进节点。"""
    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("Sim Differential World")
        scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    colour = hex_to_linear(BACKGROUND)
    if background is not None:
        background.inputs[0].default_value = (*colour, 1.0)
    world.color = colour


def ensure_camera(scene, span: float, force: bool = False):
    """没有相机就建一台正俯视正交相机；``force=True`` 时重新取景。

    场景相机**是别人的**时只在 ``force=True``（场景预设）下接管，绝不悄悄挪用户的相机。
    """
    camera = scene.camera
    ours = (
        camera is not None
        and camera.type == "CAMERA"
        and camera.data.name.startswith(CAMERA_NAME)
    )
    if camera is not None and camera.type == "CAMERA" and not force and not ours:
        return camera

    if camera is None or camera.type != "CAMERA":
        data = bpy.data.cameras.new(CAMERA_NAME)
        camera = bpy.data.objects.new(CAMERA_NAME, data)
        # 与曲线对象一致：加进当前激活的 collection
        (bpy.context.collection or scene.collection).objects.link(camera)
        scene.camera = camera

    distance = float(span) * 2.0
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = float(span)
    camera.data.clip_start = 0.01
    camera.data.clip_end = distance * 6.0
    # 俯视：相机默认朝 -Z 看，所以转到 0 就是垂直向下
    camera.rotation_euler = (0.0, 0.0, 0.0)
    camera.location = (0.0, 0.0, distance)
    return camera


def apply_render(scene) -> None:
    """渲染设置：Standard 色彩（色板所见即所得）+ GP 抗锯齿 + 24 fps。

    **为什么顺手把 fps 设成 24**：生长速率是"每帧切几条边"，同一个值在 60 fps 时间轴上
    就是 2.5 倍速 —— 图案 3 秒长完，看着就是"跳"过去的。缺省的 8 条/帧是按 24 fps 调的；
    坚持用 60 fps 的话，把「生长速率」调到 3~4 才有同样的观感。
    """
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.render.film_transparent = False
    scene.render.resolution_x = RESOLUTION[0]
    scene.render.resolution_y = RESOLUTION[1]
    scene.render.image_settings.file_format = "PNG"
    scene.render.fps = 24
    scene.render.fps_base = 1.0
    gp_settings = getattr(scene, "grease_pencil_settings", None)
    if gp_settings is not None and hasattr(gp_settings, "aa_samples"):
        gp_settings.aa_samples = 16


def setup(scene, span: float) -> None:
    """场景预设：世界底色 + 正交相机 + 渲染设置。"""
    apply_world(scene)
    ensure_camera(scene, span, force=True)
    apply_render(scene)
