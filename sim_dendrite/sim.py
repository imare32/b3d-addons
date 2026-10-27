"""帧驱动的实时同步：``frame_change_pre`` 推进相场，把场写进高度场网格。

流程（每帧）::

    读面板参数（物理 / 子步 / 后端）
      → 签名不一致就重建（求解器 + 网格拓扑 + 属性）
      → 求解器推进到当前帧（回退时先回到初始核）
      → 相场 / 过冷度 → 顶点色
      → foreach_set 写回顶点的 z 与四个属性

三条硬约定（与 ``sim_catenary`` 一致）：

* **几何 / 初始条件一变就重建**：分辨率、格距、后端、种子半径、初始过冷度、参考种子
  对象进签名，签名不一致时 handler 里自动重建；
* **物理参数实时生效**：``N_sym`` / ``c_ani`` / ``alpha`` / ``theta_off`` / ``tau`` /
  ``kappa`` / ``DT_heat`` / ``dt`` / 子步只是求解器实参，拖动滑块后下一帧就变；
* **向前推进、向后重置**：场不是缓存，拖时间轴回退只能从初始核重算（与参考实现一致）。

Taichi GPU 上 250² 网格 × 25 子步是实时档；没有 taichi 时会自动回退 NumPy（同方程，
慢一些），回退原因写在 ``STATE["note"]`` 里给面板显示。
"""

from __future__ import annotations

import time

import bpy

from . import colour, mesh
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
    "reset_fields",
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
    #: 网格拓扑签名（分辨率 / 格距），与求解器一起重建
    "topology": None,
    #: 后端名与说明（"taichi/cuda"、"numpy"、回退原因……）
    "backend": "",
    "note": "",
    #: 面板读数：固相占比、过冷度范围
    "solid": 0.0,
    "dT_range": (0.0, 0.0),
    #: 每帧耗时（指数滑动平均）与上次构建耗时
    "ms": 0.0,
    "build_ms": 0.0,
    "error": "",
    "busy": False,
}


# --- 参数换算 ---------------------------------------------------------------


def signature(settings) -> tuple:
    """几何签名：变了就得重建求解器与网格（判据放在参数组里，只有一份）。"""
    return settings.geometry_signature()


def params(settings) -> dict:
    """面板 → 求解器物理参数（键名与 :data:`solver.DEFAULTS` 一致）。"""
    return {key: float(getattr(settings, key)) for key in solver_mod.PARAM_KEYS}


def _seed_points(settings):
    """参考网格的种子点（没选对象就是 ``None``）。"""
    return (
        mesh.seed_points_from_object(settings.seed_ref) if settings.seed_ref else None
    )


# --- 构建 / 重置 ------------------------------------------------------------


def build(settings, scene=None) -> dict:
    """按面板参数重建：求解器 → 初始场 → 网格拓扑 → 属性 / 材质 → 写第一帧。"""
    scene = scene or bpy.context.scene
    started = time.perf_counter()
    nx, ny = int(settings.nx), int(settings.ny)
    dx, dy = float(settings.dx), float(settings.dy)

    solver = solver_mod.create(
        nx, ny, dx, dy, backend=str(settings.backend), params=params(settings)
    )
    seeds = _seed_points(settings)
    solver.initialize(
        seed_radius_units=float(settings.seed_radius),
        seed_points=seeds,
        initial_dT=float(settings.initial_dT),
    )

    obj = mesh.find_object() or mesh.ensure_object(scene.collection)
    mesh.write_topology(obj, nx, ny, dx, dy)
    mesh.ensure_attributes(obj.data)
    mesh.ensure_material(obj)

    STATE.update(
        solver=solver,
        signature=signature(settings),
        frame=0,
        topology=(nx, ny, dx, dy),
        backend=solver.backend,
        note=solver.note,
        error="",
        ms=0.0,
    )
    _write(settings, obj)

    if scene.world is None:
        # 场景本来没有世界：补一个项目画布底色，否则渲染出来是全黑
        scene_mod.apply_world(scene)
    scene_mod.ensure_camera(
        scene,
        scene_mod.span_of(nx, ny, dx, dy),
        float(settings.z_scale) * 0.4,
    )

    settings.dirty = False
    STATE["build_ms"] = (time.perf_counter() - started) * 1000.0
    return {
        "backend": solver.backend,
        "note": solver.note,
        "vertices": nx * ny,
        "seeds": 0 if seeds is None else len(seeds),
        "ms": STATE["build_ms"],
    }


def reset_fields(settings) -> None:
    """只重新铺初始场（不动网格、不重建求解器）。"""
    solver = STATE.get("solver")
    if solver is None:
        return
    solver.initialize(
        seed_radius_units=float(settings.seed_radius),
        seed_points=_seed_points(settings),
        initial_dT=float(settings.initial_dT),
    )


# --- 推进 -------------------------------------------------------------------


def advance(settings, scene, target: int) -> None:
    """把求解器推进到 ``target`` 帧（回退则先回到初始核）。"""
    solver = STATE.get("solver")
    if solver is None:
        return

    target = int(target)
    if target < int(STATE["frame"]):
        reset_fields(settings)
        STATE["frame"] = 0

    steps = target - int(STATE["frame"])
    if steps > 0:
        solver.set_params(**params(settings))
        solver.step(steps * max(1, int(settings.substeps)))
        STATE["frame"] = target


# --- 写入网格 ---------------------------------------------------------------


def _write(settings, obj) -> None:
    """相场 / 过冷度 → 顶点高度 + 属性 + 顶点色，并刷新面板读数。"""
    solver = STATE["solver"]
    nx, ny, dx, dy = STATE["topology"]
    phi = solver.phi_flat()
    dT = solver.dT_flat()
    colours = colour.colours(
        str(settings.colour_mode),
        phi,
        dT,
        float(settings.initial_dT),
        0.0,
    )
    mesh.write_state(
        obj,
        phi,
        dT,
        colours,
        nx=nx,
        ny=ny,
        dx=dx,
        dy=dy,
        z_scale=float(settings.z_scale),
    )
    STATE["solid"] = float(phi.mean())
    STATE["dT_range"] = (float(dT.min()), float(dT.max()))


def sync(settings, obj=None) -> bool:
    """把当前场写进网格（没对象 / 没求解器就什么都不做）。"""
    obj = obj or mesh.find_object()
    if obj is None or STATE.get("solver") is None:
        return False
    _write(settings, obj)
    return True


def redraw(settings) -> bool:
    """显示参数（高度缩放 / 配色）改了：不推进物理，只重刷当前帧。"""
    return sync(settings)


# --- Frame Change Handler ---------------------------------------------------


def tick(scene) -> None:
    """handler 的实现体：签名变了就重建，然后推进到当前帧并写网格。"""
    settings = getattr(scene, "sim_dendrite", None)
    if settings is None or STATE["busy"]:
        return

    obj = mesh.find_object()
    if obj is None:
        return
    if obj.mode != "OBJECT":
        # 编辑模式下顶点由 BMesh 掌控，foreach_set 会被丢掉；退出编辑就恢复正常
        return

    STATE["busy"] = True
    started = time.perf_counter()
    try:
        if STATE.get("solver") is None or STATE["signature"] != signature(settings):
            build(settings, scene)
        previous = int(STATE["frame"])
        advance(settings, scene, int(scene.frame_current))
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
            print("[sim_dendrite] handler error:", message)
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
    low, high = STATE.get("dT_range", (0.0, 0.0))
    return {
        "frame": int(STATE.get("frame", 0)),
        "solid": float(STATE.get("solid", 0.0)),
        "dT_low": float(low),
        "dT_high": float(high),
        "backend": str(STATE.get("backend", "")),
        "note": str(STATE.get("note", "")),
        "ms": float(STATE.get("ms", 0.0)),
        "build_ms": float(STATE.get("build_ms", 0.0)),
        "error": str(STATE.get("error", "")),
    }
