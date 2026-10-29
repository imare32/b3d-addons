"""场景装配：世界底色、取景相机、渲染设置（只管"看"，不碰模拟）。

鸟飞在 **Z = 0 的平面**上，画面范围完全由面板的「宽度 / 高度」决定 —— 跟对象包围盒无关
（包围盒是这一帧鸟的实际分布，鸟挤成一小团时按它取景会越拍越近）。所以这里用**正交相机**
按模拟区域取景：透视相机会让远处的鸟变小，而 flocking 的画面感正是"每只鸟一样大"。

相机只做一件事：从略偏的斜上方看。完全垂直俯视时看不出朝向的立体感、也完全用不上锯齿
误差之外的任何线索；斜一点，箭头厚度与遮挡关系会把群体结构交代清楚，代价是画面在竖向
被压缩（取景范围按投影后的两个方向算，见 :func:`ensure_camera`）。
"""

from __future__ import annotations

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

CAMERA_NAME = "Sim Flock Camera"

#: 场景预设的分辨率。
RESOLUTION = (1600, 1000)

#: 相机相对区域中心的朝向（略偏向 +X / −Y 的斜上方）。
_DIRECTION = Vector((0.30, -0.42, 1.0)).normalized()

#: 取景留白。
_MARGIN = 1.10


def apply_world(scene) -> None:
    """世界背景铺项目色板的画布底色（``BACKGROUND``），转线性后写进节点。"""
    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("Sim Flock World")
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


def ensure_camera(scene, width: float, height: float, force: bool = False):
    """按模拟区域摆一台正交相机；``force=True`` 时重新取景（已有机位则不动）。"""
    camera = scene.camera
    if camera is not None and camera.type == "CAMERA" and not force:
        return camera

    if camera is None or camera.type != "CAMERA":
        data = bpy.data.cameras.new(CAMERA_NAME)
        camera = bpy.data.objects.new(CAMERA_NAME, data)
        # 与鸟群对象一样，加进当前激活的 collection
        (bpy.context.collection or scene.collection).objects.link(camera)
        scene.camera = camera

    half_w = max(float(width), 1e-3) * 0.5
    half_h = max(float(height), 1e-3) * 0.5
    distance = max(half_w, half_h) * 6.0

    camera.data.type = "ORTHO"
    camera.data.sensor_fit = "HORIZONTAL"
    camera.location = _DIRECTION * distance
    camera.rotation_euler = (-_DIRECTION).to_track_quat("-Z", "Y").to_euler()

    # 取景：把区域四角投到相机的右 / 上轴上，再换成 ``ortho_scale``（= 画幅横向宽度）。
    # 只按区域宽高取景是不够的：斜看时竖向范围被压缩，画幅又比它更扁，会切掉上下。
    matrix = camera.rotation_euler.to_matrix()
    right = matrix @ Vector((1.0, 0.0, 0.0))
    up = matrix @ Vector((0.0, 1.0, 0.0))
    corners = [
        Vector((x, y, 0.0)) for x in (-half_w, half_w) for y in (-half_h, half_h)
    ]
    extent_right = max(abs(corner.dot(right)) for corner in corners)
    extent_up = max(abs(corner.dot(up)) for corner in corners)
    aspect = RESOLUTION[0] / RESOLUTION[1]

    camera.data.ortho_scale = (
        max(extent_right * 2.0, extent_up * 2.0 * aspect) * _MARGIN
    )
    camera.data.clip_start = distance * 0.01
    camera.data.clip_end = distance * 4.0
    return camera


def setup(scene, width: float, height: float) -> None:
    """场景预设：世界底色 + 重新取景的相机 + 渲染设置。"""
    apply_world(scene)
    apply_render(scene)
    ensure_camera(scene, width, height, force=True)
