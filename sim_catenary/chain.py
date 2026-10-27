"""铁环相扣悬链线：离散链理论 + 环的几何（Grease Pencil 笔划点列）。

纯算法层，**不 import bpy**，可以脱离 Blender 单独跑测试::

    python -c "from sim_catenary import chain; print(chain.arch_chain(101)[0][:3])"

坐标与模型约定
--------------
* 链铺在 **XZ 平面**（y = 0），z 向上，重力沿 -Z；
* 一个环 = 一个节点（环心），相邻环心距 = **相扣节距** ``pitch = 2(R - r)``（内径，
  不是外径 —— 取外径时环根本穿不过对方的孔，见 ``README.md``）；
* 环面轴（环所在平面的法线）奇偶交错：偶环 = ``+Y``，奇环 = 链平面内的法线
  ``(-tz, 0, tx)``。两者都垂直于局部切线，于是相邻环互相垂直、且都包含链方向，
  这才是真正相扣的姿态；环是 **3D 曲线**，交叉处谁压谁由 GP 的深度遮挡负责
  （``stroke_depth_order = '3D'``），几何里不做任何"断开"；
* 离散链平衡：``tan(θ_i) = i·l / a``（从最低点往上数第 i 段），逐段递推节点。

几何输出的消费方是 :mod:`gp`（写 Grease Pencil 笔划）；这里只产出点列与拓扑，
不认识任何 Blender 概念。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

__all__ = [
    "ARCH_A",
    "BEZIER_SEGMENTS",
    "CURVE_BEZIER",
    "CURVE_POLY",
    "GRAVITY",
    "PITCH",
    "RING_SEGMENTS",
    "RING_STRAIGHT",
    "R_MAJOR",
    "R_MINOR",
    "RingGeometry",
    "arch_chain",
    "chain_links",
    "interlock_stats",
    "link_axes",
    "pitch_for",
    "polyline_geometry",
    "recommended_segments",
    "ring_geometry",
    "tangents",
    "theoretical_tension_max",
]

# --- 物理 / 几何常量（与参考实现 .ref/ring_catenary 一致） -------------------

#: 环主圆半径 [m]（跑道形的端头半径）。
R_MAJOR = 0.06
#: 环管半径 [m]；只参与相扣节距，不影响笔划粗细（见 ``props.line_width``）。
R_MINOR = 0.02
#: 环的直段长度 [m]：0 = 正圆；> 0 就是真实链环那种「操场跑道」形
#: （两段直边 + 两个半圆端）。长轴总是沿着链方向，所以整条链看起来像真链条。
RING_STRAIGHT = 0.06
#: 相扣节距 = 孔的内长 = ``直段 + 内径 2(R - r)`` [m]。取外长就穿不过对方的孔。
PITCH = RING_STRAIGHT + 2.0 * (R_MAJOR - R_MINOR)
#: 重力加速度 [m/s²]（面板可调，这里只是缺省值）。
GRAVITY = 9.81
#: 拱形跨度参数 [m]（悬链线的特征长度 ``a``）。
ARCH_A = 2.0

#: 笔划曲线类型 —— 贝塞尔（默认：控制点少、形状是真空曲线）或折线。
CURVE_BEZIER = "BEZIER"
CURVE_POLY = "POLY"

#: 折线的推荐段数（= 一个环一圈的控制点数）。8 点就是明显的八边形，24 点看不出棱。
RING_SEGMENTS = 24
#: 贝塞尔的推荐段数（= 一个环几段三次曲线）。4 段 = 每段 90°，圆的径向误差
#: ≈ 0.027% R（R = 0.06 m 时约 16 µm），肉眼与 24 段折线无差，但控制点只有 1/6。
BEZIER_SEGMENTS = 4


def pitch_for(
    rmaj: float = R_MAJOR, rmin: float = R_MINOR, straight: float = RING_STRAIGHT
) -> float:
    """相扣节距：孔的内长 ``straight + 2(R - r)``。取外长就穿不过对方的孔。

    ``straight = 0`` 时退回正圆的老公式 ``2(R - r)``。
    """
    return max(0.0, float(straight)) + 2.0 * (float(rmaj) - float(rmin))


# --- 理论离散链 -------------------------------------------------------------


def arch_chain(
    n_nodes: int, a: float = ARCH_A, pitch: float = PITCH
) -> tuple[np.ndarray, np.ndarray, float, float]:
    """理论离散悬链线 + 它的镜像"拱"（等长、端点相同）。

    节点 = **关节**（相邻两环共享的端点），相邻节点距 = 相扣节距 ``pitch``。

    返回 ``(theory, arch, x_end, z_end)``：

    * ``theory``：``(N,3)`` 平衡态关节（最低点在 z = 0）；
    * ``arch``：``(N,3)`` 关于端点高度镜像的初始形态 —— 从这个上拱落到悬链线，
      全程环都保持相扣（模拟的初始状态）；
    * ``x_end`` / ``z_end``：单侧跨度的水平/竖直分量（建相机、布灯用）。

    关节数奇偶都行：奇数时最低点落在关节上，偶数时落在中间那段的中点（多出半段）。
    ``a`` 是特征长度，``pitch`` 是相邻关节距 = 一节的孔内长。
    """
    n = int(n_nodes)
    if n < 3:
        raise ValueError("need at least 3 nodes")
    l = float(pitch)
    a = float(a)

    # 从最低点往上逐段递推：到手的那一段 tan(θ_k) = (k − 0.5)·l / a（偶数关节时多一半），
    # 这里按"该段上端的竖直荷载"递推，奇偶两种情况只差最初那半段。
    if n % 2:
        xs = [0.0]
        zs = [0.0]
        for k in range(1, (n - 1) // 2 + 1):
            theta = math.atan(k * l / a)
            xs.append(xs[-1] + l * math.cos(theta))
            zs.append(zs[-1] + l * math.sin(theta))
        full_x = [-v for v in reversed(xs[1:])] + xs
        full_z = list(reversed(zs[1:])) + zs
    else:
        xs = [0.5 * l]  # 最低点在这一段的中间
        zs = [0.0]
        for k in range(1, n // 2):
            theta = math.atan((k + 0.5) * l / a)
            xs.append(xs[-1] + l * math.cos(theta))
            zs.append(zs[-1] + l * math.sin(theta))
        full_x = [-v for v in reversed(xs)] + xs
        full_z = list(reversed(zs)) + zs
    zeros = np.zeros(n, dtype=np.float32)
    theory = np.stack(
        [np.asarray(full_x, np.float32), zeros, np.asarray(full_z, np.float32)], axis=1
    )
    top = 2.0 * full_z[-1]
    arch = np.stack(
        [
            np.asarray(full_x, np.float32),
            zeros,
            np.asarray([top - z for z in full_z], np.float32),
        ],
        axis=1,
    )
    return theory, arch, float(xs[-1]), float(zs[-1])


def tangents(pos: np.ndarray) -> np.ndarray:
    """节点的单位切线（中心差分，端点用单侧差分）。``(N,3) → (N,3)``。"""
    pos = np.asarray(pos, np.float32)
    t = np.zeros_like(pos)
    t[1:-1] = pos[2:] - pos[:-2]
    t[0] = pos[1] - pos[0]
    t[-1] = pos[-1] - pos[-2]
    norm = np.linalg.norm(t, axis=1, keepdims=True)
    norm = np.where(norm < 1e-12, 1.0, norm)
    return t / norm


def chain_links(pos: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """关节位置 → 每个链环的 (环心, 长轴方向)。``(N,3) → (N-1,3), (N-1,3)``。

    一个链环就占一节：**环心 = 这一节的中点，长轴 = 这一节的走向**。于是相邻两环
    共用同一个关节，环心线各自有 ``r`` 越过关节（两端重叠 ``2r``）—— 相扣是结构保证的，
    不管链怎么弯都不会"飞出去"。

    这也是为什么环数 = 节数 = 关节数 − 1，且关节数与环数的奇偶不必相同。
    """
    pos = np.asarray(pos, np.float32)
    if pos.shape[0] < 2:
        raise ValueError("need at least 2 joints")
    centres = 0.5 * (pos[:-1] + pos[1:])
    direction = pos[1:] - pos[:-1]
    norm = np.linalg.norm(direction, axis=1, keepdims=True)
    direction = direction / np.where(norm < 1e-9, 1.0, norm)
    return centres.astype(np.float32), direction.astype(np.float32)


def link_axes(direction: np.ndarray) -> np.ndarray:
    """每环的环面轴（环所在平面的法线），奇偶交错，保证相邻环垂直相扣。

    * 偶环：轴 = ``+Y`` → 环面 = 链平面 XZ（从 -Y 看是一个完整的跑道形）；
    * 奇环：轴 = ``长轴 × Y``（垂直于长轴、仍在链平面内）→ 环面 = ``span{长轴, Y}``
      （从 -Y 看是一条边对边的直线，正好从偶环的孔里穿过）。

    全向量化，无 Python 循环。``(N,3) → (N,3)`` 单位向量。
    """
    direction = np.asarray(direction, np.float32)
    n = direction.shape[0]
    axes = np.zeros_like(direction)
    axes[0::2] = (0.0, 1.0, 0.0)
    odd = np.arange(1, n, 2)
    if len(odd):
        normal = np.cross(direction[odd], (0.0, 1.0, 0.0))
        norm = np.linalg.norm(normal, axis=1, keepdims=True)
        axes[odd] = normal / np.where(norm < 1e-9, 1.0, norm)
    return axes


# --- 环 / 折线 → 笔划几何 ---------------------------------------------------


@dataclass(frozen=True)
class RingGeometry:
    """一组笔划：点列 + 拓扑（每条笔划几个点、是否闭合、属于哪个环）。

    这是 :mod:`gp` 唯一认识的契约 —— 点列是 **世界坐标**，``counts`` 是每条笔划的
    点数（相加 = ``len(points)``），``ring`` 是每个点归属的环序号（按它上色）。

    ``curve == "BEZIER"`` 时额外带两侧手柄（``(M,3)``，同样是世界坐标）；折线时两个
    手柄是 ``None``。
    """

    points: np.ndarray  # (M,3) float32
    counts: np.ndarray  # (S,)   int32
    cyclic: np.ndarray  # (S,)   bool
    ring: np.ndarray  # (M,)   int32
    #: (M,3) float32，贝塞尔手柄；折线为 ``None``。
    handles_left: np.ndarray | None = None
    handles_right: np.ndarray | None = None
    curve: str = CURVE_POLY

    @property
    def strokes(self) -> int:
        return len(self.counts)

    def flat(self) -> np.ndarray:
        """``(M,3)`` → 展平的 ``(3M,)``，给 ``foreach_set`` 用。"""
        return np.ascontiguousarray(self.points, np.float32).reshape(-1)


def _sweep(pos, tangent, side, phi, radius, handle_k=0.0):
    """沿每环的 ``(tangent, side)`` 基底按角度 ``phi`` 采样圆上的点。

    ``pos/tangent/side`` 为 ``(m,3)``，``phi`` 为 ``(m,k)``，返回 ``(points, left, right)``，
    形状都是 ``(m,k,3)``；``handle_k`` 是贝塞尔手柄系数（``4/3·tan(Δ/4)``，Δ = 每段角度），
    为 0 时不给手柄（返回 ``None``）。

    手柄 = 控制点 ± ``handle_k`` × 该角度的切向（切向 = ``R·(−sinφ·u + cosφ·v)``，
    长度正好是 R，所以乘上无量纲的 ``handle_k`` 就是标准圆弧三次逼近的手柄长度）。
    """
    cos = np.cos(phi)[:, :, None]
    sin = np.sin(phi)[:, :, None]
    centre = pos[:, None, :]
    u = tangent[:, None, :]
    v = side[:, None, :]
    points = centre + radius * (cos * u + sin * v)
    if handle_k <= 0.0:
        return points, None, None
    along = radius * (-sin * u + cos * v)
    return points, points - handle_k * along, points + handle_k * along


def _basis_map(tangent, side, profile: np.ndarray) -> np.ndarray:
    """把 2D 轮廓 ``(s, t)`` 映射到每环的 3D 基底上：``s·u + t·v``。

    ``tangent/side`` 为 ``(n,3)``，``profile`` 为 ``(m,2)``，返回 ``(n,m,3)``。点列用
    ``pos[:, None, :] + _basis_map(...)``，手柄偏移也走同一个映射。
    """
    s = np.asarray(profile[:, 0], np.float32)[None, :, None]
    t = np.asarray(profile[:, 1], np.float32)[None, :, None]
    return s * tangent[:, None, :] + t * side[:, None, :]


def _stadium_profile(
    radius: float, straight: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """跑道形环（操场跑道）的 2D 轮廓：控制点 / 左手柄偏移 / 右手柄偏移。

    坐标 = ``(s 沿长轴 t 沿侧向)``，6 个控制点顺时针：右上 → 右端 → 右下 → 左下 →
    左端 → 左上。两个半圆端各拆成 2 段 90° 三次曲线（端头一个控制点），上下两条直边
    各 1 段。直边与半圆在 ``(±L/2, ±R)`` 相切，所以同一个控制点两侧的手柄同向、
    长度不同（直边 = L/3，弧 = ``4/3·tan(22.5°)·R`` = 0.5523R）。
    """
    half = 0.5 * float(straight)
    arc = (4.0 / 3.0) * math.tan(math.pi / 8.0) * float(radius)
    third = float(straight) / 3.0
    points = np.array(
        [
            (half, radius),
            (half + radius, 0.0),
            (half, -radius),
            (-half, -radius),
            (-half - radius, 0.0),
            (-half, radius),
        ],
        np.float32,
    )
    left = np.array(
        [(-third, 0.0), (0.0, arc), (arc, 0.0), (third, 0.0), (0.0, -arc), (-arc, 0.0)],
        np.float32,
    )
    right = np.array(
        [(arc, 0.0), (0.0, -arc), (-third, 0.0), (-arc, 0.0), (0.0, arc), (third, 0.0)],
        np.float32,
    )
    return points, left, right


def _stadium_loop(radius: float, straight: float, count: int) -> np.ndarray:
    """沿跑道形轮廓按弧长均匀取 ``count`` 个点（折线用，形状与贝塞尔版重合）。"""
    half = 0.5 * float(straight)
    arc = math.pi * float(radius)
    total = 2.0 * float(straight) + 2.0 * arc
    q = np.linspace(0.0, total, max(6, int(count)), endpoint=False)
    out = np.empty((len(q), 2), np.float32)
    # 上直边 → 右半圆 → 下直边 → 左半圆（从 (−L/2, +R) 顺时针走一圈）
    bend_a = float(straight)
    bend_b = bend_a + arc
    bend_c = bend_b + float(straight)
    top = q < bend_a
    out[top, 0] = -half + q[top]
    out[top, 1] = radius
    right_arc = (q >= bend_a) & (q < bend_b)
    theta = np.pi / 2.0 - (q[right_arc] - bend_a) / radius
    out[right_arc, 0] = half + radius * np.cos(theta)
    out[right_arc, 1] = radius * np.sin(theta)
    bottom = (q >= bend_b) & (q < bend_c)
    out[bottom, 0] = half - (q[bottom] - bend_b)
    out[bottom, 1] = -radius
    left_arc = q >= bend_c
    theta = -np.pi / 2.0 - (q[left_arc] - bend_c) / radius
    out[left_arc, 0] = -half + radius * np.cos(theta)
    out[left_arc, 1] = radius * np.sin(theta)
    return out


def ring_geometry(
    pos: np.ndarray,
    axes: np.ndarray,
    radius: float = R_MAJOR,
    segments: int = RING_SEGMENTS,
    *,
    curve: str = CURVE_POLY,
    straight: float = RING_STRAIGHT,
    long: np.ndarray | None = None,
) -> RingGeometry:
    """环心 + 环面轴 (+ 长轴方向) → 每个环**一整圈**闭合笔划。

    环是**真的 3D 曲线**：偶环轴 +Y、奇环轴 = 链平面内的法线，相邻环互相垂直并真的
    穿过对方的孔（100/100 相扣）。谁压住谁由 GP 的深度遮挡自己算 ——
    ``gp.write`` 会把 ``stroke_depth_order`` 设成 ``'3D'``，笔划按 3D 位置逐像素比深度，
    所以**不需要把环拆成半圆、也不需要手工排绘制顺序**：转视角、换相机都对。

    环的截面是真实链环那种**跑道形**（``straight`` = 直段长度，0 就退回正圆）。
    ``long`` 是每个环的**长轴方向** —— 上层用 :func:`chain_links` 给"这一节的走向"；
    不给时退回节点切线（旧行为，弯折时会露出错位）。

    ``segments`` 对两种曲线类型都是"一个环几段"：折线时 = 一圈的控制点数（按弧长均匀取，
    24 点看不出棱），贝塞尔时 = 几段三次曲线（4 段就是 90° 一段）。跑道形在贝塞尔下
    形状本来就是精确的（6 个控制点：两个半圆端各 2 段 90° + 两条直边各 1 段），
    所以这时 ``segments`` 不参与。
    """
    pos = np.ascontiguousarray(pos, np.float32)
    axes = np.ascontiguousarray(axes, np.float32)
    n = pos.shape[0]
    if n < 2:
        raise ValueError("need at least 2 ring centres")

    bezier = str(curve) == CURVE_BEZIER
    seg = int(segments)
    stadium = float(straight) > 1e-6

    if long is None:
        tangent = tangents(pos)
    else:
        tangent = np.ascontiguousarray(long, np.float32)
        length = np.linalg.norm(tangent, axis=1, keepdims=True)
        tangent = tangent / np.where(length < 1e-9, 1.0, length)
    side = np.cross(axes, tangent)
    norm = np.linalg.norm(side, axis=1, keepdims=True)
    side = side / np.where(norm < 1e-9, 1.0, norm)

    if stadium:
        if bezier:
            profile, left_off, right_off = _stadium_profile(radius, straight)
        else:
            profile = _stadium_loop(radius, straight, max(6, seg + (seg & 1)))
            left_off = right_off = None
        count = len(profile)
        base = _basis_map(tangent, side, profile)
        points = pos[:, None, :] + base
        left = (
            None if left_off is None else points + _basis_map(tangent, side, left_off)
        )
        right = (
            None if right_off is None else points + _basis_map(tangent, side, right_off)
        )
    else:
        seg = max(3, seg) if bezier else max(6, seg + (seg & 1))
        count = seg
        # 每段角度 → 圆弧三次逼近的手柄系数（折线时为 0，不给手柄）
        k = (4.0 / 3.0) * math.tan(math.pi / (2.0 * seg)) if bezier else 0.0
        phi = np.broadcast_to(
            np.linspace(0.0, 2.0 * np.pi, seg, endpoint=False, dtype=np.float32),
            (n, seg),
        )
        points, left, right = _sweep(pos, tangent, side, phi, radius, k)

    return RingGeometry(
        points=points.reshape(-1, 3).astype(np.float32),
        counts=np.full(n, count, np.int32),
        cyclic=np.ones(n, bool),
        ring=np.repeat(np.arange(n), count).astype(np.int32),
        handles_left=None if left is None else left.reshape(-1, 3).astype(np.float32),
        handles_right=None
        if right is None
        else right.reshape(-1, 3).astype(np.float32),
        curve=CURVE_BEZIER if bezier else CURVE_POLY,
    )


def polyline_geometry(pos: np.ndarray) -> RingGeometry:
    """把节点连成一条开放折线（链的"脊线"辅助层）。"""
    pos = np.ascontiguousarray(pos, np.float32)
    return RingGeometry(
        points=pos,
        counts=np.asarray([len(pos)], np.int32),
        cyclic=np.zeros(1, bool),
        ring=np.arange(len(pos), dtype=np.int32),
    )


def recommended_segments(curve: str) -> int:
    """某种曲线类型的推荐段数（面板切换类型时用）。"""
    return BEZIER_SEGMENTS if str(curve) == CURVE_BEZIER else RING_SEGMENTS


# --- 数值验证（面板读数 / 自测用） ------------------------------------------


def theoretical_tension_max(theory: np.ndarray, gravity: float = GRAVITY) -> float:
    """由静力平衡反推理论张力分布的端点（最大）值，用作色带固定上限。

    单位质量 w = 1，端点张力 ∝ 重力，所以重力滑块一变，色带上限跟着线性缩放。
    与参考实现 ``step9`` 的判据一致。
    """
    theory = np.asarray(theory, np.float32)
    d = np.diff(theory, axis=0)
    n = d / np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-12)
    mg = 1.0 * float(gravity)
    estimates = []
    for k in range(1, len(theory) - 1):
        den = n[k, 2] / max(n[k, 0], 1e-9) - n[k - 1, 2] / max(n[k - 1, 0], 1e-9)
        if abs(den) > 1e-9:
            estimates.append(mg / den)
    if not estimates:
        return 1.0
    horizontal = float(np.median(estimates))
    tension = horizontal / np.maximum(n[:, 0], 1e-6)
    return float(np.max(tension))


def _ring_frame(centre, axis, long_axis) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """环的局部标架：``(n 法线, u 长轴, v 侧向)``，都是单位向量。"""
    n = np.asarray(axis, np.float64)
    n = n / max(float(np.linalg.norm(n)), 1e-12)
    u = np.asarray(long_axis, np.float64)
    u = u - float(u @ n) * n  # 长轴必须在环面内
    length = float(np.linalg.norm(u))
    u = u / length if length > 1e-9 else np.array([1.0, 0.0, 0.0])
    return n, u, np.cross(n, u)


def _opening_gap(
    centre, axis, long_axis, point, radius: float, tube: float, straight: float
) -> float:
    """``point`` 到环孔边界的余量（孔 = 端头半径 ``R - r`` 的跑道形区域；< 0 = 在孔里）。"""
    n, u, v = _ring_frame(centre, axis, long_axis)
    rel = np.asarray(point, np.float64) - np.asarray(centre, np.float64)
    s = float(rel @ u)
    t = float(rel @ v)
    a = float(rel @ n)
    gap = math.hypot(max(abs(s) - 0.5 * float(straight), 0.0), t)
    return max(gap, abs(a)) - (float(radius) - float(tube))


def _tip_gap(
    centre, axis, long_axis, tip, radius: float, tube: float, straight: float
) -> float:
    """``tip`` 到环孔边界的余量（< 0 = 端头穿进孔里了）。"""
    return _opening_gap(centre, axis, long_axis, tip, radius, tube, straight)


def interlock_stats(
    pos: np.ndarray,
    axes: np.ndarray,
    radius: float = R_MAJOR,
    tube: float = R_MINOR,
    straight: float = RING_STRAIGHT,
    long: np.ndarray | None = None,
) -> tuple[int, int, list[tuple[int, float, float]]]:
    """逐对检查相邻环是否真的相扣（彼此的端头都插进对方的孔里）。

    判据：环 B 的**两个端头**（中心线穿过 A 平面的那两个点，也就是长轴两端）里更靠近
    A 的那个，必须落在 A 的孔里（半径 ``R - r`` 的跑道形区域），反之亦然。返回
    ``(相扣对数, 总对数, 违例列表)``；违例是 ``(i, gap_B, gap_A)``，``gap`` 是端头离孔
    边界的余量（负数 = 插进孔里了）。

    注意这个判据只看中心线：链弯得比较狠（> 70°）时端头会滑出孔边界，但管子本身还有
    ``r`` 的粗细，画面上仍然像"挂着"。读数掉一两对不等于视觉上脱钩。

    ``long`` = 每个环的长轴方向（上层给"这一节的走向"）；不给时退回节点切线。
    """
    pos = np.asarray(pos)
    axes = np.asarray(axes)
    long = tangents(pos) if long is None else np.asarray(long, np.float64)
    reach = 0.5 * float(straight) + float(radius)  # 环心到端头
    ok = 0
    violated: list[tuple[int, float, float]] = []
    for i in range(len(pos) - 1):
        gaps = []
        for a, b in ((i, i + 1), (i + 1, i)):
            best = min(
                _tip_gap(
                    pos[a],
                    axes[a],
                    long[a],
                    np.asarray(pos[b], np.float64) + sign * reach * long[b],
                    radius,
                    tube,
                    straight,
                )
                for sign in (1.0, -1.0)
            )
            gaps.append(best)
        if gaps[0] < 0.0 and gaps[1] < 0.0:
            ok += 1
        else:
            violated.append((i, round(gaps[0], 4), round(gaps[1], 4)))
    return ok, max(len(pos) - 1, 0), violated
