import bpy
from bpy.types import Menu, Operator
from bpy.props import StringProperty

# =========================================================================
# Helper Functions
# =========================================================================

def get_domain_icon(domain):
    icon_map = {
        'POINT': 'VERTEXSEL',
        'EDGE': 'EDGESEL',
        'FACE': 'FACESEL',
        'CORNER': 'UV',
        'CURVE': 'CURVE_BEZCURVE',
        'INSTANCE': 'EMPTY_AXIS',
    }
    return icon_map.get(domain, 'PROPERTIES')

# =========================================================================
# Operators
# =========================================================================

class B3D_OT_add_named_attribute_node(Operator):
    """Add a Named Attribute node with preconfigured settings to the node tree"""
    bl_idname = "b3d.add_named_attribute_node"
    bl_label = "Add Named Attribute Node"
    bl_description = "Add a Named Attribute node to the active Geometry Nodes tree"
    bl_options = {'REGISTER', 'UNDO'}
    
    attribute_name: StringProperty()
    attribute_type: StringProperty()
    
    def execute(self, context):
        space = context.space_data
        if not space or space.type != 'NODE_EDITOR' or space.tree_type != 'GeometryNodeTree':
            self.report({'ERROR'}, "Active area is not a Geometry Nodes editor")
            return {'CANCELLED'}
            
        node_tree = space.node_tree
        if not node_tree:
            self.report({'ERROR'}, "No active Geometry Nodes tree found")
            return {'CANCELLED'}
            
        # Create a Named Attribute node (GeometryNodeInputNamedAttribute)
        node = node_tree.nodes.new(type="GeometryNodeInputNamedAttribute")
        
        # Position node at the editor cursor location
        if hasattr(space, "cursor_location"):
            node.location = space.cursor_location
        else:
            node.location = (0, 0)
            
        # Set attribute name
        if "Name" in node.inputs:
            node.inputs["Name"].default_value = self.attribute_name
        elif len(node.inputs) > 0:
            node.inputs[0].default_value = self.attribute_name
            
        # Set data type
        if hasattr(node, "data_type"):
            try:
                node.data_type = self.attribute_type
            except Exception as e:
                self.report({'WARNING'}, f"Could not set data type: {e}")
                
        # Select and make the new node active
        node_tree.nodes.active = node
        node.select = True
        for n in node_tree.nodes:
            if n != node:
                n.select = False
                
        return {'FINISHED'}

# =========================================================================
# Menus
# =========================================================================

class B3D_MT_named_attributes_menu(Menu):
    """Menu that lists all attributes on the active object"""
    bl_label = "命名属性"
    bl_idname = "B3D_MT_named_attributes_menu"
    
    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        
        if not obj or not hasattr(obj, "data") or not hasattr(obj.data, "attributes"):
            layout.label(text="No active object with attributes", icon='ERROR')
            return
            
        attributes = obj.data.attributes
        if len(attributes) == 0:
            layout.label(text="No attributes found on object", icon='INFO')
            return
            
        for attr in attributes:
            # Skip built-in/internal attributes that start with a dot
            if attr.name.startswith('.'):
                continue
                
            icon = get_domain_icon(attr.domain)
            op = layout.operator(
                "b3d.add_named_attribute_node", 
                text=f"{attr.name} ({attr.domain} - {attr.data_type})", 
                icon=icon
            )
            op.attribute_name = attr.name
            op.attribute_type = attr.data_type

# =========================================================================
# Registration
# =========================================================================

def draw_menu(self, context):
    # Only draw the menu in the Geometry Node editor
    if context.space_data.tree_type == 'GeometryNodeTree':
        layout = self.layout
        layout.menu("B3D_MT_named_attributes_menu", text="命名属性")

def register():
    bpy.types.NODE_MT_editor_menus.append(draw_menu)

def unregister():
    bpy.types.NODE_MT_editor_menus.remove(draw_menu)
