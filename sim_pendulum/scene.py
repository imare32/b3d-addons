"""场景装配：世界底色、正面正交相机、渲染设置（只管"看"，不碰物理）。

阵列本身是**一张平面相图**：所有摆都在 Y = 0 的 XZ 平面上，杆长方向是 −Z，所以相机就
摆正前方（沿 +Y 看），正交投影 —— 没有任何透视缩短，横轴到底对应第几个初值一眼数得清。
不做倾斜：斜视只会让上下两排的摆互相压住，相图就散了。

取景按整个阵列的包围盒算::

    width  = (n−1)·间距 + 2·reach        （reach = l1 + l2，摆可以甩到水平）
    height = (n−1)·间距 + 2·reach
    span   = max(width, height × 宽高比) × 1.06

``ortho_scale`` 作用在**长边**上（1200×1100 的竖向视野只有 ``span / 1.09``），所以
竖向要乘宽高比 —— 只按宽度算的话，上下两排会被切出画面。阵列是正方形而画面不是，
左右留白就是画布底色，正好当留白用。

**分辨率跟着边长走**：每世界单位固定 ``_PIXELS_PER_UNIT`` 个像素，长边封顶
``_MAX_EDGE``。所以 N=16 是 1200 px，N=100 直接给到 3200 px —— 分形图案本来就该用大图
看，按 1200 px 铺 100×100 个摆的话，一根杆只有半个像素宽，颜色全被背景吃掉。
"""

from __future__ import annotations

import bpy

from .palette import BACKGROUND, hex_to_linear

__all__ = [
    "CAMERA_NAME",
    "RESOLUTION",
    "apply_render",
    "apply_world",
    "ensure_camera",
    "frame_of",
    "resolution_for",
    "setup",
]

CAMERA_NAME = "Sim Pendulum Camera"

#: 场景预设的分辨率（阵列是正方形，画面也就用近正方形，左右不必留大白边）。
RESOLUTION = (1200, 1100)

#: 画面宽高比（取景与分辨率共用一份）。
_ASPECT = RESOLUTION[0] / RESOLUTION[1]

#: 每世界单位多少像素：N=16 时正好把整个阵列铺成 1200 px。
_PIXELS_PER_UNIT = 26.0

#: 长边像素上限：再往上就是出印刷图，渲染时间与内存不成比例。
_MAX_EDGE = 3200

#: 取景留白系数。
_MARGIN = 1.06


def frame_of(n: int, spacing: float, reach: float) -> tuple[float, float]:
    """取景：返回 ``(正交尺度, 相机目标 z)``。"""
    extent = max((int(n) - 1) * float(spacing) + 2.0 * float(reach), 1e-3)
    span = max(extent, extent * _ASPECT) * _MARGIN
    return span, 0.0


def resolution_for(n: int, spacing: float, reach: float) -> tuple[int, int]:
    """分辨率：每世界单位 ``_PIXELS_PER_UNIT`` 像素，长边封顶 ``_MAX_EDGE``。

    这样 N 变大时线不会细成亚像素（那样颜色全被背景吃掉），代价是图变大；
    封顶之后还能靠调大「笔半径」继续看清楚。
    """
    span, _ = frame_of(n, spacing, reach)
    width = min(span * _PIXELS_PER_UNIT, float(_MAX_EDGE))
    width = max(round(width), 64) // 2 * 2
    height = max(round(width / _ASPECT), 64) // 2 * 2
    return width, height


def apply_world(scene) -> None:
    """世界背景铺项目色板的画布底色（``BACKGROUND``），转线性后写进节点。"""
    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("Sim Pendulum World")
        scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    colour = hex_to_linear(BACKGROUND)
    if background is not None:
        background.inputs[0].default_value = (*colour, 1.0)
    world.color = colour


def ensure_camera(scene, span: float, target_z: float = 0.0, force: bool = False):
    """按取景尺寸建/摆一台正面正交相机。

    * 场景没有相机 → 建一台（位置进当前激活的 collection）；
    * 场景相机**是这台**（数据块名以 ``CAMERA_NAME`` 开头）→ 每次都重新取景，改边长 /
      间距后构图自动跟着走；
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
        (bpy.context.collection or scene.collection).objects.link(camera)
        scene.camera = camera

    distance = float(span) * 2.0
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = float(span)
    camera.data.clip_start = 0.01
    camera.data.clip_end = distance * 6.0
    # 正前方：rotation X = 90° 让相机从 −Y 朝 +Y 看，画面 x = 世界 X、画面 y = 世界 Z
    camera.rotation_euler = (1.5707963267948966, 0.0, 0.0)
    camera.location = (0.0, -distance, float(target_z))
    return camera


def apply_render(scene, resolution=None) -> None:
    """渲染设置：Standard 色彩（色板所见即所得）+ 分辨率 + GP 抗锯齿。"""
    width, height = resolution or RESOLUTION
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.render.film_transparent = False
    scene.render.resolution_x = int(width)
    scene.render.resolution_y = int(height)
    scene.render.image_settings.file_format = "PNG"
    gp_settings = getattr(scene, "grease_pencil_settings", None)
    if gp_settings is not None and hasattr(gp_settings, "aa_samples"):
        gp_settings.aa_samples = 16


def setup(scene, n: int, spacing: float, reach: float) -> tuple[int, int]:
    """场景预设：世界底色 + 正面正交相机 + 渲染设置。返回选用的分辨率。"""
    span, target_z = frame_of(n, spacing, reach)
    resolution = resolution_for(n, spacing, reach)
    apply_world(scene)
    ensure_camera(scene, span, target_z, force=True)
    apply_render(scene, resolution)
    return resolution
