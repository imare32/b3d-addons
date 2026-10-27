"""凯尔特结的路径（只依赖 ``numpy`` 和外面传进来的 ``BMesh``，能脱离界面单独跑）。

**出处**：遍历规则照搬 Adam Newgas 的 ``celtic-knot``（Blender 官方 addons 仓库里那份，
Celtic / Lattice / Weave 三种编织），这里只保留 Celtic 一种。跟「顶点上两两配对」的老
写法相比，差别正是路径对不对：配对表按顶点建，可跨边之后 loop 落在边的**另一个端点**上，
旧写法仍拿它去查**本顶点**的配对表，于是路径一格一格乱跳（实测步长 2.2–4.2，还夹着零长
单点带）。照搬参考实现的走向就没有这个问题。

一副凯尔特结是这么长出来的：

1. 结的每个点都落在**网格边的中点**上 —— 一条边一个点，也就是一条边一处交叉；
2. 每条边按「扭转」决定带子跨过它时走哪一侧；Celtic 模式里每条边都同向扭转
   （:data:`TWIST_CW`）；
3. 带子一路「沿当前面转到下一条边 → 这条边扭转就跨到对侧的面」，走回自己走过的方向
   就收尾，所以每根带子都是闭合的；
4. 一条边的两侧必然一正一反（:func:`get_offset` 只看扭转与 ``forward``），交叉处天然
   一上一下；抬起来的方向是**逐角法线**（相邻两角法线的平均），不是整张面的法线。

``visit_strands`` 里 ``exited`` / ``entered`` 那套记账是参考实现的原样：每个方向只允许
被一根带子用掉一次，走回用过的方向就是终点。
"""

from __future__ import annotations

import time

import numpy as np

__all__ = [
    "IGNORE",
    "Knot",
    "STRAIGHT",
    "TWIST_CCW",
    "TWIST_CW",
    "build",
    "celtic_twists",
    "get_offset",
    "mean_edge_length",
    "mesh_signature",
    "next_edge_loop",
    "next_face_loop",
    "signature",
    "visit_strands",
]

#: 跨过一条边时带子怎么走（与参考实现的四个标记同名同义）
TWIST_CW = "twist_cw"
TWIST_CCW = "twist_ccw"
STRAIGHT = "straight"
IGNORE = "ignore"


class Knot:
    """一次遍历的结果：带子路径 + 统计。

    ``paths`` 里每根带子是 ``(点序列, 是否闭合)`` —— 点序列直接当曲线样条的控制点用，
    闭合的带子首尾相连，所以这里不出「点 + 边」，曲线对象不需要再从中反推拓扑。
    """

    __slots__ = ("paths", "stats")

    def __init__(self, paths, stats):
        self.paths = paths
        self.stats = stats

    def __repr__(self):  # pragma: no cover - 调试用
        return f"<Knot {self.stats['points']} 点 {self.stats['strands']} 带>"


# --------------------------------------------------------------------- 带子怎么走
class Directed:
    """「当前在哪条边 + 朝哪个方向」：``loop`` 是边的一侧，``forward`` 是朝向。"""

    __slots__ = ("loop", "forward")

    def __init__(self, loop, forward):
        self.loop = loop
        self.forward = forward


def is_boundary(loop) -> bool:
    """这条边只有 ``loop`` 这一张面？

    ``BMLoop.link_loops`` 是「同一条边上**别的** loop」，空就说明这条边是边界边
    （参考实现同样用 ``len(loop.link_loops) == 0`` 判定，两边语义一致）。
    """
    return not loop.link_loops


def next_face_loop(directed):
    """沿当前面转到下一条边（拐过当前边的远端顶点），跳过边界边。

    整张面扫一圈还是找不到非边界边（边界上的孤立面，比如单独一个四边形）就返回
    ``None``：参考实现这里会原地空转，我们当走到头。
    """
    loop = directed.loop
    forward = directed.forward
    face = loop.face
    for _ in range(len(face.loops)):
        loop = loop.link_loop_next if forward else loop.link_loop_prev
        if not is_boundary(loop):
            return Directed(loop, forward)
    return None


def next_edge_loop(directed):
    """跨过同一条边到另一侧的面。

    ``link_loops`` 的收尾两头就是两侧的面；跨过去以后 loop 落在边的**另一个端点**
    上（同一张面的绕向固定，两侧必然反向），所以 ``forward`` 要跟着翻 —— 这一句是
    参考实现被 4.x / 5.x 都认的写法，别改。
    """
    loop = directed.loop
    others = loop.link_loops
    if not others:
        return None
    other = others[0] if directed.forward else others[-1]
    forward = (other.vert is loop.vert) == directed.forward
    return Directed(other, forward)


def celtic_twists(bm):
    """每条边的扭转标记，返回 ``{边: 标记}``。

    Celtic 就是「每条边都同向扭转」：参考实现按 ``random() < twist_prob`` 掷骰，
    面板给 100% 时恒等于 :data:`TWIST_CW`，这里直接恒给它 —— 去掉随机数，
    同一张网格每次编出来都一模一样。没有面的散边标成忽略。
    """
    return {
        edge: (IGNORE if not edge.link_loops else TWIST_CW) for edge in bm.edges
    }


def visit_strands(bm, twists, builder):
    """一根带子一根带子地走，每跨过一次边就回报一次。

    回报给 ``builder`` 的是「跨完之后的那个方向」：先沿面转到下一条边，这条边扭转的话
    再跨到对侧；``prev_loop`` 是跨之前的方向（同一条边，另一侧的面）。

    :return: 异常收尾的带子数（撞上别的带子用过的方向，或被边界围死 —— 参考实现在
        这里直接 ``assert`` 失败，我们记个数收尾，用户网格什么形状都有）
    """
    exited = set()
    entered = set()
    broken = 0

    def trace(start):
        nonlocal broken
        builder.start_strand()
        directed = start
        while True:
            mine = exited if directed.forward else entered
            other = entered if directed.forward else exited
            if directed.loop in mine:
                break
            mine.add(directed.loop)
            advanced = next_face_loop(directed)
            if advanced is None:
                broken += 1
                break
            if advanced.loop in other:
                broken += 1
                break
            other.add(advanced.loop)
            prev_loop = advanced.loop
            twist = twists.get(advanced.loop.edge, IGNORE)
            if twist in (TWIST_CW, TWIST_CCW):
                crossed = next_edge_loop(advanced)
                if crossed is None:
                    broken += 1
                    break
                directed = crossed
            else:
                directed = advanced
            builder.add_loop(prev_loop, directed.loop, twist, directed.forward)
        builder.end_strand()

    for face in bm.faces:
        for loop in face.loops:
            if is_boundary(loop):
                continue
            if loop not in exited:
                trace(Directed(loop, True))
            if loop not in entered:
                trace(Directed(loop, False))
    return broken


def get_offset(up, down, twist, forward):
    """这一处该抬多少（参考实现的原式，连正负约定一起照搬）。

    ``up`` / ``down`` 是「向上 / 向下」的距离，**两者异号**才织得起来；同号整副结会
    塌到同一层（实测：``+0.5 / +0.5`` 全部落在 ``z=+0.5``，``+0.5 / -0.5`` 才是 ±0.5
    交错）。调用方给的 ``up`` / ``down`` 已经自带正负，这里只挑哪个生效。
    """
    if twist == TWIST_CW:
        return down if forward else up
    if twist == TWIST_CCW:
        return up if forward else down
    return (up + down) * 0.5


# --------------------------------------------------------------------- 出点
class _PathPoints:
    """给 :func:`visit_strands` 当 ``builder``：每条带子记一串坐标。"""

    def __init__(self, lift):
        self.lift = float(lift)
        self.strands = []
        self._current = []

    def start_strand(self):
        self._current = []

    def add_loop(self, prev_loop, loop, twist, forward):
        edge = loop.edge
        point = (edge.verts[0].co + edge.verts[1].co) * 0.5
        normal = loop.calc_normal() + prev_loop.calc_normal()
        length = normal.length
        if length > 1e-9:
            normal = normal / length
        amount = -get_offset(self.lift, -self.lift, twist, forward)
        point = point + normal * amount
        self._current.append((point.x, point.y, point.z))

    def end_strand(self):
        self.strands.append(self._current)


def mean_edge_length(bm):
    """平均边长：抬升与管半径都按它缩放，换网格密度不用重调参数。"""
    total = 0.0
    count = 0
    for edge in bm.edges:
        total += edge.calc_length()
        count += 1
    return total / count if count else 1.0


# --------------------------------------------------------------------- 指纹
def _fingerprint(counts, coordinates):
    """数量 + 带编号的坐标加权和。

    坐标按顶点序号加权，避免「一个顶点往东、另一个往西」互相抵消。两个指纹入口都
    调这里（同一套 numpy 运算 → 同一个网格给出一模一样的值），别在别处另算一份：
    浮点求和顺序不同，末位就会差一个数，比对时全成了「变了」。
    """
    count = counts[0]
    if count == 0 or len(coordinates) != count * 3:
        return (counts[0], counts[1], counts[2], 0.0)
    weight = np.arange(1.0, count + 1.0, dtype=np.float64)
    projection = (
        coordinates[0::3] + 3.0 * coordinates[1::3] + 7.0 * coordinates[2::3]
    ).astype(np.float64)
    total = float(np.dot(weight, projection))
    return (counts[0], counts[1], counts[2], round(total, 5))


def signature(bm):
    """``BMesh`` 的指纹：编辑模式里点一下鼠标也会报网格更新，指纹没变就不重建。

    BMesh 没有 ``foreach_get``，逐点攒坐标（比 :func:`mesh_signature` 慢，只在
    手头已经有 BMesh 时顺手算用）。
    """
    coordinates = np.fromiter(
        (value for vert in bm.verts for value in vert.co),
        dtype=np.float32,
        count=len(bm.verts) * 3,
    )
    return _fingerprint((len(bm.verts), len(bm.edges), len(bm.faces)), coordinates)


def mesh_signature(mesh):
    """网格数据块的指纹（快路径）：实时更新每帧都要问「源网格变了没」。

    ``bmesh`` 复制一遍太贵（16k 面约 30 ms），这里直接从 ``Mesh.vertices`` 拿坐标。
    """
    count = len(mesh.vertices)
    coordinates = np.empty(count * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", coordinates)
    return _fingerprint(
        (len(mesh.vertices), len(mesh.edges), len(mesh.polygons)), coordinates
    )


# --------------------------------------------------------------------- 编结
def build(bm, lift):
    """把网格编成凯尔特结。

    :param bm: ``BMesh``（调用方不必先 ``normal_update``，这里会做）
    :param lift: 上 / 下的抬升量（世界单位，沿逐角法线）；``0`` 则整副结贴在表面上
    :return: :class:`Knot` —— 每根带子是一串点，各是某条边的中点沿法线抬起 ``±lift`` 的结果
    """
    started = time.perf_counter()
    bm.normal_update()
    points = _PathPoints(lift)
    broken = visit_strands(bm, celtic_twists(bm), points)

    paths = []
    counts = []
    for strand in points.strands:
        count = len(strand)
        if count < 2:
            continue  # 单点带子连不成曲线（参考实现会留一条看不见的退化曲线）
        counts.append(count)
        # 带子走回自己走过的方向才收尾 → 一律闭合；只有两个点的「往返」闭合成环就是
        # 同一段路来回走两遍，留一段敞着
        paths.append((strand, count > 2))

    counts.sort()
    stats = {
        "faces": len(bm.faces),
        "points": sum(counts),
        "strands": len(counts),
        "shortest": counts[0] if counts else 0,
        "median": counts[len(counts) // 2] if counts else 0,
        "longest": counts[-1] if counts else 0,
        "broken": broken,
        "ms": (time.perf_counter() - started) * 1000.0,
    }
    return Knot(paths, stats)