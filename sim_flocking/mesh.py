"""网格层：**每只鸟 = 一个朝前的三角箭头**。几何生成、属性、材质。

为什么不沿用参考脚本的「点云 + 几何节点实例化」：那条路要把颜色传到实例上得多绕一层
（实例域属性 + Realize + Store Named Attribute），而这里直接把三角形写进网格 ——

* 顶点三条一组（``0..3n``），每个三角形是一个 polygon，逐 loop 顺序就是鸟的顺序，所以
  逐鸟颜色可以直接铺到 ``CORNER`` 域上（与 ``sim_game_of_life`` 同一个做法）；
* 几何只是数值：一帧一次 ``foreach_set``，300 只鸟（900 个顶点）不到 0.2 ms；
* 不依赖几何节点，导出 / 渲染 / Solid 属性着色都直接可用。

属性（面板的「数据接口」也照这个列）：

======================  ==============================================
``velocity``            FLOAT_VECTOR / POINT，每只鸟的速度（同一只鸟的 3 个顶点同值）
``speed``               FLOAT / POINT，速率（``|velocity|``），几何节点里按它挑形状方便
``color``               FLOAT_COLOR / CORNER，线性 RGBA（Solid 的 Attribute 着色与渲染
                        共用），一只鸟的 3 个 loop 同色
======================  ==============================================

箭头的朝向来自速度方向；速度为 0 的鸟按「朝 +X」画（``boid_vertices`` 里兜的底），否则
``normalize(0)`` 会给出 NaN、整只鸟飞进无限远。
"""

from __future__ import annotations

import numpy as np

__all__ = [
    "MATERIAL_NAME",
    "OBJECT_NAME",
    "boid_vertices",
    "ensure_attributes",
    "ensure_material",
    "ensure_object",
    "find_object",
    "remove_object",
    "write_state",
]

OBJECT_NAME = "Flock Boids"
MATERIAL_NAME = "Flocking"

#: 箭头长度与最宽处的比例：0.46 看起来像鸟，再宽就像纸飞机、再窄像针。
ARROW_ASPECT = 0.46


# --- 对象 -------------------------------------------------------------------


def find_object():
    """按名字找插件自己建的鸟群对象（用户改名了就当没有）。"""
    import bpy

    obj = bpy.data.objects.get(OBJECT_NAME)
    if obj is not None and obj.type == "MESH":
        return obj
    return None


def ensure_object(collection=None):
    """建/取鸟群对象，加进当前激活的 collection。"""
    import bpy

    obj = find_object()
    if obj is not None:
        return obj

    mesh = bpy.data.meshes.new(OBJECT_NAME)
    obj = bpy.data.objects.new(OBJECT_NAME, mesh)
    target = collection or bpy.context.collection or bpy.context.scene.collection
    target.objects.link(obj)
    return obj


def remove_object() -> bool:
    """删除插件自己建的鸟群对象（含数据块），返回是否真的删了。"""
    import bpy

    obj = find_object()
    if obj is None:
        return False
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)
    return True


# --- 几何 -------------------------------------------------------------------


def boid_vertices(
    positions: np.ndarray, velocities: np.ndarray, size: float
) -> np.ndarray:
    """``(n,2) 位置 + (n,2) 速度`` → ``(3n, 3)`` 顶点坐标（Z = 0 平面上的箭头）。

    顶点顺序：``0`` = 鼻尖、``1`` = 左翼、``2`` = 右翼 —— **逆时针**（从 +Z 看），法线朝
    上；换成顺时针的话 Solid 里会看到背面，颜色被光照压暗一半。
    """
    pos = np.asarray(positions, np.float32).reshape(-1, 2)
    vel = np.asarray(velocities, np.float32).reshape(-1, 2)
    n = int(pos.shape[0])
    out = np.zeros((n * 3, 3), np.float32)
    if n == 0:
        return out

    speed = np.linalg.norm(vel, axis=1)
    heading = np.zeros((n, 2), np.float32)
    moving = speed > 1e-9
    heading[moving] = vel[moving] / speed[moving, None]
    heading[~moving, 0] = 1.0  # 静止的鸟朝 +X，避免除以 0
    side = np.stack([-heading[:, 1], heading[:, 0]], axis=1)

    length = float(max(size, 1e-4))
    half_width = length * ARROW_ASPECT * 0.5
    nose = pos + heading * (length * 0.62)
    tail = pos - heading * (length * 0.38)
    left = tail + side * half_width
    right = tail - side * half_width

    out[0::3, :2] = nose
    out[1::3, :2] = left
    out[2::3, :2] = right
    return out


# --- 属性 / 材质 ------------------------------------------------------------


def _ensure_attribute(mesh, name: str, data_type: str, domain: str):
    """取/建属性；同名但类型 / 域不对的（比如用户自己的同名数据）重建。"""
    attribute = mesh.attributes.get(name)
    if attribute is not None and (
        attribute.data_type != data_type or attribute.domain != domain
    ):
        mesh.attributes.remove(attribute)
        attribute = None
    if attribute is None:
        attribute = mesh.attributes.new(name=name, type=data_type, domain=domain)
    return attribute


def ensure_attributes(mesh) -> None:
    """补齐 ``velocity`` / ``speed`` 点属性与 ``color`` 角点色属性，并设为活跃色。"""
    _ensure_attribute(mesh, "velocity", "FLOAT_VECTOR", "POINT")
    _ensure_attribute(mesh, "speed", "FLOAT", "POINT")

    attribute = mesh.color_attributes.get("color")
    if attribute is not None and (
        attribute.data_type != "FLOAT_COLOR" or attribute.domain != "CORNER"
    ):
        mesh.color_attributes.remove(attribute)
        attribute = None
    if attribute is None:
        attribute = mesh.color_attributes.new(
            name="color", type="FLOAT_COLOR", domain="CORNER"
        )
    index = mesh.color_attributes.find("color")
    if index >= 0:
        # Solid 的 Attribute 着色与渲染都读这个：不设就可能是「没有颜色」
        mesh.color_attributes.active_color_index = index
        mesh.color_attributes.render_color_index = index


def ensure_material(obj):
    """材质：把 ``color`` 属性直接当 Base Color（颜色只存一份，不复制到材质里）。"""
    import bpy

    mat = bpy.data.materials.get(MATERIAL_NAME)
    if mat is None:
        mat = bpy.data.materials.new(MATERIAL_NAME)
    mat.use_nodes = True
    # 箭头是双面都会看到的薄片：关掉背面剔除，从下方看也不会消失
    mat.use_backface_culling = False
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (320, 0)
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (0, 0)
    bsdf.inputs["Roughness"].default_value = 0.55
    attribute = nodes.new("ShaderNodeVertexColor")
    # layer_name 故意不设（默认空）：留空时取的是活跃色属性（ensure_attributes 已设好
    # active / render index）。填 "color" / "vertex_color" 会被 Cycles 当成它自己的标准
    # 顶点色名，取到空值、渲染成纯黑（EEVEE 不受影响）。
    attribute.location = (-220, 0)

    links.new(attribute.outputs["Color"], bsdf.inputs["Base Color"])
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    # 自发光补一档：箭头是几像素大的薄片，只靠世界底色照明时颜色会被底色（暖灰）染偏，
    # 速度色分不出冷档。0.4 的强度足够让「石青 / 苔绿 / 赭石 / 朱砂」保持可辨识，
    # 又不至于亮到失去明暗。
    if "Emission Color" in bsdf.inputs:
        links.new(attribute.outputs["Color"], bsdf.inputs["Emission Color"])
        bsdf.inputs["Emission Strength"].default_value = 0.4

    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)
    return mat


# --- 写状态 -----------------------------------------------------------------


def _rebuild_topology(mesh, count: int) -> None:
    """按鸟数重建三角形的拓扑（顶点 / loop / 面都是 ``3·count`` / ``count``）。"""
    mesh.clear_geometry()
    if count <= 0:
        mesh.update()
        return

    loops = count * 3
    mesh.vertices.add(count * 3)
    mesh.loops.add(loops)
    mesh.polygons.add(count)
    mesh.loops.foreach_set("vertex_index", np.arange(loops, dtype=np.int32))
    mesh.polygons.foreach_set("loop_start", np.arange(0, loops, 3, dtype=np.int32))
    mesh.polygons.foreach_set("loop_total", np.full(count, 3, dtype=np.int32))
    # 薄片箭头要平面着色：默认是 False，显式写一遍免得继承到旧网格的平滑标记
    mesh.polygons.foreach_set("use_smooth", np.zeros(count, dtype=bool))
    mesh.update(calc_edges=True)


def write_state(
    obj, vertices: np.ndarray, velocities: np.ndarray, colours: np.ndarray
) -> None:
    """把这一帧的几何与颜色写进网格（只在物体模式下调用）。"""
    mesh = obj.data
    coords = np.ascontiguousarray(np.asarray(vertices, np.float32).reshape(-1, 3))
    count = int(coords.shape[0] // 3)

    if len(mesh.vertices) != count * 3 or len(mesh.polygons) != count:
        _rebuild_topology(mesh, count)
    if count == 0:
        return
    ensure_attributes(mesh)

    mesh.vertices.foreach_set("co", coords.ravel())

    # 逐鸟 → 逐顶点：3 个顶点的属性一样（几何节点按顶点取也没问题）
    vel = np.ascontiguousarray(np.asarray(velocities, np.float32).reshape(count, 2))
    per_vertex = np.stack([vel[:, 0], vel[:, 1], np.zeros(count, np.float32)], axis=1)
    mesh.attributes["velocity"].data.foreach_set(
        "vector", np.repeat(per_vertex, 3, axis=0).ravel()
    )
    speeds = np.linalg.norm(vel, axis=1).astype(np.float32)
    mesh.attributes["speed"].data.foreach_set("value", np.repeat(speeds, 3))

    # 逐鸟 → 逐 loop：loop 按面连续存放，每面 3 个，所以直接 repeat（顺序即鸟的顺序）
    rgba = np.ascontiguousarray(np.asarray(colours, np.float32).reshape(count, 4))
    mesh.color_attributes["color"].data.foreach_set(
        "color", np.repeat(rgba, 3, axis=0).ravel()
    )
    mesh.update()
