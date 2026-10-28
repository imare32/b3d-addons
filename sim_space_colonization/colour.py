"""树枝 → 顶点色（只用项目色板，不 import bpy）。

颜色是**线性**的：``palette.hex_to_linear`` 转完之后直接写进 ``FLOAT_COLOR`` 属性，
否则渲染/预览整体发白。

两种配色：

* ``DEPTH`` —— 生长深度：根 ``AZURITE``（石青）→ 中段 ``MOSS``（苔绿）→ 枝尖
  ``OCHRE``（赭石）。藤蔓从老到新由冷转暖，是这套算法最直观的读法；
* ``RADIUS`` —— 粗细分档：细枝 ``OCHRE`` → 中段 ``MOSS`` → 主干 ``INK``（墨玄），
  主干在画布底色上最沉，枝梢最飘。
"""

from __future__ import annotations

import numpy as np

from .palette import AZURITE, INK, MOSS, OCHRE, hex_to_linear

__all__ = ["MODE_DEPTH", "MODE_RADIUS", "MODES", "colours"]

MODE_DEPTH = "DEPTH"
MODE_RADIUS = "RADIUS"

#: 面板枚举项。
MODES = [
    (
        MODE_DEPTH,
        "生长深度",
        "根（石青）→ 中段（苔绿）→ 枝尖（赭石）：由冷到暖，一眼看出长到第几代",
    ),
    (
        MODE_RADIUS,
        "粗细",
        "按半径分档：细枝（赭石）→ 中段（苔绿）→ 主干（墨玄）",
    ),
]


def _linear(value: str) -> np.ndarray:
    return np.array(hex_to_linear(value), np.float32)


#: 线性色（写完可以直接进顶点色属性）。
_ROOT = _linear(AZURITE)
_MID = _linear(MOSS)
_TIP = _linear(OCHRE)
_TRUNK = _linear(INK)


def _ramp(values: np.ndarray, stops: list) -> np.ndarray:
    """分段线性色带：``stops = [(位置, 线性 RGB), ...]``（位置升序）。"""
    positions = [stop[0] for stop in stops]
    out = np.empty((values.size, 3), np.float32)
    for channel in range(3):
        out[:, channel] = np.interp(
            values, positions, [stop[1][channel] for stop in stops]
        )
    return out


def colours(mode: str, depth: np.ndarray, radius: np.ndarray) -> np.ndarray:
    """``(n, 4)`` 线性 RGBA，顺序与节点（顶点）编号一致，alpha 恒为 1。"""
    depth_values = np.clip(np.asarray(depth, np.float32).ravel(), 0.0, 1.0)

    if str(mode) == MODE_RADIUS:
        radii = np.asarray(radius, np.float32).ravel()
        # 半径跨一到两个数量级，取对数再归一化才看得出分档
        scaled = np.log1p(np.maximum(radii, 0.0))
        high = float(scaled.max()) if scaled.size else 0.0
        values = scaled / high if high > 1e-9 else np.zeros_like(scaled)
        rgb = _ramp(values, [(0.0, _TIP), (0.55, _MID), (1.0, _TRUNK)])
    else:
        rgb = _ramp(depth_values, [(0.0, _ROOT), (0.55, _MID), (1.0, _TIP)])

    out = np.ones((depth_values.size, 4), np.float32)
    out[:, :3] = np.clip(rgb, 0.0, 1.0)
    return out
