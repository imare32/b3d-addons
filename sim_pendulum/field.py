"""双摆相空间场（纯 NumPy，不 import bpy）。

一个 N×N 的**初值阵列**：每格一个理想双摆（无阻尼、无摩擦），角度以向下为 0、
逆时针为正，两根杆都画在 XZ 平面上（Y 恒为 0，正面看就是一张平面相图）。

两种初值排布（面板里的「初值」）::

    ANGLES  θ1 沿 X、θ2 沿 Z，角速度全为 0
            —— 摆角平面（configuration space）：一眼看出"哪些初值会翻过去"
    PHASE   θ1 沿 X、dθ1 沿 Z（θ2 = 0、dθ2 = 0）
            —— 相空间切片：同步的摆列变成一条条随时间打结的丝带

运动方程（拉格朗日量导出，与 ``.lab/double_pendulum.py`` 的 Taichi kernel 同源）::

    Δ = θ1 − θ2
    D = 2·m1 + m2 − m2·cos(2Δ)
    θ̈1 = [ −g(2m1+m2)sinθ1 − m2·g·sin(θ1−2θ2)
            − 2sinΔ·m2·(dθ2²·l2 + dθ1²·l1·cosΔ) ] / (l1·D)
    θ̈2 = 2sinΔ·[ dθ1²·l1(m1+m2) + g(m1+m2)cosθ1 + dθ2²·l2·m2·cosΔ ] / (l2·D)

积分用**真正的 RK4**（参考脚本那版把 k2/k3 的 θ 导数混进了 k1 的角加速度，本实现按
``y' = f(y)`` 的标准四阶公式写，能量守恒好得多，见 README 的漂移实测）。

能量（用来给面板做守恒读数，不参与积分）::

    T = ½(m1+m2)l1²dθ1² + ½m2 l2²dθ2² + m2 l1 l2 dθ1 dθ2 cos(θ1−θ2)
    V = −(m1+m2) g l1 cosθ1 − m2 g l2 cosθ2
    E = T + V

本模块只依赖 numpy：可以脱离 Blender 直接跑（见 README）。

两个后端：本文件是纯 NumPy 版（默认，零依赖）；:mod:`sim_pendulum.taichi_field` 是同一套
公式的 Taichi GPU 版（可选，取不到就回退）。:func:`create` 是选择器，用法与
``sim_jacobi.solver.create`` 一致。

**为什么 GPU 版能把 1 万摆压进 60 fps**：这里的每个摆彼此完全独立，正好是 GPU 的甜点；
更重要的是 RK4 的四段求值和"五笔点列/半径"的组装可以塞进**同一个 kernel**，每帧只回拷
一次（~800 KB）。NumPy 版每帧要调 20 次 `accelerations()`（1 万元素上每次 437 µs，
其中三角函数 163 µs），加起来就是 10 ms 量级 —— 见 README 的实测表。
"""

from __future__ import annotations

import glob
import os
import sys

import numpy as np

__all__ = [
    "BACKEND_AUTO",
    "BACKEND_NUMPY",
    "BACKEND_TAICHI",
    "DEFAULTS",
    "INIT_ANGLES",
    "INIT_MODES",
    "INIT_PHASE",
    "KEY_POINTS",
    "MAX_N",
    "PhaseField",
    "accelerations",
    "create",
    "energy",
    "local_module_roots",
    "taichi_available",
]

#: :func:`create` 的后端选择。
BACKEND_AUTO = "AUTO"
BACKEND_TAICHI = "TAICHI"
BACKEND_NUMPY = "NUMPY"

#: 网格边长上限：1024² = 104 万个双摆。分形图案要 N≈100 才读得出来；再往上（"一像素一个
#: 双摆"的点阵画法）就要靠 GPU 了 —— 百万摆的积分在 GPU 上 0.1 ms，瓶颈是回拷和摊缓冲。
#: NumPy 后端在 N>128 时会慢到不可用（面板会提示切 Taichi）。
MAX_N = 1024

#: 场给的关键点个数：悬挂点 → 关节 → 摆锤（笔划怎么排是 ``strokes.py`` 的事）。
KEY_POINTS = 3

INIT_ANGLES = "ANGLES"
INIT_PHASE = "PHASE"

#: 面板枚举项（``(标识, 名称, 说明)``）。
INIT_MODES = [
    (
        INIT_ANGLES,
        "摆角平面 θ1–θ2",
        "每格一个 (θ1, θ2) 初值、角速度为 0：上排几乎不动，对角线上翻得最凶",
    ),
    (
        INIT_PHASE,
        "相空间 θ1–dθ1",
        "θ2 = 0、dθ2 = 0，横轴是 θ1、纵轴是初角速度 dθ1：真正的相空间切片",
    ),
]

#: 默认物理参数（无阻尼双摆，重力取标准值）。
DEFAULTS = {
    "gravity": 9.80665,  # 重力加速度 [m/s²]
    "l1": 1.0,  # 上摆杆长 [m]
    "l2": 1.0,  # 下摆杆长 [m]
    "m1": 1.0,  # 上摆质量 [kg]
    "m2": 1.0,  # 下摆质量 [kg]
}


def accelerations(th1, th2, dth1, dth2, gravity, l1, l2, m1, m2):
    """角加速度 ``(θ̈1, θ̈2)``，逐元素向量化（输入可以是标量数组）。"""
    delta = th1 - th2
    sin_delta = np.sin(delta)
    cos_delta = np.cos(delta)
    # 分母 D = 2m1 + m2 − m2·cos(2Δ)，恒 ≥ 2m1 > 0，不会除零
    denominator = 2.0 * m1 + m2 - m2 * np.cos(2.0 * delta)

    numerator1 = (
        -gravity * (2.0 * m1 + m2) * np.sin(th1)
        - m2 * gravity * np.sin(th1 - 2.0 * th2)
        - 2.0 * sin_delta * m2 * (dth2 * dth2 * l2 + dth1 * dth1 * l1 * cos_delta)
    )
    ddth1 = numerator1 / (l1 * denominator)

    numerator2 = (
        2.0
        * sin_delta
        * (
            dth1 * dth1 * l1 * (m1 + m2)
            + gravity * (m1 + m2) * np.cos(th1)
            + dth2 * dth2 * l2 * m2 * cos_delta
        )
    )
    ddth2 = numerator2 / (l2 * denominator)
    return ddth1, ddth2


def energy(th1, th2, dth1, dth2, gravity, l1, l2, m1, m2):
    """每个双摆的总机械能 ``E = T + V``（阵列同形，单位 J）。"""
    kinetic = (
        0.5 * (m1 + m2) * l1 * l1 * dth1 * dth1
        + 0.5 * m2 * l2 * l2 * dth2 * dth2
        + m2 * l1 * l2 * dth1 * dth2 * np.cos(th1 - th2)
    )
    potential = -(m1 + m2) * gravity * l1 * np.cos(th1) - m2 * gravity * l2 * np.cos(
        th2
    )
    return kinetic + potential


class PhaseField:
    """N×N 双摆阵列的状态与积分器。

    状态全在 ``float64`` 上（RK4 的舍入误差在这里最省心），导出给 Blender 时再转 f32。
    """

    #: 后端标识（面板读数用）；GPU 版见 :mod:`sim_pendulum.taichi_field`。
    backend = "numpy"
    note = "numpy"

    def __init__(
        self,
        n: int = 16,
        init: str = INIT_ANGLES,
        spacing: float = 2.4,
        spin_max: float = 9.0,
        margin: float = 1e-2,
        **params,
    ):
        self.n = int(max(2, min(int(n), MAX_N)))
        self.count = self.n * self.n
        self.init = str(init)
        self.spacing = float(spacing)
        self.spin_max = float(spin_max)
        self.margin = float(margin)
        self.steps = 0
        self.set_params(**params)

        # 格子的世界坐标：参数 1（i）沿 X，参数 2（j）沿 Z，整体居中
        index_i, index_j = np.meshgrid(
            np.arange(self.n), np.arange(self.n), indexing="ij"
        )
        self._i = index_i.reshape(-1)
        self._j = index_j.reshape(-1)
        half = 0.5 * (self.n - 1)
        self.x = (self._i - half) * self.spacing
        self.z = (self._j - half) * self.spacing

        # 悬挂点 (count,3)
        self.base = np.zeros((self.count, 3), np.float64)
        self.base[:, 0] = self.x
        self.base[:, 2] = self.z

        # 导出缓冲（每帧覆写，不再分配）；点阵画法用不到它，所以推迟到第一次 points()
        self._points = None

        span = max(np.pi - self.margin, 1e-6)
        angles = np.linspace(-span, span, self.n)
        self._first = angles[self._i]
        self._second = angles[self._j]

        self.reset()

    # --- 参数 ---------------------------------------------------------------

    def set_params(self, **kwargs) -> None:
        """就地更新物理参数（下一子步生效，不重置状态、不改拓扑）。"""
        values = dict(DEFAULTS)
        values.update(self.params if hasattr(self, "params") else {})
        for key, value in kwargs.items():
            if key in DEFAULTS and value is not None:
                values[key] = float(value)
        self.params = values
        self.gravity = values["gravity"]
        self.l1 = max(values["l1"], 1e-3)
        self.l2 = max(values["l2"], 1e-3)
        self.m1 = max(values["m1"], 1e-3)
        self.m2 = max(values["m2"], 1e-3)

    # --- 初值 ---------------------------------------------------------------

    def reset(self) -> None:
        """回到初始阵列（第 0 步），不重建拓扑。"""
        zeros = np.zeros(self.count, np.float64)
        if self.init == INIT_PHASE:
            # 横轴 θ1、纵轴初角速度 dθ1；下摆竖直向下且静止
            self.th1 = self._first.copy()
            self.th2 = zeros.copy()
            self.dth1 = self._second / max(np.pi - self.margin, 1e-6) * self.spin_max
            self.dth2 = zeros.copy()
        else:
            self.th1 = self._first.copy()
            self.th2 = self._second.copy()
            self.dth1 = zeros.copy()
            self.dth2 = zeros.copy()
        self.th1_0 = self.th1.copy()
        self.th2_0 = self.th2.copy()
        self.dth1_0 = self.dth1.copy()
        self.dth2_0 = self.dth2.copy()
        self.steps = 0

    # --- 积分 ---------------------------------------------------------------

    def _derivatives(self, th1, th2, dth1, dth2):
        ddth1, ddth2 = accelerations(
            th1, th2, dth1, dth2, self.gravity, self.l1, self.l2, self.m1, self.m2
        )
        return dth1, dth2, ddth1, ddth2

    def _rk4(self, dt: float) -> None:
        """一步四阶 Runge–Kutta（``y' = f(y)`` 的标准写法）。"""
        th1, th2, dth1, dth2 = self.th1, self.th2, self.dth1, self.dth2
        half = 0.5 * dt

        k1 = self._derivatives(th1, th2, dth1, dth2)
        k2 = self._derivatives(
            th1 + half * k1[0],
            th2 + half * k1[1],
            dth1 + half * k1[2],
            dth2 + half * k1[3],
        )
        k3 = self._derivatives(
            th1 + half * k2[0],
            th2 + half * k2[1],
            dth1 + half * k2[2],
            dth2 + half * k2[3],
        )
        k4 = self._derivatives(
            th1 + dt * k3[0], th2 + dt * k3[1], dth1 + dt * k3[2], dth2 + dt * k3[3]
        )

        sixth = dt / 6.0
        self.th1 = th1 + sixth * (k1[0] + 2.0 * k2[0] + 2.0 * k3[0] + k4[0])
        self.th2 = th2 + sixth * (k1[1] + 2.0 * k2[1] + 2.0 * k3[1] + k4[1])
        self.dth1 = dth1 + sixth * (k1[2] + 2.0 * k2[2] + 2.0 * k3[2] + k4[2])
        self.dth2 = dth2 + sixth * (k1[3] + 2.0 * k2[3] + 2.0 * k3[3] + k4[3])

    def step(self, dt: float = 0.005, substeps: int = 1) -> None:
        """推进 ``substeps`` 个 RK4 子步，每个子步 ``dt`` 秒。"""
        dt = float(dt)
        for _ in range(max(1, int(substeps))):
            self._rk4(dt)
        self.steps += max(1, int(substeps))

    # --- 场 → 几何 ----------------------------------------------------------

    @property
    def reach(self) -> float:
        """单摆最大伸展半径（取景要用）。"""
        return self.l1 + self.l2

    def dot_points(self) -> np.ndarray:
        """``(count, 3)`` 点阵画法的位置：摆就钉在悬挂点（网格）上，逐帧不变。"""
        return self.base

    def points(self) -> np.ndarray:
        """``(count, 3, 3)`` 世界坐标：``[:,0]`` 悬挂点、``[:,1]`` 关节、``[:,2]`` 摆锤。"""
        if self._points is None:
            self._points = np.zeros((self.count, KEY_POINTS, 3), np.float64)
        points = self._points
        sin1 = np.sin(self.th1)
        cos1 = np.cos(self.th1)
        sin2 = np.sin(self.th2)
        cos2 = np.cos(self.th2)

        points[:, 0, :] = self.base
        points[:, 1, 0] = self.base[:, 0] + self.l1 * sin1
        points[:, 1, 1] = 0.0
        points[:, 1, 2] = self.base[:, 2] - self.l1 * cos1
        points[:, 2, 0] = points[:, 1, 0] + self.l2 * sin2
        points[:, 2, 1] = 0.0
        points[:, 2, 2] = points[:, 1, 2] - self.l2 * cos2
        return points

    # --- 场 → 标量 ----------------------------------------------------------

    def omega(self) -> np.ndarray:
        """角速度大小 ``√(dθ1² + dθ2²)`` [rad/s]。"""
        return np.sqrt(self.dth1 * self.dth1 + self.dth2 * self.dth2)

    def rod_speeds(self) -> tuple:
        """两根杆各自的角速度 ``(|dθ1|, |dθ2|)`` —— 上摆 / 下摆分别配色用。"""
        return np.abs(self.dth1), np.abs(self.dth2)

    def energies(self) -> np.ndarray:
        """每个双摆的总机械能 ``(count,)`` [J]。"""
        return energy(
            self.th1,
            self.th2,
            self.dth1,
            self.dth2,
            self.gravity,
            self.l1,
            self.l2,
            self.m1,
            self.m2,
        )


# --- 后端选择 ---------------------------------------------------------------


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

    ``--factory-startup`` 不读用户偏好，``$BLENDER_USER_RESOURCES/scripts/modules`` 会从
    ``sys.path`` 上消失，于是"明明装了 taichi 却回退 NumPy"。这里按环境变量与便携版目录
    再补一次，不改动已有环境（与 ``sim_jacobi.solver.local_module_roots`` 同一套）。
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
    n: int = 16,
    init: str = INIT_ANGLES,
    spacing: float = 2.4,
    spin_max: float = 9.0,
    margin: float = 1e-2,
    backend: str = BACKEND_AUTO,
    **params,
):
    """建相空间场：``AUTO`` 优先 Taichi GPU，取不到就退回 NumPy。

    ``backend`` 显式给 ``TAICHI`` 时初始化失败会抛异常（面板显示原因），给 ``NUMPY`` 就
    永远走纯 NumPy。回退原因写在实例的 ``note`` 上。
    """
    choice = str(backend).upper()
    if choice in (BACKEND_AUTO, BACKEND_TAICHI):
        try:
            from .taichi_field import TaichiPendulum

            return TaichiPendulum(
                n=n,
                init=init,
                spacing=spacing,
                spin_max=spin_max,
                margin=margin,
                **params,
            )
        except Exception as exc:
            if choice == BACKEND_TAICHI:
                raise RuntimeError(f"Taichi 后端不可用: {exc}") from exc
            active_field = PhaseField(
                n=n,
                init=init,
                spacing=spacing,
                spin_max=spin_max,
                margin=margin,
                **params,
            )
            active_field.note = f"Taichi 不可用（{exc}），已回退 NumPy"
            return active_field
    return PhaseField(
        n=n,
        init=init,
        spacing=spacing,
        spin_max=spin_max,
        margin=margin,
        **params,
    )
