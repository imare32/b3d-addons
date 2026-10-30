"""算子：生成 / 撒点 / 删除 / 回读参数 / 场景预设。

生成流程全在 :mod:`build` 里（实时处理器走同一条路），算子只做编排与参数校验；
能失败的地方都收成 ``report``，不把 traceback 甩给用户。
"""

from __future__ import annotations

import random
import time

import bpy
from mathutils import Vector

from . import build, gp, seeds, viewport
from .palette import BACKGROUND, INK, hex_to_linear, hex_to_rgb
from .props import DESIGN_KEYS, read_design

__all__ = [
    "DISPLAY_KEY",
    "GENV_OT_delete",
    "GENV_OT_edit_source",
    "GENV_OT_generate",
    "GENV_OT_scatter",
    "GENV_OT_setup_scene",
    "GENV_OT_sync_from_object",
    "GENV_OT_toggle_source_wire",
    "SCATTER_NAME",
    "scatter",
]

#: 撒点建出来的种子对象名（固定名，第二次撒点复用它，不堆一地对象）。
SCATTER_NAME = "Voronoi Seeds"

#: 种子的显示粗细（世界单位）：GP 的单点笔划就是一个圆点。
SEEDS_WIDTH = 0.02

#: 切到线框前，源网格原来的「显示为」记在这儿（切回来照原样还原，不靠猜）。
DISPLAY_KEY = "gen_voronoi_display_type"


def scatter(context, count: int = 48, size: float = 4.0):
    """建 / 复用种子对象，撒一把抖动过的格点（避免近共线的瘦单元），返回对象。

    用抖动格点而不是纯随机：纯随机会撒出大量相邻点，那里会长出一堆针状单元。
    """
    obj = bpy.data.objects.get(SCATTER_NAME)
    if obj is None or obj.type != "GREASEPENCIL":
        obj = gp.create_object(SCATTER_NAME, context.collection)

    side = max(2, round(count**0.5))
    step = float(size) / side
    rng = random.Random()
    half = float(size) * 0.5
    points = []
    for row in range(side):
        for column in range(side):
            x = -half + (column + 0.15 + 0.7 * rng.random()) * step
            y = -half + (row + 0.15 + 0.7 * rng.random()) * step
            points.append((x, y, 0.0))

    frame = getattr(context.scene, "frame_current", 1)
    gp.write_dots(
        obj,
        points,
        width=SEEDS_WIDTH,
        colour=hex_to_rgb(INK),
        frame=frame,
    )
    return obj


class GENV_OT_scatter(bpy.types.Operator):
    """撒一把随机种子点（Grease Pencil 单点笔划，进编辑模式就能拖）"""

    bl_idname = "gen_voronoi.scatter"
    bl_label = "撒点"
    bl_options = {"REGISTER", "UNDO"}

    count: bpy.props.IntProperty(
        name="点数",
        description="大约撒多少个点（实际取最近的完全平方数：按方阵撒）",
        default=48,
        min=4,
        soft_max=400,
    )
    size: bpy.props.FloatProperty(
        name="范围",
        description="撒点方阵的边长（世界单位），以原点为中心",
        default=4.0,
        min=0.1,
        soft_max=20.0,
        subtype="DISTANCE",
        unit="LENGTH",
    )

    def execute(self, context):
        settings = context.scene.gen_voronoi
        obj = scatter(context, self.count, self.size)
        settings.source = obj
        settings.dirty = True
        self.report(
            {"INFO"},
            f"{obj.name}: {len(obj.data.layers[0].frames[0].drawing.strokes)} 个种子，"
            f"范围 {self.size:g} × {self.size:g}",
        )
        return {"FINISHED"}


class GENV_OT_generate(bpy.types.Operator):
    """按面板参数划分 Voronoi 单元（没有源物体时先撒一把种子）"""

    bl_idname = "gen_voronoi.generate"
    bl_label = "生成"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.gen_voronoi
        settings.error = ""

        if settings.source is None:
            seed_object = scatter(context)
            if seed_object is None:
                self.report({"ERROR"}, "could not create a seed object")
                return {"CANCELLED"}
            settings.source = seed_object

        started = time.perf_counter()
        try:
            stats, _rebuilt = build.apply(
                settings, collection=context.collection, force=True
            )
        except Exception as exc:
            settings.error = str(exc)
            settings.dirty = True
            self.report({"ERROR"}, f"voronoi failed: {exc}")
            return {"CANCELLED"}

        note = viewport.ensure_vertex_colour(context)[0]
        elapsed = (time.perf_counter() - started) * 1000.0
        message = (
            f"gen_voronoi: {stats['cells']} cells, {stats['points']} points, "
            f"from {stats['seeds']} seeds, {elapsed:.0f} ms | shading {note}"
        )
        obj = gp.find_object()
        if obj is not None:
            message += f" | {obj.name!r}"
        self.report({"INFO"}, message)
        return {"FINISHED"}


class GENV_OT_delete(bpy.types.Operator):
    """删除生成出来的单元对象"""

    bl_idname = "gen_voronoi.delete"
    bl_label = "删除单元"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if not gp.remove_object():
            self.report({"WARNING"}, "no generated object found")
            return {"CANCELLED"}
        context.scene.gen_voronoi.dirty = True
        return {"FINISHED"}


class GENV_OT_sync_from_object(bpy.types.Operator):
    """把单元对象上记录的参数读回面板"""

    bl_idname = "gen_voronoi.sync_from_object"
    bl_label = "从对象回读参数"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = gp.find_object()
        if obj is None:
            self.report({"WARNING"}, "no generated object found")
            return {"CANCELLED"}
        if DESIGN_KEYS["source"] not in obj:
            self.report({"WARNING"}, "object has no stored generation parameters")
            return {"CANCELLED"}

        settings = context.scene.gen_voronoi
        source = bpy.data.objects.get(str(obj[DESIGN_KEYS["source"]]))
        if seeds.is_source(source):
            settings.source = source
        found = read_design(obj, settings)
        settings.dirty = False
        self.report(
            {"INFO"},
            f"read back {found} parameters "
            f"(cells={settings.last_cells}, points={settings.last_points})",
        )
        return {"FINISHED"}


class GENV_OT_edit_source(bpy.types.Operator):
    """进入 / 退出源物体的编辑模式（在里面拖种子，单元实时跟着重划）"""

    bl_idname = "gen_voronoi.edit_source"
    bl_label = "编辑种子"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "gen_voronoi", None)
        return settings is not None and seeds.is_source(settings.source)

    def execute(self, context):
        source = context.scene.gen_voronoi.source
        if source.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
            return {"FINISHED"}

        # 面板按钮点下去时活动物体可能是别的：先把源选上再进编辑模式
        for obj in context.selected_objects:
            obj.select_set(False)
        source.select_set(True)
        context.view_layer.objects.active = source
        bpy.ops.object.mode_set(mode="EDIT")
        return {"FINISHED"}


class GENV_OT_toggle_source_wire(bpy.types.Operator):
    """源网格在线框与实体之间切换（线框能看穿源网格，方便核对单元怎么长出来）"""

    bl_idname = "gen_voronoi.toggle_source_wire"
    bl_label = "源网格线框显示"
    bl_options = {"REGISTER", "UNDO"}

    #: ``Object.display_type`` 的合法值；还原时拿到别的值就退回实体。
    _DISPLAY_TYPES = {"BOUNDS", "SOLID", "TEXTURED", "WIRE"}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "gen_voronoi", None)
        return (
            settings is not None
            and settings.source is not None
            and settings.source.type == "MESH"
        )

    def execute(self, context):
        source = context.scene.gen_voronoi.source
        if source.display_type == "WIRE":
            previous = str(source.get(DISPLAY_KEY, "TEXTURED"))
            source.display_type = (
                previous if previous in self._DISPLAY_TYPES else "TEXTURED"
            )
            if DISPLAY_KEY in source:
                del source[DISPLAY_KEY]
        else:
            # 记下用户自己的显示方式（BOUNDS / SOLID / TEXTURED），切回来时原样还给他
            source[DISPLAY_KEY] = source.display_type
            source.display_type = "WIRE"
        self.report({"INFO"}, f"{source.name}: {source.display_type.lower()} display")
        return {"FINISHED"}


def _paint_canvas(scene) -> None:
    """世界背景铺项目色板的画布底色（``BACKGROUND``），转线性后写进节点。

    强度用 1.0：实测 Standard 视图变换下渲出来正好是色板的 sRGB（``#E4D9D0`` →
    ``0.898 / 0.855 / 0.820``）；用 0.6 会压成 ``0.714 / 0.678 / 0.651``，比色板暗一截，
    而且和视口 Solid 模式（用 ``world.color``，不认强度）对不上。
    """
    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("Gen Voronoi World")
        scene.world = world

    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    colour = hex_to_linear(BACKGROUND)
    if background is not None:
        background.inputs[0].default_value = (*colour, 1.0)
        background.inputs[1].default_value = 1.0
    # 视口 Solid 模式的"世界底色"是另一份（存线性、按视图变换显示）
    world.color = colour




def _extent(settings):
    """图案的 XY 范围与最高点：``(中心, 边长, 顶部 z)``；拿不到就返回 ``None``。

    优先用生成物的包围盒（它含裁剪出来的边界），没有生成物时退到种子点的包围盒。
    """
    obj = gp.find_object()
    corners = []
    if obj is not None and len(obj.bound_box):
        corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
        # GP 没求值过时包围盒可能是个点，退到种子
        if max((corner - corners[0]).length for corner in corners) < 1e-6:
            corners = []
    if not corners:
        source = settings.source
        if source is None or not seeds.is_source(source):
            return None
        points = seeds.world_points(source)
        if not len(points):
            return None
        x_min, y_min, z_min = points.min(axis=0)
        x_max, y_max, z_max = points.max(axis=0)
        corners = [
            Vector((x_min, y_min, z_min)),
            Vector((x_max, y_min, z_min)),
            Vector((x_min, y_max, z_min)),
            Vector((x_max, y_max, z_max)),
        ]

    x_min = min(corner.x for corner in corners)
    x_max = max(corner.x for corner in corners)
    y_min = min(corner.y for corner in corners)
    y_max = max(corner.y for corner in corners)
    top = max(corner.z for corner in corners)
    centre = Vector(((x_min + x_max) * 0.5, (y_min + y_max) * 0.5))
    size = max(x_max - x_min, y_max - y_min, 1e-3)
    return centre, size, top


class GENV_OT_setup_scene(bpy.types.Operator):
    """建正视顶视正交相机、铺项目画布底色、视口背景切世界、切 Standard 色彩"""

    bl_idname = "gen_voronoi.setup_scene"
    bl_label = "场景预设"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        settings = scene.gen_voronoi
        extent = _extent(settings)
        if extent is None:
            self.report({"WARNING"}, "nothing to frame; generate cells first")
            return {"CANCELLED"}
        centre, size, top = extent

        camera = scene.camera
        if camera is None or camera.type != "CAMERA":
            data = bpy.data.cameras.new("Gen Voronoi Camera")
            camera = bpy.data.objects.new("Gen Voronoi Camera", data)
            (context.collection or scene.collection).objects.link(camera)
            scene.camera = camera

        # 图案是平面（XY），所以给的是**顶视正交**相机，不是透视
        camera.data.type = "ORTHO"
        camera.data.ortho_scale = size * 1.1
        camera.location = (centre.x, centre.y, top + size)
        camera.rotation_euler = (0.0, 0.0, 0.0)

        _paint_canvas(scene)
        views = viewport.ensure_world_background(context)

        scene.render.resolution_x = 2048
        scene.render.resolution_y = 2048
        scene.render.film_transparent = False
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"

        self.report(
            {"INFO"},
            f"top view ortho, scale {camera.data.ortho_scale:.2f}, 2048x2048, "
            f"Standard view transform, background {BACKGROUND}, "
            f"viewport background → world ({views})",
        )
        return {"FINISHED"}
