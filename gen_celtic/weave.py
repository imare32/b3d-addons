"""编结：源网格 → 凯尔特结 → 场景对象（算子与实时处理器共用的唯一入口）。

模块名不叫 ``build``：``knot.build`` 已经是算法层的函数，两者重名会让
``from . import build`` 拿到函数而不是模块。

一条链：:func:`source_geometry` 取源几何 → :func:`knot.signature` 对指纹 → 没变就
原样返回（编辑模式里鼠标一动就报网格更新，每次重编会卡）→ :func:`knot.build` 编结 →
:func:`tube.build_object` 写对象 → 输入参数记回对象（换文件 / 换场景后还能
:func:`ops.sync_from_object` 取回来）。

**源几何取的是求值后的那一份**：源物体上挂的修改器（含几何节点）跑完的结果才是要编的
东西，原始数据块只是修改器的输入。所以非编辑模式一律走
``source.evaluated_get(depsgraph).to_mesh()``；修改器参数一动，求值几何就变，指纹跟着变，
结自然重编。编辑模式是唯一的例外：那时求值几何是空的（见下）。

**世界坐标**：结对象自己不带变换，所以点一律先乘 ``source.matrix_world`` 再编 —— 源物体
挪了 / 转了 / 缩放了，结都长在看得见的那个位置上。物体的变换也进指纹（
:func:`unchanged` 里比 :func:`matrix_flat`），否则拖动物体不会触发重编。

**编辑模式的坐标从哪来**（5.2 实测，都不是直觉里的那一个）：

* ``obj.data`` 还是进编辑模式那一刻的坐标 —— 拖动不写回网格数据块；
* 求值网格（``obj.evaluated_get(depsgraph).data``）在编辑模式下**是空的**；
* 只有 ``bmesh.from_edit_mesh(obj.data)`` 拿得到正在拖的坐标。

所以编辑模式走编辑 BMesh（借来只读，指纹在它上面算；真要编时复制一份再变换），
其余情况复制求值网格。
"""

from __future__ import annotations

import time

import bmesh
import bpy

from . import knot, tube
from .props import DESIGN_KEYS

__all__ = [
    "apply",
    "edit_bmesh",
    "matrix_flat",
    "read_bmesh",
    "source_face_count",
    "stored_signature",
    "unchanged",
]


def edit_bmesh(obj):
    """编辑模式的编辑 BMesh；不在编辑模式返回 ``None``。

    借来的东西：属于 Blender，不能 ``free()``，也不要在上面改动几何。
    """
    if obj.mode == "EDIT" and obj.data.is_editmode:
        return bmesh.from_edit_mesh(obj.data)
    return None


def read_bmesh(mesh):
    """网格数据块 → ``BMesh``（调用方负责 ``free()``）。"""
    bm = bmesh.new()
    bm.from_mesh(mesh)
    return bm


def matrix_flat(obj):
    """对象的世界变换 → 16 个数的一行（存进自定义属性、也直接拿来比）。

    不用 ``Matrix`` 对象当键：自定义属性只认数值 / 字符串，比对时也不用每次建对象。
    """
    return [float(value) for row in obj.matrix_world for value in row]


def source_face_count(obj, depsgraph=None) -> int:
    """源物体**求值后**（修改器跑完）的面数；求值结果不是网格时返回 ``0``。

    实时更新与面板都按它判断规模：加了细分 / 几何节点的网格，规模按跑完的算，不是按
    原始数据块算（那份可能只有原始网格的零头）。
    """
    if depsgraph is None:
        depsgraph = bpy.context.evaluated_depsgraph_get()
    data = obj.evaluated_get(depsgraph).data
    return len(data.polygons) if isinstance(data, bpy.types.Mesh) else 0


def stored_signature(obj):
    """对象上记的指纹 → ``tuple``（自定义属性是数组，读回来是 list）。"""
    values = obj.get(DESIGN_KEYS["signature"])
    if values is None or len(values) != 4:
        return None
    return (int(values[0]), int(values[1]), int(values[2]), float(values[3]))


def _same_matrix(obj, matrix) -> bool:
    """生成对象上记的世界变换和当前的一致？

    结是按世界坐标编的，源物体挪一下坐标就全变了 —— 变换也必须进指纹，
    不然拖着物体走，结会留在原地。
    """
    stored = obj.get(DESIGN_KEYS["matrix"])
    if stored is None or len(stored) != 16 or len(matrix) != 16:
        return False
    return max(abs(float(a) - float(b)) for a, b in zip(stored, matrix)) < 1e-6


def unchanged(obj, source, settings, signature, matrix) -> bool:
    """生成对象上的记录和当前输入一致？一致就不必重编。"""
    if obj is None:
        return False
    if stored_signature(obj) != signature:
        return False
    if str(obj.get(DESIGN_KEYS["source"], "")) != source.name:
        return False
    if not _same_matrix(obj, matrix):
        return False
    return abs(float(obj.get(DESIGN_KEYS["lift"], -1.0)) - float(settings.lift)) < 1e-6


def apply(settings, collection=None, force=False):
    """按面板参数把源网格编成结并写进场景，返回 ``(knot, 是否重编)``。

    指纹没变（且没强制）时返回 ``(None, False)`` —— 实时处理器就靠这条省下重编。
    输入不合法直接抛 ``ValueError``，由调用方（算子 / 实时处理器）收成报告。
    """
    source = settings.source
    if source is None:
        raise ValueError("no source object; pick a mesh in the panel")
    if source.type != "MESH":
        raise ValueError(f"source must be a mesh object, got {source.type!r}")

    obj = tube.find_object()
    if obj is not None and source == obj:
        raise ValueError("source is the generated knot itself; pick another object")

    matrix = matrix_flat(source)
    borrowed = edit_bmesh(source)
    evaluated = None
    mesh = None

    if borrowed is not None:
        # 编辑模式：拖动中的坐标只在编辑 BMesh 里，指纹也在它上面算（借的，只读）
        if not len(borrowed.faces):
            raise ValueError("source mesh has no faces; a knot needs faces to weave")
        signature = knot.signature(borrowed)
    else:
        # 求值几何：修改器 / 几何节点跑完的那一份，原始数据块只是它们的输入
        evaluated = source.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        if not len(mesh.polygons):
            evaluated.to_mesh_clear()
            raise ValueError(
                "source geometry has no faces (after modifiers); "
                "a knot needs faces to weave"
            )
        signature = knot.mesh_signature(mesh)

    if not force and unchanged(obj, source, settings, signature, matrix):
        if evaluated is not None:
            evaluated.to_mesh_clear()
        settings.dirty = False
        return None, False

    # 指纹先查（不复制），确实变了才要一份能随便动的 BMesh：编辑 BMesh 是借来的，
    # 求值网格是 to_mesh() 借来的，两者都不能直接把世界变换写进去
    started = time.perf_counter()
    try:
        bm = borrowed.copy() if borrowed is not None else read_bmesh(mesh)
    finally:
        if evaluated is not None:
            evaluated.to_mesh_clear()

    try:
        # 世界坐标：抬升、半径、后续都在同一个空间里（结对象自己是单位变换）
        bm.transform(source.matrix_world)
        # 粗细与起伏都按平均边长缩放：换尺度、换网格密度都不用重调参数
        mean = knot.mean_edge_length(bm)
        strands = knot.build(bm, lift=mean * float(settings.lift))
    finally:
        bm.free()

    if not strands.paths:
        # 兜底：源网格明明有面却一个点都编不出来，说明读到的是半截几何（模式切换 /
        # 编辑刷新中途）。此时宁可报错也别写空曲线 —— 写下去指纹也就存成空的了，
        # 之后每次比对都「没变」，对象再也长不回来。
        raise ValueError("weave produced no points; the source mesh may still be updating")

    radius = mean * float(settings.radius)
    obj = tube.build_object(
        strands.paths,
        radius=radius,
        resolution=int(settings.resolution),
        spline_resolution=int(settings.spline_resolution),
        collection=collection,
        color=tuple(settings.material_color),
    )
    elapsed = (time.perf_counter() - started) * 1000.0

    obj[DESIGN_KEYS["source"]] = source.name
    obj[DESIGN_KEYS["lift"]] = float(settings.lift)
    obj[DESIGN_KEYS["radius"]] = radius
    obj[DESIGN_KEYS["resolution"]] = int(settings.resolution)
    obj[DESIGN_KEYS["spline_resolution"]] = int(settings.spline_resolution)
    obj[DESIGN_KEYS["signature"]] = [float(value) for value in signature]
    obj[DESIGN_KEYS["matrix"]] = matrix
    obj[tube.MEAN_KEY] = mean

    stats = strands.stats
    settings.error = ""
    settings.dirty = False
    settings.last_points = int(stats["points"])
    settings.last_strands = int(stats["strands"])
    settings.last_broken = int(stats["broken"])
    settings.last_ms = elapsed
    settings.last_note = (
        f"{stats['strands']} 条带子 · 带长 {stats['shortest']}–{stats['longest']}"
        f"（中位 {stats['median']}）"
    )
    return strands, True
