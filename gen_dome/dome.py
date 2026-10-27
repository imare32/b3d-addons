"""斐波那契半球 + 球面 Voronoi：逐代的 Panel 多边形（纯 numpy，不 import bpy）。

**生长就是重划分**（照搬参考实现）：每一帧拿新的点数把**整座半球**重算一遍 —— 第 1 帧
3 个点就是 3 块大面板铺满整个半球，之后点数逐帧 +1，面板越来越多、每块越来越小，叶序螺旋
跟着整体重排。任何一帧的穹顶都是**完整**的半球、尺寸不变，变的只是格子的粗细 —— 所以看着
像表面在缓慢地淌，不会忽大忽小。

这里把这件事做成「**代**」：第 ``i`` 代是一份独立算好的 Voronoi 划分，所有代拼进同一份网格
（逐面属性 ``gen`` 记代号），几何节点按当前帧挑一代显示。于是：

* 每一代都是**真正的 Voronoi 划分**（不是「同一批面板删掉几块」）；
* 播放时只是「切代」，不用实时算 Voronoi，滚动 / 倒放 / 循环都是即时的；
* 一代一点、一帧一代（默认参数），与参考实现逐帧重算的结果逐块相同，只是不用等。

算法两步，都有明确的出处：

1. **点** —— 半球上的斐波那契（向日葵）排布：方位角按黄金角一步步转，``z`` 在
   ``0..1`` 上逐点均分。``z`` 均分 ⇒ 球面上按**面积**均匀（球带的面积正比于 ``z`` 的差），
   所以点不会在顶点挤成一团。索引 0 贴着赤道、末尾贴着顶点。
2. **多边形** —— 每个点的球面 Voronoi 单元 = 一堆过原点的半空间之交
   （``x·(p_i - p_j) >= 0``，即「离我比离 j 近」）。实现上从种子切平面上的一块大多边形
   出发，逐个半空间裁剪（Sutherland–Hodgman，向量化），裁完才把顶点投影回球面。

参考实现（``ga2`` 的 ``fibonacci_voronoi.py``）走的是 ``scipy.spatial.SphericalVoronoi``：
把上半球的点镜像到下半球凑成一个整球再算。这里不镜像、也不依赖 scipy，原因有两条：

* **镜像没必要**：镜像点只在赤道以下才可能更近（对 ``z > 0`` 的 ``x``，
  ``|x - p'|² = |x - p|² + 4 z z_p > |x - p|²``），所以上半球里镜像点从来不构成约束。
  把单元直接裁到 ``z >= 0`` 得到的多边形与镜像法逐点相同，还省掉「两个几乎重合的点」
  带来的数值退化。
* **scipy 不是 Blender 自带的**：装了 ``scipy`` 的机器上能跑、别人的机器上 import 就炸。
  本仓库的约定是除 numpy 之外不带依赖（见根 ``README.md``）。

复杂度 O(点数 × 邻居数 × 多边形边数)：每个单元只拿最近的 ``_NEIGHBOURS`` 个点做裁剪
（单元最多 6~8 条边，16 个邻居已经绰绰有余）。单个半球 1000 个点约 0.13 s。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

__all__ = [
    "GENERATION_MAX",
    "GOLDEN_ANGLE",
    "MAX_POINTS",
    "MIN_POINTS",
    "PANEL_BUDGET",
    "Panels",
    "build_generations",
    "build_panels",
    "fibonacci_cap",
    "fibonacci_hemisphere",
    "generation_count",
    "spherical_voronoi",
]

#: 黄金角：向日葵排布里相邻两点的方位差，``π(3-√5)``。
GOLDEN_ANGLE = float(np.pi * (3.0 - np.sqrt(5.0)))

#: 点数上下限：3 是能形成包围关系的最小值；再往上单元会细到看不清，也拖慢裁剪。
MIN_POINTS = 3
MAX_POINTS = 4000

#: 最多烘多少代（安全阀；真正的上限是面数预算）。
GENERATION_MAX = 256

#: 生长期望的面板总数上限：每块面板约 0.1 ms，1.2 万块 ≈ 1.3 s，再往上就该减点数了。
PANEL_BUDGET = 12000

#: 起始多边形在种子切平面上的半径。够大就行：它只是「先给一块包住整个半球的平面」。
_BASE_RADIUS = 1e3
#: 起始多边形的边数。裁到最后一个单元也只会剩 5~8 条边，起手 24 边足够圆。
_BASE_SIDES = 24
#: 每个单元只跟最近的这么多种子求交（六边形邻居 + 余量）。
_NEIGHBOURS = 16
#: 顶点合并用的量化步长（单位球半径下的坐标）。
_EPS = 1e-6


def fibonacci_cap(count: int, cap_cos: float = 0.0) -> np.ndarray:
    """球冠（``z >= cap_cos``）上 ``count`` 个点的斐波那契排布，索引 0 在冠沿、末尾在顶点。

    ``cap_cos = 0`` 就是整个半球。返回 ``(count, 3)`` 的 float64 单位向量。
    """
    count = int(count)
    if count < 1:
        return np.zeros((0, 3), dtype=np.float64)

    index = np.arange(count, dtype=np.float64)
    # z 逐点均分 ⇒ 球面积均匀；+0.5 让点落在各自球带的中间，贴顶点 / 贴冠沿的点也不会重合
    z = cap_cos + (1.0 - cap_cos) * (index + 0.5) / count
    theta = index * GOLDEN_ANGLE
    r = np.sqrt(np.maximum(0.0, 1.0 - z * z))
    return np.stack([r * np.cos(theta), r * np.sin(theta), z], axis=-1)


def fibonacci_hemisphere(count: int) -> np.ndarray:
    """整个半球上的斐波那契排布（等价 ``fibonacci_cap(count, 0.0)``）。"""
    return fibonacci_cap(count, 0.0)


def _base_polygons(pts: np.ndarray) -> np.ndarray:
    """所有种子切平面上半径 ``_BASE_RADIUS`` 的一圈顶点，形状 ``(n, _BASE_SIDES, 3)``。

    基 ``(u, v)`` 满足 ``u × v = seed``（绕种子逆时针），于是多边形朝外是逆时针。

    **必须是一块「远大于单元」的平面多边形**，不能是球面上的一圈顶点：平面多边形的内部
    躺在它的平面里（离球心只有 ``cos θ``），球面上一整块区域根本不在里面。切平面的
    多边形没有这个问题 —— 半径够大时它包住整个半球，往下裁就得到真正的球面单元。
    """
    axis = np.where(
        np.abs(pts[:, 2:3]) > 0.999,
        np.array([[1.0, 0.0, 0.0]]),
        np.array([[0.0, 0.0, 1.0]]),
    )
    u = np.cross(axis, pts)
    u /= np.linalg.norm(u, axis=1, keepdims=True)
    v = np.cross(pts, u)
    angles = np.arange(_BASE_SIDES) * (2.0 * np.pi / _BASE_SIDES)
    ring = (
        np.cos(angles)[None, :, None] * u[:, None, :]
        + np.sin(angles)[None, :, None] * v[:, None, :]
    )
    return pts[:, None, :] + _BASE_RADIUS * ring


def _clip_all(poly: np.ndarray, counts: np.ndarray, normals: np.ndarray, offset=0.0):
    """一刀同时裁所有多边形：半空间 ``x·normal >= offset``，返回 ``(新顶点, 新顶点数)``。

    Sutherland–Hodgman 的批量写法：每条边 ``(a → b)`` 最多产出两个顶点（``a`` 在里面就留
    ``a``，跨越平面就再补一个交点），按边序交错进 2K 个槽位，再用 ``cumsum`` 压实、保持顺序。
    顶点留在原处不投影：裁剪平面都过原点时，顶点沿自己的射线挪多远都不改变内外关系，
    整段裁剪都在「切平面尺度」上做，投影回球面留到最后做一次。

    ``counts`` 是每行**有效**顶点数：行尾那些槽位是补位（重复最后一个顶点），一律当无效处理，
    环的收尾边按 ``(k+1) % counts`` 取 —— 这样补位既不会多出重复顶点，也不会漏掉收尾边。
    """
    rows, size = poly.shape[0], poly.shape[1]
    index = np.arange(size)[None, :]
    valid = index < counts[:, None]

    d = np.einsum("nkj,nj->nk", poly, normals) - offset
    inside = (d >= 0.0) & valid

    # 环上的下一条边：最后一个有效顶点接回第 0 个（补位行的索引随便给，反正被 valid 挡掉）
    ring = np.where(valid, (index + 1) % np.maximum(counts[:, None], 1), 0)
    ring3 = ring[:, :, None].repeat(3, axis=2)
    ahead = np.take_along_axis(poly, ring3, axis=1)
    d_ahead = np.take_along_axis(d, ring, axis=1)
    crossing = valid & (inside != (d_ahead >= 0.0))

    # 交点在 a→b 上的参数；只有跨越的那条边算得上，别处给 0（反正会被掩码丢掉）
    denom = np.where(crossing, d - d_ahead, 1.0)
    t = np.where(crossing, d / denom, 0.0)

    both = np.empty((rows, 2 * size, 3), dtype=poly.dtype)
    both[:, 0::2] = poly
    both[:, 1::2] = poly + t[:, :, None] * (ahead - poly)
    keep = np.empty((rows, 2 * size), dtype=bool)
    keep[:, 0::2] = inside
    keep[:, 1::2] = crossing

    slot = np.cumsum(keep, axis=1) - 1
    counts = slot[:, -1] + 1
    # 不保留的槽位写进末尾的哨兵列：压实后按顺序就是新的顶点环
    target = np.where(keep, slot, 2 * size)[:, :, None].repeat(3, axis=2)
    stacked = np.zeros((rows, 2 * size + 1, 3), dtype=poly.dtype)
    np.put_along_axis(stacked, target, both, axis=1)

    reach = np.maximum(counts, 1)
    new = stacked[:, : int(reach.max())]
    tail = new[np.arange(rows), reach - 1]
    pad = np.arange(new.shape[1])[None, :] >= counts[:, None]
    return np.where(pad[:, :, None], tail[:, None, :], new), counts


def spherical_voronoi(
    points: np.ndarray, cap_cos: float | None = None
) -> list[np.ndarray]:
    """每个点在单位球上的 Voronoi 单元（顶点逆时针朝外；退化的单元是空的）。

    ``cap_cos`` 给了就把单元裁到 ``z >= cap_cos`` 的球冠里（半球用 ``0.0``：赤道上因此
    是一条直边，正好当穹顶的口沿）。

    所有单元**一起**裁（每个候选平面一轮），所以点数上千也只是几十轮 numpy 运算：
    150 个点约 9 ms（逐单元循环的写法要 80 ms）。
    """
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) == 0:
        return []
    pts = pts / np.linalg.norm(pts, axis=1, keepdims=True)
    total = len(pts)
    if total < 2:
        return [np.zeros((0, 3))]

    neighbours = min(_NEIGHBOURS, total - 1)
    dots = pts @ pts.T
    np.fill_diagonal(dots, -2.0)  # 自己不是候选
    if neighbours < total - 1:
        order = np.argpartition(-dots, neighbours - 1, axis=1)[:, :neighbours]
    else:
        order = np.stack([np.delete(np.arange(total), index) for index in range(total)])
    # 「离我比离它近」= x·(p_i - p_j) >= 0（两点都在单位球上，两边平方抵消）
    normals = pts[:, None, :] - pts[order]

    poly = _base_polygons(pts)
    counts = np.full(total, poly.shape[1], dtype=np.int64)
    for step in range(neighbours):
        poly, counts = _clip_all(poly, counts, normals[:, step])
    if cap_cos is not None:
        plane = np.broadcast_to(np.array([0.0, 0.0, 1.0]), (total, 3))
        poly, counts = _clip_all(poly, counts, plane, float(cap_cos))

    cells = []
    for index in range(total):
        count = int(counts[index])
        if count < 3:
            cells.append(np.zeros((0, 3)))
            continue
        # 裁完才投影：这一步把切平面尺度的顶点收成球面上的单元顶点
        cell = poly[index, :count]
        cells.append(cell / np.linalg.norm(cell, axis=1, keepdims=True))
    return cells


def _retreat(poly: np.ndarray, seed: np.ndarray, angle: float):
    """每个顶点沿大圆朝种子退 ``angle`` 弧度；退没了（面板被缝吃掉）返回 ``None``。

    球面上「把单元边界往里推一个固定的弧长」就是这个写法：相邻两块各退 ``gap/2``，
    中间留出的缝宽处处等于 ``gap``（面板在球面上大小不一，缝却一样宽）。
    """
    if angle <= 0.0:
        return poly

    cos = np.clip(poly @ seed, -1.0, 1.0)
    alpha = np.arccos(cos)
    target = alpha - angle
    if target.min() <= 1e-9:
        return None
    # 球面线性插值：seed → v 走 t = target / alpha（插值点离 seed 正好 target 弧度）
    verts = (np.sin(angle) * seed + np.sin(target)[:, None] * poly) / np.sin(alpha)[
        :, None
    ]
    return verts / np.linalg.norm(verts, axis=1, keepdims=True)


@dataclass
class Panels:
    """一次生成的结果：顶点 / 面 / 逐面材质属性。

    属性都是**逐面**（``FACE``）的，长度 = ``len(faces)``（底板那张面也算一面：
    序号 ``-1``、代号就是它所在那一代）。几何节点按这些属性挑代、摆立柱。
    """

    verts: np.ndarray
    faces: list = field(default_factory=list)
    #: 每块面板的种子序号（赤道 → 顶点）。
    seed_index: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int32))
    #: 每块面板的种子位置（已经是最终世界尺度）。
    seed_pos: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    #: 每块面板属于第几代（0 = 最少的那一代）。
    gen: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int32))
    #: 被缝吃掉的面板数 / 提醒
    dropped: int = 0
    note: str = ""

    @property
    def face_count(self) -> int:
        return len(self.faces)

    @property
    def vert_count(self) -> int:
        return len(self.verts)

    @property
    def generations(self) -> int:
        """一共烘了几代（最大代号 + 1）。"""
        return int(self.gen.max()) + 1 if len(self.gen) else 0

    def gen_faces(self, index: int) -> int:
        """第 ``index`` 代有几块面。"""
        return int((self.gen == index).sum())


def _weld(polys, scale, eps: float):
    """多边形列表 → 去重顶点 + 面索引（量化到 ``eps`` 的格子上当键）。"""
    index: dict = {}
    verts: list = []
    faces: list = []
    for poly in polys:
        face = []
        for point in poly * scale:
            key = (
                round(float(point[0]) / eps),
                round(float(point[1]) / eps),
                round(float(point[2]) / eps),
            )
            slot = index.get(key)
            if slot is None:
                slot = len(verts)
                index[key] = slot
                verts.append(point)
            face.append(slot)
        faces.append(face)
    if not verts:
        return np.zeros((0, 3)), []
    return np.asarray(verts, dtype=np.float64), faces


def _stitch(polys, scale):
    """多边形列表 → 顶点 + 面索引，**不合并**顶点（面板之间有缝，本来就不相连）。"""
    sizes = [len(poly) for poly in polys]
    if not polys:
        return np.zeros((0, 3)), []
    verts = np.vstack(polys) * scale
    faces = []
    start = 0
    for size in sizes:
        faces.append(list(range(start, start + size)))
        start += size
    return verts, faces


def _bottom_ring(cells, eps: float = 1e-7) -> np.ndarray:
    """赤道（``z == 0``）上的顶点，按方位角排成一圈（底板那张面用它）。"""
    ring = [cell[np.abs(cell[:, 2]) < eps] for cell in cells]
    ring = [part for part in ring if len(part)]
    if not ring:
        return np.zeros((0, 3))
    points = np.vstack(ring)
    # 相邻单元共用端点：按量化键去重；再按方位角排序（逆时针）
    _, first = np.unique(
        np.round(points / _EPS).astype(np.int64), axis=0, return_index=True
    )
    points = points[np.sort(first)]
    angles = np.arctan2(points[:, 1], points[:, 0])
    return points[np.argsort(angles)]


def generation_count(count: int, length: int, animate: bool = True) -> int:
    """烘几代：一代一点、一帧一代（默认就是 +1 点/帧），并受面数预算限制。"""
    count = int(count)
    if not animate or count <= MIN_POINTS:
        return 1
    steps = min(max(2, int(length)), count - MIN_POINTS + 1)
    steps = max(2, min(steps, GENERATION_MAX))
    # 面板总数 ≈ 代数 × 点数 / 2：超预算就减代数（宁可跳代，也别把 Blender 卡死）
    budget = max(2, 2 * PANEL_BUDGET // max(count, 1))
    return max(2, min(steps, budget))


def _generation_points(count: int, progress: float) -> int:
    """第 ``progress``（0..1）代用多少个点：从 ``MIN_POINTS`` 线性长到 ``count``。"""
    points = round(MIN_POINTS + (count - MIN_POINTS) * float(np.clip(progress, 0, 1)))
    return max(MIN_POINTS, min(count, points))


def _one_generation(
    count: int,
    radius: float,
    height: float,
    gap: float,
    close_bottom: bool,
):
    """算一代（整座半球）：返回 ``(多边形列表, 种子序号, 种子位置, 丢掉的面板数)``。"""
    points = fibonacci_hemisphere(count)
    cells = spherical_voronoi(points, 0.0)
    # 缝宽是世界单位，退让量是球面上的弧长 ⇒ 换成弧度要除以半径
    retreat = 0.0 if gap <= 0.0 else (float(gap) * 0.5) / radius
    scale = np.array([radius, radius, radius * float(height)])

    polys: list = []
    seed_index: list = []
    seed_pos: list = []
    dropped = 0
    for index, cell in enumerate(cells):
        if len(cell) < 3:
            dropped += 1
            continue
        poly = _retreat(cell, points[index], retreat)
        if poly is None or len(poly) < 3:
            dropped += 1
            continue
        polys.append(poly)
        seed_index.append(index)
        seed_pos.append(points[index] * scale)

    if polys and close_bottom:
        ring = _bottom_ring(cells)
        if len(ring) >= 3:
            # 底板朝下（外法线 -Z），所以顶点按顺时针排
            polys.append(ring[::-1])
            # 底板不是「种子的面板」：序号给 -1，几何节点据此不给它放立柱
            seed_index.append(-1)
            seed_pos.append(np.zeros(3))

    return polys, seed_index, seed_pos, dropped


def build_generations(
    count: int,
    generations: int = 1,
    radius: float = 1.0,
    height: float = 1.0,
    gap: float = 0.0,
    close_bottom: bool = False,
    progress=None,
) -> Panels:
    """把 ``generations`` 代 Voronoi 划分拼成一份网格（逐面 ``gen`` 记代号）。

    每一代都是**整座半球**的一次完整重划分：点从 ``MIN_POINTS`` 线性长到 ``count``。
    ``progress`` 是可选回调 ``f(done, total)``，每算完一代调一次（算子用来刷进度条）。
    """
    count = int(count)
    if count < MIN_POINTS:
        raise ValueError(f"count must be >= {MIN_POINTS}, got {count}")
    if count > MAX_POINTS:
        raise ValueError(f"count must be <= {MAX_POINTS}, got {count}")
    if radius <= 0.0:
        raise ValueError(f"radius must be > 0, got {radius}")
    if height <= 0.0:
        raise ValueError(f"height must be > 0, got {height}")

    total = max(1, min(int(generations), GENERATION_MAX, count - MIN_POINTS + 1))
    total = max(1, total)
    scale = np.array([radius, radius, radius * float(height)])
    verts_blocks: list = []
    faces: list = []
    found_index: list = []
    found_pos: list = []
    gens: list = []
    dropped = 0
    offset = 0

    for step in range(total):
        # 只有一代时就是「最后那一代」：不能按 0 算，否则静态穹顶只剩 3 个点
        ratio = 1.0 if total == 1 else step / (total - 1)
        points = _generation_points(count, ratio)
        polys, seed_index, seed_pos, lost = _one_generation(
            points, float(radius), float(height), float(gap), close_bottom
        )
        dropped += lost
        if polys:
            if gap <= 0.0:
                # 没缝：相邻面板共用顶点；底板那一圈与面板底边是同一批坐标，一起合并
                block, faces_local = _weld(polys, scale, _EPS * float(radius))
            else:
                # 面板各退过一半缝宽，顶点本来就不相连，不必合并（快路径）
                block, faces_local = _stitch(polys, scale)

            verts_blocks.append(block)
            faces.extend([[slot + offset for slot in face] for face in faces_local])
            offset += len(block)
            found_index.append(np.asarray(seed_index, dtype=np.int32))
            found_pos.append(np.asarray(seed_pos, dtype=np.float64).reshape(-1, 3))
            gens.append(np.full(len(faces_local), step, dtype=np.int32))
        if progress is not None:
            progress(step + 1, total)

    if not faces:
        raise ValueError(f"no panel survived (count={count}, gap={gap})")

    return Panels(
        verts=np.ascontiguousarray(np.vstack(verts_blocks), dtype=np.float64),
        faces=faces,
        seed_index=np.concatenate(found_index),
        seed_pos=np.concatenate(found_pos),
        gen=np.concatenate(gens),
        dropped=dropped,
        note="" if not dropped else f"{dropped} panels vanished",
    )


def build_panels(
    count: int,
    radius: float = 1.0,
    height: float = 1.0,
    gap: float = 0.0,
    close_bottom: bool = False,
) -> Panels:
    """只有一代（静态穹顶）：``count`` 个点 → 一场球面 Voronoi → 缩放成穹顶。

    * ``radius`` —— 赤道半径（世界单位）；
    * ``height`` —— ``z`` 方向的缩放（1 = 半球，小于 1 是扁穹顶，大于 1 是尖穹顶）；
    * ``gap`` —— 面板之间的缝宽（世界单位）：每块面板的边界各往里退 ``gap/2``；
    * ``close_bottom`` —— 用赤道那一圈封一张底板（默认不封，穹顶是个开口的碗）。
    """
    return build_generations(
        count,
        generations=1,
        radius=radius,
        height=height,
        gap=gap,
        close_bottom=close_bottom,
    )
