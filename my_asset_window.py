import bpy
import sys

class B3D_OT_open_asset_window_from_spreadsheet(bpy.types.Operator):
    """Duplicate Spreadsheet and switch it to Asset Browser in a centered window"""
    bl_idname = "b3d.open_asset_window_from_spreadsheet"
    bl_label = "Open Asset Browser"
    bl_description = "Duplicate Spreadsheet and open as a centered Asset Browser window"
    bl_options = {"REGISTER"}

    def execute(self, context):
        initial_foreground = None
        pid = None
        
        if sys.platform == "win32":
            import ctypes
            import os
            initial_foreground = ctypes.windll.user32.GetForegroundWindow()
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
                
        # Register setup timer to handle window resizing/centering on Windows
        if sys.platform == "win32" and new_win is not None:
            class AssetWindowSetup:
                def __init__(self, init_fg, process_pid, target_width, target_height, win):
                    self.attempts = 0
                    self.init_fg = init_fg
                    self.pid = process_pid
                    self.width = target_width
                    self.height = target_height
                    self.new_win = win

                    import ctypes
                    # Pre-allocate ctypes objects to prevent PyType_GenericNew/GC crashes inside the Blender timer thread
                    self.window_pid = ctypes.c_ulong(0)
                    self.byref_pid = ctypes.byref(self.window_pid)
                    
                    # Define Win32 function signatures to avoid dynamic ctypes wrapper creation in the timer thread
                    self.GetWindowThreadProcessId = ctypes.windll.user32.GetWindowThreadProcessId
                    self.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
                    self.GetWindowThreadProcessId.restype = ctypes.c_uint32
                    
                    self.GetForegroundWindow = ctypes.windll.user32.GetForegroundWindow
                    self.GetForegroundWindow.argtypes = []
                    self.GetForegroundWindow.restype = ctypes.c_void_p
                    
                    self.GetSystemMetrics = ctypes.windll.user32.GetSystemMetrics
                    self.GetSystemMetrics.argtypes = [ctypes.c_int]
                    self.GetSystemMetrics.restype = ctypes.c_int
                    
                    self.MoveWindow = ctypes.windll.user32.MoveWindow
                    self.MoveWindow.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_bool]
                    self.MoveWindow.restype = ctypes.c_bool

                def __call__(self):
                    self.attempts += 1
                    if self.attempts > 40:  # 2 seconds timeout
                        return None

                    try:
                        # If the window has been closed, any access to it will throw a ReferenceError
                        if not self.new_win or self.new_win not in bpy.context.window_manager.windows.values():
                            return None

                        # Resize and center window on Windows
                        hwnd = self.GetForegroundWindow()
                        if hwnd and hwnd != self.init_fg:
                            self.window_pid.value = 0
                            self.GetWindowThreadProcessId(hwnd, self.byref_pid)
                            if self.window_pid.value == self.pid:
                                # Center the window on the screen
                                screen_width = self.GetSystemMetrics(0)
                                screen_height = self.GetSystemMetrics(1)
                                
                                x = (screen_width - self.width) // 2
                                y = (screen_height - self.height) // 2
                                
                                # Move and resize the window
                                self.MoveWindow(hwnd, x, y, self.width, self.height, True)
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
