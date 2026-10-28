"""环面网格 + 零点小球网格 + 顶点属性 + 材质。

两个对象：

* ``Jacobi Theta Torus`` —— nu×nv 的环面四边形网格（u 沿 ``i``、v 沿 ``j``，两个方向
  都是**周期**的）。顶点由求解器每帧给出（零点凹陷 + 相位涟漪），这里只负责拓扑与属性；
* ``Jacobi Zero Points`` —— 每个零点一串二十面体球（低面数，光滑着色），每帧只改顶点
  位置。不用几何节点：球就是普通网格，导出 / 渲染 / 选中都不会缺依赖。

顶点编号是 ``i*nv + j``（C 序）—— 和求解器场的展平顺序一致，所以场可以原样写进属性，
不需要重排。

属性（面板的「数据接口」也照这个列）：

======================  ==========================================
``amplitude``           FLOAT / POINT，零点光晕 ``Σ exp(−120d²)``
``phase``               FLOAT / POINT，``arg Θ``（各零点相角之和）
``phase_grid``          FLOAT / POINT，高频相位干涉格纹 [0, 1]
``potential``           FLOAT / POINT，``log|Θ|``（零点处 → −∞）
``color``               FLOAT_COLOR / POINT，线性 RGBA
======================  ==========================================
"""

from __future__ import annotations

import math

import bpy
import numpy as np

from .palette import SILK, hex_to_linear

__all__ = [
    "MATERIAL_NAME",
    "OBJECT_NAME",
    "ZERO_MATERIAL_NAME",
    "ZERO_OBJECT_NAME",
    "ensure_attributes",
    "ensure_material",
    "ensure_object",
    "ensure_zero_material",
    "ensure_zero_object",
    "find_object",
    "find_zero_object",
    "icosphere",
    "remove_objects",
    "torus_geometry",
    "write_state",
    "write_topology",
    "write_zeros",
]

OBJECT_NAME = "Jacobi Theta Torus"
ZERO_OBJECT_NAME = "Jacobi Zero Points"
MATERIAL_NAME = "Jacobi Theta Surface"
ZERO_MATERIAL_NAME = "Jacobi Theta Zeros"

#: 零点小球细分次数：1 就是 42 顶点 / 80 三角面，64 个零点也只有 2.7k 顶点。
ZERO_SUBDIVISIONS = 1

#: 二十面体模板缓存（顶点 / 面都是单位球上的常数）。
_ICO_CACHE: dict = {"key": None, "verts": None, "faces": None}


def torus_geometry(
    nu: int, nv: int, major_r: float = 3.0, minor_r: float = 1.2
) -> tuple[np.ndarray, np.ndarray]:
    """静态拓扑：``((nu·nv, 3) 顶点, (nu·nv, 4) 四边形)``，两个方向都闭合。"""
    u = np.arange(nu, dtype=np.float32) / float(nu)
    v = np.arange(nv, dtype=np.float32) / float(nv)
    theta = (2.0 * math.pi * u)[:, None]
    phi = (2.0 * math.pi * v)[None, :]
    ring = major_r + minor_r * np.cos(phi)
    verts = np.stack(
        [
            ring * np.cos(theta),
            ring * np.sin(theta),
            np.broadcast_to(minor_r * np.sin(phi), (nu, nv)),
        ],
        axis=-1,
    ).astype(np.float32)

    i = np.arange(nu, dtype=np.int32)
    j = np.arange(nv, dtype=np.int32)
    i_next = (i + 1) % nu
    j_next = (j + 1) % nv
    # 面顺序与顶点编号一致：i*nv + j
    faces = np.stack(
        [
            (i[:, None] * nv + j[None, :]).ravel(),
            (i_next[:, None] * nv + j[None, :]).ravel(),
            (i_next[:, None] * nv + j_next[None, :]).ravel(),
            (i[:, None] * nv + j_next[None, :]).ravel(),
        ],
        axis=1,
    ).astype(np.int32)
    return verts.reshape(-1, 3), faces


def _subdivide(verts: np.ndarray, faces: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """每个三角面切成四个（中点投影回单位球，重复中点只留一份）。"""
    cache: dict = {}
    grown = [tuple(point) for point in verts]
    out = []

    def midpoint(a: int, b: int) -> int:
        key = (a, b) if a < b else (b, a)
        index = cache.get(key)
        if index is not None:
            return index
        point = verts[a] + verts[b]
        point = point / np.linalg.norm(point)
        index = len(grown)
        cache[key] = index
        grown.append(tuple(point))
        return index

    for face in faces:
        a, b, c = int(face[0]), int(face[1]), int(face[2])
        ab = midpoint(a, b)
        bc = midpoint(b, c)
        ca = midpoint(c, a)
        out.extend([(a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca)])

    return np.array(grown, np.float32), np.array(out, np.int32)


def icosphere(subdivisions: int = ZERO_SUBDIVISIONS) -> tuple[np.ndarray, np.ndarray]:
    """单位二十面体球（``subdivisions`` 次细分）的 ``(顶点, 三角面)``，带缓存。"""
    key = int(subdivisions)
    if _ICO_CACHE["key"] == key:
        return _ICO_CACHE["verts"], _ICO_CACHE["faces"]

    ratio = (1.0 + math.sqrt(5.0)) * 0.5
    verts = np.array(
        [
            (-1, ratio, 0),
            (1, ratio, 0),
            (-1, -ratio, 0),
            (1, -ratio, 0),
            (0, -1, ratio),
            (0, 1, ratio),
            (0, -1, -ratio),
            (0, 1, -ratio),
            (ratio, 0, -1),
            (ratio, 0, 1),
            (-ratio, 0, -1),
            (-ratio, 0, 1),
        ],
        np.float32,
    )
    verts /= np.linalg.norm(verts, axis=1)[:, None]
    faces = np.array(
        [
            (0, 11, 5),
            (0, 5, 1),
            (0, 1, 7),
            (0, 7, 10),
            (0, 10, 11),
            (1, 5, 9),
            (5, 11, 4),
            (11, 10, 2),
            (10, 7, 6),
            (7, 1, 8),
            (3, 9, 4),
            (3, 4, 2),
            (3, 2, 6),
            (3, 6, 8),
            (3, 8, 9),
            (4, 9, 5),
            (2, 4, 11),
            (6, 2, 10),
            (8, 6, 7),
            (9, 8, 1),
        ],
        np.int32,
    )

    for _ in range(key):
        verts, faces = _subdivide(verts, faces)

    _ICO_CACHE.update(key=key, verts=verts, faces=faces)
    return verts, faces


# --- 对象 -------------------------------------------------------------------


def find_object() -> bpy.types.Object | None:
    """按名字找生成的环面对象（用户改名了就当没有）。"""
    obj = bpy.data.objects.get(OBJECT_NAME)
    if obj is not None and obj.type == "MESH":
        return obj
    return None


def find_zero_object() -> bpy.types.Object | None:
    """按名字找生成的零点小球对象。"""
    obj = bpy.data.objects.get(ZERO_OBJECT_NAME)
    if obj is not None and obj.type == "MESH":
        return obj
    return None


def _ensure_mesh_object(name: str, collection=None) -> bpy.types.Object:
    """建/取网格对象；``collection`` 缺省是**当前激活的** collection。"""
    obj = bpy.data.objects.get(name)
    if obj is not None and obj.type == "MESH":
        return obj
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    target = collection or bpy.context.collection or bpy.context.scene.collection
    target.objects.link(obj)
    return obj


def ensure_object(collection=None) -> bpy.types.Object:
    """建/取环面对象（进当前激活的 collection）。"""
    return _ensure_mesh_object(OBJECT_NAME, collection)


def ensure_zero_object(collection=None) -> bpy.types.Object:
    """建/取零点小球对象（进当前激活的 collection）。"""
    return _ensure_mesh_object(ZERO_OBJECT_NAME, collection)


def remove_objects() -> int:
    """删除两个生成对象（含数据块），返回删掉的对象个数。"""
    removed = 0
    for name in (OBJECT_NAME, ZERO_OBJECT_NAME):
        obj = bpy.data.objects.get(name)
        if obj is None:
            continue
        mesh = obj.data if obj.type == "MESH" else None
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        removed += 1
    return removed


# --- 拓扑 / 属性 -------------------------------------------------------------


def write_topology(
    obj: bpy.types.Object, nu: int, nv: int, major_r: float = 3.0, minor_r: float = 1.2
) -> None:
    """按分辨率重建环面拓扑（顶点 / 四边形 / 平滑）。"""
    verts, faces = torus_geometry(nu, nv, major_r, minor_r)
    mesh = obj.data

    mesh.clear_geometry()
    mesh.vertices.add(int(verts.shape[0]))
    mesh.vertices.foreach_set("co", verts.ravel())

    mesh.loops.add(int(faces.size))
    mesh.polygons.add(int(faces.shape[0]))
    mesh.loops.foreach_set("vertex_index", faces.ravel())
    mesh.polygons.foreach_set("loop_start", np.arange(0, faces.size, 4, dtype=np.int32))
    mesh.polygons.foreach_set("loop_total", np.full(faces.shape[0], 4, dtype=np.int32))
    mesh.polygons.foreach_set("use_smooth", np.ones(faces.shape[0], dtype=bool))
    mesh.update(calc_edges=True)


def ensure_attributes(mesh: bpy.types.Mesh) -> None:
    """补齐四张标量属性与颜色属性，并把颜色设为视口/渲染的活跃色。"""
    for name in ("amplitude", "phase", "phase_grid", "potential"):
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
    """材质：把 ``color`` 顶点属性接到 Base Color（颜色只存一份，不复制到材质里）。"""
    mat = bpy.data.materials.get(MATERIAL_NAME)
    if mat is None:
        mat = bpy.data.materials.new(MATERIAL_NAME)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (360, 0)
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (0, 0)
    attribute = nodes.new("ShaderNodeVertexColor")
    attribute.layer_name = "color"
    attribute.location = (-240, 0)

    links.new(attribute.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.32
    bsdf.inputs["Metallic"].default_value = 0.25
    # 让金线菱格自己发一点光（渲染/材质预览里才是参考实现那种"发亮"的观感）
    emission = bsdf.inputs.get("Emission Color")
    strength = bsdf.inputs.get("Emission Strength")
    if emission is not None and strength is not None:
        links.new(attribute.outputs["Color"], emission)
        strength.default_value = 0.15

    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)
    return mat


def ensure_zero_material(obj: bpy.types.Object) -> bpy.types.Material:
    """零点小球材质：绢白、低粗糙度（当"玻璃珠"用）。"""
    mat = bpy.data.materials.get(ZERO_MATERIAL_NAME)
    if mat is None:
        mat = bpy.data.materials.new(ZERO_MATERIAL_NAME)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (*hex_to_linear(SILK), 1.0)
        bsdf.inputs["Roughness"].default_value = 0.12
        bsdf.inputs["Metallic"].default_value = 0.0

    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)
    return mat


# --- 每帧写入 ---------------------------------------------------------------


def write_state(
    obj: bpy.types.Object,
    verts: np.ndarray,
    amplitude: np.ndarray,
    phase: np.ndarray,
    phase_grid: np.ndarray,
    potential: np.ndarray,
    colours: np.ndarray,
) -> None:
    """把当前帧的环面顶点与四张场写进网格。"""
    mesh = obj.data
    count = len(mesh.vertices)
    flat = np.asarray(verts, np.float32).reshape(-1, 3)
    if flat.shape[0] != count:
        raise ValueError(f"vertices {flat.shape[0]} != mesh vertices {count}")

    mesh.vertices.foreach_set("co", flat.ravel())
    mesh.attributes["amplitude"].data.foreach_set("value", amplitude)
    mesh.attributes["phase"].data.foreach_set("value", phase)
    mesh.attributes["phase_grid"].data.foreach_set("value", phase_grid)
    mesh.attributes["potential"].data.foreach_set("value", potential)
    mesh.color_attributes["color"].data.foreach_set("color", colours.ravel())
    mesh.update()


def _write_zero_topology(
    mesh: bpy.types.Mesh,
    count: int,
    template_verts: np.ndarray,
    template_faces: np.ndarray,
) -> None:
    """按「count 份二十面体球」重建小球网格拓扑。"""
    per_sphere = int(template_verts.shape[0])
    sphere_faces = int(template_faces.shape[0])

    mesh.clear_geometry()
    mesh.vertices.add(count * per_sphere)

    offsets = np.arange(count, dtype=np.int32)[:, None, None] * per_sphere
    faces = (template_faces[None, :, :] + offsets).reshape(-1)
    mesh.loops.add(int(faces.size))
    mesh.polygons.add(count * sphere_faces)
    mesh.loops.foreach_set("vertex_index", faces)
    mesh.polygons.foreach_set("loop_start", np.arange(0, faces.size, 3, dtype=np.int32))
    mesh.polygons.foreach_set(
        "loop_total", np.full(count * sphere_faces, 3, dtype=np.int32)
    )
    mesh.polygons.foreach_set("use_smooth", np.ones(count * sphere_faces, dtype=bool))
    mesh.update(calc_edges=True)


def write_zeros(
    obj: bpy.types.Object, centers: np.ndarray, sphere_radius: float
) -> None:
    """把每个零点小球摆到 ``centers``（拓扑随个数自动重建，半径只缩放顶点）。"""
    centers = np.asarray(centers, np.float32).reshape(-1, 3)
    count = int(centers.shape[0])
    template_verts, template_faces = icosphere(ZERO_SUBDIVISIONS)
    mesh = obj.data

    if count == 0:
        if len(mesh.vertices):
            mesh.clear_geometry()
        return

    per_sphere = int(template_verts.shape[0])
    if len(mesh.vertices) != count * per_sphere:
        _write_zero_topology(mesh, count, template_verts, template_faces)

    verts = (
        template_verts[None, :, :] * float(sphere_radius) + centers[:, None, :]
    ).reshape(-1, 3)
    mesh.vertices.foreach_set("co", verts.ravel())
    mesh.update()
