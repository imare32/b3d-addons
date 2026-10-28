"""太空殖民（space colonization）求解器：纯 NumPy 主体 + 可插拔的最近邻搜索。

算法（Runions 等 2007 的经典形式）每一子步做四件事：

1. **最近邻**：每个吸收点（attractor）找离它最近的树枝节点；
2. **消耗**：距离 ≤ ``kill_radius`` 的吸收点被"吃掉"（该处已经有枝条了）；
3. **聚拢**：距离 ≤ ``influence_radius`` 的吸收点把方向向量投给它的最近节点，节点汇成
   一个平均方向；
4. **生长**：该节点朝「平均方向 × (1−持久度) + 旧方向 × 持久度」推进 ``growth_step``，
   新节点挂在这个节点下面（``nodes_parent``）。

半径用 **Murray 定律的离散版**：叶子数为 1，往上累加子孙叶子数，``r = r0 · tips^taper``
（``thickness`` 默认 0.0045、``taper`` 默认 0.2）—— 根粗尖细，分叉处自然变粗。深度
``nodes_depth`` 与叶子数都是 O(N) 的按层累加（父节点索引恒小于子节点），不需要递归。
扫出来的管子还会过一遍 :func:`display_geometry`（折线平滑 + 对数域半径平滑 + 叶尖收细），
把「一格一格」的台阶和整数叶尖数的突变抹开，粗细过渡才自然。

**单位约定**：所有长度参数都是**相对输入网格包围盒对角线长度**的比例（``unit``），
由调用方乘好再传进来 —— 这样换一个 1 cm 的小零件和 10 m 的建筑，同一套面板参数都成立。

**搜索后端**（本模块只管 NumPy 那份，不 import bpy、不 import taichi）::

    NumPySearch   分块暴力搜索，零依赖，O(节点 × 吸收点)
    KDTreeSearch  scipy 的 cKDTree，O(N log N)，大场景快得多（scipy 缺失时不可用）
    TaichiSearch  taichi kernel 的并行暴力搜索，见 :mod:`sim_space_colonization.taichi_solver`

:func:`create` 按「自动 → KD 树 → taichi → NumPy」挑一个，并在 ``note`` 里写清用了谁、
为什么回退。
"""

from __future__ import annotations

import numpy as np

__all__ = [
    "BACKEND_AUTO",
    "BACKEND_NUMPY",
    "BACKEND_TAICHI",
    "DEFAULTS",
    "PARAM_KEYS",
    "KDTreeSearch",
    "NumPySearch",
    "SpaceColonization",
    "branch_chains",
    "create",
    "display_geometry",
    "taichi_available",
]

#: ``create`` 的后端选择。
BACKEND_AUTO = "AUTO"
BACKEND_TAICHI = "TAICHI"
BACKEND_NUMPY = "NUMPY"

#: 默认参数。长度类（``influence_radius`` / ``kill_radius`` / ``growth_step`` /
#: ``thickness``）是**相对包围盒对角线**的比例，其余是无量纲量。
DEFAULTS = {
    "influence_radius": 0.12,  # 吸引半径
    "kill_radius": 0.02,  # 消耗半径（≈ 吸收点之间的平均间距）
    "growth_step": 0.012,  # 每步生长长度（≈ 间距的一半）
    "dir_persistence": 0.55,  # 方向持久度（越大越挺拔，越小越随风摆）
    "jitter": 0.12,  # 方向抖动幅度
    "thickness": 0.0045,  # 最细枝（一个叶尖）的半径
    "taper": 0.2,  # 半径的 Murray 指数：r ∝ 叶尖数^taper
}

PARAM_KEYS = tuple(DEFAULTS)


def _clean(params: dict | None) -> dict:
    """只收下已知参数并转成 float（面板可能多传 / 少传）。"""
    return {
        key: float(value)
        for key, value in (params or {}).items()
        if key in DEFAULTS and value is not None
    }


def _unit(vectors: np.ndarray) -> np.ndarray:
    """逐行归一化（零向量原样返回，不产生 NaN）。"""
    array = np.asarray(vectors, np.float32).reshape(-1, 3)
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    return array / np.maximum(norms, 1e-9)


def _tangential(directions: np.ndarray, normals: np.ndarray) -> np.ndarray:
    """把生长方向压到表面切平面里再归一化。

    表面模式**必须先切向化再位移**：只靠最后那一步投影的话，方向一旦指向表面内部，
    投影会把点原样拉回来，枝条一步都走不动（参考实现贴在陡面上时就会原地打转）。

    切向分量退化为零（方向正好顶着法向）时用一个与法向垂直的固定轴兜底 —— 这种情况在
    「一个点被整圈吸收点围住」时很常见，兜底方向随后还会被抖动打散，不会是死板的直线。
    """
    normals = _unit(normals)
    projection = np.einsum("ij,ij->i", directions, normals)[:, None]
    tangent = directions - projection * normals
    lengths = np.linalg.norm(tangent, axis=1, keepdims=True)
    flat = lengths[:, 0] > 1e-4

    result = np.zeros_like(tangent)
    result[flat] = tangent[flat] / lengths[flat]
    if not flat.all():
        edge = normals[~flat]
        axis = np.where(
            np.abs(edge[:, 2:3]) > 0.9,
            np.array([[1.0, 0.0, 0.0]], np.float32),
            np.array([[0.0, 0.0, 1.0]], np.float32),
        )
        result[~flat] = _unit(np.cross(edge, axis))
    return result


# --- 最近邻搜索后端 --------------------------------------------------------


class NumPySearch:
    """分块暴力搜索：零依赖，内存有界，慢。

    分块大小按「块内元素数 × 节点数」封顶（默认 150 万），所以节点数涨到几万时单块
    只剩几十个吸收点 —— 时间线性增长，内存不炸。
    """

    backend = "numpy"
    note = "NumPy 暴力搜索（零依赖，最慢）"

    def __init__(self, budget: int = 1_500_000):
        self.budget = int(budget)

    def nearest(self, nodes: np.ndarray, attractors: np.ndarray):
        """``(最近节点索引, 距离)``，长度 = 吸收点数。"""
        count = len(attractors)
        closest = np.full(count, -1, np.int32)
        distance = np.full(count, np.inf, np.float32)
        node_count = len(nodes)
        if count == 0 or node_count == 0:
            return closest, distance

        chunk = max(8, int(self.budget // max(1, node_count)))
        for start in range(0, count, chunk):
            block = attractors[start : start + chunk]
            delta = nodes[None, :, :] - block[:, None, :]
            d2 = np.einsum("bnk,bnk->bn", delta, delta)
            best = np.argmin(d2, axis=1)
            closest[start : start + len(block)] = best
            distance[start : start + len(block)] = np.sqrt(
                d2[np.arange(len(block)), best]
            )
        return closest, distance


class KDTreeSearch:
    """scipy 的 ``cKDTree``：每次调用重建索引（节点数每步都在涨），大场景快得多。"""

    backend = "kdtree"
    note = "scipy cKDTree 最近邻"

    def __init__(self):
        from scipy.spatial import cKDTree  # 缺 scipy 时给调用方抛 ImportError

        self._tree = cKDTree

    def nearest(self, nodes: np.ndarray, attractors: np.ndarray):
        count = len(attractors)
        if count == 0 or len(nodes) == 0:
            return np.full(count, -1, np.int32), np.full(count, np.inf, np.float32)
        tree = self._tree(np.ascontiguousarray(nodes, np.float32))
        distance, closest = tree.query(
            np.ascontiguousarray(attractors, np.float32), k=1
        )
        return closest.astype(np.int32), distance.astype(np.float32)


def taichi_available() -> bool:
    """本进程能否 ``import taichi``（第一次调用要几百毫秒，别在面板 draw 里调）。"""
    try:
        import taichi  # noqa: F401
    except Exception:
        return False
    return True


def create(backend: str = BACKEND_AUTO):
    """建最近邻搜索器：``AUTO`` 依次试 KD 树 → taichi → NumPy。

    显式给 ``TAICHI`` 时初始化失败会抛异常（面板会显示原因）；``NUMPY`` 永远是纯 NumPy。
    回退原因写在实例的 ``note`` 上，供面板读数显示。
    """
    choice = str(backend).upper()
    reasons = []

    # 显式给 NUMPY 时不碰 scipy：用户要的是「零依赖那一档」，替它换了实现反而意外
    if choice == BACKEND_AUTO:
        try:
            return KDTreeSearch()
        except Exception as exc:  # scipy 没装 / 版本不对
            reasons.append(f"scipy 不可用（{exc}）")

    if choice in (BACKEND_AUTO, BACKEND_TAICHI):
        try:
            from .taichi_solver import TaichiSearch

            return TaichiSearch()
        except Exception as exc:
            if choice == BACKEND_TAICHI:
                raise RuntimeError(f"Taichi 后端不可用: {exc}") from exc
            reasons.append(f"taichi 不可用（{exc}）")

    search = NumPySearch()
    if reasons:
        search.note = "；".join(reasons) + "，已回退 NumPy 暴力搜索"
    return search


# --- 求解器 ----------------------------------------------------------------


class SpaceColonization:
    """太空殖民生长：节点树 + 吸收点云 + 逐子步推进。

    字段（都是 numpy 数组，索引即节点编号，父节点索引恒小于子节点）::

        nodes_pos     (N, 3) float32  世界坐标
        nodes_parent  (N,)   int32    根为 −1
        nodes_dir     (N, 3) float32  上一段的方向（方向持久度用）
        nodes_depth   (N,)   int32    离根几段
        nodes_tips    (N,)   float32  子孙叶尖数（半径用）
        attractors    (M, 3) float32  还没被吃掉的吸收点
    """

    def __init__(self, params: dict | None = None, search=None):
        self.params = dict(DEFAULTS)
        self.params.update(_clean(params))
        self.search = search if search is not None else NumPySearch()

        self.nodes_pos = np.zeros((0, 3), np.float32)
        self.nodes_parent = np.zeros((0,), np.int32)
        self.nodes_dir = np.zeros((0, 3), np.float32)
        self.nodes_depth = np.zeros((0,), np.int32)
        self.nodes_tips = np.zeros((0,), np.float32)
        self.attractors = np.zeros((0, 3), np.float32)

        self.initialized = False
        #: 上次 ``step`` 是否推进出任何节点（面板用它判断"卡住了"）
        self.growing = False
        self._rng = np.random.default_rng(42)

    # --- 参数 --------------------------------------------------------------

    @property
    def backend(self) -> str:
        return str(getattr(self.search, "backend", "numpy"))

    @property
    def note(self) -> str:
        return str(getattr(self.search, "note", ""))

    def set_params(self, **kwargs) -> None:
        """就地更新参数（下一子步生效，不重置结构）。"""
        self.params.update(_clean(kwargs))

    # --- 初始化 ------------------------------------------------------------

    def initialize(self, roots, attractors, seed: int = 42) -> None:
        """铺初始状态：若干根点 + 一整片吸收点。没有吸收点就不算初始化。"""
        positions = np.asarray(roots, np.float32).reshape(-1, 3)
        cloud = np.asarray(attractors, np.float32).reshape(-1, 3)

        count = len(positions)
        self.nodes_pos = np.array(positions, np.float32)
        self.nodes_parent = np.full(count, -1, np.int32)
        self.nodes_dir = self._root_directions(positions, cloud)
        self.nodes_depth = np.zeros(count, np.int32)
        self.nodes_tips = np.zeros(count, np.float32)
        self.attractors = np.array(cloud, np.float32)

        self._rng = np.random.default_rng(int(seed))
        self.initialized = count > 0 and len(cloud) > 0
        self.growing = False

    def _root_directions(self, roots: np.ndarray, cloud: np.ndarray) -> np.ndarray:
        """根的初始方向：朝附近吸收点的平均方向（没有就用 +Z）。

        这一步比参考实现的「一律 +Z」重要：贴在竖直面上的藤蔓，+Z 会直接顶进表面，
        第一步就被投影拉回来。
        """
        count = len(roots)
        fallback = np.tile(np.array([[0.0, 0.0, 1.0]], np.float32), (count, 1))
        if len(cloud) == 0:
            return fallback

        radius = float(self.params["influence_radius"])
        directions = np.array(fallback, np.float32)
        for index, root in enumerate(roots):
            delta = cloud - root
            distance = np.linalg.norm(delta, axis=1)
            near = distance <= radius
            if not near.any():
                continue
            # 距离倒数加权：近的吸收点说话更响，避免被远处一片点拉偏
            weight = (1.0 / np.maximum(distance[near], 1e-9))[:, None]
            mean = (delta[near] * weight).sum(axis=0)
            norm = float(np.linalg.norm(mean))
            if norm > 1e-9:
                directions[index] = mean / norm
        return directions

    # --- 推进 --------------------------------------------------------------

    def step(
        self,
        substeps: int = 1,
        max_nodes: int = 20000,
        surface=None,
        surface_offset: float = 0.0,
    ) -> bool:
        """推进 ``substeps`` 个子步，返回本次是否有新节点长出来。

        ``surface`` 是可选的表面约束（鸭子类型，实现见 :mod:`surface`）::

            surface.normals_at(points) -> (n, 3) 单位法向
            surface.snap(points)       -> (n, 3) 最近表面点
        """
        grown = False
        for _ in range(max(1, int(substeps))):
            if len(self.nodes_pos) >= int(max_nodes) or len(self.attractors) == 0:
                break
            if not self._substep(int(max_nodes), surface, float(surface_offset)):
                break  # 本子步没动：本帧后面也不必再试
            grown = True
        self.growing = grown
        return grown

    def _substep(self, max_nodes: int, surface, surface_offset: float) -> bool:
        params = self.params
        if len(self.attractors) == 0:
            return False

        closest, distance = self.search.nearest(self.nodes_pos, self.attractors)

        # 1. 消耗：已经贴到枝条上的吸收点
        keep = distance > float(params["kill_radius"])
        if not keep.all():
            self.attractors = self.attractors[keep]
            closest = closest[keep]
            distance = distance[keep]
        if len(self.attractors) == 0:
            return False

        # 2. 聚拢：只有落在吸引半径内的吸收点才出力
        active = distance <= float(params["influence_radius"])
        if not active.any():
            return False
        parent_index = closest[active]
        vectors = self.attractors[active] - self.nodes_pos[parent_index]
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        vectors = vectors / np.maximum(norms, 1e-9)

        # 3. 每个有吸收点的节点合成一个平均方向
        parents, inverse = np.unique(parent_index, return_inverse=True)
        accumulated = np.zeros((len(parents), 3), np.float32)
        np.add.at(accumulated, inverse.ravel(), vectors)
        norms = np.linalg.norm(accumulated, axis=1, keepdims=True)
        growth = accumulated / np.maximum(norms, 1e-9)

        # 4. 方向持久度 → 抖动 → 归一化
        #
        # 抖动放在持久度**之后**：表面模式下平均方向常常正好顶着法向（一个点在曲面上
        # 被一整圈吸收点围住时，切向分量会互相抵消），此时只有抖动量的切向分量能给出
        # 方向 —— 放在前面会被持久度那一项冲掉，第一根枝条就卡在原地。
        persistence = float(params["dir_persistence"])
        growth = (1.0 - persistence) * growth + persistence * self.nodes_dir[parents]
        norms = np.linalg.norm(growth, axis=1, keepdims=True)
        growth = growth / np.maximum(norms, 1e-9)

        jitter = float(params["jitter"])
        if jitter > 0.0:
            growth += self._rng.uniform(-jitter, jitter, growth.shape).astype(
                np.float32
            )
            norms = np.linalg.norm(growth, axis=1, keepdims=True)
            growth = growth / np.maximum(norms, 1e-9)

        step = float(params["growth_step"])
        normals = None
        if surface is not None:
            normals = _unit(surface.normals_at(self.nodes_pos[parents]))
            growth = _tangential(growth, normals)

        new_pos = self.nodes_pos[parents] + step * growth
        if surface is not None:
            new_pos = np.asarray(surface.snap(new_pos), np.float32)
            if surface_offset != 0.0:
                new_pos += _unit(surface.normals_at(new_pos)) * float(surface_offset)

        # 5. 退化节点剔除：投影把点拉回原处时不追加（否则节点会在曲面上堆成一坨）
        advance = np.linalg.norm(new_pos - self.nodes_pos[parents], axis=1)
        valid = advance >= step * 0.3
        if not valid.any():
            return False
        parents = parents[valid]
        growth = growth[valid]
        new_pos = new_pos[valid]

        room = int(max_nodes) - len(self.nodes_pos)
        if len(new_pos) > room:
            new_pos = new_pos[:room]
            parents = parents[:room]
            growth = growth[:room]
        if len(new_pos) == 0:
            return False

        self.nodes_pos = np.concatenate([self.nodes_pos, new_pos.astype(np.float32)])
        self.nodes_parent = np.concatenate(
            [self.nodes_parent, parents.astype(np.int32)]
        )
        self.nodes_dir = np.concatenate([self.nodes_dir, growth.astype(np.float32)])
        self.nodes_depth = np.concatenate(
            [self.nodes_depth, (self.nodes_depth[parents] + 1).astype(np.int32)]
        )
        self.nodes_tips = np.concatenate(
            [self.nodes_tips, np.zeros(len(new_pos), np.float32)]
        )
        return True

    # --- 导出 --------------------------------------------------------------

    def update_tips(self) -> np.ndarray:
        """按层累加**子孙叶尖数**（``nodes_tips`` 就地刷新），返回它。

        父节点索引恒小于子节点，且 ``depth = parent.depth + 1``，所以按深度从大到小
        一层一层往上加即可：每层一次 ``bincount``，总共 O(N)。

        **只有叶子（没有子节点的节点）记 1**，内部节点一律从 0 起累加。早先这里用
        ``np.ones(count)`` 起手，等于把每个内部节点自己也当一片叶子，累出来的是「子树
        节点数」而不是叶尖数 —— 一条只有一片叶子的长枝会被算成 60，半径凭空粗 4 倍，
        整棵树的粗细就乱了（叶尖多、枝条细的原则反过来变成了「枝条越长越粗」）。
        """
        count = len(self.nodes_pos)
        children = np.bincount(self.nodes_parent[1:], minlength=count)
        tips = (children == 0).astype(np.float32)
        if count <= 1:
            self.nodes_tips = tips
            return tips

        depth = self.nodes_depth
        for level in range(int(depth.max()), 0, -1):
            mask = depth == level
            if not mask.any():
                continue
            parents = self.nodes_parent[mask]
            gathered = np.bincount(
                parents[parents >= 0],
                weights=tips[mask][parents >= 0],
                minlength=count,
            )
            tips += gathered.astype(np.float32)
        self.nodes_tips = tips
        return tips

    def radii(
        self, thickness: float | None = None, taper: float | None = None
    ) -> np.ndarray:
        """每节点半径：``thickness · tips^taper``（Murray 定律的离散版）。"""
        tips = self.update_tips()
        base = float(self.params["thickness"] if thickness is None else thickness)
        power = float(self.params["taper"] if taper is None else taper)
        return (base * np.power(np.maximum(tips, 1.0), power)).astype(np.float32)

    def depth_ratio(self) -> np.ndarray:
        """归一化深度（根 0，最远枝尖 1），配色和着色器都能直接用。"""
        if len(self.nodes_depth) == 0:
            return np.zeros((0,), np.float32)
        span = max(float(self.nodes_depth.max()), 1.0)
        return (self.nodes_depth / span).astype(np.float32)

    def edges(self) -> np.ndarray:
        """``(E, 2) int32``：每对 ``(子节点, 父节点)``，可直接塞进 ``mesh.edges``。"""
        count = len(self.nodes_pos)
        if count <= 1:
            return np.zeros((0, 2), np.int32)
        child = np.arange(1, count, dtype=np.int32)
        return np.stack([child, self.nodes_parent[1:]], axis=1).astype(np.int32)


# --- 显示前处理（只影响扫出来的管子，不动求解器状态）-------------------------
#
# 生长是「一格一格往前推」的：节点连成的折线本来就带锯齿，半径又是按整数叶尖数算的，
# 于是扫出来的管子近距离看是一节一节的折面、分叉处突然粗一截 —— 就是「粗糙破碎」的来源。
# 下面三个函数把这两件事捋顺，代价是 O(N × iterations) 的 bincount。


def _smooth_positions(
    positions: np.ndarray,
    parent: np.ndarray,
    iterations: int = 2,
    strength: float = 0.5,
) -> np.ndarray:
    """沿树做几遍拉普拉斯平滑（根钉住），把节点折线捋顺。"""
    result = np.asarray(positions, np.float32).copy()
    count = len(result)
    if count < 3 or iterations <= 0:
        return result

    child = np.arange(1, count)
    elder = np.asarray(parent[1:], np.int64)
    keep = elder >= 0
    child, elder = child[keep], elder[keep]
    roots = np.asarray(parent, np.int64) < 0  # 可能有多个根

    for _ in range(iterations):
        # 用 bincount 做「散射累加」而不是 np.add.at：后者是无缓冲的逐元素累加，实测慢 2.7 倍
        # （一万节点的树，一次邻域和 2.44 ms vs 0.90 ms）。表是 (N,3)，所以逐分量算三遍。
        up = result[child]  # 每个子节点给父的贡献
        down = result[elder]  # 每个父节点给子的贡献
        total = np.empty((count, 3), np.float32)
        for axis in range(3):
            total[:, axis] = np.bincount(
                elder, weights=up[:, axis], minlength=count
            ) + np.bincount(child, weights=down[:, axis], minlength=count)
        amount = np.bincount(elder, minlength=count) + np.bincount(
            child, minlength=count
        )
        neighbour = total / np.maximum(amount, 1.0)[:, None]
        blended = (1.0 - strength) * result + strength * neighbour
        blended[roots] = result[roots]  # 根不动：整棵树是从这里长出去的
        result = blended
    return result


def _smooth_radii(
    radius: np.ndarray, parent: np.ndarray, iterations: int = 8, strength: float = 0.5
) -> np.ndarray:
    """把半径的台阶抹成过渡（分叉处半径按整数叶尖数算，相邻节点会突然差一截）。

    **在对数域里平滑**：半径是乘性的（每一级分叉乘一个比值），线性平均会把「粗枝」和
    「细枝」混成均值，过渡带上反而更糊；取对数之后「每级掉多少」才是可加的量，平滑出来
    是一条指数渐变的锥度 —— 也就是肉眼期待的「慢慢变细」。

    平滑会顺手削薄主干，所以最后按「最大半径不变」整体缩放回去。
    """
    result = np.asarray(radius, np.float32).copy()
    count = len(result)
    if count < 3 or iterations <= 0:
        return result

    peak = float(result.max())
    values = np.log(np.maximum(result, 1e-9))
    child = np.arange(1, count)
    elder = np.asarray(parent[1:], np.int64)
    keep = elder >= 0
    child, elder = child[keep], elder[keep]
    roots = np.asarray(parent, np.int64) < 0
    root_values = values[roots].copy()

    for _ in range(iterations):
        # 同 _smooth_positions：bincount 替掉 np.add.at（这里只有一列，省得更明显）
        total = np.bincount(
            elder, weights=values[child], minlength=count
        ) + np.bincount(child, weights=values[elder], minlength=count)
        amount = np.bincount(elder, minlength=count) + np.bincount(
            child, minlength=count
        )
        neighbour = total / np.maximum(amount, 1.0)
        values = (1.0 - strength) * values + strength * neighbour
        values[roots] = root_values  # 根不参与平均：主干的粗细以它为准

    smoothed = np.exp(values)
    scale = peak / max(float(smoothed.max()), 1e-9)
    return (smoothed * scale).astype(np.float32)


def _taper_tips(
    radius: np.ndarray, parent: np.ndarray, depth: int = 5, floor: float = 0.25
) -> np.ndarray:
    """叶尖往回几个节点收细：枝条末端是尖的，笔帽不至于戳出一个圆头小疙瘩。

    一个节点可能「下方」挂着好几片叶子（分叉后再分叉），它该取哪片叶子算出来的收细量？
    因为收细量随距离单调变大，取**最近**那片叶子即可 —— 换句话说每个节点的系数是
    ``floor + (1 − floor) · min(到最近叶尖的边数, span) / span``。

    实现按「层」推进而不是逐叶爬：第一层是所有叶子，第二层是它们的父节点（去掉已经算过的），
    …… 最多 ``span`` 层。每个节点只在第一次（也就是离它最近的那片叶子）被赋值，天然取了
    最小值。整个循环只做向量化的 gather + unique，不再有逐叶的 Python 双重循环
    （一万节点的树上实测 17.8 ms → 1 ms 出头）。
    """
    result = np.asarray(radius, np.float32).copy()
    count = len(result)
    if count < 2 or depth <= 0:
        return result

    parent = np.asarray(parent, np.int64)
    elder = parent[1:]
    children = np.bincount(elder[elder >= 0], minlength=count)
    factor = np.ones(count, np.float32)
    span = max(int(depth), 1)

    reached = np.zeros(count, bool)
    front = np.nonzero(children == 0)[0]  # 第 0 层：所有叶尖
    for step in range(span + 1):
        if len(front) == 0:
            break
        factor[front] = floor + (1.0 - floor) * (step / span)
        reached[front] = True
        nxt = parent[front]
        nxt = nxt[nxt >= 0]
        nxt = nxt[~reached[nxt]]
        if len(nxt) == 0:
            break
        front = np.unique(nxt)  # 兄弟节点的父会被重复点到，去重
    return result * factor


def display_geometry(solver, radius) -> tuple[np.ndarray, np.ndarray]:
    """求解器状态 → 笔划用的 ``(位置, 半径)``：捋顺折线、抹平台阶、收尖叶尖。

    ``radius`` 传 :meth:`SpaceColonization.radii` 乘好面板粗细的结果；返回的半径可以直接
    写进 GP 的 ``radius`` 属性。求解器自己的状态一个字节都不改。
    """
    parent = solver.nodes_parent
    smoothed = _smooth_radii(np.asarray(radius, np.float32), parent)
    smoothed = _taper_tips(smoothed, parent)
    return _smooth_positions(solver.nodes_pos, parent), smoothed


def branch_chains(parent) -> tuple[np.ndarray, np.ndarray]:
    """节点树 → 笔划拓扑：``(counts, indices)``。

    一条笔划 = 从根或某个分叉点，走到下一个分叉点或枝尖。分叉点在相邻两条笔划里各出现
    一次（端点重合，GP 的圆头笔帽负责咬合），所以 ``sum(counts)`` 略多于节点数。

    ``counts[S]`` 是每条笔划的点数，``indices`` 是展平的节点索引、长度 ``sum(counts)``。

    实现全部向量化（一万节点的树上 11.0 ms → 0.6 ms）：早先是逐节点建 ``heads``、再逐节点
    走链的 Python 双循环 —— 每帧都要跑，是「停滞期掉帧」里剩下的最大一块。做法是先把每个
    节点归到「离它最近的那个链头」（链头 = 根 / 分叉点 / 叶），对只有一个孩子的节点沿链一路
    传播；再把节点按链头做一次稳定排序，同组的索引天然升序（父节点索引恒小于子节点），
    于是每条链就是一段连续区间，头 + 区间里的孩子拼起来即可。
    """
    parent = np.asarray(parent, np.int64)
    count = len(parent)
    if count < 2:
        return np.zeros((0,), np.int32), np.zeros((0,), np.int32)

    parent_of_tail = parent[1:]
    child_index = np.arange(1, count, dtype=np.int64)
    valid = parent_of_tail >= 0
    children = np.bincount(parent_of_tail[valid], minlength=count)

    # 链头：根（可能有多个）、分叉点、叶 —— 都不是「恰好一个孩子」的普通中间节点
    is_head = (parent < 0) | (children != 1)

    # 只有一个孩子的节点 → 这条链的下一步
    sole = np.full(count, -1, np.int64)
    single = valid & (children[np.maximum(parent_of_tail, 0)] == 1)
    sole[parent_of_tail[single]] = child_index[single]

    # 一条链由「链头的那个孩子」唯一标识（同一个分叉头有几条链，就是靠它分开的）：
    # 沿 sole 把这个标识从链头的孩子一路往下传播
    chain_id = np.full(count, -1, np.int64)
    starts = valid & is_head[np.maximum(parent_of_tail, 0)]
    front = child_index[starts]
    chain_id[front] = front
    while len(front):
        keep = sole[front] >= 0
        if not keep.any():
            break
        source = front[keep]
        step = sole[source]
        chain_id[step] = chain_id[source]
        front = step

    member = np.nonzero(chain_id >= 0)[0]
    if len(member) == 0:
        return np.zeros((0,), np.int32), np.zeros((0,), np.int32)

    ids = chain_id[member]
    # 先按链头、再按起始孩子排（与旧的逐节点输出顺序一致，纯粹是让比特对得上）
    order = np.lexsort((ids, parent[ids]))
    ids, member = ids[order], member[order]

    # 同一条链的节点在数组里是连续的，`ids` 变化处就是链的边界
    first = np.concatenate([[0], np.nonzero(np.diff(ids) != 0)[0] + 1])
    lengths = np.diff(np.concatenate([first, [len(ids)]]))
    counts = (lengths + 1).astype(np.int32)
    offsets = np.zeros(len(counts), np.int64)
    offsets[1:] = np.cumsum(counts[:-1])
    flat = np.empty(int(counts.sum()), np.int64)
    flat[offsets] = parent[ids[first]]  # 每条链的第一个点是链头
    slot = np.ones(len(flat), bool)
    slot[offsets] = False
    flat[slot] = member  # 其余依次是这条链上的节点（索引升序 = 沿链顺序）
    return counts, flat.astype(np.int32)
