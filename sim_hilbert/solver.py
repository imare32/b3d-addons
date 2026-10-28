"""弹簧质点求解器（纯 NumPy 后端 + 后端工厂）。

模型就是最朴素的那一套 —— 顶点是质点、弹簧是阻尼弹簧、地面是碰撞平面::

    F_i = m·g + Σ_j [ −k·(|p_i − p_j| − L_ij) − c·(v_i − v_j)·n̂_ij ] · n̂_ij
    v_i += (F_i / m)·dt
    p_i += v_i·dt
    p_i.z < z_ground  →  p_i.z = z_ground, v_i.z *= −e, v_i.xy *= (1 − μ)

与参考实现（``ga2/common/mass_spring.py``，Taichi kernel）逐式一致，只把 kernel 换成
**向量化 NumPy**：

* 一次算出全部弹簧的力（``(M,3)`` 的 gather → 范数 → 轴向力），再用 ``np.bincount``
  把力散射回顶点 —— 两端各加一次，等价于 kernel 里的 ``force[a] += f; force[b] -= f``；
* 地面碰撞是逐顶点布尔掩码，写在积分之后（与参考实现同顺序：先走一步再钳位，所以
  穿地面的那一小段会被"推"回平面上，表现为一点软接触）；
* ``dt`` 是**子步**时长，每帧走 ``substeps`` 个子步 —— 显式欧拉，别把 ``dt`` 调大，
  改大先看到的是弹簧网络爆成噪点。

为什么给两个后端：4 阶（4096 顶点 / 4.7 万根弹簧）在 NumPy 上每帧几百毫秒，只有
Taichi 后端（:mod:`sim_hilbert.taichi_solver`）能实时；3 阶（512 顶点 / 5 千根弹簧）
NumPy 就够。两者 API 相同，由 :func:`create` 按需选择，本模块**不 import taichi**，
所以可以脱离 Blender 与 GPU 单独跑。
"""

from __future__ import annotations

import numpy as np

__all__ = [
    "BACKEND_AUTO",
    "BACKEND_NUMPY",
    "BACKEND_TAICHI",
    "DEFAULTS",
    "PARAM_KEYS",
    "NumPyMassSpring",
    "create",
    "taichi_available",
]

#: ``create`` 的后端选择。
BACKEND_AUTO = "AUTO"
BACKEND_TAICHI = "TAICHI"
BACKEND_NUMPY = "NUMPY"

#: 默认物理参数（刚度 / 阻尼 / 质量与参考实现一致，重力取标准值）。
DEFAULTS = {
    "stiffness": 10000.0,  # 弹簧刚度 k
    "damping": 15.0,  # 弹簧阻尼 c（沿弹簧轴的分量）
    "mass": 1.0,  # 质点质量 m
    "dt": 2e-4,  # 子步时长 [s]
    "gravity": 9.80665,  # 重力加速度 [m/s²]
    "ground_height": 0.0,  # 地面 z
    "restitution": 0.8,  # 地面弹性（0 = 完全吸收，1 = 完全反弹）
    "friction": 0.3,  # 地面摩擦（1 − μ 乘在切向速度上）
}

PARAM_KEYS = tuple(DEFAULTS)

#: 弹簧长度 / 方向的下限：两端重合时不至于除零（跳过一个子步的力即可）。
_EPS = 1e-9


class NumPyMassSpring:
    """弹簧质点求解器（NumPy 后端）。

    状态全在 ``float64`` 上（显式欧拉对舍入误差敏感），导出给 Blender 时再转 f32。
    """

    backend = "numpy"

    def __init__(
        self,
        positions: np.ndarray,
        spring_pairs: np.ndarray,
        spring_lengths: np.ndarray,
        params: dict | None = None,
    ):
        self.pos0 = np.ascontiguousarray(positions, np.float64).copy()
        self.n = int(self.pos0.shape[0])
        self.pos = self.pos0.copy()
        self.vel = np.zeros_like(self.pos)
        self.force = np.zeros_like(self.pos)

        pairs = np.ascontiguousarray(spring_pairs, np.int64)
        self.i = np.ascontiguousarray(pairs[:, 0])
        self.j = np.ascontiguousarray(pairs[:, 1])
        self.rest = np.ascontiguousarray(spring_lengths, np.float64)
        self.m = len(self.i)

        # 散射用的静态缓冲：gather = 两端顶点编号拼起来，buf = 两端受力拼起来
        self._gather = np.concatenate((self.i, self.j))
        self._buf = np.zeros((2 * self.m, 3), np.float64)
        #: 每个顶点参与的弹簧数（算平均应变用）。
        self._incident = np.maximum(
            np.bincount(self._gather, minlength=self.n), 1
        ).astype(np.float64)

        self.params = dict(DEFAULTS)
        self.set_params(**(params or {}))
        self.note = "numpy"

    # --- 参数 ---------------------------------------------------------------

    def set_params(self, **kwargs) -> None:
        """就地更新物理参数（下一子步生效，不重置状态）。"""
        for key, value in kwargs.items():
            if key in DEFAULTS and value is not None:
                self.params[key] = float(value)
        p = self.params
        self.stiffness = p["stiffness"]
        self.damping = p["damping"]
        self.mass = max(p["mass"], 1e-9)
        self.dt = p["dt"]
        self.gravity_z = -abs(p["gravity"])
        self.ground = p["ground_height"]
        self.restitution = p["restitution"]
        self.friction_scale = 1.0 - p["friction"]

    # --- 状态 ---------------------------------------------------------------

    def reset(self) -> None:
        """回到初始格子（速度归零，不重建缓冲）。"""
        np.copyto(self.pos, self.pos0)
        self.vel[:] = 0.0
        self.force[:] = 0.0

    def positions(self) -> np.ndarray:
        """当前顶点位置 ``(N,3)`` float32（副本，直接写进 Blender）。"""
        return self.pos.astype(np.float32)

    def speed(self) -> np.ndarray:
        """逐顶点速度大小 ``(N,)`` float32。"""
        return np.sqrt(np.einsum("ij,ij->i", self.vel, self.vel)).astype(np.float32)

    def strain(self) -> np.ndarray:
        """逐顶点平均应变 ``|L − L0| / L0``（每顶点按参与的弹簧数取平均）。"""
        deltas = self.pos[self.i] - self.pos[self.j]
        dist = np.sqrt(np.einsum("ij,ij->i", deltas, deltas))
        value = np.abs(dist - self.rest) / np.maximum(self.rest, _EPS)
        total = np.bincount(
            self._gather, weights=np.concatenate((value, value)), minlength=self.n
        )
        return (total / self._incident).astype(np.float32)

    # --- 物理 ---------------------------------------------------------------

    def _substep(self) -> None:
        """一个显式欧拉子步：弹簧力 → 重力 → 积分 → 地面钳位。"""
        pos, vel = self.pos, self.vel
        i, j = self.i, self.j

        deltas = pos[i] - pos[j]
        dist = np.sqrt(np.einsum("ij,ij->i", deltas, deltas))
        direction = deltas * np.reciprocal(np.maximum(dist, _EPS))[:, None]
        relative = vel[i] - vel[j]
        along = np.einsum("ij,ij->i", relative, direction)
        magnitude = -self.stiffness * (dist - self.rest) - self.damping * along

        force = magnitude[:, None] * direction
        buf = self._buf
        buf[: self.m] = force
        np.negative(force, out=buf[self.m :])

        # 散射：两端顶点各收一份（bincount 是 NumPy 里最快的定点累加）
        self.force[:] = 0.0
        for axis in range(3):
            self.force[:, axis] += np.bincount(
                self._gather, weights=buf[:, axis], minlength=self.n
            )
        self.force[:, 2] += self.mass * self.gravity_z

        scale = self.dt / self.mass
        vel += self.force * scale
        pos += vel * self.dt

        below = pos[:, 2] < self.ground
        if below.any():
            pos[below, 2] = self.ground
            vel[below, 2] *= -self.restitution
            vel[below, 0] *= self.friction_scale
            vel[below, 1] *= self.friction_scale

    def step(self, substeps: int = 1) -> None:
        """推进 ``substeps`` 个子步（每帧推进量 = 子步数 × ``dt``）。"""
        for _ in range(max(0, int(substeps))):
            self._substep()


def taichi_available() -> bool:
    """本进程能否 ``import taichi``（第一次调用要把 taichi 加载进来，别在面板 draw 里调）。"""
    try:
        import taichi  # noqa: F401
    except Exception:
        return False
    return True


def create(
    positions: np.ndarray,
    spring_pairs: np.ndarray,
    spring_lengths: np.ndarray,
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
            from .taichi_solver import TaichiMassSpring

            return TaichiMassSpring(
                positions, spring_pairs, spring_lengths, params=params
            )
        except Exception as exc:
            if choice == BACKEND_TAICHI:
                raise RuntimeError(f"Taichi 后端不可用: {exc}") from exc
            solver = NumPyMassSpring(
                positions, spring_pairs, spring_lengths, params=params
            )
            solver.note = f"Taichi 不可用（{exc}），已回退 NumPy"
            return solver
    if choice == BACKEND_NUMPY:
        return NumPyMassSpring(positions, spring_pairs, spring_lengths, params=params)
    raise ValueError(f"unknown backend {backend!r}")
