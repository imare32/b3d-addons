"""交织：把股线按"一上一下"切开，再把缺口补平（**不 import bpy**）。

算法层只出"刀口在哪"，出图是 ``scene`` + ``gp`` 的事。三件事：

1. **切**：每个交叉点，压在下面的那一段沿弧长退到对方 footprint 之外；
2. **补**：退出来的缺口用一块"沿对方边缘裁齐"的多边形补上，端头于是变成一条斜边；
3. **留边**：描边层整根不切（它是最深的一层，对方的本体与描边都会盖住它），
   所以露出来的那一圈蕾丝边始终连续。

## 切口距离

``reach`` 是**可见半宽**（本体与描边取大者），``sinθ`` 是交叉角的正弦::

    d = reach · (1 + cosθ) / sinθ = reach / tan(θ/2)

推导：平头的两个角到对方中心线分别是 ``d·sinθ ∓ reach·cosθ``，让靠里的那个角正好
贴住对方边缘，解出上式。90° 时 ``d = reach``（切口正好压在对方边缘线上），角度越斜
退得越远（56° → `1.87×reach`、45° → `2.41×`、22.5° → `5.0×`），退出来的缺口交给补片。

**为什么必须退出、不能贴平**：遮挡关系只由对方那一圈深色描边承载，本体是浅色；
浅色一旦伸进对方 footprint，就必然从对方描边上咬掉一块，"谁压着谁"当场就没了。

**为什么不能套浅角钳制**（``SIN_FLOOR``）：把 ``sinθ`` 截断会让 ``d`` 短于需要的退让
距离，切口一角照样戳进对方 footprint。代价是角度越斜退得越远，那片缺口由补片补。

## 补片

笔划的端头只能垂直于自己，而对方边缘是斜的 —— 斜交叉上两者必然错开：要么退到远处
留一个缺口，要么压进去把对方描边咬掉。补片 = 在本体局部坐标里取一块矩形，用"对方
边缘"那条半平面一刀裁过去，剩下的就是"对方边缘外侧"那块，外缘是一条**平行于对方
边缘**的直线 —— 贴平与遮挡同时成立（等同布尔多边形方案里的那一刀，但只有几次加法）。

三条铁律：

* 裁剪平面**只取本次交叉的对方股线**。曾经试过把附近所有带子都加进来（怕补片戳到
  隔壁），结果会切到缺口本身：补片被裁没、或者漏一条缝。**不要走回头路**；
* 补片的半宽取**本体**半宽，让位距离取**可见半宽 + 缝隙**：多出来的那一圈（描边）
  留着当蕾丝边，补片不该把它填掉；
* 补片**只填不描**（``stroke=None``），与本体同色，看起来就是同一根带子被削平了一个
  斜角。
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass

from .girih import Crossing, Point, Strand

__all__ = [
    "STUB",
    "Piece",
    "WeaveResult",
    "build_weave",
]

#: 残段：短于这个（× 可见半宽）的碎片直接丢掉 —— 它只会画成一个圆点。
STUB = 0.5

#: 补片越过切口的那点重叠（× 本体半宽），**只**为了和笔划端头叠住、不留缝。
#:
#: 早先取一整个半宽：带宽一粗，补片尾巴就伸到隔壁带子的描边环上，在别人的深色环上
#: 压出一道浅色缺口。四分之一个半宽就够叠住。
TIP_OVERLAP = 0.25

#: 缺口短于这个（× 可见半宽）就不补：交叉角接近 90° 时切口本来就贴着对方边缘。
TIP_MIN = 0.5

#: 残段：短于这个（× 可见半宽）的段**不给它让路，而是撤掉造出它的那一刀**。
#:
#: 切口一直在吃带子，`gap > 0` 时股线被拆成小环、切口又密，很容易把某一段吃得只剩
#: 几个像素。那颗"珠子"画出来比"这里不编"难看得多：留着它是一颗浮着的点，丢掉它
#: 是一个洞（下面的描边露出来）。所以两级处理——
#:
#: * 短于 :data:`STUB`：直接丢（它只会画成一个圆点）；
#: * 短于 ``PRUNE``：把造出它的那一刀收回去，带子连续地穿过去，代价是那处交叉没有
#:   上下之分（计入 ``stats["pruned"]``）。
PRUNE = 2.0

#: 收刀的轮数：一轮只撤一刀（撤完要重新看那段），所以每股线最多撤这么多刀。
PRUNE_PASSES = 8

#: 弧长比较的容差（世界单位）。
_EPS = 1e-9


@dataclass(frozen=True, slots=True)
class Piece:
    """一段切好的本体：点列 + 两端是否平头（切口端平头，股线自由端圆头）。"""

    points: list[Point]
    caps: tuple[bool, bool]


@dataclass(frozen=True, slots=True)
class WeaveResult:
    """一次交织的全部产物。"""

    #: 描边层：整根股线，不切。
    casing: list[tuple[list[Point], bool]]
    #: 本体层：切好的段。
    band: list[Piece]
    #: 补片：只填不描的凸多边形。
    patches: list[list[Point]]
    stats: dict


# --- 弧长工具 -------------------------------------------------------------


def _cumulative(points: list[Point]) -> list[float]:
    """累积弧长表（长度 = 点数）。"""
    cum = [0.0]
    for index in range(1, len(points)):
        cum.append(cum[-1] + math.hypot(
            points[index][0] - points[index - 1][0],
            points[index][1] - points[index - 1][1],
        ))
    return cum


def _at(points: list[Point], cum: list[float], arc: float) -> Point:
    """弧长处的点（线性插值）。"""
    if arc <= 0.0:
        return points[0]
    if arc >= cum[-1]:
        return points[-1]
    index = max(1, bisect_left(cum, arc))
    span = cum[index] - cum[index - 1]
    ratio = (arc - cum[index - 1]) / span if span > 1e-12 else 0.0
    start, end = points[index - 1], points[index]
    return (
        start[0] + (end[0] - start[0]) * ratio,
        start[1] + (end[1] - start[1]) * ratio,
    )


def _direction(points: list[Point], cum: list[float], arc: float) -> Point:
    """弧长处的单位方向。"""
    index = min(max(1, bisect_left(cum, arc)), len(points) - 1)
    start, end = points[index - 1], points[index]
    length = math.hypot(end[0] - start[0], end[1] - start[1])
    if length <= 1e-12:
        return (0.0, 0.0)
    return ((end[0] - start[0]) / length, (end[1] - start[1]) / length)


def _slice(points: list[Point], cum: list[float], start: float, end: float) -> list[Point]:
    """取 ``[start, end]`` 这一段；``end`` 超过总长时跨接缝绕回去（闭合股线）。"""
    total = cum[-1]
    if end > total:
        return _slice(points, cum, start, total) + _slice(points, cum, 0.0, end - total)[1:]
    out = [_at(points, cum, start)]
    low = max(1, bisect_right(cum, start))
    high = min(bisect_left(cum, end), len(points) - 1)
    for index in range(low, high):
        out.append(points[index])
    out.append(_at(points, cum, end))
    return out


def _spans(total: float, cuts: list[tuple[float, float]], closed: bool) -> list[tuple[float, float]]:
    """把切口区间合并后取补集 —— 也就是"留下哪些段"。

    闭合股线跨接缝的那一刀（``end > total``）要**折成两段**，不能直接按 ``total``
    截断：截了以后收口那一段就成了没被任何切口覆盖的"黑洞"。
    """
    raw: list[list[float]] = []
    for start, end in cuts:
        if closed and end > total:
            raw.append([max(0.0, start), total])
            raw.append([0.0, end - total])
            continue
        start, end = max(0.0, start), min(total, end)
        if end - start > 0.0:
            raw.append([start, end])

    merged: list[list[float]] = []
    for start, end in sorted(raw):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    keep: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in merged:
        if start > cursor:
            keep.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < total:
        keep.append((cursor, total))

    # 闭合股线的**首尾两段在接缝处本是同一条路径**：不合并的话它们各是一个笔划，
    # 转角处两个平头端帽之间会张开一个楔形缺口，露出下面的描边（黑扇形）。合并成
    # 一段（``end`` 超出 ``total``）由 :func:`_slice` 绕回去接上，转角就恢复成
    # 折线的圆角连接。
    if closed and len(keep) > 1 and keep[0][0] <= _EPS and keep[-1][1] >= total - _EPS:
        first_start, first_end = keep[0]
        last_start, _last_end = keep[-1]
        keep.pop()
        keep[0] = (last_start, total + (first_end - first_start))
    return keep


def _length(points: list[Point]) -> float:
    return sum(
        math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1])
        for i in range(1, len(points))
    )


# --- 补片 -----------------------------------------------------------------


def _clip_halfplane(polygon: list[Point], normal: Point, offset: float) -> list[Point]:
    """凸多边形按半平面 ``p·normal >= offset`` 裁一刀（Sutherland–Hodgman）。"""
    if not polygon:
        return []
    out: list[Point] = []
    count = len(polygon)
    for index in range(count):
        a = polygon[index]
        b = polygon[(index + 1) % count]
        da = a[0] * normal[0] + a[1] * normal[1] - offset
        db = b[0] * normal[0] + b[1] * normal[1] - offset
        if da >= 0.0:
            out.append(a)
            if db < 0.0:
                ratio = da / (da - db)
                out.append((a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio))
        elif db >= 0.0:
            ratio = da / (da - db)
            out.append((a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio))
    return out


def _tip_patch(
    here: Point,
    towards: Point,
    cut: float,
    half_width: float,
    clear: float,
    planes: list[tuple[Point, float]],
) -> tuple[list[Point] | None, bool]:
    """切口端头那块"多边形边"：把本体沿**对方带子的边缘**裁齐。

    ``here`` 交叉点，``towards`` 从交叉点指向**本体材料内部**的单位向量，``cut`` 该端
    的切口距离（沿 ``towards`` 量），``clear`` 要留出的距离（可见半宽 + 缝隙），
    ``planes`` 要躲开的半平面（世界坐标 ``(单位法线, 偏移)``，``p·法线 >= 偏移`` 是保留
    侧）。

    返回 ``(多边形 | None, 是否可以忽略)``：

    * ``narrow=True`` —— 缺口比描边还窄（交叉角接近 90°），**不需要**补。这时切口本身
      就贴着对方边缘，端头平齐，观感是对的；调用方要照切，只是不补片。
    * ``None, False`` —— 被裁没了，说明这块补片在这套几何里根本立不住，
      调用方应当干脆不切（否则就是一个露出描边的洞）。
    """
    if half_width <= 0.0 or clear <= 0.0:
        return None, True
    normal = (-towards[1], towards[0])
    far = cut + TIP_OVERLAP * half_width
    polygon = [
        (0.0, -half_width),
        (far, -half_width),
        (far, half_width),
        (0.0, half_width),
    ]
    origin_x, origin_y = here
    for (plane_x, plane_y), offset in planes:
        # 局部坐标是相对交叉点的，平面偏移要跟着搬一次原点
        local = (
            towards[0] * plane_x + towards[1] * plane_y,
            normal[0] * plane_x + normal[1] * plane_y,
        )
        polygon = _clip_halfplane(polygon, local, offset - (origin_x * plane_x + origin_y * plane_y))
        if len(polygon) < 3:
            return None, False
    if min(point[0] for point in polygon) > cut - TIP_MIN * clear:
        # 缺口本来就贴着对方边缘（接近 90°），没什么可补 —— 但**不是**补不出来
        return None, True
    return [
        (here[0] + s * towards[0] + q * normal[0], here[1] + s * towards[1] + q * normal[1])
        for s, q in polygon
    ], False


# --- 主流程 ---------------------------------------------------------------


def build_weave(
    strands: list[Strand],
    crossings: list[Crossing],
    *,
    half_width: float,
    casing: float,
    seam: float = 0.0,
) -> WeaveResult:
    """按 ``Crossing.over`` 把股线切成段、补平缺口，返回可直接渲染的三套几何。

    ``half_width`` 是本体笔划半径，``casing`` 是描边层的半径（两者取大者为**可见
    半宽** ``reach``）。``seam`` 是补片与对方边缘之间故意留的缝（世界单位）：0 = 严丝
    合缝，正数 = 一条深色细缝，"谁压谁"读得更清楚。调用方一般给
    ``seam = 缝宽比 × reach``。
    """
    radius = max(0.0, float(half_width))
    case = max(0.0, float(casing))
    reach = max(radius, case)
    clear = reach + max(0.0, float(seam))

    casing_paths = [(list(strand.points), strand.closed) for strand in strands]

    # 闭合股线的**收口边**（末点 → 首点）必须算进弧长里：``strand.points`` 里没有
    # 重复首点，收口那一段既不在累积表里、也不会被任何"保留段"覆盖 —— 结果就是每条
    # 闭合股线都少最后一小段，露出下面的描边（用户看到的黑条就是这个）。
    rings = [
        strand.points + [strand.points[0]]
        if strand.closed and len(strand.points) > 2
        else strand.points
        for strand in strands
    ]

    if reach <= 0.0 or not crossings:
        return WeaveResult(
            casing=casing_paths,
            band=[Piece(list(ring), (False, False)) for ring in rings],
            patches=[],
            stats={
                "cuts": 0,
                "pruned": 0,
                "patches": 0,
                "pieces": len(strands),
                "stubs": 0,
                "degenerate": False,
            },
        )

    arcs = [_cumulative(ring) for ring in rings]
    #: 每一刀：(股线号, 起弧长, 止弧长, 交叉点, 切口距离, sinθ)
    blades: list[tuple[int, float, float, Crossing, float, float]] = []
    for crossing in crossings:
        if not crossing.over:
            continue
        strand_index, arc = crossing.under
        sine = max(crossing.sin_angle, 1e-3)
        cosine = math.sqrt(max(0.0, 1.0 - sine * sine))
        distance = reach * (1.0 + cosine) / sine
        if distance <= 0.0:
            continue
        blades.append((strand_index, arc - distance, arc + distance, crossing, distance, sine))

    # --- 收刀：短段不给它让路，而是撤掉造出它的那一刀 ------------------------
    #
    # `gap > 0` 时股线被拆成小环、切口又密，很容易把某一段吃得只剩几像素。留着是
    # 一颗浮点、丢掉是一个洞（露出下面的描边），所以宁可撤掉一刀：带子连续穿过去，
    # 只在那处失去上下之分。撤"更斜的那一刀"——它退得最远，嫌疑最大。
    alive = [True] * len(blades)
    if reach > 0.0 and PRUNE > 0.0:
        for _ in range(PRUNE_PASSES):
            changed = False
            # 每一轮按股线分组一次：股线数 × 刀数 是平方级的，扫一遍就够
            per_strand: dict[int, list[int]] = {}
            for index, blade in enumerate(blades):
                if alive[index]:
                    per_strand.setdefault(blade[0], []).append(index)
            for strand_index, mine in per_strand.items():
                strand = strands[strand_index]
                total = arcs[strand_index][-1]
                if total <= 0.0:
                    continue
                mine = [index for index in mine if alive[index]]
                if not mine:
                    continue
                spans = _spans(
                    total,
                    [(blades[index][1], blades[index][2]) for index in mine],
                    strand.closed,
                )
                for start, end in spans:
                    if end - start >= PRUNE * reach:
                        continue
                    # 这段两侧的刀：一个在它前面收，一个在它后面起
                    before = [index for index in mine if blades[index][2] <= start + _EPS]
                    after = [index for index in mine if blades[index][1] >= end - _EPS]
                    pool = before + after
                    if not pool:
                        continue  # 段的一侧是股线自由端，没有刀可撤
                    victim = min(pool, key=lambda index: blades[index][5])
                    alive[victim] = False
                    changed = True
                    break  # 一轮一刀：撤完这一刀，下一轮重新看这一段
            if not changed:
                break

    # --- 遮盖检查：补不出补片的刀不要 --------------------------------------
    #
    # "如果没有一条带子挡在上面，就不要切口。" 落地就是：一刀切下去，两端必须各补出
    # 一块把缺口削成斜边的补片；补不出来的（退化交叉、边界上缺砖、方向退化）就别切，
    # 让带子整根穿过去 —— 只露描边的黑条比"这里不编"难看得多。
    def patches_for(blade) -> tuple[list[list[Point]], bool]:
        """这一刀两端的补片，以及"能否就这么切"。

        第二项为假 = 至少有一端**裁没了**（补片立不住），这时不能切；为真时即使一块
        补片也没有（缺口比描边还窄，端头本来就平齐对方边缘），切口照样成立。
        """
        strand_index, _start, _end, crossing, distance, _sine = blade
        over_index, over_arc = crossing.above
        here = crossing.point
        direction = _direction(rings[strand_index], arcs[strand_index], crossing.under[1])
        over_direction = _direction(rings[over_index], arcs[over_index], over_arc)
        if direction == (0.0, 0.0) or over_direction == (0.0, 0.0):
            return [], True
        found: list[list[Point]] = []
        usable = True
        for sign in (1.0, -1.0):
            towards = (direction[0] * sign, direction[1] * sign)
            # 对方边缘的法线，朝向补片这一侧
            normal = (-over_direction[1], over_direction[0])
            if towards[0] * normal[0] + towards[1] * normal[1] < 0.0:
                normal = (-normal[0], -normal[1])
            offset = here[0] * normal[0] + here[1] * normal[1] + clear
            patch, narrow = _tip_patch(here, towards, distance, radius, clear, [(normal, offset)])
            if narrow:
                continue  # 缺口太窄，不需要补
            if patch is None:
                usable = False  # 裁没了：这一刀没法收尾
                continue
            found.append(patch)
        return found, usable

    patches: list[list[Point]] = []
    if radius > 0.0:
        for index, blade in enumerate(blades):
            if not alive[index]:
                continue
            found, usable = patches_for(blade)
            if not usable:
                alive[index] = False
                continue
            patches.extend(found)

    cuts: list[list[tuple[float, float]]] = [[] for _ in strands]
    for index, blade in enumerate(blades):
        if alive[index]:
            cuts[blade[0]].append((blade[1], blade[2]))

    band: list[Piece] = []
    stubs = 0
    for index, strand in enumerate(strands):
        points = rings[index]
        cum = arcs[index]
        total = cum[-1]
        if total <= 0.0 or len(points) < 2:
            continue
        for start, end in _spans(total, cuts[index], strand.closed):
            if end - start <= _EPS:
                continue
            piece = _slice(points, cum, start, end)
            if len(piece) < 2 or _length(piece) < STUB * reach:
                stubs += 1
                continue
            # 切口端平头；股线自由端（开路股线的两头）保持圆头
            flat_start = strand.closed or start > _EPS
            flat_end = strand.closed or end < total - _EPS
            band.append(Piece(piece, (flat_start, flat_end)))

    degenerate = False
    if not band:
        # 退化：切口把整条带子吃光了（极端接触角 + 大间隙 + 粗描边时会出现，实测
        # 80° + gap 0.15 + 描边 1.7 就踩得到）。与其交出一张只剩描边的图，不如退回
        # "不切"，并在统计里标出来让它自己说明。
        band = [Piece(list(strand.points), (False, False)) for strand in strands]
        degenerate = True

    return WeaveResult(
        casing=casing_paths,
        band=band,
        patches=patches,
        stats={
            "cuts": sum(1 for flag in alive if flag),
            "pruned": sum(1 for flag in alive if not flag),
            "patches": len(patches),
            "pieces": len(band),
            "stubs": stubs,
            "degenerate": degenerate,
        },
    )
