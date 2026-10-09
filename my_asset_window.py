import bpy

class B3D_OT_open_asset_window_from_spreadsheet(bpy.types.Operator):
    """Open Asset Browser in a new window via Preferences window"""
    bl_idname = "b3d.open_asset_window_from_spreadsheet"
    bl_label = "Open Asset Browser"
    bl_description = "Open Asset Browser in a new window"
    bl_options = {"REGISTER"}

    def execute(self, context):
        # Capture current windows list
        current_windows = set(context.window_manager.windows)

        # Open Preferences window (creates a stable, well-positioned new window)
        bpy.ops.screen.userpref_show()

        # Identify the new window and convert it to Asset Browser
        new_windows = set(context.window_manager.windows) - current_windows
        if new_windows:
            new_win = new_windows.pop()
            if new_win.screen and new_win.screen.areas:
                area = new_win.screen.areas[0]
                area.type = 'FILE_BROWSER'
                area.ui_type = 'ASSETS'

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
