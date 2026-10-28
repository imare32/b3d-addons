"""theta 除子的四张标量场 → 顶点色（只用项目色板，不 import bpy）。

颜色是**线性**的：``palette.hex_to_linear`` 转完之后直接写进 ``FLOAT_COLOR`` 属性，
否则渲染/预览整体发白。

三种配色（面板上以「配色」切换）：

* ``LATTICE`` —— 相位干涉格纹：墨玄凹槽 → 赭石 → 绢白高脊，零点光晕上提成朱砂；
  这是参考实现（@Mathelirium）的那一套"金线菱格 + 琥珀光斑"；
* ``PHASE`` —— 复相角：按 ``arg Θ / 2π`` 循环取色（石青 → 绢白 → 赭石 → 朱砂 → 石青），
  零点处仍提朱砂，能直接看出相位的旋绕；
* ``POTENTIAL`` —— 势井：``log|Θ|`` 逐帧按 2%~98% 分位归一化，墨玄（井底）→ 石青 →
  赭石 → 绢白（井沿）。

逐帧归一化只影响**显示**映射，物理场不动；拖时间轴时 ``POTENTIAL`` 的明暗会随极值
轻微跳动，其余两种配色是固定映射。
"""

from __future__ import annotations

import math

import numpy as np

from .palette import (
    AZURITE,
    CINNABAR,
    INK,
    OCHRE,
    SILK,
    hex_to_linear,
)

__all__ = [
    "MODE_LATTICE",
    "MODE_PHASE",
    "MODE_POTENTIAL",
    "MODES",
    "colours",
]

MODE_LATTICE = "LATTICE"
MODE_PHASE = "PHASE"
MODE_POTENTIAL = "POTENTIAL"

#: 面板枚举项。
MODES = [
    (
        MODE_LATTICE,
        "相位网格",
        "墨玄凹槽 → 赭石 → 绢白高脊的金线菱格，零点光晕上提为朱砂（参考实现的观感）",
    ),
    (
        MODE_PHASE,
        "相角",
        "按 arg Θ 循环取色（石青 → 绢白 → 赭石 → 朱砂），能直接看出相位旋绕",
    ),
    (
        MODE_POTENTIAL,
        "势井",
        "按 log|Θ| 着色：墨玄（零点井底）→ 石青 → 赭石 → 绢白（井沿）",
    ),
]


def _linear(value: str) -> tuple[float, float, float]:
    return hex_to_linear(value)


#: 线性色（写完可以直接进顶点色属性）。
_DARK = np.array(_linear(INK), np.float32)
_COOL = np.array(_linear(AZURITE), np.float32)
_WARM = np.array(_linear(OCHRE), np.float32)
_HOT = np.array(_linear(CINNABAR), np.float32)
_LIGHT = np.array(_linear(SILK), np.float32)


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


def _halo(amplitude: np.ndarray) -> np.ndarray:
    """零点光晕：参考实现的 ``exp(−120d²)`` 叠加，压一下让光斑更紧。"""
    return np.clip(np.asarray(amplitude, np.float32).ravel(), 0.0, 1.0) ** 1.2


def colours(
    mode: str,
    amplitude: np.ndarray,
    phase: np.ndarray,
    phase_grid: np.ndarray,
    potential: np.ndarray,
) -> np.ndarray:
    """``(n, 4)`` 线性 RGBA（顶点顺序与展平的场一致，alpha 恒为 1）。"""
    glow = _halo(amplitude)
    glow_column = glow[:, None]

    if str(mode) == MODE_PHASE:
        turns = (np.asarray(phase, np.float32).ravel() / (2.0 * math.pi)) % 1.0
        rgb = _ramp(
            turns,
            [
                (0.0, _COOL),
                (0.25, _LIGHT),
                (0.6, _WARM),
                (0.85, _HOT),
                (1.0, _COOL),
            ],
        )
        rgb = _mix(rgb, _HOT[None, :], glow_column * 0.6)
    elif str(mode) == MODE_POTENTIAL:
        values = np.asarray(potential, np.float32).ravel()
        low, high = np.percentile(values, 2.0), np.percentile(values, 98.0)
        span = max(float(high) - float(low), 1e-6)
        norm = np.clip((values - float(low)) / span, 0.0, 1.0)
        rgb = _ramp(norm, [(0.0, _DARK), (0.35, _COOL), (0.7, _WARM), (1.0, _LIGHT)])
        rgb = _mix(rgb, _HOT[None, :], glow_column * 0.85)
    else:
        grid = np.clip(np.asarray(phase_grid, np.float32).ravel(), 0.0, 1.0)
        rgb = _ramp(grid, [(0.0, _DARK), (0.45, _WARM), (1.0, _LIGHT)])
        rgb = _mix(rgb, _HOT[None, :], glow_column * 0.85)

    out = np.ones((glow.size, 4), np.float32)
    out[:, :3] = np.clip(rgb, 0.0, 1.0)
    return out
