"""Port of ``index.js``：构造线晶格、瓷砖生长、签名与填充查询。

随机源是 :class:`zellij.rng.Mulberry32`，与 JS 侧的 ``mulberry32(seed)`` 逐位
一致 —— 浏览器里把 ``myrand`` 换成同参数的 mulberry32 后，两边得到的设计
（含颜色）完全相同。原实现的取样顺序被完整保留，包括 ``shimmer`` 分支里额外
的那次随机抽取。
"""

from __future__ import annotations

import colorsys
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
    "Features",
    "Tile",
    "Design",
    "Grid",
    "build_design",
    "create_lines",
    "get_signature",
    "match_filler",
    "rand_fill",
]

#: 面板上的 Density → ``NUM_LINES``。
DENSITY_TO_LINES = {6: 9, 10: 25, 20: 40}

FOCUS_VALUES = ("None", "Eight", "Sixteen")

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
    """原 ``initFeatures`` 决定的全局参数。"""

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
    #: 设计阶段结束时随机流的状态，供场景阶段接着用（原实现共用一条流）。
    rng_state: int = field(default=0)

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
    """p5 ``colorMode(HSB, hue_max)`` 语义的 HSB→RGB（0-255）。"""
    r, g, bl = colorsys.hsv_to_rgb((h % hue_max) / hue_max, s / hue_max, b / hue_max)
    return (round(r * 255), round(g * 255), round(bl * 255))


def _rgb_to_hsb255(colour: RGB) -> tuple[float, float, float]:
    h, s, v = colorsys.rgb_to_hsv(colour[0] / 255, colour[1] / 255, colour[2] / 255)
    return (h * 255.0, s * 255.0, v * 255.0)


def _constrain(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def rand_fill(
    colour: Design | list[RGB], cidx: int, shimmer: int, rng: Mulberry32
) -> RGB:
    """原 ``randFill``：``shimmer`` 开启时对 2 号以后的色板做亮度抖动。"""
    palette = colour.colours if isinstance(colour, Design) else colour
    col = palette[cidx]

    if shimmer >= 0 and cidx >= 2:
        h, s, v = _rgb_to_hsb255(col)
        v = _constrain(v + (rng.random() - 0.5) * 50.0, 0.0, 255.0)
        col = _hsb_to_rgb(h, s, v, 255.0)

    return col


# --- 构造线 ---------------------------------------------------------------


def _make_all_lines(n: int) -> list[Line]:
    """原 ``makeAllLines``：四组构造线，顺序必须保持原样（索引被后续逻辑使用）。"""
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

        # 原实现这里每次都推入同一个索引（``rem`` 重复），行为照搬。
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


def create_lines(
    num: int, line_density: int, focus: str, rng: Mulberry32
) -> tuple[list[Line], list[list[tuple[float, float]]]]:
    """原 ``createLines``：返回保留的构造线与需要合并的顶点组。"""
    all_lines = _make_all_lines(line_density)
    keep_lines: list[Line] = []
    groups: list[list[tuple[float, float]]] = []

    if focus == "Eight":
        _make_random_2x2(all_lines, keep_lines, groups, line_density, rng)
    elif focus == "Sixteen":
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
    """原 ``Grid``：记录每个交点被哪些构造线穿过。"""

    __slots__ = ("side", "users", "drawn", "group")

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
    """原 ``getAllTiles``：洪水填充出每个交点周围的多边形瓦片。"""
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
    """原 ``getSignature``：按角点类型编码瓷砖形状（L/I/V/C）。"""
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
    """原 ``drawTile`` 前半段：随机旋转起点，再逐格旋转直到命中图案库。

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
    """把图案簇坐标映射到设计空间的变换（对应原实现的 ``matchTwoSegs``）。"""
    fv = (filler_bounds[0], filler_bounds[1])
    fw = (filler_bounds[2], filler_bounds[3])
    return match_two_segs(fv, fw, lt0, lt1)


# --- 顶层 -----------------------------------------------------------------


def init_features(rng: Mulberry32) -> Features:
    """原 ``initFeatures``，随机抽取顺序与分支完全保留。"""
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
    """原 ``initColours``：5 色色板（HSB 0-100）。"""
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

    ``density`` / ``focus`` / ``shimmer`` 为 ``None`` 时沿用原草图的随机结果
    （即真正保持 `Auto` 行为）；给出时才覆盖。覆盖只影响这些参数本身，
    随机数序列仍然一致，所以同 seed 的配色不会因为覆盖而改变。
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

    # 原 ``buildDesign`` 末尾：把每个强制区域的瓦片合并成一块。
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

    return Design(
        seed=seed,
        features=features,
        colours=colours,
        lines=lines,
        groups=groups,
        tiles=tiles,
        boundary=boundary,
        rng_state=rng.state,
    )
