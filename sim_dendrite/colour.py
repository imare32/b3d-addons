"""相场 → 顶点色（只用项目色板，不 import bpy）。

颜色是**线性**的：``palette.hex_to_linear`` 转完之后直接写进 ``FLOAT_COLOR`` 属性，
否则渲染/预览整体发白。

两种配色：

* ``PHASE`` —— 相场本身：液相 ``SILK`` → 固相 ``AZURITE``，界面 ``4φ(1−φ)`` 上提
  ``OCHRE`` 到 ``CINNABAR`` 的高光带（晶体轮廓就是这么"亮"起来的）；
* ``THERMAL`` —— 过冷度：``BACKGROUND``（最冷）→ ``AZURITE`` → ``OCHRE`` →
  ``CINNABAR``（最热，枝晶尖端与界面放出的潜热）。
"""

from __future__ import annotations

import numpy as np

from .palette import (
    AZURITE,
    BACKGROUND,
    CINNABAR,
    OCHRE,
    SILK,
    hex_to_linear,
)

__all__ = ["MODE_PHASE", "MODE_THERMAL", "MODES", "colours"]

MODE_PHASE = "PHASE"
MODE_THERMAL = "THERMAL"

#: 面板枚举项。
MODES = [
    (MODE_PHASE, "相场", "液相绢白 → 固相石青，界面用赭石到朱砂的高光带勾出枝晶轮廓"),
    (
        MODE_THERMAL,
        "过冷度",
        "按 ΔT 着色：画布底色（最冷）→ 石青 → 赭石 → 朱砂（最热）",
    ),
]


def _linear(value: str) -> tuple[float, float, float]:
    return hex_to_linear(value)


#: 线性色（写完可以直接进顶点色属性）。
_LIQUID = np.array(_linear(SILK), np.float32)
_SOLID = np.array(_linear(AZURITE), np.float32)
_RIM = np.array(_linear(OCHRE), np.float32)
_HOT = np.array(_linear(CINNABAR), np.float32)
_COLD = np.array(_linear(BACKGROUND), np.float32)


def _mix(a: np.ndarray, b: np.ndarray, weight: np.ndarray) -> np.ndarray:
    """逐顶点线性插值：``a + (b − a)·w``（``w`` 可广播成 ``(n, 1)``）。"""
    return a + (b - a) * weight


def _ramp(values: np.ndarray, stops: list) -> np.ndarray:
    """分段线性色带：``stops = [(位置, 线性 RGB), ...]``（位置升序）。"""
    positions = [stop[0] for stop in stops]
    out = np.empty((values.size, 3), np.float32)
    for channel in range(3):
        channel_values = [stop[1][channel] for stop in stops]
        out[:, channel] = np.interp(values, positions, channel_values)
    return out


def _smoothstep(edge0: float, edge1: float, values: np.ndarray) -> np.ndarray:
    """把 ``0/1`` 的过渡收紧到 ``[edge0, edge1]``：让固相看着"实"，液相看着"空"。

    不这么做的话，相场在细枝上只有两三个格子宽，``φ`` 的中值会铺满整根枝 —— 视觉上
    枝晶是"糊"的。收紧的是**显示**映射，物理场不动。
    """
    t = np.clip((values - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def colours(
    mode: str,
    phi: np.ndarray,
    dT: np.ndarray,
    vmin: float = -0.5,
    vmax: float = 0.0,
) -> np.ndarray:
    """``(n, 4)`` 线性 RGBA（顶点顺序与展平的相场一致，alpha 恒为 1）。"""
    phi_values = np.clip(np.asarray(phi, np.float32).ravel(), 0.0, 1.0)

    if str(mode) == MODE_THERMAL:
        span = max(float(vmax) - float(vmin), 1e-6)
        heat = np.clip(
            (np.asarray(dT, np.float32).ravel() - float(vmin)) / span, 0.0, 1.0
        )
        rgb = _ramp(heat, [(0.0, _COLD), (0.45, _SOLID), (0.75, _RIM), (1.0, _HOT)])
    else:
        # 液相 → 固相：用 smoothstep 收紧过渡，晶体内部才是一整片实色
        solid = _smoothstep(0.25, 0.75, phi_values)
        rgb = _mix(_LIQUID[None, :], _SOLID[None, :], solid[:, None])
        # 界面 4φ(1−φ)（最中间取 1）上提成暖色高光带，正中间再压一层朱砂
        interface = (4.0 * phi_values * (1.0 - phi_values))[:, None]
        rgb = _mix(rgb, _RIM[None, :], interface * 0.85)
        hot = np.clip(interface * 1.6 - 0.6, 0.0, 1.0) * 0.5
        rgb = _mix(rgb, _HOT[None, :], hot)

    out = np.ones((phi_values.size, 4), np.float32)
    out[:, :3] = np.clip(rgb, 0.0, 1.0)
    return out
