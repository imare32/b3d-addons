import bpy
import sys

class B3D_OT_open_asset_window_from_spreadsheet(bpy.types.Operator):
    """Duplicate Spreadsheet and switch it to Asset Browser in a centered window"""
    bl_idname = "b3d.open_asset_window_from_spreadsheet"
    bl_label = "Open Asset Browser"
    bl_description = "Duplicate Spreadsheet and open as a centered Asset Browser window"
    bl_options = {"REGISTER"}

    def execute(self, context):
        from .platform_window import window_helper
        import os

        initial_foreground = window_helper.get_foreground_window()
        pid = os.getpid()

        # Capture current windows list
        current_windows = set(context.window_manager.windows)
        
        # Duplicate the active Spreadsheet area into a new window
        bpy.ops.screen.area_dupli("INVOKE_DEFAULT")
        
        # Identify the new window and convert it to Asset Browser
        new_win = None
        new_windows = set(context.window_manager.windows) - current_windows
        if new_windows:
            new_win = new_windows.pop()
            if new_win.screen and new_win.screen.areas:
                area = new_win.screen.areas[0]
                area.type = 'FILE_BROWSER'
                area.ui_type = 'ASSETS'
                
        # Register setup timer to handle window resizing/centering
        if new_win is not None and initial_foreground is not None:
            class AssetWindowSetup:
                def __init__(self, init_fg, process_pid, target_width, target_height, win):
                    self.attempts = 0
                    self.init_fg = init_fg
                    self.pid = process_pid
                    self.width = target_width
                    self.height = target_height
                    self.new_win = win

                def __call__(self):
                    self.attempts += 1
                    if self.attempts > 40:  # 2 seconds timeout
                        return None

                    try:
                        # If the window has been closed, any access to it will throw a ReferenceError
                        if not self.new_win or self.new_win not in bpy.context.window_manager.windows.values():
                            return None

                        # Resize and center window
                        hwnd = window_helper.get_foreground_window()
                        if hwnd and hwnd != self.init_fg:
                            window_pid = window_helper.get_window_pid(hwnd)
                            if window_pid == self.pid:
                                # Center the window on the screen
                                screen_width, screen_height = window_helper.get_screen_size()
                                
                                x = (screen_width - self.width) // 2
                                y = (screen_height - self.height) // 2
                                
                                # Move and resize the window
                                window_helper.move_resize_window(hwnd, x, y, self.width, self.height)
                                return None  # Success, stop timer

                    except (ReferenceError, Exception):
                        # Stop the timer immediately if the window was closed or any error occurred
                        return None
                        
                    return 0.05

            bpy.app.timers.register(
                AssetWindowSetup(initial_foreground, pid, 1280, 720, new_win),
                first_interval=0.05
            )

        return {"FINISHED"}

def draw_spreadsheet_header_button(self, context):
    # Only draw the button if there are multiple areas in the screen (i.e. not in a popped single-area window)
    if len(context.screen.areas) <= 1:
        return
        
    self.layout.separator()
    self.layout.operator(
        "b3d.open_asset_window_from_spreadsheet", 
        text="Asset Browser", 
        icon="ASSET_MANAGER"
    )

def register():
    bpy.types.SPREADSHEET_HT_header.append(draw_spreadsheet_header_button)

def unregister():
    bpy.types.SPREADSHEET_HT_header.remove(draw_spreadsheet_header_button)
