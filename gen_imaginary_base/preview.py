"""视口预览：``POST_VIEW`` 立即模式画点集与取景框，**不创建任何数据块**。

做法与 `fractal_family` 的 ``preview.py`` 一致：只有想让「改参数 → 立刻看到结果」成立时
才需要它，而它一旦建临时对象，撤销栈和场景里就会多出一堆垃圾。

按数字分组画
    同号的点颜色相同，于是可以一组一次 ``draw``（每组一条 batch）。96 个数字就是 96 次
    绘制调用 —— 比"逐点给色"省事得多，也不用自己写 shader。

点大小
    ``POINTS`` 图元的大小由 ``gpu.state.point_size_set`` 控制，是**屏幕像素**，所以缩放
    视口时点不会跟着变大变小 —— 这正是点集想要的：拉近是看结构，不是看圆点。

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

from . import palette
from .hud_layout import frame_polygon

__all__ = ["install", "remove", "tag_redraw"]

#: 点大小（像素）。太小看不清结构，太大一万点就糊成一片。
POINT_SIZE = 3.4

#: 取景框线宽（像素）与颜色。中性灰：深浅主题下都看得见。
FRAME_WIDTH = 1.6
FRAME_COLOUR = (0.42, 0.42, 0.45, 0.75)

_HANDLE = None
_HANDLE_KEY = "imaginarybase_preview_handle"
_HANDLE_REGION = "WINDOW"

#: 画好的批次缓存：``key -> {"points": [...], "frame": batch}``
_BATCHES: dict = {}
_CACHE_LIMIT = 4

#: 只打印一次绘制异常，免得每个重绘都刷屏。
_REPORTED = False


def _settings(context=None):
    context = context or bpy.context
    scene = getattr(context, "scene", None)
    return getattr(scene, "imaginarybase", None) if scene is not None else None


def _build_batches(settings):
    """按数字分组建 batch；同时给取景框建一条线。"""
    data = settings.point_data()
    xy = data["xy"]
    if len(xy) == 0:
        return []
    positions = np.zeros((len(xy), 3), dtype=np.float32)
    positions[:, :2] = xy
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batches = []
    for slot in np.unique(data["slot"]):
        mask = data["slot"] == slot
        # 每个 slot 用自己那号线性色（与 GP 输出一致），预乘 alpha 交给混合状态
        colour = palette.digit_colour_linear(int(slot))
        batches.append(
            (
                batch_for_shader(shader, "POINTS", {"pos": positions[mask]}),
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
    key = (settings.point_key(), settings.domain)
    cached = _BATCHES.get(key)
    if cached is not None:
        return cached
    data = settings.point_data()
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    built = {
        "points": _build_batches(settings),
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
        frame = batches.get("frame")
        if frame is not None:
            gpu.state.line_width_set(FRAME_WIDTH)
            shader.uniform_float("color", FRAME_COLOUR)
            frame.draw(shader)
        gpu.state.point_size_set(POINT_SIZE)
        for batch, colour in batches.get("points") or ():
            shader.uniform_float("color", colour)
            batch.draw(shader)
    except Exception:
        if not _REPORTED:
            _REPORTED = True
            print("[gen_imaginary_base] 视口预览绘制失败（后续不再重复报告）：")
            traceback.print_exc()
    finally:
        gpu.state.point_size_set(1.0)
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
