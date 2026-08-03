import bpy


def get_color_ramp(context):
    ptr = getattr(context, "button_pointer", None)
    prop = getattr(context, "button_prop", None)
    if not ptr:
        return None

    # Case 1: The pointer itself is a ColorRamp
    if isinstance(ptr, bpy.types.ColorRamp):
        return ptr

    # Case 2: The pointer is the owner and the property name matches
    if prop and hasattr(ptr, prop.identifier):
        val = getattr(ptr, prop.identifier)
        if isinstance(val, bpy.types.ColorRamp):
            return val

    # Case 3: Fallback check for property named "color_ramp" on the pointer
    if hasattr(ptr, "color_ramp"):
        val = getattr(ptr, "color_ramp")
        if isinstance(val, bpy.types.ColorRamp):
            return val

    return None


def apply_color_ramp_data(color_ramp, data):
    color_ramp.interpolation = data.get("interpolation", "LINEAR")
    color_ramp.color_mode = data.get("color_mode", "RGB")
    color_ramp.hue_interpolation = data.get("hue_interpolation", "NEAR")

    stops = data.get("elements", [])
    if not stops:
        return

    # Sort stops by position to be clean
    stops = sorted(stops, key=lambda x: x[0])

    elements = color_ramp.elements
    # Remove all but one element
    while len(elements) > 1:
        elements.remove(elements[-1])

    # Set the first element
    elements[0].position = stops[0][0]
    elements[0].color = stops[0][1]

    # Add the rest of the elements
    for pos, color in stops[1:]:
        el = elements.new(pos)
        el.color = color


class B3D_OT_copy_color_ramp(bpy.types.Operator):
    """Copy Color Ramp stop configurations"""

    bl_idname = "b3d.copy_color_ramp"
    bl_label = "Copy Color Ramp"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        color_ramp = get_color_ramp(context)
        if not color_ramp:
            self.report({"WARNING"}, "No active Color Ramp found")
            return {"CANCELLED"}

        data = {
            "interpolation": color_ramp.interpolation,
            "color_mode": color_ramp.color_mode,
            "hue_interpolation": color_ramp.hue_interpolation,
            "elements": [(e.position, list(e.color)) for e in color_ramp.elements],
        }

        # Save to bpy.app.driver_namespace
        bpy.app.driver_namespace["b3d_color_ramp_clipboard"] = data
        self.report({"INFO"}, "Color Ramp copied to clipboard")
        return {"FINISHED"}


class B3D_OT_paste_color_ramp(bpy.types.Operator):
    """Paste Color Ramp configurations from clipboard"""

    bl_idname = "b3d.paste_color_ramp"
    bl_label = "Paste Color Ramp"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return "b3d_color_ramp_clipboard" in bpy.app.driver_namespace

    def execute(self, context):
        color_ramp = get_color_ramp(context)
        if not color_ramp:
            self.report({"WARNING"}, "No active Color Ramp found")
            return {"CANCELLED"}

        data = bpy.app.driver_namespace.get("b3d_color_ramp_clipboard")
        if not data:
            self.report({"WARNING"}, "Clipboard is empty")
            return {"CANCELLED"}

        try:
            apply_color_ramp_data(color_ramp, data)
            self.report({"INFO"}, "Color Ramp pasted successfully")
        except Exception as e:
            self.report({"ERROR"}, f"Failed to paste Color Ramp: {e}")
            return {"CANCELLED"}

        return {"FINISHED"}


class B3D_OT_generate_color_ramp(bpy.types.Operator):
    """Generate a preset Color Ramp"""

    bl_idname = "b3d.generate_color_ramp"
    bl_label = "Generate Color Ramp Preset"
    bl_options = {"REGISTER", "UNDO"}

    preset: bpy.props.StringProperty()

    def execute(self, context):
        color_ramp = get_color_ramp(context)
        if not color_ramp:
            self.report({"WARNING"}, "No active Color Ramp found")
            return {"CANCELLED"}

        presets_data = {
            "HEATMAP": {
                "elements": [
                    (0.0, (0.0, 0.0, 0.5, 1.0)),
                    (0.2, (0.0, 1.0, 1.0, 1.0)),
                    (0.4, (0.0, 1.0, 0.0, 1.0)),
                    (0.6, (1.0, 1.0, 0.0, 1.0)),
                    (0.8, (1.0, 0.5, 0.0, 1.0)),
                    (1.0, (1.0, 0.0, 0.0, 1.0)),
                ]
            },
            "OCEAN": {
                "elements": [
                    (0.0, (0.0, 0.05, 0.2, 1.0)),
                    (0.25, (0.0, 0.2, 0.5, 1.0)),
                    (0.5, (0.0, 0.5, 0.6, 1.0)),
                    (0.75, (0.0, 0.8, 0.8, 1.0)),
                    (1.0, (0.8, 1.0, 1.0, 1.0)),
                ]
            },
            "FOREST": {
                "elements": [
                    (0.0, (0.02, 0.1, 0.05, 1.0)),
                    (0.25, (0.05, 0.25, 0.1, 1.0)),
                    (0.5, (0.15, 0.45, 0.15, 1.0)),
                    (0.75, (0.4, 0.7, 0.2, 1.0)),
                    (1.0, (0.8, 0.9, 0.3, 1.0)),
                ]
            },
            "GRAYSCALE": {
                "elements": [
                    (0.0, (0.0, 0.0, 0.0, 1.0)),
                    (0.25, (0.25, 0.25, 0.25, 1.0)),
                    (0.5, (0.5, 0.5, 0.5, 1.0)),
                    (0.75, (0.75, 0.75, 0.75, 1.0)),
                    (1.0, (1.0, 1.0, 1.0, 1.0)),
                ]
            },
            "ELEVATION": {
                "elements": [
                    (0.0, (0.1, 0.5, 0.2, 1.0)),
                    (0.25, (0.5, 0.7, 0.3, 1.0)),
                    (0.5, (0.8, 0.8, 0.4, 1.0)),
                    (0.75, (0.5, 0.35, 0.2, 1.0)),
                    (1.0, (0.95, 0.95, 0.95, 1.0)),
                ]
            },
            "RAINBOW": {
                "elements": [
                    (0.0, (0.5, 0.0, 1.0, 1.0)),
                    (0.2, (0.0, 0.0, 1.0, 1.0)),
                    (0.4, (0.0, 1.0, 1.0, 1.0)),
                    (0.6, (0.0, 1.0, 0.0, 1.0)),
                    (0.8, (1.0, 1.0, 0.0, 1.0)),
                    (1.0, (1.0, 0.0, 0.0, 1.0)),
                ]
            },
            "MAGMA": {
                "elements": [
                    (0.0, (0.05, 0.03, 0.1, 1.0)),
                    (0.2, (0.3, 0.05, 0.2, 1.0)),
                    (0.4, (0.65, 0.1, 0.25, 1.0)),
                    (0.6, (0.9, 0.3, 0.2, 1.0)),
                    (0.8, (0.98, 0.65, 0.15, 1.0)),
                    (1.0, (0.98, 0.95, 0.6, 1.0)),
                ]
            },
            "VIRIDIS": {
                "elements": [
                    (0.0, (0.27, 0.0, 0.33, 1.0)),
                    (0.2, (0.24, 0.22, 0.5, 1.0)),
                    (0.4, (0.16, 0.44, 0.56, 1.0)),
                    (0.6, (0.13, 0.63, 0.53, 1.0)),
                    (0.8, (0.37, 0.8, 0.38, 1.0)),
                    (1.0, (0.99, 0.9, 0.15, 1.0)),
                ]
            },
            "PLASMA": {
                "elements": [
                    (0.0, (0.05, 0.03, 0.53, 1.0)),
                    (0.2, (0.38, 0.02, 0.63, 1.0)),
                    (0.4, (0.69, 0.09, 0.57, 1.0)),
                    (0.6, (0.9, 0.27, 0.4, 1.0)),
                    (0.8, (0.99, 0.59, 0.18, 1.0)),
                    (1.0, (0.94, 0.97, 0.13, 1.0)),
                ]
            },
            "TERRAIN": {
                "elements": [
                    (0.0, (0.0, 0.1, 0.4, 1.0)),
                    (0.2, (0.1, 0.5, 0.8, 1.0)),
                    (0.4, (0.2, 0.6, 0.2, 1.0)),
                    (0.6, (0.1, 0.4, 0.1, 1.0)),
                    (0.8, (0.5, 0.4, 0.2, 1.0)),
                    (1.0, (0.9, 0.9, 0.9, 1.0)),
                ]
            },
            "DESERT": {
                "elements": [
                    (0.0, (0.9, 0.8, 0.6, 1.0)),
                    (0.25, (0.8, 0.6, 0.4, 1.0)),
                    (0.5, (0.7, 0.4, 0.3, 1.0)),
                    (0.75, (0.55, 0.25, 0.15, 1.0)),
                    (1.0, (0.3, 0.15, 0.1, 1.0)),
                ]
            },
            "SUNSET": {
                "elements": [
                    (0.0, (0.15, 0.1, 0.3, 1.0)),
                    (0.25, (0.45, 0.1, 0.4, 1.0)),
                    (0.5, (0.8, 0.25, 0.25, 1.0)),
                    (0.75, (0.95, 0.55, 0.15, 1.0)),
                    (1.0, (0.98, 0.85, 0.3, 1.0)),
                ]
            },
            "PASTEL": {
                "elements": [
                    (0.0, (0.95, 0.75, 0.75, 1.0)),
                    (0.2, (0.95, 0.85, 0.75, 1.0)),
                    (0.4, (0.95, 0.95, 0.75, 1.0)),
                    (0.6, (0.75, 0.95, 0.85, 1.0)),
                    (0.8, (0.85, 0.75, 0.95, 1.0)),
                    (1.0, (0.75, 0.85, 0.95, 1.0)),
                ]
            },
            "COOL_WARM": {
                "elements": [
                    (0.0, (0.15, 0.3, 0.8, 1.0)),
                    (0.25, (0.6, 0.75, 0.95, 1.0)),
                    (0.5, (0.95, 0.95, 0.95, 1.0)),
                    (0.75, (0.95, 0.65, 0.6, 1.0)),
                    (1.0, (0.85, 0.15, 0.15, 1.0)),
                ]
            },
            "CYAN_MAGENTA": {
                "elements": [
                    (0.0, (0.0, 0.8, 0.8, 1.0)),
                    (0.25, (0.6, 0.95, 0.95, 1.0)),
                    (0.5, (0.95, 0.95, 0.95, 1.0)),
                    (0.75, (0.95, 0.6, 0.85, 1.0)),
                    (1.0, (0.8, 0.0, 0.6, 1.0)),
                ]
            },
        }

        data = presets_data.get(self.preset)
        if not data:
            self.report({"WARNING"}, f"Preset '{self.preset}' not found")
            return {"CANCELLED"}

        try:
            apply_color_ramp_data(color_ramp, data)
            self.report({"INFO"}, f"Generated Color Ramp preset: {self.preset}")
        except Exception as e:
            self.report({"ERROR"}, f"Failed to generate Color Ramp: {e}")
            return {"CANCELLED"}

        return {"FINISHED"}


class B3D_MT_generate_color_ramp_menu(bpy.types.Menu):
    """Submenu containing the list of classic color ramp presets"""

    bl_idname = "B3D_MT_generate_color_ramp_menu"
    bl_label = "Generate Color Ramp"

    def draw(self, context):
        layout = self.layout
        presets = [
            ("HEATMAP", "Heatmap (热力图)"),
            ("OCEAN", "Ocean (海洋)"),
            ("FOREST", "Forest (森林)"),
            ("GRAYSCALE", "Grayscale (纯灰度)"),
            ("ELEVATION", "Elevation (地图海拔)"),
            ("RAINBOW", "Rainbow (彩虹)"),
            ("MAGMA", "Magma (岩浆)"),
            ("VIRIDIS", "Viridis (翠绿)"),
            ("PLASMA", "Plasma (等离子)"),
            ("TERRAIN", "Terrain (地形)"),
            ("DESERT", "Desert (沙漠)"),
            ("SUNSET", "Sunset (落日)"),
            ("PASTEL", "Pastel (马卡龙)"),
            ("COOL_WARM", "Cool-Warm (冷暖色)"),
            ("CYAN_MAGENTA", "Cyan-Magenta (青红)"),
        ]
        for key, name in presets:
            op = layout.operator("b3d.generate_color_ramp", text=name)
            op.preset = key


def draw_color_ramp_context_menu(self, context):
    color_ramp = get_color_ramp(context)
    if color_ramp is not None:
        layout = self.layout
        layout.separator()
        layout.operator("b3d.copy_color_ramp", text="Copy Color Ramp", icon="COPYDOWN")
        layout.operator(
            "b3d.paste_color_ramp", text="Paste Color Ramp", icon="PASTEDOWN"
        )
        layout.menu("B3D_MT_generate_color_ramp_menu", icon="COLOR")


def register():
    bpy.types.UI_MT_button_context_menu.append(draw_color_ramp_context_menu)


def unregister():
    bpy.types.UI_MT_button_context_menu.remove(draw_color_ramp_context_menu)
