"""Warren 模型的相场晶枝生长求解器（纯 NumPy 后端 + 后端工厂）。

相场 ``φ``（0 = 液相，1 = 固相）与无量纲过冷度 ``ΔT = T − T_m`` 耦合::

    τ ∂φ/∂t = ∇·(D ∇φ) + φ(1−φ)·[φ − 1/2 − (κ1/π)·arctan(κ2·ΔT)]
    ∂ΔT/∂t  = D_T·∇²ΔT + ∂φ/∂t

各向异性体现在扩散张量 ``D`` 上（界面刚度对法向角取 ``N`` 次对称）::

    ψ  = θ0 + arctan2(∂_yφ, ∂_xφ)
    β  = cos(N·ψ)
    M  = α²·(1 + c·β)
    D  = M · [[1 + cβ, −∂_ψβ], [∂_ψβ, 1 + cβ]]

这里把 ``cos`` / ``sin`` 换成了 ``tan(Nψ/2)`` 的有理形式（``β = (1−q)/(1+q)``，
``∂_ψβ = −2N·Φ/(1+q)``，``q = Φ²``，``Φ = tan(Nψ/2)``），与参考实现
``.ref/dendrite_growth.py``（Taichi）逐式一致 —— 只把 kernel 换成按**面通量**矢量化的
NumPy：每个子步只在 ``(nx+1)×ny`` 与 ``nx×(ny+1)`` 两个面网格上各算一次
``arctan2`` / ``tan``。

显式欧拉 + 时间步 ``dt``；``phi`` 每步夹到 ``[0, 1]``，边界是零通量的 Neumann
（对网格做 edge padding，等价于参考实现里 ``get_val`` 的索引钳位）。

Taichi 后端在 :mod:`sim_dendrite.taichi_solver`（同一套方程，GPU kernel），两者 API
相同，由 :func:`create` 按需选择；本模块**不 import taichi**，所以可以脱离 Blender 与
GPU 单独跑（系统 Python + numpy 即可）。
"""

from __future__ import annotations

import math

import numpy as np

__all__ = [
    "BACKEND_AUTO",
    "BACKEND_NUMPY",
    "BACKEND_TAICHI",
    "DEFAULTS",
    "PARAM_KEYS",
    "NumPyPhaseField",
    "create",
    "taichi_available",
]

#: ``create`` 的后端选择。
BACKEND_AUTO = "AUTO"
BACKEND_TAICHI = "TAICHI"
BACKEND_NUMPY = "NUMPY"

#: 默认物理参数（与参考实现一致：6 次对称的雪花状晶枝）。
DEFAULTS = {
    "DT_heat": 2.25,  # 热扩散 / 潜热系数
    "alpha": 0.015,  # 界面宽度尺度
    "c_ani": 0.02,  # 各向异性强度
    "N_sym": 6.0,  # 旋转对称次数（6 = 雪花，4 = 立方）
    "theta_off": math.pi / 8.0,  # 晶向偏角
    "tau": 3e-4,  # 相场弛豫时间
    "kappa1": 0.9,  # 过冷度耦合 1
    "kappa2": 20.0,  # 过冷度耦合 2
    "dt": 2e-5,  # 显式欧拉时间步
}

PARAM_KEYS = tuple(DEFAULTS)

#: ``tan(Nψ/2)`` 在 ``ψ = π/N`` 处发散：钳到 ±1e5 后 ``q = 1e10`` 正好落在参考实现的
#: 奇异判据上（``q > 1e10`` 才特殊处理），结果等价但不会出现 inf/inf。
_PHI_LIMIT = 1e5


class NumPyPhaseField:
    """二维 Warren 相场求解器（NumPy 后端）。

    字段按 ``[i, j]`` 索引（``i`` 沿 x、``j`` 沿 y），展平时是 C 序 —— 与
    ``mesh.grid_geometry`` 的顶点编号 ``i*ny + j`` 一致，所以 ``phi_flat()`` 可以直接
    写进顶点属性。
    """

    backend = "numpy"

    def __init__(
        self,
        nx: int = 250,
        ny: int = 250,
        dx: float = 0.025,
        dy: float = 0.025,
        params: dict | None = None,
    ):
        self.nx = int(nx)
        self.ny = int(ny)
        self.dx = float(dx)
        self.dy = float(dy)

        self.params = dict(DEFAULTS)
        self.params.update(self._clean(params or {}))

        shape = (self.nx, self.ny)
        self._phi = np.zeros(shape, np.float32)
        self._dT = np.zeros(shape, np.float32)
        self._new_phi = np.zeros(shape, np.float32)
        self._new_dT = np.zeros(shape, np.float32)

        #: 后端说明（面板读数用；NumPy 后端没有可报告的回退原因）。
        self.note = "numpy"

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
        """就地更新物理参数（下一子步生效，不重置场）。"""
        self.params.update(self._clean(kwargs))

    # --- 初始化 -------------------------------------------------------------

    def initialize(
        self,
        seed_radius_units: float = 5.0,
        center: tuple[float, float] | None = None,
        seed_points: np.ndarray | None = None,
        initial_dT: float = -0.5,
    ) -> None:
        """铺初始场：中心一个圆形晶核（可选再加若干点位晶核），液体整体过冷。

        ``seed_points`` 是归一化到 ``[-1, 1]`` 的二维坐标（参考网格顶点的 XY 投影），
        ``seed_radius_units`` 同时是这些核的半径（网格格数）。
        """
        nx, ny, dx, dy = self.nx, self.ny, self.dx, self.dy
        radius = dx * float(seed_radius_units)

        self._phi.fill(0.0)
        self._dT.fill(float(initial_dT))

        if center is None:
            center_x = nx * dx * 0.5
            center_y = ny * dy * 0.5
        else:
            center_x, center_y = float(center[0]), float(center[1])

        xs = (np.arange(nx, dtype=np.float32) * dx - center_x) ** 2
        ys = (np.arange(ny, dtype=np.float32) * dy - center_y) ** 2
        self._phi[xs[:, None] + ys[None, :] < radius * radius] = 1.0

        if seed_points is not None and len(seed_points) > 0:
            self._seed_points(seed_points, max(1, int(seed_radius_units)))

    def _seed_points(self, points: np.ndarray, radius_cells: int) -> None:
        """在自定义点位（归一化坐标）上各放一个圆核。"""
        pts = np.ascontiguousarray(points, dtype=np.float32).reshape(-1, 2)
        ci = np.clip(
            ((pts[:, 0] + 1.0) * 0.5 * self.nx).astype(np.int32), 0, self.nx - 1
        )
        cj = np.clip(
            ((pts[:, 1] + 1.0) * 0.5 * self.ny).astype(np.int32), 0, self.ny - 1
        )

        offsets = np.arange(-radius_cells, radius_cells + 1)
        di, dj = np.meshgrid(offsets, offsets, indexing="ij")
        inside = di**2 + dj**2 <= radius_cells**2
        di = di[inside]
        dj = dj[inside]

        for x, y in zip(ci, cj):
            rows = np.clip(x + di, 0, self.nx - 1)
            cols = np.clip(y + dj, 0, self.ny - 1)
            self._phi[rows, cols] = 1.0

    # --- 物理 ---------------------------------------------------------------

    def _anisotropy(self, grad_x: np.ndarray, grad_y: np.ndarray) -> tuple:
        """面网格上的 ``(M·(1+cβ), M·c·∂_ψβ)``：即 ``Dxx`` 与偏导项的幅值。"""
        p = self.params
        n_sym = p["N_sym"]
        psi = p["theta_off"] + np.arctan2(grad_y, grad_x)
        phi = np.clip(np.tan(n_sym * psi * 0.5), -_PHI_LIMIT, _PHI_LIMIT)
        q = phi * phi
        beta = (1.0 - q) / (1.0 + q)
        dbeta = -n_sym * 2.0 * phi / (1.0 + q)

        c_ani = p["c_ani"]
        stiffness = p["alpha"] ** 2 * (1.0 + c_ani * beta)
        return stiffness * (1.0 + c_ani * beta), stiffness * (c_ani * dbeta)

    def _substep(self) -> None:
        """一个显式欧拉子步：各向异性相场 + 热扩散（含潜热）。"""
        p = self.params
        nx, ny = self.nx, self.ny
        dx, dy = self.dx, self.dy
        phi, dT = self._phi, self._dT

        # 零通量边界 = 索引钳位：pad 一圈 edge，之后所有偏移都是一次切片
        phi_pad = np.pad(phi, 1, mode="edge")
        dT_pad = np.pad(dT, 1, mode="edge")

        # x 向面通量：面索引 k = 0..nx（k = i+1 是格 i 的右面；两端在域外，法向梯度为 0）
        left = phi_pad[0 : nx + 1, 1 : ny + 1]
        right = phi_pad[1 : nx + 2, 1 : ny + 1]
        grad_y = (
            0.5
            * (
                (phi_pad[0 : nx + 1, 2 : ny + 2] - phi_pad[0 : nx + 1, 0:ny])
                + (phi_pad[1 : nx + 2, 2 : ny + 2] - phi_pad[1 : nx + 2, 0:ny])
            )
            / (2.0 * dy)
        )
        grad_x = (right - left) / dx
        diag, off = self._anisotropy(grad_x, grad_y)
        flux_x = diag * grad_x - off * grad_y

        # y 向面通量：面索引 m = 0..ny（m = j+1 是格 j 的上表面）
        lower = phi_pad[1 : nx + 1, 0 : ny + 1]
        upper = phi_pad[1 : nx + 1, 1 : ny + 2]
        grad_x = (
            0.5
            * (
                (phi_pad[2 : nx + 2, 0 : ny + 1] - phi_pad[0:nx, 0 : ny + 1])
                + (phi_pad[2 : nx + 2, 1 : ny + 2] - phi_pad[0:nx, 1 : ny + 2])
            )
            / (2.0 * dx)
        )
        grad_y = (upper - lower) / dy
        diag, off = self._anisotropy(grad_x, grad_y)
        flux_y = off * grad_x + diag * grad_y

        div_flux = (flux_x[1 : nx + 1, :] - flux_x[0:nx, :]) / dx + (
            flux_y[:, 1 : ny + 1] - flux_y[:, 0:ny]
        ) / dy

        driving = (
            phi - 0.5 - (p["kappa1"] / math.pi) * np.arctan2(p["kappa2"] * dT, 1.0)
        )
        dphi_dt = (div_flux + phi * (1.0 - phi) * driving) / p["tau"]
        np.clip(phi + dphi_dt * p["dt"], 0.0, 1.0, out=self._new_phi)

        lap_T = (
            dT_pad[2 : nx + 2, 1 : ny + 1]
            + dT_pad[0:nx, 1 : ny + 1]
            + dT_pad[1 : nx + 1, 2 : ny + 2]
            + dT_pad[1 : nx + 1, 0:ny]
            - 4.0 * dT
        ) / (dx * dx)
        self._new_dT[...] = dT + (p["DT_heat"] * lap_T + dphi_dt) * p["dt"]

        self._phi, self._new_phi = self._new_phi, self._phi
        self._dT, self._new_dT = self._new_dT, self._dT

    def step(self, substeps: int = 1) -> None:
        """推进 ``substeps`` 个时间步（每帧的推进量由面板的「子步」给）。"""
        for _ in range(max(1, int(substeps))):
            self._substep()

    # --- 导出 ---------------------------------------------------------------

    def phi_flat(self) -> np.ndarray:
        """相场展平成顶点顺序（``index = i*ny + j``，C 序）。"""
        return self._phi.ravel()

    def dT_flat(self) -> np.ndarray:
        """过冷度展平成顶点顺序（同上）。"""
        return self._dT.ravel()

    def fields(self) -> tuple[np.ndarray, np.ndarray]:
        """``(phi, dT)`` 的展平副本（每帧只看一眼时用这个，避免误改内部状态）。"""
        return self._phi.ravel().copy(), self._dT.ravel().copy()


def taichi_available() -> bool:
    """本进程能否 ``import taichi``。

    第一次调用要把 taichi 加载进来（几百毫秒），别在面板 ``draw`` 里调。
    """
    try:
        import taichi  # noqa: F401
    except Exception:
        return False
    return True


def create(
    nx: int,
    ny: int,
    dx: float,
    dy: float,
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
            from .taichi_solver import TaichiPhaseField

            return TaichiPhaseField(nx, ny, dx, dy, params=params)
        except Exception as exc:
            if choice == BACKEND_TAICHI:
                raise RuntimeError(f"Taichi 后端不可用: {exc}") from exc
            solver = NumPyPhaseField(nx, ny, dx, dy, params=params)
            solver.note = f"Taichi 不可用（{exc}），已回退 NumPy"
            return solver
    return NumPyPhaseField(nx, ny, dx, dy, params=params)
