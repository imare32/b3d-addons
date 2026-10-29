"""种子提取：源物体（网格 / Grease Pencil）→ 世界坐标点列。

源物体支持两种（面板上都能选）：

* **网格** —— 每个顶点一个种子。物体模式读的是**求值后**的几何（修改器 / 几何节点
  跑完的结果），编辑模式只能读裸网格（见下）；
* **Grease Pencil** —— 每层笔划的每个点一个种子。这是「在 GP 编辑模式里画点、单元
  实时长出来」那条路。

**编辑模型的坐标从哪来**（5.2.2 实测，都不是直觉里的那一个）：

* 网格编辑模式：``obj.data`` 还是进编辑模式那一刻的旧坐标，求值网格在编辑模式里是
  **空的**，正在拖的坐标只有 ``bmesh.from_edit_mesh(obj.data)`` 拿得到；
* GP 编辑模式：编辑的就是**同一份** drawing 数据（往 ``position`` 属性写值当场生效，
  退出编辑模式也还在），所以直接读 ``layer.frames[].drawing`` 的属性就行，不需要
  另找「编辑数据」。

点一律先乘 ``matrix_world`` 再交出去：单元对象自己不带变换，源物体挪了 / 转了 /
缩放了，图案都长在看得见的那个位置上。
"""

from __future__ import annotations

import zlib

import bmesh
import bpy
import numpy as np

__all__ = [
    "GREASEPENCIL",
    "MESH",
    "SOURCE_TYPES",
    "count",
    "geometry_key",
    "is_source",
    "matrix_flat",
    "world_points",
]

MESH = "MESH"
GREASEPENCIL = "GREASEPENCIL"

#: 能当种子的物体类型。
SOURCE_TYPES = (MESH, GREASEPENCIL)

_EMPTY = np.empty((0, 3), dtype=np.float64)


def is_source(obj) -> bool:
    """这个物体能不能当种子源。"""
    return obj is not None and obj.type in SOURCE_TYPES


def matrix_flat(obj) -> list[float]:
    """对象的世界变换 → 16 个数（进指纹，也存到生成对象上）。"""
    return [float(value) for row in obj.matrix_world for value in row]


def world_points(obj, frame: int | None = None) -> np.ndarray:
    """源物体此刻的世界坐标点列，``(n, 3)`` 的 float64（编辑模式里也是活的）。"""
    local = _local_points(obj, frame)
    if not len(local):
        return _EMPTY
    matrix = np.array(obj.matrix_world, dtype=np.float64)
    return local @ matrix[:3, :3].T + matrix[:3, 3]


def count(obj, frame: int | None = None) -> int:
    """种子个数（便宜的那一份：不复制坐标，只给实时更新做规模判断）。"""
    if obj is None:
        return 0
    if obj.type == MESH:
        if obj.mode == "EDIT" and obj.data.is_editmode:
            return len(bmesh.from_edit_mesh(obj.data).verts)
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        data = evaluated.data
        return len(data.vertices) if isinstance(data, bpy.types.Mesh) else 0
    if obj.type == GREASEPENCIL:
        total = 0
        for layer in obj.data.layers:
            drawing = _drawing_at(layer, _frame_number(frame))
            if drawing is None:
                continue
            attribute = drawing.attributes.get("position")
            if attribute is not None:
                total += len(attribute.data)
        return total
    return 0


def geometry_key(points: np.ndarray, matrix=None) -> int:
    """点列 + 世界变换的指纹（crc32）：拖动 / 增删点 / 挪物体都会变。

    比「坐标求和」严：求和几乎不可能区分开两套不同的点，crc32 是逐字节比对。
    只存一个整数，生成对象上跑一趟比对是 O(点数)，实时更新靠它提前退出。

    **只留 31 位**：这个数要放进 Blender 的自定义属性，而那边的整数是 C 的 ``int``
    （实测写 2³² 以上的值会抛 ``Python int too large to convert to C int``）。
    """
    digest = zlib.crc32(np.ascontiguousarray(points, dtype=np.float64).tobytes())
    if matrix is not None:
        digest = zlib.crc32(np.asarray(matrix, dtype=np.float64).tobytes(), digest)
    return int(digest) & 0x7FFFFFFF


# --- 各类型的取点 ---------------------------------------------------------


def _local_points(obj, frame) -> np.ndarray:
    if obj.type == MESH:
        return _mesh_points(obj)
    if obj.type == GREASEPENCIL:
        return _gp_points(obj, frame)
    raise ValueError(f"unsupported source type {obj.type!r}")


def _mesh_points(obj) -> np.ndarray:
    if obj.mode == "EDIT" and obj.data.is_editmode:
        # 借来的 BMesh：只读，不能 free()，不能往上写
        verts = bmesh.from_edit_mesh(obj.data).verts
        if not len(verts):
            return _EMPTY
        return np.array([vert.co[:] for vert in verts], dtype=np.float64)

    # 求值几何：修改器 / 几何节点跑完的那一份，原始数据块只是它们的输入
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        total = len(mesh.vertices)
        if not total:
            return _EMPTY
        coords = np.empty(total * 3, dtype=np.float64)
        mesh.vertices.foreach_get("co", coords)
        return coords.reshape(-1, 3)
    finally:
        evaluated.to_mesh_clear()


def _gp_points(obj, frame) -> np.ndarray:
    number = _frame_number(frame)
    chunks = []
    for layer in obj.data.layers:
        drawing = _drawing_at(layer, number)
        if drawing is None:
            continue
        attribute = drawing.attributes.get("position")
        if attribute is None or not len(attribute.data):
            continue
        coords = np.empty(len(attribute.data) * 3, dtype=np.float64)
        attribute.data.foreach_get("vector", coords)
        chunks.append(coords.reshape(-1, 3))
    if not chunks:
        return _EMPTY
    return np.concatenate(chunks) if len(chunks) > 1 else chunks[0]


def _frame_number(frame) -> int:
    if frame is not None:
        return int(frame)
    scene = getattr(bpy.context, "scene", None)
    return int(scene.frame_current) if scene is not None else 1


def _drawing_at(layer, number: int):
    """图层在 ``number`` 帧上显示的那张 drawing（GP 取「不晚于当前帧的最后一帧」）。"""
    chosen = None
    for item in layer.frames:
        if item.frame_number <= number and (
            chosen is None or item.frame_number >= chosen.frame_number
        ):
            chosen = item
    if chosen is None and len(layer.frames):
        chosen = layer.frames[0]
    return None if chosen is None else chosen.drawing
