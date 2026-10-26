"""设计层：构造线晶格、瓷砖生长、签名匹配、簇合并、配色。

整张图的"纯数据"都从这里出来，不 import 任何渲染设施：

* 输入一个 32 位整数种子，随机流从头开始（:class:`~.rng.Mulberry32`），
  所以同种子必然出同一张图，颜色也在内；
* **抽取顺序是协议**：``init_features`` → ``init_colours`` → ``create_lines``
  → 瓷砖生长（含簇合并里挑种子那一步）。顺序一改，同种子的结果就变；
* 输出 :class:`Design`，由 ``scene`` 装配成 ``Shape``，与画在哪无关。
"""

from __future__ import annotations

import colorsys
import heapq
import math
from dataclasses import dataclass, field
from typing import NamedTuple

from . import fillers
from .geom import (
    TOL,
    dot,
    group_tiles,
    match_two_segs,
    point_add,
    point_dist,
    point_scale,
    point_sub,
)
from .rng import Mulberry32

__all__ = [
    "DENSITY_TO_LINES",
    "FOCUS_VALUES",
    "CLUSTER_FOCUS",
    "Features",
    "Tile",
    "Design",
    "Grid",
    "build_design",
    "create_lines",
    "get_signature",
    "match_filler",
    "rand_fill",
    "merge_cluster",
]

#: 密度 → 保留的构造线条数（候选共 ``6n+4`` 条）。
#: 6 / 10 / 20 是三个常用档位，其余按约 30% 的保留率补齐。
DENSITY_TO_LINES = {4: 8, 6: 9, 8: 16, 10: 25, 12: 26, 16: 30, 20: 40, 24: 44}

FOCUS_VALUES = ("None", "Eight", "Sixteen", "Block3x3", "Block4x4")

#: 「合并相邻瓷砖」型的 focus → 合并多少块。
#: 这两个不走构造线的索引计划，而是在瓷砖生成完之后按邻接关系合并，
#: 所以任意计数都能用（``Eight`` / ``Sixteen`` 才是写死索引的那一对）。
#: 键名用 3x3 / 4x4 而不是块数：并出来的就是 3×3、4×4 的一块。
CLUSTER_FOCUS = {"Block3x3": 9, "Block4x4": 16}

#: 合并之后至少要剩下这么多块正常瓷砖，否则放弃合并（否则整张图会变成一块空心大块）。
MIN_REMAINING_TILES = 3

ORDERED_DIRS = (5, 2, 1, 0, 3, 6, 7, 8)

_R22 = math.sqrt(2.0) * 0.5

INT_DIR_VECS = (
    (-1, -1),
    (0, -1),
    (1, -1),
    (-1, 0),
    (0, 0),
    (1, 0),
    (-1, 1),
    (0, 1),
    (1, 1),
)

DIR_VECS = (
    (-_R22, -_R22),
    (0.0, -1.0),
    (_R22, -_R22),
    (-1.0, 0.0),
    (0.0, 0.0),
    (1.0, 0.0),
    (-_R22, _R22),
    (0.0, 1.0),
    (_R22, _R22),
)

RGB = tuple[int, int, int]


class Line(NamedTuple):
    pos: tuple[float, float]
    dir: tuple[float, float]


class Tile(NamedTuple):
    vertex: tuple[int, int]
    path: list[tuple[float, float]]


@dataclass
class Features:
    """一次设计的全局参数（由 :func:`init_features` 一次抽定）。"""

    line_density: int
    num_lines: int
    focus: str
    shimmer: int
    grid_side: int


@dataclass
class Design:
    """一次完整的设计结果（纯数据，与任何渲染后端无关）。"""

    seed: int
    features: Features
    colours: list[RGB]
    lines: list[Line]
    groups: list[list[tuple[float, float]]]
    tiles: list[Tile]
    boundary: list[tuple[float, float]]
    #: 设计阶段结束时随机流的状态：场景阶段从这里重新开始，全流程只有一条流。
    rng_state: int = field(default=0)
    #: 焦点区域因为"剩下的正常瓷砖太少"而被跳过（``Block3x3`` / ``Block4x4``）。
    focus_skipped: bool = False

    @property
    def grid_side(self) -> int:
        return self.features.grid_side

    @property
    def shimmer(self) -> int:
        return self.features.shimmer

    def rng(self) -> Mulberry32:
        """场景阶段用的随机源（每次调用都从设计结束时的状态重新开始）。"""
        return Mulberry32.from_state(self.rng_state)


# --- 颜色 -----------------------------------------------------------------


def _hsb_to_rgb(h: float, s: float, b: float, hue_max: float = 100.0) -> RGB:
    """HSB→RGB（分量 0-255）；三个分量都按 ``hue_max`` 归一化。"""
    r, g, bl = colorsys.hsv_to_rgb((h % hue_max) / hue_max, s / hue_max, b / hue_max)
    return (round(r * 255), round(g * 255), round(bl * 255))


def _rgb_to_hsb255(colour: RGB) -> tuple[float, float, float]:
    h, s, v = colorsys.rgb_to_hsv(colour[0] / 255, colour[1] / 255, colour[2] / 255)
    return (h * 255.0, s * 255.0, v * 255.0)


def _constrain(value: float, low: float, high: float) -> float:
    return low if value < low else min(value, high)


def rand_fill(
    colour: Design | list[RGB], cidx: int, shimmer: int, rng: Mulberry32
) -> RGB:
    """按色板取填充色；``shimmer`` 开启时对 2 号以后的色做亮度抖动。"""
    palette = colour.colours if isinstance(colour, Design) else colour
    col = palette[cidx]

    if shimmer >= 0 and cidx >= 2:
        h, s, v = _rgb_to_hsb255(col)
        v = _constrain(v + (rng.random() - 0.5) * 50.0, 0.0, 255.0)
        col = _hsb_to_rgb(h, s, v, 255.0)

    return col


# --- 构造线 ---------------------------------------------------------------


def _make_all_lines(n: int) -> list[Line]:
    """生成四组候选构造线；顺序即索引计划，不能重排。"""
    lines: list[Line] = []

    # 水平线，从左边缘出发
    for i in range(n + 1):
        lines.append(Line((0, 2 * i), (1, 0)))

    # 垂直线，从上边缘出发
    for i in range(n + 1):
        lines.append(Line((2 * i, 0), (0, 1)))

    # 斜率 -1：n 条朝西北，n+1 条朝东南
    for i in range(n + 1):
        lines.append(Line((2 * n, 2 * i), (-1, -1)))
    for i in range(n):
        lines.append(Line((0, 2 * i + 2), (1, 1)))

    # 斜率 1：n+1 条朝东北，n 条朝西南
    for i in range(n + 1):
        lines.append(Line((0, 2 * i), (1, -1)))
    for i in range(n):
        lines.append(Line((2 * i + 2, 2 * n), (1, -1)))

    return lines


def _make_random_star(
    all_lines: list[Line],
    keep_lines: list[Line],
    groups: list[list[tuple[float, float]]],
    n: int,
    rng: Mulberry32,
) -> None:
    ax = int(rng.random() * (n - 4)) + 2
    ay = int(rng.random() * (n - 4)) + 2

    plan = (
        (4 * n + 7 + ax + ay, False),
        (4 * n + 6 + ax + ay, True),
        (4 * n + 5 + ax + ay, False),
        (4 * n + 4 + ax + ay, False),
        (4 * n + 3 + ax + ay, False),
        (4 * n + 2 + ax + ay, True),
        (4 * n + 1 + ax + ay, False),
        (3 * n + 5 + ax - ay, False),
        (3 * n + 4 + ax - ay, True),
        (3 * n + 3 + ax - ay, False),
        (3 * n + 2 + ax - ay, False),
        (3 * n + 1 + ax - ay, False),
        (3 * n + 0 + ax - ay, True),
        (3 * n - 1 + ax - ay, False),
        (n + 1 + ay + 2, True),
        (n + 1 + ay + 1, False),
        (n + 1 + ay, False),
        (n + 1 + ay - 1, True),
        (ax + 2, True),
        (ax + 1, False),
        (ax, False),
        (ax - 1, True),
    )

    for idx, keep in plan:
        if keep:
            keep_lines.append(all_lines[idx])
        all_lines.pop(idx)

    groups.append(
        [
            (2 * ay + 1, 2 * ax - 3),
            (2 * ay - 2, 2 * ax - 2),
            (2 * ay, 2 * ax - 2),
            (2 * ay + 2, 2 * ax - 2),
            (2 * ay + 4, 2 * ax - 2),
            (2 * ay - 2, 2 * ax),
            (2 * ay + 4, 2 * ax),
            (2 * ay - 3, 2 * ax + 1),
            (2 * ay + 5, 2 * ax + 1),
            (2 * ay - 2, 2 * ax + 2),
            (2 * ay + 4, 2 * ax + 2),
            (2 * ay - 2, 2 * ax + 4),
            (2 * ay, 2 * ax + 4),
            (2 * ay + 2, 2 * ax + 4),
            (2 * ay + 4, 2 * ax + 4),
            (2 * ay + 1, 2 * ax + 5),
        ]
    )


def _make_random_2x2(
    all_lines: list[Line],
    keep_lines: list[Line],
    groups: list[list[tuple[float, float]]],
    n: int,
    rng: Mulberry32,
) -> None:
    if rng.random() < 0.5:
        ax = int(rng.random() * n)
        ay = int(rng.random() * n)

        # 这里每次都推入同一个索引（``rem`` 里有重复项）：索引计划依赖这个重复量。
        rem: list[int] = []
        for _ in range(3):
            k = (2 * n + 2) + (n - 1) + ax - ay
            if 2 * n + 2 <= k < 4 * n + 3:
                rem.append(k)
        for i in range(3):
            rem.append(4 * n + 3 + i + ax + ay)

        rem.reverse()
        for i in rem:
            all_lines.pop(i)

        groups.append(
            [
                (2 * ay, 2 * ax),
                (2 * ay + 2, 2 * ax),
                (2 * ay, 2 * ax + 2),
                (2 * ay + 2, 2 * ax + 2),
            ]
        )

        for i in (n + 1 + ay + 1, n + 1 + ay, ax + 1, ax):
            keep_lines.append(all_lines[i])
            all_lines.pop(i)
    else:
        # 斜向：去掉 2 水平 1 垂直，或 1 水平 2 垂直
        a = int(rng.random() * n)
        b = int(rng.random() * (n - 1)) + 1

        if rng.random() < 0.5:
            all_lines.pop(n + 1 + a + 1)
            all_lines.pop(n + 1 + a)
            all_lines.pop(b)
            groups.append(
                [
                    (2 * a + 1, 2 * b - 1),
                    (2 * a + 1, 2 * b + 1),
                    (2 * a, 2 * b),
                    (2 * a + 2, 2 * b),
                ]
            )

            for i in (
                4 * n + a + b + 1,
                4 * n + a + b,
                3 * n - 1 + b - a,
                3 * n - 2 + b - a,
            ):
                keep_lines.append(all_lines[i])
                all_lines.pop(i)
        else:
            all_lines.pop(n + 1 + b)
            all_lines.pop(a + 1)
            all_lines.pop(a)
            groups.append(
                [
                    (2 * b, 2 * a),
                    (2 * b, 2 * a + 2),
                    (2 * b - 1, 2 * a + 1),
                    (2 * b + 1, 2 * a + 1),
                ]
            )

            for i in (
                4 * n + a + b + 1,
                4 * n + a + b,
                3 * n + a - b,
                3 * n - 1 + a - b,
            ):
                keep_lines.append(all_lines[i])
                all_lines.pop(i)


def _polygon_area(path) -> float:
    total = 0.0
    count = len(path)
    for index in range(count):
        x1, y1 = path[index]
        x2, y2 = path[(index + 1) % count]
        total += x1 * y2 - x2 * y1
    return abs(total) * 0.5


def _polygon_centroid(path) -> tuple[float, float]:
    count = len(path)
    return (sum(p[0] for p in path) / count, sum(p[1] for p in path) / count)


def _tile_neighbours(paths) -> dict[int, set[int]]:
    """按重合的边求瓷砖邻接表（端点量化后比较，与 ``scene._edge_key`` 同一套约定）。"""
    owners: dict[tuple, list[int]] = {}
    for index, path in enumerate(paths):
        count = len(path)
        for i in range(count):
            a = path[i]
            b = path[(i + 1) % count]
            key = tuple(
                sorted(
                    (
                        (round(a[0], 5), round(a[1], 5)),
                        (round(b[0], 5), round(b[1], 5)),
                    )
                )
            )
            owners.setdefault(key, []).append(index)

    neighbours: dict[int, set[int]] = {index: set() for index in range(len(paths))}
    for indices in owners.values():
        for i in indices:
            for j in indices:
                if i != j:
                    neighbours[i].add(j)
    return neighbours


def merge_cluster(tiles: list[Tile], count: int, rng: Mulberry32) -> bool:
    """把 ``count`` 块彼此相邻的瓷砖合并成一块（原地修改 ``tiles``）。

    ``Eight`` / ``Sixteen`` 走的是写死索引的构造线计划，只对那两种尺寸成立；
    这里改成"先出瓷砖、再按邻接关系把最近的 N 块并起来"，任意计数都能用：
    以面积中位数以上的瓷砖作种子，从它出发按质心距离优先地 BFS 扩展，
    收集满 ``count`` 块后交给 ``group_tiles`` 合并轮廓。

    合并出来的那块形状不规则、匹配不到填充图案，所以它永远是空心的。为了让结果
    不至于变成"一大块空心 + 零星小块"，合并后必须还剩 ``MIN_REMAINING_TILES`` 块
    正常瓷砖，否则整张图放弃合并（返回 ``False``，按普通设计出图）。
    """
    if len(tiles) < 2 or count < 2:
        return False
    if len(tiles) - count < MIN_REMAINING_TILES:
        return False
    count = min(count, len(tiles) - MIN_REMAINING_TILES)
    if count < 2:
        return False

    paths = [tile.path for tile in tiles]
    areas = sorted(_polygon_area(path) for path in paths)
    median = areas[len(areas) // 2]
    candidates = [i for i, path in enumerate(paths) if _polygon_area(path) >= median]
    seed = candidates[int(rng.random() * len(candidates)) % len(candidates)]
    centre = _polygon_centroid(paths[seed])

    neighbours = _tile_neighbours(paths)
    chosen: list[int] = []
    seen = {seed}
    queue = [(0.0, seed)]
    while queue and len(chosen) < count:
        _, current = heapq.heappop(queue)
        chosen.append(current)
        for other in neighbours.get(current, ()):
            if other in seen:
                continue
            seen.add(other)
            cx, cy = _polygon_centroid(paths[other])
            heapq.heappush(queue, (math.hypot(cx - centre[0], cy - centre[1]), other))

    if len(chosen) < 2:
        return False

    path = group_tiles([paths[index] for index in chosen])
    if not path:
        return False

    for index in sorted(chosen, reverse=True):
        tiles.pop(index)
    tiles.append(Tile((0, 0), path))
    return True


def create_lines(
    num: int, line_density: int, focus: str, rng: Mulberry32
) -> tuple[list[Line], list[list[tuple[float, float]]]]:
    """挑选保留的构造线，并算出需要合并的顶点组。"""
    all_lines = _make_all_lines(line_density)
    keep_lines: list[Line] = []
    groups: list[list[tuple[float, float]]] = []

    if focus == "Eight":
        _make_random_2x2(all_lines, keep_lines, groups, line_density, rng)
    elif focus == "Sixteen" and line_density >= 5:
        # 星形计划的下标范围按 n ≥ 5 推出来的，density 4 时点会越界，直接跳过。
        _make_random_star(all_lines, keep_lines, groups, line_density, rng)

    # 已经保留下来的线不算进随机预算
    num -= len(keep_lines)

    while all_lines and num > 0:
        ri = int(rng.random() * len(all_lines))
        keep_lines.append(all_lines[ri])
        all_lines.pop(ri)
        num -= 1

    return keep_lines, groups


# --- 交点网格 -------------------------------------------------------------


class Grid:
    """交点网格：记录每个交点被哪些构造线穿过。"""

    __slots__ = ("drawn", "group", "side", "users")

    def __init__(self, side: int):
        self.side = side
        count = side * side
        self.users: list[list[Line]] = [[] for _ in range(count)]
        self.drawn: list[bool] = [False] * count
        self.group: list[int] = [-1] * count

    def get_users(self, pt) -> list[Line]:
        return self.users[pt[1] * self.side + pt[0]]

    def num_users(self, pt) -> int:
        return len(self.users[pt[1] * self.side + pt[0]])

    def add_user(self, pt, line: Line) -> None:
        self.users[pt[1] * self.side + pt[0]].append(line)

    def is_drawn(self, pt) -> bool:
        return self.drawn[pt[1] * self.side + pt[0]]

    def set_drawn(self, pt) -> None:
        self.drawn[pt[1] * self.side + pt[0]] = True

    def get_group(self, pt) -> int:
        return self.group[pt[1] * self.side + pt[0]]

    def set_group(self, pt, group: int) -> None:
        self.group[pt[1] * self.side + pt[0]] = group

    def mark_ray(self, line: Line, pos, direction) -> None:
        side = self.side
        x, y = pos
        dx, dy = direction
        while 0 <= x < side and 0 <= y < side:
            self.users[int(y) * side + int(x)].append(line)
            x += dx
            y += dy

    def mark_line(self, line: Line) -> None:
        p, d = line.pos, line.dir
        self.mark_ray(line, p, d)
        self.mark_ray(line, point_sub(p, d), point_scale(d, -1))

    def mark_lines(self, lines) -> None:
        for line in lines:
            self.mark_line(line)

    def find_neighbour(self, pt, direction):
        """沿 ``direction`` 找下一个交点（被多条线穿过）。"""
        side = self.side
        x = pt[0] + direction[0]
        y = pt[1] + direction[1]
        while 0 <= x < side and 0 <= y < side:
            idx = int(y) * side + int(x)
            if len(self.users[idx]) > 1:
                return (int(x), int(y))
            x += direction[0]
            y += direction[1]
        return None


def get_all_tiles(grid: Grid) -> tuple[list[Tile], list[tuple[float, float]]]:
    """洪水填充出每个交点周围的多边形瓦片，顺带收下边界边。"""
    side = grid.side
    tiles: list[Tile] = []
    boundary: list[tuple[float, float]] = []

    start = None
    for y in range(side):
        base = y * side
        for x in range(side):
            if len(grid.users[base + x]) >= 2:
                start = (x, y)
                break
        if start is not None:
            break

    if start is None:
        return tiles, boundary

    stack: list[tuple] = [(start, None, None)]

    while stack:
        pt, align_p, align_q = stack.pop()
        if grid.is_drawn(pt):
            continue

        used_dirs = set()
        for line in grid.get_users(pt):
            d = line.dir
            used_dirs.add((int(d[1]) + 1) * 3 + (int(d[0]) + 1))
            used_dirs.add((-int(d[1]) + 1) * 3 + (-int(d[0]) + 1))

        grid.set_drawn(pt)

        # 1) 交点周围的多边形（单位边长，从原点起画）
        pts: list[tuple[float, float]] = []
        last = (0.0, 0.0)
        for d in ORDERED_DIRS:
            if d in used_dirs:
                ddir = DIR_VECS[d]
                ppdir = (-ddir[1], ddir[0])
                pts.append(last)
                last = point_add(last, ppdir)

        # 2) 与上一个瓦片对齐：找到与 delt 同向的边，平移过去
        if align_p is not None:
            delt = point_sub(align_p, align_q)
            for idx in range(len(pts)):
                v = point_sub(pts[(idx + 1) % len(pts)], pts[idx])
                if point_dist(v, delt) < 1e-5:
                    translation = point_sub(align_q, pts[idx])
                    break
            else:
                translation = (0.0, 0.0)
            pts = [point_add(p, translation) for p in pts]

        tiles.append(Tile(pt, pts))

        # 3) 走到邻居，顺手记录边界点
        vidx = 0
        for d in ORDERED_DIRS:
            if d in used_dirs:
                neigh = grid.find_neighbour(pt, INT_DIR_VECS[d])
                if neigh is not None:
                    if not grid.is_drawn(neigh):
                        stack.append((neigh, pts[vidx], pts[(vidx + 1) % len(pts)]))
                else:
                    boundary.append(pts[vidx])
                    boundary.append(pts[(vidx + 1) % len(pts)])
                vidx += 1

    return tiles, boundary


# --- 签名与填充 -----------------------------------------------------------


def get_signature(path) -> str:
    """按角点类型编码瓷砖形状（L / I / V / C）。"""
    n = len(path)
    out = []
    for i in range(n):
        a = path[(i + n - 1) % n]
        b = path[i]
        c = path[(i + 1) % n]
        s = dot(point_sub(a, b), point_sub(c, b))
        if abs(s) < TOL:
            out.append("L")
        elif abs(1 + s) < TOL:
            out.append("I")
        elif s > 0:
            out.append("V")
        else:
            out.append("C")
    return "".join(out)


def match_filler(path, rng: Mulberry32):
    """随机换个旋转起点，再逐格旋转，直到签名在图案库里命中。

    返回 ``(旋转后的点列, 签名, 图案簇列表)``；未命中时返回 ``(None, 签名, [])``。
    """
    lt = list(path)
    rot = int(rng.random() * len(lt))
    lt = lt[rot:] + lt[:rot]
    sig = get_signature(lt)

    for _ in range(len(sig)):
        if fillers.has(sig):
            return lt, sig, fillers.clusters(sig)
        lt = lt[1:] + lt[:1]
        sig = sig[1:] + sig[:1]

    return None, sig, []


def filler_transform(filler_bounds, lt0, lt1):
    """把图案簇的包围盒对角线映到瓷砖的一条边上。"""
    fv = (filler_bounds[0], filler_bounds[1])
    fw = (filler_bounds[2], filler_bounds[3])
    return match_two_segs(fv, fw, lt0, lt1)


# --- 顶层 -----------------------------------------------------------------


def init_features(rng: Mulberry32) -> Features:
    """抽全局特征：密度、焦点、抖动开关。抽取顺序就是随机流顺序，不能改。"""
    v = rng.random()
    if v < 0.7:
        line_density, num_lines = 10, 25
    elif v < 0.9:
        line_density, num_lines = 6, 9
    else:
        line_density, num_lines = 20, 40

    v = rng.random()
    if v < 0.75:
        focus = "None"
    elif v < 0.95:
        focus = "Eight"
    else:
        focus = "Sixteen"

    v = rng.random()
    if v < 0.75:
        shimmer = -1
    else:
        shimmer = int(3 * rng.random()) + 2

    return Features(line_density, num_lines, focus, shimmer, 2 * line_density + 1)


def init_colours(rng: Mulberry32) -> list[RGB]:
    """生成 5 色色板（色相与饱和 0..100，明度按百分比）。"""
    return [
        _hsb_to_rgb(rng.random() * 100, rng.random() * 100, rng.random() * 30),
        _hsb_to_rgb(rng.random() * 100, rng.random() * 10, 100 - rng.random() * 5),
        _hsb_to_rgb(rng.random() * 100, rng.random() * 50 + 25, rng.random() * 50 + 25),
        _hsb_to_rgb(rng.random() * 100, rng.random() * 50 + 25, rng.random() * 50 + 25),
        _hsb_to_rgb(rng.random() * 100, rng.random() * 50 + 25, rng.random() * 50 + 25),
    ]


def build_design(
    seed: int,
    *,
    density: int | None = None,
    focus: str | None = None,
    shimmer: bool | None = None,
) -> Design:
    """按 seed 生成一份设计。

    ``density`` / ``focus`` / ``shimmer`` 为 ``None`` 时保留随机抽取的结果；
    给出时才覆盖。覆盖只改这几个参数本身，不动随机流，所以同 seed 的配色不会
    因为覆盖而改变。
    """
    rng = Mulberry32(seed)
    features = init_features(rng)

    if density is not None:
        features.line_density = density
        features.num_lines = DENSITY_TO_LINES[density]
        features.grid_side = 2 * density + 1
    if focus is not None:
        features.focus = focus
    if shimmer is not None:
        features.shimmer = 3 if shimmer else -1

    colours = init_colours(rng)
    lines, groups = create_lines(
        features.num_lines, features.line_density, features.focus, rng
    )

    grid = Grid(features.grid_side)
    grid.mark_lines(lines)

    tiles, boundary = get_all_tiles(grid)

    # 写死索引的焦点：把这些交点组所在的瓦片合并成一块。
    for idx, group in enumerate(groups):
        for pt in group:
            grid.set_group(pt, idx)

        members: list[list[tuple[float, float]]] = []
        for t_idx in range(len(tiles) - 1, -1, -1):
            if grid.get_group(tiles[t_idx].vertex) == idx:
                members.append(tiles[t_idx].path)
                tiles.pop(t_idx)

        if not members:
            continue

        path = group_tiles(members)
        if path:
            tiles.append(Tile((0, 0), path))

    # 「合并相邻瓷砖」型 focus：在瓷砖生成完之后按邻接关系并块。
    cluster = CLUSTER_FOCUS.get(features.focus)
    focus_skipped = False
    if cluster:
        focus_skipped = not merge_cluster(tiles, cluster, rng)

    return Design(
        seed=seed,
        features=features,
        colours=colours,
        lines=lines,
        groups=groups,
        tiles=tiles,
        boundary=boundary,
        rng_state=rng.state,
        focus_skipped=focus_skipped,
    )
