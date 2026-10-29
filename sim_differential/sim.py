"""帧驱动的实时同步：``frame_change_pre`` 推进差分生长，把折线写进 GP 笔划。

流程（每帧）::

    读面板参数（物理 / 子步 / 后端）
      → 签名不一致就重建（求解器 + 定长笔划 + 属性）
      → 求解器推进到当前帧（回退时先回到初始圆环）
      → 拉伸比 / 曲率 → 逐点颜色
      → foreach_set 写回 position / radius / opacity / vertex_color / stretch / curvature

三条硬约定（与 ``sim_catenary`` / ``sim_hilbert`` / ``sim_dendrite`` 一致）：

* **几何 / 初始条件一变就重建**：初始节点数、半径、抖动、种子、节点上限、后端进签名，
  签名不一致时 handler 里自动重建；
* **物理参数实时生效**：斥力 / 平滑 / 边界 / 分裂距离 / 生长速率 / 锁 Z / 子步只是求解器
  实参，拖动滑块后下一帧就变；
* **向前推进、向后重置**：生长状态不是缓存，拖时间轴回退只能从初始圆环重算（每帧
  ``substeps`` 个子步 + 一次分裂）。

**笔划是定长的**：GP 的拓扑不能逐帧变，所以水位永远铺到「节点上限」，真正画出来的只有
前 ``n`` 个点 —— 剩下的点位置塌在末点上、半径与不透明度都是 0（见 :mod:`gp` 的文档）。
于是节点数每帧都在涨也不用重建图层，逐帧只有几次 ``foreach_set``。
"""

from __future__ import annotations

import time

import bpy
import numpy as np

from . import colour, gp
from . import scene as scene_mod
from . import solver as solver_mod

__all__ = [
    "STATE",
    "advance",
    "build",
    "frame_handler",
    "params",
    "purge_handlers",
    "redraw",
    "reset",
    "signature",
    "stats",
    "sync",
    "tick",
]

#: 模块级会话状态（handler / 算子 / 面板共用）。
STATE: dict = {
    "solver": None,
    "signature": None,
    "frame": 0,
    #: 节点缓冲容量（= 面板的节点上限，笔划拓扑也按它铺）
    "capacity": 0,
    #: 后端名与说明（"taichi/cuda"、"numpy"、回退原因……）
    "backend": "",
    "note": "",
    #: 面板读数：节点数、拉伸比、曲率峰值
    "nodes": 0,
    "stretch_mean": 0.0,
    "curvature_peak": 0.0,
    #: 每帧耗时（指数滑动平均）与上次构建耗时
    "ms": 0.0,
    "build_ms": 0.0,
    "error": "",
    "busy": False,
}


# --- 参数换算 ---------------------------------------------------------------


def signature(settings) -> tuple:
    """几何签名：变了就得重建求解器与笔划（判据放在参数组里，只有一份）。"""
    return settings.geometry_signature()


def params(settings) -> dict:
    """面板 → 求解器物理参数（键名与 :data:`solver.DEFAULTS` 一致）。"""
    return {key: float(getattr(settings, key)) for key in solver_mod.PARAM_KEYS}


def _layer_frame(scene) -> int:
    """GP 图层帧号：放在场景起始帧上（图层帧之间是保持，一帧 drawing 覆盖整段动画）。"""
    return int(getattr(scene, "frame_start", 1) or 1)


# --- 构建 / 重置 ------------------------------------------------------------


def build(settings, scene=None) -> dict:
    """按面板参数重建：求解器 → 初始圆环 → GP 对象 → 场景（世界 / 相机）。"""
    scene = scene or bpy.context.scene
    started = time.perf_counter()

    active = solver_mod.create(
        segments=int(settings.init_segments),
        radius=float(settings.init_radius),
        jitter=float(settings.jitter),
        seed=int(settings.seed),
        capacity=int(settings.max_nodes),
        backend=str(settings.backend),
        params=params(settings),
    )

    STATE.update(
        solver=active,
        signature=signature(settings),
        frame=0,
        capacity=int(active.capacity),
        backend=active.backend,
        note=active.note,
        error="",
        ms=0.0,
    )

    # 对象进当前激活的 collection（ensure_object 内部取 context.collection）
    obj = gp.find_object() or gp.ensure_object()
    _write(settings, obj, scene)

    if scene.world is None:
        # 场景本来没有世界：补一个项目画布底色，否则渲染出来是全黑
        scene_mod.apply_world(scene)
    scene_mod.ensure_camera(scene, scene_mod.span_of(float(settings.bound_radius)))

    settings.dirty = False
    STATE["build_ms"] = (time.perf_counter() - started) * 1000.0
    return {
        "backend": active.backend,
        "note": active.note,
        "nodes": active.node_count,
        "capacity": int(active.capacity),
        "ms": STATE["build_ms"],
    }


def reset(settings, scene=None) -> bool:
    """回到初始圆环（帧 0），不重建求解器与笔划拓扑。"""
    active = STATE.get("solver")
    if active is None:
        return False
    active.reset()
    STATE["frame"] = 0
    obj = gp.find_object()
    return _write(settings, obj, scene or bpy.context.scene)


# --- 推进 -------------------------------------------------------------------


def advance(settings, scene, target: int) -> None:
    """把求解器推进到 ``target`` 帧（回退则先回到初始圆环）。"""
    active = STATE.get("solver")
    if active is None:
        return

    target = int(target)
    if target < int(STATE["frame"]):
        active.reset()
        STATE["frame"] = 0

    frames = target - int(STATE["frame"])
    if frames > 0:
        active.set_params(**params(settings))
        substeps = max(1, int(settings.substeps))
        for _ in range(frames):
            active.step(substeps)
        STATE["frame"] = target


# --- 写入 GP ----------------------------------------------------------------


def _pad(values: np.ndarray, count: int) -> np.ndarray:
    """把 ``(n,)`` 数组铺到 ``count``，空位填 0（笔划是定长的）。"""
    out = np.zeros(count, np.float32)
    out[: len(values)] = values
    return out


def _stroke(settings) -> tuple:
    """当前状态 → 一个笔划组（定长缓冲：有效点 + 隐藏的空位）。"""
    active = STATE["solver"]
    capacity = int(STATE["capacity"])
    if active is None or capacity <= 0:
        raise RuntimeError("求解器还没建好")

    positions = np.asarray(active.positions(), np.float32)
    count = int(len(positions))
    stretch = np.asarray(active.stretch(), np.float32)
    curvature = np.asarray(active.curvature(), np.float32)

    colours = colour.colours(
        str(settings.colour_mode),
        stretch,
        curvature,
        tuple(settings.mono_colour),
    )

    # 位置：空位塌在末点上（零长度线段画不出东西），再靠半径 / 不透明度双保险
    points = np.repeat(positions[-1:], capacity, axis=0)
    points[:count] = positions

    rgba = np.zeros((capacity, 4), np.float32)
    rgba[:count] = colours

    radius = np.zeros(capacity, np.float32)
    radius[:count] = float(settings.line_width)
    opacity = np.zeros(capacity, np.float32)
    opacity[:count] = 1.0

    STATE["nodes"] = count
    STATE["stretch_mean"] = float(stretch.mean()) if stretch.size else 0.0
    STATE["curvature_peak"] = float(curvature.max()) if curvature.size else 0.0

    stroke = gp.StrokeSet(
        layer=gp.LAYER_GROWTH,
        # 一整条闭合折线就是一个笔划（容量固定，节点数涨了也不用改拓扑）
        counts=np.array([capacity], np.int32),
        points=points,
        radius=radius,
        colours=rgba,
        cyclic=np.array([True]),
        extras=(("stretch", _pad(stretch, capacity)), ("curvature", _pad(curvature, capacity))),
        opacity=opacity,
    )
    return (stroke,)


def _write(settings, obj, scene=None) -> bool:
    """把当前状态写进 GP：拓扑没变就只刷缓冲，变了就整层重写。"""
    if obj is None:
        return False
    strokes = _stroke(settings)
    if not gp.refresh(obj, strokes):
        gp.write(obj, strokes, frame=_layer_frame(scene or bpy.context.scene))
    return True


def sync(settings, obj=None, scene=None) -> bool:
    """把当前状态写进 GP（没对象 / 没求解器就什么都不做）。"""
    obj = obj or gp.find_object()
    if obj is None or STATE.get("solver") is None:
        return False
    return _write(settings, obj, scene)


def redraw(settings) -> bool:
    """显示参数（配色 / 线宽 / 单色）改了：不推进物理，只重刷当前帧。"""
    return sync(settings)


# --- Frame Change Handler ---------------------------------------------------


def tick(scene) -> None:
    """handler 的实现体：签名变了就重建，然后推进到当前帧并写笔划。"""
    settings = getattr(scene, "sim_differential", None)
    if settings is None or STATE["busy"]:
        return

    obj = gp.find_object()
    if obj is None:
        return
    if obj.mode != "OBJECT":
        # 编辑模式下点列由 GP 编辑器掌管，foreach_set 会被丢掉；退出编辑就恢复
        return

    STATE["busy"] = True
    started = time.perf_counter()
    try:
        if STATE.get("solver") is None or STATE["signature"] != signature(settings):
            build(settings, scene)
        previous = int(STATE["frame"])
        advance(settings, scene, int(scene.frame_current))
        sync(settings, obj, scene)
        # 一次 tick 可能补多帧（拖时间轴跳过一段），所以除以实际推进的帧数
        steps = max(1, int(STATE["frame"]) - previous)
        cost = (time.perf_counter() - started) * 1000.0 / steps
        STATE["ms"] = cost if not STATE["ms"] else 0.6 * STATE["ms"] + 0.4 * cost
        STATE["error"] = ""
    except Exception as exc:
        # 出错时 handler 每帧都会再进来一次，同一条错误只报一次，免得刷屏
        message = str(exc)
        if message != STATE["error"]:
            print("[sim_differential] handler error:", message)
        STATE["error"] = message
    finally:
        STATE["busy"] = False


def frame_handler(scene) -> None:
    tick(scene)


def purge_handlers() -> None:
    """按名字移除 handler，防止重载后堆叠（重载 = 禁用 → 清模块 → 启用）。"""
    bpy.app.handlers.frame_change_pre[:] = [
        handler
        for handler in bpy.app.handlers.frame_change_pre
        if getattr(handler, "__name__", "") != frame_handler.__name__
    ]


def stats() -> dict:
    """面板读数（只读快照）。"""
    return {
        "frame": int(STATE.get("frame", 0)),
        "nodes": int(STATE.get("nodes", 0)),
        "capacity": int(STATE.get("capacity", 0)),
        "stretch_mean": float(STATE.get("stretch_mean", 0.0)),
        "curvature_peak": float(STATE.get("curvature_peak", 0.0)),
        "backend": str(STATE.get("backend", "")),
        "note": str(STATE.get("note", "")),
        "ms": float(STATE.get("ms", 0.0)),
        "build_ms": float(STATE.get("build_ms", 0.0)),
        "error": str(STATE.get("error", "")),
    }
