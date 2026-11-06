import bpy


class EvaluateExpression(bpy.types.Operator):
    """Simple expression test"""

    bl_idname = "dev.expr_test"
    bl_label = "Expression Test"
    bl_options = {"REGISTER", "UNDO"}

    expression: bpy.props.StringProperty(
        name="Expression",
        description="Python expression to evaluate",
        default="context.area.ui_type",
    )

    local_vars = {"bpy": bpy, "C": bpy.context, "D": bpy.data}

    def execute(self, context):
        self.local_vars.update({"context": context})
        try:
            result = eval(self.expression, globals(), self.local_vars)
            self.report({"INFO"}, f"Result: {result}")
        except Exception as e:
            self.report({"ERROR"}, f"Error: {e}")
        return {"FINISHED"}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)


class B3D_OT_copy_text(bpy.types.Operator):
    """Copy text to clipboard"""

    bl_idname = "b3d.copy_text"
    bl_label = "Copy Text"
    bl_options = {"REGISTER", "UNDO"}

    text: bpy.props.StringProperty(
        name="Text",
        description="Text to copy to clipboard",
        default="",
    )

    def execute(self, context):
        context.window_manager.clipboard = self.text
        self.report({"INFO"}, f"Copied to clipboard: {self.text}")
        return {"FINISHED"}


class SetSceneFrameEnd(bpy.types.Operator):
    """Set Scene Frame End"""

    bl_idname = "scene.set_frame_end"
    bl_label = "Set Scene Frame End"

    def execute(self, context):
        if context.scene.frame_end != 3000:
            context.scene.frame_end = 3000
        else:
            context.scene.frame_end = 250

        def draw(self: bpy.types.UIPopupMenu, context):
            self.layout.prop(context.scene, "frame_end")
            self.layout.prop(context.scene, "frame_current")

        bpy.context.window_manager.popup_menu(draw, title="Set Frame", icon="BLENDER")

        return {"FINISHED"}


class DuplicateOrFullscreenWindow(bpy.types.Operator):
    """Duplicate or Fullscreen the current window"""

    bl_idname = "window.duplicate_or_fullscreen"
    bl_label = "Duplicate or Fullscreen"

    def execute(self, context):
        area_count = len(context.screen.areas)
        area_type = context.area.type
        is_properties = area_type == "PROPERTIES"
        is_outliner = area_type == "OUTLINER"

        if area_count == 1:
            if (
                area_type not in ["PROPERTIES", "OUTLINER", "PREFERENCES"]
                and context.window.height < 1080
            ):
                bpy.ops.wm.window_fullscreen_toggle()
            else:
                bpy.ops.wm.window_close()
        else:
            from .platform_window import window_helper
            import os

            if is_properties or is_outliner:
                pid = os.getpid()
                initial_foreground = window_helper.get_foreground_window()

                if initial_foreground is not None:

                    class WindowResizer:
                        def __init__(self, mode):
                            self.attempts = 0
                            self.mode = mode

                        def __call__(self):
                            self.attempts += 1
                            if self.attempts > 40:  # 2 seconds timeout
                                return None

                            hwnd = window_helper.get_foreground_window()
                            if not hwnd or hwnd == initial_foreground:
                                return 0.05

                            window_pid = window_helper.get_window_pid(hwnd)
                            if window_pid != pid:
                                return 0.05

                            rect_info = window_helper.get_window_rect(hwnd)
                            if rect_info:
                                left, top, right, bottom, width, height = rect_info
                                if self.mode == "PROPERTIES":
                                    delta_width = 312  # Expand 312 pixels to the left
                                    new_left = left - delta_width
                                    new_width = width + delta_width
                                    window_helper.move_resize_window(
                                        hwnd,
                                        new_left,
                                        top,
                                        new_width,
                                        height,
                                    )
                                elif self.mode == "OUTLINER":
                                    delta_height = 400  # Expand 400 pixels downward
                                    delta_width = 200  # Expand 200 pixels leftward
                                    new_left = left - delta_width
                                    new_width = width + delta_width
                                    new_height = height + delta_height
                                    window_helper.move_resize_window(
                                        hwnd,
                                        new_left,
                                        top,
                                        new_width,
                                        new_height,
                                    )
                                return None
                            return 0.05

                    mode = "PROPERTIES" if is_properties else "OUTLINER"
                    bpy.app.timers.register(WindowResizer(mode), first_interval=0.05)
            else:
                initial_windows = set(context.window_manager.windows)

                class WindowMaximizer:
                    def __init__(self, init_wins):
                        self.attempts = 0
                        self.initial_windows = init_wins

                    def __call__(self):
                        self.attempts += 1
                        if self.attempts > 40:  # 2 seconds timeout
                            return None

                        current_windows = set(context.window_manager.windows)
                        new_windows = current_windows - self.initial_windows
                        if new_windows:
                            new_win = list(new_windows)[0]
                            override = {
                                "window": new_win,
                                "screen": new_win.screen,
                                "area": new_win.screen.areas[0]
                                if (new_win.screen and new_win.screen.areas)
                                else None,
                                "workspace": context.workspace,
                            }
                            try:
                                if hasattr(context, "temp_override"):
                                    with context.temp_override(**override):
                                        bpy.ops.wm.window_fullscreen_toggle()
                                else:
                                    bpy.ops.wm.window_fullscreen_toggle(override)
                            except Exception as e:
                                print(f"[b3d] Maximize window error: {e}")
                            return None
                        return 0.05

                bpy.app.timers.register(
                    WindowMaximizer(initial_windows), first_interval=0.05
                )

            bpy.ops.screen.area_dupli("INVOKE_DEFAULT")

        return {"FINISHED"}


class ToggleWireframeShow(bpy.types.Operator):
    """Toggle Wireframe Display for the Active Mesh Object"""

    bl_idname = "object.toggle_wireframe"
    bl_label = "Toggle Wireframe"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.active_object
        if obj is not None and obj.type in ("MESH", "GREASEPENCIL"):
            obj.show_wire = not obj.show_wire
        return {"FINISHED"}


addon_keymaps = []


def register():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="3D View", space_type="VIEW_3D")
        # Register shortcut Alt + Shift + E to 'VIEW_3D'
        kmi = km.keymap_items.new(
            SetSceneFrameEnd.bl_idname,
            type="E",
            value="PRESS",
            alt=True,
            shift=True,
        )
        addon_keymaps.append((km, kmi))

        # Register shortcut Alt + Shift + W to VIEW_3D (triggers object.toggle_wireframe)
        kmi = km.keymap_items.new(
            ToggleWireframeShow.bl_idname,
            type="W",
            value="PRESS",
            alt=True,
            shift=True,
        )
        addon_keymaps.append((km, kmi))

        for name, space_type in [
            ("Node Editor", "NODE_EDITOR"),
            ("3D View", "VIEW_3D"),
            ("Console", "CONSOLE"),
            ("Property Editor", "PROPERTIES"),
            ("Outliner", "OUTLINER"),
            ("Preferences", "PREFERENCES"),
        ]:
            km = kc.keymaps.new(name=name, space_type=space_type)
            kmi = km.keymap_items.new(
                DuplicateOrFullscreenWindow.bl_idname,
                type="D",
                value="PRESS",
                alt=True,
                shift=True,
            )
            addon_keymaps.append((km, kmi))


def unregister():
    # Unregister keymaps
    for km, kmi in addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    addon_keymaps.clear()
