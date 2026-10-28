"""Jacobi theta 除数环面求解器（纯 NumPy 后端 + 后端工厂）。

环面 ``T²`` 上放 N 个移动的零点 ``c_j(t)``，用 theta 除子::

    Θ(z, t) = Π_{j=1..N} θ1(z − c_j(t) | τ)

在 ``(u, v) ∈ [0,1)`` 的 nu×nv 网格上求值，化成顶点要用的四张标量场：

* ``amplitude``  —— 零点邻近度：每个零点的 ``exp(−120·d²)`` 叠加（"零点的光晕"）；
* ``phase``      —— 各零点 ``arg θ1`` 的累加；
* ``potential``  —— ``log|Θ|``（零点处发散到 −∞，就是"势井"）；
* ``phase_grid`` —— 高频相位干涉格纹，再乘一份被光晕压暗的系数。

环面顶点半径 ``r = R_major + r_minor + h``，``h`` 是零点凹陷与相位涟漪的叠加::

    h = −halo·dip_depth + (phase_grid − 0.5)·ripple_scale

零点本身是环面上的弹性小球（:class:`SphereParticles`）：``(u, v)`` 在环面上自由运动，
两球在**三维**环面流形上的距离小于 ``2·sphere_radius`` 时交换动量并分离 —— 与参考实现
``ga2/common/jacobi_theta.py`` 同一套规则，只是把 O(N²) 的成对循环换成了向量化的
``np.add.at``（64 个零点仍是几千次浮点运算，无关紧要）。

θ1 用 Fourier 级数（前 6 项，与参考实现同样截断）::

    θ1(z | τ) = 2·Σ_{n=0..5} (−1)^n · q^{(n+½)²} · sin((2n+1)·π·z),  q = exp(iπτ)

实现上有两条路：:func:`theta1` 是照着式子直接对复数数组求值（写文档、对答案用）；
:func:`theta1_separable` 把 ``z = du + τ·dv`` 拆成只依赖 u / v 的两半，用一维
``sin``/``cosh`` 加外积算 —— 网格求值走的是后者（快一个量级）。

Taichi 后端在 :mod:`sim_jacobi.taichi_solver`（同一套公式的 GPU kernel），两者 API 相同，
由 :func:`create` 按需选择；本模块**不 import taichi**，所以可以脱离 Blender 与 GPU
单独跑（系统 Python + numpy 即可）。
"""

from __future__ import annotations

import glob
import math
import os
import sys

import numpy as np

__all__ = [
    "BACKEND_AUTO",
    "BACKEND_NUMPY",
    "BACKEND_TAICHI",
    "DEFAULTS",
    "MAX_ZEROS",
    "PARAM_KEYS",
    "SphereParticles",
    "NumPyJacobi",
    "create",
    "local_module_roots",
    "taichi_available",
    "theta1",
    "theta1_separable",
    "wrap_unit",
]

#: ``create`` 的后端选择。
BACKEND_AUTO = "AUTO"
BACKEND_TAICHI = "TAICHI"
BACKEND_NUMPY = "NUMPY"

#: 零点的上限（物理与显示都一样；参考实现也是 64）。
MAX_ZEROS = 64

#: 默认参数（与参考实现一致；``num_zeros`` 是整数，其余是浮点）。
DEFAULTS = {
    "num_zeros": 8.0,  # 环面上移动的零点个数
    "sphere_radius": 0.12,  # 零点的显示 / 碰撞半径
    "zero_dip_depth": 0.25,  # 零点下方凹陷的深度
    "z_disp_scale": 0.01,  # 相位涟漪的位移幅度
    "tau_re": 0.3,  # 模参数 τ 的实部
    "tau_im": 1.2,  # 模参数 τ 的虚部（> 0）
    "speed": 0.01,  # 每帧物理时间步（运动速度）
    "major_r": 3.0,  # 环面主半径
    "minor_r": 1.2,  # 环面管半径
}

PARAM_KEYS = tuple(DEFAULTS)

#: θ1 级数截断项数（参考实现同样取 6 项）。
THETA_TERMS = 6


def theta1(
    z: np.ndarray, tau_re: float, tau_im: float, terms: int = THETA_TERMS
) -> np.ndarray:
    """θ1(z | τ) 的 Fourier 级数，``z`` 是复数数组（逐点求值，返回同形状复数）。

    ``q = exp(iπτ)`` 直接按复数幂写；``τ_im > 0`` 时 ``|q| < 1``，级数收敛（缺省 τ_im
    下第 6 项已在 1e-9 量级）。
    """
    total = np.zeros_like(z, dtype=np.complex128)
    i_pi_tau = 1j * math.pi * (float(tau_re) + 1j * float(tau_im))
    for n in range(int(terms)):
        half = n + 0.5
        weight = np.exp(i_pi_tau * (half * half))
        term = np.sin((2.0 * n + 1.0) * math.pi * z)
        total += weight * term if n % 2 == 0 else -weight * term  # (-1)^n
    return 2.0 * total


def theta1_separable(
    du: np.ndarray,
    dv: np.ndarray,
    tau_re: float,
    tau_im: float,
    terms: int = THETA_TERMS,
) -> tuple[np.ndarray, np.ndarray]:
    """θ1 的可分离快路径：返回 ``(实部, 虚部)``，形状 ``(du.size, dv.size)``。

    ``du`` 沿 u、``dv`` 沿 v（都是一维），复自变量 ``z = du + τ·dv``。级数里三角与双曲
    项可以拆开::

        sin((2n+1)π(a+b)) = sin A·cos B + cos A·sin B     （A 只依赖 du，B 只依赖 dv）
        sin(x + iy)       = sin x·cosh y + i·cos x·sinh y

    于是每一项只剩一维的 ``sin`` / ``cos`` / ``sinh`` / ``cosh``（nu 或 nv 个点）加两次
    二维外积 —— 比直接对复数数组调 ``np.sin`` 快一个量级（150×100 网格 × 8 零点实测
    24.9 ms → 2.4 ms）。与 :func:`theta1` 逐点等价（float32 下差 1e-6 量级）。
    """
    du = np.ascontiguousarray(du, np.float32).ravel()
    dv = np.ascontiguousarray(dv, np.float32).ravel()
    re = np.zeros((du.size, dv.size), np.float32)
    im = np.zeros((du.size, dv.size), np.float32)

    for n in range(int(terms)):
        half = n + 0.5
        wave = (2.0 * n + 1.0) * math.pi
        sign = 1.0 if n % 2 == 0 else -1.0
        # q^{(n+½)²} = exp(iπτ(n+½)²)：标量，逐项只算一次
        q_mag = math.exp(-math.pi * float(tau_im) * half * half)
        q_phase = math.pi * float(tau_re) * half * half
        weight_re = np.float32(sign * q_mag * math.cos(q_phase))
        weight_im = np.float32(sign * q_mag * math.sin(q_phase))

        # 实部角度 = A(du) + B(dv)，虚部 = C(dv)；三个都只依赖一维
        angle_a = (wave * du).astype(np.float32)
        angle_b = (wave * float(tau_re) * dv).astype(np.float32)
        length_c = (wave * float(tau_im) * dv).astype(np.float32)

        sin_a, cos_a = np.sin(angle_a), np.cos(angle_a)
        sin_b, cos_b = np.sin(angle_b), np.cos(angle_b)
        cosh_c, sinh_c = np.cosh(length_c), np.sinh(length_c)

        # Re/Im 各取一对因子（注意交叉项：Re 用 (q_re·cosh, q_im·sinh)，
        # Im 用 (q_im·cosh, q_re·sinh)），所以四串一维因子只算一次
        re_cos = (weight_re * cosh_c).astype(np.float32)[None, :]
        re_sin = (weight_im * sinh_c).astype(np.float32)[None, :]
        im_cos = (weight_im * cosh_c).astype(np.float32)[None, :]
        im_sin = (weight_re * sinh_c).astype(np.float32)[None, :]

        sin_ab = sin_a[:, None] * cos_b[None, :] + cos_a[:, None] * sin_b[None, :]
        cos_ab = cos_a[:, None] * cos_b[None, :] - sin_a[:, None] * sin_b[None, :]

        re += sin_ab * re_cos - cos_ab * re_sin
        im += sin_ab * im_cos + cos_ab * im_sin

    re *= 2.0
    im *= 2.0
    return re, im


def wrap_unit(offset: np.ndarray) -> np.ndarray:
    """把参数域偏移卷到 ``(−0.5, 0.5]``，与 Taichi 的 ``ti.round``（.5 远离零）逐位一致。

    numpy 的 ``np.round`` 是 .5 取偶：在 ``u − c`` 恰为 ±0.5 的网格线上两边会差 1，而
    ``θ1(z+1) = −θ1(z)``，于是同一帧会差一个整体符号（相位差 π、格纹跟着变）。所以这里
    自己按"远离零"取整。

    ``±0.5`` 的加法必须在 float64 里做：float32 下 ``−0.49999997 − 0.5`` 会四舍五入成
    ``−1.0``（半个 ulp 的锅），于是差一个 ulp 的网格线被误判成"平局"，又回到符号不一致。
    """
    offset = np.asarray(offset, np.float32)
    wide = offset.astype(np.float64)
    rounded = np.where(wide >= 0.0, np.floor(wide + 0.5), np.ceil(wide - 0.5))
    return (wide - rounded).astype(np.float32)


class SphereParticles:
    """环面上的弹性碰撞小球（位置在 ``(u, v)`` 参数域，速度同域）。

    两个后端共用这一层：它只依赖 numpy，结果也就完全一致 —— 换后端不会改变运动。
    """

    def __init__(
        self,
        max_zeros: int = MAX_ZEROS,
        major_r: float = 3.0,
        minor_r: float = 1.2,
        seed: int = 42,
    ):
        self.max_zeros = int(max_zeros)
        self.major_r = float(major_r)
        self.minor_r = float(minor_r)
        self.seed = int(seed)

        #: 当前参与运动的零点个数。
        self.count = 0
        self.pos_u = np.zeros(self.max_zeros, np.float32)
        self.pos_v = np.zeros(self.max_zeros, np.float32)
        self.vel_u = np.zeros(self.max_zeros, np.float32)
        self.vel_v = np.zeros(self.max_zeros, np.float32)

    # --- 参数 ---------------------------------------------------------------

    @staticmethod
    def _clean(params: dict) -> dict:
        """只收下已知参数并转成 float（面板可能多传/少传）。"""
        return {
            key: float(value)
            for key, value in params.items()
            if key in DEFAULTS and value is not None
        }

    def set_params(self, **kwargs) -> None:
        """就地更新运动参数；零点个数变了就重铺初值（与参考实现一致）。"""
        clean = self._clean(kwargs)
        if not clean:
            return
        self.params.update(clean)
        self.major_r = self.params["major_r"]
        self.minor_r = self.params["minor_r"]
        self.resize(int(self.params["num_zeros"]))

    # --- 初始化 -------------------------------------------------------------

    def initialize(self, count: int | None = None) -> None:
        """按参考实现铺初值：正方形网格布局 + 固定种子下的随机速度（可复现）。"""
        count = max(1, min(int(self.count if count is None else count), self.max_zeros))
        rng = np.random.default_rng(self.seed)
        grid = math.ceil(math.sqrt(count))

        for slot in range(count):
            i, j = divmod(slot, grid)
            self.pos_u[slot] = (i + 0.5) / grid
            self.pos_v[slot] = (j + 0.5) / grid
            angle = rng.uniform(0.0, 2.0 * math.pi)
            speed = rng.uniform(0.4, 0.8)
            self.vel_u[slot] = speed * math.cos(angle)
            self.vel_v[slot] = speed * math.sin(angle)

        for slot in range(count, self.max_zeros):
            self.vel_u[slot] = 0.0
            self.vel_v[slot] = 0.0

        self.count = count

    def resize(self, count: int) -> bool:
        """零点数变了才重铺初值，返回是否真的重铺了。"""
        count = max(1, min(int(count), self.max_zeros))
        if count == self.count:
            return False
        self.initialize(count)
        return True

    # --- 几何 ---------------------------------------------------------------

    def centers(self, r_eff: float) -> np.ndarray:
        """零点的三维位置 ``(n, 3)``：环面上半径 ``r_eff`` 处（不含主半径偏移）。"""
        n = self.count
        if n == 0:
            return np.zeros((0, 3), np.float32)
        theta = 2.0 * math.pi * self.pos_u[:n].astype(np.float64)
        phi = 2.0 * math.pi * self.pos_v[:n].astype(np.float64)
        ring = self.major_r + float(r_eff) * np.cos(phi)
        return np.stack(
            [ring * np.cos(theta), ring * np.sin(theta), float(r_eff) * np.sin(phi)],
            axis=1,
        ).astype(np.float32)

    # --- 物理 ---------------------------------------------------------------

    def advance(
        self,
        dt: float = 0.015,
        sphere_radius: float = 0.12,
        restitution: float = 0.95,
        substeps: int = 4,
    ) -> None:
        """推进一个物理步（内部再切 ``substeps`` 个亚步，避免穿透）。"""
        n = self.count
        if n == 0:
            return
        sub_dt = float(dt) / max(1, int(substeps))
        for _ in range(max(1, int(substeps))):
            self.pos_u[:n] = (self.pos_u[:n] + self.vel_u[:n] * sub_dt) % 1.0
            self.pos_v[:n] = (self.pos_v[:n] + self.vel_v[:n] * sub_dt) % 1.0
            self._collide(float(sphere_radius), float(restitution))

    def _collide(self, sphere_radius: float, restitution: float) -> None:
        """成对处理接触（向量化）：法向相对速度 < 0 时交换动量并推开重叠。"""
        n = self.count
        if n < 2:
            return

        contact = 2.0 * float(sphere_radius)
        points = self.centers(self.minor_r + float(sphere_radius))
        i, j = np.triu_indices(n, 1)
        delta = points[j] - points[i]
        dist = np.sqrt(np.einsum("ij,ij->i", delta, delta))
        hit = (dist > 1e-4) & (dist < contact)
        if not hit.any():
            return
        i, j, dist = i[hit], j[hit], dist[hit]

        # 环面参数域上的位移方向（跨接缝时取最短的那一侧）
        du = self.pos_u[j] - self.pos_u[i]
        dv = self.pos_v[j] - self.pos_v[i]
        du = du - np.round(du)
        dv = dv - np.round(dv)
        length = np.sqrt(du * du + dv * dv) + 1e-6
        dir_u = du / length
        dir_v = dv / length

        approach = (self.vel_u[j] - self.vel_u[i]) * dir_u + (
            self.vel_v[j] - self.vel_v[i]
        ) * dir_v
        moving = approach < 0.0
        impulse = np.where(moving, -(1.0 + restitution) * approach, 0.0)

        half_u = 0.5 * impulse * dir_u
        half_v = 0.5 * impulse * dir_v
        np.add.at(self.vel_u, i, -half_u)
        np.add.at(self.vel_u, j, half_u)
        np.add.at(self.vel_v, i, -half_v)
        np.add.at(self.vel_v, j, half_v)

        # 分离校正：把三维重叠折算回参数域（0.3 的松弛系数让画面不抖）
        overlap = np.where(
            moving, 0.15 * (contact - dist) / (2.0 * math.pi * self.minor_r), 0.0
        )
        np.add.at(self.pos_u, i, -overlap * dir_u)
        np.add.at(self.pos_u, j, overlap * dir_u)
        np.add.at(self.pos_v, i, -overlap * dir_v)
        np.add.at(self.pos_v, j, overlap * dir_v)
        np.mod(self.pos_u[:n], 1.0, out=self.pos_u[:n])
        np.mod(self.pos_v[:n], 1.0, out=self.pos_v[:n])

    def step(self, count: int = 1) -> None:
        """按面板参数推进 ``count`` 个物理步（每步 ``dt = speed · 1.5``）。"""
        dt = float(self.params["speed"]) * 1.5
        radius = float(self.params["sphere_radius"])
        for _ in range(max(1, int(count))):
            self.advance(dt, radius)


class NumPyJacobi(SphereParticles):
    """环面 theta 除子求解器（NumPy 后端）。

    场按 ``[i, j]`` 索引（``i`` 沿 u、``j`` 沿 v），展平时是 C 序 —— 与
    ``mesh.torus_geometry`` 的顶点编号 ``i*nv + j`` 一致，所以场可以直接写进顶点属性。
    """

    backend = "numpy"

    def __init__(
        self,
        nu: int = 150,
        nv: int = 100,
        major_r: float = 3.0,
        minor_r: float = 1.2,
        max_zeros: int = MAX_ZEROS,
        params: dict | None = None,
    ):
        super().__init__(max_zeros=max_zeros, major_r=major_r, minor_r=minor_r)
        self.nu = int(nu)
        self.nv = int(nv)

        self.params = dict(DEFAULTS)
        self.params.update(self._clean(params or {}))

        #: 后端说明（面板读数用；NumPy 后端没有可报告的回退原因）。
        self.note = "numpy"

        self.initialize(int(self.params["num_zeros"]))

    # --- 一帧 ---------------------------------------------------------------

    def evaluate(self) -> tuple:
        """算一帧：``(verts(nu·nv,3), amplitude, phase, phase_grid, potential, zeros)``。

        五个场都是按 ``i*nv + j`` 展平的 float32，``zeros`` 是 ``(n, 3)`` 的三维球心。
        """
        nu, nv = self.nu, self.nv
        p = self.params
        n = self.count
        two_pi = 2.0 * math.pi

        u1 = np.arange(nu, dtype=np.float32) / np.float32(nu)
        v1 = np.arange(nv, dtype=np.float32) / np.float32(nv)
        u = u1[:, None]
        v = v1[None, :]

        potential = np.zeros((nu, nv), np.float32)
        phase = np.zeros((nu, nv), np.float32)
        halo = np.zeros((nu, nv), np.float32)

        for k in range(n):
            du = wrap_unit(u1 - np.float32(self.pos_u[k]))
            dv = wrap_unit(v1 - np.float32(self.pos_v[k]))

            th_re, th_im = theta1_separable(du, dv, p["tau_re"], p["tau_im"])
            magnitude = np.sqrt(th_re * th_re + th_im * th_im + 1e-10)
            potential += np.log(magnitude + 1e-4)
            phase += np.arctan2(th_im, th_re)
            # 光晕也可分离：exp(−120(du²+dv²)) = 行因子 × 列因子
            halo += (
                np.exp(-120.0 * du * du)[:, None] * np.exp(-120.0 * dv * dv)[None, :]
            )

        # 高频、极细的旋转菱形相位干涉格纹（参考实现的取值）
        fine_a = np.cos(32.0 * math.pi * (u + v))
        fine_b = np.cos(32.0 * math.pi * (u - v))
        micro = np.sin(48.0 * math.pi * u) * np.cos(48.0 * math.pi * v)
        spiral = np.sin(4.0 * phase)
        lattice = 0.5 + 0.175 * (fine_a + fine_b) + 0.15 * micro * spiral
        phase_grid = lattice * (1.0 - 0.25 * np.tanh(halo))

        # 环面顶点：零点凹陷 + 相位涟漪
        height = -halo * p["zero_dip_depth"] + (phase_grid - 0.5) * p["z_disp_scale"]
        r_eff = p["minor_r"] + height
        theta_ang = two_pi * u
        phi_ang = two_pi * v
        ring = p["major_r"] + r_eff * np.cos(phi_ang)
        verts = np.stack(
            [
                ring * np.cos(theta_ang),
                ring * np.sin(theta_ang),
                r_eff * np.sin(phi_ang),
            ],
            axis=-1,
        ).astype(np.float32)

        zeros = self.centers(
            p["minor_r"] - p["zero_dip_depth"] * 0.5 + p["sphere_radius"]
        )
        return (
            verts.reshape(-1, 3),
            halo.astype(np.float32).ravel(),
            phase.astype(np.float32).ravel(),
            phase_grid.astype(np.float32).ravel(),
            potential.astype(np.float32).ravel(),
            zeros,
        )


def taichi_available() -> bool:
    """本进程能否 ``import taichi``。

    第一次调用要把 taichi 加载进来（几百毫秒），别在面板 ``draw`` 里调。
    """
    try:
        import taichi  # noqa: F401
    except Exception:
        return False
    return True


def local_module_roots() -> list[str]:
    """第三方库的兜底查找路径（只在 ``import taichi`` 失败时才用）。

    正常启动（带 ``BLENDER_USER_RESOURCES``）时 Blender 会把
    ``$BLENDER_USER_RESOURCES/scripts/modules`` 加进 ``sys.path``，用不着这段逻辑；但
    ``--factory-startup`` 不读用户偏好，那一份路径也会丢 —— 后台测试 / CI 常这么启动，
    于是就会「明明装了 taichi 却回退 NumPy」。这里按环境变量与便携版目录再补一次，
    不改动已有环境；官方扩展依赖目录 ``extensions/.local/lib/python<X.Y>/site-packages``
    本来就在 ``sys.path`` 上，只是顺手一起兜底。
    """
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    roots = []
    user = os.environ.get("BLENDER_USER_RESOURCES")
    if user:
        roots.append(os.path.join(user, "scripts", "modules"))
        roots.append(
            os.path.join(user, "extensions", ".local", "lib", version, "site-packages")
        )
    appdata = os.environ.get("APPDATA")
    if appdata:
        pattern = os.path.join(
            appdata, "Blender Foundation", "Blender", "*", "scripts", "modules"
        )
        roots.extend(sorted(glob.glob(pattern), reverse=True))
    return [root for root in roots if os.path.isdir(root)]


def create(
    nu: int,
    nv: int,
    major_r: float = 3.0,
    minor_r: float = 1.2,
    backend: str = BACKEND_AUTO,
    params: dict | None = None,
):
    """建求解器：``AUTO`` 优先 Taichi GPU，取不到就退回 NumPy。

    ``backend`` 显式给 ``TAICHI`` 时初始化失败会抛异常（面板会显示原因），给 ``NUMPY``
    就永远走纯 NumPy。回退原因写在实例的 ``note`` 上。
    """
    choice = str(backend).upper()
    if choice in (BACKEND_AUTO, BACKEND_TAICHI):
        try:
            from .taichi_solver import TaichiJacobi

            return TaichiJacobi(nu, nv, major_r, minor_r, params=params)
        except Exception as exc:
            if choice == BACKEND_TAICHI:
                raise RuntimeError(f"Taichi 后端不可用: {exc}") from exc
            solver = NumPyJacobi(nu, nv, major_r, minor_r, params=params)
            solver.note = f"Taichi 不可用（{exc}），已回退 NumPy"
            return solver
    return NumPyJacobi(nu, nv, major_r, minor_r, params=params)
