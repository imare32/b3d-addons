"""视口预览：``POST_VIEW`` 立即模式画点集，**不创建任何数据块**。

做法与 `fractal_family` 的 ``preview.py`` 一致：只有想让「改参数 → 立刻看到结果」成立时
才需要它，而它一旦建临时对象，撤销栈和场景里就会多出一堆垃圾。

按数字分组画
    同号的点颜色相同，于是可以一组一次 ``draw``（每组一条 batch）。96 个数字就是 96 次
    绘制调用 —— 比"逐点给色"省事得多，也不用自己写 shader。

点大小
    ``POINTS`` 图元的大小由 ``gpu.state.point_size_set`` 控制，是**屏幕像素**，所以缩放
    视口时点不会跟着变大变小 —— 这正是点集想要的：拉近是看结构，不是看圆点。
"""

from __future__ import annotations

import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from . import palette

__all__ = ["install", "remove", "tag_redraw"]

#: 点大小（像素）。太小看不清结构，太大一万点就糊成一片。
POINT_SIZE = 3.4

_HANDLE = None
_HANDLE_KEY = "imaginarybase_preview_handle"
_HANDLE_REGION = "WINDOW"

#: 画好的批次缓存：``key -> [(batch, colour), ...]``
_BATCHES: dict = {}
_CACHE_LIMIT = 4


def _settings(context=None):
    context = context or bpy.context
    scene = getattr(context, "scene", None)
    return getattr(scene, "imaginarybase", None) if scene is not None else None


def _build_batches(settings):
    """按数字分组建 batch：``[(batch, (r, g, b, a)), ...]``。"""
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


def _batches_for(settings):
    key = settings.point_key()
    cached = _BATCHES.get(key)
    if cached is not None:
        return cached
    batches = _build_batches(settings)
    if len(_BATCHES) >= _CACHE_LIMIT:
        _BATCHES.clear()
    _BATCHES[key] = batches
    return batches


def _draw():
    settings = _settings()
    if settings is None or not settings.show_preview:
        return
    try:
        batches = _batches_for(settings)
    except Exception:
        return
    if not batches:
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    gpu.state.blend_set("ALPHA")
    gpu.state.point_size_set(POINT_SIZE)
    shader.bind()
    try:
        for batch, colour in batches:
            shader.uniform_float("color", colour)
            batch.draw(shader)
    finally:
        gpu.state.point_size_set(1.0)
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
