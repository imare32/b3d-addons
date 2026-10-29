"""帧驱动的实时生长：``frame_change_pre`` 推进太空殖民，把节点树画成 Grease Pencil 笔划。

流程（每帧）::

    读面板参数（算法 / 子步 / 模式）
      → 签名不一致就重建（表面 + 吸收点 + 求解器）
      → 求解器推进到当前帧（回退时整棵重算）
      → 叶子数 → 半径（再平滑 / 收尖），深度 → 配色
      → 树拆成链 → 每链一条 GP 笔划（逐点半径 + 逐点颜色）

三条硬约定（与 ``sim_dendrite`` / ``sim_catenary`` 一致）：

* **输入 / 取样一变就重建**：目标网格、根顶点组、模式、后端、吸收点数、种子进签名；
* **算法参数实时生效**：吸引半径 / 消耗半径 / 步长 / 持久度 / 抖动 / 子步只是求解器实参，
  拖动滑块后下一帧就变（历史节点不回溯，从当前位置继续长）；
* **向前推进、向后重算**：节点树不是缓存，拖时间轴回退只能从头再长一遍（与参考实现一致）。

时间轴语义是「帧 = 生长批」，不是物理时间：``substeps`` 决定一帧长多少。
"""

from __future__ import annotations

import time

import bpy
import numpy as np

from . import colour, gp
from . import solver as solver_mod
from . import surface as surface_mod

__all__ = [
    "STATE",
    "advance",
    "build",
    "frame_handler",
    "params",
    "purge_handlers",
    "redraw",
    "signature",
    "stats",
    "sync",
    "tick",
]

#: 模块级会话状态（handler / 算子 / 面板共用）。
STATE: dict = {
    "solver": None,
    "surface": None,
    "signature": None,
    #: 包围盒对角线（世界单位）：面板参数乘它就是世界单位
    "unit": 1.0,
    "frame": 0,
    #: 后端名与说明（"kdtree"、"taichi/cuda"、回退原因……）
    "backend": "",
    "note": "",
    #: 面板读数
    "nodes": 0,
    "attractors": 0,
    #: 上次写进 GP 的「几何 / 外观指纹」：
    #: 节点数 + 粗细 / 梯度 / 倍数 / 配色。一样就跳过整帧重写。
    "render_key": None,
    #: 每帧耗时（指数滑动平均）与上次构建耗时
    "ms": 0.0,
    "build_ms": 0.0,
    "error": "",
    "busy": False,
}


# --- 参数换算 ---------------------------------------------------------------


def signature(settings) -> tuple:
    """重建签名（判据放在参数组里，只有一份）。"""
    return settings.geometry_signature()


def params(settings, unit: float) -> dict:
    """面板 → 求解器实参：长度类参数乘各自的世界单位。"""
    unit = float(unit)
    return {
        "influence_radius": float(settings.influence_radius) * unit,
        "kill_radius": float(settings.kill_radius) * unit,
        "growth_step": float(settings.growth_step) * unit,
        "dir_persistence": float(settings.dir_persistence),
        "jitter": float(settings.jitter),
        "thickness": float(settings.thickness) * unit,
        "taper": float(settings.taper),
    }


# --- 构建 / 重置 ------------------------------------------------------------


def build(settings, scene=None) -> dict:
    """按面板参数重建：表面 → 吸收点 → 根 → 求解器 → 结果对象 → 写第一帧。"""
    scene = scene or bpy.context.scene
    started = time.perf_counter()

    target = settings.target
    if target is None:
        # 没选目标：给一只缺省输入网格（细分过的猴子），装好插件就能直接「构建生长」
        target = surface_mod.default_target()
        settings.target = target
    if gp.is_result(target):
        raise ValueError("目标网格不能是插件生成的结果对象：换一个输入网格")
    surface = surface_mod.load(target)
    if surface is None:
        raise ValueError("目标网格没有可用几何（选一个网格对象）")
    if not surface.usable:
        raise ValueError("目标网格没有面：至少需要一个可三角化的多边形")

    attractors = surface.sample(int(settings.max_attractors), int(settings.seed))
    roots = surface_mod.roots(target, str(settings.root_group), surface)
    if len(roots) == 0:
        raise ValueError("找不到根点：给目标网格建一个根顶点组，或者让它有顶点")

    unit = surface.unit
    search = solver_mod.create(str(settings.backend))
    solver = solver_mod.SpaceColonization(params(settings, unit), search)
    solver.initialize(roots, attractors, seed=int(settings.seed))
    if not solver.initialized:
        raise ValueError("初始化失败：吸收点为空（目标网格可能退化）")

    obj = gp.ensure_object()

    STATE.update(
        solver=solver,
        surface=surface,
        signature=signature(settings),
        unit=unit,
        frame=0,
        backend=solver.backend,
        note=solver.note,
        error="",
        ms=0.0,
        render_key=None,  # 重建 = 一棵新树：强制重写一帧
    )
    _write(settings, obj)
    settings.dirty = False

    STATE["build_ms"] = (time.perf_counter() - started) * 1000.0
    return {
        "backend": solver.backend,
        "note": solver.note,
        "roots": len(roots),
        "attractors": len(attractors),
        "unit": unit,
        "ms": STATE["build_ms"],
    }


def reset(settings, scene=None) -> bool:
    """整棵重来（重新撒吸收点、重建求解器），不动结果对象。"""
    build(settings, scene)
    return True


# --- 推进 -------------------------------------------------------------------


def advance(settings, target_frame: int) -> bool:
    """把求解器推进到 ``target_frame`` 帧；回退就整棵重算。"""
    solver = STATE.get("solver")
    if solver is None:
        return False

    target = int(target_frame)
    rebuilt = False
    if target < int(STATE["frame"]):
        # 节点树只能向前长：回退等价于重新初始化，再从头长到目标帧
        # （这里不能 return：重建后 STATE["frame"] 归零，下面会把它补到 target）
        build(settings)
        rebuilt = True

    # 重建换掉的是 **STATE 里的求解器对象**，上面那个局部变量已经失效了 —— 必须重取，
    # 否则会继续推进旧树、而网格写的是新树（画面停在根点）
    solver = STATE.get("solver")
    if solver is None:
        return rebuilt

    steps = target - int(STATE["frame"])
    if steps <= 0:
        return rebuilt

    unit = float(STATE["unit"])
    solver.set_params(**params(settings, unit))
    surface = STATE.get("surface") if str(settings.mode) == "SURFACE" else None
    offset = float(settings.surface_offset) * unit

    for _ in range(steps):
        if not solver.step(
            substeps=int(settings.substeps),
            max_nodes=int(settings.max_nodes),
            surface=surface,
            surface_offset=offset,
        ):
            break  # 吸收点吃完 / 节点到顶 / 卡住：这一帧不再空转
    STATE["frame"] = target
    return True


# --- 写入笔划 ---------------------------------------------------------------


def _write(settings, obj) -> None:
    """节点树 → GP 笔划：拆链、算半径与配色，并刷新面板读数。

    写进 GP 的是「捋顺后」的几何（:func:`solver.display_geometry`）：原始折线一格一格
    带锯齿、分叉处半径还会突然跳一截。平滑只发生在显示副本上，求解器状态一个字节不动。

    **没变就跳过**：整条链路（半径 / 平滑 / 收尖 / 拆链 / 写笔划）是 O(节点数) 的，一万
    节点时一次约 10 ms；而生长停了以后画面每帧其实完全一样。指纹 = 节点数 + 粗细 / 梯度 /
    倍数 / 配色，一致就直接 return —— 停滞期从每帧十几毫秒降到接近零，比较下去就能跑满 60。
    """
    solver = STATE["solver"]
    key = (
        len(solver.nodes_pos),
        float(settings.thickness),
        float(settings.taper),
        float(settings.radius_scale),
        str(settings.colour_mode),
    )
    if key == STATE["render_key"]:
        return

    unit = float(STATE["unit"])
    radius = solver.radii(
        thickness=float(settings.thickness) * unit, taper=float(settings.taper)
    )
    radius = radius * float(settings.radius_scale)

    positions, radius = solver_mod.display_geometry(solver, radius)
    counts, chain = solver_mod.branch_chains(solver.nodes_parent)
    if len(counts) == 0:
        return

    order = chain.astype(np.int64)
    depth = solver.depth_ratio()[order]
    per_point_radius = radius[order]
    info = gp.write(
        obj,
        counts,
        positions[order],
        per_point_radius,
        colour.colours(str(settings.colour_mode), depth, per_point_radius),
    )
    STATE["render_key"] = key
    STATE["nodes"] = len(solver.nodes_pos)
    STATE["strokes"] = int(info["strokes"])
    STATE["attractors"] = len(solver.attractors)


def sync(settings, obj=None) -> bool:
    """把当前树写进 GP 对象（没对象 / 没求解器就什么都不做）。"""
    obj = obj or gp.find_object()
    if obj is None or STATE.get("solver") is None:
        return False
    _write(settings, obj)
    return True


def redraw(settings) -> bool:
    """显示参数（配色 / 粗细）改了：不推进生长，只重画当前帧。"""
    return sync(settings, gp.find_object())


# --- Frame Change Handler ---------------------------------------------------


def tick(scene) -> None:
    """handler 的实现体：签名变了就重建，然后推进到当前帧并重画笔划。"""
    settings = getattr(scene, "sim_space_colonization", None)
    if settings is None or STATE["busy"]:
        return

    obj = gp.find_object()
    if obj is None:
        return
    if obj.mode != "OBJECT":
        # 在 GP 编辑模式里笔划归编辑器管，重建会把用户正在画的东西冲掉
        return

    STATE["busy"] = True
    started = time.perf_counter()
    try:
        if STATE.get("solver") is None or STATE["signature"] != signature(settings):
            build(settings, scene)
        previous = int(STATE["frame"])
        if advance(settings, int(scene.frame_current)):
            sync(settings, obj)
        # 一次 tick 可能补多帧（拖时间轴跳过一段），所以除以实际推进的帧数
        steps = max(1, int(STATE["frame"]) - previous)
        cost = (time.perf_counter() - started) * 1000.0 / steps
        STATE["ms"] = cost if not STATE["ms"] else 0.6 * STATE["ms"] + 0.4 * cost
        STATE["error"] = ""
    except Exception as exc:
        # 出错时 handler 每帧都会再进来一次，同一条错误只报一次，免得刷屏
        message = str(exc)
        if message != STATE["error"]:
            print("[sim_space_colonization] handler error:", message)
        STATE["error"] = message
    finally:
        STATE["busy"] = False


def frame_handler(scene) -> None:
    tick(scene)


def purge_handlers() -> None:
    """只移除**本包注册过**的 handler，防止重载后堆叠（重载 = 禁用 → 清模块 → 启用）。

    判据是模块命名空间（``__package__`` 前缀），不是函数名：``bpy.app.handlers`` 是一串平铺
    的函数对象，没有"按插件分区"的机制，而仓库里各 sim 插件的 handler 全叫 ``frame_handler``
    —— 按名字清理会把别人家的也摘掉（两个 sim 插件同时启用时，后启用的会把先启用的停掉）。
    模块名稳定（热重载后不变，函数对象换新），所以拿它判断归属。
    """
    package = __package__ or __name__

    def ours(handler) -> bool:
        module = getattr(handler, "__module__", "")
        return module == package or module.startswith(package + ".")

    bpy.app.handlers.frame_change_pre[:] = [
        handler for handler in bpy.app.handlers.frame_change_pre if not ours(handler)
    ]


def stats() -> dict:
    """面板读数（只读快照）。"""
    return {
        "frame": int(STATE.get("frame", 0)),
        "nodes": int(STATE.get("nodes", 0)),
        "strokes": int(STATE.get("strokes", 0)),
        "attractors": int(STATE.get("attractors", 0)),
        "backend": str(STATE.get("backend", "")),
        "note": str(STATE.get("note", "")),
        "ms": float(STATE.get("ms", 0.0)),
        "build_ms": float(STATE.get("build_ms", 0.0)),
        "error": str(STATE.get("error", "")),
    }
