"""可选的几何后端：把 GP 笔划扫成**实体管**（几何节点 + 材质）。

节点组 ``Torus Knot Tube``（只建一次，所有生成对象共用）::

    Group Input ─► Grease Pencil to Curves ─► Curve to Mesh ─► Set Shade Smooth ─► Set Material ─► Group Output
                                                     ▲
                        Curve Circle（截面）─────────┘

实测（5.2.2）记下的三条：

* ``Grease Pencil to Curves`` 的 **``Layers as Instances`` 必须关掉**：默认 ``True`` 时
  它把每层吐成**实例**，后面的 ``Curve to Mesh`` 拿到实例什么也产出不了（渲染出来是空的）。
* 管径 = **截面圆环的半径**。GP 笔划的 ``radius``（线宽）不会带到曲线上，
  ``Set Curve Radius`` 也改不动管径 —— 所以半径直接驱动 ``Curve Circle``。
* 笔划闭合（``stroke.cyclic``）时扫出来就是闭合的管；``Fill Caps`` 仍开着，万一有断头的
  股，端口是封的、不会是破面。

修改器上的接口输入（``Radius`` / ``Resolution``）改的是节点组数据上的值：Blender 5.x
得走 ``modifier.properties.inputs.<identifier>.value``（identifier 是 ``Socket_8`` 这种，
名字要回接口里查，见 :func:`set_mod_input`），并且**写完不会自动标脏** —— 得自己
``obj.update_tag()``（见 :func:`_tag`），不然拖滑块时视口停在旧几何上。老的
``modifier[identifier]`` 写法在 5.x 上直接抛 "id properties not supported"（修改器不是 ID）。
"""

from __future__ import annotations

import bpy

__all__ = [
    "GROUP_NAME",
    "MATERIAL_NAME",
    "MODIFIER_NAME",
    "build_tube_group",
    "ensure_modifier",
    "material",
    "modifier",
    "remove_modifier",
    "set_enabled",
    "set_geometry_material",
    "set_mod_input",
    "set_radius",
    "set_resolution",
]

#: 对象上的修改器名。
MODIFIER_NAME = "Tube"
#: 几何节点组名。
GROUP_NAME = "Torus Knot Tube"
#: 材质名。
MATERIAL_NAME = "Torus Knot Cord"

#: 节点组版本；不匹配就地重搭（老文件里的组可能缺输入，直接复用会静默失效）。
#: 1 = GP 笔划 → 曲线 → 扫管。
GROUP_VERSION = 1
_VERSION_KEY = "gen_torus_knot_api"


def _socket(node, *names):
    """按名字取输入插口（跨版本按名字找，不认死序号）。"""
    for name in names:
        socket = node.inputs.get(name)
        if socket is not None:
            return socket
    raise KeyError(f"{node.bl_idname} missing input {names[0]!r}")


def _new_node(nodes, bl_idname, location):
    """建节点并摆好位置（不指定位置的话所有节点都叠在原点）。"""
    node = nodes.new(bl_idname)
    node.location = location
    return node


def _interface_identifier(group, name):
    """节点组接口里叫 ``name`` 的输入插口的 identifier（``Socket_8`` 这种）。"""
    for item in group.interface.items_tree:
        if (
            item.item_type == "SOCKET"
            and item.in_out == "INPUT"
            and item.name == name
        ):
            return item.identifier
    return None


def _mod_inputs(modifier):
    """5.x 的接口输入集合（``modifier.properties.inputs``）；老版本没有，返回 ``None``。"""
    return getattr(getattr(modifier, "properties", None), "inputs", None)


def _input_socket(modifier, name):
    """5.x：按插口名取修改器上的输入对象（有 ``value`` 的那个）。

    纯几何插口（``Geometry``）没有 ``value``，所以不能只查属性在不在。
    """
    inputs = _mod_inputs(modifier)
    if inputs is None:
        return None
    identifier = _interface_identifier(modifier.node_group, name)
    socket = getattr(inputs, identifier, None) if identifier is not None else None
    if socket is not None and hasattr(socket, "value"):
        return socket
    return None


def set_mod_input(modifier, name, value) -> None:
    """按插口名写节点组修改器的输入（``Radius`` / ``Resolution``）。"""
    socket = _input_socket(modifier, name)
    if socket is not None:
        socket.value = value
        return
    group = modifier.node_group
    if _mod_inputs(modifier) is not None:  # 5.x：接口在、插口没值（几何插口）
        raise ValueError(f"input socket {name!r} has no value to write")
    identifier = _interface_identifier(group, name) if group is not None else None
    if identifier is None:
        raise ValueError(f"node group has no input socket named {name!r}")
    modifier[identifier] = value  # 4.x：下标就是 identifier


def build_tube_group():
    """取（必要时建）扫管节点组。"""
    group = bpy.data.node_groups.get(GROUP_NAME)
    if group is not None and group.get(_VERSION_KEY) == GROUP_VERSION:
        return group

    if group is None:
        group = bpy.data.node_groups.new(GROUP_NAME, "GeometryNodeTree")
    else:
        # 旧版本：原地重搭（对象上的修改器引用因此不会断）
        for node in list(group.nodes):
            group.nodes.remove(node)
        interface = group.interface
        if hasattr(interface, "clear"):
            interface.clear()
        else:  # pragma: no cover - 兼容老接口
            for item in list(interface.items_tree):
                interface.remove(item)

    group.is_modifier = True
    group.interface.new_socket(
        name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry"
    )
    group.interface.new_socket(
        name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry"
    )
    # 接口插口只认基础类型，DISTANCE 是拿 subtype 标的
    radius = group.interface.new_socket(
        name="Radius", in_out="INPUT", socket_type="NodeSocketFloat"
    )
    radius.subtype = "DISTANCE"
    radius.default_value = 0.04
    resolution = group.interface.new_socket(
        name="Resolution", in_out="INPUT", socket_type="NodeSocketInt"
    )
    resolution.default_value = 8

    nodes, links = group.nodes, group.links
    source = _new_node(nodes, "NodeGroupInput", (-447.0, 0.0))
    output = _new_node(nodes, "NodeGroupOutput", (523.0, 0.0))

    # 笔划 → 曲线：**必须**关掉 Layers as Instances，否则输出的是实例，扫不出东西
    to_curves = _new_node(nodes, "GeometryNodeGreasePencilToCurves", (-257.0, 71.0))
    circle = _new_node(nodes, "GeometryNodeCurvePrimitiveCircle", (-247.0, -71.0))
    circle.mode = "RADIUS"
    to_mesh = _new_node(nodes, "GeometryNodeCurveToMesh", (-47.0, 0.0))
    smooth = _new_node(nodes, "GeometryNodeSetShadeSmooth", (143.0, 0.0))
    # 材质在实际输出上定一次：GP 数据块自己没有材质（颜色走 vertex_color），
    # 扫成管之后要上色就靠这一处
    assign = _new_node(nodes, "GeometryNodeSetMaterial", (333.0, 0.0))

    links.new(source.outputs["Geometry"], to_curves.inputs["Grease Pencil"])
    links.new(to_curves.outputs["Curves"], to_mesh.inputs["Curve"])
    links.new(circle.outputs["Curve"], to_mesh.inputs["Profile Curve"])
    links.new(to_mesh.outputs["Mesh"], smooth.inputs["Mesh"])
    links.new(smooth.outputs["Mesh"], assign.inputs["Geometry"])
    links.new(assign.outputs["Geometry"], output.inputs["Geometry"])

    links.new(source.outputs["Resolution"], _socket(circle, "Resolution"))
    links.new(source.outputs["Radius"], _socket(circle, "Radius"))

    _socket(to_curves, "Layers as Instances").default_value = False
    _socket(to_mesh, "Fill Caps").default_value = True
    _socket(smooth, "Shade Smooth").default_value = True

    group[_VERSION_KEY] = GROUP_VERSION
    return group


def modifier(obj):
    """取对象上的扫管修改器（没有就返回 ``None``）。"""
    found = obj.modifiers.get(MODIFIER_NAME)
    if found is None or found.type != "NODES":
        return None
    return found


def ensure_modifier(obj, radius: float, resolution: int):
    """给对象挂上（或修好）扫管修改器，返回修改器。

    两个接口输入**一律照参数重写**，一个都不省：节点组就地重搭后新增的插口在修改器上
    会停在 0，管径是 0 的话视口里什么都看不见。
    """
    found = obj.modifiers.get(MODIFIER_NAME)
    if found is None or found.type != "NODES":
        if found is not None:
            obj.modifiers.remove(found)
        found = obj.modifiers.new(MODIFIER_NAME, "NODES")
    group = build_tube_group()
    if found.node_group is not group:
        found.node_group = group
    set_mod_input(found, "Radius", float(radius))
    set_mod_input(found, "Resolution", int(resolution))
    return found


def remove_modifier(obj) -> bool:
    """摘掉扫管修改器（回到纯笔划）。"""
    found = modifier(obj)
    if found is None:
        return False
    obj.modifiers.remove(found)
    return True


def _tag(obj) -> None:
    """改完修改器输入得给对象打 tag，视口才会重求值（``view_layer.update()`` 不顶用）。"""
    obj.update_tag()


def set_enabled(obj, enabled: bool) -> bool:
    """开关管状实体：改的只是修改器的可见性，几何原样留着（打开就是瞬时的）。"""
    found = modifier(obj)
    if found is None:
        return False
    found.show_viewport = bool(enabled)
    found.show_render = bool(enabled)
    _tag(obj)
    return True


def set_radius(obj, radius: float) -> bool:
    """只改管半径（修改器输入），不碰笔划。"""
    found = modifier(obj)
    if found is None:
        return False
    set_mod_input(found, "Radius", float(radius))
    _tag(obj)
    return True


def set_resolution(obj, resolution: int) -> bool:
    """只改圆周分段（修改器输入），不碰笔划。"""
    found = modifier(obj)
    if found is None:
        return False
    set_mod_input(found, "Resolution", int(resolution))
    _tag(obj)
    return True


def set_geometry_material(group, mat) -> bool:
    """把材质写进节点组的 ``Set Material``（几何输出自带材质才会上色）。"""
    if group is None:
        return False
    for node in group.nodes:
        if node.bl_idname != "GeometryNodeSetMaterial":
            continue
        socket = node.inputs.get("Material")
        if socket is not None:
            socket.default_value = mat
        else:  # pragma: no cover - 兼容 4.x
            node.material = mat
        return True
    return False


def material(colour, name: str = MATERIAL_NAME):
    """取（必要时建 / 改色）管材质；``colour`` 是**线性** RGB。"""
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    tree = mat.node_tree
    bsdf = tree.nodes.get("Principled BSDF") if tree is not None else None
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (colour[0], colour[1], colour[2], 1.0)
        bsdf.inputs["Roughness"].default_value = 0.55
        # 一丝金属度：管的高光不至于死平，也不至于变成塑料
        bsdf.inputs["Metallic"].default_value = 0.15
    mat.diffuse_color = (colour[0], colour[1], colour[2], 1.0)
    return mat