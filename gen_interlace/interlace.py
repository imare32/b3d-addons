"""交错编织算法层：折线股线 → 交叉事件 → 一上一下 → 可见片段。

只 import numpy，不 import bpy —— 可以脱离界面单独跑。

分层跟 ``manim-lace`` 一致，只是把繁的部分都砍掉了::

    Strand(折线 + 闭合标记) → Crossing(交叉事件) → over/under(奇偶求解) → Piece(可见片段)

**一上一下的规则**（``ALTERNATE``，默认）：沿每一条股线、按它被经过的**次序**交替取
「在上 / 在下」。写成公式::

    over(某次经过) = flag[股线] XOR (这次经过的序号 % 2)

一次交叉有两次经过（a 一次、b 一次），必须一上一下，于是约束是::

    flag[b] = flag[a] XOR 1 XOR (pos_a % 2) XOR (pos_b % 2)

``flag`` 在约束图上传一遍（BFS）就定下来 —— 这不是「每个交叉随手翻一下」的近似：同一条
股线上相邻的两次经过必然一上一下，整幅图案才真的是一上一下编起来的。

自交（同一条股线自己跟自己交叉）时约束退化成
``1 XOR (pos_a % 2) XOR (pos_b % 2) == 0``，也就是那两次经过的序号必须奇偶不同；否则无解。
无解处按「a 在上」放宽并记进 ``stats['relaxed']`` —— 跟参考实现同一个策略：宁可标记，
不冒充严格交替。整块无解（奇数条约束绕成环）也一样，只是这里不报「最小见证」，只计数。

**几何**：两种切法，``tuck`` 切换。

``tuck=True``（平贴端头，默认）—— 被压住的一段退到**完全离开对方 footprint**，退出来的
缺口用一块**沿对方边缘裁齐的多边形补片**填回去（照 ``gen_zellij/weave.py`` 的做法）::

    d = (h * (1 + cos θ) + gap) / sin θ        # 切口半宽（沿本股线的弧长）
    gap = 缝宽 * h

平头的两个角到对方中心线是 ``d·sinθ ∓ h·cosθ``，取近的那个正好贴住对方边缘（多出
``gap`` 就是那条看得见的缝）。90° 时 ``d = h + gap``、缺口为 0（不用补）；角度越斜退得
越远（45° → `2.4h + gap/sin`、22.5° → `5.0h + …`），退出来的那条**斜缺口**由补片
:func:`_tip_patch` 补成一条平行于对方边缘的直线 —— 于是端头一律平头、轮廓一律贴平。

``tuck=False``（旧的零重叠方案）::

    r = h / max(|sin θ|, 浅角钳制) + air * width (+ h/2，圆头才补)

* ``h / sin θ`` —— 上方那条带子（宽 ``2h``）沿下方股线方向的投影，一半就是它。按它切，
  只要不接近平行，切口就刚好被上方股线盖住；
* ``圆头`` 会往外多画 ``h/2``（实测：半径 0.05 的圆头比几何长度长 0.0982），所以圆头要补。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

__all__ = [
    "ALTERNATE",
    "CROSSING",
    "RULES",
    "STRAND",
    "Crossing",
    "Piece",
    "Strand",
    "arc_tables",
    "cut_pieces",
    "find_crossings",
    "solve_weave",
]

#: 一上一下的三种规则。
ALTERNATE = "ALTERNATE"  # 沿股线交替（真正的编织）
STRAND = "STRAND"  # 偶数号股线恒在上（分层，不是编织）
CROSSING = "CROSSING"  # 按交叉序号交替（会自相矛盾，但有的图好看）
RULES = (ALTERNATE, STRAND, CROSSING)

_EPS = 1e-9

#: 补片越过自己那刀的重叠量（半宽的倍数）：只为了让补片和笔划端头叠住、不留接缝。
#: 取太大（一整半宽）会伸到隔壁股线的描边里，取 0.25 就够叠住。
_TIP_OVERLAP = 0.25

#: 缺口短于 ``_TIP_MIN × 可见半宽`` 就不补 —— 90° 上切口本来就贴着对方边缘，补了只是重叠。
_TIP_MIN = 0.5

#: 残段短于 ``_PRUNE × 半宽`` 就把造出它的那一刀收回去：宁可不编，也不留一颗浮着的珠子。
_PRUNE = 2.0

#: 平贴模式的 ``1/sin`` 保底（只为不除零）：这里**不能**用「浅角钳制」那种截断，截断会让
#: ``d`` 短于需要的退让距离，切口一角照样戳进对方 footprint。
_TUCK_SIN_FLOOR = 1e-3


@dataclass(frozen=True)
class Strand:
    """一条股线：有序折线 + 是否闭合（闭合 = 最后一点回到第一点）。"""

    index: int
    points: np.ndarray  # (n, 3) float64，世界坐标；交叉只在 XY 上算
    closed: bool = False


@dataclass
class Crossing:
    """一次交叉事件：哪两条股线、在各自的哪一段、哪一点、夹角多少。"""

    path_a: int
    path_b: int
    seg_a: int
    seg_b: int
    t_a: float
    t_b: float
    point: np.ndarray  # (2,) float64
    sin_angle: float
    over: bool = True  # True = a 在上（solve_weave 填）

    def under_side(self) -> tuple[int, int, float]:
        """被压住的那一侧：``(股线号, 段号, 段内参数)``。"""
        if self.over:
            return self.path_b, self.seg_b, self.t_b
        return self.path_a, self.seg_a, self.t_a


@dataclass(frozen=True)
class Piece:
    """可见的一截笔划：属于哪条股线、是这条股线上的第几截、点列、是否闭合。

    ``fill=True`` 的是**补片**（平贴端头退出来的那块缺口）：它不描边、只填充，颜色跟
    笔划一样，所以看起来就是同一根股线被削平了一个斜角。
    """

    strand: int
    piece: int
    points: np.ndarray  # (m, 3)
    cyclic: bool = False
    fill: bool = False


# --- 线段表 ---------------------------------------------------------------


def _segments(strands):
    """把所有股线摊平成一份线段表。

    返回 ``(股线号, 段号, 起点, 终点, 方向, 长度, 每条的段数)``；零长段直接丢掉
    （它既没有方向、也不贡献弧长，留着只会在求交时除零）。
    """
    owner, ids, starts, ends, per_strand = [], [], [], [], []
    for strand in strands:
        points = np.asarray(strand.points, dtype=np.float64)[:, :2]
        count = 0
        if len(points) >= 2:
            if strand.closed:
                head, tail = points, np.roll(points, -1, axis=0)
            else:
                head, tail = points[:-1], points[1:]
            span = np.linalg.norm(tail - head, axis=1)
            keep = span > 1e-12
            owner.append(np.full(int(keep.sum()), strand.index, dtype=np.int64))
            ids.append(np.nonzero(keep)[0].astype(np.int64))
            starts.append(head[keep])
            ends.append(tail[keep])
            count = int(keep.sum())
        per_strand.append(count)

    if not owner:
        empty = np.zeros(0, dtype=np.float64)
        return (
            np.zeros(0, dtype=np.int64),
            np.zeros(0, dtype=np.int64),
            empty.reshape(0, 2),
            empty.reshape(0, 2),
            empty.reshape(0, 2),
            empty,
            per_strand,
        )

    owner = np.concatenate(owner)
    ids = np.concatenate(ids)
    starts = np.vstack(starts)
    ends = np.vstack(ends)
    deltas = ends - starts
    lengths = np.linalg.norm(deltas, axis=1)
    return owner, ids, starts, ends, deltas, lengths, per_strand


def _candidate_pairs(xmin, xmax, ymin, ymax, total):
    """宽相：按 x 排序找出「可能的交叉对」，全程向量化。

    思路（跟参考实现同一条路子，但一次算完）：把线段按 ``xmin`` 排序后，第 i 条只可能
    跟它右边 ``xmin <= xmax[i]`` 的那些相交 —— 一个 ``searchsorted`` 就能算出每条的
    候选上限，再用 ``repeat`` 摊平成两张索引表。**不要**写成 Python 循环逐条扫：实测
    1500 段时循环版光循环本身就要 15 ms，向量化之后整段（含精确求交）不到 1 ms。

    最坏情况仍然是 O(n²)（比如一把几乎竖直的长线段），所以上限放在调用方
    （``build.SEGMENT_LIMIT``）。
    """
    order = np.argsort(xmin, kind="stable")
    sxmin = xmin[order]
    limits = np.searchsorted(sxmin, xmax[order], side="right")
    counts = np.maximum(limits - np.arange(total) - 1, 0)
    if not counts.any():
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)

    ends = np.cumsum(counts)
    first = np.repeat(np.arange(total), counts)
    offsets = np.arange(int(ends[-1])) - np.repeat(ends - counts, counts)
    second = first + 1 + offsets
    left = order[first]
    right = order[second]

    near = (ymax[left] >= ymin[right]) & (ymin[left] <= ymax[right])
    return left[near], right[near]


# --- 交叉检测 -------------------------------------------------------------


def find_crossings(strands) -> list[Crossing]:
    """两条股线之间所有交叉（含同一条股线自己的自交）。

    宽相是「按 x 排序 + 二分找右界」的扫描式剪枝，再按 y 区间过一遍，最后才做精确求交
    （三段都向量化，见 :func:`_candidate_pairs`）。
    """
    owner, ids, starts, ends, deltas, lengths, per_strand = _segments(strands)
    total = len(owner)
    if total < 2:
        return []

    left, right = _candidate_pairs(
        np.minimum(starts[:, 0], ends[:, 0]),
        np.maximum(starts[:, 0], ends[:, 0]),
        np.minimum(starts[:, 1], ends[:, 1]),
        np.maximum(starts[:, 1], ends[:, 1]),
        total,
    )
    if not len(left):
        return []

    # 同一条股线上相邻的两段共用端点：那不是交叉，剔掉
    per = np.asarray(per_strand, dtype=np.int64)
    same = owner[left] == owner[right]
    low = np.minimum(ids[left], ids[right])
    high = np.maximum(ids[left], ids[right])
    adjacent = same & (
        (high - low == 1) | ((low == 0) & (high == per[owner[left]] - 1))
    )
    keep = ~adjacent
    left, right = left[keep], right[keep]
    if not len(left):
        return []

    # 精确求交（全量向量化）：参数 = cross(起点差, 对方方向) / cross(本方向, 对方方向)
    d1, d2 = deltas[left], deltas[right]
    cross = d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]
    usable = np.abs(cross) > 1e-12
    safe = np.where(usable, cross, 1.0)
    diff = starts[right] - starts[left]
    ua = (diff[:, 0] * d2[:, 1] - diff[:, 1] * d2[:, 0]) / safe
    ub = (diff[:, 0] * d1[:, 1] - diff[:, 1] * d1[:, 0]) / safe
    hit = (
        usable & (ua >= -_EPS) & (ua <= 1.0 + _EPS) & (ub >= -_EPS) & (ub <= 1.0 + _EPS)
    )
    if not hit.any():
        return []

    found: dict[tuple, Crossing] = {}
    for index in np.nonzero(hit)[0]:
        first, second = int(left[index]), int(right[index])
        first_owner, second_owner = int(owner[first]), int(owner[second])
        sin_angle = min(
            abs(float(cross[index])) / (lengths[first] * lengths[second]), 1.0
        )
        point = starts[first] + deltas[first] * float(ua[index])
        if first_owner <= second_owner:
            a, b = first_owner, second_owner
            seg_a, t_a = int(ids[first]), float(ua[index])
            seg_b, t_b = int(ids[second]), float(ub[index])
        else:
            a, b = second_owner, first_owner
            seg_a, t_a = int(ids[second]), float(ub[index])
            seg_b, t_b = int(ids[first]), float(ua[index])
        key = (a, b, round(float(point[0]), 6), round(float(point[1]), 6))
        if key in found:
            continue  # 同一个点上重复报的交叉
        found[key] = Crossing(
            path_a=a,
            path_b=b,
            seg_a=seg_a,
            seg_b=seg_b,
            t_a=t_a,
            t_b=t_b,
            point=point.astype(np.float64),
            sin_angle=sin_angle,
        )
    return list(found.values())


# --- 一上一下 -------------------------------------------------------------


def _pass_positions(strands, crossings) -> dict[tuple[int, int], int]:
    """每次经过在自己那条股线上的次序（0 起）：``(交叉号, 0=a/1=b) -> 序号``。"""
    entries = [[] for _ in strands]
    for number, crossing in enumerate(crossings):
        entries[crossing.path_a].append((crossing.seg_a, crossing.t_a, number, 0))
        entries[crossing.path_b].append((crossing.seg_b, crossing.t_b, number, 1))

    positions = {}
    for items in entries:
        if not items:
            continue
        items.sort(key=lambda item: (item[0], item[1]))
        for order, (_, _, number, side) in enumerate(items):
            positions[(number, side)] = order
    return positions


def solve_weave(strands, crossings, rule: str = ALTERNATE) -> dict:
    """定下每个交叉谁在上（写回 ``Crossing.over``），返回统计。"""
    stats = {"crossings": len(crossings), "relaxed": 0}
    if not crossings:
        return stats

    if rule == STRAND:
        for crossing in crossings:
            crossing.over = crossing.path_a % 2 == 0
        return stats
    if rule == CROSSING:
        for number, crossing in enumerate(crossings):
            crossing.over = number % 2 == 0
        return stats

    positions = _pass_positions(strands, crossings)

    # 约束：flag[b] = flag[a] XOR parity；自交时 a == b，parity 必须为 0 才有解
    adjacency = [[] for _ in strands]
    for number, crossing in enumerate(crossings):
        parity = 1 ^ (positions[(number, 0)] % 2) ^ (positions[(number, 1)] % 2)
        adjacency[crossing.path_a].append((crossing.path_b, parity))
        adjacency[crossing.path_b].append((crossing.path_a, parity))

    flags: dict[int, int] = {}
    for start in range(len(strands)):
        if start in flags:
            continue
        flags[start] = 1
        stack = [start]
        while stack:
            path = stack.pop()
            for other, parity in adjacency[path]:
                wanted = flags[path] ^ parity
                if other not in flags:
                    flags[other] = wanted
                    stack.append(other)

    for number, crossing in enumerate(crossings):
        over_a = bool(flags.get(crossing.path_a, 1) ^ (positions[(number, 0)] % 2))
        over_b = bool(flags.get(crossing.path_b, 1) ^ (positions[(number, 1)] % 2))
        if over_a == over_b:
            over_a = True  # 无解：放宽成 a 在上（跟参考实现同一个默认）
            stats["relaxed"] += 1
        crossing.over = over_a
    return stats


# --- 切段（弧长） ---------------------------------------------------------


def arc_tables(strand: Strand):
    """``(三维点序列, 累积弧长)``；闭合股线把首点补在末尾，好让最后一整段有长度。"""
    points = np.asarray(strand.points, dtype=np.float64)
    if strand.closed and len(points) > 1:
        loop = np.vstack([points, points[:1]])
    else:
        loop = points
    steps = np.linalg.norm(np.diff(loop[:, :2], axis=0), axis=1)
    cumulative = np.concatenate([[0.0], np.cumsum(steps)])
    return loop, cumulative


def _point_at(loop, cumulative, distance):
    """弧长 ``distance`` 处的点（在折线上线性插值）。"""
    if distance <= 0.0:
        return loop[0].copy()
    if distance >= cumulative[-1]:
        return loop[-1].copy()
    index = int(np.searchsorted(cumulative, distance, side="right") - 1)
    index = min(max(index, 0), len(loop) - 2)
    span = cumulative[index + 1] - cumulative[index]
    ratio = 0.0 if span <= 1e-12 else (distance - cumulative[index]) / span
    return loop[index] + (loop[index + 1] - loop[index]) * ratio


def _slice(loop, cumulative, start: float, end: float) -> np.ndarray:
    """取弧长 ``[start, end]`` 那一段，端点插值补上。"""
    inner = (cumulative > start + _EPS) & (cumulative < end - _EPS)
    pieces = [loop[inner]]
    pieces.insert(0, _point_at(loop, cumulative, start).reshape(1, -1))
    pieces.append(_point_at(loop, cumulative, end).reshape(1, -1))
    return np.vstack(pieces)


def _spans(start: float, end: float, total: float, closed: bool) -> list[tuple]:
    """把一段切口折到 ``[0, total]`` 上；跨零点（闭合股线）就拆成两段。"""
    length = end - start
    if total <= 1e-12:
        return []
    if length >= total:
        return [(0.0, total)]
    if not closed:
        low = max(start, 0.0)
        high = min(end, total)
        return [(low, high)] if high - low > 1e-12 else []
    start = start % total
    end = start + length
    if end <= total:
        return [(start, end)]
    return [(start, total), (0.0, end - total)]


def _merge(spans: list[tuple]) -> list[tuple]:
    """合并重叠的切口区间。"""
    if not spans:
        return []
    spans.sort()
    merged = [list(spans[0])]
    for start, end in spans[1:]:
        if start <= merged[-1][1] + _EPS:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _visible_spans(cuts: list[tuple], total: float) -> list[tuple]:
    """切口的补集：还能看见的那些弧长区间。"""
    spans, cursor = [], 0.0
    for start, end in _merge(cuts):
        if start > cursor + _EPS:
            spans.append((cursor, min(start, total)))
        cursor = max(cursor, end)
    if cursor < total - _EPS:
        spans.append((cursor, total))
    return spans


def _clip_halfplane(polygon, normal, offset):
    """凸多边形按半平面 ``p·normal >= offset`` 裁一刀（Sutherland–Hodgman）。"""
    if not polygon:
        return []
    out = []
    count = len(polygon)
    for index in range(count):
        ax, ay = polygon[index]
        bx, by = polygon[(index + 1) % count]
        da = ax * normal[0] + ay * normal[1] - offset
        db = bx * normal[0] + by * normal[1] - offset
        if da >= 0.0:
            out.append((ax, ay))
            if db < 0.0:
                ratio = da / (da - db)
                out.append((ax + (bx - ax) * ratio, ay + (by - ay) * ratio))
        elif db >= 0.0:
            ratio = da / (da - db)
            out.append((ax + (bx - ax) * ratio, ay + (by - ay) * ratio))
    return out


def _tip_patch(here, towards, cut, half_width, clear, planes, z: float = 0.0):
    """切口端头那块「削平」补片：把股线沿**对方股线的边缘**裁齐。

    笔划的端头只能垂直于自己，而对方边缘是斜的 —— 斜交叉上两者必然错开：要么退到远处留
    一个大缺口，要么压进去把对方咬掉一块。这块多边形把缺口补成一条斜边，于是「贴平」与
    「不压到对方」同时成立（等同多边形方案里的那一刀）。

    ``here`` 交叉点，``towards`` 从交叉点指向**保留材料**的单位向量，``cut`` 该端的切口
    距离（沿 ``towards`` 量），``clear`` 要躲开的距离（对方半宽 + 缝宽），``planes`` 要躲开
    的半平面（``(单位法线, 偏移)``，``p·法线 >= 偏移`` 是保留侧）。返回 ``(m, 3)`` 的凸多边形
    （z 取股线的高度）；缺口小到可以忽略时返回 ``None``。
    """
    if half_width <= 0.0 or clear <= 0.0:
        return None
    nx, ny = -towards[1], towards[0]
    far = cut + _TIP_OVERLAP * half_width
    polygon = [
        (0.0, -half_width),
        (far, -half_width),
        (far, half_width),
        (0.0, half_width),
    ]
    origin_x, origin_y = float(here[0]), float(here[1])
    for (plane_x, plane_y), offset in planes:
        local = (
            towards[0] * plane_x + towards[1] * plane_y,
            nx * plane_x + ny * plane_y,
        )
        polygon = _clip_halfplane(
            polygon, local, offset - (origin_x * plane_x + origin_y * plane_y)
        )
        if len(polygon) < 3:
            return None
    if min(point[0] for point in polygon) > cut - _TIP_MIN * clear:
        return None  # 切口本来就贴着对方边缘（≈90°），没什么可补
    return np.array(
        [
            (origin_x + s * towards[0] + q * nx, origin_y + s * towards[1] + q * ny, z)
            for s, q in polygon
        ],
        dtype=np.float64,
    )


def _axis_at(strand, seg: int):
    """股线第 ``seg`` 段的单位方向；退化段返回 ``None``。"""
    points = np.asarray(strand.points, dtype=np.float64)
    count = len(points)
    if count < 2 or seg < 0 or seg >= count:
        return None
    following = (seg + 1) % count if strand.closed else seg + 1
    if following >= count:
        return None
    delta = points[following, :2] - points[seg, :2]
    length = float(np.linalg.norm(delta))
    if length < 1e-12:
        return None
    return delta / length


def _prune_spans(spans, threshold: float, closed: bool):
    """把短于 ``threshold`` 的内部片段并进隔壁一段（等于把造出它的那一刀收回去）。

    平贴模式的切口比旧方案长得多，一条股线上两处交叉挨得近时会切出一颗「浮着的珠子」。
    GP 笔划又自带圆头，那颗珠子比「这里不编」难看得多 —— 所以宁可收回一刀（该交叉点两根
    股线直接叠在一起），也不留珠子。
    """
    if threshold <= 0.0 or len(spans) < 2:
        return list(spans), 0
    kept = list(spans)
    pruned = 0
    for _ in range(8):
        found = -1
        for index, (start, end) in enumerate(kept):
            if end - start >= threshold:
                continue
            if closed:
                if len(kept) < 3:
                    break
            elif index == 0 or index + 1 >= len(kept):
                continue
            found = index
            break
        if found < 0:
            break
        if found + 1 < len(kept):
            kept[found + 1] = (kept[found][0], kept[found + 1][1])
        else:
            # 闭合股线的最后一段：并回前一段（跨接缝的两个区间没法合成一个）
            kept[found - 1] = (kept[found - 1][0], kept[found][1])
        kept.pop(found)
        pruned += 1
    return kept, pruned


def cut_pieces(
    strands,
    crossings,
    width: float,
    min_sin: float = 0.2,
    air: float = 0.35,
    round_caps: bool = False,
    tuck: bool = True,
    seam: float = 0.25,
) -> tuple[list[Piece], dict]:
    """按一上一下把被压住的弧段切掉，返回可见片段（含补片）与统计。

    ``tuck=True``（默认）走平贴端头，切口半宽::

        r = (h * (1 + cos θ) + gap) / sin θ,   gap = seam * h

    退出来的缺口由 :func:`_tip_patch` 补平（圆头没法补，所以 ``round_caps`` 与它互斥）。

    否则是旧的零重叠方案::

        r = h / max(sin θ, 浅角钳制) + air * width (+ h/2，圆头才补)

    ``h = width / 2``。
    """
    halfwidth = float(width) * 0.5
    use_tuck = bool(tuck) and not round_caps
    gap = max(0.0, float(seam)) * halfwidth
    extra = float(air) * float(width) + (halfwidth * 0.5 if round_caps else 0.0)
    tables = [arc_tables(strand) for strand in strands]
    cuts: list[list[tuple]] = [[] for _ in strands]
    patches: list[Piece] = []

    for crossing in crossings:
        path, seg, t = crossing.under_side()
        loop, cumulative = tables[path]
        if seg + 1 >= len(cumulative):
            continue
        start = cumulative[seg]
        span = cumulative[seg + 1] - start
        arc = start + span * t
        total = cumulative[-1]

        sine = abs(crossing.sin_angle)
        if use_tuck:
            cosine = math.sqrt(max(0.0, 1.0 - sine * sine))
            radius = (halfwidth * (1.0 + cosine) + gap) / max(sine, _TUCK_SIN_FLOOR)
        else:
            radius = halfwidth / max(sine, min_sin, 1e-3) + extra

        for low, high in _spans(
            arc - radius, arc + radius, total, strands[path].closed
        ):
            cuts[path].append((low, high))

        if not use_tuck:
            continue
        # 缺口补片：沿对方股线的**边缘**把它裁平，两端各一块
        under_axis = _axis_at(strands[path], seg)
        over_path = crossing.path_a if crossing.over else crossing.path_b
        over_seg = crossing.seg_a if crossing.over else crossing.seg_b
        over_axis = _axis_at(strands[over_path], over_seg)
        if under_axis is None or over_axis is None:
            continue
        points = np.asarray(strands[path].points, dtype=np.float64)
        z = float(points[seg][2] + (points[seg + 1][2] - points[seg][2]) * t)
        clear = halfwidth + gap
        for sign in (1.0, -1.0):
            towards = (under_axis[0] * sign, under_axis[1] * sign)
            side = (-over_axis[1], over_axis[0])
            if towards[0] * side[0] + towards[1] * side[1] < 0.0:
                side = (-side[0], -side[1])  # 法线朝补片那一侧
            patch = _tip_patch(
                crossing.point,
                towards,
                radius,
                halfwidth,
                clear,
                [
                    (
                        side,
                        crossing.point[0] * side[0]
                        + crossing.point[1] * side[1]
                        + clear,
                    )
                ],
                z,
            )
            if patch is not None:
                patches.append(Piece(path, -1, patch, cyclic=True, fill=True))

    pieces: list[Piece] = []
    used = 0
    pruned = 0
    threshold = _PRUNE * halfwidth if use_tuck else 0.0
    for strand in strands:
        loop, cumulative = tables[strand.index]
        total = cumulative[-1]
        if total <= 1e-12:
            continue
        spans = _visible_spans(cuts[strand.index], total)
        if not spans:
            continue
        if threshold > 0.0:
            spans, gone = _prune_spans(spans, threshold, strand.closed)
            pruned += gone
        used += len(spans)

        if not cuts[strand.index]:
            # 一次都没被压：整条原样留着（闭合的还能保持闭合）
            points = loop[:-1] if strand.closed else loop
            if len(points) >= 2:
                pieces.append(
                    Piece(strand.index, 0, points.copy(), cyclic=strand.closed)
                )
            continue

        # 闭合股线的首尾两段在接缝处连起来，免得白白多一个断头
        if (
            strand.closed
            and len(spans) > 1
            and abs(spans[0][0]) < _EPS
            and abs(spans[-1][1] - total) < _EPS
        ):
            tail = _slice(loop, cumulative, spans[-1][0], total)
            head = _slice(loop, cumulative, 0.0, spans[0][1])
            joined = np.vstack([tail, head[1:]])
            pieces.append(Piece(strand.index, 0, joined, cyclic=False))
            rest = spans[1:-1]
            offset = 1
        else:
            rest = spans
            offset = 0

        for order, (start, end) in enumerate(rest):
            points = _slice(loop, cumulative, start, end)
            if len(points) < 2:
                continue
            pieces.append(Piece(strand.index, order + offset, points, cyclic=False))

    pieces.extend(patches)
    stats = {
        "pieces": len(pieces) - len(patches),
        "patches": len(patches),
        "pruned": pruned,
        "tuck": use_tuck,
        "spans": used,
        "points": int(sum(len(piece.points) for piece in pieces)),
    }
    return pieces, stats


def length_bounds(pieces) -> tuple[float, float]:
    """片段长度的最小 / 最大值（面板上给个参考读数）。"""
    if not pieces:
        return 0.0, 0.0
    lengths = [
        float(np.linalg.norm(np.diff(piece.points[:, :2], axis=0), axis=1).sum())
        for piece in pieces
        if len(piece.points) > 1
    ]
    if not lengths:
        return 0.0, 0.0
    return min(lengths), max(lengths)
