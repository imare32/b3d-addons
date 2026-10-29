"""Boids 群集（flocking）：二维分离 / 对齐 / 聚集，纯 NumPy 后端 + 后端工厂。

模型与参考脚本 ``scripts/flocking.py`` 是同一条 Reynolds boids 链，只是把「每条规则各算
一次操舵」写清楚了::

    分离  sep = Σ_{0<d<sep_rad} (pᵢ−pⱼ)/(d·(d+ε))      「别撞上」，按 1/d 加权
    对齐  ali = mean(vⱼ)      0<d<ali_rad                「和大家同向」
    聚集  coh = mean(pⱼ) − pᵢ 0<d<coh_rad               「别掉队」

三条规则各自折算成一次操舵（见 :func:`steer`：目标方向 × ``max_speed`` − 当前速度，再截到
``max_force``），按权重求和就是加速度::

    aᵢ = w_s·steer(sep) + w_a·steer(ali) + w_c·steer(coh) + noise·max_force·rand
    vᵢ ← clamp(vᵢ + aᵢ·dt, max_speed)
    pᵢ ← pᵢ + vᵢ·dt，再按边界方式处理（环绕 / 软边界 / 反弹）

**只有两条规则没有的项**（参考实现也没有，但这是画面像不像鸟群的关键）：

* **操舵要截断**：不加 ``max_force`` 截断时，分离力在重叠处会爆掉，整群会抖动；
* **噪声**：纯 boids 迟早锁成「整群同向直飞」，加一点点随机扰动才一直有转弯。

本模块**不 import bpy**（只用 numpy），可以脱离 Blender 单独跑::

    python -c "from sim_flocking import solver; f=solver.create(200); [f.step(2, 0.5) for _ in range(100)]; print(f.positions().mean(0))"

Taichi 后端在 :mod:`sim_flocking.taichi_solver`（同一套方程，GPU kernel），两者 API 相同，
由 :func:`create` 按需选择；本模块**不 import taichi**。

两个后端的**初始状态一模一样**：撒点与初速度都由 NumPy 的 ``default_rng(seed)`` 生成后上传，
所以 ``noise = 0`` 时同种子、同参数下两边的差异只有浮点累加顺序。``noise > 0`` 时不行 ——
Taichi 的 ``ti.random()`` 按线程取种子，不是同一条随机序列，两边会慢慢分叉。
"""

from __future__ import annotations

import numpy as np

__all__ = [
    "BACKEND_AUTO",
    "BACKEND_NUMPY",
    "BACKEND_TAICHI",
    "BOUNDARIES",
    "BOUNDARY_BOUNCE",
    "BOUNDARY_CODES",
    "BOUNDARY_SOFT",
    "BOUNDARY_WRAP",
    "DEFAULTS",
    "FLOAT_KEYS",
    "MAX_BOIDS",
    "NumPyFlock",
    "PARAM_KEYS",
    "create",
    "initial_speed",
    "random_flock",
    "steer",
    "taichi_available",
]

#: 鸟数上限（两个后端都按这个值预分配缓冲；再多请自己改常量）。
MAX_BOIDS = 20000

#: ``create`` 的后端选择。
BACKEND_AUTO = "AUTO"
BACKEND_TAICHI = "TAICHI"
BACKEND_NUMPY = "NUMPY"

#: 边界方式：超出模拟区域的鸟怎么回家。
BOUNDARY_WRAP = "WRAP"
BOUNDARY_SOFT = "SOFT"
BOUNDARY_BOUNCE = "BOUNCE"

#: 面板枚举项 ``(标识, 显示名, 说明)``。
BOUNDARIES = [
    (
        BOUNDARY_WRAP,
        "环绕（环面）",
        "出了左边从右边回来：没有边界感，整群可以一直直线飞（缺省，参考实现的行为）",
    ),
    (
        BOUNDARY_SOFT,
        "软边界",
        (
            "靠近边缘就被推回来：鸟会沿边界拐弯，画面里有一圈看不见的墙，"
            "运动更像在培养皿里"
        ),
    ),
    (
        BOUNDARY_BOUNCE,
        "反弹",
        "撞到边缘就镜像反射：会在边界上留下明显的折线，适合看单只鸟的轨迹",
    ),
]

#: 边界方式 → kernel 里的整数码（Taichi 后端用；NumPy 后端只认字符串）。
BOUNDARY_CODES = {BOUNDARY_WRAP: 0, BOUNDARY_SOFT: 1, BOUNDARY_BOUNCE: 2}

#: 默认参数。缺省是「20×20 的区域、300 只鸟」的量级（与参考实现同一口径）：
#: 平均间距 ≈ 1.1，感知半径要给到 2 以上才连得上邻居。
#:
#: 这套缺省值是按「画面里看得出一个鸟群」调的，实测（300 只、20×20、120 帧后的
#: 「平均离群心距」）：参考实现的 1.6 半径是 8.2（散成一片小点，但区域内还看得到空隙），
#: 半径 2.2 + 噪声 0.1 会收成 3.3 的一团 —— 约占画面三分之一，且有持续的分叉与汇聚。
DEFAULTS = {
    "max_speed": 0.8,  # 速度上限（世界单位 / 秒，乘 dt 就是每步位移）
    "max_force": 0.05,  # 单条规则的最大操舵力（加速度上限）
    "sep_rad": 0.55,  # 分离半径：比它近就互相推开
    "ali_rad": 2.2,  # 对齐半径：比它近就对齐速度
    "coh_rad": 2.2,  # 聚集半径：比它近就往重心靠
    "sep_weight": 1.5,  # 分离权重
    "ali_weight": 1.0,  # 对齐权重
    "coh_weight": 1.0,  # 聚集权重
    "width": 20.0,  # 模拟区域宽（X，居中于原点）
    "height": 20.0,  # 模拟区域高（Y，居中于原点）
    "noise": 0.1,  # 随机扰动（占 max_force 的比例）：0 = 纯 boids，0.1~0.3 会一直转弯
    "boundary": BOUNDARY_WRAP,  # 边界方式（见 BOUNDARIES）
}

#: 全部参数名（含 ``boundary`` 这个字符串项）。
PARAM_KEYS = tuple(DEFAULTS)

#: 只要取 ``float()`` 的参数名（``boundary`` 是枚举字符串，单独处理）。
FLOAT_KEYS = tuple(key for key in DEFAULTS if key != "boundary")


# --- 公共工具（两个后端共用，纯 numpy） -------------------------------------


def random_flock(count: int, width: float, height: float, seed: int = 0) -> tuple:
    """均匀撒 ``count`` 只鸟：``(位置 (n,2), 单位速度 (n,2))``。

    位置均匀落在 ``width × height`` 的矩形里（居中于原点），初速度是随机方向的单位向量 ——
    这样「初始速度」不比 ``max_speed`` 快，第一帧不会先被削一刀。
    """
    rng = np.random.default_rng(int(seed))
    n = int(max(count, 0))
    pos = rng.uniform(-0.5, 0.5, size=(n, 2)).astype(np.float32)
    pos *= np.array([max(float(width), 1e-6), max(float(height), 1e-6)], np.float32)

    angles = rng.uniform(0.0, 2.0 * np.pi, n).astype(np.float32)
    vel = np.stack([np.cos(angles), np.sin(angles)], axis=1).astype(np.float32)
    return pos, vel


def steer(
    target: np.ndarray,
    velocity: np.ndarray,
    max_speed: float,
    max_force: float,
) -> np.ndarray:
    """一次操舵：``clamp(normalize(target)·max_speed − velocity, max_force)``。

    目标为零向量（这个半径内没有邻居）时返回零力 —— 否则「没有邻居」会被解释成
    「以最大速度冲向原点」，鸟群会整体塌向中心。
    """
    target = np.asarray(target, np.float32)
    velocity = np.asarray(velocity, np.float32)
    norm = np.linalg.norm(target, axis=1)
    moving = norm > 1e-9

    desired = np.zeros_like(target)
    desired[moving] = target[moving] / norm[moving, None] * float(max_speed)

    force = desired - velocity
    force[~moving] = 0.0
    length = np.linalg.norm(force, axis=1)
    over = length > float(max_force)
    force[over] *= (float(max_force) / length[over])[:, None]
    return force.astype(np.float32)


def initial_speed(max_speed: float) -> float:
    """初始 / 新撒的鸟的速度大小：**半速**。

    参考实现用的是单位向量，``max_speed`` 小于 1 时开局就超速 —— 第一帧被削一刀，配色也
    从「最快」开始。半速开局看得出加速过程，而且与 ``max_speed`` 的绝对值无关。
    """
    return max(float(max_speed), 1e-6) * 0.5


def _next_capacity(count: int) -> int:
    """缓冲容量按 256 取整，避免拖动物数时每帧扩容。"""
    return int(np.ceil(max(int(count), 1) / 256.0) * 256)


class NumPyFlock:
    """boids 求解器（NumPy 后端）。

    状态两块：``pos`` / ``vel`` 都是 ``(capacity, 2)`` 的 float32，只有前 ``count`` 行有效。
    容量只增不减（``_reserve``），所以拖动物数、往画面上撒鸟都不会重建缓冲。

    邻居搜索是**分块暴力扫描**：每次取一段查询（块大小按 ``2M / n`` 自适应，保证一次只有
    几 MB 的临时数组），一次算出该段所有鸟到全部鸟的距离，再分别按三个半径做掩码矩阵乘。
    n = 300 时一步约 0.3 ms，n = 3000 时约 8 ms；再大的规模请换 Taichi 后端。
    """

    backend = "numpy"

    def __init__(
        self,
        count: int = 300,
        seed: int = 0,
        params: dict | None = None,
    ):
        #: 后端说明（面板读数用；NumPy 后端没有可报告的回退原因）。
        self.note = "numpy"
        self.seed = int(seed)
        self.params = dict(DEFAULTS)
        self.params["boundary"] = BOUNDARY_WRAP
        self.set_params(params or {})

        #: 平均邻居数（对齐半径内），面板读数用
        self.mean_neighbours = 0.0

        self._rng = np.random.default_rng(self.seed)
        self._capacity = 0
        self._n = 0
        self._pos = np.zeros((0, 2), np.float32)
        self._vel = np.zeros((0, 2), np.float32)

        self._reserve(_next_capacity(count))
        self.set_count(int(count))

    # --- 容量 / 数量 --------------------------------------------------------

    @property
    def count(self) -> int:
        """当前的鸟数。"""
        return int(self._n)

    @property
    def capacity(self) -> int:
        """缓冲容量（≥ 鸟数，只增不减）。"""
        return int(self._capacity)

    def _reserve(self, capacity: int) -> None:
        """扩容到至少 ``capacity``（保留已有状态）。"""
        capacity = int(max(capacity, 1))
        if capacity <= self._capacity:
            return
        pos = np.zeros((capacity, 2), np.float32)
        vel = np.zeros((capacity, 2), np.float32)
        if self._capacity:
            pos[: self._capacity] = self._pos
            vel[: self._capacity] = self._vel
        self._pos, self._vel = pos, vel
        self._capacity = capacity

    def set_params(self, values: dict) -> None:
        """更新参数（只认 :data:`DEFAULTS` 里的键；``boundary`` 是字符串）。"""
        for key, value in (values or {}).items():
            if key not in DEFAULTS:
                continue
            if key == "boundary":
                self.params["boundary"] = str(value).upper()
            else:
                self.params[key] = float(value)

    def set_count(self, count: int) -> None:
        """改鸟数：多了就随机补一批（``_spawn_random``），少了就砍掉尾巴。"""
        count = int(max(0, min(int(count), MAX_BOIDS)))
        if count > self._capacity:
            self._reserve(_next_capacity(count))
        if count > self._n:
            self._spawn_random(count - self._n)
        self._n = count

    # --- 初始状态 / 撒鸟 ----------------------------------------------------

    def reset(self) -> None:
        """按 ``seed`` 重新撒一遍（同一个种子每次重置都是同一个初始画面）。"""
        self._rng = np.random.default_rng(self.seed)
        pos, vel = random_flock(
            self._n,
            float(self.params["width"]),
            float(self.params["height"]),
            self.seed,
        )
        self._pos[: self._n] = pos
        self._vel[: self._n] = vel * initial_speed(self.params["max_speed"])

    def _spawn_random(self, amount: int) -> None:
        """在区域里随机补 ``amount`` 只鸟（接在当前鸟数后面）。"""
        amount = int(amount)
        if amount <= 0:
            return
        pos, vel = random_flock(
            amount,
            float(self.params["width"]),
            float(self.params["height"]),
            int(self._rng.integers(0, 2**31 - 1)),
        )
        start = self._n
        self._pos[start : start + amount] = pos
        self._vel[start : start + amount] = vel * initial_speed(
            self.params["max_speed"]
        )

    def spawn(self, points, spread: float = 0.0) -> int:
        """在第一现场撒鸟：``points`` 是 ``(k,2)``（或单个 ``(2,)``）坐标。

        ``spread > 0`` 时每只鸟在自己落点周围的圆盘里再抖一下（画笔半径），否则全部落在
        同一点 —— 同一点上的鸟互相距离为零，分离规则会直接跳过它们。返回真的加了几只
        （受 :data:`MAX_BOIDS` 限制）。
        """
        pts = np.asarray(points, np.float32).reshape(-1, 2)
        room = int(MAX_BOIDS) - self._n
        if room <= 0 or pts.shape[0] == 0:
            return 0
        pts = pts[:room]
        amount = int(pts.shape[0])

        if float(spread) > 0.0:
            radius = np.sqrt(self._rng.random(amount)).astype(np.float32)
            radius *= float(spread)
            angles = (self._rng.random(amount) * 2.0 * np.pi).astype(np.float32)
            pts = (
                pts
                + np.stack([np.cos(angles), np.sin(angles)], axis=1) * radius[:, None]
            )

        angles = (self._rng.random(amount) * 2.0 * np.pi).astype(np.float32)
        vel = np.stack([np.cos(angles), np.sin(angles)], axis=1).astype(np.float32)
        vel *= initial_speed(self.params["max_speed"])

        self._reserve(self._n + amount)
        self._pos[self._n : self._n + amount] = pts
        self._vel[self._n : self._n + amount] = vel
        self._n += amount
        return amount

    # --- 状态 ---------------------------------------------------------------

    def positions(self) -> np.ndarray:
        """位置 ``(n, 2)``（只读视图，别就地改）。"""
        return self._pos[: self._n]

    def velocities(self) -> np.ndarray:
        """速度 ``(n, 2)``（只读视图，别就地改）。"""
        return self._vel[: self._n]

    # --- 推进 ---------------------------------------------------------------

    def step(self, substeps: int = 1, dt: float = 0.5) -> None:
        """往前推 ``substeps`` 个子步，每步时长 ``dt``。"""
        for _ in range(max(1, int(substeps))):
            self._substep(float(dt))

    def _substep(self, dt: float) -> None:
        n = self._n
        if n == 0:
            return

        params = self.params
        max_speed = max(float(params["max_speed"]), 1e-6)
        max_force = max(float(params["max_force"]), 1e-9)

        pos = self._pos[:n]
        vel = self._vel[:n]

        sep_sum, ali_sum, coh_sum, ali_count, coh_count = self._neighbourhood(pos, vel)

        sep_force = steer(sep_sum, vel, max_speed, max_force)
        ali_force = steer(ali_sum, vel, max_speed, max_force)

        # 「聚集」的目标是邻居重心 − 自己；没人可聚时给零向量（steer 会返回零力）
        safe = np.maximum(coh_count, 1.0)[:, None]
        coh_target = np.where(coh_count[:, None] > 0.0, coh_sum / safe - pos, 0.0)
        coh_force = steer(coh_target, vel, max_speed, max_force)

        acc = (
            sep_force * float(params["sep_weight"])
            + ali_force * float(params["ali_weight"])
            + coh_force * float(params["coh_weight"])
        )

        noise = float(params["noise"])
        if noise > 0.0:
            acc += self._rng.standard_normal((n, 2)).astype(np.float32) * (
                noise * max_force * 2.0
            )

        acc += self._boundary_force(pos, max_speed, max_force)

        vel += acc * dt
        speed = np.linalg.norm(vel, axis=1)
        over = speed > max_speed
        if np.any(over):
            vel[over] *= (max_speed / speed[over])[:, None]

        pos += vel * dt
        self._apply_boundary(pos, vel)

        counts = np.asarray(ali_count, np.float32)
        self.mean_neighbours = float(counts.mean()) if counts.size else 0.0

    def _neighbourhood(self, pos: np.ndarray, vel: np.ndarray) -> tuple:
        """分块暴力扫描：返回 ``(sep_sum, ali_sum, coh_sum, ali_count, coh_count)``。"""
        n = int(pos.shape[0])
        params = self.params
        sep_limit = float(params["sep_rad"]) ** 2
        ali_limit = float(params["ali_rad"]) ** 2
        coh_limit = float(params["coh_rad"]) ** 2

        sep_sum = np.zeros((n, 2), np.float32)
        ali_sum = np.zeros((n, 2), np.float32)
        coh_sum = np.zeros((n, 2), np.float32)
        ali_count = np.zeros(n, np.float32)
        coh_count = np.zeros(n, np.float32)

        block = int(max(32, min(512, 2_000_000 // max(n, 1))))
        for start in range(0, n, block):
            stop = min(start + block, n)
            diff = pos[start:stop, None, :] - pos[None, :, :]
            dist2 = np.einsum("bnc,bnc->bn", diff, diff)

            # 分离：按 1/(d·(d+ε)) 加权的单位向量和（ε 防止两只鸟重合时除零）
            mask = (dist2 > 1e-12) & (dist2 < sep_limit)
            if mask.any():
                # 只对掩码内的格子开方（掩码外先填 1，避免 0 距离触发除零警告）
                dist = np.sqrt(np.where(mask, dist2, 1.0))
                weight = np.where(mask, 1.0 / (dist * (dist + 1e-4)), 0.0)
                sep_sum[start:stop] = np.einsum(
                    "bn,bnc->bc", weight.astype(np.float32), diff
                )

            # 对齐：邻居速度之和（除以邻居数是 steer 之前才需要的，这里保留计数）
            mask = (dist2 > 1e-12) & (dist2 < ali_limit)
            if mask.any():
                weight = mask.astype(np.float32)
                ali_sum[start:stop] = weight @ vel
                ali_count[start:stop] = weight.sum(axis=1)

            # 聚集：邻居位置之和
            mask = (dist2 > 1e-12) & (dist2 < coh_limit)
            if mask.any():
                weight = mask.astype(np.float32)
                coh_sum[start:stop] = weight @ pos
                coh_count[start:stop] = weight.sum(axis=1)

        return sep_sum, ali_sum, coh_sum, ali_count, coh_count

    # --- 边界 ---------------------------------------------------------------

    def _half(self) -> np.ndarray:
        return np.array(
            [
                max(float(self.params["width"]), 1e-6) * 0.5,
                max(float(self.params["height"]), 1e-6) * 0.5,
            ],
            np.float32,
        )

    def _boundary_force(
        self, pos: np.ndarray, max_speed: float, max_force: float
    ) -> np.ndarray:
        """「软边界」的推回力（其它两种边界返回零）。

        只在这圈里起作用：越贴近墙、推得越狠，强度以 ``max_speed`` 为单位 —— 因为操舵力
        真正要抵消的是「已经到墙边的那个速度」，用 ``max_force`` 作单位会推不回来。
        """
        if self.params["boundary"] != BOUNDARY_SOFT:
            return np.zeros_like(pos)

        half = self._half()
        margin = max(float(min(half)) * 0.24, 1e-6)
        distance = half - np.abs(pos)  # 到最近同侧墙的距离，负数 = 已经出界
        closeness = np.clip(1.0 - distance / margin, 0.0, 1.0)
        return (-np.sign(pos) * closeness * (max_speed * 1.5)).astype(np.float32)

    def _apply_boundary(self, pos: np.ndarray, vel: np.ndarray) -> None:
        """按边界方式处理位置（就地改写 ``pos`` / ``vel``）。"""
        half = self._half()
        mode = self.params["boundary"]

        if mode == BOUNDARY_WRAP:
            pos[:] = (pos + half) % (half * 2.0) - half
            return

        # 软边界 / 反弹都要先夹回区域内，免得一只鸟冲出很远之后每一帧都在"回头"
        pos[:] = np.clip(pos, -half, half)
        if mode == BOUNDARY_BOUNCE:
            for axis in (0, 1):
                # 夹住之后仍然"贴着墙"且速度还在往外：把这一维的速度翻过来
                at_wall = np.abs(pos[:, axis]) >= half[axis] * 0.999
                going_out = np.sign(vel[:, axis]) == np.sign(pos[:, axis])
                flip = at_wall & going_out & (np.abs(vel[:, axis]) > 1e-9)
                vel[flip, axis] *= -1.0


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
    count: int = 300,
    seed: int = 0,
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
            from .taichi_solver import TaichiFlock

            return TaichiFlock(count, seed=seed, params=params)
        except Exception as exc:
            if choice == BACKEND_TAICHI:
                raise RuntimeError(f"Taichi 后端不可用: {exc}") from exc
            flock = NumPyFlock(count, seed=seed, params=params)
            flock.note = f"Taichi 不可用（{exc}），已回退 NumPy"
            return flock
    return NumPyFlock(count, seed=seed, params=params)
