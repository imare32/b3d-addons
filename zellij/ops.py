"""算子：生成 GP 对象、导出 SVG。"""

import os
import random
import time

import bpy
from bpy.props import StringProperty
from bpy_extras.io_utils import ExportHelper
from mathutils import Quaternion, Vector

from . import design, gp
from .scene import build_layers

__all__ = [
    "B3D_OT_zellij_generate",
    "B3D_OT_zellij_export_svg",
    "B3D_OT_zellij_attribute_shading",
]

_DENSITY = {"AUTO": None, "D6": 6, "D10": 10, "D20": 20}
_FOCUS = {"AUTO": None, "NONE": "None", "EIGHT": "Eight", "SIXTEEN": "Sixteen"}
_SHIMMER = {"AUTO": None, "ON": True, "OFF": False}


class B3D_OT_zellij_generate(bpy.types.Operator):
    """Generate a Zellij design as a Grease Pencil object"""

    bl_idname = "b3d.zellij_generate"
    bl_label = "Generate Zellij"
    bl_description = "Generate the design on the object's local XY plane"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.scene is not None

    def execute(self, context):
        props = context.scene.zellij

        seed = props.seed if props.seed >= 0 else random.randrange(1, 2**31 - 1)
        started = time.perf_counter()

        try:
            paper = design.build_design(
                seed,
                density=_DENSITY[props.density],
                focus=_FOCUS[props.focus],
                shimmer=_SHIMMER[props.shimmer],
            )
            shapes = build_layers(
                paper,
                size=props.size,
                margin=0.0,
            )
            gp.build_object(
                shapes,
                size=props.size,
                frame=props.frame,
                fill_width=props.fill_stroke,
                tiling_width=props.tiling_stroke,
                lines_width=props.lines_stroke,
                fill_opacity=props.fill_opacity,
                tiling_opacity=props.tiling_opacity,
                lines_opacity=props.lines_opacity,
                palette=paper.colours,
                properties={
                    "seed": seed,
                    "focus": paper.features.focus,
                    "density": paper.features.line_density,
                    "size": props.size,
                },
            )
        except Exception as exc:  # noqa: BLE001 - 直接把错误转给用户
            self.report({"ERROR"}, f"Zellij: {exc}")
            return {"CANCELLED"}

        elapsed = (time.perf_counter() - started) * 1000.0
        self.report(
            {"INFO"},
            f"Zellij {seed}: {len(shapes)} strokes, {paper.features.line_density} density, "
            f"{elapsed:.0f} ms",
        )
        return {"FINISHED"}


class B3D_OT_zellij_attribute_shading(bpy.types.Operator):
    """Set every 3D viewport to Solid shading with Attribute colours"""

    bl_idname = "b3d.zellij_attribute_shading"
    bl_label = "Attribute Shading"
    bl_description = (
        "Switch all 3D viewports to Solid shading with Attribute colours, so the "
        "fill / stroke vertex colours are visible"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.window_manager is not None

    def execute(self, context):
        count = 0
        for window in context.window_manager.windows:
            screen = window.screen
            if screen is None:
                continue
            for area in screen.areas:
                if area.type != "VIEW_3D":
                    continue
                for space in area.spaces:
                    if space.type != "VIEW_3D":
                        continue
                    shading = space.shading
                    shading.type = "SOLID"
                    shading.color_type = "VERTEX"
                    count += 1
                area.tag_redraw()
        self.report({"INFO"}, f"Attribute shading on {count} viewport(s)")
        return {"FINISHED"}


def _iter_viewports():
    """遍历所有 3D 视口：``(window, screen, area, region, region_3d)``。"""
    window_manager = getattr(bpy.context, "window_manager", None)
    if window_manager is None:
        return

    for window in window_manager.windows:
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            region_3d = area.spaces.active.region_3d
            if region is None or region_3d is None:
                continue
            yield window, screen, area, region, region_3d


def _redraw(window, screen, area, region):
    """强制重画一个视口。

    ``RegionView3D.view_matrix`` 是缓存的，改了 ``view_rotation`` 之后必须真的
    重画一次，导出器才会拿到新的投影矩阵（拖一次视口等价于这里做的重画）。
    """
    try:
        with bpy.context.temp_override(
            window=window, screen=screen, area=area, region=region
        ):
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
    except (RuntimeError, TypeError, AttributeError):
        pass


class _TopOrtho:
    """把视口临时切成俯视正交，退出时还原（导出的是「视口看到的样子」）。"""

    def __init__(self, context, obj):
        self.context = context
        self.obj = obj
        self.saved = []

    def __enter__(self):
        size = float(self.obj.get("size", 1.0))
        center = self.obj.matrix_world @ Vector((size * 0.5, size * 0.5, 0.0))

        for window, screen, area, region, region_3d in _iter_viewports():
            self.saved.append(
                (
                    (window, screen, area, region),
                    region_3d,
                    (
                        region_3d.view_perspective,
                        region_3d.view_rotation.copy(),
                        region_3d.view_location.copy(),
                        region_3d.view_distance,
                    ),
                )
            )
            region_3d.view_perspective = "ORTHO"
            region_3d.view_rotation = Quaternion((1.0, 0.0, 0.0, 0.0))
            region_3d.view_location = center
            region_3d.view_distance = max(size * 2.0, 0.1)
            _redraw(window, screen, area, region)

        return self

    def __exit__(self, *exc_info):
        for (window, screen, area, region), region_3d, saved in self.saved:
            perspective, rotation, location, distance = saved
            region_3d.view_perspective = perspective
            region_3d.view_rotation = rotation
            region_3d.view_location = location
            region_3d.view_distance = distance
            _redraw(window, screen, area, region)

        self.saved = []
        return False


class B3D_OT_zellij_export_svg(bpy.types.Operator, ExportHelper):
    """Export the active Grease Pencil object to SVG"""

    bl_idname = "b3d.zellij_export_svg"
    bl_label = "Export Zellij SVG"
    bl_description = (
        "Export the active Grease Pencil object using Blender's SVG exporter"
    )
    bl_options = {"REGISTER"}

    filename_ext = ".svg"
    filter_glob: StringProperty(default="*.svg", options={"HIDDEN"})

    def invoke(self, context, event):
        obj = context.active_object
        if obj is None or obj.type != "GREASEPENCIL":
            self.report({"ERROR"}, "Active object must be a Grease Pencil object")
            return {"CANCELLED"}

        others = [other for other in context.selected_objects if other != obj]
        if others:
            self.report(
                {"ERROR"},
                "Only the Grease Pencil object may be selected: "
                + ", ".join(other.name for other in others),
            )
            return {"CANCELLED"}

        if not self.filepath:
            self.filepath = bpy.path.ensure_ext(obj.name, self.filename_ext)
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        filepath = self.filepath
        if not filepath.lower().endswith(".svg"):
            filepath = bpy.path.ensure_ext(filepath, ".svg")

        obj = context.active_object
        filled = context.scene.zellij.export_filled

        try:
            with _TopOrtho(context, obj):
                bpy.ops.wm.grease_pencil_export_svg(
                    "EXEC_DEFAULT",
                    filepath=filepath,
                    use_fill=filled,
                    use_uniform_width=True,
                    selected_object_type="ACTIVE",
                    frame_mode="ACTIVE",
                )
        except Exception as exc:  # noqa: BLE001 - 把 Blender 的报错转给用户
            self.report({"ERROR"}, f"SVG export failed: {exc}")
            return {"CANCELLED"}

        if not os.path.exists(filepath):
            self.report({"ERROR"}, f"SVG export did not produce a file: {filepath}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Exported {os.path.basename(filepath)}")
        return {"FINISHED"}
