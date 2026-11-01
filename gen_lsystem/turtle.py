"""L-system 的 turtle 解释：模块串 → 折线路径（纯 Python，**不 import bpy / mathutils**）。

一根 **路径**（:class:`Path`）就是一条 Grease Pencil 笔划。分支（``[``）各自成一条，从
**分叉点**起步；父分支穿过 ``]`` 之后接着原来那条路径往下走 —— 所以它在 GP 里仍然是
**一整笔**（不分叉、不在分叉处断开），这与参考实现的「同一个 mesh 里续边」等价。

符号表（``G`` 与 ``F`` 同义，ABOP 里常拿它当「F 的孪生兄弟」来画两种颜色/两种线）::

    F / G     前进并画线（可带长度：F(1.5)）
    f         只前进，不画（当前路径在此断开）
    + / -     绕局部 Y 轴左 / 右转（可带角度：+(45)，**单位是度**）
    ^ / &     绕局部 X 轴抬头 / 低头
    \\ / /     绕局部 Z 轴翻滚（左 / 右）
    |         掉头（绕 Y 转 180°）
    $         扶正：把局部「左」轴摆到水平（ABOP 的 rotate upright）
    [ / ]     压栈 / 出栈（分支，深度 +1）
    !         变细（× 衰减，可带倍率：!(0.7)）
    #         变粗（× 增粗，可带倍率）
    ¤         直接设半径（¤(0.02)）
    s         整体缩放（步长与半径一起乘）
    £         把转角换成一个随机角（按种子定）
    %         剪掉本分支的剩余部分（之后的 F 只前进不画线，直到 ``]``）
    { } : ; ~ p m w   忽略 —— 参考实现里这些是网格 / 对象 / 材质相关，GP 用不上

**朝向**沿用参考实现：前进方向是**局部 +Z**，所以 ``+`` / ``-`` 绕 Y、``^`` / ``&`` 绕 X、
``\\`` / ``/`` 绕 Z。坐标先在这个「turtle 坐标系」里算（+Z 朝上），最后按维度落位：

* ``3D``：原样（+Z 朝上，与 Blender 一致）；
* ``2D``：整张图绕 X 轴转 -90°，变成躺平在 **XY 平面**上的平面图案（+Y 朝上），和仓库里
  其他二维插件（``gen_voronoi`` / ``gen_zellij``）的画布朝向一致。

向重力（tropism）在 **turtle 坐标系**里固定取 ``(0, 0, -1)``：主茎朝上时重力与朝向共线、
不产生弯折，侧枝则被往回压 —— 正是 ABOP 图 2.8 那种「越往外越垂」的手感。
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .lsystem import (
    DEFAULT_MAX_SYMBOLS,
    LSystemError,
    Rule,
    Token,
    iterate,
)

__all__ = [
    "DEFAULT_ANGLE",
    "Path",
    "Skeleton",
    "build",
    "interpret",
    "progress_of",
]

#: 默认转角（弧度，= 25°）。
DEFAULT_ANGLE = math.radians(25.0)

_FORWARD = (0.0, 0.0, 1.0)
_GRAVITY = (0.0, 0.0, -1.0)
_IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
_TWO_PI = 2.0 * math.pi

#: 参考实现里有、但 GP 后端用不上的符号（网格 / 对象 / 材质 / 拷贝）。命中就记进
#: ``Skeleton.ignored``，面板上摊给人看；其余不画线的字母当**占位模块**（``X`` / ``A``……），
#: 那是 L-system 的正常写法，不报警。
_UNSUPPORTED = {"p", "m", "w", "~", ":", ";", "{", "}", "@"}

#: 转角符号 → (局部轴, 正负)：``+``/``-`` 绕 Y、``^``/``&`` 绕 X、``\``/``/`` 绕 Z。
_TURNS = {
    "+": (1, 1.0),
    "-": (1, -1.0),
    "^": (0, 1.0),
    "&": (0, -1.0),
    "\\": (2, -1.0),
    "/": (2, 1.0),
}


@dataclass(frozen=True, slots=True)
class Path:
    """一条笔划：点列 + 逐点半径 + 分支深度 + 逐点进度。

    ``radius`` 是**几何算出来的粗细**（``!`` / ``#`` / ``¤`` 的结果），不含面板上的
    「宽度倍数」—— 那一档是 GP 属性，改它不该重算这条路径。

    ``progress`` 是**沿这一笔的进度**（0 起、1 止，按累计弧长归一化），每笔各自从 0 走到 1。
    单笔图案（龙形 / 科赫 / 希尔伯特）拿它就是从起点扫到终点；分叉树拿它是「每根枝条各自
    朝梢部推进」。口径与几何节点里的 ``Spline Parameter.Factor`` 一致（都是弧长比）。
    """

    points: tuple[tuple[float, float, float], ...]
    radii: tuple[float, ...]
    depth: int = 0
    progress: tuple[float, ...] = ()


@dataclass(frozen=True, slots=True)
class Skeleton:
    """一次解释的全部结果。"""

    paths: tuple[Path, ...]
    #: 展开后剩多少个模块（调试用：它和点数差得远就说明规则里大多是控制符）。
    symbols: int = 0
    #: 被忽略的模块（去重后），面板上摊给人看。
    ignored: tuple[str, ...] = ()
    #: 最大分支深度。
    depth: int = 0

    @property
    def path_count(self) -> int:
        """笔划数。"""
        return len(self.paths)

    @property
    def point_count(self) -> int:
        """总点数。"""
        return sum(len(path.points) for path in self.paths)

    @property
    def radius_range(self) -> tuple[float, float]:
        """``(最细, 最粗)``；没有点时返回 ``(0.0, 0.0)``。"""
        radii = [radius for path in self.paths for radius in path.radii]
        if not radii:
            return 0.0, 0.0
        return min(radii), max(radii)

    def bounds(self) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
        """``(最小角, 最大角)``；没有点时返回 ``None``。"""
        low = [math.inf, math.inf, math.inf]
        high = [-math.inf, -math.inf, -math.inf]
        empty = True
        for path in self.paths:
            for point in path.points:
                empty = False
                for axis in range(3):
                    low[axis] = min(low[axis], point[axis])
                    high[axis] = max(high[axis], point[axis])
        if empty:
            return None
        return (low[0], low[1], low[2]), (high[0], high[1], high[2])


# --- 3×3 矩阵（不引 mathutils，算法层要能脱离 Blender 跑） --------------------


def _multiply(a, b):
    return (
        (
            a[0][0] * b[0][0] + a[0][1] * b[1][0] + a[0][2] * b[2][0],
            a[0][0] * b[0][1] + a[0][1] * b[1][1] + a[0][2] * b[2][1],
            a[0][0] * b[0][2] + a[0][1] * b[1][2] + a[0][2] * b[2][2],
        ),
        (
            a[1][0] * b[0][0] + a[1][1] * b[1][0] + a[1][2] * b[2][0],
            a[1][0] * b[0][1] + a[1][1] * b[1][1] + a[1][2] * b[2][1],
            a[1][0] * b[0][2] + a[1][1] * b[1][2] + a[1][2] * b[2][2],
        ),
        (
            a[2][0] * b[0][0] + a[2][1] * b[1][0] + a[2][2] * b[2][0],
            a[2][0] * b[0][1] + a[2][1] * b[1][1] + a[2][2] * b[2][1],
            a[2][0] * b[0][2] + a[2][1] * b[1][2] + a[2][2] * b[2][2],
        ),
    )


def _apply(matrix, vector):
    return (
        matrix[0][0] * vector[0] + matrix[0][1] * vector[1] + matrix[0][2] * vector[2],
        matrix[1][0] * vector[0] + matrix[1][1] * vector[1] + matrix[1][2] * vector[2],
        matrix[2][0] * vector[0] + matrix[2][1] * vector[1] + matrix[2][2] * vector[2],
    )


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _length(vector) -> float:
    return math.sqrt(vector[0] * vector[0] + vector[1] * vector[1] + vector[2] * vector[2])


def _normalize(vector):
    size = _length(vector)
    if size < 1e-12:
        return (0.0, 0.0, 0.0)
    return (vector[0] / size, vector[1] / size, vector[2] / size)


def _rotation(axis: int, angle: float):
    """绕局部 X / Y / Z 的旋转矩阵（右乘：``rot = rot @ R``）。"""
    cosine = math.cos(angle)
    sine = math.sin(angle)
    if axis == 0:
        return ((1.0, 0.0, 0.0), (0.0, cosine, -sine), (0.0, sine, cosine))
    if axis == 1:
        return ((cosine, 0.0, sine), (0.0, 1.0, 0.0), (-sine, 0.0, cosine))
    return ((cosine, -sine, 0.0), (sine, cosine, 0.0), (0.0, 0.0, 1.0))


def _rotation_about(axis, angle: float):
    """绕任意单位轴旋转（罗德里格斯公式）：向重力要用它。"""
    x, y, z = axis
    cosine = math.cos(angle)
    sine = math.sin(angle)
    rest = 1.0 - cosine
    return (
        (
            cosine + x * x * rest,
            x * y * rest - z * sine,
            x * z * rest + y * sine,
        ),
        (
            y * x * rest + z * sine,
            cosine + y * y * rest,
            y * z * rest - x * sine,
        ),
        (
            z * x * rest - y * sine,
            z * y * rest + x * sine,
            cosine + z * z * rest,
        ),
    )


def _bend(rot, force: float, gravity):
    """向重力：把朝向朝 ``gravity`` 扳一点，转角 = ``force × |heading × gravity|``。

    与参考实现同一条式子，区别只是**绕世界轴、左乘** —— 参考实现把世界轴当成本地轴用，
    侧枝一多就会歪；这里在几何上是「真正的重力」。
    """
    heading = _apply(rot, _FORWARD)
    axis = _cross(heading, gravity)
    amount = _length(axis) * float(force)
    if amount < 1e-9:
        return rot
    return _multiply(_rotation_about(_normalize(axis), amount), rot)


def _upright(rot):
    """扶正：重排局部坐标系，让「左」轴水平（朝向不变）。朝向本来就竖直时不动。"""
    heading = _apply(rot, _FORWARD)
    horizontal = _cross(heading, (0.0, 0.0, 1.0))
    if _length(horizontal) < 1e-9:
        return rot
    left = _normalize(horizontal)
    # 列向量：ex → 左（-left）、ey → 上、ez → 朝向
    new_x = (-left[0], -left[1], -left[2])
    new_y = _cross(heading, new_x)
    return (
        (new_x[0], new_y[0], heading[0]),
        (new_x[1], new_y[1], heading[1]),
        (new_x[2], new_y[2], heading[2]),
    )


# --- 解释 -------------------------------------------------------------------


def progress_of(points: Sequence[tuple[float, float, float]]) -> tuple[float, ...]:
    """一条路径的逐点进度（0 起、1 止，按**累计弧长**归一化）。

    每笔各自从 0 走到 1 —— 单笔图案（龙形 / 科赫 / 希尔伯特）就是「从起点扫到终点」，
    分叉树则是「每根枝条各自朝梢部推进」。口径与几何节点的 ``Spline Parameter.Factor``
    一致（都是弧长比，不是点数比）。

    退路：所有点重合（总长为 0）时按点数均分；不足两点给 0。
    """
    count = len(points)
    if count < 2:
        return (0.0,) * max(count, 0)

    walked = [0.0]
    total = 0.0
    for index in range(1, count):
        previous = points[index - 1]
        current = points[index]
        total += math.sqrt(
            (current[0] - previous[0]) ** 2
            + (current[1] - previous[1]) ** 2
            + (current[2] - previous[2]) ** 2
        )
        walked.append(total)
    if total <= 0.0:
        return tuple(index / (count - 1) for index in range(count))
    return tuple(distance / total for distance in walked)


def _number(params, symbol: str) -> float:
    """取第一个参数当数字；取不到就报一句带符号名的话。"""
    try:
        return float(params[0])
    except (TypeError, ValueError):
        raise LSystemError(f"{symbol}(...)：参数 {params[0]!r} 不是数字") from None


def interpret(
    tokens: Iterable[Token],
    *,
    angle: float = DEFAULT_ANGLE,
    length: float = 1.0,
    radius: float = 0.01,
    radius_decay: float = 0.8,
    radius_fat: float = 1.2,
    tropism: float = 0.0,
    scale: float = 1.0,
    seed: int = 0,
    dimension: str = "2D",
) -> Skeleton:
    """跑一遍 turtle，得到路径列表。

    :param tokens: 展开后的模块（:func:`gen_lsystem.lsystem.iterate` 的结果）。
    :param angle: 默认转角（弧度；符号处写了 ``+(45)`` 就用括号里的，单位度）。
    :param length: 默认步长（``F`` 不带参数时用它）。
    :param radius: 基础粗细（``F`` 落点时的半径）。
    :param radius_decay: ``!`` 的默认倍率（变小）。
    :param radius_fat: ``#`` 的默认倍率（变大）。
    :param tropism: 向重力强度，0 = 关。
    :param scale: 整体缩放（步长与半径一起乘）。
    :param seed: 随机种子（``£`` / ``rand()``）。
    :param dimension: ``"2D"`` 躺平到 XY 平面，``"3D"`` 原样（+Z 朝上）。
    :raises LSystemError: 参数不是数字 / 分支栈不配对到无法恢复。
    """
    tokens = list(tokens)
    rng = random.Random(int(seed))

    position = (0.0, 0.0, 0.0)
    rotation = _IDENTITY
    thickness = max(0.0, float(radius))
    step = float(length)
    factor = float(scale)
    turn = float(angle)
    stage = 0
    top_stage = 0
    muted = False
    current: list[tuple[tuple[float, float, float], float]] = []
    paths: list[Path] = []
    stack: list[tuple] = []
    ignored: set[str] = set()

    def flush() -> None:
        """收掉当前路径（不足两个点的不算一笔）。"""
        nonlocal current
        if len(current) >= 2:
            points = tuple(point for point, _radius in current)
            paths.append(
                Path(
                    points=points,
                    radii=tuple(radius for _point, radius in current),
                    depth=stage,
                    progress=progress_of(points),
                )
            )
        current = []

    def advance(distance: float) -> None:
        """沿局部 +Z 走一步，然后按向重力扳一下朝向。"""
        nonlocal position, rotation
        heading = _apply(rotation, _FORWARD)
        position = (
            position[0] + heading[0] * distance,
            position[1] + heading[1] * distance,
            position[2] + heading[2] * distance,
        )
        if tropism > 0.0:
            rotation = _bend(rotation, tropism, _GRAVITY)

    for token in tokens:
        symbol = token.module
        params = token.params

        if symbol == "F" or symbol == "G":
            start = position
            advance((_number(params, symbol) if params else step) * factor)
            if muted:
                # ``%`` 之后：只前进，不落点
                if current:
                    flush()
                continue
            if not current:
                current.append((start, thickness))
            current.append((position, thickness))

        elif symbol == "f":
            advance((_number(params, symbol) if params else step) * factor)
            if current:
                flush()

        elif symbol in _TURNS:
            axis, sign = _TURNS[symbol]
            amount = math.radians(_number(params, symbol)) if params else turn
            rotation = _multiply(rotation, _rotation(axis, sign * amount))

        elif symbol == "|":
            rotation = _multiply(rotation, _rotation(1, math.pi))
        elif symbol == "$":
            rotation = _upright(rotation)
        elif symbol == "[":
            stack.append((position, rotation, thickness, step, factor, stage, current, muted))
            stage += 1
            top_stage = max(top_stage, stage)
            current = []
        elif symbol == "]":
            flush()
            if stack:
                position, rotation, thickness, step, factor, stage, current, muted = stack.pop()
        elif symbol == "!":
            thickness *= _number(params, symbol) if params else float(radius_decay)
        elif symbol == "#":
            thickness *= _number(params, symbol) if params else float(radius_fat)
        elif symbol == "¤":
            thickness = _number(params, symbol) if params else thickness
        elif symbol == "s":
            amount = _number(params, symbol) if params else 1.0
            factor *= amount
            thickness *= amount
        elif symbol == "£":
            turn = rng.random() * _TWO_PI
        elif symbol == "%":
            flush()
            muted = True
        elif symbol in _UNSUPPORTED or not symbol.isalpha():
            # 占位模块（X / A / B……）是 L-system 的正常写法，不算「忽略」
            ignored.add(symbol)

    flush()

    if str(dimension).upper().startswith("2"):
        # 绕 X 轴 -90°：(x, y, z) → (x, z, -y)，turtle 的 +Z 变成世界 +Y（躺平在 XY 平面）
        paths = [
            Path(
                points=tuple((x, z, -y) for x, y, z in path.points),
                radii=path.radii,
                depth=path.depth,
                progress=path.progress,
            )
            for path in paths
        ]

    return Skeleton(
        paths=tuple(paths),
        symbols=len(tokens),
        ignored=tuple(sorted(ignored)),
        depth=top_stage,
    )


def build(
    *,
    axiom: str = "F",
    rules: Sequence[Rule] = (),
    iterations: int = 1,
    seed: int = 0,
    angle: float = DEFAULT_ANGLE,
    length: float = 1.0,
    radius: float = 0.01,
    radius_decay: float = 0.8,
    radius_fat: float = 1.2,
    tropism: float = 0.0,
    scale: float = 1.0,
    dimension: str = "2D",
    max_symbols: int = DEFAULT_MAX_SYMBOLS,
) -> Skeleton:
    """一步到位：先重写、再解释。参数与 :func:`interpret` 一一对应。"""
    tokens = iterate(axiom, rules, iterations, seed=seed, max_symbols=max_symbols)
    return interpret(
        tokens,
        angle=angle,
        length=length,
        radius=radius,
        radius_decay=radius_decay,
        radius_fat=radius_fat,
        tropism=tropism,
        scale=scale,
        seed=seed,
        dimension=dimension,
    )
