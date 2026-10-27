"""高度场网格层：``nx × ny`` 网格 + 顶点属性 + 材质 + 参考对象取样。

网格铺在 **XY 平面**（``x`` 沿 ``i``、``y`` 沿 ``j``），顶点高度 ``z = φ·z_scale``：

* 拓扑只在分辨率/间距变化时重建（:func:`write_topology`）；
* 每帧只改 ``co`` 与属性（:func:`write_state`），走 ``foreach_set``，250² 网格 < 1 ms；
* 顶点编号是 ``i*ny + j``（C 序）—— 和 :class:`~sim_dendrite.solver.NumPyPhaseField`
  的字段展平顺序一致，所以相场数组可以原样写进属性，不需要重排。

属性（面板的「数据接口」也照这个列）：

======================  ==========================================
``phi``                 FLOAT / POINT，相场 0（液相）~ 1（固相）
``dT``                  FLOAT / POINT，过冷度 ΔT = T − T_m
``interface``           FLOAT / POINT，``4φ(1−φ)``，界面指示函数
``color``               FLOAT_COLOR / POINT，线性 RGBA（Solid 的 Attribute 着色与渲染共用）
======================  ==========================================
"""

from __future__ import annotations

import bmesh
import bpy
import numpy as np

__all__ = [
    "MATERIAL_NAME",
    "OBJECT_NAME",
    "ensure_attributes",
    "ensure_material",
    "ensure_object",
    "find_object",
    "grid_geometry",
    "remove_object",
    "seed_points_from_object",
    "write_state",
    "write_topology",
]

OBJECT_NAME = "Dendrite Growth"
MATERIAL_NAME = "Dendrite Phase Field"

#: 展平后的顶点缓冲缓存（每帧只改 z，不重建 xy）。
_VERTEX_CACHE: dict = {"key": None, "array": None}


def grid_geometry(
    nx: int, ny: int, dx: float, dy: float
) -> tuple[np.ndarray, np.ndarray]:
    """静态拓扑：``((nx·ny, 3) 顶点, ((nx−1)(ny−1), 4) 四边形)``，以原点为中心。"""
    xs = (np.arange(nx, dtype=np.float32) - (nx - 1) * 0.5) * dx
    ys = (np.arange(ny, dtype=np.float32) - (ny - 1) * 0.5) * dy

    verts = np.zeros((nx * ny, 3), np.float32)
    verts[:, 0] = np.repeat(xs, ny)
    verts[:, 1] = np.tile(ys, nx)

    i, j = np.meshgrid(np.arange(nx - 1), np.arange(ny - 1), indexing="ij")
    faces = np.empty((i.size, 4), np.int32)
    faces[:, 0] = (i * ny + j).ravel()
    faces[:, 1] = ((i + 1) * ny + j).ravel()
    faces[:, 2] = ((i + 1) * ny + j + 1).ravel()
    faces[:, 3] = (i * ny + j + 1).ravel()
    return verts, faces


def find_object() -> bpy.types.Object | None:
    """按名字找生成的对象（用户改名了就当没有）。"""
    obj = bpy.data.objects.get(OBJECT_NAME)
    if obj is not None and obj.type == "MESH":
        return obj
    return None


def ensure_object(collection=None) -> bpy.types.Object:
    """建/取生成用的网格对象；``collection`` 缺省是场景的根集合。"""
    obj = find_object()
    if obj is not None:
        return obj
    mesh = bpy.data.meshes.new(OBJECT_NAME)
    obj = bpy.data.objects.new(OBJECT_NAME, mesh)
    (collection or bpy.context.scene.collection).objects.link(obj)
    return obj


def remove_object() -> bool:
    """删除生成的对象（含数据块），返回是否真的删了。"""
    obj = find_object()
    if obj is None:
        return False
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)
    _VERTEX_CACHE.update(key=None, array=None)
    return True


def write_topology(
    obj: bpy.types.Object, nx: int, ny: int, dx: float, dy: float
) -> None:
    """按分辨率重建网格拓扑（顶点 / 四边形 / 平滑），并刷新顶点缓冲缓存。"""
    verts, faces = grid_geometry(nx, ny, dx, dy)
    mesh = obj.data

    mesh.clear_geometry()
    mesh.vertices.add(int(verts.shape[0]))
    mesh.vertices.foreach_set("co", verts.ravel())

    mesh.loops.add(int(faces.size))
    mesh.polygons.add(int(faces.shape[0]))
    mesh.loops.foreach_set("vertex_index", faces.ravel().astype(np.int32))
    mesh.polygons.foreach_set("loop_start", np.arange(0, faces.size, 4, dtype=np.int32))
    mesh.polygons.foreach_set("loop_total", np.full(faces.shape[0], 4, dtype=np.int32))
    mesh.polygons.foreach_set("use_smooth", np.ones(faces.shape[0], dtype=bool))
    mesh.update(calc_edges=True)

    _VERTEX_CACHE.update(
        key=(int(nx), int(ny), round(float(dx), 9), round(float(dy), 9)), array=verts
    )


def _vertex_buffer(nx: int, ny: int, dx: float, dy: float) -> np.ndarray:
    """取（或重建）顶点缓冲：x/y 是静态的，每帧只覆写 z。"""
    key = (int(nx), int(ny), round(float(dx), 9), round(float(dy), 9))
    if _VERTEX_CACHE["key"] != key:
        verts, _ = grid_geometry(nx, ny, dx, dy)
        _VERTEX_CACHE.update(key=key, array=verts)
    return _VERTEX_CACHE["array"]


def ensure_attributes(mesh: bpy.types.Mesh) -> None:
    """补齐标量属性与颜色属性，并把颜色设为视口/渲染的活跃色。"""
    for name in ("phi", "dT", "interface"):
        if name not in mesh.attributes:
            mesh.attributes.new(name=name, type="FLOAT", domain="POINT")
    if "color" not in mesh.color_attributes:
        mesh.color_attributes.new(name="color", type="FLOAT_COLOR", domain="POINT")
    index = mesh.color_attributes.find("color")
    if index >= 0:
        # Solid 的 Attribute 着色与渲染都读这个：不设就可能是「没有颜色」
        mesh.color_attributes.active_color_index = index
        mesh.color_attributes.render_color_index = index


def ensure_material(obj: bpy.types.Object) -> bpy.types.Material:
    """材质：把 ``color`` 顶点属性直接当 Base Color（颜色只存一份，不复制到材质里）。"""
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
    attribute.layer_name = "color"
    attribute.location = (-220, 0)

    links.new(attribute.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.45
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)
    return mat


def write_state(
    obj: bpy.types.Object,
    phi: np.ndarray,
    dT: np.ndarray,
    colours: np.ndarray,
    *,
    nx: int,
    ny: int,
    dx: float,
    dy: float,
    z_scale: float,
) -> None:
    """把当前场写进网格：高度 ``z = φ·z_scale``、三个标量属性、一份顶点色。"""
    mesh = obj.data
    count = len(mesh.vertices)
    if phi.size != count:
        raise ValueError(f"field size {phi.size} != mesh vertices {count}")

    verts = _vertex_buffer(nx, ny, dx, dy)
    verts[:, 2] = phi * float(z_scale)

    mesh.vertices.foreach_set("co", verts.ravel())
    mesh.attributes["phi"].data.foreach_set("value", phi)
    mesh.attributes["dT"].data.foreach_set("value", dT)
    mesh.attributes["interface"].data.foreach_set("value", 4.0 * phi * (1.0 - phi))
    mesh.color_attributes["color"].data.foreach_set("color", colours.ravel())
    mesh.update()


def seed_points_from_object(obj: bpy.types.Object | None) -> np.ndarray | None:
    """参考网格的顶点 → 归一化到 ``[-1, 1]`` 的二维种子点（世界空间 XY 投影）。

    支持物体模式（取求值后的网格，修改器/几何节点都算）与编辑模式（BMesh 里的当前顶点）。
    返回 ``None`` 表示没有可用顶点。
    """
    if obj is None or obj.type != "MESH":
        return None

    matrix = np.array(obj.matrix_world, dtype=np.float32)
    rotation = matrix[:3, :3]
    translation = matrix[:3, 3]

    if obj.mode == "EDIT":
        bm = bmesh.from_edit_mesh(obj.data)
        if len(bm.verts) == 0:
            return None
        verts = np.array([vertex.co for vertex in bm.verts], dtype=np.float32)
    else:
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        temp_mesh = evaluated.to_mesh()
        try:
            count = len(temp_mesh.vertices)
            if count == 0:
                return None
            verts = np.empty(count * 3, dtype=np.float32)
            temp_mesh.vertices.foreach_get("co", verts)
            verts = verts.reshape(count, 3)
        finally:
            evaluated.to_mesh_clear()

    world = verts @ rotation.T + translation
    points = world[:, :2]

    low = points.min(axis=0)
    high = points.max(axis=0)
    size = float((high - low).max())
    if size < 1e-6:
        size = 1.0
    return ((points - (low + high) * 0.5) / size * 1.6).astype(np.float32)
