"""侧栏面板：``View3D > UI > Zellij``。"""

import bpy

from . import gp

__all__ = ["B3D_PT_zellij"]


class B3D_PT_zellij(bpy.types.Panel):
    bl_label = "Zellij"
    bl_idname = "B3D_PT_zellij"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Zellij"

    def draw(self, context):
        layout = self.layout
        props = context.scene.zellij

        row = layout.row(align=True)
        row.operator("b3d.zellij_generate", text="Generate")
        row.operator("b3d.zellij_attribute_shading", text="", icon="SHADING_SOLID")

        placement = layout.box()
        placement.label(text="Placement")
        col = placement.column(align=True)
        col.prop(props, "seed")
        col.prop(props, "size")
        col.prop(props, "frame")

        design = layout.box()
        design.label(text="Design")
        col = design.column(align=True)
        col.prop(props, "density")
        col.prop(props, "focus")
        col.prop(props, "shimmer")

        stroke = layout.box()
        stroke.label(text="Stroke")
        col = stroke.column(align=True)
        col.prop(props, "fill_stroke", text="Fill")
        col.prop(props, "tiling_stroke", text="Tiling")
        col.prop(props, "lines_stroke", text="Lines")

        opacity = layout.box()
        opacity.label(text="Opacity")
        col = opacity.column(align=True)
        col.prop(props, "fill_opacity", text="Fill")
        col.prop(props, "tiling_opacity", text="Tiling")
        col.prop(props, "lines_opacity", text="Lines")

        output = layout.box()
        output.label(text="Output")
        output.prop(props, "export_filled")
        output.operator("b3d.zellij_export_svg", text="Export SVG")

        obj = gp.find_object()
        if obj is None:
            return

        layers = layout.box()
        layers.label(text="Layers")
        col = layers.column(align=True)
        for layer in obj.data.layers:
            col.prop(layer, "hide", text=layer.name)

        info = layers.column(align=True)
        info.label(
            text=f"seed {obj.get('seed', '?')} · density {obj.get('density', '?')}"
        )
        info.label(text=f"focus {obj.get('focus', '?')}")
