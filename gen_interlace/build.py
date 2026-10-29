"""编织入口：源 → 折线 → 交叉 → 一上一下 → 可见片段 → GP 对象。

算子（手动「生成」）与实时处理器（:mod:`live`）走的是同一条路：先比指纹，都没变就直接
返回，所以处理器和 timer 可以放心地重复调用。
"""

from __future__ import annotations

import time
import zlib

import bpy

from . import gp, interlace, strands
from .props import DESIGN_KEYS, GEOMETRY_FIELDS, store_design

__all__ = [
    "SEGMENT_LIMIT",
    "apply",
    "params_key",
    "scene_frame",
    "unchanged",
]

#: 一次编织最多处理多少条线段：交叉检测的最坏情况是 O(n²)，再多会把界面卡死。
SEGMENT_LIMIT = 40000


def scene_frame() -> int:
    """当前帧（生成物的图层每轮都挪到这一帧上）。"""
    scene = getattr(bpy.context, "scene", None)
    return int(scene.frame_current) if scene is not None else 1


def params_key(settings) -> int:
    """几何参数的指纹：参数一动就变（种子一点没动时靠它触发重编）。

    **只留 31 位**：这个数要放进 Blender 的自定义属性（那边的整数是 C 的 ``int``）。
    """
    text = "|".join(repr(getattr(settings, name)) for name in GEOMETRY_FIELDS)
    return zlib.crc32(text.encode("utf-8")) & 0x7FFFFFFF


def unchanged(settings, source, key: int) -> bool:
    """指纹都没变就不用重编：源几何、参数、源物体名三样都对得上才算没变。"""
    obj = gp.find_object()
    if obj is None:
        return False
    if settings.dirty:
        return False
    if int(obj.get(DESIGN_KEYS["geometry"], 0)) != int(key):
        return False
    if int(obj.get(DESIGN_KEYS["params"], 0)) != params_key(settings):
        return False
    return str(obj.get(DESIGN_KEYS["source"], "")) == source.name


def apply(settings, force: bool = False):
    """编一次，返回 ``(stats, 是否真的重编了)``；出错抛 ``ValueError``。"""
    source = settings.source
    if not strands.is_source(source):
        raise ValueError("还没有股线源：选一个曲线 / GP 物体，或者点右边的骰子撒一把")

    found = strands.polylines(source, float(settings.step))
    if not found:
        raise ValueError("源上没有股线：曲线要有样条、GP 要有笔划")

    segments = sum(
        max(len(points) - 1 + (1 if closed else 0), 0) for points, closed in found
    )
    key = strands.signature(found)
    if not force and unchanged(settings, source, key):
        return _from_settings(settings), False
    if segments > SEGMENT_LIMIT:
        raise ValueError(
            f"线段太多（{segments} > {SEGMENT_LIMIT}）：调大「采样步长」或减少股线"
        )

    started = time.perf_counter()
    body = [
        interlace.Strand(index, points, closed)
        for index, (points, closed) in enumerate(found)
    ]
    crossings = interlace.find_crossings(body)
    weave = interlace.solve_weave(body, crossings, settings.rule)
    # 圆头补不平斜缺口：平贴模式下强制平头（面板上也会跟着提示）
    round_caps = bool(settings.round_caps) and not settings.tuck
    pieces, cut = interlace.cut_pieces(
        body,
        crossings,
        float(settings.width),
        min_sin=float(settings.min_sin),
        air=float(settings.air),
        round_caps=round_caps,
        tuck=bool(settings.tuck),
        seam=float(settings.seam),
    )
    if not pieces:
        raise ValueError("一条可见片段都没剩下：带宽太大，或者股线挤在一起了")

    obj = gp.build_object(
        pieces,
        width=float(settings.width),
        colour=tuple(settings.colour),
        opacity=float(settings.opacity),
        round_caps=round_caps,
        frame=scene_frame(),
    )
    elapsed = (time.perf_counter() - started) * 1000.0

    store_design(obj, settings)
    obj[DESIGN_KEYS["geometry"]] = key
    obj[DESIGN_KEYS["params"]] = params_key(settings)
    obj[DESIGN_KEYS["source"]] = source.name
    obj["gen_interlace_strands"] = len(body)
    obj["gen_interlace_crossings"] = len(crossings)

    stats = {
        "strands": len(body),
        "segments": segments,
        "crossings": len(crossings),
        "pieces": cut["pieces"],
        "patches": cut["patches"],
        "pruned": cut["pruned"],
        "points": cut["points"],
        "relaxed": weave.get("relaxed", 0),
        "ms": elapsed,
    }
    _announce(settings, stats, source)
    return stats, True


def _announce(settings, stats: dict, source) -> None:
    """把这一次的结果写进面板读数的字段里。"""
    settings.error = ""
    settings.dirty = False
    settings.last_strands = int(stats["strands"])
    settings.last_crossings = int(stats["crossings"])
    settings.last_pieces = int(stats["pieces"])
    settings.last_patches = int(stats.get("patches", 0))
    settings.last_points = int(stats["points"])
    settings.last_relaxed = int(stats["relaxed"])
    settings.last_ms = float(stats["ms"])

    notes = [f"{stats['crossings']} 个交叉 · {stats['pieces']} 截"]
    if stats.get("patches"):
        notes.append(f"{stats['patches']} 块补片")
    if stats.get("pruned"):
        notes.append(f"收回 {stats['pruned']} 刀（残段太短）")
    if stats["relaxed"]:
        notes.append(f"放宽 {stats['relaxed']} 处（严格交替无解）")
    approximated = strands.approximated(source)
    if approximated:
        notes.append(f"{approximated} 条 NURBS 用控制点近似")
    settings.last_note = " · ".join(notes)


def _from_settings(settings) -> dict:
    """没重编时把上次的读数原样交回去（调用方要的只是 stats）。"""
    return {
        "strands": settings.last_strands,
        "crossings": settings.last_crossings,
        "pieces": settings.last_pieces,
        "patches": settings.last_patches,
        "pruned": 0,
        "points": settings.last_points,
        "relaxed": settings.last_relaxed,
        "ms": settings.last_ms,
    }
