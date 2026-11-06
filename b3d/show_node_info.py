import bpy


def show_node_name(node):
    if node.type == "GROUP":
        if node.node_tree is not None:
            return node.node_tree.name
    if node.type in ["MATH", "VECT_MATH", "COMPARE"]:
        return node.bl_label + " → " + node.operation.replace("_", " ").title()
    if node.name and node.label and node.name != node.label:
        return f"{node.name} ({node.label})"
    return node.name or node.label or "Unknown"


def show_socket_name(socket):
    name = socket.name
    if socket.identifier != socket.name:
        name = f"{socket.name} ({socket.identifier})"
    return name


def show_node_info(self: bpy.types.Panel, context: bpy.types.Context):
    node_active = context.active_node
    if node_active is None or not node_active.select:
        return

    layout = self.layout.box()
    nodetree = context.space_data.node_tree

    row = layout.row()
    row.alignment = "LEFT"
    op = row.operator(
        "b3d.copy_text",
        text=show_node_name(node_active),
        icon=nodetree.bl_icon,
        emboss=False,
    )
    op.text = node_active.bl_idname
    row = layout.row(align=True)
    row.use_property_split = False
    row.prop(node_active, "location", text="X", index=0)
    row.prop(node_active, "location", text="Y", index=1)
    row = layout.row(align=True)
    row.use_property_split = False
    row.prop(node_active, "width", text="W")
    row.prop(node_active, "dimensions", text="H", index=1)

    if node_active.inputs:
        socket_count = len(node_active.inputs)
        index_length = len(str(socket_count))
        col = layout.column(align=True)
        box = col.box()
        box.scale_y = 0.75
        box.label(text="Input Sockets:")
        for i, input in enumerate(node_active.inputs):
            row = col.row(align=True)
            box = row.box()
            box.scale_y = 0.5
            row = box.row(align=True)
            row.label(text=f"{i:>0{index_length}}. {show_socket_name(input)}")
            icon = "HIDE_ON" if not input.enabled or input.hide else "HIDE_OFF"
            row.prop(input, "hide", icon_only=True, icon=icon, emboss=False)

    if node_active.outputs:
        socket_count = max(len(node_active.inputs), len(node_active.outputs))
        index_length = len(str(socket_count))
        col = layout.column(align=True)
        box = col.box()
        box.scale_y = 0.75
        box.label(text="Output Sockets:")
        for i, output in enumerate(node_active.outputs):
            row = col.row(align=True)
            box = row.box()
            box.scale_y = 0.5
            row = box.row(align=True)
            row.label(text=f"{i:>0{index_length}}. {show_socket_name(output)}")
            icon = "HIDE_ON" if not output.enabled or output.hide else "HIDE_OFF"
            row.prop(output, "hide", icon_only=True, icon=icon, emboss=False)


def register():
    bpy.types.NODE_PT_active_node_generic.append(show_node_info)


def unregister():
    bpy.types.NODE_PT_active_node_generic.remove(show_node_info)
