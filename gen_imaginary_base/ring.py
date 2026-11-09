"""二次整数环 Z[ω] 上的**进制展开**：纯算法层，不 import bpy。

这个模块是「虚数进制」的全部数学，可以脱离 Blender 直接跑（`python ring.py` 也能自测）。

环
    两个域，ω² 各自定死：``I`` 域 ω = i（ω² = −1，高斯整数，方点阵）；``W`` 域
    ω = ζ = e^{2πi/3}（ω² = −1−ω，艾森斯坦整数，三角点阵）。与 `fractal_family` 的
    G / E 是同一对环，只是这里按「虚数单位」命名 —— 因为本插件的参数就是 ω² 本身。

数系
    给定**底数** ``b`` 和**数字集** ``D``（含 0），整数 ``n`` 写成
    ``n = Σ d_k · b^k``（``d_k ∈ D``）。取遍 ``|D|^N`` 个以 ``|D|`` 为基的地址，
    就得到 ``N`` 位以内能表示的全部整数 —— 画在平面上就是那片自仿射的自相似点集。

为什么点集要预计算成表
    取遍地址是 ``|D|^N``，``|D| = 4``、``N = 16`` 就是 40 亿。所以这里的入口一律先按
    点数预算夹一次 ``N``，再把「第 k 位的第 d 个数字」折算成向量表，最后用 numpy 的
    fancy indexing 一次性求和 —— 循环只发生在 ``N × |D|`` 张表上，与点数无关。
"""

from __future__ import annotations

import math

import numpy as np

__all__ = [
    "DOMAINS",
    "QInt",
    "SQRT_3_OVER_2",
    "MAX_PLACES",
    "canonical_check",
    "cell_polygon",
    "coords_of",
    "domain_label",
    "format_qint",
    "lattice_basis",
    "max_places",
    "mod_ring",
    "parse_qint",
    "place_count",
    "point_set",
    "snap_to_lattice",
]

#: 三角点阵的 ω 方向高度（√3 / 2）。
SQRT_3_OVER_2 = math.sqrt(3.0) / 2.0

#: 域：``I`` = 高斯（ω = i）、``W`` = 艾森斯坦（ω = ζ）。
DOMAINS = ("I", "W")

#: 位数上限。位数进指数，16 位配上 4 个数字就是 43 亿个地址 —— 真正的上限由点数预算
#: 决定（见 :func:`max_places`），这个常量只是 UI 滑杆的硬边界。
MAX_PLACES = 16

#: 域的固定参数：``(d0, d1)`` 表示 ω² = d0 + d1·ω，外加显示用的名字。
_DOMAIN = {
    "I": ((-1, 0), "Gaussian", "i"),
    "W": ((-1, -1), "Eisenstein", "ω"),
}


def domain_label(domain: str) -> str:
    """域的显示名，例如 ``"ω² = −1−ω (Eisenstein)"``。"""
    (d0, d1), family, unit = _DOMAIN.get(domain, _DOMAIN["I"])
    if d1 == 0:
        right = f"{d0}"
    elif d1 == 1:
        right = f"{d0}+{unit}"
    elif d1 == -1:
        right = f"{d0}-{unit}"
    else:
        right = f"{d0}{'+' if d1 > 0 else '-'}{abs(d1)}{unit}"
    return f"{unit}² = {right} ({family})"


class QInt:
    """环元素 ``a + b·ω``。

    只存两个整数 + 域，不做归一化 —— 同一个环元素只有一种表示（``Z[ω]`` 的基是
    ``{1, ω}``），所以可以直接拿 ``(a, b, domain)`` 当哈希键去重。
    """

    __slots__ = ("a", "b", "domain")

    def __init__(self, a: int = 0, b: int = 0, domain: str = "I"):
        self.a = int(a)
        self.b = int(b)
        self.domain = domain if domain in DOMAINS else "I"

    # --- 坐标 ---------------------------------------------------------------

    @property
    def coord(self) -> np.ndarray:
        """映射到平面的 ``(x, y)``：``W`` 域是斜坐标到直角的换算。"""
        if self.domain == "W":
            return np.array([self.a - 0.5 * self.b, self.b * SQRT_3_OVER_2])
        return np.array([float(self.a), float(self.b)])

    @property
    def norm(self) -> int:
        """域范数 ``N(a + bω) = x·conj(x)``；``I`` 域是 ``a² + b²``。"""
        if self.domain == "W":
            return self.a * self.a - self.a * self.b + self.b * self.b
        return self.a * self.a + self.b * self.b

    @property
    def is_zero(self) -> bool:
        return self.a == 0 and self.b == 0

    # --- 运算 ---------------------------------------------------------------

    def conj(self) -> QInt:
        """共轭。``W`` 域里 ``conj(a + bζ) = a − b·ζ² `` 化简后是 ``(a − b) − bζ``。"""
        if self.domain == "W":
            return QInt(self.a - self.b, -self.b, self.domain)
        return QInt(self.a, -self.b, self.domain)

    def __add__(self, other: QInt) -> QInt:
        return QInt(self.a + other.a, self.b + other.b, self.domain)

    def __sub__(self, other: QInt) -> QInt:
        return QInt(self.a - other.a, self.b - other.b, self.domain)

    def __neg__(self) -> QInt:
        return QInt(-self.a, -self.b, self.domain)

    def __mul__(self, other: QInt) -> QInt:
        """环乘法：``ω² = d0 + d1·ω`` 把二次项折回去。"""
        (d0, d1), _family, _unit = _DOMAIN[self.domain]
        a, b = self.a, self.b
        c, d = other.a, other.b
        return QInt(a * c + d0 * b * d, a * d + b * c + d1 * b * d, self.domain)

    def __eq__(self, other) -> bool:
        if not isinstance(other, QInt):
            return NotImplemented
        return (self.a, self.b, self.domain) == (other.a, other.b, other.domain)

    def __hash__(self) -> int:
        return hash((self.a, self.b, self.domain))

    def __repr__(self) -> str:
        return f"QInt({self.a}, {self.b}, {self.domain!r})"

    def __str__(self) -> str:
        return format_qint(self.domain, self.a, self.b)


def lattice_basis(domain: str) -> np.ndarray:
    """点阵基矢（2×2，第 0 列是 1 的方向，第 1 列是 ω 的方向）。"""
    if domain == "W":
        return np.array([[1.0, -0.5], [0.0, SQRT_3_OVER_2]])
    return np.eye(2)


def cell_polygon(domain: str, gap: float = 0.0) -> np.ndarray:
    """格点周围的**胞腔**多边形（格距为 1，逆时针，``(n, 2)``）。

    这两种形状就是各自点阵的 Voronoi 胞腔，所以按格点平移铺开来能严密地填满平面：

    * ``I``（高斯整数，方点阵）→ 边长 1 的**正方形**；
    * ``W``（艾森斯坦整数，三角点阵）→ 外接半径 ``1/√3`` 的**正六边形**，尖顶朝上
      （顶点在 30° + k·60°，与点阵的六个邻居方向对齐；平边到中心恰好 0.5）。

    ``gap`` 是每边**向内缩进的比例**（``0`` = 胞腔共边、完全铺满）。缩一点，相邻格之间
    才看得出界线 —— 视口预览与「实心格」输出都走这里，两者形状才会完全重合。

    :return: ``(4, 2)`` 或 ``(6, 2)`` 的角点偏移，单位是格距。
    """
    scale = max(0.05, 1.0 - min(max(float(gap), 0.0), 0.95))
    if domain != "W":
        half = 0.5 * scale
        return np.array(
            [(-half, -half), (half, -half), (half, half), (-half, half)], dtype=np.float32
        )
    radius = scale / math.sqrt(3.0)
    return np.array(
        [
            (
                radius * math.cos(math.pi / 6.0 + index * math.pi / 3.0),
                radius * math.sin(math.pi / 6.0 + index * math.pi / 3.0),
            )
            for index in range(6)
        ],
        dtype=np.float32,
    )


def coords_of(domain: str, a, b):
    """整数对 → 平面坐标；``a`` / ``b`` 可以是标量也可以是 numpy 数组。"""
    if domain == "W":
        return a - 0.5 * b, b * SQRT_3_OVER_2
    return a, b


def snap_to_lattice(domain: str, x: float, y: float) -> tuple[int, int]:
    """平面坐标 → 最近的整数格点（HUD 拖动时的吸附）。"""
    if domain == "W":
        b = y / SQRT_3_OVER_2
        return round(x + b * 0.5), round(b)
    return round(x), round(y)


# ---------------------------------------------------------------------------
# 显示
# ---------------------------------------------------------------------------


def format_qint(domain: str, a: int, b: int) -> str:
    """``(a, b)`` → ``"1-2ω"`` / ``"3"`` / ``"0"``（面板与 HUD 上显示的就是它）。"""
    unit = _DOMAIN.get(domain, _DOMAIN["I"])[2]
    if b == 0:
        return str(a)
    if b == 1:
        body = f"{unit}"
    elif b == -1:
        body = f"-{unit}"
    else:
        body = f"{b}{unit}"
    if a == 0:
        return body
    # b < 0 时 body 自带负号（"-2ω"），b > 0 时得自己补一个加号，否则 "1" + "2ω" 会拼成 "12ω"
    return f"{a}{body}" if b < 0 else f"{a}+{body}"


def parse_qint(domain: str, text: str) -> tuple[int, int]:
    """``"1-2ω"`` / ``"-3+i"`` / ``"4"`` / ``"ω"`` → ``(a, b)``。

    只认 :func:`format_qint` 的写法（实部在前、虚部系数紧贴单位），这是面板与分享码
    里出现的唯一形式；认不出的部分按 0 处理，不抛异常 —— 用户正在输入时就该能容错。
    """
    unit = _DOMAIN.get(domain, _DOMAIN["I"])[2]
    cleaned = text.replace(" ", "").replace("*", "").replace("−", "-")
    for alias in ("i", "ω", "ζ", "j"):
        cleaned = cleaned.replace(alias, unit)
    if not cleaned:
        return 0, 0
    if unit not in cleaned:
        try:
            return int(float(cleaned)), 0
        except ValueError:
            return 0, 0
    head = cleaned.split(unit)[0]
    if head in ("", "+"):
        return 0, 1
    if head == "-":
        return 0, -1
    # 实部与虚部系数之间那条符号：从右往左找第一个不在首位的 +/-
    cut = max(head.rfind("+"), head.rfind("-"))
    if cut <= 0:
        # 没有分隔符：整个 head 就是 ω 的系数（"3ω"）
        try:
            return 0, int(float(head))
        except ValueError:
            return 0, 0
    try:
        a = int(float(head[:cut]))
    except ValueError:
        a = 0
    tail = head[cut:]
    try:
        b = int(float(tail)) if tail not in ("+", "-") else (1 if tail == "+" else -1)
    except ValueError:
        b = 1
    return a, b


# ---------------------------------------------------------------------------
# 整除（判断数字集是不是完全剩余系要用）
# ---------------------------------------------------------------------------


def _nearest(domain: str, x: float, y: float) -> tuple[int, int]:
    """平面上一点 → 最近的环元素（``W`` 域是六边形最近点，不能直接四舍五入）。"""
    if domain != "W":
        return round(x), round(y)
    # 轴向坐标 → 立方坐标取整，再把偏差最大的那一项修正回去（标准的 hex rounding）
    a = x + y / math.sqrt(3.0)
    b = 2.0 * y / math.sqrt(3.0)
    c = -a - b
    ra, rb, rc = round(a), round(b), round(c)
    da, db, dc = abs(ra - a), abs(rb - b), abs(rc - c)
    if da > db and da > dc:
        ra = -rb - rc
    elif db > dc:
        rb = -ra - rc
    return int(ra), int(rb)


def mod_ring(domain: str, n: QInt, base: QInt) -> QInt:
    """``n mod base`` 的余数：``n − round(n / base) · base``。

    ``Z[ω]`` 是欧氏整环（两个域都是范数欧氏），所以「最近商」给出的余数一定比底数小。
    """
    quotient = n * base.conj()
    scale = base.norm
    if scale == 0:
        raise ZeroDivisionError("modulo by zero base")
    a, b = _nearest(domain, quotient.a / scale, quotient.b / scale)
    return n - QInt(a, b, domain) * base


def canonical_check(domain: str, base: QInt, digits) -> dict:
    """这个数系是不是**有效的进位制**（每个整数唯一表示）。

    两条判据，都是判定的、也都是便宜的：

    1. ``|D| = N(base)`` —— 位数必须等于底数的范数，否则要么表示不全、要么必然撞车；
    2. ``D`` 在 ``Z[ω]/(base)`` 里两两不同余 —— 等价于 ``D`` 是模 ``base`` 的完全剩余系。

    满足这两条，地址 → 整数就是双射（Knuth 对高斯整数、Kátai–Kovács 对一般二次环）。

    参考项目不判断这件事（任意数字集都能拖），所以它的图里经常出现"点叠在一起"；
    这里把它显式算出来，面板上给一行提示 —— 是这个插件比参考实现多出来的东西。

    :return: ``{"ok": bool, "digits": int, "norm": int, "reason": str}``
    """
    size = len(digits)
    scale = base.norm
    if size == 0:
        return {"ok": False, "digits": 0, "norm": scale, "reason": "no digits"}
    if size != scale:
        return {
            "ok": False,
            "digits": size,
            "norm": scale,
            "reason": f"{size} digits but the base has norm {scale}",
        }
    seen = {}
    for index, digit in enumerate(digits):
        residue = mod_ring(domain, digit, base)
        key = (residue.a, residue.b)
        if key in seen:
            return {
                "ok": False,
                "digits": size,
                "norm": scale,
                "reason": f"digits {seen[key]} and {index} are congruent mod the base",
            }
        seen[key] = index
    return {"ok": True, "digits": size, "norm": scale, "reason": ""}


# ---------------------------------------------------------------------------
# 地址 → 整数（点集）
# ---------------------------------------------------------------------------


def max_places(digit_count: int, limit: int) -> int:
    """点数预算 ``limit`` 下最多能取到几位（至少 1，至多 :data:`MAX_PLACES`）。"""
    digit_count = max(2, int(digit_count))
    limit = max(int(limit), digit_count)
    return max(1, min(MAX_PLACES, int(math.log(limit) / math.log(digit_count))))


def place_count(digit_count: int, places: int) -> int:
    """位数 ``places`` 下的地址总数 ``|D|^places``。"""
    return max(1, int(digit_count)) ** max(1, int(places))


def _power_tables(domain: str, base, digits, places: int):
    """``(A, B)``：``A[k][d]`` / ``B[k][d]`` = 数字 ``d`` 乘 ``b^k`` 的两个分量。

    表只有 ``places × |D|`` 项，与点数无关 —— 这是整套点集能一次算完的关键。
    """
    size = len(digits)
    powers = [(1, 0)]
    b = QInt(base[0], base[1], domain)
    current = QInt(1, 0, domain)
    for _ in range(places):
        current = current * b
        powers.append((current.a, current.b))
    table_a = np.zeros((places, size), dtype=np.int64)
    table_b = np.zeros((places, size), dtype=np.int64)
    for place in range(places):
        pa, pb = powers[place]
        for index, (da, db) in enumerate(digits):
            value = QInt(da, db, domain) * QInt(pa, pb, domain)
            table_a[place, index] = value.a
            table_b[place, index] = value.b
    return table_a, table_b


def point_set(
    domain: str,
    base,
    digits,
    places: int,
    *,
    limit: int = 60000,
    colour_mode: str = "AGGREGATE",
    colour_place: int = 1,
):
    """算出 ``places`` 位以内全部整数的点集。

    :param domain: ``"I"`` / ``"W"``。
    :param base: 底数 ``(a, b)``。
    :param digits: 数字集 ``[(a, b), ...]``，首位约定是 0。
    :param places: 位数（会被 :func:`max_places` 再夹一次）。
    :param limit: 点数预算（超过就自动降一位，宁可少画一级也不卡住视口）。
    :param colour_mode: ``"AGGREGATE"``（聚合）/ ``"LEAD"``（按最高位）/ ``"PLACE"``（按第 k 位）。
    :param colour_place: ``PLACE`` 模式下的位号（1 = 最低位）。

    :return: ``dict``，字段见下面 ``result`` 的构造 —— 坐标是 ``(n, 2)`` 的 float32，
        ``index`` 是每个点自己的地址（用于上色与查重），``places`` / ``count`` 是实算值。
    """
    digits = [(int(a), int(b)) for a, b in digits]
    size = len(digits)
    if size < 2:
        return {
            "xy": np.zeros((0, 2), np.float32),
            "index": np.zeros(0, np.int64),
            "tint": np.zeros(0, np.int32),
            "places": 0,
            "count": 0,
            "digit_count": size,
        }

    places = max(1, min(int(places), max_places(size, limit)))
    count = min(place_count(size, places), int(limit))

    # 地址 → 每一位的编号（混合进制；第 0 位是最低位）。用整除而不是循环取模，
    # 整个数组一次算完。
    index = np.arange(count, dtype=np.int64)
    place_digit = np.empty((count, places), dtype=np.int32)
    for place in range(places):
        place_digit[:, place] = (index // (size**place)) % size

    table_a, table_b = _power_tables(domain, base, digits, places)
    a = np.zeros(count, dtype=np.int64)
    b = np.zeros(count, dtype=np.int64)
    for place in range(places):
        slot = place_digit[:, place]
        a += table_a[place][slot]
        b += table_b[place][slot]

    x, y = coords_of(domain, a.astype(np.float64), b.astype(np.float64))

    result = {
        "xy": np.stack([x, y], axis=1).astype(np.float32),
        "index": index,
        "a": a,
        "b": b,
        "place_digit": place_digit,
        "places": places,
        "count": count,
        "digit_count": size,
        "lead": (
            place_digit[:, places - 1] if places else np.zeros(count, np.int32)
        ).astype(np.int32),
    }
    result["slot"], result["shade"] = _tints(result, colour_mode, colour_place)
    return result


def _tints(result, mode: str, place: int):
    """每个点用哪一号颜色、以及那一号要混多浓。

    返回 ``(slot, shade)``：``slot`` 是数字集里的编号（``palette`` 按它取色），``shade``
    是该点离"最表层"有多远 —— ``1.0`` = 纯正的数字色，越接近 ``0.8`` 越淡。

    ``AGGREGATE`` 是参考实现的默认档：颜色由**最高位**决定，但同一个最高位底下，
    低位的身份会被一层层混进去，于是每个数字都在各层里复现自己 —— 这是这套点集最能
    看出自相似的一档，也是最贵的（递归，一遍 Python 循环）。
    """
    count = result["count"]
    size = result["digit_count"]
    if mode == "PLACE":
        slot_index = max(1, min(int(place), result["places"])) - 1
        return (
            result["place_digit"][:, slot_index].astype(np.int32),
            np.ones(count, dtype=np.float32),
        )
    lead = result["lead"].astype(np.int32)
    if mode != "AGGREGATE" or result["places"] <= 1:
        return lead, np.ones(count, dtype=np.float32)

    # shade(n) = 0.8 + 0.2 · shade(n 去掉最高位)，shade(0) = 0。
    # 「去掉最高位」一定让地址变小，所以按地址升序扫一遍就等价于递归。
    weight = int(size) ** (result["places"] - 1)
    drop = result["index"] - lead.astype(np.int64) * weight
    shade = np.zeros(count, dtype=np.float32)
    for i in range(count):
        shade[i] = 0.8 + 0.2 * float(shade[int(drop[i])])
    return lead, shade
