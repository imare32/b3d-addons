import bpy
from bpy.types import Operator
from bpy.props import StringProperty, IntProperty

# =========================================================================
# Fixed Editor Groups Configuration
# =========================================================================

GROUPS = [
    # Group 1
    [
        ('GeometryNodeTree', None),
        ('ShaderNodeTree', 'OBJECT'),
        ('ShaderNodeTree', 'WORLD'),
        ('IMAGE_EDITOR', None),
        ('DOPESHEET', None),
        ('TIMELINE', None),
        ('TEXT_EDITOR', None),
    ],
    # Group 2
    [
        ('PROPERTIES', None),
        ('CONSOLE', None),
    ],
    # Group 3
    [
        ('SPREADSHEET', None),
        ('ASSETS', None),
        ('FILES', None),
    ]
]

# =========================================================================
# Helper Functions
# =========================================================================

def get_editor_icon(ui_type, s_type):
    if ui_type == 'ShaderNodeTree':
        if s_type == 'WORLD':
            return 'WORLD'
        return 'NODE_MATERIAL'
    
    icon_map = {
        'GeometryNodeTree': 'GEOMETRY_NODES',
        'IMAGE_EDITOR': 'IMAGE',
        'DOPESHEET': 'ACTION',
        'TIMELINE': 'TIME',
        'TEXT_EDITOR': 'TEXT',
        'PROPERTIES': 'PROPERTIES',
        'CONSOLE': 'CONSOLE',
        'SPREADSHEET': 'SPREADSHEET',
        'ASSETS': 'ASSET_MANAGER',
        'FILES': 'FILEBROWSER',
    }
    return icon_map.get(ui_type, 'NONE')

def get_editor_tooltip(ui_type, s_type):
    if ui_type == 'ShaderNodeTree':
        if s_type == 'WORLD':
            return "Shader Editor (World)"
        return "Shader Editor (Object)"
        
    tooltip_map = {
        'GeometryNodeTree': "Geometry Node Editor",
        'IMAGE_EDITOR': "Image Editor",
        'DOPESHEET': "Dope Sheet",
        'TIMELINE': "Timeline",
        'TEXT_EDITOR': "Text Editor",
        'PROPERTIES': "Properties",
        'CONSOLE': "Python Console",
        'SPREADSHEET': "Spreadsheet",
        'ASSETS': "Asset Browser",
        'FILES': "File Browser",
    }
    return tooltip_map.get(ui_type, ui_type)


# =========================================================================
# Operators
# =========================================================================

class B3D_OT_switch_editor(Operator):
    """Switch active area to a specific editor type and set options if applicable"""
    bl_idname = "b3d.switch_editor"
    bl_label = "Switch Editor"
    bl_description = "Switch current area to editor type"
    
    ui_type: StringProperty()
    shader_type: StringProperty(default="")
    
    def execute(self, context):
        area = context.area
        if not area:
            return {'CANCELLED'}
        try:
            area.ui_type = self.ui_type
            if self.ui_type == 'ShaderNodeTree' and self.shader_type:
                space = area.spaces.active
                if space and space.type == 'NODE_EDITOR':
                    space.shader_type = self.shader_type
            return {'FINISHED'}
          
        except Exception as e:
            self.report({'ERROR'}, f"Failed to switch to {self.ui_type}: {e}")
            return {'CANCELLED'}


class B3D_OT_cycle_editors(Operator):
    """Cycle through editors in the current editor's active group"""
    bl_idname = "b3d.cycle_editors"
    bl_label = "Cycle Through Group Editors"
    bl_description = "Cycle to the next editor in the current group"
    
    @classmethod
    def poll(cls, context):
        area = context.area
        if not area:
            return False
            
        current_ui_type = area.ui_type
        current_shader_type = ""
        
        if current_ui_type == 'ShaderNodeTree':
            space = area.spaces.active
            if space and space.type == 'NODE_EDITOR':
                current_shader_type = space.shader_type
                
        # Find which group contains the current editor
        for group in GROUPS:
            for ui_type, s_type in group:
                if ui_type == current_ui_type:
                    if ui_type != 'ShaderNodeTree' or s_type == current_shader_type:
                        return True
        return False
        
    def execute(self, context):
        area = context.area
        if not area:
            return {'CANCELLED'}
            
        current_ui_type = area.ui_type
        current_shader_type = ""
        
        if current_ui_type == 'ShaderNodeTree':
            space = area.spaces.active
            if space and space.type == 'NODE_EDITOR':
                current_shader_type = space.shader_type
                
        # Find which group contains the current editor
        active_group = None
        for group in GROUPS:
            for ui_type, s_type in group:
                if ui_type == current_ui_type:
                    if ui_type != 'ShaderNodeTree' or s_type == current_shader_type:
                        active_group = group
                        break
            if active_group:
                break
                
        if not active_group:
            return {'CANCELLED'}
            
        # Find current index in group
        current_idx = -1
        for i, (ui_type, s_type) in enumerate(active_group):
            if ui_type == current_ui_type:
                if ui_type != 'ShaderNodeTree' or s_type == current_shader_type:
                    current_idx = i
                    break
                    
        # Cycle to the next item
        next_idx = (current_idx + 1) % len(active_group)
        next_ui_type, next_shader_type = active_group[next_idx]
        
        try:
            area.ui_type = next_ui_type
            if next_ui_type == 'ShaderNodeTree' and next_shader_type:
                space = area.spaces.active
                if space and space.type == 'NODE_EDITOR':
                    space.shader_type = next_shader_type
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Failed to cycle editor: {e}")
            return {'CANCELLED'}





# =========================================================================
# UI Drawing
# =========================================================================

def draw_tab_switcher_btn(self, context):
    layout = self.layout
    area = context.area
    if not area:
        return
        
    current_ui_type = area.ui_type
    current_shader_type = ""
    
    if current_ui_type == 'ShaderNodeTree':
        space = area.spaces.active
        if space and space.type == 'NODE_EDITOR':
            current_shader_type = space.shader_type
            
    # Find which group contains the current editor
    active_group = None
    for group in GROUPS:
        for ui_type, s_type in group:
            if ui_type == current_ui_type:
                if ui_type != 'ShaderNodeTree' or s_type == current_shader_type:
                    active_group = group
                    break
        if active_group:
            break
            
    if not active_group:
        return
        
    row = layout.row(align=True)
    
    # Push switches to the right end in editors with sparse headers
    # if area.ui_type in {'CONSOLE', 'SPREADSHEET', 'ASSETS', 'FILES', 'TEXT_EDITOR', 'IMAGE_EDITOR', 'DOPESHEET', 'TIMELINE'}:
    if area.ui_type in {'CONSOLE', 'FILES'}:
        row.separator_spacer()
    else:
        row.separator()
    
    box = row.box()
    switch_row = box.row(align=True)
    
    for ui_type, s_type in active_group:
        icon = get_editor_icon(ui_type, s_type)
        tooltip = get_editor_tooltip(ui_type, s_type)
        is_active = (current_ui_type == ui_type) and (ui_type != 'ShaderNodeTree' or current_shader_type == s_type)
        
        op = switch_row.operator(
            "b3d.switch_editor", 
            text="", 
            icon=icon, 
            depress=is_active,
            text_ctxt=tooltip
        )
        op.ui_type = ui_type
        op.shader_type = s_type if s_type else ""
        
        # Add a separator inside Group 1 after the Image Editor button
        if ui_type == 'IMAGE_EDITOR':
            switch_row.separator()

    # Add separator and recursive orphans purge button for the first group
    if active_group == GROUPS[0]:
        row.separator()
        purge_op = row.operator("outliner.orphans_purge", icon="TRASH", text="", emboss=False)
        purge_op.do_recursive = True


# =========================================================================
# Registration & Shortcuts
# =========================================================================

addon_keymaps = []

# List of headers to draw on
HEADERS = [
    bpy.types.NODE_HT_header, bpy.types.DOPESHEET_HT_header, bpy.types.PROPERTIES_HT_header,
    bpy.types.OUTLINER_HT_header, bpy.types.SPREADSHEET_HT_header, bpy.types.IMAGE_HT_header,
    bpy.types.VIEW3D_HT_header, bpy.types.USERPREF_HT_header, bpy.types.CONSOLE_HT_header,
    bpy.types.TEXT_HT_header, bpy.types.GRAPH_HT_header, bpy.types.SEQUENCER_HT_header,
    bpy.types.CLIP_HT_header, bpy.types.NLA_HT_header, bpy.types.INFO_HT_header,
    bpy.types.FILEBROWSER_HT_header,
]

def register_shortcuts():
    global addon_keymaps
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if not kc:
        return
        
    # Window-level keymap
    km = kc.keymaps.new(name="Window", space_type='EMPTY', region_type='WINDOW')
    
    # Alt + Backtick (ACCENT_GRAVE): Cycle current group
    kmi_cycle = km.keymap_items.new(
        idname="b3d.cycle_editors",
        type='ACCENT_GRAVE',
        value='PRESS',
        alt=True
    )
    addon_keymaps.append((km, kmi_cycle))


def register():
    # Append draw callbacks to headers
    for header in HEADERS:
        header.append(draw_tab_switcher_btn)
        
    # Register keyboard shortcuts
    register_shortcuts()


def unregister():
    # Remove draw callbacks from headers
    for header in HEADERS:
        try:
            header.remove(draw_tab_switcher_btn)
        except Exception:
            pass
            
    # Clean up keyboard shortcuts
    global addon_keymaps
    for km, kmi in addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    addon_keymaps.clear()
