"""Gray-Scott 反应扩散求解器（纯 NumPy 后端 + 后端工厂）。

两种化学物质在二维网格上互相催化与消耗（``u`` = 基质 / substrate，``v`` = 活化剂 /
activator）::

    ∂u/∂t = Du·∇²u − u·v² + F·(1 − u)
    ∂v/∂t = Dv·∇²v + u·v² − (F + k)·v

``F``（feed，补料率）与 ``k``（kill，移除率）决定图案形态：迷宫、珊瑚纹、斑点、
细胞分裂、螺旋波、混沌蠕虫……:data:`PRESETS` 里是几套经典取值。

离散化与参考实现 ``ga2/common/reaction_diffusion.py`` 逐式一致：

* **九点各向同性拉普拉斯**（权重 ``1/5`` 十字 + ``1/20`` 对角 − 中心，和对角都在
  格距为 1 的网格单位下算，所以没有 ``1/dx²``）：各向同性是这种图案不生棱角的关键，
  五点stencil 会明显偏斜；
* **周期性边界**（环面）：NumPy 用 ``mode="wrap"`` 的 padding，Taichi 用索引取模；
* **显式欧拉**：``dt`` 的上限由扩散决定 —— 九点 stencil 的最负本征值约 ``−1.6``，
  所以 ``dt·Du ≤ 2/1.6 ≈ 1.25``；缺省 ``dt = 1``（与参考实现相同）还有余量；
* 每步把 ``u`` / ``v`` 夹到 ``[0, 1]``，防止显式欧拉在界面处冲过头。

Taichi 后端在 :mod:`sim_reaction_diffusion.taichi_solver`（同一套方程，GPU kernel），
两者 API 相同，由 :func:`create` 按需选择；本模块**不 import taichi**，所以可以脱离
Blender 与 GPU 单独跑（系统 Python + numpy 即可）。
"""

from __future__ import annotations

import numpy as np

__all__ = [
    "BACKEND_AUTO",
    "BACKEND_NUMPY",
    "BACKEND_TAICHI",
    "DEFAULTS",
    "PARAM_KEYS",
    "PRESETS",
    "NumPyReactionDiffusion",
    "create",
    "preset_params",
    "seed_arrays",
    "taichi_available",
]

#: ``create`` 的后端选择。
BACKEND_AUTO = "AUTO"
BACKEND_TAICHI = "TAICHI"
BACKEND_NUMPY = "NUMPY"

#: 默认物理参数（= ``MAZE`` 预设）。
DEFAULTS = {
    "Du": 1.0,  # u 的扩散系数
    "Dv": 0.5,  # v 的扩散系数（v 必须先「追上」u 才有图案，所以 Dv < Du）
    "F": 0.038,  # 补料率
    "k": 0.062,  # 移除率
    "dt": 1.0,  # 显式欧拉时间步（网格单位）
}

PARAM_KEYS = tuple(DEFAULTS)

#: 预设参数表。``v_ref`` 是该图案 v 浓度的典型幅值（实测 99 分位），只用于显示时的
#: 归一化（各预设的 v 幅值差好几倍，不归一化就没法共用一条色带），不参与物理。
#:
#: 参数都在 ``Du = 1`` / ``Dv = 0.5`` / ``dt = 1`` 这套标准尺度下，所以 (F, k) 直接
#: 落在经典 Gray-Scott 参数图上。名称按**实测形态**取：参考实现里的
#: ``F=0.030, k=0.062`` 标的是 "Maze"，但它实际长成六边形斑点阵列（见 README 的
#: 「与参考实现的差别」），所以这里把名称与取值对齐。
PRESETS = {
    "MAZE": {
        "label": "迷宫通道",
        "Du": 1.0,
        "Dv": 0.5,
        "F": 0.038,
        "k": 0.062,
        "v_ref": 0.39,
        "note": "细密的迷宫通道，边界缓慢漂移、偶尔断开",
    },
    "CORAL": {
        "label": "珊瑚指纹",
        "Du": 1.0,
        "Dv": 0.5,
        "F": 0.062,
        "k": 0.062,
        "v_ref": 0.44,
        "note": "更粗更密的指纹条纹，很快铺满全域",
    },
    "SPOTS": {
        "label": "六边形斑点",
        "Du": 1.0,
        "Dv": 0.5,
        "F": 0.030,
        "k": 0.062,
        "v_ref": 0.37,
        "note": "排列成六方点阵的圆斑，像菌落或豹纹",
    },
    "CELLS": {
        "label": "细胞分裂",
        "Du": 1.0,
        "Dv": 0.5,
        "F": 0.032,
        "k": 0.064,
        "v_ref": 0.37,
        "note": "稀疏的圆点：长大、挨上了就合并或分裂",
    },
    "WAVES": {
        "label": "波前火焰",
        "Du": 1.0,
        "Dv": 0.5,
        "F": 0.020,
        "k": 0.052,
        "v_ref": 0.40,
        "note": "向外推进的波前，撞在一起就湮灭（靠近存活边界：撒点太散可能整体熄灭）",
    },
    "WORMS": {
        "label": "粗大蠕虫",
        "Du": 1.0,
        "Dv": 0.5,
        "F": 0.026,
        "k": 0.055,
        "v_ref": 0.34,
        "note": "又粗又慢的蠕虫斑块，一直在缓慢变形",
    },
    "CHAOS": {
        "label": "缠绕蠕虫",
        "Du": 1.0,
        "Dv": 0.5,
        "F": 0.046,
        "k": 0.063,
        "v_ref": 0.41,
        "note": "始终不稳定的缠绕丝，永远在动",
    },
    "HOLES": {
        "label": "实底孔洞",
        "Du": 1.0,
        "Dv": 0.5,
        "F": 0.050,
        "k": 0.060,
        "v_ref": 0.32,
        "note": "负图案：v 几乎铺满全域，只剩一片孔洞",
    },
}

#: 面板上预设下拉的缺省键。
DEFAULT_PRESET = "MAZE"

#: 种子模式（``seed_arrays`` 的 ``mode`` 实参）。
SEED_CENTER = "CENTER"
SEED_MULTI = "MULTI"
SEED_GRID = "GRID"
SEED_NOISE = "NOISE"
SEEDS = (SEED_CENTER, SEED_MULTI, SEED_GRID, SEED_NOISE)

#: 种子处的取值（与参考实现一致：u 压到 0.5、v 拉满）。
_SEED_U = 0.5
_SEED_V = 1.0


def preset_params(key: str) -> dict:
    """预设 → ``{Du, Dv, F, k}``；未知键回退 :data:`DEFAULT_PRESET`。"""
    preset = PRESETS.get(str(key).upper(), PRESETS[DEFAULT_PRESET])
    return {name: float(preset[name]) for name in ("Du", "Dv", "F", "k")}


# --- 初始条件（两个后端共用这一份） ----------------------------------------


def seed_arrays(
    nx: int,
    ny: int,
    mode: str = SEED_CENTER,
    radius_cells: float = 5.0,
    points: np.ndarray | None = None,
    rng_seed: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """铺初始场：``u = 1``、``v = 0``，再在种子上压 ``u = 0.5``、``v = 1``。

    ``mode``：

    * ``CENTER`` —— 中心一个方形核（与参考实现相同）；
    * ``MULTI`` —— 中心 + 四角共五个核，图案从五个地方对撞；
    * ``GRID`` —— 规则阵列，得到整齐的重复单元；
    * ``NOISE`` —— 随机撒点，最快铺满全域，适合看纯图案不看出生过程。

    ``radius_cells`` 是核的半径（格数）；``points`` 是归一化到 ``[-1, 1]`` 的额外点位
    （参考网格的顶点投影），无论 ``mode`` 是什么都会叠加上去。
    返回 ``(u, v)``，形状 ``(nx, ny)``、C 序。
    """
    nx = int(nx)
    ny = int(ny)
    radius = max(1, round(float(radius_cells)))

    u = np.ones((nx, ny), np.float32)
    v = np.zeros((nx, ny), np.float32)

    mode = str(mode).upper()
    cxs, cys = [nx // 2], [ny // 2]
    if mode == SEED_MULTI:
        quarter_x, quarter_y = nx // 4, ny // 4
        cxs = [nx // 2, quarter_x, 3 * quarter_x, quarter_x, 3 * quarter_x]
        cys = [ny // 2, quarter_y, 3 * quarter_y, 3 * quarter_y, quarter_y]
    elif mode == SEED_GRID:
        step = max(4 * radius, min(nx, ny) // 6)
        cxs = list(range(step // 2, nx, step))
        cys = list(range(step // 2, ny, step))
    elif mode == SEED_NOISE:
        # 每 ~2000 格撒一颗，密度足够在几十帧内长出完整图案
        count = max(1, (nx * ny) // 2000)
        rng = np.random.default_rng(int(rng_seed))
        cxs = rng.integers(0, nx, count).tolist()
        cys = rng.integers(0, ny, count).tolist()

    for cx in cxs:
        for cy in cys:
            _stamp(u, v, int(cx), int(cy), radius)

    if points is not None and len(points) > 0:
        pts = np.ascontiguousarray(points, dtype=np.float32).reshape(-1, 2)
        ci = np.clip(((pts[:, 0] + 1.0) * 0.5 * nx).astype(np.int32), 0, nx - 1)
        cj = np.clip(((pts[:, 1] + 1.0) * 0.5 * ny).astype(np.int32), 0, ny - 1)
        for x, y in zip(ci.tolist(), cj.tolist()):
            _stamp(u, v, int(x), int(y), radius)

    return u, v


def _stamp(u: np.ndarray, v: np.ndarray, cx: int, cy: int, radius: int) -> None:
    """在 ``(cx, cy)`` 盖一个方形核（周期性边界：越界的部分绕回另一边）。"""
    nx, ny = u.shape
    rows = np.arange(cx - radius, cx + radius + 1) % nx
    cols = np.arange(cy - radius, cy + radius + 1) % ny
    u[np.ix_(rows, cols)] = _SEED_U
    v[np.ix_(rows, cols)] = _SEED_V


# --- NumPy 后端 -------------------------------------------------------------


class NumPyReactionDiffusion:
    """二维 Gray-Scott 反应扩散求解器（NumPy 后端）。

    字段按 ``[i, j]`` 索引（``i`` 沿 x、``j`` 沿 y），展平时是 C 序 —— 与
    :func:`mesh.grid_geometry` 的顶点编号 ``i*ny + j`` 一致，所以 ``v_flat()`` 可以
    直接写进顶点属性 / 高度。
    """

    backend = "numpy"

    def __init__(
        self,
        nx: int = 200,
        ny: int = 200,
        params: dict | None = None,
    ):
        self.nx = int(nx)
        self.ny = int(ny)

        self.params = dict(DEFAULTS)
        self.params.update(self._clean(params or {}))

        shape = (self.nx, self.ny)
        #: 当前场与下一步的缓冲（每步交换，不再分配）。
        self.u = np.ones(shape, np.float32)
        self.v = np.zeros(shape, np.float32)
        self._u_next = np.ones(shape, np.float32)
        self._v_next = np.zeros(shape, np.float32)

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

    # --- 初始化 / 注种 ------------------------------------------------------

    def initialize(
        self,
        mode: str = SEED_CENTER,
        radius_cells: float = 5.0,
        points: np.ndarray | None = None,
        rng_seed: int = 0,
    ) -> None:
        """重铺初始场（``u = 1`` / ``v = 0`` + 种子）。"""
        u, v = seed_arrays(self.nx, self.ny, mode, radius_cells, points, rng_seed)
        self.u[...] = u
        self.v[...] = v

    def inject_seed(self, cx: int, cy: int, radius_cells: float = 4.0) -> None:
        """在格坐标 ``(cx, cy)`` 注一颗新种子（不重置其他区域）。"""
        radius = max(1, round(float(radius_cells)))
        _stamp(self.u, self.v, int(cx), int(cy), radius)

    # --- 物理 ---------------------------------------------------------------

    @staticmethod
    def _laplacian(field: np.ndarray) -> np.ndarray:
        """九点各向同性拉普拉斯，周期性边界（``wrap`` padding）。

        权重：中心 −1、十字 0.2、对角 0.05（和为 0，常数场拉普拉斯为 0）。
        """
        pad = np.pad(field, 1, mode="wrap")
        centre = pad[1:-1, 1:-1]
        cardinals = pad[2:, 1:-1] + pad[:-2, 1:-1] + pad[1:-1, 2:] + pad[1:-1, :-2]
        diagonals = pad[2:, 2:] + pad[2:, :-2] + pad[:-2, 2:] + pad[:-2, :-2]
        return 0.2 * cardinals + 0.05 * diagonals - centre

    def _substep(self) -> None:
        """一个显式欧拉子步。"""
        p = self.params
        u, v = self.u, self.v
        dt = p["dt"]

        reaction = u * v * v
        np.clip(
            u + (p["Du"] * self._laplacian(u) - reaction + p["F"] * (1.0 - u)) * dt,
            0.0,
            1.0,
            out=self._u_next,
        )
        np.clip(
            v + (p["Dv"] * self._laplacian(v) + reaction - (p["F"] + p["k"]) * v) * dt,
            0.0,
            1.0,
            out=self._v_next,
        )

        self.u, self._u_next = self._u_next, self.u
        self.v, self._v_next = self._v_next, self.v

    def step(self, substeps: int = 1) -> None:
        """推进 ``substeps`` 个时间步（每帧的推进量由面板的「子步」给）。"""
        for _ in range(max(1, int(substeps))):
            self._substep()

    # --- 导出 ---------------------------------------------------------------

    def u_flat(self) -> np.ndarray:
        """基质浓度展平成顶点顺序（``index = i*ny + j``，C 序）。"""
        return self.u.ravel()

    def v_flat(self) -> np.ndarray:
        """活化剂浓度展平成顶点顺序（同上）。"""
        return self.v.ravel()

    def fields(self) -> tuple[np.ndarray, np.ndarray]:
        """``(u, v)`` 的展平副本（每帧只看一眼时用这个，避免误改内部状态）。"""
        return self.u.ravel().copy(), self.v.ravel().copy()


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
            from .taichi_solver import TaichiReactionDiffusion

            return TaichiReactionDiffusion(nx, ny, params=params)
        except Exception as exc:
            if choice == BACKEND_TAICHI:
                raise RuntimeError(f"Taichi 后端不可用: {exc}") from exc
            solver = NumPyReactionDiffusion(nx, ny, params=params)
            solver.note = f"Taichi 不可用（{exc}），已回退 NumPy"
            return solver
    return NumPyReactionDiffusion(nx, ny, params=params)
