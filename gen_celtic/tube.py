"""几何后端：把「点 + 边」的带子网格喂进几何节点，长出实体绳管。

算法层（``knot``）只算出一堆折线（每个交点一个点、几点一条带），这里负责三件事：

1. **节点组** —— 一个 ``Celtic Knot Tube`` 几何节点组：``Mesh to Curve``（只吃边、
   断开处各自成条）→ ``Curve Spline Type`` 转 Bezier + 自动手柄（折线变圆滑绳路）
   → ``Set Spline Resolution``（绳路采样密度，``Spline Resolution`` 输入）→
   ``Curve to Mesh`` 用圆环截面挤出（``Resolution`` / ``Radius`` 两个输入）→
   ``Set Shade Smooth`` → ``Set Material``。组只建一次，之后复用。
2. **对象** —— 带子网格写进 ``Celtic Knot`` 数据块，挂上上面的修改器。改抬升 = 重写
   点坐标；改半径 / 分段 = 只改修改器输入，不碰网格（所以拖滑块是实时的）。
3. **材质** —— 一个 Principled 材质，基色进的是**线性**值（面板属性是
   ``COLOR_GAMMA``，存的就是线性）。

**Blender 5.x 注意**：修改器上的节点组输入要走
``modifier.properties.inputs.<identifier>.value``（identifier 是 ``Socket_8`` 这种，
名字得回节点组接口里查，见 :func:`set_mod_input`）；老的 ``modifier[identifier]``
写法在 5.x 上直接抛 "id properties not supported"（修改器不是 ID）。而且**写它不会
自动标记对象重求值** —— 改完得自己 ``obj.update_tag()``（见 :func:`_tag`），
不然拖滑块时视口停在旧几何上。
"""

from __future__ import annotations

import bpy

__all__ = [
    "GROUP_NAME",
    "MARKER",
    "MATERIAL_NAME",
    "MODIFIER_NAME",
    "OBJECT_NAME",
    "SPLINE_RESOLUTION",
    "build_object",
    "ensure_modifier",
    "find_object",
    "get_mod_input",
    "material",
    "remove_object",
    "set_geometry_material",
    "set_mod_input",
    "set_radius",
    "set_resolution",
    "set_spline_resolution",
    "stored_mean",
    "tube_group",
    "write_strands",
]

#: 生成对象的名字（固定名，方便找回来）。
OBJECT_NAME = "Celtic Knot"
#: 生成对象上的标记：有它就是本插件生成的（``find_object`` 据此找对象）。
MARKER = "gen_celtic"
#: 对象上的修改器名。
MODIFIER_NAME = "Tube"
#: 几何节点组名（只建一次，之后所有生成对象共用）。
GROUP_NAME = "Celtic Knot Tube"
#: 材质名。
MATERIAL_NAME = "Celtic Cord"

#: 节点组版本；不匹配就地重搭（老文件里的组可能缺输入，直接复用会静默失效）。
GROUP_VERSION = 2
_VERSION_KEY = "gen_celtic_api"
#: 绳路采样密度（Bezier 每段插多少点）的默认值；输出面数 ≈ 源面数 × 4 × 圆周分段 × 这个值。
SPLINE_RESOLUTION = 12
#: 存到生成对象上的「平均边长」：半径比例要靠它换算成世界单位。
MEAN_KEY = "gen_celtic_mean"


def _socket(node, *names):
    """按名字取输入插口（跨版本按名字找，不认死序号）。"""
    for name in names:
        socket = node.inputs.get(name)
        if socket is not None:
            return socket
    raise KeyError(f"{node.bl_idname} missing input {names[0]!r}")


def _interface_identifier(modifier, name):
    """节点组接口里叫 ``name`` 的输入插口的 identifier（``Socket_8`` 这种）。

    修改器只认 identifier，不认面板上显示的插口名，所以每次都得回去查一遍。
    """
    group = modifier.node_group
    if group is None:
        return None
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
    """5.x：按插口名取修改器上的输入对象（有 ``value`` 的那个）；拿不到返回 ``None``。

    ``inputs`` 上**所有**插口都有同名属性，但纯几何插口（``Geometry``）没有
    ``value`` —— 所以不能只查属性在不在，得连 ``value`` 一起查。
    """
    inputs = _mod_inputs(modifier)
    if inputs is None:
        return None
    identifier = _interface_identifier(modifier, name)
    socket = getattr(inputs, identifier, None) if identifier is not None else None
    if socket is not None and hasattr(socket, "value"):
        return socket
    return None


def set_mod_input(modifier, name, value) -> None:
    """按插口名写节点组修改器的输入（``Radius`` / ``Resolution``）。

    Blender 5.x 把接口输入挪到了 ``modifier.properties.inputs.<identifier>.value``；
    老的 ``modifier[identifier]`` 在 5.x 上直接抛 "id properties not supported"
    （修改器不是 ID），所以优先走新接口，老版本退回下标写法。
    """
    socket = _input_socket(modifier, name)
    if socket is not None:
        socket.value = value
        return
    if _mod_inputs(modifier) is not None:  # 5.x：接口在、插口没值（几何插口）
        raise ValueError(f"input socket {name!r} has no value to write")
    identifier = _interface_identifier(modifier, name)
    if identifier is None:
        raise ValueError(f"node group has no input socket named {name!r}")
    modifier[identifier] = value  # 4.x：# 下标就是 identifier


def get_mod_input(modifier, name, default=None):
    """读节点组修改器的输入；没有这个插口（或几何插口没有值）就返回 ``default``。"""
    socket = _input_socket(modifier, name)
    if socket is not None:
        return socket.value
    if _mod_inputs(modifier) is not None:
        return default  # 5.x：新接口说了算，读不到就是没值，别去碰 IDProperties
    identifier = _interface_identifier(modifier, name)
    if identifier is None:
        return default
    return modifier.get(identifier, default)  # 4.x：# 老版本才走 IDProperties


def tube_group():
    """取（必要时建）绳管节点组。"""
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
    # 接口插口只认基础类型，DISTANCE 是拿 subtype 标的（不是 NodeSocketFloatDistance）
    radius = group.interface.new_socket(
        name="Radius", in_out="INPUT", socket_type="NodeSocketFloat"
    )
    radius.subtype = "DISTANCE"
    radius.default_value = 0.05
    resolution = group.interface.new_socket(
        name="Resolution", in_out="INPUT", socket_type="NodeSocketInt"
    )
    resolution.default_value = 8
    spline_res = group.interface.new_socket(
        name="Spline Resolution", in_out="INPUT", socket_type="NodeSocketInt"
    )
    spline_res.default_value = SPLINE_RESOLUTION

    nodes, links = group.nodes, group.links
    source = nodes.new("NodeGroupInput")
    output = nodes.new("NodeGroupOutput")

    # 边 → 曲线：相连的边走成一条样条，闭合带子是闭合的环
    to_curve = nodes.new("GeometryNodeMeshToCurve")
    # 折线 → Bezier（自动手柄）：阶梯状的折线走成真实绳子的圆滑路
    spline = nodes.new("GeometryNodeCurveSplineType")
    spline.spline_type = "BEZIER"
    handles = nodes.new("GeometryNodeCurveSetHandles")
    handles.handle_type = "AUTO"
    handles.mode = {"LEFT", "RIGHT"}
    # 绳路采样密度：Bezier 每段插多少点。默认 12（Blender 自己的默认值）——
    # 输出面数 ≈ 源面数 × 4 × 圆周分段 × 这个值，源网格很密时在修改器面板里调小它
    sampling = nodes.new("GeometryNodeSetSplineResolution")
    # 圆环截面 → 挤成管
    circle = nodes.new("GeometryNodeCurvePrimitiveCircle")
    circle.mode = "RADIUS"
    to_mesh = nodes.new("GeometryNodeCurveToMesh")
    smooth = nodes.new("GeometryNodeSetShadeSmooth")
    # 材质必须在节点组里定：带子网格只有边没有面，材质槽传不到求值后的几何上，
    # 少了这一步渲染出来是默认白（材质属性挂在没面的网格上是空转）
    assign = nodes.new("GeometryNodeSetMaterial")

    links.new(source.outputs["Geometry"], to_curve.inputs["Mesh"])
    links.new(to_curve.outputs["Curve"], spline.inputs["Curve"])
    links.new(spline.outputs["Curve"], handles.inputs["Curve"])
    links.new(handles.outputs["Curve"], sampling.inputs["Curve"])
    links.new(sampling.outputs["Curve"], to_mesh.inputs["Curve"])
    links.new(circle.outputs["Curve"], to_mesh.inputs["Profile Curve"])
    links.new(to_mesh.outputs["Mesh"], smooth.inputs["Mesh"])
    links.new(smooth.outputs["Mesh"], assign.inputs["Geometry"])
    links.new(assign.outputs["Geometry"], output.inputs["Geometry"])

    links.new(source.outputs["Resolution"], _socket(circle, "Resolution"))
    links.new(source.outputs["Radius"], _socket(circle, "Radius"))
    links.new(source.outputs["Spline Resolution"], _socket(sampling, "Resolution"))
    # 断头处长个盖：边界上的带子末端是封口的
    _socket(to_mesh, "Fill Caps").default_value = True
    _socket(smooth, "Shade Smooth").default_value = True

    group[_VERSION_KEY] = GROUP_VERSION
    return group


def find_object():
    """按标记找回生成对象（没有就返回 ``None``）。"""
    for obj in bpy.data.objects:
        if obj.type == "MESH" and MARKER in obj:
            return obj
    return None


def remove_object() -> bool:
    """删掉生成对象（连带它独占的数据块），没找到返回 ``False``。"""
    obj = find_object()
    if obj is None:
        return False
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)
    return True


def set_geometry_material(group, mat) -> bool:
    """把材质写进节点组的 ``Set Material``（几何输出自带材质才会上色）。

    5.x 里材质是输入插口（``NodeSocketMaterial``），4.x 里是节点属性，两条都写。
    """
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


def ensure_modifier(
    obj,
    radius: float,
    resolution: int,
    spline_resolution: int = SPLINE_RESOLUTION,
):
    """给对象挂上（或修好）绳管修改器，返回修改器。

    三个接口输入**一律照参数重写**，一个都不省：节点组就地重搭后新增的插口在修改器上
    会停在 0（拿不到接口自己的默认值 —— 严格的说不只是 0，重建时改过的默认值同样不继承），
    绳路细分是 0 的话管子就退化成折线扫出来的多面体了。
    """
    modifier = obj.modifiers.get(MODIFIER_NAME)
    if modifier is None or modifier.type != "NODES":
        if modifier is not None:
            obj.modifiers.remove(modifier)
        modifier = obj.modifiers.new(MODIFIER_NAME, "NODES")
    group = tube_group()
    if modifier.node_group is not group:
        modifier.node_group = group
    set_mod_input(modifier, "Radius", float(radius))
    set_mod_input(modifier, "Resolution", int(resolution))
    set_mod_input(modifier, "Spline Resolution", int(spline_resolution))
    return modifier


def _tag(obj) -> None:
    """改完修改器输入得给对象打 tag，视口才会重求值。

    接口输入存在节点组数据上，写它**不会**自动标脏：``view_layer.update()`` 与
    ``node_group.update_tag()`` 都测过、都不顶用（5.2 实测），只有 ``obj.update_tag()``
    能把修改器重新算一遍 —— 少了这一步，拖半径 / 分段滑块视口就停在旧几何上。
    打 tag 不会引发重编：实时处理器只认源网格的指纹，这里一个坐标都没动。
    """
    obj.update_tag()


def set_radius(obj, radius: float) -> None:
    """只改管半径（修改器输入），不碰带子网格。"""
    modifier = obj.modifiers.get(MODIFIER_NAME)
    if modifier is not None:
        set_mod_input(modifier, "Radius", float(radius))
        _tag(obj)


def set_resolution(obj, resolution: int) -> None:
    """只改圆周分段（修改器输入），不碰带子网格。"""
    modifier = obj.modifiers.get(MODIFIER_NAME)
    if modifier is not None:
        set_mod_input(modifier, "Resolution", int(resolution))
        _tag(obj)


def set_spline_resolution(obj, spline_resolution: int) -> None:
    """只改绳路采样密度（修改器输入），不碰带子网格。"""
    modifier = obj.modifiers.get(MODIFIER_NAME)
    if modifier is not None:
        set_mod_input(modifier, "Spline Resolution", int(spline_resolution))
        _tag(obj)


def stored_mean(obj) -> float:
    """生成时算的**平均边长**（半径比例靠它换算成世界单位）。"""
    return float(obj.get(MEAN_KEY, 0.0))


def material(color, name: str = MATERIAL_NAME):
    """取（必要时建 / 改色）绳管材质；``color`` 是**线性** RGB。"""
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    tree = mat.node_tree
    bsdf = tree.nodes.get("Principled BSDF") if tree is not None else None
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (color[0], color[1], color[2], 1.0)
        bsdf.inputs["Roughness"].default_value = 0.55
        # 一丝金属度：绳管的高光不至于死平，但也不至于变成塑料
        bsdf.inputs["Metallic"].default_value = 0.15
    mat.diffuse_color = (color[0], color[1], color[2], 1.0)
    return mat


def write_strands(mesh, verts, edges) -> None:
    """把带子折线写进网格数据块（原地重建，数据块与材质槽都留着）。"""
    mesh.clear_geometry()
    mesh.from_pydata(verts, edges, [])
    mesh.update()


def build_object(
    verts,
    edges,
    radius: float,
    resolution: int,
    spline_resolution: int = SPLINE_RESOLUTION,
    collection=None,
    color=(0.8, 0.8, 0.8),
):
    """写（或更新）生成对象：带子网格 + 绳管修改器 + 材质。

    对象已经存在就地更新：数据块不变，材质槽不变，改的只是点坐标与修改器输入 ——
    编辑模式里反复更新也不会把用户挪过的对象位置、加过的材质槽抖掉。
    """
    obj = find_object()
    if obj is None:
        mesh = bpy.data.meshes.new(OBJECT_NAME)
        obj = bpy.data.objects.new(OBJECT_NAME, mesh)
        (collection or bpy.context.collection).objects.link(obj)
    write_strands(obj.data, verts, edges)
    obj[MARKER] = True
    modifier = ensure_modifier(obj, radius, resolution, spline_resolution)

    mat = material(color)
    # 几何那头也要认这个材质（带子网格没有面，材质槽传不到求值后的几何上）
    set_geometry_material(modifier.node_group, mat)
    if len(obj.data.materials):
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)
    return obj