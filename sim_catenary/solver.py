"""XPBD 杆链求解器（纯 NumPy，无 bpy、无 Taichi）。

模型与参考实现 ``.ref/ring_catenary/scripts/xpbd_chain.py`` 一致，只把 Taichi kernel
换成向量化 NumPy：

* 节点 = 相邻两环共享的**关节**，相邻节点之间一根**杆约束**（长度 = 相扣节距
  ``pitch`` = 一节的内长）；一个环就占一节；
* 两端节点逆质量为 0（固定锚点）；
* 每子步：重力预测 → ``iters`` 次杆约束投影（+ 折角下限）→ 速度/位置更新 → 端点钳位；
* 约束投影按**奇偶红黑交错**分两批向量化：同批的杆不共享节点，所以可以一次性
  广播修正而不会互相竞争（等价于 Gauss-Seidel 的一遍扫描）；
* **折角下限**（可选）：真实链环有体积，关节不可能对折。用 ``|p[i+2] − p[i]|`` 的
  最小值表达折角上限（等长杆时 = ``2·l·cos(φ/2)``），按 ``i mod 3`` 分三批投影。
  没有它，锚点那根杆会整个甩翻折回来，长链环会看着"飞到邻居身上"；
* ``lambda`` 每子步清零，子步末的累积值就是约束乘子，张力 ``T = max(λ,0)/dt²``。

为什么不用 Taichi：这个链只有百来个节点，逐子步 NumPy 广播已经能跑到实时
（实测见 ``README.md``），而扩展要自包含 —— 少一个第三方依赖就少一处装不上的地方。
"""

from __future__ import annotations

import math

import numpy as np

from .chain import GRAVITY, PITCH

__all__ = ["XpbdChain"]


class XpbdChain:
    """一条两端固定、中间自由下落的杆链（节点 = 环与环之间的关节）。"""

    def __init__(self, pos0: np.ndarray, pitch: float = PITCH, alpha: float = 0.0):
        pos0 = np.ascontiguousarray(pos0, np.float64)
        self.n = int(pos0.shape[0])
        if self.n < 3:
            raise ValueError("chain needs at least 3 nodes")
        self.pitch = float(pitch)
        self.alpha = float(alpha)
        self.pos0 = pos0.copy()

        self.x = pos0.copy()
        self.p = pos0.copy()
        self.v = np.zeros_like(pos0)
        self.x_prev = pos0.copy()
        #: 逆质量：端点 = 0（固定），其余 = 1（单位质量）。
        self.w = np.ones(self.n, np.float64)
        self.w[0] = self.w[-1] = 0.0
        self.lam = np.zeros(self.n - 1, np.float64)

        # 杆按奇偶分成两批：同批的杆不共享节点，可以一次广播修正。
        # 两批都用**步长切片**（视图）而不是花式索引：`p[i] += x` 走视图是原地写，
        # 走花式索引要 gather + scatter 各拷一份 —— 实测差 2.5 倍（Blender 里 9.4 → 3.9 ms/帧）。
        n = self.n
        p, w, lam = self.p, self.w, self.lam
        self._parity = (
            (
                p[0:-1:2],
                p[1::2],
                w[0:-1:2],
                w[1::2],
                (w[0:-1:2] + w[1::2]),
                lam[0::2],
            ),
            (
                p[1:-1:2],
                p[2::2],
                w[1:-1:2],
                w[2::2],
                (w[1:-1:2] + w[2::2]),
                lam[1::2],
            ),
        )

        # 折角限制：真实链环有体积，关节处不可能对折。用"第 i 与第 i+2 个节点的最小距离"
        # 表达（等长杆时 |p[i+2] − p[i]| = 2·l·cos(φ/2)，φ 就是折角），限定 φ ≤ φ_max。
        # 步长 2 的约束会跨过中间节点，所以按 i mod 3 分三批，批内互不共享节点；
        # 必须用步长切片（视图，原地写），花式索引拿到的是副本。
        self._bend_views = []
        for k in range(3):
            if n - 2 <= k:
                continue
            self._bend_views.append(
                (p[k : n - 2 : 3], p[k + 2 : n : 3], w[k : n - 2 : 3], w[k + 2 : n : 3])
            )
        #: 折角下限对应的最小两点距（None = 不限制）；每步由 ``bend_limit`` 更新。
        self._bend_min: float | None = None

    # --- 状态 ---------------------------------------------------------------

    def reset(self) -> None:
        """回到初始形态（位置 = 构造时的拱形，速度 / λ 归零）。不重建任何对象。"""
        self.x[:] = self.pos0
        self.p[:] = self.pos0
        self.x_prev[:] = self.pos0
        self.v[:] = 0.0
        self.lam[:] = 0.0

    def positions(self) -> np.ndarray:
        return self.x

    def tension(self, dt: float) -> np.ndarray:
        """边张力 ``max(λ,0)/dt²``（链只能拉不能推，受压的 λ 截断为 0）。"""
        return np.maximum(self.lam, 0.0).astype(np.float32) / np.float32(dt * dt)

    # --- 物理 ---------------------------------------------------------------

    def _predict(self, dt: float, gravity: float) -> None:
        np.copyto(self.x_prev, self.x)  # 预测前的位置 = 速度差分基准
        self.v[:, 2] -= gravity * dt * self.w
        self.p[:] = self.x + self.v * (dt * self.w)[:, None]

    def _solve(self, parity, alpha: float) -> None:
        i, j, w_i, w_j, w_sum, lam = parity
        if not len(i):
            return
        d = j - i
        dist = np.sqrt(d[:, 0] * d[:, 0] + d[:, 1] * d[:, 1] + d[:, 2] * d[:, 2])
        np.maximum(dist, 1e-9, out=dist)
        dlam = (dist - self.pitch) / (w_sum + alpha)
        lam += dlam
        # 单位方向 × 修正量：d/dist * dlam，再按逆质量分给两端（视图原地写）
        scale = dlam / dist
        i += (scale * w_i)[:, None] * d
        j -= (scale * w_j)[:, None] * d

    def _solve_bend(self, bend) -> None:
        """折角下限：``|p[i+2] − p[i]|`` 不小于 ``_bend_min``（等价于折角 ≤ 上限）。"""
        i, j, w_i, w_j = bend
        if self._bend_min is None or not len(i):
            return
        d = j - i
        dist = np.sqrt(d[:, 0] * d[:, 0] + d[:, 1] * d[:, 1] + d[:, 2] * d[:, 2])
        push = self._bend_min - dist
        if not np.any(push > 0.0):
            return
        np.maximum(push, 0.0, out=push)
        w_sum = w_i + w_j
        w_sum = np.where(w_sum > 0.0, w_sum, 1.0)
        # 方向未定义（完全重合）时交给杆约束，这一对跳过
        safe = np.where(dist < 1e-9, 1.0, dist)
        n = d / safe[:, None]
        k = np.where(dist < 1e-9, 0.0, push / w_sum)
        i -= (k * w_i)[:, None] * n
        j += (k * w_j)[:, None] * n

    def _integrate(self, dt: float, damp: float) -> None:
        np.subtract(self.p, self.x_prev, out=self.v)
        self.v /= dt
        self.v *= damp
        np.copyto(self.x, self.p)
        # 端点钳位（固定锚点不受求解影响）
        self.x[0] = self.pos0[0]
        self.x[-1] = self.pos0[-1]
        self.v[0] = 0.0
        self.v[-1] = 0.0
        self.p[0] = self.pos0[0]
        self.p[-1] = self.pos0[-1]

    def step(
        self,
        dt: float,
        iters: int = 20,
        damp: float = 0.997,
        gravity: float = GRAVITY,
        alpha: float | None = None,
        bend_limit: float | None = None,
    ) -> None:
        """推进一个物理子步。

        ``bend_limit`` 是关节处允许的最大折角（弧度）；``None`` 或 ≥ π 表示不限制。
        真实链环有体积，关节不可能对折 —— 不限制的话锚点那根杆会整个甩翻、折回来压在
        邻居身上（正圆时看不出来，长链环一眼就能看出"环飞了"）。
        """
        if alpha is None:
            alpha = self.alpha
        self._bend_min = (
            None
            if bend_limit is None or bend_limit >= math.pi
            else 2.0 * self.pitch * math.cos(0.5 * float(bend_limit))
        )
        self.lam[:] = 0.0
        self._predict(dt, gravity)
        for _ in range(max(1, int(iters))):
            for parity in self._parity:
                self._solve(parity, alpha)
            for bend in self._bend_views:
                self._solve_bend(bend)
        self._integrate(dt, damp)

    def advance(
        self,
        frames: int,
        dt: float,
        substeps: int = 10,
        iters: int = 20,
        damp: float = 0.997,
        gravity: float = GRAVITY,
        alpha: float | None = None,
        bend_limit: float | None = None,
    ) -> np.ndarray:
        """按帧推进（每帧 ``substeps`` 个子步，``dt`` 是**子步**时长）。

        ``bend_limit``：关节允许的最大折角（弧度，``None`` = 不限制）。返回位置数组
        （内部缓冲，调用方自行复制或直接读取）。
        """
        if alpha is None:
            alpha = self.alpha
        for _ in range(max(0, int(frames))):
            for _ in range(max(1, int(substeps))):
                self.step(
                    dt,
                    iters=iters,
                    damp=damp,
                    gravity=gravity,
                    alpha=alpha,
                    bend_limit=bend_limit,
                )
        return self.x
