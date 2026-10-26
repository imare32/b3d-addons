"""编织：把 motif 边网变成上下交替的带子。

``Outlines`` 层原来给每个色块的轮廓各画一条闭合笔划；编织要的是另一件事——把**整张图的
边网**看成若干根带子，在结点处决定谁压谁，再把"压在下面"的那一端切掉一个缺口。

判据与几何无关：over/under 是挂在"顶点 + 边端"上的组合状态。只要一个顶点能配对出两条
以上"直通"的路径，那里就是交叉点。做法照抄 Alhambra 的 ``interlace`` 图层：顶点出边按
角度排序 → 绕一圈交替 → 沿带子走到下一处交叉时取反。

三条硬约束（都直接决定观感）：

* **共享边只能是一条带子**：相邻色块共用一条缝，不去重就会有两条带子争同一段，
  over/under 立刻自相矛盾（去重键与 ``scene._edge_key`` 一致：端点量化后无序比较）；
* **带子 ≠ 多边形**：带子是"穿过结点时转角最小的路径"，闭合色块轮廓只是它的边料；
  顶点配对时按"最接近反向"贪心，转角超过 90° 的不配（配不上就是带子末端）；
* **不靠绘制顺序**：over/under 关系里存在环（A 压 B、B 压 C、C 压 A），任何"分两层、
  一层压另一层"都排不出全局序，所以必须把下面的带子真的切掉。

切多少由带宽决定（:func:`_end_cut`），所以**带宽是几何参数**，改它必须重新生成。
``WeaveStyle.tuck``（默认开）让压下的那一端**钻到对方身下**（本体同色叠画看不见、
描边层永远在所有本体下面），于是切口可以取"正好贴平对方边缘"的那个解，斜交叉也能用
平头——代价是本体会咬掉对方描边环上一小块。关掉则回到零重叠方案：切口远在对方身影
之外，斜交叉只能用圆头。

纯 Python，不 import bpy；结果与随机流无关（同 seed 必然同结果）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

__all__ = ["Point", "Weave", "WeaveStyle", "build_weave"]

Point = tuple[float, float]

#: 端点聚类的默认容差 = 图形包围盒跨度 × 该比例（画布 10 时约 0.0017，是色块最短边
#: 0.06 的 3%）。实测同一段缝的两份坐标差在 1e-6~1e-3，量化去重会漏掉一半。
SNAP_RATIO = 2e-4

#: 交叉角正弦的下限：8 方向网格里最小是 45°（0.707），留一点余量给退化情况，
#: 免得 ``1/sin`` 把缺口放大到吃掉整条带子。
SIN_FLOOR = 0.5

#: 短于 ``STUB * 半径`` 的残段直接丢掉：它只会画成一个圆点。
STUB = 0.5

#: 短于 ``PRUNE * 半径`` 的残段会被"修剪"掉：连着的那个缺口收回，两根带子叠在一起。
#: 一颗浮着的小珠子比"这里不编"难看得多，而且同色不透明时叠画看不出来。
PRUNE = 2.0

#: 交叉角正弦高于此值（≈ 64°..116°）才用平头。实测交叉角只有 45° 和 90° 两种，
#: 90° 时平头的切面正好压在对方带子的边缘线上，45° 时平头会甩出一个尖角缺口，不如圆头。
#: 只在 ``WeaveStyle.tuck=False``（不许压进对方身下）时用到。
FLAT_CAP_SIN = 0.9

#: ``tuck=True`` 时交叉角正弦的下限：低于它就不再"钻到对方身下"（``tan(θ/2)`` 随角度
#: 变小趋于 0，钻进去的那截会越拉越长，咬掉对方描边环的口子也跟着变长），退回零重叠
#: 的圆头。实测的两个角度（45° = 0.707、90° = 1）都在门内。
TUCK_SIN = 0.5

#: 修剪的迭代次数：每轮收回一刀，收敛极快，留几轮兜底。
_PRUNE_PASSES = 8


@dataclass(frozen=True, slots=True)
class WeaveStyle:
    """编织的外观参数（都是世界单位 / 无量纲比率）。"""

    #: 带子半宽（= 笔划半径，总宽 = 2×）；画布 10 下 0.010 左右是"勾缝"，0.03 以上带子本身就是主图。
    half_width: float = 0.010
    #: 描边（casing）半径；画在本体下面、比本体宽，露出的一圈就是"蕾丝边"。
    #: 它不改变几何，只影响缺口要让开多少——所以缺口是按 ``max(half_width, casing)`` 算的。
    casing: float = 0.0125
    #: 缺口在"刚好贴住上方带子"之外再多留的量，单位是**可见半宽**的倍数。
    #: ``tuck=True`` 时语义反过来：再往里**多钻**这么多，让贴平更确定。
    gap_ratio: float = 0.0
    #: 端点聚类容差（世界单位）；0 = 按包围盒自动取 ``SNAP_RATIO``。
    snap: float = 0.0
    #: 允许压下的那一端钻到对方身下（切口取"贴平对方边缘"的解，斜交叉也用平头）。
    #: 关掉是旧的零重叠方案：只有接近垂直的交叉能贴平，其余用圆头（见 ``_end_cut``）。
    tuck: bool = True


@dataclass(slots=True)
class Weave:
    """一次编织的结果：可以直接变成 ``scene.Shape`` 的点列，外加统计。"""

    #: ``(点列, 是否闭合)``；闭合只出现在整根带子一处交叉都没有的时候。
    paths: list[tuple[list[Point], bool]] = field(default_factory=list)
    #: 与 ``paths`` 一一对应：``(起点是否平头, 终点是否平头)``。``tuck`` 打开时平头用在
    #: 所有交叉上（切口整个藏在对方身下）；关掉时只用在接近 90° 的交叉上，斜交叉用圆头。
    caps: list[tuple[bool, bool]] = field(default_factory=list)
    nodes: int = 0
    edges: int = 0
    strands: int = 0
    crossings: int = 0
    cuts: int = 0
    #: 被收回的缺口数（残段太短，宁可不编，见 ``PRUNE``）。
    pruned: int = 0
    #: 绘制次序用的"下→上"关系：``(交叉点, 段序号, 是否压在下面)``。同一个交叉点上
    #: 必然同时有压上的一段和压下的若干段；按"下先画"拓扑排序就能让圆头被盖住。
    links: list[tuple[int, int, bool]] = field(default_factory=list)
    #: "没能沿带子交替"的通过段数。奇数环（三角形色块）上必然出现，**不是错误**：
    #: 每个交叉点仍然严格一上一下（见 ``build_weave`` 里的说明）。
    mismatches: int = 0


# --- 小工具 ---------------------------------------------------------------


def _sub(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1])


def _norm(v: Point) -> Point:
    size = math.hypot(v[0], v[1])
    if size < 1e-12:
        return (0.0, 0.0)
    return (v[0] / size, v[1] / size)


def _sin_between(u: Point, w: Point) -> float:
    """两条**线**的夹角正弦（0 = 平行，1 = 垂直）。"""
    return abs(u[0] * w[1] - u[1] * w[0])


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


# --- 图 --------------------------------------------------------------------


def _cells(a: Point, b: Point, cell: float, margin: float = 0.0):
    """线段包围盒覆盖的栅格单元（``margin`` 给容差留一圈）。"""
    lo_x, hi_x = (a[0], b[0]) if a[0] <= b[0] else (b[0], a[0])
    lo_y, hi_y = (a[1], b[1]) if a[1] <= b[1] else (b[1], a[1])
    for cx in range(int((lo_x - margin) // cell), int((hi_x + margin) // cell) + 1):
        for cy in range(int((lo_y - margin) // cell), int((hi_y + margin) // cell) + 1):
            yield (cx, cy)


def _nodes_on_edge(points, node_grid, cell, snap, a, b):
    """落在边 ``(a, b)`` 内部的结点：``[(t, node), ...]``，按 ``t`` 升序。

    这一条同时吃掉三类毛病：近似重合的两份端点、共线部分重叠的长短边、T 型结点。
    """
    ax, ay = points[a]
    bx, by = points[b]
    ex, ey = bx - ax, by - ay
    length2 = ex * ex + ey * ey
    if length2 <= 1e-18:
        return []
    hits = []
    for key in _cells(points[a], points[b], cell, snap):
        for index in node_grid.get(key, ()):
            if index == a or index == b:
                continue
            dx = points[index][0] - ax
            dy = points[index][1] - ay
            t = (dx * ex + dy * ey) / length2
            if t <= 0.0 or t >= 1.0:
                continue
            if (dx - ex * t) ** 2 + (dy - ey * t) ** 2 > snap * snap:
                continue
            hits.append((t, index))
    hits.sort()
    return hits


def _crossing(points, first, second, snap):
    """两段**内部**相交：返回 ``(t, u, point)``，平行/不相交返回 ``None``。"""
    (ax, ay), (bx, by) = points[first[0]], points[first[1]]
    (cx, cy), (dx, dy) = points[second[0]], points[second[1]]
    rx, ry = bx - ax, by - ay
    sx, sy = dx - cx, dy - cy
    denominator = rx * sy - ry * sx
    length = math.hypot(rx, ry) * math.hypot(sx, sy)
    if length <= 1e-18 or abs(denominator) <= 1e-9 * length:
        return None  # 平行（含共线，共线交给"落在边内部的结点"处理）
    t = ((cx - ax) * sy - (cy - ay) * sx) / denominator
    u = ((cx - ax) * ry - (cy - ay) * rx) / denominator
    # 端点附近交给结点聚类，不在这里插点
    slack = min(0.25, max(snap / max(math.hypot(rx, ry), 1e-12), 1e-6))
    if not (slack < t < 1.0 - slack and slack < u < 1.0 - slack):
        return None
    return t, u, (ax + rx * t, ay + ry * t)


def _split_edge(points, a, b, breaks):
    """把边 ``(a, b)`` 在内部断点处打断，返回若干条规范化的边。"""
    if not breaks:
        return [(a, b) if a < b else (b, a)]
    ax, ay = points[a]
    bx, by = points[b]
    ex, ey = bx - ax, by - ay
    length2 = ex * ex + ey * ey
    if length2 <= 1e-18:
        return []
    ordered = []
    seen = set()
    for _t, index in sorted(breaks):
        if index == a or index == b or index in seen:
            continue
        if (points[index][0] - ax) * ex + (points[index][1] - ay) * ey <= 0.0:
            continue
        if (points[index][0] - bx) * -ex + (points[index][1] - by) * -ey <= 0.0:
            continue
        seen.add(index)
        ordered.append(index)
    out = []
    previous = a
    for index in ordered:
        out.append((previous, index) if previous < index else (index, previous))
        previous = index
    if previous != b:
        out.append((previous, b) if previous < b else (b, previous))
    return out


def _edge_network(polygons, snap: float = 0.0):
    """色块多边形 → **平面化**后的无向边网。

    量化去重不够用。相邻色块的共用缝是由各自的仿射变换摆出来的，同一段缝的两份坐标
    只差 ``1e-6``~``1e-3``；4 位量化有一半正好落在桶边界上，于是同一段缝留下两条边
    （实测 density 10：7843 条边里有 3971 条是这种"两份"）。除此之外还有共线部分重叠
    （797 处）、T 型结点（2252 处），以及两边内部真交叉但没有结点（593 处）。

    所以这里做三件事，顺序不能换：

    1. **端点容差聚类**：``snap`` 半径内归成同一个结点（栅格 + 邻域查询，不做跨桶量化）；
    2. **平面化**：每条边在"落在它内部的结点"处打断——重合、部分重叠、T 型一次消掉；
    3. **交点入库**：两条非共线边内部相交时插入结点并同时打断两边，否则那里根本
       没有结点，也就无从编织。
    """
    flat = [(x, y) for polygon in polygons for x, y in polygon]
    if not flat:
        return [], []
    xs = [point[0] for point in flat]
    ys = [point[1] for point in flat]
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    if snap <= 0.0:
        snap = max(span * SNAP_RATIO, 1e-9)
    cell = max(snap * 8.0, span / 128.0)
    snap2 = snap * snap

    points: list[Point] = []
    grid: dict[tuple[int, int], list[int]] = {}

    def node_of(x, y) -> int:
        cx, cy = int(x // cell), int(y // cell)
        best, best_d = -1, snap2
        for ax in (cx - 1, cx, cx + 1):
            for ay in (cy - 1, cy, cy + 1):
                for index in grid.get((ax, ay), ()):
                    dx = points[index][0] - x
                    dy = points[index][1] - y
                    distance = dx * dx + dy * dy
                    if distance <= best_d:
                        best, best_d = index, distance
        if best < 0:
            best = len(points)
            points.append((x, y))
            grid.setdefault((cx, cy), []).append(best)
        return best

    seen: set[tuple[int, int]] = set()
    raw: list[tuple[int, int]] = []
    for polygon in polygons:
        count = len(polygon)
        if count < 3:
            continue
        ids = [node_of(x, y) for x, y in polygon]
        for i in range(count):
            a = ids[i]
            b = ids[(i + 1) % count]
            if a == b:
                continue
            key = (a, b) if a < b else (b, a)
            if key not in seen:
                seen.add(key)
                raw.append(key)

    breaks: list[list[tuple[float, int]]] = [[] for _ in raw]

    # 交点：同一栅格单元里的两条边互相试一下（长边会跨多个单元，用集合去重）
    edge_grid: dict[tuple[int, int], list[int]] = {}
    for eid, (a, b) in enumerate(raw):
        for key in _cells(points[a], points[b], cell):
            edge_grid.setdefault(key, []).append(eid)

    tested: set[tuple[int, int]] = set()
    for bucket in edge_grid.values():
        count = len(bucket)
        for i in range(count):
            for j in range(i + 1, count):
                one, two = bucket[i], bucket[j]
                if one > two:
                    one, two = two, one
                pair = (one, two)
                if pair in tested:
                    continue
                tested.add(pair)
                hit = _crossing(points, raw[one], raw[two], snap)
                if hit is None:
                    continue
                t, u, point = hit
                index = node_of(point[0], point[1])
                breaks[one].append((t, index))
                breaks[two].append((u, index))

    # 结点栅格（含刚插入的交点）→ 每条边的内部断点：近似重合、共线重叠、T 型
    node_grid: dict[tuple[int, int], list[int]] = {}
    for index, (x, y) in enumerate(points):
        node_grid.setdefault((int(x // cell), int(y // cell)), []).append(index)
    for eid, (a, b) in enumerate(raw):
        breaks[eid].extend(_nodes_on_edge(points, node_grid, cell, snap, a, b))

    edges: list[tuple[int, int]] = []
    pieces: set[tuple[int, int]] = set()
    for eid, (a, b) in enumerate(raw):
        for piece in _split_edge(points, a, b, breaks[eid]):
            if piece in pieces:
                continue
            pieces.add(piece)
            edges.append(piece)
    return points, edges


def _pair_edges(incident, points, edges):
    """每个顶点上把出边两两配成"直通"——配对表就是带子的走向。"""
    partner: dict[tuple[int, int], int] = {}
    for node_id, ends in enumerate(incident):
        if len(ends) < 2:
            continue
        origin = points[node_id]
        dirs = []
        for eid in ends:
            a, b = edges[eid]
            dirs.append(_norm(_sub(points[b if a == node_id else a], origin)))

        candidates = []
        for i in range(len(ends)):
            for j in range(i + 1, len(ends)):
                cos = dirs[i][0] * dirs[j][0] + dirs[i][1] * dirs[j][1]
                if cos > 0.0:
                    # 同向（夹角 < 90°）：带子在这里"折返"，不成对
                    continue
                candidates.append((_sin_between(dirs[i], dirs[j]), i, j))
        candidates.sort()

        used = [False] * len(ends)
        for _straightness, i, j in candidates:
            if used[i] or used[j]:
                continue
            used[i] = used[j] = True
            partner[(node_id, ends[i])] = ends[j]
            partner[(node_id, ends[j])] = ends[i]
    return partner


def _trace(edges, partner):
    """按配对表追带子：每个顶点都直通就一直走下去，配不上就在那里收尾。"""
    used = [False] * len(edges)
    strands = []

    for eid0 in range(len(edges)):
        if used[eid0]:
            continue
        start, far = edges[eid0]
        nodes = [start, far]
        walked = [eid0]
        used[eid0] = True
        closed = False

        # 向右
        cur, in_edge = far, eid0
        while True:
            nxt = partner.get((cur, in_edge))
            if nxt is None:
                break
            if used[nxt]:
                closed = nxt == eid0
                break
            used[nxt] = True
            a, b = edges[nxt]
            cur = b if a == cur else a
            nodes.append(cur)
            walked.append(nxt)
            in_edge = nxt

        # 向左（闭合的话右边已经绕回来了，不用再走）
        if not closed:
            cur, in_edge = start, eid0
            while True:
                nxt = partner.get((cur, in_edge))
                if nxt is None or used[nxt]:
                    break
                used[nxt] = True
                a, b = edges[nxt]
                cur = b if a == cur else a
                nodes.insert(0, cur)
                walked.insert(0, nxt)
                in_edge = nxt

        if closed:
            # 绕回起点时末节点是重复的；边表里已经含"收口"的那条边，长度因此对得上
            while len(nodes) > 1 and nodes[0] == nodes[-1]:
                nodes.pop()
        strands.append({"nodes": nodes, "edges": walked, "closed": closed})
    return strands


# --- 弧长 ------------------------------------------------------------------


def _cumulative(points):
    cum = [0.0]
    for a, b in zip(points, points[1:]):
        cum.append(cum[-1] + _dist(a, b))
    return cum


def _at(points, cum, where: float) -> Point:
    """弧长 ``where`` 处的插值点。"""
    if where <= 0.0:
        return points[0]
    if where >= cum[-1]:
        return points[-1]
    # 节点数不多（几十），线性扫比 bisect 更省事也够快
    for i in range(1, len(cum)):
        if cum[i] >= where:
            span = cum[i] - cum[i - 1]
            ratio = (where - cum[i - 1]) / span if span > 1e-12 else 0.0
            a, b = points[i - 1], points[i]
            return (a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio)
    return points[-1]


def _slice(points, cum, start: float, end: float) -> list[Point]:
    """取 ``[start, end]`` 这段弧长对应的折线（端点插值，中间节点原样保留）。"""
    out = [_at(points, cum, start)]
    for i in range(1, len(points) - 1):
        if start < cum[i] < end:
            out.append(points[i])
    tail = _at(points, cum, end)
    if _dist(out[-1], tail) > 1e-9:
        out.append(tail)
    return out


def _subtract(total: float, cuts, closed: bool):
    """从 ``[0, total]`` 里减掉切口区间，返回保留的若干段。"""
    merged: list[list[float]] = []
    for entry in sorted(cuts):
        start, end = entry[0], entry[1]
        start = max(0.0, start)
        end = min(total, end)
        if end - start <= 0.0:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    # 闭合带子的切口跨过接缝时，首尾两段其实是同一段，先接起来
    if closed and len(merged) > 1 and merged[0][0] <= 0.0 and merged[-1][1] >= total:
        merged[0][0] = merged[-1][0] - total
        merged.pop()

    keep = []
    cursor = 0.0
    for start, end in merged:
        if start > cursor:
            keep.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < total:
        keep.append((cursor, total))
    return keep


def _end_cut(reach: float, gap: float, sin_angle: float, tuck: bool) -> tuple[float, bool]:
    """压下去那一端要往回切多远，以及该用哪种端头。

    ``reach`` 是**可见半宽**（本体与 casing 取大者），``sin(θ)`` 取该顶点上**最斜**的
    那根交叉带（缺口取最大，宁可多切也不要戳出来）。

    ``tuck=False``（旧方案）：**不许压进对方身下**。平头的切面垂直于自己，两个角到对方
    中心线是 ``d·sinθ ± reach·cosθ``，要让靠里的角贴住对方边缘::

        d = reach * (1 + cosθ) / sinθ = reach / tan(θ/2)

    90° 时 ``d = reach``，切口正好压在对方边缘线上——这才是 Alhambra 那种"切进去"的
    样子；45° 时靠外的角会甩出 ``2·reach·cosθ``，所以斜交叉只用圆头::

        (d - reach) * sin(θ) >= reach + gap
        d = reach + (reach + gap) / sin(θ)

    ``tuck=True``：允许那一端**钻到对方身下**，于是可以取"贴平"的那个解——

        d = (reach - gap) * (1 - cosθ) / sinθ = (reach - gap) * tan(θ/2)

    钻进去的那一截为什么看不见：本体同色不透明（盖住对方本体看不出来），描边在
    ``Interlace Case`` 这张独立图层里、永远在所有本体下面（深压深也看不出来）。露在
    外面的轮廓因此精确与对方边缘重合，斜交叉也能用平头。代价只剩"本体咬掉对方描边环
    上一小块"（45° 实测 0.53 reach²，比零重叠方案里圆头已经咬的 0.23 略大）。
    ``sinθ < TUCK_SIN`` 时钻得没完没了，退回零重叠的圆头。
    """
    sine = max(sin_angle, SIN_FLOOR)
    cosine = math.sqrt(max(0.0, 1.0 - sine * sine))
    if tuck and sin_angle >= TUCK_SIN:
        return max(0.0, reach - gap) * (1.0 - cosine) / sine, True
    if not tuck and sin_angle >= FLAT_CAP_SIN:
        return reach * (1.0 + cosine) / sine, True
    return reach + (reach + gap) / sine, False


# --- 主流程 ----------------------------------------------------------------


def build_weave(polygons, style: WeaveStyle | None = None) -> Weave:
    """把一组闭合色块多边形编成带子。

    ``polygons`` 是**世界坐标**下的点列（``scene`` 里已经过仿射与去重）；返回的
    ``paths`` 同样在世界坐标里，可直接变成开口 ``Shape``。
    """
    style = style or WeaveStyle()
    radius = max(0.0, float(style.half_width))
    casing = max(0.0, float(style.casing))
    #: 可见半宽：casing 画在本体下面且更宽，所以真正占地方、真正要被让开的是它。
    reach = max(radius, casing)
    gap = max(0.0, float(style.gap_ratio)) * max(reach, 1e-12)
    tuck = bool(style.tuck)

    points, edges = _edge_network(polygons, style.snap)
    result = Weave(nodes=len(points), edges=len(edges))
    if not edges:
        return result

    incident: list[list[int]] = [[] for _ in points]
    for eid, (a, b) in enumerate(edges):
        incident[a].append(eid)
        incident[b].append(eid)

    partner = _pair_edges(incident, points, edges)
    strands = _trace(edges, partner)
    result.strands = len(strands)

    # 通过段：一个顶点上被配对起来的两条边，就是一根带子在那里穿过
    pid_at: dict[tuple[int, int], int] = {}
    pid_node: list[int] = []
    pid_ends: list[tuple[int, int]] = []
    for node_id, ends in enumerate(incident):
        for eid in ends:
            other = partner.get((node_id, eid))
            if other is None or other < eid:
                continue
            pid = len(pid_node)
            pid_node.append(node_id)
            pid_ends.append((eid, other))
            pid_at[(node_id, eid)] = pid
            pid_at[(node_id, other)] = pid

    by_node: dict[int, list[int]] = {}
    for pid, node_id in enumerate(pid_node):
        by_node.setdefault(node_id, []).append(pid)
    # 两根以上带子穿过同一个顶点才算交叉；度 ≤ 3 的 T 结点只有一个通过段，自然不断开
    crossings = {node: pids for node, pids in by_node.items() if len(pids) > 1}
    result.crossings = len(crossings)

    # 每根带子的走向（用于给"第几处交叉"编号）
    pid_strand = [-1] * len(pid_node)
    pid_order = [0] * len(pid_node)
    cursor = [0] * len(strands)
    for sidx, strand in enumerate(strands):
        nodes = strand["nodes"]
        walked = strand["edges"]
        count = len(nodes)
        indices = range(count) if strand["closed"] else range(1, count - 1)
        for i in indices:
            node_id = nodes[i]
            in_edge = walked[i - 1] if i > 0 else walked[-1]
            pid = pid_at.get((node_id, in_edge))
            if pid is None or pid_at.get((node_id, walked[i])) != pid:
                continue
            pid_strand[pid] = sidx
            if node_id in crossings:
                pid_order[pid] = cursor[sidx]
                cursor[sidx] += 1

    # --- over/under ---------------------------------------------------------
    #
    # 硬约束只有一条：**同一个交叉点上相邻的两根带子必须一上一下**（绕顶点交替）。
    # 这保证每个交叉点正好一根压上、一根压下，任何情况下都不会出现两根带子重叠或
    # 两边同时缺口的破绽——这是"看起来像编织"的底线，必须无条件满足。
    #
    # "沿带子走到下一处交叉就取反"只能当**偏好**：色块拼图里有三角形/五边形的环，
    # 奇数个交叉点绕一圈回来本来就不可能交替（绕一圈要翻转奇数次），硬当约束会把
    # 底线破掉。所以先用 BFS 沿带子传播一个理想相位（异或方程，奇环按先到的赋值
    # 放过去），再在每个顶点上用**多数票**选"从哪一条开始压上"的起点：
    # 每个通过段投票给它自己想要的起点，票多的那个就是该顶点的起点。
    phase = [-1] * len(strands)
    links: list[list[tuple[int, int]]] = [[] for _ in strands]
    ordered_crossings: dict[int, list[int]] = {}
    for node_id, pids in crossings.items():
        pids = sorted(
            pids, key=lambda pid: _passage_angle(pid, pid_node, pid_ends, points, edges)
        )
        ordered_crossings[node_id] = pids
        for a, b in zip(pids, pids[1:]):
            sa, sb = pid_strand[a], pid_strand[b]
            if sa < 0 or sb < 0 or sa == sb:
                continue  # 自交：相位方程退化成恒等式，跳过
            flip = 1 ^ (pid_order[a] & 1) ^ (pid_order[b] & 1)
            links[sa].append((sb, flip))
            links[sb].append((sa, flip))

    for seed in range(len(strands)):
        if phase[seed] >= 0:
            continue
        phase[seed] = 0
        stack = [seed]
        while stack:
            current = stack.pop()
            for other, flip in links[current]:
                want = phase[current] ^ flip
                if phase[other] < 0:
                    phase[other] = want
                    stack.append(other)

    def _wanted(pid: int) -> int:
        """这根带子希望在自己这个通过段上是什么状态（0 = 压下，1 = 压上）。"""
        strand = pid_strand[pid]
        if strand < 0:
            return 0
        return int(phase[strand] ^ (pid_order[pid] & 1))

    is_over = [False] * len(pid_node)
    for node_id, pids in ordered_crossings.items():
        votes = 0
        for slot, pid in enumerate(pids):
            votes += 1 if (_wanted(pid) ^ (slot & 1)) else -1
        base = 1 if votes > 0 else 0
        for slot, pid in enumerate(pids):
            is_over[pid] = bool(base ^ (slot & 1))
            if int(is_over[pid]) != _wanted(pid):
                # 票数不够、没能顺带满足"沿带子交替"的通过段（奇数环必然有）
                result.mismatches += 1

    for sidx, strand in enumerate(strands):
        nodes = strand["nodes"]
        walked = strand["edges"]
        count = len(nodes)
        closed = strand["closed"]
        raw = [points[node] for node in nodes]
        ring = raw + [raw[0]] if closed else raw
        cum = _cumulative(ring)
        total = cum[-1]
        if total <= 0.0:
            continue

        # 每个切口：(进侧弧长, 出侧弧长, 交叉点, 进侧是否平头, 出侧是否平头)
        cuts: list[tuple[float, float, int, bool, bool]] = []
        if reach > 0.0:
            indices = range(count) if closed else range(1, count - 1)
            for i in indices:
                node_id = nodes[i]
                if node_id not in crossings:
                    continue
                in_edge = walked[i - 1] if i > 0 else walked[-1]
                pid = pid_at.get((node_id, in_edge))
                if pid is None or is_over[pid]:
                    continue

                here = points[node_id]
                prev_node = nodes[i - 1] if i > 0 else nodes[-1]
                next_node = nodes[(i + 1) % count]
                sin_in = _min_sin(pid, _norm(_sub(here, points[prev_node])),
                                  crossings[node_id], pid_node, pid_ends, points, edges)
                sin_out = _min_sin(pid, _norm(_sub(points[next_node], here)),
                                   crossings[node_id], pid_node, pid_ends, points, edges)
                d_in, flat_in = _end_cut(reach, gap, sin_in, tuck)
                d_out, flat_out = _end_cut(reach, gap, sin_out, tuck)
                cuts.append((cum[i] - d_in, cum[i] + d_out, node_id, flat_in, flat_out))
                result.cuts += 1

        if not cuts:
            keep = [(0.0, total)]
            unbroken = closed
        else:
            unbroken = False
            if closed:
                wrapped = []
                for start, end, node_id, flat_in, flat_out in cuts:
                    if end > total:
                        wrapped.append((start, total, node_id, flat_in, flat_out))
                        wrapped.append((0.0, end - total, node_id, flat_in, flat_out))
                    elif start < 0.0:
                        wrapped.append((0.0, end, node_id, flat_in, flat_out))
                        wrapped.append((total + start, total, node_id, flat_in, flat_out))
                    else:
                        wrapped.append((start, end, node_id, flat_in, flat_out))
                cuts = wrapped
            keep = _subtract(total, cuts, closed)

        # 残段修剪：缺口把一段切得比笔宽还短时，画出来就是一颗浮着的"珠子"，比"这里不编"
        # 难看得多。所以把切出这颗珠子的那一刀收回去：那个交叉点两根带子直接叠在一起，
        # 同色不透明的情况下完全看不出来（代价是该点失去上下之分）。
        if keep and reach > 0.0:
            for _ in range(_PRUNE_PASSES):
                bead = None
                for start, end in keep:
                    if start > 0.0 and end < total and end - start < PRUNE * reach:
                        bead = start
                        break
                if bead is None:
                    break
                drop = None
                for index, entry in enumerate(cuts):
                    if abs(entry[1] - bead) < 1e-9:
                        drop = index
                        break
                if drop is None:
                    break
                del cuts[drop]
                result.pruned += 1
                keep = _subtract(total, cuts, closed)

        # 闭合带子被切出一个跨接缝的整段时，_subtract 会给出两段，这里合并回去
        if closed and len(keep) > 1 and keep[0][0] <= 0.0 and keep[-1][1] >= total:
            head = keep.pop(0)
            tail = keep.pop()
            keep.append((tail[0], head[1] + total))

        emitted: list[tuple[float, float, int]] = []
        for start, end in keep:
            if end - start < STUB * reach:
                continue
            if unbroken:
                piece = raw
            else:
                piece = _slice(ring, cum, max(0.0, start), min(total, end))
                if len(piece) < 2:
                    continue
            piece_id = len(result.paths)
            flat_start = flat_end = False
            for cut in cuts:
                if abs(min(max(cut[0], 0.0), total) - end) < 1e-9:
                    flat_end = cut[3]  # 这一段结束在切口的"进侧"上
                elif abs(min(max(cut[1], 0.0), total) - start) < 1e-9:
                    flat_start = cut[4]  # 这一段从切口的"出侧"起步
            result.paths.append((piece, unbroken))
            result.caps.append((flat_start, flat_end))
            emitted.append((start, end, piece_id))
            # 端头正好落在交叉点上 ⇒ 这一段是"压在下面"的
            for where, at_end in ((start, True), (end, False)):
                for cut in cuts:
                    value = cut[1] if at_end else cut[0]
                    if abs(min(max(value, 0.0), total) - where) < 1e-9:
                        result.links.append((cut[2], piece_id, True))
                        break

        # 交叉点落在某一段的内部 ⇒ 这一段是"压在上面"的（绘制次序约束的另一半）
        for i in (range(count) if closed else range(1, count - 1)):
            node_id = nodes[i]
            if node_id not in crossings:
                continue
            in_edge = walked[i - 1] if i > 0 else walked[-1]
            pid = pid_at.get((node_id, in_edge))
            if pid is None or not is_over[pid]:
                continue
            where = cum[i]
            for start, end, piece_id in emitted:
                if start - 1e-9 <= where <= end + 1e-9:
                    result.links.append((node_id, piece_id, False))
                    break

    return result


def _passage_angle(pid, pid_node, pid_ends, points, edges) -> float:
    """通过段的走向角（绕顶点排序用）。"""
    node_id = pid_node[pid]
    origin = points[node_id]
    dirs = []
    for eid in pid_ends[pid]:
        a, b = edges[eid]
        dirs.append(_norm(_sub(points[b if a == node_id else a], origin)))
    axis = _sub(dirs[0], dirs[1])  # 直通时 = 2×方向，转角时 = 两条边的对角线
    return math.atan2(axis[1], axis[0])


def _passage_axis(pid, pid_node, pid_ends, points, edges) -> Point:
    node_id = pid_node[pid]
    origin = points[node_id]
    dirs = []
    for eid in pid_ends[pid]:
        a, b = edges[eid]
        dirs.append(_norm(_sub(points[b if a == node_id else a], origin)))
    return _norm(_sub(dirs[0], dirs[1]))


def _min_sin(pid, direction, pids, pid_node, pid_ends, points, edges) -> float:
    """``direction`` 与同一顶点上其它通过段之间夹角正弦的最小值。"""
    worst = 1.0
    for other in pids:
        if other == pid:
            continue
        axis = _passage_axis(other, pid_node, pid_ends, points, edges)
        if axis == (0.0, 0.0):
            continue
        worst = min(worst, _sin_between(direction, axis))
    return worst
