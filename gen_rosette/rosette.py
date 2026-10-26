"""星形玫瑰线（star rosette）的几何构造：核 → 镜像成花瓣 → 旋转 n 份 → 串成曲线。

移植自 Manim 版 ``.ref/rosette.py``：去掉 numpy（只用 ``math``），并把"合并成一串"
的结果**显式标出是否闭合** —— 编织需要知道哪一条是闭合带子（闭合带子的切口会跨接缝）。

三步（照搬论文式的"核 + 镜像 + 旋转"构造）：

1. :func:`cal_kernels` 逐阶算骨架：第 0 / 1 阶由三角恒等式直接给出，第 2 阶按
   ``star_type`` 分叉，更高阶把前一阶旋转 ``π/n`` 再与 x 轴求交；
2. :func:`get_petals_by_kernel` 把核沿 x 轴镜像成一片花瓣，再旋转 ``2π/n`` 复制 ``n`` 份；
3. :func:`sort_petals` 按跳步 ``level + 1`` 重排，:func:`merge_adjacent_petals` 把首尾
   相接的花瓣并成连续曲线。

为什么"跳步重排"：相邻花瓣（差 1 步）只是共用一条半径边界，端点并不重合；而差
``level + 1`` 步的两片花瓣，前一片的末端正好落在后一片的起点上——连起来的那条曲线
就是玫瑰线本身的走势（``{n / (level + 1)}`` 星形多边形的推广）。步长与 ``n`` 互质时
``n`` 片花瓣并成**一条**闭合曲线；不互质时并成 ``gcd(n, level + 1)`` 条。

两个已知的"数学上不存在"，都显式报错而不是产出 ``inf`` / ``nan``：

* ``n = 3`` 时 ``xk0 = cosθ + sinθ / tan2θ = 0``，核退化，故 ``MIN_PETALS = 4``；
* 某些 ``(n, star_type)`` 组合的某一阶会让旋转后的核线段与 x 轴平行，那一步没有交点
  （例如 ``n = 4`` 的 ``"O"`` 型第 2 阶、``n = 6`` 的 ``"PA"`` 第 4 阶）。可行阶数随 ``n``
  增大而增大，但不是无限——:func:`realizable_level` 就是给面板用的上限查询。
"""

from __future__ import annotations

import math
from typing import NamedTuple

__all__ = [
    "MIN_PETALS",
    "PARALLEL_ATOL",
    "STAR_TYPES",
    "STAR_TYPE_LABELS",
    "Curve",
    "build_curves",
    "cal_kernels",
    "get_petals_by_kernel",
    "max_level_cap",
    "merge_adjacent_petals",
    "realizable_level",
    "sort_petals",
]

Point = tuple[float, float]

#: 支持的花型标识，与论文 Figure 8 的十种核一一对应。
#:
#: 首位是核的两条边在往外走时的走势：``C`` 汇聚（向轴靠拢）、``P`` 平行（与对称轴
#: 平行）、``D`` 发散（背离轴）；次位是第二条边的收尾方式：``A`` 锐角、``O`` 钝角、
#: ``R`` 直角。``S``（straight）是特例：核只有一条直线段，没有分叉。
STAR_TYPES: tuple[str, ...] = (
    "S",
    "PA",
    "PO",
    "PR",
    "CA",
    "CR",
    "CO",
    "DA",
    "DO",
    "DR",
)

#: 面板下拉里的说明（存进文件的仍是上面这些 ASCII 标识）。
STAR_TYPE_LABELS = {
    "S": "S · 直边（单段直核）",
    "PA": "PA · 平行 · 锐角",
    "PO": "PO · 平行 · 钝角",
    "PR": "PR · 平行 · 直角",
    "CA": "CA · 汇聚 · 锐角",
    "CR": "CR · 汇聚 · 直角",
    "CO": "CO · 汇聚 · 钝角",
    "DA": "DA · 发散 · 锐角",
    "DO": "DO · 发散 · 钝角",
    "DR": "DR · 发散 · 直角",
}

#: 允许的最小花瓣数。``n = 3`` 时 ``xk0 = cosθ + sinθ / tan2θ = 0``，核退化成一条
#: 从原点出发的径向线段，只能得到几根交叉的辐射线。
MIN_PETALS = 4

#: 判定"旋转后的核线段与 x 轴平行"的容差。平行时该线段与 x 轴没有交点，高阶核无法构造。
PARALLEL_ATOL = 1e-9

#: 两条端点算不算重合的容差。坐标量级约 1，纯三角函数误差在 1e-15，留几个数量级余量。
MERGE_TOL = 1e-9

#: :func:`realizable_level` 默认向上试到第几阶就停（阶数越高，坐标越往原点外跑，
#: 实际可用的花样也早在第 6 阶之前就失去观感了）。
_LEVEL_CAP = 8


class Curve(NamedTuple):
    """一条串好的曲线：``closed`` 时 ``points`` 的末点**不**重复首点。"""

    points: list[Point]
    closed: bool

    @property
    def span(self) -> float:
        """包围盒边长（长的那个），给缩放用。"""
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return max(max(xs) - min(xs), max(ys) - min(ys))


# --- 小工具 ---------------------------------------------------------------


def _validate(n: int, level: int, star_type: str) -> None:
    if isinstance(n, bool) or not isinstance(n, int):
        raise TypeError(f"n must be an integer, got {type(n).__name__}")
    if n < MIN_PETALS:
        raise ValueError(f"n must be >= {MIN_PETALS}, got {n}")
    if isinstance(level, bool) or not isinstance(level, int):
        raise TypeError(f"level must be an integer, got {type(level).__name__}")
    if level < 0:
        raise ValueError(f"level must be >= 0, got {level}")
    if star_type not in STAR_TYPES:
        raise ValueError(
            f"unknown star_type {star_type!r}, expected one of {STAR_TYPES}"
        )


def _rotate(points: list[Point], angle: float) -> list[Point]:
    c, s = math.cos(angle), math.sin(angle)
    return [(x * c - y * s, x * s + y * c) for x, y in points]


def _mirror(points: list[Point]) -> list[Point]:
    """沿 x 轴镜像（y 取反）。"""
    return [(x, -y) for x, y in points]


# --- 核 -------------------------------------------------------------------


def cal_kernels(n: int, level: int = 2, star_type: str = "PA") -> list[list[Point]]:
    """计算 rosette 的基础骨架（kernels），返回列表的第 ``level`` 项即目标骨架。

    第 0 / 1 阶由三角恒等式直接给出，第 2 阶按 ``star_type`` 分叉，更高阶通过把前一阶
    旋转 ``π/n`` 再与 x 轴求交迭代得到。只构造到请求的阶，避免为不需要的阶算出退化坐标。

    异常:
        TypeError: ``n`` 或 ``level`` 不是整数。
        ValueError: ``n < MIN_PETALS``、``level < 0``、``star_type`` 不被支持，或该
            ``(n, star_type)`` 组合不存在第 ``level`` 阶骨架（旋转后的核线段与 x 轴平行）。
    """
    _validate(n, level, star_type)

    theta = math.pi / n
    x0, y0 = math.cos(theta), math.sin(theta)
    x1, y1 = math.cos(2.0 * theta), math.sin(2.0 * theta)

    # 0. 直边核（S）：整条核就是 (level+1)θ 处圆的切线弦。
    #
    #    切线弦的切点在单位圆上、方向垂直于半径，所以 ``n`` 份旋转复制后就是标准
    #    ``{n / (level + 1)}`` 星形多边形（顶点在单位圆上、边互相相切）。切线与 x 轴
    #    的交点是 ``1 / cos((level + 1)θ)``；余弦非正时切点已经越过 90°，交点跑到负半轴
    #    或不存在，这一阶不可实现。第 0 / 1 阶的核本来就是单段直线，直接沿用下面的构造。
    if star_type == "S" and level >= 2:
        phi = (level + 1) * theta
        cos_phi = math.cos(phi)
        if cos_phi <= PARALLEL_ATOL:
            raise ValueError(
                f"level {level} is not realizable for n={n}, star_type='S': "
                f"the tangent at {(level + 1)}θ = {math.degrees(phi):.1f}° no longer "
                "meets the positive x-axis; the largest usable level for this n is "
                f"{max(1, (n - 1) // 2 - 1)}"
            )
        kernel = [(math.cos(phi), math.sin(phi)), (1.0 / cos_phi, 0.0)]
        # 直边核没有"逐阶构造"的过程，但调用方一律取 ``[level]``，所以列表长度仍是
        # ``level + 1``，中间各阶用同一条核占位（每项各拷一份，免得共享同一个列表）。
        return [[(x, y) for x, y in kernel] for _ in range(level + 1)]

    # 1. 第 0 / 1 阶
    xk0 = x0 + y0 / math.tan(2.0 * theta)
    xk1 = x1 + y1 / math.tan(theta)
    kernels: list[list[Point]] = [
        [(x0, y0), (xk0, 0.0)],
        [(x1, y1), (xk1, 0.0)],
    ]

    # 2. 第 2 阶（封闭形式）
    if level >= 2:
        family, tip = star_type[0], star_type[1]
        if tip == "O" and math.isclose(
            math.cos(2.0 * theta), 0.0, abs_tol=PARALLEL_ATOL
        ):
            raise ValueError(
                f"level 2 is not realizable for n={n}, star_type={star_type!r}: "
                "the O tip line is parallel to the x-axis, so the construction has no "
                "intersection to use; use level 0 or 1 instead"
            )

        x2, y2 = math.cos(3.0 * theta), math.sin(3.0 * theta)

        # 三条分支只差一件事：中间那个点落在哪条高度线上（``side``）。
        # P 平行 = 和尖端同高（于是"尖端 → 中点"是水平线，平行于对称轴）；
        # C 汇聚 = 落在第 0 阶尖端的高度 y0（比尖端低，边朝轴收）；
        # D 发散 = 落在 y0 关于"过尖端的高度线"的镜像 2·y2 − y0 上（比尖端高，边朝外散）。
        # 三条都把中点放在"从尖端斜切 θ 的那条线"上，所以 x 的求法完全一致，
        # 只有 P 例外（它沿平行链一路量过去，见 xk21 / xk22）。
        if family == "P":
            xk21 = xk0 + y2 / math.tan(2.0 * theta)
            xk22 = xk21 + y2 / math.cos(theta)
            side = y2
        elif family == "C":
            side = y0
            xk22 = x2 + (y2 - side) / math.tan(theta)
        else:  # "D"
            side = 2.0 * y2 - y0
            xk22 = x2 + (y2 - side) / math.tan(theta)

        # 第二条边怎么收尾：A 外推 θ、R 垂直、O 内收 2θ
        if tip == "A":
            xk23 = xk22 + side * math.tan(theta)
        elif tip == "O":
            xk23 = xk22 - side * math.tan(2.0 * theta)
        else:  # "R"
            xk23 = xk22
        kernels.append([(x2, y2), (xk22, side), (xk23, 0.0)])

    # 3. 迭代更高阶：旋转前一阶，与 x 轴求交
    for order in range(3, level + 1):
        rotated = _rotate(kernels[-1], theta)
        rot_p1, rot_p2, rot_p3 = rotated[0], rotated[1], rotated[2]
        (rx2, ry2), (rx3, ry3) = rot_p2, rot_p3
        if math.isclose(ry2, ry3, rel_tol=PARALLEL_ATOL, abs_tol=PARALLEL_ATOL):
            raise ValueError(
                f"level {order} is not realizable for n={n}, star_type={star_type!r}: "
                "the rotated kernel segment is parallel to the x-axis, so the construction "
                f"has no intersection to use; the largest usable level for this "
                f"(n, star_type) is {order - 1}"
            )
        px = (rx2 - ry2 * (rx3 - rx2) / (ry3 - ry2), 0.0)
        kernels.append([rot_p1, rot_p2, px])

    return kernels


def realizable_level(n: int, star_type: str = "PA", cap: int = _LEVEL_CAP) -> int:
    """该 ``(n, star_type)`` 组合最高能算到第几阶（含）。

    从第 0 阶逐阶往上试，第一次抛 :class:`ValueError` 就停。面板用它给出上限提示、
    算子用它把越界的 ``level`` 收回来。
    """
    level = 0
    while level < cap:
        try:
            cal_kernels(n, level + 1, star_type)
        except ValueError:
            return level
        level += 1
    return level


def max_level_cap() -> int:
    """面板滑块的上限（与 :func:`realizable_level` 的 ``cap`` 一致）。"""
    return _LEVEL_CAP


# --- 花瓣 -----------------------------------------------------------------


def get_petals_by_kernel(kernel: list[Point], n: int) -> list[list[Point]]:
    """把核沿 x 轴镜像成一片花瓣，再旋转 ``n`` 份（逆时针）。

    镜像时去掉与 x 轴重合的中间点：``kernel[-2::-1]`` 就是"倒着走但不含末点"，
    末点落在 x 轴上，翻转后会与原点重合。
    """
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")

    flip_part = _mirror(kernel[-2::-1])
    petal = list(reversed(kernel + flip_part))
    return [_rotate(petal, i * 2.0 * math.pi / n) for i in range(n)]


def sort_petals(petals: list[list[Point]], level: int) -> list[list[Point]]:
    """按拓扑连接顺序重排花瓣：以 ``level + 1`` 为跳步分解成若干条循环轨道。

    ``gcd(n, step)`` 条轨道，每条长 ``n / gcd``；顺序展开后相邻两片首尾相接。
    """
    n = len(petals)
    if n == 0:
        return []
    step = level + 1
    num_cycles = math.gcd(n, step)
    cycle_len = n // num_cycles

    order: list[int] = []
    for start in range(num_cycles):
        current = start
        for _ in range(cycle_len):
            order.append(current)
            current = (current + step) % n
    return [petals[index] for index in order]


def merge_adjacent_petals(
    petals: list[list[Point]], tol: float = MERGE_TOL
) -> list[Curve]:
    """合并首尾相接的花瓣，返回若干条曲线（首尾重合的标成闭合，并去掉重复末点）。"""
    if not petals:
        return []

    merged: list[list[Point]] = []
    current = list(petals[0])

    for petal in petals[1:]:
        if _near(current[-1], petal[0], tol):
            current.extend(petal[1:])
        else:
            merged.append(current)
            current = list(petal)
    merged.append(current)

    curves: list[Curve] = []
    for points in merged:
        closed = len(points) > 2 and _near(points[0], points[-1], tol)
        if closed:
            points = points[:-1]
        if len(points) >= 2:
            curves.append(Curve(points, closed))
    return curves


def _near(a: Point, b: Point, tol: float) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


# --- 顶层 -----------------------------------------------------------------


def build_curves(n: int, level: int = 2, star_type: str = "PA") -> list[Curve]:
    """一次算完：核 → 花瓣 → 跳步重排 → 合并成曲线。

    异常与 :func:`cal_kernels` 相同。
    """
    kernel = cal_kernels(n, level, star_type)[level]
    petals = get_petals_by_kernel(kernel, n)
    return merge_adjacent_petals(sort_petals(petals, level))
