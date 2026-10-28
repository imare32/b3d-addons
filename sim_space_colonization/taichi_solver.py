"""Taichi 后端的最近邻搜索（GPU 并行暴力搜索）。

太空殖民每子步的大头是「每个吸收点找最近的树枝节点」—— 一个典型的 ``M × N`` 距离矩阵
``argmin``。这里就是参考实现 ``common/space_colonization.py`` 里的那个 kernel：
``for j in range(num_attractors)`` 外层并行、内层扫所有节点求最小值，GPU 上 3000 个吸收点
× 2 万节点仍在毫秒级。

只做搜索，不做生长：节点树的增删与半径计算留在 :mod:`sim_space_colonization.solver`
（纯 NumPy，能脱离 Blender 单独测）。取不到 GPU 时退 CPU（``ti.cpu``），再失败就由
:func:`sim_space_colonization.solver.create` 回退 NumPy / KD 树。

``ti.init`` 一个进程只做一次：Blender 热重载模块时复用已有 runtime，不会重复初始化报错。
"""

# 注意：**不能**加 ``from __future__ import annotations`` —— Taichi 直接读
# ``__annotations__`` 判断 kernel 参数类型，PEP 563 的字符串注解会报
# ``TaichiSyntaxError: Invalid type annotation``。

import glob
import os
import sys

import numpy as np


def _local_module_roots() -> list[str]:
    """第三方库的兜底查找路径（只在 ``import taichi`` 失败时才用）。

    正常启动（带 ``BLENDER_USER_RESOURCES``）时 Blender 会把
    ``$BLENDER_USER_RESOURCES/scripts/modules`` 加进 ``sys.path``；``--factory-startup``
    不读用户偏好，那一份路径会丢 —— 那时「明明装了 taichi 却回退」就出在这里。
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

__all__ = ["TaichiSearch", "find_closest_nodes_taichi", "ensure_init"]

#: 是否已完成 ``ti.init``（热重载会清掉模块级变量，但 runtime 还在、会重新探测）。
_READY = False
#: 实际架构名（cuda / vulkan / cpu …），只用于面板读数。
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
            ti.init(arch=ti.cpu, offline_cache=True)
    try:
        _ARCH = str(ti.lang.impl.current_cfg().arch).split(".")[-1].lower()
    except Exception:
        _ARCH = "?"
    _READY = True
    return _ARCH


@ti.kernel
def find_closest_nodes_taichi(
    nodes_pos: ti.types.ndarray(dtype=ti.f32, ndim=2),
    attractors: ti.types.ndarray(dtype=ti.f32, ndim=2),
    closest_node: ti.types.ndarray(dtype=ti.i32, ndim=1),
    closest_dist: ti.types.ndarray(dtype=ti.f32, ndim=1),
):
    """每个吸收点在并行循环里扫全部节点，写出最近节点索引与距离。"""
    num_nodes = nodes_pos.shape[0]
    num_attractors = attractors.shape[0]

    for j in range(num_attractors):
        ax = attractors[j, 0]
        ay = attractors[j, 1]
        az = attractors[j, 2]

        min_dist_sq = 1e20
        min_idx = -1

        for i in range(num_nodes):
            dx = nodes_pos[i, 0] - ax
            dy = nodes_pos[i, 1] - ay
            dz = nodes_pos[i, 2] - az
            d_sq = dx * dx + dy * dy + dz * dz
            if d_sq < min_dist_sq:
                min_dist_sq = d_sq
                min_idx = i

        closest_node[j] = min_idx
        closest_dist[j] = ti.math.sqrt(min_dist_sq)


class TaichiSearch:
    """``solver.create`` 认的搜索器：``nearest(nodes, attractors) -> (idx, dist)``。"""

    backend = "taichi"

    def __init__(self):
        arch = ensure_init()
        self.note = f"taichi {arch} 并行搜索"

    def nearest(self, nodes: np.ndarray, attractors: np.ndarray):
        count = len(attractors)
        closest = np.full(count, -1, np.int32)
        distance = np.full(count, np.inf, np.float32)
        if count == 0 or len(nodes) == 0:
            return closest, distance

        nodes = np.ascontiguousarray(nodes, np.float32)
        cloud = np.ascontiguousarray(attractors, np.float32)
        find_closest_nodes_taichi(nodes, cloud, closest, distance)
        return closest, distance
