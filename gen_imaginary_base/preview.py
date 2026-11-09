"""视口预览：``POST_VIEW`` 立即模式画点集与取景框，**不创建任何数据块**。

做法与 `fractal_family` 的 ``preview.py`` 一致：只有想让「改参数 → 立刻看到结果」成立时
才需要它，而它一旦建临时对象，撤销栈和场景里就会多出一堆垃圾。

每个整数画成一整格
    Gaussian 铺方格、Eisenstein 铺六边形 —— 和参考实现的画布一致。早先版本画的是
    屏幕像素大小的 ``POINTS``，远远看过去只是一层灰；铺满格子之后形状才读得出来，
    而且缩放视口时格子跟着几何走，不是固定像素大小。

按数字分组画
    同号的格子颜色相同，于是可以一组一次 ``draw``（每组一条 batch）。96 个数字就是
    96 次绘制调用，比"逐点给色"省事，也不用自己写 shader。

取景框
    Gaussian（``I``）画正方形，Eisenstein（``W``）画六边形 —— 与 HUD 和网页版一致。
    六边形就是三角格的胞腔形状，看一眼就知道"这批点落在哪种格里"。
"""

from __future__ import annotations

import traceback

import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from . import palette, ring
from .hud_layout import frame_polygon

__all__ = ["install", "remove", "tag_redraw"]

#: 取景框线宽（像素）与颜色。中性灰：深浅主题下都看得见。
FRAME_WIDTH = 1.6
FRAME_COLOUR = (0.42, 0.42, 0.45, 0.75)

_HANDLE = None
_HANDLE_KEY = "imaginarybase_preview_handle"
_HANDLE_REGION = "WINDOW"

#: 画好的批次缓存：``key -> {"cells": [...], "frame": batch}``
_BATCHES: dict = {}
_CACHE_LIMIT = 4

#: 只打印一次绘制异常，免得每个重绘都刷屏。
_REPORTED = False


def cell_triangles(domain: str, gap: float = 0.0) -> np.ndarray:
    """单位胞腔拆成三角形，返回角点偏移表 ``(6, 2)`` 或 ``(12, 2)``。

    胞腔形状直接取 :func:`ring.cell_polygon` —— 就是「实心格」输出里那一片瓦片的形状，
    所以预览里看到的格子与生成出来的完全重合（缝隙也一致）。方形是 2 个三角形、
    六边形是 4 个，扇形从第一个角点展开，不额外引入中心点。
    """
    polygon = ring.cell_polygon(domain, gap)
    corners = []
    for index in range(1, len(polygon) - 1):
        corners.extend((polygon[0], polygon[index], polygon[index + 1]))
    return np.array(corners, dtype=np.float32)


def _settings(context=None):
    context = context or bpy.context
    scene = getattr(context, "scene", None)
    return getattr(scene, "imaginarybase", None) if scene is not None else None


def _build_batches(settings):
    """按数字分组，把每个整数铺成**一整格**（方格 / 六边形）。"""
    data = settings.point_data()
    xy = data["xy"]
    if len(xy) == 0:
        return []
    table = cell_triangles(settings.domain, float(settings.cell_gap))
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batches = []
    for slot in np.unique(data["slot"]):
        mask = data["slot"] == slot
        # (n, 12, 2)：每个点展开成一格的角点，z 恒为 0（点集本来就在 XY 平面上）
        cell = xy[mask][:, None, :] + table[None, :, :]
        verts = np.zeros((cell.shape[0], cell.shape[1], 3), dtype=np.float32)
        verts[:, :, :2] = cell
        # 每个 slot 用自己那号线性色（与 GP 输出一致）
        colour = palette.digit_colour_linear(int(slot))
        batches.append(
            (
                batch_for_shader(shader, "TRIS", {"pos": verts.reshape(-1, 3)}),
                (*colour, 1.0),
            )
        )
    return batches


def _frame_batch(settings, data, shader):
    """取景框：包住整个点集，形状随 domain（方形 / 六边形）。"""
    xy = data["xy"]
    if len(xy) == 0:
        return None
    low = xy.min(axis=0)
    high = xy.max(axis=0)
    centre = (low + high) / 2.0
    half = (high - low) / 2.0
    # 留 8% 余量，最近的那一圈点不会压在线上
    pad = float(max(half[0], half[1], 1.0)) * 0.08
    polygon = frame_polygon(
        settings.domain, float(half[0]) + pad, float(half[1]) + pad, "contains"
    )
    points = [(centre[0] + x, centre[1] + y, 0.0) for x, y in polygon]
    points.append(points[0])
    return batch_for_shader(shader, "LINE_STRIP", {"pos": points})


def _batches_for(settings):
    key = (
        settings.point_key(),
        settings.domain,
        round(float(settings.cell_gap), 4),
    )
    cached = _BATCHES.get(key)
    if cached is not None:
        return cached
    data = settings.point_data()
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    built = {
        "cells": _build_batches(settings),
        "frame": _frame_batch(settings, data, shader),
    }
    if len(_BATCHES) >= _CACHE_LIMIT:
        _BATCHES.clear()
    _BATCHES[key] = built
    return built


def _draw():
    global _REPORTED
    settings = _settings()
    if settings is None or not settings.show_preview:
        return
    try:
        batches = _batches_for(settings)
    except Exception:
        if not _REPORTED:
            _REPORTED = True
            print("[gen_imaginary_base] 视口预览构建失败（后续不再重复报告）：")
            traceback.print_exc()
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    gpu.state.blend_set("ALPHA")
    shader.bind()
    try:
        # 先铺格子、后描框：框压在格子上面才看得见
        for batch, colour in batches.get("cells") or ():
            shader.uniform_float("color", colour)
            batch.draw(shader)
        frame = batches.get("frame")
        if frame is not None:
            gpu.state.line_width_set(FRAME_WIDTH)
            shader.uniform_float("color", FRAME_COLOUR)
            frame.draw(shader)
    except Exception:
        if not _REPORTED:
            _REPORTED = True
            print("[gen_imaginary_base] 视口预览绘制失败（后续不再重复报告）：")
            traceback.print_exc()
    finally:
        gpu.state.line_width_set(1.0)
        gpu.state.blend_set("NONE")


def tag_redraw() -> None:
    """请求重绘所有 3D 视图。"""
    _BATCHES.clear()
    window = getattr(bpy.context, "window", None)
    screen = getattr(window, "screen", None) if window is not None else None
    areas = (
        []
        if screen is None
        else [area for area in screen.areas if area.type == "VIEW_3D"]
    )
    for area in areas:
        area.tag_redraw()


def install() -> None:
    global _HANDLE
    remove()
    _HANDLE = bpy.types.SpaceView3D.draw_handler_add(
        _draw, (), _HANDLE_REGION, "POST_VIEW"
    )
    bpy.app.driver_namespace[_HANDLE_KEY] = (_HANDLE, _HANDLE_REGION)


def remove() -> None:
    global _HANDLE
    stored = bpy.app.driver_namespace.pop(_HANDLE_KEY, None)
    handles = [_HANDLE]
    if isinstance(stored, tuple):
        handles.append(stored[0])
    elif stored is not None:
        handles.append(stored)
    for handle in handles:
        if handle is None:
            continue
        try:
            bpy.types.SpaceView3D.draw_handler_remove(handle, _HANDLE_REGION)
        except (ValueError, TypeError):
            pass
    _HANDLE = None
    _BATCHES.clear()
