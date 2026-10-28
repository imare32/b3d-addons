"""帧驱动的实时同步：``frame_change_pre`` 推进零点运动，把 theta 除子写进环面网格。

流程（每帧）::

    读面板参数（物理 / 子步 / 后端）
      → 签名不一致就重建（求解器 + 网格拓扑 + 属性 + 零点小球）
      → 求解器推进到当前帧（回退时先回到初始布局）
      → 四张标量场 → 顶点色
      → foreach_set 写回环面顶点 / 属性 / 零点位置

三条硬约定（与 ``sim_dendrite`` / ``sim_catenary`` 一致）：

* **几何一变就重建**：分辨率、后端进签名，签名不一致时 handler 里自动重建；
* **物理参数实时生效**：零点个数、τ、凹陷深度、涟漪幅度、速度、子步只是求解器实参，
  拖动滑块后下一帧就变（零点个数变了会重铺初值，与参考实现一致）；
* **向前推进、向后重置**：零点运动是累积的，拖时间轴回退只能从初始布局重算。
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
    "apply_params",
    "build",
    "frame_handler",
    "params",
    "purge_handlers",
    "redraw",
    "reset_particles",
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
    #: 后端名与说明（"taichi/cuda"、"numpy"、回退原因……）
    "backend": "",
    "note": "",
    #: 面板读数：当前零点个数、每帧耗时（指数滑动平均）、上次构建耗时
    "zeros": 0,
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
    """面板 → 求解器参数（键名与 :data:`solver.DEFAULTS` 一致）。"""
    return {key: float(getattr(settings, key)) for key in solver_mod.PARAM_KEYS}


def apply_params(settings) -> None:
    """把面板参数灌进求解器（没有求解器就什么都不做）。"""
    solver = STATE.get("solver")
    if solver is not None:
        solver.set_params(**params(settings))


# --- 构建 / 重置 ------------------------------------------------------------


def build(settings, scene=None) -> dict:
    """按面板参数重建：求解器 → 网格拓扑 → 属性 / 材质 → 零点小球 → 写第一帧。"""
    scene = scene or bpy.context.scene
    started = time.perf_counter()
    nu, nv = int(settings.nu), int(settings.nv)
    major_r, minor_r = float(settings.major_r), float(settings.minor_r)

    solver = solver_mod.create(
        nu, nv, major_r, minor_r, backend=str(settings.backend), params=params(settings)
    )

    # 对象进当前激活的 collection（ensure_object 内部取 context.collection）
    obj = mesh.find_object() or mesh.ensure_object()
    mesh.write_topology(obj, nu, nv, major_r, minor_r)
    mesh.ensure_attributes(obj.data)
    mesh.ensure_material(obj)

    zeros_obj = mesh.find_zero_object() or mesh.ensure_zero_object()
    mesh.ensure_zero_material(zeros_obj)

    STATE.update(
        solver=solver,
        signature=signature(settings),
        frame=0,
        backend=solver.backend,
        note=solver.note,
        error="",
        ms=0.0,
    )
    _write(settings, obj, zeros_obj)

    if scene.world is None:
        # 场景本来没有世界：补一个项目画布底色，否则渲染出来是全黑
        scene_mod.apply_world(scene)
    scene_mod.ensure_camera(scene, scene_mod.outer_radius(major_r, minor_r))

    settings.dirty = False
    STATE["build_ms"] = (time.perf_counter() - started) * 1000.0
    return {
        "backend": solver.backend,
        "note": solver.note,
        "vertices": nu * nv,
        "zeros": int(STATE["zeros"]),
        "ms": STATE["build_ms"],
    }


def reset_particles(settings) -> None:
    """只把零点放回初始布局（不动网格、不重建求解器）。"""
    solver = STATE.get("solver")
    if solver is None:
        return
    solver.initialize(int(settings.num_zeros))


# --- 推进 -------------------------------------------------------------------


def advance(settings, scene, target: int) -> None:
    """把零点运动推进到 ``target`` 帧（回退则先回到初始布局）。"""
    solver = STATE.get("solver")
    if solver is None:
        return

    target = int(target)
    if target < int(STATE["frame"]):
        reset_particles(settings)
        STATE["frame"] = 0

    steps = target - int(STATE["frame"])
    if steps > 0:
        solver.step(steps * max(1, int(settings.substeps)))
        STATE["frame"] = target


# --- 写入网格 ---------------------------------------------------------------


def _write(settings, obj, zeros_obj) -> None:
    """求解器算一帧 → 环面顶点 / 四张属性 / 顶点色 / 零点小球。"""
    solver = STATE["solver"]
    verts, amplitude, phase, phase_grid, potential, zeros = solver.evaluate()

    colours = colour.colours(
        str(settings.colour_mode), amplitude, phase, phase_grid, potential
    )
    mesh.write_state(obj, verts, amplitude, phase, phase_grid, potential, colours)
    mesh.write_zeros(zeros_obj, zeros, float(settings.sphere_radius))
    STATE["zeros"] = int(zeros.shape[0])


def sync(settings, obj=None, zeros_obj=None) -> bool:
    """把当前帧写进网格（没对象 / 没求解器就什么都不做）。"""
    obj = obj or mesh.find_object()
    zeros_obj = zeros_obj or mesh.find_zero_object()
    if obj is None or zeros_obj is None or STATE.get("solver") is None:
        return False
    _write(settings, obj, zeros_obj)
    return True


def redraw(settings) -> bool:
    """显示参数（配色）改了：不推进物理，只重刷当前帧。"""
    apply_params(settings)
    return sync(settings)


# --- Frame Change Handler ---------------------------------------------------


def tick(scene) -> None:
    """handler 的实现体：签名变了就重建，然后推进到当前帧并写网格。"""
    settings = getattr(scene, "sim_jacobi", None)
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
        apply_params(settings)
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
            print("[sim_jacobi] handler error:", message)
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
        "zeros": int(STATE.get("zeros", 0)),
        "backend": str(STATE.get("backend", "")),
        "note": str(STATE.get("note", "")),
        "ms": float(STATE.get("ms", 0.0)),
        "build_ms": float(STATE.get("build_ms", 0.0)),
        "error": str(STATE.get("error", "")),
    }
