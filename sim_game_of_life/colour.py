"""面状态 → 面颜色（只用项目色板，不 import bpy）。

颜色是**线性**的：``palette.hex_to_linear`` 转完之后直接写进 ``FLOAT_COLOR`` 属性，
否则渲染/预览整体发白。

两种配色：

* ``STATE`` —— 生死本身：死格 ``SILK``（比画布底色略亮，留着网格轮廓）→ 活格
  ``AZURITE``，帧间插值处 ``4a(1−a)`` 上提 ``OCHRE`` 到 ``CINNABAR`` 的高光带，
  于是「出生 / 死亡的那一瞬间」是画面里最亮的地方；
* ``AGE`` —— 连续存活代数：石青（新生）→ 苔绿 → 赭石 → 朱砂（老细胞），
  仍按活度淡入淡出，死格保持 ``SILK``。
"""

from __future__ import annotations

import numpy as np

from .palette import AZURITE, CINNABAR, MOSS, OCHRE, SILK, hex_to_linear

__all__ = ["MODE_AGE", "MODE_STATE", "MODES", "colours"]

MODE_STATE = "STATE"
MODE_AGE = "AGE"

#: 面板枚举项。
MODES = [
    (
        MODE_STATE,
        "生死",
        "死格绢白、活格石青，生灭的过渡处用赭石到朱砂的高光带勾出来",
    ),
    (
        MODE_AGE,
        "年龄",
        "按连续存活代数着色：石青（新生）→ 苔绿 → 赭石 → 朱砂（老细胞）",
    ),
]

#: 线性色（写完可以直接进顶点色属性）。
_DEAD = np.array(hex_to_linear(SILK), np.float32)
_ALIVE = np.array(hex_to_linear(AZURITE), np.float32)
_RIM = np.array(hex_to_linear(OCHRE), np.float32)
_HOT = np.array(hex_to_linear(CINNABAR), np.float32)
_YOUNG = np.array(hex_to_linear(AZURITE), np.float32)
_MID = np.array(hex_to_linear(MOSS), np.float32)
_OLD = np.array(hex_to_linear(OCHRE), np.float32)
_ANCIENT = np.array(hex_to_linear(CINNABAR), np.float32)


def _mix(a: np.ndarray, b: np.ndarray, weight: np.ndarray) -> np.ndarray:
    """逐格线性插值：``a + (b − a)·w``（``w`` 可广播成 ``(n, 1)``）。"""
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
    """把 ``0/1`` 的过渡收紧到 ``[edge0, edge1]``：让活细胞看着"实"，死细胞看着"空"。

    不这么做的话，插值中的半格会铺满整块网格，画面是"糊"的。收紧的是**显示**映射，
    状态本身不动。
    """
    t = np.clip((values - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def colours(
    mode: str,
    alive: np.ndarray,
    age: np.ndarray | None = None,
    age_scale: float = 30.0,
) -> np.ndarray:
    """``(n, 4)`` 线性 RGBA（顺序与面属性一致，alpha 恒为 1）。"""
    values = np.clip(np.asarray(alive, np.float32).ravel(), 0.0, 1.0)
    solid = _smoothstep(0.25, 0.75, values)[:, None]

    if str(mode) == MODE_AGE:
        span = max(float(age_scale), 1.0)
        if age is None:
            heat = np.zeros(values.size, np.float32)
        else:
            heat = np.clip(np.asarray(age, np.float32).ravel() / span, 0.0, 1.0)
        rgb = _ramp(heat, [(0.0, _YOUNG), (0.33, _MID), (0.66, _OLD), (1.0, _ANCIENT)])
        rgb = _mix(_DEAD[None, :], rgb, solid)
    else:
        rgb = _mix(_DEAD[None, :], _ALIVE[None, :], solid)
        # 插值中的半格 = 正在生灭：上提成暖色高光带，正中间再压一层朱砂
        interface = (4.0 * values * (1.0 - values))[:, None]
        rgb = _mix(rgb, _RIM[None, :], interface * 0.8)
        hot = np.clip(interface * 1.6 - 0.6, 0.0, 1.0) * 0.5
        rgb = _mix(rgb, _HOT[None, :], hot)

    out = np.ones((values.size, 4), np.float32)
    out[:, :3] = np.clip(rgb, 0.0, 1.0)
    return out
