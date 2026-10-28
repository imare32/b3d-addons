"""网格层：**面 = 细胞**。拓扑提取、属性、材质、状态写入。

邻域（Moore）从 BMesh 提取：一个面的邻居 = 至少和它共用一个顶点的其他面 —— 四边形
网格上是 8 个（4 条边 + 4 个对角），三角面一般 11~12 个；上限
:data:`~sim_game_of_life.solver.MAX_NEIGHBORS` = 24，超过时按「共边优先」截断。
所以生命游戏跑的是**曲面上**的细胞自动机，不是平面棋盘。

属性（面板的「数据接口」也照这个列）：

======================  ==============================================
``alive``               FLOAT / FACE，0（死）~ 1（生）。帧间插值后是连续的，用来驱动
                        几何节点时按 ``> 0.5`` 取活格
``age``                 FLOAT / FACE，连续存活代数（这一代活着就 +1，死掉归零）
``color``               FLOAT_COLOR / CORNER，线性 RGBA（Solid 的 Attribute 着色与渲染
                        共用）。颜色属性只能落在 POINT / CORNER 域上，所以按
                        「loop → 所属面」展开了一遍
======================  ==============================================

几何**不动**：这个插件只往目标网格上写上面三个属性（外加一个读 ``color`` 的材质），
一个顶点都不会挪。目标网格改了拓扑（面数变了）会由 :mod:`sim_game_of_life.sim` 重新
提取邻域。
"""

from __future__ import annotations

import math

import bmesh
import bpy
import numpy as np

from .solver import MAX_NEIGHBORS

__all__ = [
    "MATERIAL_NAME",
    "OBJECT_NAME",
    "ensure_attributes",
    "ensure_default_object",
    "ensure_material",
    "extract_topology",
    "find_object",
    "remove_object",
    "topology_stats",
    "torus_geometry",
    "write_state",
]

OBJECT_NAME = "GOL Surface"
MATERIAL_NAME = "Game of Life"

#: 缺省网格（环面）参数：48×16 = 768 个四边形，每个面 8 个邻居，曲率处处不同。
TORUS_SEGMENTS = (48, 16)
TORUS_RADII = (1.5, 0.5)

#: loop → 所属面 的映射缓存（只在网格拓扑变化时重建）。
_LOOP_MAP: dict = {"key": None, "array": None}


# --- 缺省网格 ---------------------------------------------------------------


def torus_geometry(
    major_segments: int = TORUS_SEGMENTS[0],
    minor_segments: int = TORUS_SEGMENTS[1],
    major_radius: float = TORUS_RADII[0],
    minor_radius: float = TORUS_RADII[1],
) -> tuple[np.ndarray, np.ndarray]:
    """标准环面：``(顶点 (nu·nv, 3), 四边形 ((nu·nv), 4))``，首尾相接（无边界）。"""
    nu, nv = int(major_segments), int(minor_segments)
    u = np.arange(nu, dtype=np.float64) * (2.0 * math.pi / nu)
    v = np.arange(nv, dtype=np.float64) * (2.0 * math.pi / nv)

    radius = (major_radius + minor_radius * np.cos(v))[np.newaxis, :]  # (1, nv)
    verts = np.empty((nu * nv, 3), np.float32)
    verts[:, 0] = (radius * np.cos(u)[:, np.newaxis]).ravel()
    verts[:, 1] = (radius * np.sin(u)[:, np.newaxis]).ravel()
    verts[:, 2] = np.tile(minor_radius * np.sin(v), nu).astype(np.float32)

    faces = np.empty((nu * nv, 4), np.int32)
    index_u = np.arange(nu)
    index_v = np.arange(nv)
    grid_u, grid_v = np.meshgrid(index_u, index_v, indexing="ij")
    next_u = (grid_u + 1) % nu
    next_v = (grid_v + 1) % nv
    faces[:, 0] = (grid_u * nv + grid_v).ravel()
    faces[:, 1] = (next_u * nv + grid_v).ravel()
    faces[:, 2] = (next_u * nv + next_v).ravel()
    faces[:, 3] = (grid_u * nv + next_v).ravel()
    return verts, faces


def find_object() -> bpy.types.Object | None:
    """按名字找插件自己建的缺省对象（用户改名了就当没有）。"""
    obj = bpy.data.objects.get(OBJECT_NAME)
    if obj is not None and obj.type == "MESH":
        return obj
    return None


def ensure_default_object(collection=None) -> bpy.types.Object:
    """建/取缺省环面（没有目标网格时用它跑），加进当前激活的 collection。

    ``collection`` 缺省是 ``bpy.context.collection``（大纲 / 属性编辑器里选中的那个），
    与仓库其它插件一致；后台 / handler 里取不到时才回退场景根集合。
    """
    obj = find_object()
    if obj is not None:
        return obj

    verts, faces = torus_geometry()
    mesh = bpy.data.meshes.new(OBJECT_NAME)
    mesh.vertices.add(int(verts.shape[0]))
    mesh.vertices.foreach_set("co", verts.ravel())
    mesh.loops.add(int(faces.size))
    mesh.polygons.add(int(faces.shape[0]))
    mesh.loops.foreach_set("vertex_index", faces.ravel().astype(np.int32))
    mesh.polygons.foreach_set("loop_start", np.arange(0, faces.size, 4, dtype=np.int32))
    mesh.polygons.foreach_set("loop_total", np.full(faces.shape[0], 4, dtype=np.int32))
    mesh.update(calc_edges=True)

    # 环面的四边形绕向是手写的，交给 bmesh 统一朝外，免得 Solid 里一半是黑的
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()

    obj = bpy.data.objects.new(OBJECT_NAME, mesh)
    target = collection or bpy.context.collection or bpy.context.scene.collection
    target.objects.link(obj)
    _LOOP_MAP.update(key=None, array=None)
    return obj


def remove_object() -> bool:
    """删除插件自己建的缺省对象（含数据块），返回是否真的删了。"""
    obj = find_object()
    if obj is None:
        return False
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)
    _LOOP_MAP.update(key=None, array=None)
    return True


# --- 拓扑 -------------------------------------------------------------------


def extract_topology(obj: bpy.types.Object) -> tuple[np.ndarray, np.ndarray]:
    """从网格提取面邻域：``((n, MAX_NEIGHBORS) 索引, (n,) 有效邻居数)``。

    物体模式读取网格数据；编辑模式读当前 BMesh（顶点/面还没提交到网格数据）。
    """
    if obj.type != "MESH":
        raise TypeError(f"目标不是网格对象：{obj.name}")

    if obj.mode == "EDIT":
        bm = bmesh.from_edit_mesh(obj.data)
        return _topology_from_bmesh(bm)

    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        return _topology_from_bmesh(bm)
    finally:
        bm.free()


def topology_stats(counts: np.ndarray) -> dict:
    """邻域分布的体检结果（面板读数 + 警告用）。

    ``standard`` 是**恰好 8 个邻居**的面占比 —— 康威规则假设的就是这个邻域。四边形
    网格大多逼近 1.0；三角面（11~12 个邻居）、边界（3~5 个）、高价顶点（被截断到
    :data:`~sim_game_of_life.solver.MAX_NEIGHBORS`）都会把它拉低。实测参考值：闭环
    环面 100%、带边界的平面网格 84%、猴头 53%、猴头细分两级后 93%。
    """
    values = np.asarray(counts, np.int32).ravel()
    total = int(values.size)
    if total == 0:
        return {"faces": 0, "standard": 0.0, "min": 0, "max": 0}
    return {
        "faces": total,
        "standard": float((values == 8).mean()),
        "min": int(values.min()),
        "max": int(values.max()),
    }


def _topology_from_bmesh(bm) -> tuple[np.ndarray, np.ndarray]:
    """BMesh → 面邻域数组（顶点 → 面 的倒排表，避免逐面走 ``link_faces``）。

    邻居按「共边（4 连通）优先、共点（对角）在后」排列：截断到
    :data:`~sim_game_of_life.solver.MAX_NEIGHBORS` 时先掉的是对角邻居。UV 球两极
    那种高价顶点会让一圈面全都互为邻居（50 个），不排序的话掉哪个全看集合顺序。
    """
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    n_faces = len(bm.faces)
    vert_faces = [[] for _ in range(len(bm.verts))]
    for face in bm.faces:
        for vert in face.verts:
            vert_faces[vert.index].append(face.index)

    neighbors = np.full((n_faces, MAX_NEIGHBORS), -1, dtype=np.int32)
    counts = np.zeros(n_faces, dtype=np.int32)

    for face in bm.faces:
        index = face.index
        edge_near = set()
        for edge in face.edges:
            for other in edge.link_faces:
                if other is not face:
                    edge_near.add(other.index)

        vert_near = set()
        for vert in face.verts:
            vert_near.update(vert_faces[vert.index])
        vert_near.discard(index)

        # 排序只为结果可复现：计数是 bincount，与顺序无关
        values = sorted(edge_near) + sorted(vert_near - edge_near)
        values = values[:MAX_NEIGHBORS]
        counts[index] = len(values)
        neighbors[index, : len(values)] = values

    return neighbors, counts


# --- 属性 / 材质 ------------------------------------------------------------


def _ensure_face_attribute(mesh: bpy.types.Mesh, name: str):
    """取/建 ``FLOAT / FACE`` 属性；同名但类型不对的（比如参考脚本留下的旧数据）重建。"""
    attribute = mesh.attributes.get(name)
    if attribute is not None and (
        attribute.data_type != "FLOAT" or attribute.domain != "FACE"
    ):
        mesh.attributes.remove(attribute)
        attribute = None
    if attribute is None:
        attribute = mesh.attributes.new(name=name, type="FLOAT", domain="FACE")
    return attribute


def ensure_attributes(mesh: bpy.types.Mesh) -> None:
    """补齐 ``alive`` / ``age`` 面属性与 ``color`` 角点色属性，并设为活跃色。"""
    _ensure_face_attribute(mesh, "alive")
    _ensure_face_attribute(mesh, "age")

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


def ensure_material(obj: bpy.types.Object) -> bpy.types.Material:
    """材质：把 ``color`` 属性直接当 Base Color（颜色只存一份，不复制到材质里）。"""
    mat = bpy.data.materials.get(MATERIAL_NAME)
    if mat is None:
        mat = bpy.data.materials.new(MATERIAL_NAME)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (320, 0)
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (0, 0)
    attribute = nodes.new("ShaderNodeVertexColor")
    # layer_name 故意不设（默认空）：留空时取的是活跃色属性（ensure_attributes 已设好
    # active / render index）。填 "color" / "vertex_color" 会被 Cycles 当成它自己的标准
    # 顶点色名，取到空值、渲染成纯黑（EEVEE 不受影响）。
    attribute.location = (-220, 0)

    links.new(attribute.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.45
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)
    return mat


# --- 写状态 -----------------------------------------------------------------


def _face_of_loop(mesh: bpy.types.Mesh) -> np.ndarray:
    """``loop → 所属面``（loop 按面连续存放，所以 ``repeat`` 就够）。"""
    key = (len(mesh.polygons), len(mesh.loops))
    if _LOOP_MAP["key"] != key:
        totals = np.empty(key[0], dtype=np.int32)
        mesh.polygons.foreach_get("loop_total", totals)
        _LOOP_MAP.update(
            key=key, array=np.repeat(np.arange(key[0], dtype=np.int32), totals)
        )
    return _LOOP_MAP["array"]


def write_state(
    obj: bpy.types.Object,
    alive: np.ndarray,
    age: np.ndarray,
    colours: np.ndarray,
) -> None:
    """把这一帧的状态写进网格属性（只在物体模式下调用）。"""
    mesh = obj.data
    count = len(mesh.polygons)
    values = np.ascontiguousarray(np.asarray(alive, np.float32).ravel())
    if values.size != count:
        raise ValueError(f"状态长度 {values.size} 与面数 {count} 不一致")

    ages = np.ascontiguousarray(np.asarray(age, np.float32).ravel())
    mesh.attributes["alive"].data.foreach_set("value", values)
    mesh.attributes["age"].data.foreach_set("value", ages)

    mapping = _face_of_loop(mesh)
    loop_colours = np.ascontiguousarray(
        np.asarray(colours, np.float32).reshape(-1, 4)[mapping]
    )
    mesh.color_attributes["color"].data.foreach_set("color", loop_colours.ravel())
    mesh.update()
