"""生成入口：种子 → 单元 → GP 对象（算子与实时处理器走同一条路）。

一条链：:func:`seeds.world_points` 取种子 → :func:`seeds.geometry_key` 算指纹 →
没变就原样返回（编辑模式里鼠标一动就报一次依赖图更新，每动一下都整份重算会卡）→
:func:`voronoi.build_cells` 划分 → 裁剪 / 内缩 / 采样 → :func:`gp.build_object` 写对象。

**为什么要两份指纹**：种子指纹只管「源几何变没变」，可参数（半径 / 间距 / 形状……）
变了同样要重算，而那时种子一点没动。所以生成对象上另存一个参数指纹，两者任一变了
才重新生成 —— 少一遍是常态。

坐标全在世界空间：单元对象自己不带变换，源物体挪了 / 转了，图案跟着走。
"""

from __future__ import annotations

import time
import zlib

import bpy
from mathutils import Vector

from . import gp, seeds, voronoi
from .props import DESIGN_KEYS, GEOMETRY_FIELDS, store_design

__all__ = [
    "apply",
    "bounds",
    "cells_from_points",
    "params_key",
    "stored_key",
    "unchanged",
]


def _scene_frame() -> int:
    scene = getattr(bpy.context, "scene", None)
    return int(scene.frame_current) if scene is not None else 1


def params_key(settings) -> int:
    """几何参数（不含源物体）的指纹：参数一动，这个数就变（同样只留 31 位）。"""
    parts = []
    for name in GEOMETRY_FIELDS:
        value = getattr(settings, name)
        parts.append(value if isinstance(value, (bool, int, float)) else str(value))
    return int(zlib.crc32(repr(parts).encode("utf-8"))) & 0x7FFFFFFF


def stored_key(obj, key: str):
    """读生成对象上存的指纹（自定义属性）；没有就返回 ``None``。"""
    if obj is None or key not in obj:
        return None
    try:
        return int(obj[key])
    except (TypeError, ValueError):
        return None


def unchanged(obj, source, settings, geometry: int, params: int) -> bool:
    """生成对象上的记录和当前输入一致？一致就不必重算。"""
    if obj is None:
        return False
    if stored_key(obj, DESIGN_KEYS["geometry"]) != geometry:
        return False
    if stored_key(obj, DESIGN_KEYS["params"]) != params:
        return False
    return str(obj.get(DESIGN_KEYS["source"], "")) == source.name


def bounds(points, settings):
    """组织边界盒（``low``, ``high``）；不裁剪时返回 ``(None, None)``。

    ``AUTO`` 取种子包围盒外扩 ``clip_margin`` 倍对角线，``FIXED`` 取面板给的宽高
    （以原点为中心，跟参考实现一致）。
    """
    boundary = settings.boundary
    if boundary == "NONE":
        return None, None
    if boundary == "FIXED":
        half_width = float(settings.boundary_width) * 0.5
        half_height = float(settings.boundary_height) * 0.5
        return Vector((-half_width, -half_height)), Vector((half_width, half_height))

    low = Vector((float(points[:, 0].min()), float(points[:, 1].min())))
    high = Vector((float(points[:, 0].max()), float(points[:, 1].max())))
    diagonal = (high - low).length
    margin = (
        diagonal * float(settings.clip_margin)
        if diagonal > 0.0
        else float(settings.max_radius)
    )
    return low - Vector((margin, margin)), high + Vector((margin, margin))


def cells_from_points(points, settings):
    """种子点 → 单元笔划列表（裁剪 / 内缩 / 采样都在这儿做完）。

    裁剪顺序照参考实现：先按「气泡半径」把每个单元收成圆，再按组织边界裁，
    最后整体内缩 ``spacing`` —— 内缩放在最后，缝宽才处处相等。
    """
    raw = voronoi.build_cells(points)
    low, high = bounds(points, settings)
    bubble = bool(settings.use_max_radius)
    radius = float(settings.max_radius)
    shape = settings.shape
    resolution = int(settings.resolution)
    min_edge = float(settings.min_edge)
    spacing = float(settings.spacing)

    strokes = []
    for index, poly in enumerate(raw):
        if poly is None:
            continue
        seed = Vector((float(points[index][0]), float(points[index][1])))
        poly = voronoi.clean_polygon(poly)
        if len(poly) < 3:
            continue

        # 1) 气泡：单元收到自己种子周围半径 radius 的圆里
        if bubble:
            poly = voronoi.clip_circle(poly, seed, radius)
            if len(poly) < 3:
                continue

        # 2) 组织边界。气泡模式下把边界按种子外扩一圈，圆才不会被远处的边界切平；
        #    严格模式则要求种子自己在框里，框外的单元整个不要（参考实现同此）
        if low is not None:
            if bubble:
                clip_low = Vector(
                    (min(low.x, seed.x - radius), min(low.y, seed.y - radius))
                )
                clip_high = Vector(
                    (max(high.x, seed.x + radius), max(high.y, seed.y + radius))
                )
            else:
                if not (low.x <= seed.x <= high.x and low.y <= seed.y <= high.y):
                    continue
                clip_low, clip_high = low, high
            poly = voronoi.clip_rect(poly, clip_low, clip_high)
            if len(poly) < 3:
                continue

        # 3) 内缩：单元之间留出缝，缝宽处处等于 spacing（种子当「缩没」的判据）
        poly = voronoi.inset_polygon(poly, spacing, seed)
        if poly is None:
            continue

        stroke = voronoi.cell_stroke(
            poly, shape=shape, resolution=resolution, min_edge=min_edge
        )
        if stroke is None:
            continue
        strokes.append(voronoi.Cell(index, float(points[index][2]), stroke))
    return strokes


def apply(settings, collection=None, force: bool = False):
    """按面板参数生成单元并写进场景，返回 ``(stats, 是否重算)``。

    指纹都没变（且没强制）时返回 ``(None, False)`` —— 实时处理器就靠这条省下重算。
    输入不合法直接抛 ``ValueError``，由调用方（算子 / 实时处理器）收成报告。
    """
    source = settings.source
    if source is None:
        raise ValueError("还没有源物体：选一个网格，或者点「撒点」建一份 GP 种子")
    if source.type not in seeds.SOURCE_TYPES:
        raise ValueError(f"源只能是网格或 Grease Pencil，现在是 {source.type!r}")

    obj = gp.find_object()
    if obj is not None and source == obj:
        raise ValueError("源不能是生成物自己，换一个物体")

    matrix = seeds.matrix_flat(source)
    points = seeds.world_points(source)
    if not len(points):
        raise ValueError("源物体没有点：网格没有顶点 / GP 里没有笔划")

    geometry = seeds.geometry_key(points, matrix)
    params = params_key(settings)
    if not force and unchanged(obj, source, settings, geometry, params):
        settings.dirty = False
        return None, False

    started = time.perf_counter()
    cells = cells_from_points(points, settings)
    if not cells:
        raise ValueError("一个单元都没剩下：种子重合，或裁剪 / 间距把单元吃光了")

    fill = tuple(settings.fill_colour) if settings.fill else None
    obj = gp.build_object(
        cells,
        width=float(settings.width),
        colour=tuple(settings.colour),
        fill=fill,
        fill_alpha=float(settings.fill_alpha),
        frame=_scene_frame(),
        collection=collection,
    )
    elapsed = (time.perf_counter() - started) * 1000.0

    obj[DESIGN_KEYS["source"]] = source.name
    obj[DESIGN_KEYS["geometry"]] = geometry
    obj[DESIGN_KEYS["params"]] = params
    obj["gen_voronoi_cells"] = len(cells)
    obj["gen_voronoi_points"] = int(sum(len(cell.points) for cell in cells))
    store_design(obj, settings)

    stats = {
        "cells": len(cells),
        "points": int(sum(len(cell.points) for cell in cells)),
        "seeds": len(points),
        "dropped": len(points) - len(cells),
        "ms": elapsed,
    }
    settings.error = ""
    settings.dirty = False
    settings.last_cells = int(stats["cells"])
    settings.last_points = int(stats["points"])
    settings.last_ms = elapsed
    # 种子数比单元多 = 有单元被丢掉了（种子重合 / 被边界裁光 / 内缩过头）
    settings.last_note = (
        f"{stats['seeds']} 个种子，丢掉 {stats['dropped']} 个单元"
        if stats["dropped"]
        else f"{stats['seeds']} 个种子"
    )
    return stats, True
