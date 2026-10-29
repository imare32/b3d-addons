"""逐鸟速度 / 朝向 → 颜色（只用项目色板，不 import bpy）。

颜色是**线性**的：``palette.hex_to_linear`` 转完之后直接写进 ``FLOAT_COLOR`` 属性，
否则渲染/预览整体发白。

两种配色：

* ``SPEED`` —— 快慢：石青（慢）→ 苔绿 → 赭石 → 朱砂（快）。鸟群的「快慢结构」在画面里
  直接可见：外侧赶上来的快、转弯内圈的慢；
* ``HEADING`` —— 朝向：一圈环形色带（石青 → 苔绿 → 赭石 → 朱砂 → 回到石青），用来观察
  群体怎么分叉、怎么汇聚。色带首尾同色，所以东与西不会突然断开。
"""

from __future__ import annotations

import numpy as np

from .palette import AZURITE, CINNABAR, MOSS, OCHRE, hex_to_linear

__all__ = ["MODE_HEADING", "MODE_SPEED", "MODES", "colours"]

MODE_SPEED = "SPEED"
MODE_HEADING = "HEADING"

#: 面板枚举项。
MODES = [
    (
        MODE_SPEED,
        "速度",
        "按当前速率着色：石青（慢）→ 苔绿 → 赭石 → 朱砂（快），以「速度上限」为满格",
    ),
    (
        MODE_HEADING,
        "朝向",
        (
            "按飞行方向着色：一圈环形色带（首尾同色）。整群同向时是一整片同一个颜色，"
            "只有分叉、绕圈、迎头对冲时才看得出结构"
        ),
    ),
]

#: 线性色（写完可以直接进顶点色属性）。
_SLOW = np.array(hex_to_linear(AZURITE), np.float32)
_MID = np.array(hex_to_linear(MOSS), np.float32)
_WARM = np.array(hex_to_linear(OCHRE), np.float32)
_FAST = np.array(hex_to_linear(CINNABAR), np.float32)


def _ramp(values: np.ndarray, stops: list) -> np.ndarray:
    """分段线性色带：``stops = [(位置, 线性 RGB), ...]``（位置升序）。"""
    positions = [stop[0] for stop in stops]
    out = np.empty((values.size, 3), np.float32)
    for channel in range(3):
        channel_values = [stop[1][channel] for stop in stops]
        out[:, channel] = np.interp(values, positions, channel_values)
    return out


def colours(mode: str, velocities: np.ndarray, max_speed: float = 0.8) -> np.ndarray:
    """``(n, 4)`` 线性 RGBA（顺序与鸟的顺序一致，alpha 恒为 1）。"""
    vel = np.asarray(velocities, np.float32).reshape(-1, 2)
    count = int(vel.shape[0])
    if count == 0:
        return np.zeros((0, 4), np.float32)

    if str(mode) == MODE_HEADING:
        # atan2 ∈ [−π, π] → [0, 1]，色带首尾同色（环向没有接缝）
        angle = np.arctan2(vel[:, 1], vel[:, 0])
        weight = (angle + np.pi) / (2.0 * np.pi)
        rgb = _ramp(
            weight,
            [
                (0.0, _SLOW),
                (0.25, _MID),
                (0.5, _WARM),
                (0.75, _FAST),
                (1.0, _SLOW),
            ],
        )
    else:
        speed = np.linalg.norm(vel, axis=1)
        # 1.8 次方：鸟群绝大多数时间在「巡航」（0.6~0.8 倍速度上限），线性映射会整片发暖、
        # 只有极少数落在冷色档；压一下曲线让巡航色落在苔绿附近，加速时才转到赭石 / 朱砂。
        weight = np.clip(speed / max(float(max_speed), 1e-6), 0.0, 1.0) ** 1.8
        rgb = _ramp(
            weight,
            [
                # 冷色占大半：鸟群多数时间在巡航，画面主调应该是石青 / 苔绿，
                # 赭石与朱砂留给「正在加速」的少数个体
                (0.0, _SLOW),
                (0.55, _MID),
                (0.85, _WARM),
                (1.0, _FAST),
            ],
        )

    out = np.ones((count, 4), np.float32)
    out[:, :3] = np.clip(rgb, 0.0, 1.0)
    return out
