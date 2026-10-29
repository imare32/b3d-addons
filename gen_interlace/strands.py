"""股线来源：曲线物体的样条 / Grease Pencil 的笔划 → 折线（世界坐标）。

两种源都能用，面板上直接选：

* **曲线（Curve）** —— 每条样条一条股线。``POLY`` 点原样用；``BEZIER`` 按「采样步长」
  自己细分（不借 Blender 的求值网格，因为编辑模式里求值数据是旧的）；
  ``NURBS`` 只用控制点近似（``stats['approximated']`` 会记一笔，面板上也有提示）；
* **Grease Pencil** —— 每层每一帧里的每一笔一条股线，``cyclic`` 当闭合。这跟
  ``gen_voronoi`` 是同一个套路：GP 编辑模式编辑的就是同一份 drawing 数据，所以画一笔、
  拖一下，编织跟着变。

点一律先乘 ``matrix_world`` 再交出去：股线对象自己不带变换，源物体挪了 / 转了 / 缩放了，
图案都长在看得见的那个位置上。

**曲线的编辑模式也是活的**（5.2.2 实测）：曲线不像网格那样另开一份 BMesh，编辑模式改的
就是 ``obj.data.splines`` 本身，所以直接读样条就行，不需要 ``bmesh.from_edit_mesh``。
"""

from __future__ import annotations

import math
import zlib

import bpy
import numpy as np

__all__ = [
    "CURVE",
    "GREASEPENCIL",
    "SOURCE_TYPES",
    "approximated",
    "count",
    "is_source",
    "polylines",
    "signature",
]

CURVE = "CURVE"
GREASEPENCIL = "GREASEPENCIL"

#: 能当股线源的物体类型。
SOURCE_TYPES = (CURVE, GREASEPENCIL)

_EMPTY3 = np.empty((0, 3), dtype=np.float64)
#: 一段贝塞尔最多细分多少步（再密就只是浪费点数）。
_MAX_STEPS = 64


def is_source(obj) -> bool:
    """这个物体能不能当股线源。"""
    return obj is not None and obj.type in SOURCE_TYPES


def polylines(obj, step: float = 0.02, frame: int | None = None):
    """源物体此刻的股线：``[(points (n, 3) float64 世界坐标, 是否闭合), ...]``。"""
    if obj is None:
        return []
    if obj.type == CURVE:
        raw = _curve_polylines(obj.data, step)
    elif obj.type == GREASEPENCIL:
        raw = _gp_polylines(obj, frame)
    else:
        return []

    matrix = np.array(obj.matrix_world, dtype=np.float64)
    rotation, offset = matrix[:3, :3], matrix[:3, 3]
    return [
        (points @ rotation.T + offset, closed)
        for points, closed in raw
        if len(points) >= 2
    ]


def count(obj, frame: int | None = None) -> int:
    """源上有多少条股线（便宜的那份：只看条数，不复制坐标）。"""
    if obj is None:
        return 0
    if obj.type == CURVE:
        return sum(1 for spline in obj.data.splines if _point_count(spline) >= 2)
    if obj.type == GREASEPENCIL:
        total = 0
        for layer in obj.data.layers:
            drawing = _drawing_at(layer, _frame_number(frame))
            if drawing is not None:
                total += len(drawing.strokes)
        return total
    return 0


def approximated(obj) -> int:
    """被「控制点近似」处理的样条条数（只有 ``NURBS`` 会这样，面板上会提示）。"""
    if obj is None or obj.type != CURVE:
        return 0
    return sum(
        1
        for spline in obj.data.splines
        if spline.type == "NURBS" and _point_count(spline) >= 2
    )


def signature(found, matrix=None) -> int:
    """股线几何 + 世界变换的指纹（crc32）：拖点 / 增删笔划 / 挪物体都会变。

    **只留 31 位**：这个数要放进 Blender 的自定义属性，那边的整数是 C 的 ``int``
    （实测写 2³² 以上的值会抛 ``Python int too large to convert to C int``）。
    """
    digest = 0
    for points, closed in found:
        digest = zlib.crc32(
            np.ascontiguousarray(points, dtype=np.float64).tobytes(), digest
        )
        digest = zlib.crc32(b"\x01" if closed else b"\x00", digest)
    if matrix is not None:
        digest = zlib.crc32(np.asarray(matrix, dtype=np.float64).tobytes(), digest)
    return int(digest) & 0x7FFFFFFF


# --- 曲线 -----------------------------------------------------------------


def _point_count(spline) -> int:
    if spline.type == "BEZIER":
        return len(spline.bezier_points)
    return len(spline.points)


def _curve_polylines(data, step: float):
    found = []
    for spline in data.splines:
        if spline.type == "BEZIER":
            points = _bezier_points(spline, step)
        else:
            points = _control_points(spline)
        if len(points) >= 2:
            found.append((points, bool(spline.use_cyclic_u)))
    return found


def _control_points(spline) -> np.ndarray:
    """``POLY`` / ``NURBS`` 的控制点（``NURBS`` 用控制多边形近似，见模块说明）。"""
    total = len(spline.points)
    if not total:
        return _EMPTY3
    coords = np.empty(total * 4, dtype=np.float64)
    spline.points.foreach_get("co", coords)
    return coords.reshape(-1, 4)[:, :3].astype(np.float64)


def _bezier_points(spline, step: float) -> np.ndarray:
    """把贝塞尔样条细分成折线（每段起点留给上一段，末尾补一个收尾点）。"""
    handles = spline.bezier_points
    total = len(handles)
    if total < 2:
        return _EMPTY3

    coords = np.empty(total * 3, dtype=np.float64)
    handles.foreach_get("co", coords)
    coords = coords.reshape(-1, 3)
    left = np.empty(total * 3, dtype=np.float64)
    handles.foreach_get("handle_left", left)
    left = left.reshape(-1, 3)
    right = np.empty(total * 3, dtype=np.float64)
    handles.foreach_get("handle_right", right)
    right = right.reshape(-1, 3)

    segments = total if spline.use_cyclic_u else total - 1
    step = max(float(step), 1e-5)
    chunks = []
    for index in range(segments):
        following = (index + 1) % total
        start, end = coords[index], coords[following]
        out, into = right[index], left[following]
        # 控制多边形长度当弧长上界：比「弦长」更贴近曲线真实长度，弯得厉害时不会太稀
        rough = (
            float(np.linalg.norm(end - start))
            + float(np.linalg.norm(out - start))
            + float(np.linalg.norm(into - end))
        )
        steps = int(min(_MAX_STEPS, max(2, math.ceil(rough / step))))
        t = np.linspace(0.0, 1.0, steps, endpoint=False)[:, None]
        u = 1.0 - t
        chunks.append(
            (u**3) * start
            + (3.0 * u * u * t) * out
            + (3.0 * u * t * t) * into
            + (t**3) * end
        )
    chunks.append(coords[:1] if spline.use_cyclic_u else coords[-1:])
    points = np.vstack(chunks)
    if spline.use_cyclic_u and len(points) > 1:
        points = points[:-1]  # 闭合由 closed 标记表达，别在末尾重复首点
    return points


# --- Grease Pencil --------------------------------------------------------


def _gp_polylines(obj, frame):
    number = _frame_number(frame)
    found = []
    for layer in obj.data.layers:
        drawing = _drawing_at(layer, number)
        if drawing is None or not len(drawing.strokes):
            continue
        attribute = drawing.attributes.get("position")
        if attribute is None or not len(attribute.data):
            continue
        coords = np.empty(len(attribute.data) * 3, dtype=np.float64)
        attribute.data.foreach_get("vector", coords)
        coords = coords.reshape(-1, 3)
        cursor = 0
        for stroke in drawing.strokes:
            size = len(stroke.points)
            if size >= 2:
                found.append(
                    (coords[cursor : cursor + size].copy(), bool(stroke.cyclic))
                )
            cursor += size
    return found


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
