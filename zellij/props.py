"""场景属性：``scene.zellij``（侧栏面板的所有参数）。"""

import bpy

from . import gp

__all__ = ["B3D_ZellijSettings"]


def _redraw(context):
    screen = getattr(context, "screen", None)
    if screen is None:
        return
    for area in screen.areas:
        area.tag_redraw()


def _update_stroke(self, context):
    """滑块实时回写半径：不重新生成几何，只改已有图层的点属性。"""
    obj = gp.find_object()
    if obj is None:
        return
    gp.apply_stroke_widths(
        obj,
        fill_width=self.fill_stroke,
        tiling_width=self.tiling_stroke,
        lines_width=self.lines_stroke,
    )
    _redraw(context)


def _update_opacity(self, context):
    """透明度滑块 → 两个 Opacity 修改器。"""
    obj = gp.find_object()
    if obj is None:
        return
    gp.apply_opacities(
        obj,
        fill_opacity=self.fill_opacity,
        tiling_opacity=self.tiling_opacity,
        lines_opacity=self.lines_opacity,
    )
    _redraw(context)


class B3D_ZellijSettings(bpy.types.PropertyGroup):
    seed: bpy.props.IntProperty(
        name="Seed",
        description="Random seed, -1 generates a new design every time",
        default=-1,
        min=-1,
    )
    size: bpy.props.FloatProperty(
        name="Size",
        description="Side length of the square canvas",
        default=1.0,
        min=0.001,
        soft_max=10.0,
        precision=3,
    )
    frame: bpy.props.IntProperty(
        name="Frame",
        description="Frame number the strokes are keyed on",
        default=1,
        min=1,
    )

    density: bpy.props.EnumProperty(
        name="Density",
        description="Construction line density",
        items=(
            ("AUTO", "Auto", "Keep the original sketch's random choice"),
            ("D6", "6", "Sparse (9 lines)"),
            ("D10", "10", "Normal (25 lines)"),
            ("D20", "20", "Dense (40 lines)"),
        ),
        default="AUTO",
    )
    focus: bpy.props.EnumProperty(
        name="Focus",
        description="Forced feature in the design",
        items=(
            ("AUTO", "Auto", "Keep the original sketch's random choice"),
            ("NONE", "None", "No forced feature"),
            ("EIGHT", "Eight", "Force a 2x2 block"),
            ("SIXTEEN", "Sixteen", "Force a star pattern"),
        ),
        default="AUTO",
    )
    shimmer: bpy.props.EnumProperty(
        name="Shimmer",
        description="Random brightness variation of the accent colours",
        items=(
            ("AUTO", "Auto", "Keep the original sketch's random choice"),
            ("ON", "On", "Always shimmer"),
            ("OFF", "Off", "Never shimmer"),
        ),
        default="AUTO",
    )

    fill_stroke: bpy.props.FloatProperty(
        name="Fill Stroke",
        description="Radius of the fill layers' strokes, as a fraction of Size (0 = hidden)",
        default=0.001,
        min=0.0,
        max=0.01,
        precision=4,
        subtype="FACTOR",
        update=_update_stroke,
    )
    tiling_stroke: bpy.props.FloatProperty(
        name="Tiling Stroke",
        description="Radius of the red Tiling outlines, as a fraction of Size (0 = hidden)",
        default=0.001,
        min=0.0,
        max=0.01,
        precision=4,
        subtype="FACTOR",
        update=_update_stroke,
    )
    lines_stroke: bpy.props.FloatProperty(
        name="Lines Stroke",
        description="Radius of the dashed construction lines, as a fraction of Size (0 = hidden)",
        default=0.001,
        min=0.0,
        max=0.01,
        precision=4,
        subtype="FACTOR",
        update=_update_stroke,
    )

    fill_opacity: bpy.props.FloatProperty(
        name="Fill Opacity",
        description="Opacity modifier value for the fill layers' strokes",
        default=gp.FILL_OPACITY,
        min=0.0,
        max=1.0,
        precision=3,
        update=_update_opacity,
    )
    tiling_opacity: bpy.props.FloatProperty(
        name="Tiling Opacity",
        description="Opacity modifier value for the red Tiling layer",
        default=gp.TILING_OPACITY,
        min=0.0,
        max=1.0,
        precision=3,
        update=_update_opacity,
    )
    lines_opacity: bpy.props.FloatProperty(
        name="Lines Opacity",
        description="Opacity modifier value for the dashed Lines layer",
        default=gp.LINES_OPACITY,
        min=0.0,
        max=1.0,
        precision=3,
        update=_update_opacity,
    )

    export_filled: bpy.props.BoolProperty(
        name="Filled",
        description="Export solid shapes; disable for thin outlines (debug view)",
        default=True,
    )


def register():
    bpy.types.Scene.zellij = bpy.props.PointerProperty(type=B3D_ZellijSettings)


def unregister():
    try:
        del bpy.types.Scene.zellij
    except (AttributeError, RuntimeError, TypeError):
        pass
