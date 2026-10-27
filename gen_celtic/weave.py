"""编结：源网格 → 凯尔特结 → 场景对象（算子与实时处理器共用的唯一入口）。

模块名不叫 ``build``：``knot.build`` 已经是算法层的函数，两者重名会让
``from . import build`` 拿到函数而不是模块。

一条链：:func:`edit_bmesh` 取源几何 → :func:`knot.signature` 对指纹 → 没变就原样
返回（编辑模式里鼠标一动就报网格更新，每次重编会卡）→ :func:`knot.build` 编结 →
:func:`tube.build_object` 写对象 → 输入参数记回对象（换文件 / 换场景后还能
:func:`ops.sync_from_object` 取回来）。

**编辑模式的坐标从哪来**（5.2 实测，都不是直觉里的那一个）：

* ``obj.data`` 还是进编辑模式那一刻的坐标 —— 拖动不写回网格数据块；
* 求值网格（``obj.evaluated_get(depsgraph).data``）在编辑模式下**是空的**；
* 只有 ``bmesh.from_edit_mesh(obj.data)`` 拿得到正在拖的坐标。

所以编辑模式走编辑 BMesh（借用，不复制也不 free），其余情况才复制网格数据块。
"""

from __future__ import annotations

import time

import bmesh

from . import knot, tube
from .props import DESIGN_KEYS

__all__ = ["apply", "edit_bmesh", "read_bmesh", "stored_signature", "unchanged"]


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


def stored_signature(obj):
    """对象上记的指纹 → ``tuple``（自定义属性是数组，读回来是 list）。"""
    values = obj.get(DESIGN_KEYS["signature"])
    if values is None or len(values) != 4:
        return None
    return (int(values[0]), int(values[1]), int(values[2]), float(values[3]))


def unchanged(obj, source, settings, signature) -> bool:
    """生成对象上的记录和当前输入一致？一致就不必重编。"""
    if obj is None:
        return False
    if stored_signature(obj) != signature:
        return False
    if str(obj.get(DESIGN_KEYS["source"], "")) != source.name:
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

    borrowed = edit_bmesh(source)
    if borrowed is not None:
        # 编辑模式：指纹只能在编辑 BMesh 上算（那里才有拖动中的坐标），好在它是借的
        if not len(borrowed.faces):
            raise ValueError("source mesh has no faces; a knot needs faces to weave")
        signature = knot.signature(borrowed)
    else:
        if not len(source.data.polygons):
            raise ValueError("source mesh has no faces; a knot needs faces to weave")
        signature = knot.mesh_signature(source.data)

    if not force and unchanged(obj, source, settings, signature):
        settings.dirty = False
        return None, False

    # 指纹先查（不复制），确实变了才要一份 BMesh 来编
    bm, borrowing = (borrowed, True) if borrowed is not None else (
        read_bmesh(source.data),
        False,
    )

    started = time.perf_counter()
    try:
        # 粗细与起伏都按平均边长缩放：换尺度、换网格密度都不用重调参数
        mean = knot.mean_edge_length(bm)
        strands = knot.build(bm, lift=mean * float(settings.lift))
    finally:
        if not borrowing:
            bm.free()

    if not strands.verts:
        # 兜底：源网格明明有面却一个点都编不出来，说明读到的是半截几何（模式切换 /
        # 编辑刷新中途）。此时宁可报错也别写空网格 —— 写下去指纹也就存成空的了，
        # 之后每次比对都「没变」，对象再也长不回来。
        raise ValueError("weave produced no points; the source mesh may still be updating")

    radius = mean * float(settings.radius)
    obj = tube.build_object(
        strands.verts,
        strands.edges,
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
    obj[tube.MEAN_KEY] = mean

    stats = strands.stats
    settings.error = ""
    settings.dirty = False
    settings.last_points = int(stats["points"])
    settings.last_strands = int(stats["strands"])
    settings.last_conflicts = int(stats["conflicts"])
    settings.last_ms = elapsed
    settings.last_note = (
        f"{stats['strands']} 条带子 · 带长 {stats['shortest']}–{stats['longest']}"
        f"（中位 {stats['median']}）"
    )
    return strands, True