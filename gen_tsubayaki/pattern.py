"""弦线族算法层：两条轨道之间的弦（Processing 版 "Tsubayaki" 的数学核心）。

本模块**不 import bpy**，可以脱离 Blender 单独跑::

    from gen_tsubayaki.pattern import generate

    pattern = generate(n=24, t=0.0)
    pattern.points     # (2m, 3) 设计坐标
    pattern.alpha      # (m,) 逐弦不透明度

参考实现是 ``ga2/common/tsubayaki_pattern.py``（Processing / p5.js 移植版），本模块
保持它的公式与参数名，只做三件事：

1. **向量化**：参考里逐点 append 的循环改成 ``np`` 的整段运算（同一个式子，结果对拍一致）；
2. **拆掉归一化**：参考最后一步拿 ``1/180 * scale`` 把坐标塞进 Blender 单位，这里一律
   输出**设计坐标**（就是参考的像素坐标：象限边界 ±180、单块 360），居中 / 等比缩放 /
   单位换算全部交给 :mod:`scene`。于是 ``scale`` 参数消失（它等价于"画布 3 单位"）；
3. **补上"四格整铺"**：参考的 ``TILED`` 只画一个象限变体、居中放在原点，这里多给一个
   ``ALL``（把四个变体按 2x2 铺回它们本来所在的格子里，见 :func:`_quadrant`）。

**这是生成式，不是模拟**：``generate()`` 是纯函数，输出只由入参决定，没有跨帧状态，
``t`` 是参数而不是被累积的时间 —— 第 N 帧的图案由公式直接给出，所以时间轴可以随便
前后拖、可以只渲染中间某一帧（对比 ``sim_*``：那里第 N 帧必须从第 1 帧顺序算出来）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

__all__ = [
    "ALL_VARIANTS",
    "CONSTRAIN_BOUND",
    "MAX_N",
    "OUTER_ORBIT",
    "PATTERN_MODES",
    "TILE_SIZE",
    "VARIANTS",
    "VARIANT_LABELS",
    "VARIANT_MAP",
    "Pattern",
    "chord_count",
    "frame_ink",
    "generate",
]

TAU = 2.0 * math.pi

#: 外轨半径 = ``OUTER_ORBIT - n``（参考里写死 400，是它那块画布的边长量级）。
OUTER_ORBIT = 400.0

#: 默认裁剪边界（参考的 '180'）：单块 360 的一半。
CONSTRAIN_BOUND = 180.0

#: 默认单块边长（参考的 '360'）。
TILE_SIZE = 360.0

#: ``n`` 的上限：到 400 时外轨半径已经归零，再大就翻到对面去，
#: 图案退化成"穿过中心的弦束"，同时弦数（2n）也画不动了。
MAX_N = 400

#: 图案模式（存进文件的是 ASCII 标识）。
PATTERN_MODES = ("SINGLE", "TILED")

#: 象限变体 → 参考里的 ``(qx, qy)``：它们是**格心在 180 单位下的坐标**，
#: 也就是 2x2 铺开时各自所在的象限。
VARIANT_MAP = {
    "VAR_1": (1, 1),
    "VAR_2": (3, 1),
    "VAR_3": (1, 3),
    "VAR_4": (3, 3),
}

#: 四个象限变体（参考里能选的四个）。
ALL_VARIANTS = ("VAR_1", "VAR_2", "VAR_3", "VAR_4")

#: 四个变体 + 整铺（``ALL`` 是本扩展补的，参考里没有：它一次只画一个变体）。
VARIANTS = ALL_VARIANTS + ("ALL",)

#: 名称里带上级数：参考用 ``freq_mult = qx + qy * 4``，于是四格正好是 5 / 7 / 13 / 15。
VARIANT_LABELS = {
    "VAR_1": "单格 5x（左低右低）",
    "VAR_2": "单格 7x（左高右低）",
    "VAR_3": "单格 13x（左低右高）",
    "VAR_4": "单格 15x（左高右高）",
    "ALL": "四格整铺 2x2",
}


@dataclass(frozen=True, slots=True)
class Pattern:
    """一帧的弦线族（设计坐标，未做画布变换）。"""

    #: ``(2m, 3)``：每根弦两个端点，按弦序展平（``[x1, y1, z1, x2, y2, z2, ...]``）。
    points: np.ndarray
    #: ``(m,)``：每根弦的点数（恒为 2）；给 GP 后端 ``add_strokes`` 直接用。
    counts: tuple[int, ...]
    #: ``(m,)``：逐弦不透明度（参考的 Color Ramp：首弦最浓、末弦只剩 ``fade_tail``）。
    alpha: np.ndarray
    #: 帧级不透明度（参考的 ``(256 - t * 4) / 255``），已乘进 :attr:`alpha`。
    ink: float
    #: 本次用的参数，回读 / 报告用。
    n: float
    t: float
    #: 设计空间包围盒 ``(lo_x, lo_y, hi_x, hi_y)``（不含淡出，只算几何）。
    bounds: tuple[float, float, float, float]

    @property
    def chords(self) -> int:
        """弦数（= 笔划数 = ``len(counts)``）。"""
        return len(self.counts)

    @property
    def span(self) -> float:
        """包围盒较长边的边长（画布等比缩放用）。"""
        lo_x, lo_y, hi_x, hi_y = self.bounds
        return max(hi_x - lo_x, hi_y - lo_y)

    @property
    def centre(self) -> tuple[float, float]:
        """包围盒中心（俯视相机取景用）。"""
        lo_x, lo_y, hi_x, hi_y = self.bounds
        return (0.5 * (lo_x + hi_x), 0.5 * (lo_y + hi_y))


def chord_count(n: float) -> int:
    """给定 ``n``，这一族有多少根弦。

    参考是 ``for r in arange(0, TAU, PI/n)``，所以弦数是 ``ceil(2n)``；
    ``n`` 先被夹到 ``>= 1``（参考的 ``max(1.0, n_param)``，同时避免步长除零）。
    """
    return math.ceil(TAU / (math.pi / max(1.0, float(n))))


def frame_ink(t: float, rate: float = 4.0) -> float:
    """帧级不透明度：参考的 ``(256 - t * 4) / 255``，夹在 ``0..1``。

    ``t`` 一路涨上去时整族弦越来越淡（参考靠这一条做"墨迹退去"），
    ``t`` 还是 0 就是全墨。
    """
    return float(min(1.0, max(0.0, (256.0 - float(t) * float(rate)) / 255.0)))


def _r_vals(n_val: float) -> np.ndarray:
    """轨道角：``arange(0, TAU, PI/n)``（参考原式，端点不取 2π）。"""
    return np.arange(0.0, TAU, math.pi / n_val)


def _single(n_val: float, t: float, r: np.ndarray, z_amp: float, z_freq: float):
    """单轨星图：内轨半径 ``n``、频率 ``t``，外轨半径 ``400 - n``、频率 1。

    参考原式 ``line(n*sin(r*t), n*cos(r*t), (400-n)*sin(r), (400-n)*cos(r))``。
    """
    x1 = n_val * np.sin(r * t)
    y1 = n_val * np.cos(r * t)
    x2 = (OUTER_ORBIT - n_val) * np.sin(r)
    y2 = (OUTER_ORBIT - n_val) * np.cos(r)
    z1 = z_amp * np.sin(z_freq * r * t)
    z2 = z_amp * np.cos(z_freq * r)
    return x1, y1, z1, x2, y2, z2


def _quadrant(
    n_val: float,
    t: float,
    r: np.ndarray,
    variant: str,
    *,
    bound: float,
    tile_size: float,
    z_amp: float,
    z_freq: float,
    offset: tuple[float, float] = (0.0, 0.0),
):
    """分块裁剪：一个象限变体的弦（参考的 ``TILED`` 分支）。

    两处**端点裁剪**（``np.clip`` 代替 Processing 的 ``constrain``）：
    ``n`` 和 ``tile_size - n`` 常常大于裁剪边界，于是端点被按到格子的边上 ——
    弦不再"绕圆"而是贴着方块边缘走，这就是参考里"分块裁剪"的形态（同时也是为什么
    这套图案看起来像开片 / 冰裂：短弦各自贴边，拼出方形骨架）。

    ``offset`` 是这一格的格心：参考**只画一个变体、放在原点**（偏移被丢掉），所以默认
    是 ``(0, 0)``；``ALL`` 整铺时调用方把格心补回来（``±tile_size/2``），于是四格拼成
    边长 ``2 * tile_size`` 的整张 —— 每格内部各自裁到 ``±bound``，``bound`` 小于半格时
    格间留缝。
    """
    qx, qy = VARIANT_MAP[variant]
    q = r * t * (qx + qy * 4.0)

    x1 = np.clip(n_val * np.sin(q), -bound, bound) + offset[0]
    y1 = np.clip(n_val * np.cos(q), -bound, bound) + offset[1]
    x2 = np.clip((tile_size - n_val) * np.sin(r), -bound, bound) + offset[0]
    y2 = np.clip((tile_size - n_val) * np.cos(r), -bound, bound) + offset[1]
    z1 = z_amp * np.sin(z_freq * r + q)
    z2 = z_amp * np.cos(z_freq * r)
    return x1, y1, z1, x2, y2, z2


def _stack(parts) -> np.ndarray:
    """把 ``(x1, y1, z1, x2, y2, z2)`` 六条等长数组交错成 ``(2m, 3)`` 点列。"""
    points = np.empty((parts[0].size * 2, 3), dtype=np.float64)
    points[0::2, 0] = parts[0]
    points[0::2, 1] = parts[1]
    points[0::2, 2] = parts[2]
    points[1::2, 0] = parts[3]
    points[1::2, 1] = parts[4]
    points[1::2, 2] = parts[5]
    return points


def _alpha_for(chords: int, ink: float, *, fade: bool, fade_tail: float) -> np.ndarray:
    """逐弦不透明度：参考的 Color Ramp（首端 ``ink``、末端 ``ink * 0.1``，线性插值）。"""
    if not fade or chords <= 1:
        return np.full(chords, ink, dtype=np.float64)
    ramp = np.linspace(0.0, 1.0, chords)
    return ink * (1.0 - (1.0 - float(fade_tail)) * ramp)


def generate(
    n: float,
    t: float = 0.0,
    *,
    mode: str = "SINGLE",
    variant: str = "VAR_1",
    constrain_bound: float = CONSTRAIN_BOUND,
    tile_size: float = TILE_SIZE,
    z_wave_amp: float = 0.0,
    z_wave_freq: float = 2.0,
    fade: bool = True,
    fade_rate: float = 4.0,
    fade_tail: float = 0.1,
) -> Pattern:
    """生成一帧的弦线族。

    :param n: 轨道半径 / 步长（Processing 的 ``n``），夹到 ``1..MAX_N``；弦数 = ``2n``。
    :param t: 动态频率（Processing 的 ``t``）—— **参数**，不是被累积的时间。
    :param mode: ``"SINGLE"`` 单轨星图 / ``"TILED"`` 分块裁剪。
    :param variant: :data:`VARIANTS` 之一；``"ALL"`` = 四个变体按 2x2 整铺。
    :param constrain_bound: 分块裁剪的边界（参考 180，即单块 360 的一半）。
    :param tile_size: 单块边长（参考 360）。
    :param z_wave_amp: Z 向波动振幅，单位是**设计坐标**（±180 的量级），随画布等比缩放。
    :param z_wave_freq: Z 向波动频率倍数。
    :param fade: 逐弦淡出（参考的 Color Ramp + 帧级墨色衰减）。
    :param fade_rate: 帧级墨色衰减率（参考写死 4.0）：``ink = (256 - t * rate) / 255``。
    :param fade_tail: 末弦保留的墨量比例（参考 0.1）。
    """
    n_val = float(min(float(MAX_N), max(1.0, float(n))))
    t_val = float(t)
    r = _r_vals(n_val)
    z_amp = float(z_wave_amp)
    z_freq = float(z_wave_freq)

    if str(mode).upper() == "TILED":
        # "ALL" = 四格整铺：格心 = (qx - 2, qy - 2) * 半格，即 ±tile_size/2；
        # 单个变体照参考居中放在原点（偏移丢掉）。
        half = 0.5 * float(tile_size)
        names = list(VARIANT_MAP) if variant == "ALL" else [str(variant)]
        quads = []
        for name in names:
            if name not in VARIANT_MAP:
                continue
            qx, qy = VARIANT_MAP[name]
            spread = (
                (0.0, 0.0)
                if variant != "ALL"
                else ((qx - 2.0) * half, (qy - 2.0) * half)
            )
            quads.append(
                _quadrant(
                    n_val,
                    t_val,
                    r,
                    name,
                    bound=float(constrain_bound),
                    tile_size=float(tile_size),
                    z_amp=z_amp,
                    z_freq=z_freq,
                    offset=spread,
                )
            )
        if not quads:
            quads = [
                _quadrant(
                    n_val,
                    t_val,
                    r,
                    "VAR_1",
                    bound=float(constrain_bound),
                    tile_size=float(tile_size),
                    z_amp=z_amp,
                    z_freq=z_freq,
                )
            ]
        parts = tuple(
            np.concatenate([quad[index] for quad in quads]) for index in range(6)
        )
    else:
        parts = _single(n_val, t_val, r, z_amp, z_freq)

    points = _stack(parts)
    chords = points.shape[0] // 2

    ink = frame_ink(t_val, fade_rate) if fade else 1.0
    alpha = _alpha_for(chords, ink, fade=bool(fade), fade_tail=float(fade_tail))

    xs = points[:, 0]
    ys = points[:, 1]
    bounds = (float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max()))
    return Pattern(
        points=points,
        counts=(2,) * chords,
        alpha=alpha,
        ink=float(ink),
        n=n_val,
        t=t_val,
        bounds=bounds,
    )
