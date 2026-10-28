"""场景装配：世界底色、斜俯视正交相机、地面纸面、渲染设置（只管"看"，不碰物理）。

格子是立体的（X/Y/Z 三个方向都占地方），所以给一台**斜俯视的正交相机**：方位角 38°、
俯角 20°，既不正面平铺也不俯视压扁，格子的层次和落地后的堆叠都读得出来。

取景按「初始格子落下来」那一刻算：竖向上界是起始格子的顶面 ``drop_height + size/2``，
下界是地面 ``0``，横向至少一只格子的对角线宽 —— 这样第一帧整个格子都在画面里，落地后
的堆只占下半幅，构图不会跟着物理乱跳。

地面是一大片**自发光纸面**（``SILK``，比世界底色的 ``BACKGROUND`` 亮一点）：没有灯光也
能渲染，曲线落上去有明确的"接触面"，而 Solid 模式里靠材质的 ``diffuse_color`` 显示。
"""

from __future__ import annotations

import math

import bpy

from .palette import BACKGROUND, SILK, hex_to_linear

__all__ = [
    "CAMERA_NAME",
    "GROUND_NAME",
    "RESOLUTION",
    "apply_render",
    "apply_world",
    "ensure_camera",
    "ensure_ground",
    "frame_of",
    "remove_ground",
    "setup",
]

CAMERA_NAME = "Sim Hilbert Camera"
GROUND_NAME = "Sim Hilbert Ground"

#: 相机方位角（绕 Z）与俯角：38° / 20° 是实测最能看懂格子层次的组合。
AZIMUTH = math.radians(38.0)
ELEVATION = math.radians(20.0)

#: 场景预设的分辨率。
RESOLUTION = (1600, 1000)


def frame_of(size: float, drop_height: float) -> tuple[float, float]:
    """取景范围：返回 ``(正交尺度, 相机目标高度)``。

    竖向要装下 ``[0, drop_height + size/2]``（起始格子的顶面），横向至少 ``1.7 × size``
    （落地后摊开会变宽）。

    注意两个容易算错的地方：``ortho_scale`` 作用在**长边**上（1600×1000 的竖向视野只有
    ``span / 1.6``），而斜视时格子的进深也会抬高它在屏幕上的竖直尺寸
    （``进深 × sin(俯角)``）—— 只顾竖向高度的话，格子后面那半个会被切出画面。
    """
    extent = max(float(drop_height) + 0.5 * float(size), 1e-3)
    aspect = RESOLUTION[0] / RESOLUTION[1]
    screen = extent * math.cos(ELEVATION) + 1.4 * float(size) * math.sin(ELEVATION)
    span = max(1.8 * float(size), 1.08 * screen * aspect)
    return span, 0.5 * extent


def apply_world(scene) -> None:
    """世界背景铺项目色板的画布底色（``BACKGROUND``），转线性后写进节点。"""
    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("Sim Hilbert World")
        scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    colour = hex_to_linear(BACKGROUND)
    if background is not None:
        background.inputs[0].default_value = (*colour, 1.0)
    world.color = colour


def ensure_camera(scene, span: float, target_z: float = 0.0, force: bool = False):
    """按取景尺寸建/摆一台斜俯视正交相机。

    * 场景没有相机 → 建一台（位置进当前激活的 collection）；
    * 场景相机**是这台**（数据块叫 ``CAMERA_NAME``）→ 每次都重新取景，改尺寸 / 落差后
      构图自动跟着走；
    * 场景相机是别人的 → 只在 ``force=True``（场景预设）时接管，绝不悄悄挪用户自己的相机。
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

    distance = float(span) * 2.5
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = float(span)
    camera.data.clip_start = 0.01
    camera.data.clip_end = distance * 6.0
    # rotation_euler 是 XYZ 序（R = Rz·Ry·Rx）：先俯下 (π/2 − 仰角)，再绕 Z 转到方位角
    camera.rotation_euler = (math.pi * 0.5 - ELEVATION, 0.0, AZIMUTH)
    horizontal = math.cos(ELEVATION) * distance
    camera.location = (
        math.sin(AZIMUTH) * horizontal,
        -math.cos(AZIMUTH) * horizontal,
        float(target_z) + math.sin(ELEVATION) * distance,
    )
    return camera


def _ground_material():
    """自发光纸面：没有灯光也能渲染，颜色就是色板里的 ``SILK``。"""
    mat = bpy.data.materials.get(GROUND_NAME)
    if mat is None:
        mat = bpy.data.materials.new(GROUND_NAME)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (320, 0)
    emission = nodes.new("ShaderNodeEmission")
    emission.location = (0, 0)
    colour = hex_to_linear(SILK)
    emission.inputs["Color"].default_value = (*colour, 1.0)
    emission.inputs["Strength"].default_value = 1.0
    mat.node_tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    # Solid 模式的着色用这个颜色（材质预览 / 渲染走节点）
    mat.diffuse_color = (*colour, 1.0)
    return mat


def ensure_ground(scene, span: float, height: float = 0.0, collection=None):
    """建/取地面纸面（放在 ``height`` 上），并随取景尺寸缩放。

    纸面开得比取景范围大很多（8 倍正交尺度）：正交相机的画面里一旦露出纸面的边，
    构图就毁了 —— 而斜视时纸面的远边很容易跑进画面。
    """
    size = float(span) * 8.0
    obj = bpy.data.objects.get(GROUND_NAME)
    if obj is None or obj.type != "MESH":
        mesh = bpy.data.meshes.new(GROUND_NAME)
        half = size * 0.5
        mesh.from_pydata(
            [
                (-half, -half, 0.0),
                (half, -half, 0.0),
                (half, half, 0.0),
                (-half, half, 0.0),
            ],
            [],
            [(0, 1, 2, 3)],
        )
        mesh.update()
        obj = bpy.data.objects.new(GROUND_NAME, mesh)
        target = collection or bpy.context.collection or bpy.context.scene.collection
        target.objects.link(obj)

    if not obj.data.materials:
        obj.data.materials.append(_ground_material())
    obj.location = (0.0, 0.0, float(height))
    return obj


def remove_ground() -> bool:
    """删掉地面纸面（连带网格数据块）。返回是否真的删了。"""
    obj = bpy.data.objects.get(GROUND_NAME)
    if obj is None:
        return False
    mesh = obj.data if obj.type == "MESH" else None
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)
    return True


def apply_render(scene) -> None:
    """渲染设置：Standard 色彩（色板所见即所得）+ GP 抗锯齿。"""
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.render.film_transparent = False
    scene.render.resolution_x = RESOLUTION[0]
    scene.render.resolution_y = RESOLUTION[1]
    scene.render.image_settings.file_format = "PNG"
    gp_settings = getattr(scene, "grease_pencil_settings", None)
    if gp_settings is not None and hasattr(gp_settings, "aa_samples"):
        gp_settings.aa_samples = 16


def setup(scene, size: float, drop_height: float, ground: float = 0.0) -> None:
    """场景预设：世界底色 + 正交相机 + 地面 + 渲染设置。"""
    span, target_z = frame_of(size, drop_height)
    apply_world(scene)
    ensure_camera(scene, span, target_z, force=True)
    ensure_ground(scene, span, ground)
    apply_render(scene)
