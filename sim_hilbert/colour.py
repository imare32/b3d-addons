"""速度 / 应变 → 笔划逐点颜色（只用项目色板，不 import bpy）。

颜色是**线性**的：``palette.hex_to_linear`` 转完之后直接写进 GP 的 ``vertex_color``
（POINT 域 FLOAT_COLOR），否则渲染/预览整体发灰发白。

三种配色：

* ``SPEED`` —— 逐点速度：静下来的部分是石青（冷），砸到地面的地方烧成赭石 → 朱砂；
* ``STRAIN`` —— 逐点平均应变 ``|L − L0| / L0``：弹簧被拉开/压紧的地方最暖，能直接看出
  冲击波在格子里怎么传播；
* ``MONO`` —— 单色（面板给的颜色），线稿模式。

归一化尺度由面板给（``speed_max`` / ``strain_max``），**不用逐帧最大值** —— 自归一化会
让热力图每帧整体变色，看不出"能量在耗散"。
"""

from __future__ import annotations

import numpy as np

from .palette import AZURITE, CINNABAR, OCHRE, hex_to_linear, srgb_to_linear

__all__ = ["MODE_MONO", "MODE_SPEED", "MODE_STRAIN", "MODES", "colours"]

MODE_SPEED = "SPEED"
MODE_STRAIN = "STRAIN"
MODE_MONO = "MONO"

#: 面板枚举项。
MODES = [
    (
        MODE_SPEED,
        "速度",
        "按逐点速度着色：石青（静）→ 赭石 → 朱砂（最快）。落地冲击一眼能看出来",
    ),
    (
        MODE_STRAIN,
        "应变",
        "按逐点平均应变 |L−L0|/L0 着色：弹簧被拉开或压紧的地方最暖，冲击波在格子里走",
    ),
    (MODE_MONO, "单色", "整条曲线一个颜色（面板给的颜色），纯线稿"),
]

#: 色带（线性空间）：冷 → 暖。位置在同一色带上按模式重标。
_STOPS = np.array(
    [
        hex_to_linear(AZURITE),
        hex_to_linear(OCHRE),
        hex_to_linear(CINNABAR),
    ],
    np.float32,
)

#: 速度色带的节点位置：中段偏后，让"慢慢动"的部分保持冷色，冲击帧才烧起来。
_SPEED_XS = np.array([0.0, 0.55, 1.0], np.float32)
#: 应变色带更早进入暖色：应变本身就是小量，10% 就该是朱砂。
_STRAIN_XS = np.array([0.0, 0.35, 1.0], np.float32)


def _ramp(values: np.ndarray, xs: np.ndarray) -> np.ndarray:
    """分段线性色带 → ``(n,3)`` 线性 RGB。"""
    out = np.empty((values.size, 3), np.float32)
    for channel in range(3):
        out[:, channel] = np.interp(values, xs, _STOPS[:, channel])
    return out


def colours(
    mode: str,
    speed: np.ndarray,
    strain: np.ndarray,
    speed_max: float = 8.0,
    strain_max: float = 0.25,
    mono=(0.17, 0.17, 0.17),
) -> np.ndarray:
    """``(n,4)`` 线性 RGBA（顶点顺序与求解器的位置数组一致，alpha 恒为 1）。

    ``mono`` 是 **sRGB** 三元组（``COLOR_GAMMA`` 属性的约定），在这里转线性。
    """
    mode = str(mode)
    if mode == MODE_MONO:
        rgb = np.array([srgb_to_linear(float(c)) for c in mono], np.float32)
        out = np.ones((len(speed), 4), np.float32)
        out[:, :3] = rgb
        return out

    out = np.ones((len(speed), 4), np.float32)
    if mode == MODE_STRAIN:
        scale = max(float(strain_max), 1e-6)
        value = np.clip(np.asarray(strain, np.float32) / scale, 0.0, 1.0)
        out[:, :3] = _ramp(value, _STRAIN_XS)
    else:
        scale = max(float(speed_max), 1e-6)
        value = np.clip(np.asarray(speed, np.float32) / scale, 0.0, 1.0)
        # 0.7 次幂：低速段仍是冷色，中速就开始偏暖，冲击帧满格
        out[:, :3] = _ramp(np.power(value, 0.7), _SPEED_XS)
    return out
