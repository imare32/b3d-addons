"""Taichi 后端的 boids（GPU kernel，几万只才需要）。

与 :mod:`sim_flocking.solver` 的 NumPy 版**同一套方程**，只是把每个子步丢给 kernel：

* 邻居搜索是 O(n²) 暴力扫描（每个线程扫一遍全场）：GPU 上 5000 只约 1 ms，几万只才有
  意义；不像 NumPy 后端那样分块，也不需要定长邻域表；
* 每个子步拆成两段循环（与参考实现一致）：第一段只写 ``acc``，第二段只读写**自己那一行**
  的位置与速度 —— 合成一段的话，别的线程会读到写了一半的 ``pos`` / ``vel``；
* 初始状态与撒鸟都由 NumPy 生成后上传（不是 kernel 里 ``ti.random``），所以 ``noise = 0``
  时两个后端走的是同一条演化路径；``noise > 0`` 时随机扰动来自 ``ti.random()``（按线程取
  种子），两边会慢慢分叉。

Taichi 的 ``ti.init`` 一个进程只做一次：Blender 里热重载模块时复用已有的 runtime，
不会因为重复初始化而报错。取不到 GPU 时退到 CPU（``ti.cpu``），再失败就由
:func:`sim_flocking.solver.create` 回退到 NumPy 后端。
"""

# 注意：**不能**加 ``from __future__ import annotations`` —— Taichi 直接读
# ``__annotations__`` 来判断 kernel 参数类型，PEP 563 的字符串注解会让它报
# ``TaichiSyntaxError: Invalid type annotation``。

import glob
import os
import sys

import numpy as np

from .solver import (
    BOUNDARY_CODES,
    BOUNDARY_WRAP,
    DEFAULTS,
    MAX_BOIDS,
    initial_speed,
    random_flock,
)


def _local_module_roots() -> list[str]:
    """第三方库的兜底查找路径（只在 ``import taichi`` 失败时才用）。

    正常启动（带 ``BLENDER_USER_RESOURCES``）时 Blender 会把
    ``$BLENDER_USER_RESOURCES/scripts/modules`` 加进 ``sys.path``；但
    ``--factory-startup`` 不读用户偏好，那一份路径也会丢 —— 后台测试 / CI 常这么启动，
    于是就会「明明装了 taichi 却回退 NumPy」。这里按环境变量与便携版目录再补一次。
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


try:
    import taichi as ti
except ImportError:  # pragma: no cover - 取决于本机安装
    for _root in _local_module_roots():
        if _root not in sys.path:
            sys.path.append(_root)
    import taichi as ti

__all__ = ["TaichiFlock", "ensure_init"]

#: 是否已完成 ``ti.init``（热重载会把模块级变量清掉，但 runtime 还在，会重新探测）。
_READY = False

#: 实际用的架构名（cuda / vulkan / metal / cpu / ...），只用于面板读数。
_ARCH = "?"


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
            # 没有可用 GPU/驱动：CPU 后端照样正确，只是慢
            ti.init(arch=ti.cpu, offline_cache=True)
    try:
        _ARCH = str(ti.lang.impl.current_cfg().arch).split(".")[-1].lower()
    except Exception:
        _ARCH = "?"
    _READY = True
    return _ARCH


@ti.data_oriented
class TaichiFlock:
    """boids 求解器（Taichi 后端，GPU 优先）。"""

    backend = "taichi"

    def __init__(self, count: int = 300, seed: int = 0, params: dict | None = None):
        arch = ensure_init()
        #: 后端说明（面板读数用）。
        self.note = f"taichi/{arch}"
        self.seed = int(seed)
        self.params = dict(DEFAULTS)
        self.params["boundary"] = BOUNDARY_WRAP
        self.set_params(params or {})

        #: 平均邻居数（对齐半径内），面板读数用
        self.mean_neighbours = 0.0

        self._rng = np.random.default_rng(self.seed)
        # **字段按上限一次分配**（不要按需扩容）：Taichi 的字段是定长的，重建字段对象之后
        # 已编译的 kernel 可能仍指着旧字段 —— 实测把 ``_ali_count`` 重建过一次之后就再也
        # 读不到值（positions / velocities 看着正常，读数却永远是 0）。上限 20000 只的
        # 字段一共不到 600 KB 显存，没必要为省它冒这个风险。
        self._capacity = int(MAX_BOIDS)
        self._n = 0
        self._num = ti.field(ti.i32, shape=())
        self._pos = ti.Vector.field(2, ti.f32, shape=self._capacity)
        self._vel = ti.Vector.field(2, ti.f32, shape=self._capacity)
        self._acc = ti.Vector.field(2, ti.f32, shape=self._capacity)
        self._ali_count = ti.field(ti.i32, shape=self._capacity)

        self.set_count(int(count))

    # --- 容量 / 数量 --------------------------------------------------------

    @property
    def count(self) -> int:
        """当前的鸟数。"""
        return int(self._n)

    @property
    def capacity(self) -> int:
        """缓冲容量：Taichi 后端恒为 :data:`~sim_flocking.solver.MAX_BOIDS`。"""
        return int(self._capacity)

    def set_params(self, values: dict) -> None:
        """更新参数（只认 :data:`~sim_flocking.solver.DEFAULTS` 里的键）。"""
        for key, value in (values or {}).items():
            if key not in DEFAULTS:
                continue
            if key == "boundary":
                self.params["boundary"] = str(value).upper()
            else:
                self.params[key] = float(value)

    def set_count(self, count: int) -> None:
        """改鸟数：多了就随机补一批，少了就砍掉尾巴。"""
        count = int(max(0, min(int(count), MAX_BOIDS)))
        if count > self._n:
            self._spawn_random(count - self._n)
        self._n = count
        self._num[None] = count

    # --- 初始状态 / 撒鸟 ----------------------------------------------------

    def reset(self) -> None:
        """按 ``seed`` 重新撒一遍。"""
        self._rng = np.random.default_rng(self.seed)
        pos, vel = random_flock(
            self._n,
            float(self.params["width"]),
            float(self.params["height"]),
            self.seed,
        )
        buffer_pos = np.zeros((self._capacity, 2), np.float32)
        buffer_vel = np.zeros((self._capacity, 2), np.float32)
        buffer_pos[: self._n] = pos
        buffer_vel[: self._n] = vel * initial_speed(self.params["max_speed"])
        self._pos.from_numpy(buffer_pos)
        self._vel.from_numpy(buffer_vel)

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
        self._upload_spawn(pos, vel * initial_speed(self.params["max_speed"]))

    def spawn(self, points, spread: float = 0.0) -> int:
        """在指定位置撒鸟（与 NumPy 后端同接口，见那边的文档）。"""
        pts = np.asarray(points, np.float32).reshape(-1, 2)
        room = int(MAX_BOIDS) - self._n
        if room <= 0 or pts.shape[0] == 0:
            return 0
        pts = pts[:room].copy()
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

        self._upload_spawn(pts, vel)
        return amount

    def _upload_spawn(self, pos: np.ndarray, vel: np.ndarray) -> None:
        """把 ``pos`` / ``vel`` 接到第 ``_n`` 行之后，并推进鸟数。"""
        amount = len(pos)
        if amount <= 0:
            return
        buffer_pos = self._pos.to_numpy()
        buffer_vel = self._vel.to_numpy()
        buffer_pos[self._n : self._n + amount] = pos
        buffer_vel[self._n : self._n + amount] = vel
        self._pos.from_numpy(buffer_pos)
        self._vel.from_numpy(buffer_vel)
        self._n += amount
        self._num[None] = self._n

    # --- 状态 ---------------------------------------------------------------

    def positions(self) -> np.ndarray:
        """位置 ``(n, 2)``（拷回主机的新数组）。"""
        return self._pos.to_numpy()[: self._n]

    def velocities(self) -> np.ndarray:
        """速度 ``(n, 2)``（拷回主机的新数组）。"""
        return self._vel.to_numpy()[: self._n]

    # --- 推进 ---------------------------------------------------------------

    def step(self, substeps: int = 1, dt: float = 0.5) -> None:
        """往前推 ``substeps`` 个子步，每步时长 ``dt``。"""
        if self._n <= 0:
            return
        params = self.params
        code = BOUNDARY_CODES.get(str(params["boundary"]), 0)
        for _ in range(max(1, int(substeps))):
            self._substep_kernel(
                float(dt),
                max(float(params["max_speed"]), 1e-6),
                max(float(params["max_force"]), 1e-9),
                float(params["sep_rad"]),
                float(params["ali_rad"]),
                float(params["coh_rad"]),
                float(params["sep_weight"]),
                float(params["ali_weight"]),
                float(params["coh_weight"]),
                float(params["noise"]),
                max(float(params["width"]), 1e-6),
                max(float(params["height"]), 1e-6),
                int(code),
            )
        counts = self._ali_count.to_numpy()[: self._n]
        self.mean_neighbours = float(counts.mean()) if counts.size else 0.0

    # --- kernel -------------------------------------------------------------

    @ti.func
    def _steer(self, target, velocity, max_speed, max_force):
        """操舵：``clamp(normalize(target)·max_speed − velocity, max_force)``。"""
        steer = ti.Vector([0.0, 0.0])
        norm = target.norm()
        if norm > 1e-9:
            steer = target / norm * max_speed - velocity
            length = steer.norm()
            if length > max_force:
                steer = steer / length * max_force
        return steer

    @ti.kernel
    def _substep_kernel(
        self,
        dt: ti.f32,
        max_speed: ti.f32,
        max_force: ti.f32,
        sep_rad: ti.f32,
        ali_rad: ti.f32,
        coh_rad: ti.f32,
        sep_weight: ti.f32,
        ali_weight: ti.f32,
        coh_weight: ti.f32,
        noise: ti.f32,
        width: ti.f32,
        height: ti.f32,
        boundary: ti.i32,
    ):
        n = self._num[None]
        sep_limit = sep_rad * sep_rad
        ali_limit = ali_rad * ali_rad
        coh_limit = coh_rad * coh_rad
        half_w = width * 0.5
        half_h = height * 0.5
        margin = ti.max(ti.min(half_w, half_h) * 0.24, 1e-6)

        # 第一段：只算加速度（只写 _acc，不碰 pos / vel）
        for i in range(n):
            p_i = self._pos[i]
            v_i = self._vel[i]
            sep = ti.Vector([0.0, 0.0])
            ali = ti.Vector([0.0, 0.0])
            coh = ti.Vector([0.0, 0.0])
            ali_n = 0
            coh_n = 0
            for j in range(n):
                if i != j:
                    diff = p_i - self._pos[j]
                    dist2 = diff.dot(diff)
                    if dist2 > 1e-12:
                        if dist2 < sep_limit:
                            dist = ti.sqrt(dist2)
                            sep += diff / (dist * (dist + 1e-4))
                        if dist2 < ali_limit:
                            ali += self._vel[j]
                            ali_n += 1
                        if dist2 < coh_limit:
                            coh += self._pos[j]
                            coh_n += 1

            acc = self._steer(sep, v_i, max_speed, max_force) * sep_weight
            acc += self._steer(ali, v_i, max_speed, max_force) * ali_weight

            coh_target = ti.Vector([0.0, 0.0])
            if coh_n > 0:
                coh_target = coh / ti.cast(coh_n, ti.f32) - p_i
            acc += self._steer(coh_target, v_i, max_speed, max_force) * coh_weight

            if noise > 0.0:
                acc += ti.Vector([ti.random() - 0.5, ti.random() - 0.5]) * (
                    noise * max_force * 3.46
                )

            if boundary == 1:
                # 软边界：越贴墙推得越狠，强度以 max_speed 为单位（要抵消的是已有速度）
                dx = ti.min(1.0, ti.max(0.0, 1.0 - (half_w - ti.abs(p_i[0])) / margin))
                dy = ti.min(1.0, ti.max(0.0, 1.0 - (half_h - ti.abs(p_i[1])) / margin))
                sx = ti.select(p_i[0] >= 0.0, -1.0, 1.0)
                sy = ti.select(p_i[1] >= 0.0, -1.0, 1.0)
                acc += ti.Vector([sx * dx, sy * dy]) * (max_speed * 1.5)

            self._acc[i] = acc
            self._ali_count[i] = ali_n

        # 第二段：只读写自己那一行（积分 + 边界），并行安全
        for i in range(n):
            velocity = self._vel[i] + self._acc[i] * dt
            speed = velocity.norm()
            if speed > max_speed:
                velocity = velocity / speed * max_speed

            position = self._pos[i] + velocity * dt
            if boundary == 0:
                if position[0] < -half_w:
                    position[0] = position[0] + width
                elif position[0] > half_w:
                    position[0] = position[0] - width
                if position[1] < -half_h:
                    position[1] = position[1] + height
                elif position[1] > half_h:
                    position[1] = position[1] - height
            else:
                position[0] = ti.max(-half_w, ti.min(half_w, position[0]))
                position[1] = ti.max(-half_h, ti.min(half_h, position[1]))
                if boundary == 2:
                    if position[0] >= half_w * 0.999 and velocity[0] > 0.0:
                        velocity[0] = -velocity[0]
                    if position[0] <= -half_w * 0.999 and velocity[0] < 0.0:
                        velocity[0] = -velocity[0]
                    if position[1] >= half_h * 0.999 and velocity[1] > 0.0:
                        velocity[1] = -velocity[1]
                    if position[1] <= -half_h * 0.999 and velocity[1] < 0.0:
                        velocity[1] = -velocity[1]

            self._vel[i] = velocity
            self._pos[i] = position
