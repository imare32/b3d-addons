"""拉伸比 / 曲率 → 笔划逐点颜色（只用项目色板，不 import bpy）。

颜色是**线性**的：``palette.hex_to_linear`` 转完之后直接写进 GP 的 ``vertex_color``
（POINT 域 FLOAT_COLOR），否则渲染/预览整体发灰发白。

三种配色：

* ``CURVATURE`` —— 逐点转角 / π：直段是石青（冷），打褶最狠的地方烧成赭石 → 朱砂。
  差分生长的"折痕密度"一眼能看出来，这是缺省配色；
* ``STRETCH`` —— 逐点拉伸比 ``L̄ / split_dist``（1.0 = 这条边马上要被切开）：被拉长的地
  方最暖。它标出的是"下一帧会在哪里长"；
* ``MONO`` —— 单色（面板给的颜色），线稿模式。

归一化尺度都是**物理量本身**，不用逐帧最大值 —— 自归一化会让颜色每帧整体重新分布，
看不出"折叠越来越密"这个过程。
"""

from __future__ import annotations

import numpy as np

from .palette import AZURITE, CINNABAR, OCHRE, hex_to_linear, srgb_to_linear

__all__ = ["MODE_CURVATURE", "MODE_MONO", "MODE_STRETCH", "MODES", "colours"]

MODE_CURVATURE = "CURVATURE"
MODE_STRETCH = "STRETCH"
MODE_MONO = "MONO"

#: 面板枚举项。
MODES = [
    (
        MODE_CURVATURE,
        "曲率",
        "按逐点转角着色：石青（直）→ 赭石 → 朱砂（折痕最紧）。打褶的密度一眼能看出来",
    ),
    (
        MODE_STRETCH,
        "拉伸",
        "按逐点拉伸比 L/split_dist 着色：越接近分裂阈值越暖，标出下一帧会长在哪里",
    ),
    (MODE_MONO, "单色", "整条曲线一个颜色（面板给的颜色），纯线稿"),
]

#: 色带（线性空间）：冷 → 暖。
_STOPS = np.array(
    [
        hex_to_linear(AZURITE),
        hex_to_linear(OCHRE),
        hex_to_linear(CINNABAR),
    ],
    np.float32,
)

#: 曲率色带节点：中段偏后，只有真正拐死的地方才烧起来。
_CURVATURE_XS = np.array([0.0, 0.45, 1.0], np.float32)
#: 拉伸色带更早进暖色：0.4 倍分裂距离就开始偏赭石，1.0 满格（朱砂）。
_STRETCH_XS = np.array([0.0, 0.35, 1.0], np.float32)


def _ramp(values: np.ndarray, xs: np.ndarray) -> np.ndarray:
    """分段线性色带 → ``(n,3)`` 线性 RGB。"""
    out = np.empty((values.size, 3), np.float32)
    for channel in range(3):
        out[:, channel] = np.interp(values, xs, _STOPS[:, channel])
    return out


def colours(
    mode: str,
    stretch: np.ndarray,
    curvature: np.ndarray,
    mono=(0.17, 0.17, 0.17),
) -> np.ndarray:
    """``(n,4)`` 线性 RGBA（顶点顺序与求解器的位置数组一致，alpha 恒为 1）。

    ``mono`` 是 **sRGB** 三元组（``COLOR_GAMMA`` 属性的约定），在这里转线性。
    """
    mode = str(mode)
    reference = np.asarray(curvature, np.float32).ravel()
    out = np.ones((reference.size, 4), np.float32)

    if mode == MODE_MONO:
        out[:, :3] = np.array(
            [srgb_to_linear(float(c)) for c in mono], np.float32
        )
        return out

    if mode == MODE_STRETCH:
        # 拉伸比 0.5 以下算"松"，1.0（= 马上就要被切开）满格
        ratio = np.asarray(stretch, np.float32).ravel()
        value = np.clip((ratio - 0.5) / 0.5, 0.0, 1.0)
        out[:, :3] = _ramp(value, _STRETCH_XS)
    else:
        # 转角 / π 本身就在 [0, 1]；0.6 次幂让小折痕也有点颜色
        value = np.power(np.clip(reference, 0.0, 1.0), 0.6)
        out[:, :3] = _ramp(value, _CURVATURE_XS)
    return out
