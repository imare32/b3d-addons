import json
import traceback
from pathlib import Path

import bpy


class NODE_OT_my_arrange_all_nodes(bpy.types.Operator):
    """Arrange all nodes in the active node tree"""

    bl_idname = "node.my_arrange_all_nodes"
    bl_label = "Arrange All Nodes"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        space = getattr(context, "space_data", None)
        return (
            space is not None
            and space.type == "NODE_EDITOR"
            and space.edit_tree is not None
        )

    def execute(self, context):
        try:
            return self._arrange(context)
        except Exception as e:
            traceback.print_exc()
            self.report({"ERROR"}, f"Arrange failed: {type(e).__name__}: {e}")
            return {"CANCELLED"}

    def _get_tree(self, context):
        """Return the node tree to arrange, reporting why it cannot be used."""
        space = context.space_data
        if space is None or space.type != "NODE_EDITOR":
            self.report({"ERROR"}, "Run this operator from a Node Editor")
            return None

        ntree = space.edit_tree
        if ntree is None:
            self.report({"WARNING"}, "No active node tree found")
            return None

        # Linked or read only data cannot be rearranged.
        if getattr(ntree, "library", None) is not None or getattr(
            ntree, "is_library_indirect", False
        ):
            self.report(
                {"ERROR"},
                f"Node tree '{ntree.name}' is linked from another blend file, "
                f"make it local before arranging",
            )
            return None
        if getattr(ntree, "is_editable", True) is False:
            self.report({"ERROR"}, f"Node tree '{ntree.name}' is not editable")
            return None

        return ntree

    def _restore_selection(self, ntree, original_selection):
        """Restore the saved selection state, node by node, never raises."""
        failed = []
        for name, selected in original_selection.items():
            try:
                node = ntree.nodes.get(name)
                if node is None:
                    # Removed while arranging, nothing to restore.
                    continue
                node.select = selected
            except (ReferenceError, AttributeError, TypeError):
                failed.append(name)

        if failed:
            self.report(
                {"WARNING"},
                f"Could not restore the selection of {len(failed)} node(s): "
                f"{', '.join(failed[:3])}",
            )

    def _arrange(self, context):
        ntree = self._get_tree(context)
        if ntree is None:
            return {"CANCELLED"}

        nodes = list(ntree.nodes)
        if not nodes:
            self.report({"WARNING"}, f"Node tree '{ntree.name}' is empty")
            return {"CANCELLED"}

        undefined = [node.name for node in nodes if node.bl_idname == "NodeUndefined"]
        if undefined:
            self.report(
                {"WARNING"},
                f"{len(undefined)} undefined node(s) (missing addon), arrange may "
                f"fail: {', '.join(undefined[:3])}",
            )

        arrange_op = getattr(bpy.ops.node, "na_arrange_selected", None)
        if arrange_op is None:
            self.report(
                {"ERROR"},
                "Operator 'node.na_arrange_selected' is not available, "
                "enable the 'Node Arrange' addon first",
            )
            return {"CANCELLED"}

        # Save the selection state by NAME: the arrange operator may free nodes,
        # so Python references to them must not be kept across the call.
        original_selection = {node.name: node.select for node in nodes}

        try:
            for node in nodes:
                node.select = True

            result = arrange_op()
            if result and "FINISHED" not in result:
                self.report(
                    {"WARNING"},
                    f"Arrange operator returned {set(result)} for {len(nodes)} node(s)",
                )
        except RuntimeError as e:
            self.report({"ERROR"}, f"Arrange operator failed: {e}")
            return {"CANCELLED"}
        finally:
            self._restore_selection(ntree, original_selection)

        return {"FINISHED"}


def load_rules():
    """Load rules from JSON file"""
    rules_path = Path(__file__).parent / "rules.json"
    try:
        with open(rules_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading rules from {rules_path}: {e}")
        return {}


rules = load_rules()


def execute_rules(node_group: bpy.types.NodeTree):
    """Apply rules to nodes"""
    for node in node_group.nodes:
        node.hide = False
        rule = rules.setdefault(node.bl_idname, {})
        if rule.get("hide_unlinked_input_sockets"):
            for socket in node.inputs:
                if socket.enabled and not socket.is_linked:
                    socket.hide = True
        if rule.get("hide_unlinked_output_sockets"):
            for socket in node.outputs:
                if socket.enabled and not socket.is_linked:
                    socket.hide = True
        if rule.get("hide_options"):
            node.show_options = False
        if rule.get("hide_default_vectors"):
            for socket in node.inputs:
                if (
                    socket.enabled
                    and not socket.is_linked
                    and socket.type in ["VECTOR", "ROTATION"]
                ):
                    names_zero = ["Vector", "Location", "Translation", "Rotation"]
                    if socket.name in names_zero and tuple(socket.default_value) == (
                        0,
                        0,
                        0,
                    ):
                        socket.hide = True
                    if socket.name in [
                        "Scale",
                    ] and tuple(socket.default_value) == (1, 1, 1):
                        socket.hide = True


def dispatch_group_input_node(node_group: bpy.types.NodeTree):
    for node in list(node_group.nodes):
        nodei: bpy.types.Node | None = None
        for socketi in node.inputs:
            if not socketi.is_linked:
                continue
            link: bpy.types.NodeLink
            for link in socketi.links:
                if link.from_node.bl_idname == "NodeGroupInput":
                    if (
                        len(
                            {l.to_node for o in link.from_node.outputs for l in o.links}
                        )
                        == 1
                    ):
                        continue
                    if nodei is None:
                        nodei = node_group.nodes.new("NodeGroupInput")
                        nodei.select = False
                    for socketo in nodei.outputs:
                        if socketo.identifier == link.from_socket.identifier:
                            node_group.links.remove(link)
                            node_group.links.new(socketo, socketi)
                            break

    for node in list(node_group.nodes):
        if node.bl_idname == "NodeGroupInput":
            flag = False
            for output_socket in node.outputs:
                if not output_socket.is_linked:
                    output_socket.hide = True
                else:
                    flag = True
            if not flag:
                node_group.nodes.remove(node)


def dispatch_feed_input_node(node_group: bpy.types.NodeTree):
    bl_idnames = [
        "GeometryNodeInputIndex",
        "GeometryNodeInputPosition",
        "GeometryNodeInputMeshFaceArea",
        "ShaderNodeNewGeometry",
    ]
    for node in list(node_group.nodes):
        for socketi in node.inputs:
            if not socketi.is_linked:
                continue
            link: bpy.types.NodeLink = socketi.links[0]
            from_node = link.from_node
            if from_node.bl_idname in bl_idnames:
                if len({l.to_node for o in from_node.outputs for l in o.links}) <= 1:
                    continue
                feed_node = node_group.nodes.new(from_node.bl_idname)
                feed_node.select = False
                feed_node.location = from_node.location
                for socketo in feed_node.outputs:
                    if socketo.identifier == link.from_socket.identifier:
                        node_group.links.new(socketo, socketi)
                        break


def remove_nodes_without_links(node_group: bpy.types.NodeTree):
    linked_nodes = set()
    for link in node_group.links:
        linked_nodes.add(link.from_node)
        linked_nodes.add(link.to_node)

    unlinked_nodes = set(node_group.nodes) - linked_nodes
    for node in unlinked_nodes:
        node_group.nodes.remove(node)


def reset_node_width(node_group: bpy.types.NodeTree):
    for node in node_group.nodes:
        node.select = False
        if node.bl_idname in ["ShaderNodeMapping", "ShaderNodeGroup", "NodeGroupInput"]:
            node.width = 140
        if node.type == "CUSTOM" and node.width != node.bl_width_default:
            node.width = node.bl_width_default


def remove_reroute_nodes(node_group: bpy.types.NodeTree):
    """Remove all reroute nodes and keep the links"""
    for node in list(node_group.nodes):
        if node.type != "REROUTE":
            continue

        socketi, socketo = node.inputs[0], node.outputs[0]

        if len(socketi.links) > 0:
            linki = socketi.links[0]

            for linko in socketo.links:
                node_group.links.new(linki.from_socket, linko.to_socket)

        node_group.nodes.remove(node)


def replace_deprecated_nodes(node_group: bpy.types.NodeTree):
    """Replace deprecated nodes with new nodes"""
    for node in list(node_group.nodes):
        if node.bl_idname == "FunctionNodeAlignEulerToVector":
            new_node = node_group.nodes.new("FunctionNodeAlignRotationToVector")
            new_node.select = node.select
            new_node.location = node.location
            options = ["axis", "pivot_axis"]
            for option in options:
                setattr(new_node, option, getattr(node, option))
            for i, socketi in enumerate(node.inputs):
                new_node.inputs[i].default_value = socketi.default_value
                if socketi.is_linked:
                    link = socketi.links[0]
                    node_group.links.new(link.from_socket, new_node.inputs[i])
            for i, socketo in enumerate(node.outputs):
                for link in socketo.links:
                    node_group.links.new(new_node.outputs[i], link.to_socket)
            node_group.nodes.remove(node)


def remove_frame_nodes(node_group: bpy.types.NodeTree):
    """Remove all frame nodes"""
    for node in list(node_group.nodes):
        if node.type == "FRAME":
            node_group.nodes.remove(node)


class OptimizeNodeTree(bpy.types.Operator):
    """Optimize node tree"""

    bl_idname = "node.b3d_optimize_node_tree"
    bl_label = "Optimize Node Tree"
    bl_options = {"REGISTER", "UNDO"}

    to_remove_frame_nodes: bpy.props.BoolProperty(default=False)
    to_remove_orphan_nodes: bpy.props.BoolProperty(default=False)

    @classmethod
    def poll(cls, context):
        space = context.space_data
        return (
            space is not None
            and space.type == "NODE_EDITOR"
            and space.edit_tree is not None
        )

    def execute(self, context):
        space_data = context.space_data
        node_group = space_data.edit_tree
        if self.to_remove_frame_nodes:
            remove_frame_nodes(node_group)
        remove_reroute_nodes(node_group)
        replace_deprecated_nodes(node_group)
        dispatch_group_input_node(node_group)
        dispatch_feed_input_node(node_group)
        execute_rules(node_group)
        reset_node_width(node_group)
        if self.to_remove_orphan_nodes:
            remove_nodes_without_links(node_group)
        return {"FINISHED"}


addon_keymaps = []


def register():
    # Register shortcuts to Node Editor
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Node Editor", space_type="NODE_EDITOR")

        # Alt + Shift + F -> my_arrange_all_nodes
        kmi = km.keymap_items.new(
            NODE_OT_my_arrange_all_nodes.bl_idname,
            type="F",
            value="PRESS",
            alt=True,
            shift=True,
        )
        addon_keymaps.append((km, kmi))

        # Alt + Shift + Q -> b3d_optimize_node_tree
        kmi = km.keymap_items.new(
            OptimizeNodeTree.bl_idname,
            type="Q",
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
            print("Error: Failed to unregister keymap item:", kmi.name)
    addon_keymaps.clear()
