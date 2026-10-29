"""相空间场 → 笔划缓冲（点列 / 逐点半径 / 逐点颜色 / 标量属性，不 import bpy）。

一个双摆 = **三条笔划**（顺序就是绘制顺序，自下而上）:

======  ========  ============================================================
#       点数      内容
======  ========  ============================================================
0       2         下摆（关节 → 摆锤），等宽，两端圆头
1       2         上摆（悬挂点 → 关节），等宽，两端圆头，盖在下摆之上
2       1         扣子（关节处的一个点），半径放大，盖住两根杆的接缝
======  ========  ============================================================

**三笔分色**是这套画法的关键：一条 3 点折线只能给"逐点颜色"，渲染出来是从悬挂点到摆锤
的渐变，看不出"两根杆"的结构。拆成三笔以后每条笔划内部颜色恒定，扣子和下摆同色、上摆
另一种颜色 —— 交界处就是一个"扣住"的接头，N 大时双摆分形的色带也更清楚。

缓冲（``S`` = 双摆数，``M = 5S`` 个点）:

======================  ==================  ==================================
缓冲                    形状                说明
======================  ==================  ==================================
``sizes``               (3S,)               每条笔划 2 / 2 / 1 点（GP ``add_strokes``）
``points``              (M, 3) f32          世界坐标，逐帧整条重写
``radii``               (M,) f32            逐点半径：杆等宽，扣子 × 扣子倍数
``colours``             (M, 4) f32 **线性** RGBA，每条笔划内部恒定
``cyclic``              (3S,) bool          全 False
======================  ==================  ==================================

配色四档，每档都**分别**给上摆和下摆一个颜色（满标不走逐帧自归一化，否则热力图每帧
整体变色，看不出能量在演化）：

* ``SPECTRUM`` —— 色相：上摆 ``θ1``、下摆 ``θ2`` 各铺满一整圈色轮（N 大时看分形用这档）；
* ``SPEED``    —— 上摆 ``|dθ1|``、下摆 ``|dθ2|``，石青 → 赭石 → 朱砂；
* ``PHASE``    —— 上摆 ``½(1−cosθ1)``、下摆 ``½(1−cosθ2)``，垂下去冷、举起来暖；
* ``MONO``     —— 两根杆同色（面板给的颜色）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .palette import (
    AZURITE,
    CINNABAR,
    OCHRE,
    hex_to_linear,
    hex_to_rgb,
    srgb_to_linear,
)

__all__ = [
    "DOT_SCALE_REFERENCE",
    "LAYER",
    "DUAL_LOWER",
    "DUAL_UPPER",
    "MODE_DUAL",
    "MODE_PHASE",
    "MODE_SPECTRUM",
    "MODE_SPEED",
    "MODES",
    "POINTS_PER_PENDULUM",
    "SIZES",
    "STROKES_PER_PENDULUM",
    "Strokes",
    "build",
]

#: 图层名（GP 后端只有这一层；网格后端不看它）。
LAYER = "Pendulums"

MODE_SPECTRUM = "SPECTRUM"
MODE_SPEED = "SPEED"
MODE_PHASE = "PHASE"
MODE_DUAL = "DUAL"

#: 双色模式的缺省两色（参考实现的 ``C1_DATA`` / ``C2_DATA``，sRGB）。
DUAL_UPPER = "#796BAF"
DUAL_LOWER = "#719C73"

#: 上面两色的 sRGB 三元组（``build()`` 的缺省参数用；面板那边直接读十六进制）。
_DUAL_DEFAULT = (hex_to_rgb(DUAL_UPPER), hex_to_rgb(DUAL_LOWER))

MODES = [
    (
        MODE_SPECTRUM,
        "全色相（相空间）",
        (
            "上摆 θ1、下摆 θ2 各铺满一整圈色相：整个色轮都用上，N 上百时双摆分形的"
            "色带最清楚，动画里颜色会整片流动"
        ),
    ),
    (
        MODE_SPEED,
        "角速度",
        "上摆 |dθ1|、下摆 |dθ2| 走石青 → 赭石 → 朱砂：静止的冷、甩起来烧红",
    ),
    (
        MODE_PHASE,
        "相角",
        "上摆 ½(1−cosθ1)、下摆 ½(1−cosθ2)：垂下去石青、举起来朱砂，初值相图读得出来",
    ),
    (
        MODE_DUAL,
        "双色",
        "上摆一个固定颜色、下摆（和扣子）另一个：面板里两个取色器，缺省取参考实现的紫 / 绿",
    ),
]

#: 画法一：一个双摆三条笔划 —— 下摆 2 点 / 上摆 2 点 / 扣子 1 点。
LAYOUT_RODS = "RODS"
#: 画法二：一个双摆**一个点**（一像素一个摆）—— 位置就在网格上，逐帧只变颜色，
#: 所以位置缓冲一次写死，每帧只写颜色 / 半径。N 上千（百万摆）只能走这档。
LAYOUT_DOTS = "DOTS"

LAYOUTS = [
    (
        LAYOUT_RODS,
        "杆（三笔）",
        "下摆 / 上摆 / 扣子三笔，等宽圆头，逐点半径 —— 小、中规模的本命画法",
    ),
    (
        LAYOUT_DOTS,
        "点（一像素一个摆）",
        "一个摆一个圆点，位置固定、逐帧只改颜色：笔划数就是摆数，N=1000（百万摆）也放得下",
    ),
]

#: 各画法下"一个双摆"占几条笔划、每条几个点。
SIZES_RODS = (2, 2, 1)
SIZES_DOTS = (1,)

#: 扣子半径 / 杆半径 —— 参考实现就是 ``0.05 : 0.08``，取 5/8。
DOT_SCALE_REFERENCE = 5.0 / 8.0

#: 兼容旧名（三笔画法），面板与外部脚本都用它。
SIZES = SIZES_RODS
POINTS_PER_PENDULUM = sum(SIZES_RODS)
STROKES_PER_PENDULUM = len(SIZES_RODS)


def sizes_of(layout: str) -> tuple:
    """画法 → 每摆的笔划点数（``(2,2,1)`` 或 ``(1,)``）。"""
    return SIZES_DOTS if str(layout).upper() == LAYOUT_DOTS else SIZES_RODS


#: 色带（线性空间）：冷 → 暖，与 sim_hilbert / sim_catenary 同一套。
_STOPS = np.array(
    [hex_to_linear(AZURITE), hex_to_linear(OCHRE), hex_to_linear(CINNABAR)],
    np.float32,
)

#: 角速度色带的节点位置：中段偏后，慢慢晃的部分保持冷色，甩起来才烧红。
_SPEED_XS = np.array([0.0, 0.55, 1.0], np.float32)
#: 相角色带均匀铺开（½(1−cosθ) 本身就是 0..1）。
_PHASE_XS = np.array([0.0, 0.5, 1.0], np.float32)

#: 全色相档的饱和度（不拉满 1.0：纯原色在纸上会跳，0.9 已经占满色轮）。
_SPECTRUM_SATURATION = 0.9

#: 杆画法里两根杆的色相各偏多少（1/12 = 30°，合计两根杆差 60°）。
#: 调成 0 就是两根杆同色；0.25 就是互补色。
_SPECTRUM_SPLIT = 1.0 / 12.0

#: sRGB → 线性 的查表精度。直接对 3 万个分量算 ``x ** 2.4`` 要 ~0.8 ms/帧，
#: 查表（4096 级，误差 ≤ 6e−4）只要 ~0.1 ms。
_LINEAR_LUT_SIZE = 4096
_LINEAR_LUT = None

#: 色轮三通道的相位偏移（红 / 绿 / 蓝），见 :func:`_hsv_linear`。
_HSV_OFFSETS = np.array([0.0, 4.0, 2.0], np.float32)


def _srgb_to_linear_lut(values: np.ndarray) -> np.ndarray:
    """``values**(2.4 分段)`` 的查表版（结果与逐元素精确公式差 ≤ 6e−4）。"""
    global _LINEAR_LUT
    if _LINEAR_LUT is None:
        steps = np.linspace(0.0, 1.0, _LINEAR_LUT_SIZE, dtype=np.float32)
        _LINEAR_LUT = np.where(
            steps <= 0.04045, steps / 12.92, ((steps + 0.055) / 1.055) ** 2.4
        ).astype(np.float32)
    index = np.clip(values * (_LINEAR_LUT_SIZE - 1), 0, _LINEAR_LUT_SIZE - 1)
    return _LINEAR_LUT[index.astype(np.int32)]


#: 全色相档的 8 bit 色轮（256 级，线性 RGBA）。百万点时逐点算 HSV 要 30 ms，
#: 改成"量化到 256 级 + 查表"只要 6~10 ms；1M 像素的图上看不出 256 级量化。
_WHEEL_STEPS = 256
_WHEEL = None


def _wheel_rgba(angles: np.ndarray) -> np.ndarray:
    """角度 → 色轮查表 ``(count,4)`` 线性 RGBA（点阵画法的快路径）。"""
    global _WHEEL
    if _WHEEL is None:
        steps = np.linspace(0.0, 1.0, _WHEEL_STEPS, endpoint=False, dtype=np.float32)
        rgb = _hsv_linear(steps, _SPECTRUM_SATURATION)
        _WHEEL = np.concatenate(
            [rgb, np.ones((_WHEEL_STEPS, 1), np.float32)], axis=1
        ).astype(np.float32)
    index = np.clip(
        (np.asarray(angles, np.float32) / np.float32(2.0 * np.pi) + 0.5)
        % 1.0
        * _WHEEL_STEPS,
        0,
        _WHEEL_STEPS - 1,
    ).astype(np.int32)
    return _WHEEL[index]


@dataclass(frozen=True)
class Strokes:
    """一个后端要画的一批笔划（拓扑 + 点列 + 逐点半径 + 逐点颜色 + 标量）。"""

    layer: str
    #: (S·画法笔划数,) 每条笔划的点数（杆：2/2/1；点：1），相加 = ``len(points)``。
    sizes: np.ndarray
    #: (M,3) 世界坐标点列（笔划 k 的点从 ``offsets[k]`` 开始）。
    points: np.ndarray
    #: (M,) 逐点半径（世界单位，GP 的 ``radius``）。
    radii: np.ndarray
    #: (M,4) **线性** RGBA，逐点。
    colours: np.ndarray
    #: 每条摆占几条笔划（杆 3 / 点 1）。
    strokes_per_pendulum: int
    #: (S·笔划数,) 是否闭合。
    cyclic: np.ndarray
    #: ``((名字, (M,) float32), ...)``：额外的 POINT 域 FLOAT 属性（数据接口）。
    extras: tuple = ()
    #: 点列是否**逐帧不变**（点阵画法：摆钉在网格上，只换颜色）。后端的 ``refresh``
    #: 因此可以跳过位置写回 —— 百万点时这一项就是 12 MB/帧。
    static_points: bool = False
    #: 点阵的世界边长（``N×间距``）：图像后端要把它撑成一块面。杆画法不用（0）。
    extent: float = 0.0

    @property
    def pendulums(self) -> int:
        """双摆个数（每条摆占 ``strokes_per_pendulum`` 条笔划）。"""
        return len(self.sizes) // max(1, int(self.strokes_per_pendulum))


def _ramp(values: np.ndarray, xs: np.ndarray) -> np.ndarray:
    """分段线性色带 → ``(n,3)`` 线性 RGB。"""
    out = np.empty((values.size, 3), np.float32)
    for channel in range(3):
        out[:, channel] = np.interp(values, xs, _STOPS[:, channel])
    return out


def _hue(angles: np.ndarray) -> np.ndarray:
    """角度 [-π, π] → 色相 [0, 1]（-π 与 +π 在色轮上接得住）。"""
    return ((np.asarray(angles, np.float32) / np.float32(2.0 * np.pi)) + 0.5) % 1.0


def _hsv_linear(hue: np.ndarray, saturation: float = 1.0, value: float = 1.0):
    """色相 → **线性** RGB（中间过一遍 sRGB 的 HSV 公式，否则暗部会偏）。

    用满 0..1 的色相走完六段：这是"占满整个颜色空间"的那条路 —— 相角本来就是周期量，
    映射到色轮上是最自然的读法。

    实现走**三段三角波**（``|((h·6+offset) mod 6) − 3| − 1`` 再钳到 0..1），三个通道一次
    广播算完、取模用一次比较代替 ``fmod``：1 万摆（2 万个颜色）~0.35 ms，比"六次掩码赋值"
    的 1.0 ms 快三倍 —— 每帧都要算一次，值得。
    """
    hues = np.asarray(hue, np.float32) * 6.0
    hues = hues - 6.0 * (hues >= 6.0)  # 色相已在 [0,1)，只需处理 =1 的边界
    table = hues[:, None] + _HSV_OFFSETS  # (n, 1) + (3,) → (n, 3)
    table -= 6.0 * (table >= 6.0)  # 等价于 mod 6，但不用 fmod
    np.abs(table - 3.0, out=table)
    table -= 1.0
    np.clip(table, 0.0, 1.0, out=table)
    # 满饱和时就是纯色轮；乘 S·V 再抬 V(1−S) 等价于 HSV 的 (C, m) 分解
    table *= value * saturation
    table += value * (1.0 - saturation)
    # sRGB → 线性（颜色属性按线性存，见 palette.srgb_to_linear）；走查表，见 _srgb_to_linear_lut
    return _srgb_to_linear_lut(table)


def _rod_colours(mode: str, phase_field, speed_max: float, dual, single: bool = False):
    """两根杆各自的颜色 ``(上摆, 下摆)``，各 ``(count,3)`` 线性 RGB。

    ``single=True`` 时只算上摆那一路（点阵画法只要一个颜色），百万点时省掉一半配色开销。
    """
    mode = str(mode)
    count = int(phase_field.count)

    if mode == MODE_DUAL:
        # 双色：上摆一个固定色、下摆（连同扣子）另一个固定色
        upper_colour, lower_colour = dual
        upper = np.tile(
            np.array([srgb_to_linear(float(c)) for c in upper_colour], np.float32),
            (count, 1),
        )
        if single:
            return upper, None
        return upper, np.tile(
            np.array([srgb_to_linear(float(c)) for c in lower_colour], np.float32),
            (count, 1),
        )

    if mode == MODE_SPECTRUM:
        # 色相取两根摆角的**平均**：θ1 沿 X、θ2 沿 Z 等权，等值线正好转成 45° 斜纹
        # （只取 θ1 是竖条、只取 θ2 是横条）。
        base = _hue(
            0.5
            * (
                np.asarray(phase_field.th1, np.float32)
                + np.asarray(phase_field.th2, np.float32)
            )
        )
        if single:
            # 点阵：一个像素只能带一个颜色，直接用这个斜纹色相
            return _hsv_linear(base, _SPECTRUM_SATURATION), None
        # 杆画法：两根杆各偏 ±30°（合计 60°），斜纹方向不变、两根杆仍然一眼分得开
        upper = _hsv_linear((base + _SPECTRUM_SPLIT) % 1.0, _SPECTRUM_SATURATION)
        lower = _hsv_linear((base - _SPECTRUM_SPLIT) % 1.0, _SPECTRUM_SATURATION)
        return upper, lower

    if mode == MODE_PHASE:
        upper = _ramp(
            np.clip(
                0.5 * (1.0 - np.cos(np.asarray(phase_field.th1, np.float32))), 0.0, 1.0
            ),
            _PHASE_XS,
        )
        if single:
            return upper, None
        lower = _ramp(
            np.clip(
                0.5 * (1.0 - np.cos(np.asarray(phase_field.th2, np.float32))), 0.0, 1.0
            ),
            _PHASE_XS,
        )
        return upper, lower

    # 角速度：两根杆各看自己的角速度
    speed1, speed2 = phase_field.rod_speeds()
    scale = max(float(speed_max), 1e-6)
    # 0.7 次幂：低速段仍是冷色，中速就开始偏暖，峰值满格
    upper = _ramp(np.power(np.clip(speed1 / scale, 0.0, 1.0), 0.7), _SPEED_XS)
    if single:
        return upper, None
    return upper, _ramp(np.power(np.clip(speed2 / scale, 0.0, 1.0), 0.7), _SPEED_XS)


def build(
    phase_field,
    mode: str = MODE_SPEED,
    speed_max: float = 12.0,
    width: float = 0.08,
    dot_scale: float = DOT_SCALE_REFERENCE,
    dual=_DUAL_DEFAULT,
    layout: str = LAYOUT_RODS,
    extras: bool = True,
) -> Strokes:
    """把当前场状态摊成笔划缓冲（每帧调一次，拓扑不变）。

    ``layout`` 决定一个摆画成什么：三笔（下摆 / 上摆 / 扣子）还是一个点。
    ``extras=False`` 不摊那 6 个标量属性（图像后端用不到它们，百万点时省 ~20 ms）。
    """
    count = int(phase_field.count)
    sizes_tuple = sizes_of(layout)
    per_pendulum = sum(sizes_tuple)
    per_strokes = len(sizes_tuple)
    dots = per_pendulum == 1
    total = count * per_pendulum

    # 点阵 + 全色相走"量化色轮查表"（百万点省 ~25 ms）；其余档位照常算两根杆的颜色
    wheel = dots and str(mode) == MODE_SPECTRUM
    if wheel:
        upper = lower = None
    else:
        upper, lower = _rod_colours(mode, phase_field, speed_max, dual, single=dots)

    if dots:
        # 点阵：摆钉在网格上，位置一次算好、逐帧不变（后端因此可以跳过位置写回）
        points = np.asarray(phase_field.dot_points(), np.float32).reshape(count, 1, 3)
        colours = np.empty((count, 1, 4), np.float32)
        if wheel:
            # 色相取两根摆角的平均 → 初值色带是 45° 斜纹
            colours[:, 0, :] = _wheel_rgba(
                0.5 * (np.asarray(phase_field.th1) + np.asarray(phase_field.th2))
            )
        else:
            colours[:, 0, :3] = upper  # 一个点只能带一个颜色，取 θ1 那一路
            colours[:, 0, 3] = 1.0
        radii = np.full(
            count,
            0.5 * float(getattr(phase_field, "spacing", 2.4)) * 0.92,
            np.float32,
        )
    else:
        # GPU 后端（taichi_field）已经在 kernel 里按三笔排好点列，直接拿来用；
        # NumPy 后端的关键点是 [悬挂点, 关节, 摆锤]，这里重排成"下摆 / 上摆 / 扣子"
        ready = getattr(phase_field, "stroke_points", None)
        if callable(ready):
            points = np.asarray(ready(), np.float32).reshape(count, per_pendulum, 3)
        else:
            key = phase_field.points()
            points = np.empty((count, per_pendulum, 3), np.float32)
            points[:, 0, :] = key[:, 1, :]  # 下摆：关节
            points[:, 1, :] = key[:, 2, :]  # 下摆：摆锤
            points[:, 2, :] = key[:, 0, :]  # 上摆：悬挂点
            points[:, 3, :] = key[:, 1, :]  # 上摆：关节
            points[:, 4, :] = key[:, 1, :]  # 扣子：关节上的一个点

        colours = np.empty((count, per_pendulum, 4), np.float32)
        colours[:, :, 3] = 1.0
        colours[:, 0, :3] = lower
        colours[:, 1, :3] = lower
        colours[:, 2, :3] = upper
        colours[:, 3, :3] = upper
        colours[:, 4, :3] = lower  # 扣子跟下摆同色

        rod = float(width)
        radii = np.empty(per_pendulum, np.float32)
        radii[:4] = rod
        radii[4] = rod * float(dot_scale)
        radii = np.tile(radii, count)

    attributes = ()
    if extras:
        speed1, speed2 = phase_field.rod_speeds()
        attributes = tuple(
            (
                name,
                np.repeat(np.asarray(values, np.float32), per_pendulum),
            )
            for name, values in (
                ("theta1", phase_field.th1),
                ("theta2", phase_field.th2),
                ("speed", np.hypot(speed1, speed2)),
                ("energy", phase_field.energies()),
                ("speed_upper", speed1),
                ("speed_lower", speed2),
            )
        )

    return Strokes(
        layer=LAYER,
        sizes=np.tile(np.array(sizes_tuple, np.int32), count),
        points=np.ascontiguousarray(points.reshape(total, 3), np.float32),
        radii=radii,
        colours=np.ascontiguousarray(colours.reshape(total, 4), np.float32),
        strokes_per_pendulum=per_strokes,
        cyclic=np.zeros(count * per_strokes, bool),
        extras=attributes,
        static_points=dots,
        extent=(
            float(round(np.sqrt(count))) * float(getattr(phase_field, "spacing", 2.4))
            if dots
            else 0.0
        ),
    )
