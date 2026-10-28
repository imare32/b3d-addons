"""输入网格层：表面三角化 → 吸收点取样 / 根点提取 / 表面投影。

「对任意一个输入网格执行算法」的落点都在这里。参考实现只拿**顶点**当吸收点，粗网格
（一个立方体就 8 个点）根本长不出东西；这里改成**按面积加权的表面随机取样**，同一个
网格不论是 8 个顶点还是 10 万个顶点，吸收点的密度都一样，跟拓扑无关。

没选目标网格时由 :func:`default_target` 兜底：造一只（或复用已有的）细分过的猴子，
装好插件就能直接「构建生长」，不用先自己搭一个输入。

支持两种取几何的方式（和 ``gen_celtic`` / ``sim_dendrite`` 一致）：

* 物体模式：取**求值后**的网格（修改器、几何节点都算数）；
* 编辑模式：取 BMesh 里的当前顶点（一边拉点一边看生长）。

:class:`Surface` 同时是求解器要的「表面约束」（鸭子类型，见
:meth:`sim_space_colonization.solver.SpaceColonization.step`）：``normals_at`` 给法向、
``snap`` 给最近表面点。BVH 建在**世界空间**，所以求解器里不需要再做任何矩阵变换。
"""

from __future__ import annotations

import bmesh
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

__all__ = [
    "DEFAULT_ROOT_GROUP",
    "DEFAULT_TARGET_NAME",
    "Surface",
    "default_target",
    "load",
    "lowest_vertex_index",
    "root_group_name",
    "roots",
    "vertices_local_min_z",
]

#: 根顶点组的缺省名字（有它就用组里的顶点当根，没有就用最低点）。
DEFAULT_ROOT_GROUP = "root"

#: 缺省输入网格的名字（没有目标网格时自动建；同名对象已存在就复用它）。
DEFAULT_TARGET_NAME = "Colonization Target"


def default_target(context=None):
    """缺省输入网格：没选目标时用的那只「细分过的猴子」。

    参考实现同样是「点开就有一只 subdivided Suzanne」。名字固定成
    :data:`DEFAULT_TARGET_NAME`：同名网格已存在就复用，用户改过它也不会被覆盖。

    细分走 SUBSURF 修改器再烘焙成网格（Catmull-Clark，全四边形）。**不用**
    ``bmesh.ops.subdivide_edges``：它在猴子的耳朵、眼窝上会切出硬折面，看着像被捏皱了。
    """
    existing = bpy.data.objects.get(DEFAULT_TARGET_NAME)
    if existing is not None and existing.type == "MESH":
        return existing

    mesh = bpy.data.meshes.new(DEFAULT_TARGET_NAME)
    bm = bmesh.new()
    try:
        bmesh.ops.create_monkey(bm)
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        bm.to_mesh(mesh)
    finally:
        bm.free()

    context = context or bpy.context
    scene = getattr(context, "scene", None) or bpy.context.scene
    collection = getattr(context, "collection", None) or scene.collection
    target = bpy.data.objects.new(DEFAULT_TARGET_NAME, mesh)
    collection.objects.link(target)

    try:
        # 原始猴子只有几百个面，吸收点会挤在少数几个大三角形里 —— 细分一级正好
        modifier = target.modifiers.new("Subdivision", "SUBSURF")
        modifier.levels = 1
        modifier.render_levels = 1
        view_layer = getattr(context, "view_layer", None) or bpy.context.view_layer
        view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        baked = bpy.data.meshes.new_from_object(target.evaluated_get(depsgraph))
        target.modifiers.remove(modifier)
        target.data = baked
        bpy.data.meshes.remove(mesh)
        mesh = baked
        mesh.name = DEFAULT_TARGET_NAME  # 旧网格腾出来的名字，别留成 .001
    except Exception:  # 拿不到求值网格就退回低模猴子：点少一些，但照样能长
        for modifier in list(target.modifiers):
            target.modifiers.remove(modifier)

    for polygon in mesh.polygons:
        polygon.use_smooth = True
    mesh.update()
    return target


def _world_vertices(obj) -> np.ndarray:
    """物体 / 编辑模式下取顶点坐标（世界空间）。"""
    matrix = np.array(obj.matrix_world, dtype=np.float64)
    rotation = matrix[:3, :3]
    translation = matrix[:3, 3]

    if obj.mode == "EDIT":
        bm = bmesh.from_edit_mesh(obj.data)
        count = len(bm.verts)
        if count == 0:
            return np.zeros((0, 3), np.float32)
        local = np.array([vertex.co for vertex in bm.verts], dtype=np.float64)
    else:
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        temp = evaluated.to_mesh()
        try:
            count = len(temp.vertices)
            if count == 0:
                return np.zeros((0, 3), np.float32)
            flat = np.empty(count * 3, dtype=np.float64)
            temp.vertices.foreach_get("co", flat)
            local = flat.reshape(count, 3)
        finally:
            evaluated.to_mesh_clear()

    return (local @ rotation.T + translation).astype(np.float32)


def _world_triangles(obj) -> np.ndarray:
    """三角化后的面索引（顶点顺序与 :func:`_world_vertices` 一致）。"""
    if obj.mode == "EDIT":
        # 拷一份再三角化：直接改编辑模式的 BMesh 等于把用户的网格改了
        bm = bmesh.from_edit_mesh(obj.data).copy()
        if len(bm.verts) == 0:
            return np.zeros((0, 3), np.int32)
        bm.faces.ensure_lookup_table()
        bmesh.ops.triangulate(bm, faces=list(bm.faces), quad_method="BEAUTY")
        bm.verts.index_update()
        triangles = np.array(
            [[vertex.index for vertex in face.verts] for face in bm.faces],
            dtype=np.int32,
        )
        bm.free()
        return triangles

    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    temp = evaluated.to_mesh()
    try:
        temp.calc_loop_triangles()
        count = len(temp.loop_triangles)
        if count == 0:
            return np.zeros((0, 3), np.int32)
        indices = np.empty(count * 3, dtype=np.int32)
        temp.loop_triangles.foreach_get("vertices", indices)
        return indices.reshape(count, 3)
    finally:
        evaluated.to_mesh_clear()


class Surface:
    """输入网格的**世界空间**三角化 + BVH：取样、求法向、贴回表面。"""

    def __init__(self, vertices: np.ndarray, triangles: np.ndarray):
        self.vertices = np.ascontiguousarray(vertices, np.float32).reshape(-1, 3)
        self.triangles = np.ascontiguousarray(triangles, np.int32).reshape(-1, 3)
        self.bvh = None
        if len(self.vertices) and len(self.triangles):
            self.bvh = BVHTree.FromPolygons(
                self.vertices.tolist(),
                self.triangles.tolist(),
                all_triangles=True,
            )

        if len(self.vertices):
            low = self.vertices.min(axis=0)
            high = self.vertices.max(axis=0)
            self.low = low.astype(np.float32)
            self.high = high.astype(np.float32)
            self.center = ((low + high) * 0.5).astype(np.float32)
            #: 包围盒对角线长度：面板上所有长度参数都按它换算成世界单位
            self.unit = float(np.linalg.norm(high - low)) or 1.0
        else:
            self.low = np.zeros(3, np.float32)
            self.high = np.zeros(3, np.float32)
            self.center = np.zeros(3, np.float32)
            self.unit = 1.0

    @property
    def usable(self) -> bool:
        return self.bvh is not None and len(self.triangles) > 0

    def areas(self) -> np.ndarray:
        """每个三角形的面积（取样权重）。"""
        a = self.vertices[self.triangles[:, 0]]
        b = self.vertices[self.triangles[:, 1]]
        c = self.vertices[self.triangles[:, 2]]
        return 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)

    def sample(self, count: int, seed: int = 42) -> np.ndarray:
        """按面积加权在表面上撒 ``count`` 个吸收点。

        退化网格（全是零面积三角形，比如只剩顶点 / 边）直接退回顶点取样，不会返回空。
        """
        count = max(0, int(count))
        if count == 0 or len(self.vertices) == 0:
            return np.zeros((0, 3), np.float32)

        if len(self.triangles) == 0:
            return self._sample_vertices(count, seed)

        areas = self.areas()
        total = float(areas.sum())
        if total <= 1e-12:
            return self._sample_vertices(count, seed)

        rng = np.random.default_rng(int(seed))
        cdf = np.cumsum(areas) / total
        which = np.searchsorted(cdf, rng.random(count))
        which = np.clip(which, 0, len(self.triangles) - 1)

        triangle = self.triangles[which]
        a = self.vertices[triangle[:, 0]]
        b = self.vertices[triangle[:, 1]]
        c = self.vertices[triangle[:, 2]]

        # 重心坐标（Osada 的三角均匀分布：u+v>1 时翻到另一半）
        u = rng.random(count).astype(np.float32)
        v = rng.random(count).astype(np.float32)
        flip = u + v > 1.0
        u[flip] = 1.0 - u[flip]
        v[flip] = 1.0 - v[flip]
        return (a + u[:, None] * (b - a) + v[:, None] * (c - a)).astype(np.float32)

    def _sample_vertices(self, count: int, seed: int) -> np.ndarray:
        rng = np.random.default_rng(int(seed))
        index = rng.integers(0, len(self.vertices), size=count)
        return np.array(self.vertices[index], np.float32)

    # --- 表面约束（求解器用） ----------------------------------------------

    def _nearest(self, points) -> tuple[np.ndarray, np.ndarray]:
        """每个点最近的表面点与法向（BVH 没命中时原样返回 / 用 +Z）。"""
        array = np.asarray(points, np.float32).reshape(-1, 3)
        locations = np.array(array, np.float32)
        normals = np.tile(np.array([[0.0, 0.0, 1.0]], np.float32), (len(array), 1))
        if self.bvh is None:
            return locations, normals

        # ``BVHTree.find_nearest`` 返回 ``(location, normal, index, distance)``，
        # 没命中时四项全是 ``None`` —— 没有单独的 found 标志位
        for index, point in enumerate(array):
            location, normal, _face, _distance = self.bvh.find_nearest(
                Vector(point.tolist())
            )
            if location is not None:
                locations[index] = location[:]
                normals[index] = normal[:]
        return locations, normals

    def snap(self, points) -> np.ndarray:
        """把点贴到最近的表面上（求解器的表面约束）。"""
        return self._nearest(points)[0]

    def normals_at(self, points) -> np.ndarray:
        """最近表面的法向（求解器用它在切平面里修正生长方向）。"""
        return self._nearest(points)[1]


def load(obj) -> Surface | None:
    """输入对象 → :class:`Surface`；不是网格或没有几何时返回 ``None``。"""
    if obj is None or getattr(obj, "type", None) != "MESH":
        return None
    vertices = _world_vertices(obj)
    if len(vertices) == 0:
        return None
    return Surface(vertices, _world_triangles(obj))


def root_group_name(obj, name: str = DEFAULT_ROOT_GROUP) -> str:
    """实际能用的根顶点组名（对象上没有这个组时返回空串）。"""
    if obj is None or obj.type != "MESH":
        return ""
    if obj.vertex_groups.get(name) is None:
        return ""
    return name


def roots(obj, group: str = DEFAULT_ROOT_GROUP, surface: Surface | None = None):
    """根点（世界空间 ``(R, 3)``）：优先取根顶点组，没有就给最低点。

    顶点组支持多个根（一个组里放几个点，就长出一丛藤蔓）。取到的点会贴回表面并沿法向
    抬一个很小的距离，避免和输入网格本身打架。
    """
    if obj is None or obj.type != "MESH":
        return np.zeros((0, 3), np.float32)

    matrix = np.array(obj.matrix_world, dtype=np.float64)
    rotation = matrix[:3, :3]
    translation = matrix[:3, 3]

    cohort = obj.vertex_groups.get(group)
    points = np.zeros((0, 3), np.float32)
    if cohort is not None:
        indices = []
        for vertex in obj.data.vertices:
            for element in vertex.groups:
                if element.group == cohort.index and element.weight > 0.0:
                    indices.append(vertex.index)
                    break
        if indices:
            local = np.array(
                [obj.data.vertices[index].co for index in indices], dtype=np.float64
            )
            points = (local @ rotation.T + translation).astype(np.float32)

    if len(points) == 0:
        # 没有根组：用最低点（和参考实现一致），贴在表面上当根
        if surface is not None and len(surface.vertices):
            lowest = np.array(surface.vertices, np.float32)
            lowest = lowest[np.argsort(lowest[:, 2])[0]][None, :]
            points = lowest
        else:
            points = vertices_local_min_z(obj, matrix)[None, :].astype(np.float32)

    if surface is not None and surface.usable and len(points):
        points = surface.snap(points)
        points = points + surface.normals_at(points) * (surface.unit * 0.001)
    return points.astype(np.float32)


def lowest_vertex_index(obj) -> int:
    """世界空间 z 最小的顶点索引（没有顶点时返回 −1）。"""
    count = len(obj.data.vertices)
    if count == 0:
        return -1
    flat = np.empty(count * 3, dtype=np.float64)
    obj.data.vertices.foreach_get("co", flat)
    matrix = np.array(obj.matrix_world, dtype=np.float64)
    world = flat.reshape(count, 3) @ matrix[:3, :3].T + matrix[:3, 3]
    return int(np.argmin(world[:, 2]))


def vertices_local_min_z(obj, matrix=None) -> np.ndarray:
    """对象顶点里世界空间 z 最小的那个点（没有可用表面时的兜底）。"""
    if matrix is None:
        matrix = np.array(obj.matrix_world, dtype=np.float64)
    index = lowest_vertex_index(obj)
    if index < 0:
        return np.zeros(3, np.float32)
    return (
        matrix[:3, :3] @ np.array(obj.data.vertices[index].co, np.float64)
        + matrix[:3, 3]
    ).astype(np.float32)
