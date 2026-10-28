"""浓度场 → 顶点色（只用项目色板，不 import bpy）。

颜色是**线性**的：``palette.hex_to_linear`` 转完之后直接写进 ``FLOAT_COLOR`` 属性，
否则渲染/预览整体发白。

三种配色：

* ``PATTERN`` —— 活化剂浓度 ``v``：画布底色 → 石青 → 赭石 → 朱砂，图案本体就是色块；
* ``INK`` —— 基质亏缺 ``1 − u``：绢白宣纸到墨玄的浓淡，像一张水墨拓片；
* ``EDGE`` —— ``|∇v|``：只把图案的轮廓描出来，线条走赭石 → 朱砂，底色留白。

``PATTERN`` / ``EDGE`` 的归一化用预设给的 ``v_ref``（各预设的 v 幅值差好几倍），
再乘面板的「对比度」，所以同一条色带能用在所有预设上；``INK`` 直接按 ``1 − u`` 归一化。
"""

from __future__ import annotations

import numpy as np

from .palette import (
    AZURITE,
    BACKGROUND,
    CINNABAR,
    INK,
    OCHRE,
    SILK,
    hex_to_linear,
)

__all__ = ["MODE_EDGE", "MODE_INK", "MODE_PATTERN", "MODES", "colours", "edge_field"]

MODE_PATTERN = "PATTERN"
MODE_INK = "INK"
MODE_EDGE = "EDGE"

#: 面板枚举项。
MODES = [
    (
        MODE_PATTERN,
        "图案",
        "按活化剂 v 着色：画布底色 → 石青 → 赭石 → 朱砂，图案本体就是色块",
    ),
    (
        MODE_INK,
        "水墨",
        "按基质亏缺 1−u 着色：绢白宣纸到墨玄的浓淡，像一张拓片",
    ),
    (
        MODE_EDGE,
        "轮廓",
        "只描图案边缘（|∇v|）：赭石到朱砂的线，底色留白",
    ),
]

#: 线性色（写完可以直接进顶点色属性）。
_BASE = np.array(hex_to_linear(BACKGROUND), np.float32)
_PAPER = np.array(hex_to_linear(SILK), np.float32)
_COLD = np.array(hex_to_linear(AZURITE), np.float32)
_WARM = np.array(hex_to_linear(OCHRE), np.float32)
_HOT = np.array(hex_to_linear(CINNABAR), np.float32)
_INK = np.array(hex_to_linear(INK), np.float32)

#: ``INK`` 模式下 ``1 − u`` 的归一分母：图案里 u 大约在 0.2~0.4，背景是 1。
_INK_GAIN = 0.85

#: ``EDGE`` 模式：``|∇v|`` 相对 ``v_ref`` 的归一化尺度。实测成熟图案的 ``|∇v|``
#: 99 分位约 ``0.23·v_ref``（与网格分辨率基本无关），所以 0.25 刚好把轮廓拉满。
_EDGE_GAIN = 0.25


def _ramp(values: np.ndarray, stops: list) -> np.ndarray:
    """分段线性色带：``stops = [(位置, 线性 RGB), ...]``（位置升序）。"""
    positions = [stop[0] for stop in stops]
    out = np.empty((values.size, 3), np.float32)
    for channel in range(3):
        channel_values = [stop[1][channel] for stop in stops]
        out[:, channel] = np.interp(values, positions, channel_values)
    return out


def edge_field(v: np.ndarray, nx: int, ny: int) -> np.ndarray:
    """``|∇v|``（中心差分、周期性边界），按顶点顺序展平返回。

    网格顶点编号是 ``i*ny + j``（C 序），所以要先 ``reshape`` 回 ``(nx, ny)``。
    """
    field = np.ascontiguousarray(v, np.float32).reshape(int(nx), int(ny))
    grad_x = 0.5 * (np.roll(field, -1, 0) - np.roll(field, 1, 0))
    grad_y = 0.5 * (np.roll(field, -1, 1) - np.roll(field, 1, 1))
    return np.hypot(grad_x, grad_y).ravel()


def colours(
    mode: str,
    u: np.ndarray,
    v: np.ndarray,
    nx: int,
    ny: int,
    v_ref: float = 0.4,
    contrast: float = 1.0,
    edge: np.ndarray | None = None,
) -> np.ndarray:
    """``(n, 4)`` 线性 RGBA（顶点顺序与展平的浓度场一致，alpha 恒为 1）。

    ``v_ref`` 是该预设 v 的典型幅值，``contrast`` 是面板上的对比度倍数；
    ``edge`` 传入已经算好的 ``|∇v|``（每帧只算一次）可以省一次梯度。
    """
    u_values = np.clip(np.asarray(u, np.float32).ravel(), 0.0, 1.0)
    v_values = np.clip(np.asarray(v, np.float32).ravel(), 0.0, 1.0)
    span = max(float(v_ref) * float(contrast), 1e-6)

    if str(mode) == MODE_INK:
        # 基质越少墨越浓：1 − u 从 0（宣纸）到 ~0.8（图案内部）铺满墨阶
        density = np.clip((1.0 - u_values) / _INK_GAIN, 0.0, 1.0)
        rgb = _ramp(density, [(0.0, _PAPER), (0.55, _COLD), (1.0, _INK)])
    elif str(mode) == MODE_EDGE:
        gradient = (
            np.asarray(edge, np.float32).ravel()
            if edge is not None
            else edge_field(v_values, nx, ny)
        )
        heat = np.clip(gradient / (span * _EDGE_GAIN), 0.0, 1.0)
        rgb = _ramp(heat, [(0.0, _BASE), (0.45, _WARM), (1.0, _HOT)])
    else:
        heat = np.clip(v_values / span, 0.0, 1.0)
        rgb = _ramp(heat, [(0.0, _BASE), (0.35, _COLD), (0.72, _WARM), (1.0, _HOT)])

    out = np.ones((u_values.size, 4), np.float32)
    out[:, :3] = np.clip(rgb, 0.0, 1.0)
    return out
