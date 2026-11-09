"""「贴零 → 生育」动画的时间轴：纯算法层，不 import bpy。

视频（TheGrayCuber / imaginary_bases）在 4:18 之后用 base −1+i、数字 {0, 1} 讲的构造，
可以拆成两个动作，反复做就能长出整片点集：

======  ==========================  ==========================================
贴零    ``n → n·b``                 乘底数 = **旋转 arg(b) + 缩放 |b|**。整片
                                    形状在原地转着长大，仍然是一个缩放副本 ——
                                    这就是"收缩比统一"的那一面。
生育    ``n → n + d``（d ∈ D）      每个数字一份拷贝，``|D|`` 份。上一级留下的
                                    空隙就是被这一步填掉的 —— 这是"分支数"那一面。
======  ==========================  ==========================================

于是 ``places`` 位的点集满足递推

    S₁ = D,        S_{k+1} = b·S_k + D

**为什么必须分成两步**：一步到位的话点会从父辈直接跳到子辈的落点，「整体旋转放大」和
「按数字裂开」两件事糊在一起，就看不出分形是怎么来的了。收缩比 ``|b|`` 与分支数 ``|D|``
刚好配平（``|D| = N(b) = |b|²``），所以每裂一次，形状的轮廓不变、只是被切得更碎 ——
这正是上面那条递推的几何含义。

**旋转是真的旋转**：环元素嵌进复平面后，乘以 ``b^t`` 就是旋转 ``t·arg(b)``、缩放
``|b|^t``，所以缩放阶段用 ``b^t``（``t`` 从 0 到 1）逐帧插值，走出的是一条对数螺线，
而不是把两端坐标线性插值。线性插值也能动，但转动是假的（点走弦而不是弧）。

**上色**：颜色跟着**第 1 级的那两个（几个）数字**走 —— 也就是每个点的「最高位」，静态输出
里 ``LEAD`` 那一档。这样生育那一步看起来是"一团裂成 |D| 团、每团各守着自己的颜色"，一路
跟下去就能看出整片点集 = ``|D|`` 个缩放副本；最后一帧的颜色与静态输出选「最高位」时
**逐点一致**。

（按"刚贴上去的那一位"上色也说得通 —— 那正是视频里 "each color is now its own triangle"
的画面 —— 但最低位在方格点阵上就是奇偶，最后一帧会变成一张棋盘格，反而把龙形糊掉了。）

动画本身不做 ``AGGREGATE`` 的逐层淡出：视频里"每一份拷贝各是一块纯色"才是看懂分支的关键，
淡出反而把层级糊掉。所以静态输出里选「最高位」时，动画与它看起来完全是一回事。
"""

from __future__ import annotations

import cmath
from collections import namedtuple

import numpy as np

from . import ring

__all__ = [
    "FRAMES_DEFAULT",
    "HOLD_DEFAULT",
    "STROKE_LIMIT",
    "Frame",
    "estimate_strokes",
    "make_plan",
    "timeline",
]

#: 每个相位（贴零 / 生育）默认几帧。24 fps 下 8 帧 ≈ 三分之一秒，够看出转动。
FRAMES_DEFAULT = 8
#: 每一级做完后停几帧 —— 不停的话"长好了"这一瞬间会被下一级的转动直接盖掉。
HOLD_DEFAULT = 2
#: 动画的笔划总预算。每帧都是一张独立的 drawing，所有帧的点数加起来才是真正的开销；
#: 超了就自动少做一级（与静态输出的点数预算同一个思路）。
STROKE_LIMIT = 80_000

#: 一帧的画面：``xy`` 是 ``(n, 2)`` 的平面坐标，``index`` 是地址，``slot`` 是出生用到的
#: 数字编号（= 颜色编号），``shade`` 是淡出系数。
Frame = namedtuple("Frame", "number phase level t xy index slot shade")


# ---------------------------------------------------------------------------
# 复平面
# ---------------------------------------------------------------------------


def _complex(domain: str, pair) -> complex:
    """环元素 ``(a, b)`` → 复数。两个域的 ``coords_of`` 本来就是同一个嵌入
    （``1 → 1``，``ω → e^{2πi/3}``），所以乘法的几何就是旋转 + 缩放。"""
    x, y = ring.coords_of(domain, float(pair[0]), float(pair[1]))
    return complex(float(x), float(y))


def _powers(base: complex, t: float) -> complex:
    """``b^t``：旋转 ``t·arg(b)``、缩放 ``|b|^t``。"""
    if abs(base) < 1e-12:
        return 0j
    return cmath.exp(t * cmath.log(base))


def _xy(positions: np.ndarray) -> np.ndarray:
    return np.stack([positions.real, positions.imag], axis=1).astype(np.float32)


# ---------------------------------------------------------------------------
# 预算
# ---------------------------------------------------------------------------


def estimate_strokes(count: int, levels: int, per_phase: int, hold: int) -> int:
    """整段动画的笔划总数（= 各帧点数之和）。

    第 1 级停 ``hold`` 帧；之后每一级是「贴零 ``per_phase`` 帧（点数 ``count^k``）+
    生育 ``per_phase`` 帧（点数 ``count^{k+1}``）+ 停留 ``hold`` 帧（点数 ``count^{k+1}``）」。
    超过 ``STROKE_LIMIT`` 时提前返回一个大数，不必算完。
    """
    count = max(2, int(count))
    total = hold * count
    for level in range(1, max(1, int(levels))):
        scale = count**level
        born = scale * count
        total += per_phase * scale + (per_phase + hold) * born
        if total > STROKE_LIMIT * 4:
            return total
    return total


def make_plan(domain, base, digits, places, *, per_phase=FRAMES_DEFAULT, hold=HOLD_DEFAULT):
    """定下「做几级、共几帧」，并把级数夹到笔划预算装得下为止。

    :return: ``{"count", "levels", "frames", "strokes", "capped", "places"}``；
        ``capped`` 为真表示原本要做的级数被预算砍掉了。
    """
    count = len(digits)
    places = max(1, int(places))
    per_phase = max(1, int(per_phase))
    hold = max(0, int(hold))
    if count < 2 or (base[0] == 0 and base[1] == 0):
        return {
            "count": count,
            "levels": 0,
            "frames": 0,
            "strokes": 0,
            "capped": False,
            "places": places,
        }

    levels = places
    capped = False
    while levels > 1 and estimate_strokes(count, levels, per_phase, hold) > STROKE_LIMIT:
        levels -= 1
        capped = True

    strokes = estimate_strokes(count, levels, per_phase, hold)
    # 第 1 级至少停 1 帧，否则"每级停留 0 + 只做 1 级"会产出 0 帧
    frames = max(1, hold) + max(0, levels - 1) * (2 * per_phase + hold)
    return {
        "count": count,
        "levels": levels,
        "frames": frames,
        "strokes": strokes,
        "capped": capped,
        "places": places,
    }


# ---------------------------------------------------------------------------
# 时间轴
# ---------------------------------------------------------------------------


def timeline(
    domain: str,
    base,
    digits,
    levels: int,
    *,
    per_phase: int = FRAMES_DEFAULT,
    hold: int = HOLD_DEFAULT,
):
    """逐帧产出 :class:`Frame`。帧号从 1 开始，连续。

    ``shade`` 恒为 1（纯正的数字色）—— 见模块文档「上色」那一节。
    """
    count = len(digits)
    if count < 2 or levels < 1:
        return
    per_phase = max(1, int(per_phase))
    hold = max(0, int(hold))

    base_c = _complex(domain, base)
    digit_c = np.array([_complex(domain, pair) for pair in digits], dtype=np.complex128)
    digit_index = np.arange(count, dtype=np.int64)

    # --- 第 1 级：数字自己 ------------------------------------------------
    positions = digit_c.copy()
    index = digit_index.copy()
    slot = digit_index.astype(np.int32)
    shade = np.ones(count, dtype=np.float32)

    number = 0

    def emit(phase, level, t, xy, idx, slots, shades):
        nonlocal number
        number += 1
        return Frame(number, phase, level, t, xy, idx, slots, shades)

    for _ in range(max(1, hold)):
        yield emit("hold", 1, 0.0, _xy(positions), index, slot, shade)

    for level in range(1, levels):
        # --- 贴零：整片乘 b^t，转着长大 -----------------------------------
        for step in range(1, per_phase + 1):
            t = step / per_phase
            yield emit(
                "scale", level, t, _xy(positions * _powers(base_c, t)), index, slot, shade
            )

        # --- 生育：每份拷贝平移 d·t ---------------------------------------
        # 先算好"落点"那一份（t=1）的状态，中间帧只是把它按 t 往回拉。
        # 顺序必须是**父辈优先**：每个父辈连着摊开 count 个子辈，与 np.repeat 一致。
        #
        # 地址怎么变：贴零 = 乘 b，在地址里就是**末尾补一个 0**（最低位左移一位），
        # 生育再把那个 0 换成某个数字 d。所以 n' = d + q·n —— 新生出来的是**第 1 位**
        # （最低位），不是最高位。静态输出的「最高位」是这一支最初的祖先，反过来看
        # 就是"整片 = |D| 个缩放副本"。
        parents = len(index)
        scaled = positions * base_c
        parent_pos = np.repeat(scaled, count)
        offsets = np.tile(digit_c, parents)
        child_index = np.tile(digit_index, parents) + count * np.repeat(index, count)
        # 颜色**不跟**新生出来的那一位，而是继承祖先 —— 见模块文档「上色」
        child_slot = np.repeat(slot, count).astype(np.int32)
        child_shade = np.ones(len(parent_pos), dtype=np.float32)

        for step in range(1, per_phase + 1):
            t = step / per_phase
            yield emit(
                "birth",
                level + 1,
                t,
                _xy(parent_pos + offsets * t),
                child_index,
                child_slot,
                child_shade,
            )

        # --- 落到下一级，成为下一轮的起点 ---------------------------------
        positions = parent_pos + offsets
        index = child_index
        slot = child_slot
        shade = child_shade

        for _ in range(hold):
            yield emit("hold", level + 1, 0.0, _xy(positions), index, slot, shade)
