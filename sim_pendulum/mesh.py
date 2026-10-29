"""网格后端：一个双摆 5 顶点 / 2 条边 + 几何节点扫成圆管（对照用）。

这个后端存在的唯一理由是**对照**：参考实现
（``.lab/double_pendulum.py``）就是把结果写进网格的顶点，所以面板给了
「输出 = 网格」这一档，方便在同一个场景里量出两条同步路径的真实开销
（面板读数里的「同步」一项，README 有实测表）。

结构：

* 顶点 **5 个 / 摆**，点序与 GP 的三笔一致：``0-1`` 下摆、``2-3`` 上摆、``4`` 扣子；
  边只有 **2 条 / 摆**（0-1、2-3）；
* 顶点 4 是**孤立顶点**：留着只为让两边写回的数据量严格相等（``5S`` 个点、同一套属性）；
  ``Mesh to Curve`` 会直接丢掉孤立顶点，所以**网格后端没有扣子**（GP 那边有）。要画出来
  得再实例化小球，那是另一套成本，不值得为"对照"这个用途做；
* ``ink_color``（FLOAT_COLOR / POINT）+ ``radius``（FLOAT / POINT）+ 六个标量 —— 与 GP
  后端的属性一一对应。颜色叫 ``ink_color`` 是为了绕开 Cycles 的标准色名坑（见
  :func:`_material`）；
* 光有边是渲染不出东西的（Blender 不画裸边），所以挂一个几何节点修改器：
  ``Mesh to Curve``（按边） → ``Curve to Mesh``（圆管，``Scale`` 吃 ``radius``） →
  ``Set Material`` —— 半径属性因此能真的变成粗细，和 GP 的逐点半径对得上；
* 材质用自发光：GP 那层是 ``use_lights = False`` 的无光照线稿，网格这边只有发光才同色。

**同步量**：``5S`` 个顶点 × (位置 3 + 颜色 4 + 半径 1 + 标量 6) = 70S 个 float32，和 GP
完全一样 —— 两条后端写的是同一份数据，差别只在对象类型（GP 的 drawing 属性 vs 网格属性），
所以那张对比表量的就是纯粹的写回路径开销。
"""

from __future__ import annotations

import bpy
import numpy as np

__all__ = [
    "MATERIAL_NAME",
    "OBJECT_NAME",
    "NODES_NAME",
    "find_object",
    "refresh",
    "remove_object",
    "ensure_object",
    "write",
]

OBJECT_NAME = "Sim Pendulum Mesh"
MATERIAL_NAME = "Sim Pendulum Mesh Ink"
NODES_NAME = "Sim Pendulum Mesh Tube"

#: 顶点数 / 边数（一个双摆一份）。顶点 4 是"扣子"，只有点、没有边：
#: ``Mesh to Curve`` 会把它当孤立顶点丢掉，所以网格后端画不出扣子（见模块说明）。
VERTS_PER_PENDULUM = 5
EDGES_PER_PENDULUM = 2

#: 圆管剖面的边数（6 边形足够，2304 个摆时面数就是 8× 这个量级）。
_PROFILE_RESOLUTION = 6

#: 节点组结构版本：改了内部连线就 +1，已存在的旧组会被原地重搭。
_GROUP_VERSION = 3
_GROUP_VERSION_KEY = "sim_pendulum_version"

_ATTR_COLOR = "ink_color"
_ATTR_RADIUS = "radius"
#: 管子用的半径存一份不撞名的（曲线点有内置 radius，同名属性会被它盖掉）。
_ATTR_INK_RADIUS = "ink_radius"


def find_object(name: str = OBJECT_NAME):
    obj = bpy.data.objects.get(name)
    if obj is None or obj.type != "MESH":
        return None
    return obj


def remove_object(name: str = OBJECT_NAME) -> bool:
    obj = bpy.data.objects.get(name)
    if obj is None:
        return False
    mesh = obj.data if obj.type == "MESH" else None
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)
    return True


def ensure_object(name: str = OBJECT_NAME, collection=None, scene=None):
    """拿网格对象，没有就建（同名但类型不对的旧对象会被替换）。

    给了 ``scene`` 会补一次"必须在目标场景里"的检查：``bpy.context.collection`` 可能还停在
    上一个场景，对象被链进别的场景就等于没建（渲染一片空白）。
    """
    obj = bpy.data.objects.get(name)
    if obj is not None and obj.type != "MESH":
        bpy.data.objects.remove(obj, do_unlink=True)
        obj = None
    if obj is None:
        mesh = bpy.data.meshes.new(name)
        obj = bpy.data.objects.new(name, mesh)
        target = collection or bpy.context.collection or bpy.context.scene.collection
        target.objects.link(obj)
    elif collection is not None and obj.name not in collection.objects:
        collection.objects.link(obj)
    if scene is not None and obj.name not in scene.objects:
        scene.collection.objects.link(obj)
    return obj


# --- 材质 / 几何节点 --------------------------------------------------------


def _material():
    """自发光墨线：颜色按名字读 ``ink_color`` 属性，无灯也所见即所得。

    两条实测结论（5.2，EEVEE + Cycles 都验过）：

    * **不能**用 ``ShaderNodeVertexColor`` + 空 ``layer_name``（本仓库其它网格插件的写法）：
      它依赖"活跃色属性"，而几何节点扫出来的求值网格上
      ``color_attributes.active_color_index == -1``，节点取不到颜色 → 整根管纯黑；
    * 颜色属性**不能**叫 ``color`` / ``vertex_color``：Cycles 会把这两个名字当成自己的
      标准顶点色名、取到空值（同样是纯黑，EEVEE 却正常 —— 只测 EEVEE 发现不了）。
      所以这里叫 ``ink_color``，材质用 ``ShaderNodeAttribute``
      （``attribute_type = 'GEOMETRY'``）按名字直取，两种渲染引擎一致。
    """
    mat = bpy.data.materials.get(MATERIAL_NAME)
    if mat is None:
        mat = bpy.data.materials.new(MATERIAL_NAME)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (320, 0)
    attribute = nodes.new("ShaderNodeAttribute")
    attribute.location = (-360, 0)
    attribute.attribute_type = "GEOMETRY"
    attribute.attribute_name = _ATTR_COLOR
    emission = nodes.new("ShaderNodeEmission")
    emission.location = (-120, 0)
    emission.inputs["Strength"].default_value = 1.0
    mat.node_tree.links.new(attribute.outputs["Color"], emission.inputs["Color"])
    mat.node_tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return mat


def _node_group():
    """``Mesh to Curve`` → ``Curve to Mesh``：把边扫成圆管，粗细吃网格的 ``radius``。

    三条 5.2 实测的硬约束（前两条都不报错，只是画面上什么都没有）：

    * ``group.is_modifier = True`` 必须置位（与 ``gen_celtic/tube.py`` 一致）；
    * ``Curve to Mesh`` 的 ``Profile Curve`` **必须接一个剖面**（这里用
      ``Curve Primitive Circle``，半径 1、6 段），空着的话输出只是那条骨架折线
      （N 个点、0 个面），渲染什么都没有 —— 不是"默认圆管"；
    * 粗细要走 ``Curve to Mesh`` 的 ``Scale`` 字段，**不要**指望曲线点半径：
      ``Set Curve Radius`` 改的点半径只对"曲线对象自身的渲染倒角"生效，
      ``Curve to Mesh`` 根本不读它（实测剖面半径 1 + 曲线半径 0.5 → 管子半径仍是 1）。
    """
    group = bpy.data.node_groups.get(NODES_NAME)
    if group is not None and group.get(_GROUP_VERSION_KEY) == _GROUP_VERSION:
        group.is_modifier = True
        return group
    if group is None:
        group = bpy.data.node_groups.new(NODES_NAME, "GeometryNodeTree")
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
    group[_GROUP_VERSION_KEY] = _GROUP_VERSION
    group.interface.new_socket(
        "Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry"
    )
    group.interface.new_socket(
        "Geometry", in_out="INPUT", socket_type="NodeSocketGeometry"
    )
    nodes = group.nodes
    links = group.links

    group_in = nodes.new("NodeGroupInput")
    group_in.location = (-620, 0)
    to_curve = nodes.new("GeometryNodeMeshToCurve")
    to_curve.location = (-420, 0)
    profile = nodes.new("GeometryNodeCurvePrimitiveCircle")
    profile.location = (-420, -220)
    profile.inputs["Radius"].default_value = 1.0
    profile.inputs["Resolution"].default_value = _PROFILE_RESOLUTION
    to_mesh = nodes.new("GeometryNodeCurveToMesh")
    to_mesh.location = (-180, 0)
    material = nodes.new("GeometryNodeSetMaterial")
    material.location = (60, 0)
    # 5.x：材质是 Set Material 的输入插口（4.x 才是节点属性 node.material）
    material.inputs["Material"].default_value = _material()
    group_out = nodes.new("NodeGroupOutput")
    group_out.location = (280, 0)

    # 逐点半径：先按名字取自网格的 ``radius``，另存一份不撞名的 ``ink_radius``
    # 跟在点上；转换后拿它喂 ``Curve to Mesh`` 的 ``Scale``（剖面半径 1 × 该值 = 管半径）。
    named_in = nodes.new("GeometryNodeInputNamedAttribute")
    named_in.location = (-620, -220)
    named_in.data_type = "FLOAT"
    named_in.inputs["Name"].default_value = _ATTR_RADIUS
    store = nodes.new("GeometryNodeStoreNamedAttribute")
    store.location = (-420, -220)
    store.data_type = "FLOAT"
    store.domain = "POINT"
    store.inputs["Name"].default_value = _ATTR_INK_RADIUS
    named_scale = nodes.new("GeometryNodeInputNamedAttribute")
    named_scale.location = (-420, -420)
    named_scale.data_type = "FLOAT"
    named_scale.inputs["Name"].default_value = _ATTR_INK_RADIUS

    links.new(group_in.outputs["Geometry"], store.inputs["Geometry"])
    links.new(named_in.outputs["Attribute"], store.inputs["Value"])
    links.new(store.outputs["Geometry"], to_curve.inputs["Mesh"])
    links.new(named_scale.outputs["Attribute"], to_mesh.inputs["Scale"])
    links.new(to_curve.outputs["Curve"], to_mesh.inputs["Curve"])
    links.new(profile.outputs["Curve"], to_mesh.inputs["Profile Curve"])
    links.new(to_mesh.outputs["Mesh"], material.inputs["Geometry"])
    links.new(material.outputs["Geometry"], group_out.inputs["Geometry"])
    return group


def _ensure_modifier(obj) -> None:
    """挂上几何节点修改器（已经挂过就只更新节点组）。"""
    group = _node_group()
    for modifier in obj.modifiers:
        if (
            modifier.type == "NODES"
            and modifier.node_group is not None
            and modifier.node_group.name == NODES_NAME
        ):
            modifier.node_group = group
            return
    modifier = obj.modifiers.new("Tube", "NODES")
    modifier.node_group = group


# --- 拓扑 / 数据 ------------------------------------------------------------


def _ensure_topology(mesh, count: int) -> bool:
    """顶点 / 边数量对不上就整块重建。返回是否重建过。"""
    verts = count * VERTS_PER_PENDULUM
    if len(mesh.vertices) == verts and len(mesh.edges) == count * EDGES_PER_PENDULUM:
        return False

    # 两根杆各一条边：0-1（下摆）、2-3（上摆）；顶点 4 是扣子，故意不连线
    base = (np.arange(count, dtype=np.int32) * VERTS_PER_PENDULUM).reshape(-1, 1)
    edges = np.column_stack([base + 0, base + 1, base + 2, base + 3]).reshape(-1, 2)

    mesh.clear_geometry()
    mesh.vertices.add(verts)
    mesh.edges.add(count * EDGES_PER_PENDULUM)
    mesh.edges.foreach_set("vertices", edges.reshape(-1))
    mesh.update()
    return True


def _ensure_attributes(mesh, verts: int) -> None:
    """颜色 / 半径属性（缺就建，并把颜色设成活跃色属性）。"""
    if _ATTR_COLOR not in mesh.attributes:
        attribute = mesh.attributes.new(_ATTR_COLOR, "FLOAT_COLOR", "POINT")
        attribute.data.foreach_set("color", np.ones(verts * 4, np.float32))
    if _ATTR_RADIUS not in mesh.attributes:
        mesh.attributes.new(_ATTR_RADIUS, "FLOAT", "POINT")
    index = mesh.color_attributes.find(_ATTR_COLOR)
    if index >= 0:
        mesh.color_attributes.active_color_index = index
        mesh.color_attributes.render_color_index = index


def _write_buffers(mesh, strokes) -> None:
    """逐帧写顶点位置 / 颜色 / 半径 / 标量（拓扑不变，全是 ``foreach_set``）。"""
    mesh.vertices.foreach_set("co", strokes.points.reshape(-1))
    mesh.attributes[_ATTR_COLOR].data.foreach_set("color", strokes.colours.reshape(-1))
    mesh.attributes[_ATTR_RADIUS].data.foreach_set("value", strokes.radii)
    for name, values in strokes.extras:
        attribute = mesh.attributes.get(name)
        if attribute is None:
            attribute = mesh.attributes.new(name, "FLOAT", "POINT")
        attribute.data.foreach_set("value", values)
    mesh.update()


def write(obj, strokes, frame=None) -> int:
    """重建拓扑并写满整条缓冲（改网格边长后调它）。返回双摆数。

    ``frame`` 只是和 GP 后端对齐签名（网格没有图层动画帧的概念，忽略）。
    """
    mesh = obj.data
    count = strokes.pendulums
    verts = count * VERTS_PER_PENDULUM
    _ensure_topology(mesh, count)
    _ensure_attributes(mesh, verts)
    _ensure_modifier(obj)
    if not mesh.materials:
        mesh.materials.append(_material())
    _write_buffers(mesh, strokes)
    return count


def refresh(obj, strokes) -> bool:
    """逐帧就地更新（拓扑不变）。返回是否成功；``False`` 时调用方走 :func:`write`。"""
    mesh = obj.data
    count = strokes.pendulums
    if len(mesh.vertices) != count * VERTS_PER_PENDULUM:
        return False
    if _ATTR_COLOR not in mesh.attributes or _ATTR_RADIUS not in mesh.attributes:
        return False
    try:
        _write_buffers(mesh, strokes)
    except (KeyError, ValueError):
        return False
    return True
