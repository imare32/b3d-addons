"""编织：把一张闭合多边形边网编成上下交替的带子。

源自 ``gen_zellij/weave.py``（那套实现在 Blender 5.2.2 上实测过），只有两处改动：

1. :func:`edge_network` 由私有改成公开——玫瑰线的色块（:mod:`faces`）要复用同一张
   平面化边网，色块边界才会与带子中心线严格重合；
2. 模块文档里提到的 ``Outlines`` / ``Fills`` / ``Interlace`` 指的是 ``gen_zellij`` 的
   图层名，本插件对应 ``Rosette`` / ``Faces`` / ``Rosette Case``，语义一致。

输入从"色块多边形"换成了"玫瑰线闭合曲线"（:class:`~.rosette.Curve`），但算法不需要
任何改动：曲线自交处本来就是结点，平面化会把它切成标准的 X 交叉，配对时"最接近直通"
的两条边正好是同一条曲线的两段——于是带子沿着玫瑰线自己走，压上 / 压下全自动。

判据与几何无关：over/under 是挂在"顶点 + 边端"上的组合状态。只要一个顶点能配对出两条
以上"直通"的路径，那里就是交叉点。做法照抄 Alhambra 的 ``interlace`` 图层：顶点出边按
角度排序 → 绕一圈交替 → 沿带子走到下一处交叉时取反。

三条硬约束（都直接决定观感）：

* **重复的边只算一条带子**：不去重就会有两条带子争同一段，over/under 立刻自相矛盾
  （去重键是"端点量化后无序比较"的边）；
* **带子 ≠ 多边形**：带子是"穿过结点时转角最小的路径"，闭合轮廓只是它的边料；
  顶点配对时按"最接近反向"贪心，转角超过 90° 的不配（配不上就是带子末端，
  玫瑰线的花尖就落在这一档：那里的两条边夹角很小，带子在此收尾、圆头收边）；
* **不靠绘制顺序**：over/under 关系里存在环（A 压 B、B 压 C、C 压 A），任何"分两层、
  一层压另一层"都排不出全局序，所以必须把下面的带子真的切掉。

切多少由带宽与交叉角决定（:func:`_end_cut`），所以**带宽是几何参数**，改它必须重新生成。
``WeaveStyle.tuck``（默认开）把本体切到**完全退出对方 footprint**（``d = reach/tan(θ/2)``，
角度越斜退得越远：90° → 1.0、45° → 2.4、22.5° → 5.0 个可见半宽），退出来的缺口由
:func:`_tip_patch` 补一块沿对方边缘裁齐的多边形——于是轮廓在任何角度都贴平，端头全是平头。
关掉则回到零重叠方案：同一套几何，但斜交叉只能用圆头。

纯 Python，不 import bpy；结果与随机流无关（同 seed 必然同结果）。
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field

__all__ = ["Point", "Weave", "WeaveStyle", "build_weave", "edge_network"]

Point = tuple[float, float]

#: 端点聚类的默认容差 = 图形包围盒跨度 × 该比例（画布 10 时约 0.0017，是色块最短边
#: 0.06 的 3%）。实测同一段缝的两份坐标差在 1e-6~1e-3，量化去重会漏掉一半。
SNAP_RATIO = 2e-4

#: 交叉角正弦的下限（**只在 ``tuck=False`` 时用到**）：圆头公式里的 ``1/sin`` 会把缺口
#: 放大到吃掉整条带子，这里留一点余量给退化情况。``tuck=True`` 必须用真实的角度——
#: 一旦在这儿截断，切口就短于需要的退让距离，一角戳进对方 footprint（见 ``_end_cut``）。
SIN_FLOOR = 0.5

#: 配对时"折返"的判定容差（出边方向的点积）：夹角明显小于 90° 才不配对，直角算一对。
#: 不能用 0——直角转角的方向点积实测只有 ±5e-4 的噪声，严格比较会让同构结点的结果随机。
PAIR_COS = 0.02

#: 短于 ``STUB * 半径`` 的残段直接丢掉：它只会画成一个圆点。
STUB = 0.5

#: 短于 ``PRUNE * 半径`` 的残段会被"修剪"掉：连着的那个缺口收回，两根带子叠在一起。
#: 一颗浮着的小珠子比"这里不编"难看得多，而且同色不透明时叠画看不出来。
PRUNE = 2.0

#: 交叉角正弦高于此值（≈ 64°..116°）才用平头，**只在 ``WeaveStyle.tuck=False``（旧方案：
#: 不许压进对方身下）时用到**——那一版 45° 上平头会甩出尖角缺口，只能圆头。
#: ``tuck=True`` 一律平头：切口退到对方 footprint 之外，缺口归端头补片。
FLAT_CAP_SIN = 0.9

#: 端头补片（:func:`_tip_patch`）的触发门槛：缺口短于 ``TIP_MIN × 可见半宽`` 就不补——
#: 90° 上笔划切口本来就与对方边缘平齐（缺口 0），补片只会和笔划重叠。
TIP_MIN = 0.5

#: 端头补片越过笔划切口的那一点点重叠（``TIP_OVERLAP × 半宽``）：只为了让两块同色填充
#: 叠住、不留接缝。取太大（原来是一整个半宽）会让补片伸到隔壁带子的描边环上。
TIP_OVERLAP = 0.25

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
    #: 缺口在"刚好贴住上方带子"之外再多留的量，单位是**可见半宽**的倍数；0 = 正好顶住。
    #: ``tuck=True`` 时由端头补片让出来（补片少补这么多），留出的那条深色就是缝。
    gap_ratio: float = 0.0
    #: 端点聚类容差（世界单位）；0 = 按包围盒自动取 ``SNAP_RATIO``。
    snap: float = 0.0
    #: 允许压下的那一端钻到对方身下（切口取"贴平对方边缘"的解，斜交叉也用平头）。
    #: 关掉是旧的零重叠方案：只有接近垂直的交叉能贴平，其余用圆头（见 ``_end_cut``）。
    tuck: bool = True


@dataclass(slots=True)
class Weave:
    """一次编织的结果：可以直接变成 ``scene.Shape`` 的点列，外加统计。"""

    #: ``(点列, 是否闭合)``。描边层用这一套：``tuck`` 时不切口（深色压深色、又永远在
    #: 所有本体下面，切与不切看不出来），所以是整根带子；``tuck=False`` 时是切过的。
    paths: list[tuple[list[Point], bool]] = field(default_factory=list)
    #: 与 ``paths`` 一一对应：``(起点是否平头, 终点是否平头)``。
    caps: list[tuple[bool, bool]] = field(default_factory=list)
    #: 本体那一套（世界坐标，已切口）；空 = 与 ``paths`` 相同。本体是浅色，必须完全退出
    #: 对方 footprint，否则会咬断对方那圈深色描边，上下遮挡就没了。
    body_paths: list[tuple[list[Point], bool]] = field(default_factory=list)
    #: 与 ``body_paths`` 一一对应。
    body_caps: list[tuple[bool, bool]] = field(default_factory=list)
    #: 本体退出去留下的缺口 → 沿**对方带子边缘**裁齐的凸多边形（世界坐标），画在本体层、
    #: 只填不描。它把轮廓补回边缘线上，等于把多边形方案的裁切局部搬了进来：贴平与遮挡
    #: 同时成立（笔划端头做不到——切面只能垂直于自己，对方边缘是斜的）。
    tips: list[list[Point]] = field(default_factory=list)
    nodes: int = 0
    edges: int = 0
    strands: int = 0
    crossings: int = 0
    cuts: int = 0
    #: 被收回的缺口数（残段太短，宁可不编，见 ``PRUNE``）。
    pruned: int = 0
    #: 绘制次序用的"下→上"关系：``(交叉点, 段序号, 是否压在下面)``。同一个交叉点上
    #: 必然同时有压上的一段和压下的若干段；按"下先画"拓扑排序就能让圆头被盖住。
    #: 段序号指 ``paths``（外廓那一套）里的下标。
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


def edge_network(polygons, snap: float = 0.0):
    """闭合多边形 → **平面化**后的无向边网 ``(points, edges)``。

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
    """每个顶点上把出边两两配成"直通"——配对表就是带子的走向。

    只排除**折返**的配对（两条边指向同一侧，夹角 < 90°）。判据带一个 ``PAIR_COS`` 的
    容差，不能写成 ``cos > 0``：直角（90°）的转角在数值上正好落在 0 附近（实测 |cos| ≤
    5e-4），严格比较等于拿噪声决定配对——同一张旋转对称的图里，同构的直角拐角会有一半
    配成带子、一半留成两个断头，渲染出来就是"一处尖头、一处圆头"。
    """
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
                if cos > PAIR_COS:
                    # 同向（夹角明显小于 90°）：带子在这里"折返"，不成对
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
    """弧长 ``where`` 处的插值点。

    配对把整张边网串成几条**很长的闭合带子**之后（density 24 有几千个节点），这里必须
    二分；线性扫会让每个切口都过一遍整根带子，实测慢二十倍。
    """
    if where <= 0.0:
        return points[0]
    if where >= cum[-1]:
        return points[-1]
    index = bisect_left(cum, where)
    index = 1 if index < 1 else index
    span = cum[index] - cum[index - 1]
    ratio = (where - cum[index - 1]) / span if span > 1e-12 else 0.0
    a, b = points[index - 1], points[index]
    return (a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio)


def _slice(points, cum, start: float, end: float) -> list[Point]:
    """取 ``[start, end]`` 这段弧长对应的折线（端点插值，中间节点原样保留）。

    闭合带子跨接缝的那一段会把首尾两段接起来，``end`` 因此会超过总长——这里必须真的
    绕回去接着走，不能拿 ``_at`` 钳到末点（那样后半截整段丢掉，只剩两条圆头残段）。
    """
    total = cum[-1]
    if end > total:
        head = _slice(points, cum, start, total)
        tail = _slice(points, cum, 0.0, end - total)
        return head + tail[1:]
    out = [_at(points, cum, start)]
    low = max(1, bisect_right(cum, start))
    high = min(bisect_left(cum, end), len(points) - 1)
    for i in range(low, high):
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
    那根交叉带（缺口取最大，宁可多切也不要戳出来）。切的对象始终是**本体**那一层。

    ``tuck=True``（默认）：本体**退出对方 footprint**，`d = reach / tan(θ/2)`——平头的
    两个角到对方中心线是 ``d·sinθ ± reach·cosθ``，让靠里的角正好贴住对方边缘::

        d = reach * (1 + cosθ) / sinθ = reach / tan(θ/2)

    90° 时 ``d = reach``，切口正好压在对方边缘线上；角度越斜退得越远（45° → `2.41×reach`、
    22.5° → `5.0×reach`），退出来的缺口由 :func:`_tip_patch` 补成一条沿对方边缘的斜边。
    **为什么不直接贴平**：本体是浅色，它一旦伸进对方 footprint，就必然从对方那圈深色
    描边上咬掉一块——遮挡关系只由那圈描边承载，咬了就没了。所以这里一律平头、一律按
    真实的 ``sinθ`` 算，**不能**套 ``SIN_FLOOR``：截断会让 ``d`` 短于需要的退让距离，
    切口一角照样戳进去。

    ``tuck=False``（旧方案）：同一套几何，但斜交叉用圆头（GP 会在末点之外再糊出半个
    笔宽，"切到 d"实际是"圆头尖停在 ``d - reach`` 处"）::

        (d - reach) * sin(θ) >= reach + gap
        d = reach + (reach + gap) / sin(θ)

    ``sinθ`` 低于 ``FLAT_CAP_SIN`` 时退回后者。
    """
    if tuck:
        # 一律平头、一律按**真实**的 sinθ 切：这里不能再套 SIN_FLOOR，那会让 d 小于
        # 需要的退让距离，切口一角戳进对方 footprint，把描边环咬掉。
        # 代价是角度越斜退得越远（22.5° → 5.0×reach，15° → 7.6×reach），
        # 那片缺口交给 _tip_patch 补。
        sine = max(sin_angle, 1e-3)
        cosine = math.sqrt(max(0.0, 1.0 - sine * sine))
        return reach * (1.0 + cosine) / sine, True
    sine = max(sin_angle, SIN_FLOOR)
    cosine = math.sqrt(max(0.0, 1.0 - sine * sine))
    if sin_angle >= FLAT_CAP_SIN:
        return reach * (1.0 + cosine) / sine, True
    return reach + (reach + gap) / sine, False


def _clip_halfplane(polygon, normal, offset):
    """凸多边形按半平面 ``p·normal >= offset`` 裁一刀（Sutherland–Hodgman）。"""
    if not polygon:
        return []
    out = []
    count = len(polygon)
    for i in range(count):
        a = polygon[i]
        b = polygon[(i + 1) % count]
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


def _tip_patch(here, towards, cut, half_width, clear, planes):
    """切口端头那块"多边形边"：把本体沿**对方带子的边缘**裁齐。

    笔划的端头只能垂直于自己，而对方边缘是斜的——斜交叉上两者必然错开：要么退到远处
    留一个缺口，要么压进去把对方那圈描边咬断。这个多边形把缺口补成一条斜边，贴平与
    遮挡就能同时成立（等同布尔多边形方案里的那一刀）。

    ``here`` 交叉点，``towards`` 从交叉点指向**本体材料内部**的单位向量，``cut`` 该端
    的切口距离（沿 ``towards`` 量），``clear`` 要留出的距离（可见半宽 + 间隙），
    ``planes`` 要躲开的半平面（世界坐标 ``(单位法线, 偏移)``，``p·法线 >= 偏移`` 是保留
    侧，调用方已按补片那一侧取好方向）。返回世界坐标的凸多边形；缺口可以忽略时返回
    ``None``。

    补片沿 ``towards`` 从交叉点伸到 ``cut + TIP_OVERLAP × 半宽``——**只**为了和笔划端头
    叠住、不留缝。这个尾巴曾经取一整个半宽（`cut + half_width`），带宽一粗就伸到隔壁
    带子的描边里，在别人的深色环上压出一道浅色缺口；缩小到四分之一个半宽就够叠住，而且
    不再伸那么远。
    """
    if half_width <= 0.0 or clear <= 0.0:
        return None
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
        polygon = _clip_halfplane(
            polygon, local, offset - (origin_x * plane_x + origin_y * plane_y)
        )
        if len(polygon) < 3:
            return None
    if min(point[0] for point in polygon) > cut - TIP_MIN * clear:
        return None  # 切口本来就贴着对方边缘（90°），没什么可补
    return [
        (here[0] + s * towards[0] + q * normal[0], here[1] + s * towards[1] + q * normal[1])
        for s, q in polygon
    ]


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

    points, edges = edge_network(polygons, style.snap)
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

    # 描边层**不切口**：它是最深的一层、颜色又只有一种，对方的本体压在它上面、对方的
    # 描边与它同色——切与不切一个像素都不差。真正必须停下来的只有本体（浅色），它停在
    # 对方 footprint 之外，缺口由 _tip_patch 补齐。所以这里只有一套切口。
    cut_body = bool(tuck) and 0.0 < radius < reach
    if cut_body:
        result.paths.extend(
            ([points[node] for node in strand["nodes"]], strand["closed"])
            for strand in strands
        )
        result.caps.extend((False, False) for _ in strands)
        paths = result.body_paths
        caps_out = result.body_caps
    else:
        paths = result.paths
        caps_out = result.caps

    # 补片要躲开的距离：可见半宽 + 间隙。缺口两侧都按它裁。
    band_gap = reach + gap

    for strand in strands:
        nodes = strand["nodes"]
        walked = strand["edges"]
        count = len(nodes)
        closed = strand["closed"]
        piece_links = result.links
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
                if not cut_body:
                    continue
                # 本体退出对方 footprint 留下的缺口：按对方边缘补一块多边形。
                # 裁剪平面**只取本结点的其它通过段**——补片就是为它们让位的。不能额外拿
                # 附近带子去裁：那会切到缺口本身（补片被裁没、或者漏一条缝）。
                axes = [
                    _passage_axis(other, pid_node, pid_ends, points, edges)
                    for other in crossings[node_id]
                    if other != pid
                ]
                for towards, d_end, flat in (
                    (_sub(points[prev_node], here), d_in, flat_in),
                    (_sub(points[next_node], here), d_out, flat_out),
                ):
                    if not flat:
                        continue
                    direction = _norm(towards)
                    far_point = (
                        here[0] + direction[0] * d_end,
                        here[1] + direction[1] * d_end,
                    )
                    planes = []
                    for axis in axes:
                        side = (-axis[1], axis[0])
                        # 法线朝补片那一侧，约束就只剩一条半平面：(p - here)·side >= clear
                        if (
                            (far_point[0] - here[0]) * side[0]
                            + (far_point[1] - here[1]) * side[1]
                            < 0.0
                        ):
                            side = (-side[0], -side[1])
                        planes.append(
                            (side, here[0] * side[0] + here[1] * side[1] + band_gap)
                        )
                    patch = _tip_patch(
                        here, direction, d_end, radius, band_gap, planes
                    )
                    if patch is not None:
                        result.tips.append(patch)

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

        # 弧长位置 → 切口：端头类型与"压在下面"都要拿段的首尾去反查切口。原来是逐段扫
        # 所有切口（O(段 × 切口)），配对把整张网串成几条长带子之后成了瓶颈（density 24
        # 实测 750 万次 min/max/abs），先建表。
        start_of: dict[float, tuple] = {}
        exit_of: dict[float, tuple] = {}
        for cut in cuts:
            start_of.setdefault(round(min(max(cut[0], 0.0), total), 7), cut)
            exit_of.setdefault(round(min(max(cut[1], 0.0), total), 7), cut)

        emitted: list[tuple[float, float, int]] = []
        for start, end in keep:
            if end - start < STUB * reach:
                continue
            if unbroken:
                piece = raw
            else:
                # end 可以超过总长（闭合带子跨接缝的那一段），_slice 负责绕回去
                piece = _slice(ring, cum, max(0.0, start), end)
                if len(piece) < 2:
                    continue
            piece_id = len(paths)
            wrapped_end = end - total if end > total else end
            entering = start_of.get(round(wrapped_end, 7))
            leaving = exit_of.get(round(start, 7))
            # 这一段结束在切口的"进侧"上 / 从切口的"出侧"起步
            flat_end = bool(entering and entering[3])
            flat_start = bool(leaving and leaving[4])
            paths.append((piece, unbroken))
            caps_out.append((flat_start, flat_end))
            emitted.append((start, end, piece_id))
            # 端头正好落在交叉点上 ⇒ 这一段是"压在下面"的
            for cut in (leaving, entering):
                if cut is not None:
                    piece_links.append((cut[2], piece_id, True))

        # 交叉点落在某一段的内部 ⇒ 这一段是"压在上面"的（绘制次序约束的另一半）。
        # 段与交叉点都是按弧长递增的，用游标推进，别对每个交叉点都扫一遍所有段。
        seam_piece = emitted[-1][2] if emitted and emitted[-1][1] > total else None
        piece_cursor = 0
        for i in (range(count) if closed else range(1, count - 1)):
            node_id = nodes[i]
            if node_id not in crossings:
                continue
            in_edge = walked[i - 1] if i > 0 else walked[-1]
            pid = pid_at.get((node_id, in_edge))
            if pid is None or not is_over[pid]:
                continue
            where = cum[i]
            while piece_cursor < len(emitted) and emitted[piece_cursor][1] < where - 1e-9:
                piece_cursor += 1
            start, end, piece_id = (
                emitted[piece_cursor] if piece_cursor < len(emitted) else (0.0, 0.0, -1)
            )
            if piece_id >= 0 and start - 1e-9 <= where <= end + 1e-9:
                piece_links.append((node_id, piece_id, False))
            elif seam_piece is not None and where + total <= emitted[-1][1] + 1e-9:
                # 跨接缝那一段的后半截
                piece_links.append((node_id, seam_piece, False))

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
