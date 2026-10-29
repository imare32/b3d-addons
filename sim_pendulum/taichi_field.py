"""Taichi 后端：RK4 与笔划点列**全在 GPU 上算**，每帧只回拷一次。

同一个 kernel 里做两件事：把 ``substeps`` 个 RK4 子步推完、顺手写出五笔点列（下摆 2 点 /
上摆 2 点 / 扣子 1 点）。所以每帧只有 **1 次 kernel 启动 + 1 次回拷**（1 万摆时 600 KB）——
这是把 10k 摆压进 60 fps 的关键：NumPy 版光积分就要 10 ms（每帧 20 次 ``accelerations()``，
1 万元素上每次 ~437 µs，其中 6 次三角函数占 163 µs）。

与 :class:`sim_pendulum.field.PhaseField` **同一套公式**（拉格朗日量导出的 θ̈1 / θ̈2），
差别只在这三处：

* 状态是 ``float32``（消费级 GPU 上 f64 只有 1/32~1/64 吞吐）；能量漂移读数因此比 NumPy 版
  大一点，仍是 1e−5 量级；
* 每次加速度只算 **4 次三角函数**（``sinθ1/cosθ1/sinθ2/cosθ2``），
  ``sinΔ = s₁c₂−c₁s₂``、``cosΔ = c₁c₂+s₁s₂``、``cos2Δ = cos²Δ−sin²Δ``、
  ``sin(θ1−2θ2) = s₁cos2θ₂−c₁sin2θ₂`` 全部由它们组合 —— NumPy 版是 6 次；
* 点列直接写进 ``pos`` 字段，不再经过"场 → 关键点 → 重排"的 CPU 过程。

``ti.init`` 一个进程只做一次（Blender 里热重载模块时复用已有 runtime）；取不到 GPU 时退到
``ti.cpu``，再失败就由 :func:`sim_pendulum.field.create` 回退 NumPy。

**注意**：不能加 ``from __future__ import annotations`` —— Taichi 直接读 ``__annotations__``
判断 kernel 参数类型，PEP 563 的字符串注解会报 ``TaichiSyntaxError``。
"""

import sys

import numpy as np

from . import field as field_mod

try:
    import taichi as ti
except ImportError:  # pragma: no cover - 取决于本机安装
    for _root in field_mod.local_module_roots():
        if _root not in sys.path:
            sys.path.append(_root)
    import taichi as ti

__all__ = ["TaichiPendulum", "ensure_init"]

#: 一个双摆几条笔划 / 几个点（与 :mod:`sim_pendulum.strokes` 一致）。
_STROKES = 3
_POINTS = 5

#: ``ti.init`` 是否已完成（热重载会清模块级变量，但 runtime 还在，会重新探测）。
_READY = False
_ARCH = ""


def ensure_init() -> str:
    """保证 Taichi 已初始化，返回实际架构名。优先 GPU，失败退 CPU。"""
    global _READY, _ARCH
    if _READY:
        return _ARCH

    runtime = ti.lang.impl.get_runtime()
    if getattr(runtime, "prog", None) is None:
        try:
            ti.init(arch=ti.gpu, offline_cache=True)
        except Exception:
            # 没有可用 GPU / 驱动：CPU 后端照样正确，只是快不了
            ti.init(arch=ti.cpu, offline_cache=True)
    try:
        _ARCH = str(ti.lang.impl.current_cfg().arch).split(".")[-1].lower()
    except Exception:  # pragma: no cover - 老版本接口差异
        _ARCH = "unknown"
    _READY = True
    return _ARCH


@ti.func
def _accel(th1, th2, w1, w2, gravity, l1, l2, m1, m2):
    """角加速度 ``(θ̈1, θ̈2)``：4 次三角函数，其余全是乘加。"""
    s1 = ti.sin(th1)
    c1 = ti.cos(th1)
    s2 = ti.sin(th2)
    c2 = ti.cos(th2)
    sin_delta = s1 * c2 - c1 * s2
    cos_delta = c1 * c2 + s1 * s2
    cos_2delta = cos_delta * cos_delta - sin_delta * sin_delta
    sin_2th2 = 2.0 * s2 * c2
    cos_2th2 = c2 * c2 - s2 * s2
    sin_x = s1 * cos_2th2 - c1 * sin_2th2  # sin(θ1 − 2θ2)

    denominator = 2.0 * m1 + m2 - m2 * cos_2delta
    numerator1 = (
        -gravity * (2.0 * m1 + m2) * s1
        - m2 * gravity * sin_x
        - 2.0 * sin_delta * m2 * (w2 * w2 * l2 + w1 * w1 * l1 * cos_delta)
    )
    numerator2 = (
        2.0
        * sin_delta
        * (
            w1 * w1 * l1 * (m1 + m2)
            + gravity * (m1 + m2) * c1
            + w2 * w2 * l2 * m2 * cos_delta
        )
    )
    return numerator1 / (l1 * denominator), numerator2 / (l2 * denominator)


@ti.kernel
def _advance_kernel(
    state: ti.types.ndarray(),
    base_x: ti.types.ndarray(),
    base_z: ti.types.ndarray(),
    pos: ti.types.ndarray(),
    dt: ti.f32,
    substeps: ti.i32,
    count: ti.i32,
    gravity: ti.f32,
    l1: ti.f32,
    l2: ti.f32,
    m1: ti.f32,
    m2: ti.f32,
):
    """推进 ``substeps`` 个 RK4 子步，然后写出三笔点列（下摆 / 上摆 / 扣子）。

    ``y = (θ1, θ2, dθ1, dθ2)``，``f(y) = (dθ1, dθ2, θ̈1, θ̈2)``；四段用 p/q 表示两段
    角加速度，最后一次性合成 —— 与 NumPy 版的公式逐项对应（差 f32 舍入）。

    参数类型都是 ``ti.types.ndarray()``：**不按数组身份特化**，所以整场 session 只编译一次，
    换 N（换容量）也不用等。
    """
    half = 0.5 * dt
    sixth = dt / 6.0
    for i in range(count):
        th1 = state[i, 0]
        th2 = state[i, 1]
        w1 = state[i, 2]
        w2 = state[i, 3]
        for _ in range(substeps):
            # k1: y
            p1, q1 = _accel(th1, th2, w1, w2, gravity, l1, l2, m1, m2)
            # k2: y + h/2·k1
            p2, q2 = _accel(
                th1 + half * w1,
                th2 + half * w2,
                w1 + half * p1,
                w2 + half * q1,
                gravity,
                l1,
                l2,
                m1,
                m2,
            )
            # k3: y + h/2·k2（k2 的 θ 分量是 w1 + h/2·p1）
            p3, q3 = _accel(
                th1 + half * (w1 + half * p1),
                th2 + half * (w2 + half * q1),
                w1 + half * p2,
                w2 + half * q2,
                gravity,
                l1,
                l2,
                m1,
                m2,
            )
            # k4: y + h·k3
            p4, q4 = _accel(
                th1 + dt * (w1 + half * p2),
                th2 + dt * (w2 + half * q2),
                w1 + dt * p3,
                w2 + dt * q3,
                gravity,
                l1,
                l2,
                m1,
                m2,
            )
            next_th1 = th1 + sixth * (
                w1 + 2.0 * (w1 + half * p1) + 2.0 * (w1 + half * p2) + (w1 + dt * p3)
            )
            next_th2 = th2 + sixth * (
                w2 + 2.0 * (w2 + half * q1) + 2.0 * (w2 + half * q2) + (w2 + dt * q3)
            )
            next_w1 = w1 + sixth * (p1 + 2.0 * p2 + 2.0 * p3 + p4)
            next_w2 = w2 + sixth * (q1 + 2.0 * q2 + 2.0 * q3 + q4)
            th1 = next_th1
            th2 = next_th2
            w1 = next_w1
            w2 = next_w2

        state[i, 0] = th1
        state[i, 1] = th2
        state[i, 2] = w1
        state[i, 3] = w2

        s1 = ti.sin(th1)
        c1 = ti.cos(th1)
        s2 = ti.sin(th2)
        c2 = ti.cos(th2)
        bx = base_x[i]
        bz = base_z[i]
        jx = bx + l1 * s1
        jz = bz - l1 * c1
        ex = jx + l2 * s2
        ez = jz - l2 * c2

        k = i * 5
        pos[k + 0, 0] = jx  # 下摆：关节
        pos[k + 0, 1] = 0.0
        pos[k + 0, 2] = jz
        pos[k + 1, 0] = ex  # 下摆：摆锤
        pos[k + 1, 1] = 0.0
        pos[k + 1, 2] = ez
        pos[k + 2, 0] = bx  # 上摆：悬挂点
        pos[k + 2, 1] = 0.0
        pos[k + 2, 2] = bz
        pos[k + 3, 0] = jx  # 上摆：关节
        pos[k + 3, 1] = 0.0
        pos[k + 3, 2] = jz
        pos[k + 4, 0] = jx  # 扣子
        pos[k + 4, 1] = 0.0
        pos[k + 4, 2] = jz


@ti.data_oriented
class TaichiPendulum:
    """N×N 双摆阵列（Taichi 后端，GPU 优先）；API 与 :class:`PhaseField` 对齐。"""

    backend = "taichi"

    def __init__(
        self,
        n=16,
        init=field_mod.INIT_ANGLES,
        spacing=2.4,
        spin_max=9.0,
        margin=1e-2,
        **params,
    ):
        arch = ensure_init()

        self.n = int(max(2, min(int(n), field_mod.MAX_N)))
        self.count = self.n * self.n
        self.init = str(init)
        self.spacing = float(spacing)
        self.spin_max = float(spin_max)
        self.margin = float(margin)
        self.steps = 0
        self.note = f"taichi/{arch}"
        self.backend = "taichi"
        #: 是否每帧回拷点列（点阵画法下由 sim.build 置假 —— 位置是静态的）
        self.pull_points = True
        self.set_params(**params)

        # 格子的世界坐标（与 NumPy 版同一布局：参数 1 沿 X、参数 2 沿 Z）
        index_i, index_j = np.meshgrid(
            np.arange(self.n), np.arange(self.n), indexing="ij"
        )
        self._i = index_i.reshape(-1)
        self._j = index_j.reshape(-1)
        half = 0.5 * (self.n - 1)
        self._x = ((self._i - half) * self.spacing).astype(np.float32)
        self._z = ((self._j - half) * self.spacing).astype(np.float32)

        # 缓冲按 count 精确分配：kernel 参数是 ``ti.types.ndarray()``，**形状是运行期信息**，
        # 换 N 不会重编译，所以没理由多分配（多分配只会让每帧回拷变大）。
        self._capacity = self.count
        # 缓冲区用 ``ti.ndarray``（不是 ``ti.field``）：kernel 的参数类型是
        # ``ti.types.ndarray()``，**同一份 kernel 对所有实例只编译一次**。
        # 用 ``ti.field`` + ``self.xxx`` 的话每个实例都会重新编译一遍（实测每次构建
        # 多等 0.5~1 s）。
        # 状态放**一个** (cap,4) 缓冲里：回拷一次就够（Taichi 的 to_numpy 有 ~0.2 ms
        # 固定开销，四次分开拷要多花 0.6 ms）。
        self._state = ti.ndarray(ti.f32, shape=(self._capacity, 4))
        self._bx = ti.ndarray(ti.f32, shape=(self._capacity,))
        self._bz = ti.ndarray(ti.f32, shape=(self._capacity,))
        self._pos = ti.ndarray(ti.f32, shape=(self._capacity * _POINTS, 3))
        bx = np.zeros(self._capacity, np.float32)
        bz = np.zeros(self._capacity, np.float32)
        bx[: self.count] = self._x
        bz[: self.count] = self._z
        self._bx.from_numpy(bx)
        self._bz.from_numpy(bz)

        # CPU 侧缓存（每帧 _pull 一次，供配色 / 读数使用）
        self.th1 = np.zeros(self.count, np.float32)
        self.th2 = np.zeros(self.count, np.float32)
        self.dth1 = np.zeros(self.count, np.float32)
        self.dth2 = np.zeros(self.count, np.float32)
        self._stroke_points = np.zeros((self.count * _POINTS, 3), np.float32)
        self._dots = np.stack(
            [self._x, np.zeros(self.count, np.float32), self._z], axis=1
        ).astype(np.float32)

        span = max(np.pi - self.margin, 1e-6)
        angles = np.linspace(-span, span, self.n)
        self._first = angles[self._i]
        self._second = angles[self._j]

        self.reset()

    # --- 参数 ---------------------------------------------------------------

    def set_params(self, **kwargs) -> None:
        """就地更新物理参数（下一子步生效，不重置状态、不改拓扑）。"""
        values = dict(field_mod.DEFAULTS)
        values.update(self.params if hasattr(self, "params") else {})
        for key, value in kwargs.items():
            if key in field_mod.DEFAULTS and value is not None:
                values[key] = float(value)
        self.params = values
        self.gravity = values["gravity"]
        self.l1 = max(values["l1"], 1e-3)
        self.l2 = max(values["l2"], 1e-3)
        self.m1 = max(values["m1"], 1e-3)
        self.m2 = max(values["m2"], 1e-3)

    @property
    def reach(self) -> float:
        """单摆最大伸展半径（取景要用）。"""
        return self.l1 + self.l2

    # --- 初值 ---------------------------------------------------------------

    def reset(self) -> None:
        """回到初始阵列（第 0 步），不重建拓扑。"""
        count = self.count
        if self.init == field_mod.INIT_PHASE:
            th1 = self._first.astype(np.float32)
            th2 = np.zeros(count, np.float32)
            w1 = (self._second / max(np.pi - self.margin, 1e-6) * self.spin_max).astype(
                np.float32
            )
            w2 = np.zeros(count, np.float32)
        else:
            th1 = self._first.astype(np.float32)
            th2 = self._second.astype(np.float32)
            w1 = np.zeros(count, np.float32)
            w2 = np.zeros(count, np.float32)

        state = np.zeros((self._capacity, 4), np.float32)
        state[:count, 0] = th1
        state[:count, 1] = th2
        state[:count, 2] = w1
        state[:count, 3] = w2
        self._state.from_numpy(state)
        self.steps = 0
        # substeps = 0：只把点列刷新一遍，不推进物理
        self._advance(0.0, 0, count, self.gravity, self.l1, self.l2, self.m1, self.m2)
        self._pull()

    # --- 积分 ---------------------------------------------------------------

    def _advance(self, dt, substeps, count, gravity, l1, l2, m1, m2) -> None:
        _advance_kernel(
            self._state,
            self._bx,
            self._bz,
            self._pos,
            float(dt),
            int(substeps),
            int(count),
            float(gravity),
            float(l1),
            float(l2),
            float(m1),
            float(m2),
        )

    def step(self, dt: float = 0.005, substeps: int = 1) -> None:
        """推进 ``substeps`` 个 RK4 子步，每个子步 ``dt`` 秒。"""
        steps = max(1, int(substeps))
        self._advance(
            float(dt),
            steps,
            self.count,
            self.gravity,
            self.l1,
            self.l2,
            self.m1,
            self.m2,
        )
        self.steps += steps
        self._pull()

    def _pull(self) -> None:
        """把状态（以及点列）回拷到 CPU。

        Taichi 的 ``to_numpy`` 是**回拷瓶颈**：1 万摆 0.8 MB 约 0.9 ms，100 万摆 76 MB
        要 ~35 ms（≈2 GB/s）。所以两件事：

        * 四元状态塞进一个 ``(count,4)`` 缓冲，一次拷完（四份分开拷多花 0.6 ms）；
        * ``pull_points`` 为假时（点阵画法：摆钉在网格上）**整块跳过点列** ——
          百万摆时那正好是 60 MB/帧。
        """
        state = self._state.to_numpy()[: self.count]
        self.th1 = state[:, 0]
        self.th2 = state[:, 1]
        self.dth1 = state[:, 2]
        self.dth2 = state[:, 3]
        if self.pull_points:
            self._stroke_points = self._pos.to_numpy()[: self.count * _POINTS]

    # --- 场 → 几何 ----------------------------------------------------------

    def dot_points(self) -> np.ndarray:
        """``(count, 3)`` 点阵画法的位置（网格上，逐帧不变）。"""
        return self._dots

    def stroke_points(self) -> np.ndarray:
        """``(5S, 3)`` 点列（三笔顺序：下摆 2 / 上摆 2 / 扣子 1），GPU 直接写好。"""
        return self._stroke_points

    def points(self) -> np.ndarray:
        """``(count, 3, 3)`` 关键点（悬挂点 / 关节 / 摆锤），与 NumPy 版同义。"""
        points = np.zeros((self.count, 3, 3), np.float32)
        sin1 = np.sin(self.th1)
        cos1 = np.cos(self.th1)
        sin2 = np.sin(self.th2)
        cos2 = np.cos(self.th2)
        points[:, 0, 0] = self._x
        points[:, 0, 2] = self._z
        points[:, 1, 0] = self._x + self.l1 * sin1
        points[:, 1, 2] = self._z - self.l1 * cos1
        points[:, 2, 0] = points[:, 1, 0] + self.l2 * sin2
        points[:, 2, 2] = points[:, 1, 2] - self.l2 * cos2
        return points

    # --- 场 → 标量 ----------------------------------------------------------

    def omega(self) -> np.ndarray:
        """角速度大小 ``√(dθ1² + dθ2²)`` [rad/s]。"""
        return np.sqrt(self.dth1 * self.dth1 + self.dth2 * self.dth2)

    def rod_speeds(self) -> tuple:
        """两根杆各自的角速度 ``(|dθ1|, |dθ2|)``。"""
        return np.abs(self.dth1), np.abs(self.dth2)

    def energies(self) -> np.ndarray:
        """每个双摆的总机械能 ``(count,)`` [J]（与 NumPy 版同一套公式，CPU 上算）。"""
        return field_mod.energy(
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
