"""算子：生成 / 撒一把 / 删除 / 编辑源 / 回读参数 / 场景预设。

生成流程全在 :mod:`build` 里（实时处理器走同一条路），算子只做编排与参数校验；
能失败的地方都收成 ``report``，不把 traceback 甩给用户。
"""

from __future__ import annotations

import math

import bpy
import numpy as np
from mathutils import Vector

from . import build, gp, strands, viewport
from .palette import BACKGROUND, hex_to_linear
from .props import DESIGN_KEYS, read_design

__all__ = [
    "DISPLAY_KEY",
    "GENI_OT_delete",
    "GENI_OT_edit_source",
    "GENI_OT_generate",
    "GENI_OT_scatter",
    "GENI_OT_setup_scene",
    "GENI_OT_sync_from_object",
    "GENI_OT_toggle_source_wire",
    "SCATTER_NAME",
    "scatter",
]

#: 撒一把建出来的股线对象名（固定名，第二次撒点复用它，不堆一地对象）。
SCATTER_NAME = "Interlace Strands"

#: 每条股线的采样点数（POLY 样条，够平滑又不至于让交叉检测变慢）。
SCATTER_POINTS = 72

#: 撒出来的股线在横向跨度里走几个完整波（1.5 个周期看起来最像一截编织带）。
SCATTER_CYCLES = 1.5

#: 振幅相对跨度的比例（太大会互相压扁，太小又交叉不密）。
SCATTER_AMPLITUDE = 0.21

#: 切到线框前，源物体原来的「显示为」记在这儿（切回来照原样还原，不靠猜）。
DISPLAY_KEY = "gen_interlace_display_type"


def scatter(context, count: int = 7, size: float = 3.0):
    """建 / 复用股线对象，铺一把「辫子」股线，返回对象。

    每条股线都是**同频、同幅、初相依次错开**的正弦：这种排布下任意两条都会在一个周期里
    交叉两次、而且交叉角不浅 —— 编出来就是规整的菱形编织。（各给一个频率看着"随机"，
    但会撒出一堆几乎相切的交叉，切口又长又难看。）股线是一条曲线，进编辑模式就能拖点。
    """
    obj = bpy.data.objects.get(SCATTER_NAME)
    if obj is None or obj.type != "CURVE":
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)
        data = bpy.data.curves.new(SCATTER_NAME, "CURVE")
        data.dimensions = "3D"
        obj = bpy.data.objects.new(SCATTER_NAME, data)
        (context.collection or context.scene.collection).objects.link(obj)

    data = obj.data
    data.splines.clear()

    total = max(2, int(count))
    span = float(size) * 0.5
    amplitude = float(size) * SCATTER_AMPLITUDE
    for index in range(total):
        phase = math.tau * index / total
        coords = []
        for step in range(SCATTER_POINTS):
            x = -span + 2.0 * span * step / (SCATTER_POINTS - 1)
            y = amplitude * math.sin(
                math.tau * SCATTER_CYCLES * (x + span) / (2.0 * span) + phase
            )
            coords.extend((x, y, 0.0, 1.0))

        spline = data.splines.new("POLY")
        spline.points.add(SCATTER_POINTS - 1)
        spline.points.foreach_set("co", coords)
        spline.use_cyclic_u = False
    return obj


class GENI_OT_scatter(bpy.types.Operator):
    """撒一把正弦股线（曲线对象，进编辑模式就能拖点）"""

    bl_idname = "gen_interlace.scatter"
    bl_label = "撒一把股线"
    bl_options = {"REGISTER", "UNDO"}

    count: bpy.props.IntProperty(
        name="股线数",
        description="铺几条正弦股线（同频、同幅、初相依次错开，编出来是规整的菱形）",
        default=7,
        min=2,
        soft_max=40,
    )
    size: bpy.props.FloatProperty(
        name="范围",
        description="图案的横向跨度（世界单位），以原点为中心",
        default=3.0,
        min=0.1,
        soft_max=20.0,
        subtype="DISTANCE",
        unit="LENGTH",
    )

    def execute(self, context):
        settings = context.scene.gen_interlace
        obj = scatter(context, self.count, self.size)
        settings.source = obj
        settings.dirty = True
        self.report(
            {"INFO"},
            f"{obj.name}: {len(obj.data.splines)} 条股线，跨度 {self.size:g}",
        )
        return {"FINISHED"}


class GENI_OT_generate(bpy.types.Operator):
    """按面板参数编一次（没有股线源时先撒一把）"""

    bl_idname = "gen_interlace.generate"
    bl_label = "生成"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.gen_interlace
        settings.error = ""
        if not strands.is_source(settings.source):
            obj = scatter(context)
            settings.source = obj
            settings.dirty = True

        settings.dirty = True
        try:
            stats, _rebuilt = build.apply(settings, force=True)
        except ValueError as exc:
            settings.error = str(exc)
            settings.dirty = True
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        state, _changed = viewport.ensure_vertex_colour(context)
        self.report(
            {"INFO"},
            f"gen_interlace: {stats['strands']} 条股线 · {stats['crossings']} 个交叉 · "
            f"{stats['pieces']} 截 / {stats.get('patches', 0)} 补片 / "
            f"{stats['points']} 点 · {stats['ms']:.0f} ms | "
            f"shading {state} | '{gp.OBJECT_NAME}'",
        )
        return {"FINISHED"}


class GENI_OT_delete(bpy.types.Operator):
    """删掉生成对象（股线源留着）"""

    bl_idname = "gen_interlace.delete"
    bl_label = "删除"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        removed = gp.remove_object()
        settings = context.scene.gen_interlace
        settings.dirty = True
        settings.last_strands = 0
        settings.last_crossings = 0
        settings.last_pieces = 0
        settings.last_patches = 0
        settings.last_points = 0
        settings.last_relaxed = 0
        settings.last_ms = 0.0
        settings.last_note = ""
        settings.error = ""
        self.report({"INFO"}, "删掉了生成对象" if removed else "没有生成对象可删")
        return {"FINISHED"}


class GENI_OT_edit_source(bpy.types.Operator):
    """进出股线源的编辑模式（曲线 / GP 都行）"""

    bl_idname = "gen_interlace.edit_source"
    bl_label = "编辑股线"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        source = context.scene.gen_interlace.source
        if not strands.is_source(source):
            self.report({"ERROR"}, "没有股线源")
            return {"CANCELLED"}

        if source.mode != "OBJECT":
            for obj in list(context.selected_objects):
                if obj.mode != "OBJECT":
                    obj.select_set(True)
            bpy.ops.object.mode_set(mode="OBJECT")
            self.report({"INFO"}, f"{source.name}: 回到物体模式")
            return {"FINISHED"}

        for obj in list(context.selected_objects):
            obj.select_set(False)
        source.select_set(True)
        context.view_layer.objects.active = source
        bpy.ops.object.mode_set(mode="EDIT")
        self.report({"INFO"}, f"{source.name}: 编辑模式（拖点，编织跟着变）")
        return {"FINISHED"}


class GENI_OT_sync_from_object(bpy.types.Operator):
    """从生成对象上记录的参数回读面板"""

    bl_idname = "gen_interlace.sync_from_object"
    bl_label = "回读参数"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.gen_interlace
        obj = gp.find_object()
        if obj is None:
            self.report({"WARNING"}, "没有生成对象可回读")
            return {"CANCELLED"}

        found = read_design(obj, settings)
        name = str(obj.get(DESIGN_KEYS["source"], ""))
        source = bpy.data.objects.get(name) if name else None
        if strands.is_source(source):
            settings.source = source
            found += 1
        settings.dirty = True
        self.report(
            {"INFO"},
            f"回读 {found} 项参数（{settings.last_strands} 条股线 · "
            f"{settings.last_crossings} 个交叉 · {settings.last_pieces} 截）",
        )
        return {"FINISHED"}


class GENI_OT_toggle_source_wire(bpy.types.Operator):
    """源物体在实体 / 线框之间切换（套在图案外面时切线框才看得穿）"""

    bl_idname = "gen_interlace.toggle_source_wire"
    bl_label = "源显示为线框"
    bl_options = {"UNDO"}

    _DISPLAY_TYPES = {"TEXTURED", "SOLID", "WIRE", "BOUNDS"}

    def execute(self, context):
        source = context.scene.gen_interlace.source
        if source is None:
            self.report({"ERROR"}, "没有股线源")
            return {"CANCELLED"}

        if source.display_type == "WIRE":
            previous = str(source.get(DISPLAY_KEY, "TEXTURED"))
            source.display_type = (
                previous if previous in self._DISPLAY_TYPES else "TEXTURED"
            )
            if DISPLAY_KEY in source:
                del source[DISPLAY_KEY]
        else:
            source[DISPLAY_KEY] = source.display_type
            source.display_type = "WIRE"
        self.report({"INFO"}, f"{source.name}: {source.display_type.lower()} display")
        return {"FINISHED"}


# --- 场景预设 -------------------------------------------------------------


def _paint_canvas(scene) -> None:
    """世界背景铺项目色板的画布底色（``BACKGROUND``），转线性后写进节点。

    强度用 1.0：实测 Standard 视图变换下渲出来正好是色板的 sRGB（``#E4D9D0`` →
    ``0.898 / 0.855 / 0.820``）；用 0.6 会压成 ``0.714 / 0.678 / 0.651``，比色板暗一截，
    而且和视口 Solid 模式（用 ``world.color``，不认强度）对不上。
    """
    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("Gen Interlace World")
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

    优先用生成物的包围盒，没有生成物时退到源股线的包围盒。
    """
    obj = gp.find_object()
    corners = []
    if obj is not None and len(obj.bound_box):
        corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
        # GP 没求值过时包围盒可能是个点，退到股线
        if max((corner - corners[0]).length for corner in corners) < 1e-6:
            corners = []

    if not corners:
        source = settings.source
        if not strands.is_source(source):
            return None
        found = strands.polylines(source, float(settings.step))
        if not found:
            return None
        points = np.vstack([points for points, _closed in found])
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


class GENI_OT_setup_scene(bpy.types.Operator):
    """建正视顶视正交相机、铺项目画布底色、视口背景切世界、切 Standard 色彩"""

    bl_idname = "gen_interlace.setup_scene"
    bl_label = "场景预设"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        settings = scene.gen_interlace
        extent = _extent(settings)
        if extent is None:
            self.report({"WARNING"}, "nothing to frame; generate the lace first")
            return {"CANCELLED"}
        centre, size, top = extent

        camera = scene.camera
        if camera is None or camera.type != "CAMERA":
            data = bpy.data.cameras.new("Gen Interlace Camera")
            camera = bpy.data.objects.new("Gen Interlace Camera", data)
            (context.collection or scene.collection).objects.link(camera)
            scene.camera = camera

        # 图案是平面的（XY），所以给的是**顶视正交**相机，不是透视
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
