"""帧驱动的实时演化：``frame_change_pre`` 推进 boids，把每只鸟画成三角箭头。

流程（每帧）::

    读面板参数（鸟数 / 物理 / 边界 / 配色 / 后端）
      → 后端签名不一致就重建（求解器 + 初始撒点）
      → 种子改过就重新撒一遍
      → 鸟数变了就往缓冲尾巴上补 / 砍（不重建，已经在飞的鸟不被打断）
      → 推进到当前帧（每帧 substeps 个子步）
      → 位置 + 速度 → 三角形几何 → 逐鸟颜色，一次写进网格

三条硬约定（与 ``sim_game_of_life`` / ``sim_differential`` / ``sim_hilbert`` 一致）：

* **后端变了就重建**：签名只有后端一项 —— 鸟数、物理、边界都是实时的；
* **物理 / 显示实时生效**：改完下一帧就变（配色与箭头长度连下一帧都不用等，见
  :func:`redraw`）；
* **向前推进、向后重置**：位置没有历史缓存，拖时间轴回退只能从第 0 帧重算 —— 于是同一条
  时间轴每次播出来的群体运动完全一样。

开销：一步是 O(n²) 的邻居扫描。NumPy 后端 300 只约 1.6 ms/子步（缺省 2 子步 ≈ 3 ms/帧），
1000 只约 18 ms/子步；上千只请用 Taichi 后端（同一个 kernel 在 GPU 上 5000 只约 1 ms）。
"""

from __future__ import annotations

import time

import bpy
import numpy as np

from . import colour, mesh
from . import scene as scene_mod
from . import solver as solver_mod

__all__ = [
    "STATE",
    "advance",
    "build",
    "flocking_frame_handler",
    "params",
    "purge_handlers",
    "redraw",
    "refresh",
    "reset",
    "signature",
    "spawn",
    "stats",
    "sync",
    "tick",
]

#: 模块级会话状态（handler / 算子 / 面板共用）。
STATE: dict = {
    "solver": None,
    #: 后端签名，与求解器一起重建
    "signature": None,
    #: 已经推进到第几帧（回退到这里之前会重置）
    "frame": 0,
    #: 缓冲容量（面板读数用）
    "capacity": 0,
    #: 后端名与说明（"taichi/cuda"、"numpy"、回退原因……）
    "backend": "",
    "note": "",
    #: 面板读数：鸟数、平均邻居数、平均速率
    "count": 0,
    "neighbours": 0.0,
    "speed": 0.0,
    #: 每帧耗时（指数滑动平均）与上次构建耗时
    "ms": 0.0,
    "build_ms": 0.0,
    "error": "",
    "busy": False,
}


# --- 参数换算 ---------------------------------------------------------------


def signature(settings) -> tuple:
    """重建签名：变了就得重建求解器（判据放在参数组里，只有一份）。"""
    return settings.geometry_signature()


def params(settings) -> dict:
    """面板 → 求解器参数（键名与 :data:`solver.DEFAULTS` 一致）。"""
    values = {key: float(getattr(settings, key)) for key in solver_mod.FLOAT_KEYS}
    values["boundary"] = str(settings.boundary)
    return values


# --- 构建 / 重置 ------------------------------------------------------------


def build(settings, scene=None) -> dict:
    """按面板参数重建：求解器 → 初始撒点 → 对象 / 材质 / 属性 → 场景 → 第一帧。"""
    scene = scene or bpy.context.scene
    started = time.perf_counter()

    active = solver_mod.create(
        count=int(settings.count),
        seed=int(settings.seed),
        backend=str(settings.backend),
        params=params(settings),
    )
    active.reset()

    # 对象进当前激活的 collection（ensure_object 内部取 context.collection）
    obj = mesh.find_object() or mesh.ensure_object()
    mesh.ensure_material(obj)

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
    _write(settings, obj)

    if scene.world is None:
        # 场景本来没有世界：补一个项目画布底色，否则渲染出来是全黑
        scene_mod.apply_world(scene)
    scene_mod.ensure_camera(scene, float(settings.width), float(settings.height))

    settings.dirty = False
    STATE["build_ms"] = (time.perf_counter() - started) * 1000.0
    return {
        "count": int(active.count),
        "backend": active.backend,
        "note": active.note,
        "ms": STATE["build_ms"],
    }


def reset(settings, scene=None) -> bool:
    """按当前种子重新撒一遍（不重建求解器、不重建网格拓扑）。"""
    active = STATE.get("solver")
    if active is None:
        return False
    active.set_params(params(settings))
    active.set_count(int(settings.count))
    active.reset()
    STATE["frame"] = 0
    obj = mesh.find_object()
    if obj is None:
        return False
    _write(settings, obj)
    return True


def refresh(settings) -> bool:
    """不推进物理，只把参数落进求解器并重画当前帧（鸟数 / 配色 / 箭头长度用它）。"""
    active = STATE.get("solver")
    if active is None:
        return False
    active.set_params(params(settings))
    active.set_count(int(settings.count))
    obj = mesh.find_object()
    if obj is None:
        return False
    _write(settings, obj)
    return True


def redraw(settings) -> bool:
    """显示参数（配色 / 箭头长度）改了：不推进物理，只重刷当前帧。"""
    return refresh(settings)


# --- 推进 -------------------------------------------------------------------


def advance(settings, scene, target: int) -> None:
    """把求解器推进到 ``target`` 帧（回退则先回到第 0 帧）。"""
    active = STATE.get("solver")
    if active is None:
        return

    target = int(target)
    if target < int(STATE["frame"]):
        active.reset()
        STATE["frame"] = 0

    frames = target - int(STATE["frame"])
    if frames <= 0:
        return

    active.set_params(params(settings))
    substeps = max(1, int(settings.substeps))
    dt = max(float(settings.time_step), 1e-4)
    for _ in range(frames):
        active.step(substeps, dt)
    STATE["frame"] = target


# --- 写入网格 ---------------------------------------------------------------


def _write(settings, obj) -> None:
    """位置 + 速度 → 三角形几何与逐鸟颜色，一次写进网格并刷新面板读数。"""
    active = STATE["solver"]
    if active is None or obj is None:
        return

    positions = np.asarray(active.positions(), np.float32)
    velocities = np.asarray(active.velocities(), np.float32)

    rgba = colour.colours(
        str(settings.colour_mode), velocities, float(settings.max_speed)
    )
    vertices = mesh.boid_vertices(positions, velocities, float(settings.size))
    mesh.write_state(obj, vertices, velocities, rgba)

    speeds = np.linalg.norm(velocities, axis=1)
    STATE["count"] = len(positions)
    STATE["capacity"] = int(active.capacity)
    STATE["neighbours"] = float(getattr(active, "mean_neighbours", 0.0))
    STATE["speed"] = float(speeds.mean()) if speeds.size else 0.0


def sync(settings, obj=None) -> bool:
    """把当前状态写进网格（没对象 / 没求解器就什么都不做）。"""
    obj = obj or mesh.find_object()
    if obj is None or STATE.get("solver") is None:
        return False
    _write(settings, obj)
    return True


# --- 撒鸟 -------------------------------------------------------------------


def spawn(settings, centre, radius: float = 0.0, number: int = 1) -> int:
    """在 ``centre``（世界坐标 ``(x, y)``）附近撒 ``number`` 只鸟，返回真的加了几只。

    回写的鸟数会同步到面板（``settings.count``），所以撒完再拖鸟数滑杆不会把刚撒的鸟
    悄悄丢掉 —— 拖着它只会继续补 / 减。
    """
    active = STATE.get("solver")
    if active is None:
        return 0

    point = np.asarray(centre, np.float32).reshape(1, 2)
    points = np.repeat(point, max(1, int(number)), axis=0)
    added = active.spawn(points, spread=float(radius))
    if added:
        settings.count = int(active.count)
        obj = mesh.find_object()
        if obj is not None:
            _write(settings, obj)
    return int(added)


# --- Frame Change Handler ---------------------------------------------------


def tick(scene) -> None:
    """handler 的实现体：签名变了就重建，然后推进到当前帧并写网格。"""
    settings = getattr(scene, "sim_flocking", None)
    if settings is None or STATE["busy"]:
        return

    obj = mesh.find_object()
    if obj is None:
        return
    if obj.mode != "OBJECT":
        # 编辑模式下顶点数据由 BMesh 掌管，foreach_set 会被丢掉；退出编辑就恢复正常
        return

    STATE["busy"] = True
    started = time.perf_counter()
    try:
        if STATE.get("solver") is None or STATE["signature"] != signature(settings):
            build(settings, scene)
        elif settings.dirty:
            reset(settings, scene)
            settings.dirty = False

        active = STATE["solver"]
        if int(settings.count) != int(active.count):
            # 鸟数滑杆：只是补 / 砍缓冲尾巴，已经在飞的鸟不被打断
            active.set_count(int(settings.count))

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
            print("[sim_flocking] handler error:", message)
        STATE["error"] = message
    finally:
        STATE["busy"] = False


def flocking_frame_handler(scene) -> None:
    tick(scene)


def purge_handlers() -> None:
    """移除**本包注册过的** handler，防止重载后堆叠（重载 = 禁用 → 清模块 → 启用）。

    判据是**模块命名空间**（``__package__`` 前缀），不是函数名：``bpy.app.handlers`` 是一串
    平铺的函数对象，没有任何"按插件分区"的机制，而仓库里各 sim 插件的 handler 全叫
    ``frame_handler`` —— 按名字清理就会把别人家的也摘掉（两个 sim 插件同时启用时，后启用的
    那个会把先启用的停掉）。模块名是唯一稳定的判据：扩展装在 ``bl_ext.<repo>.<pkg>`` 下，
    热重载后模块名不变、函数对象换新。

    函数名另外还带插件前缀（:func:`flocking_frame_handler`），是为了防**旧版本**的其它插件
    —— 那些版本仍按名字清场，名字唯一的 handler 它们清不到。
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
        "count": int(STATE.get("count", 0)),
        "capacity": int(STATE.get("capacity", 0)),
        "neighbours": float(STATE.get("neighbours", 0.0)),
        "speed": float(STATE.get("speed", 0.0)),
        "backend": str(STATE.get("backend", "")),
        "note": str(STATE.get("note", "")),
        "ms": float(STATE.get("ms", 0.0)),
        "build_ms": float(STATE.get("build_ms", 0.0)),
        "error": str(STATE.get("error", "")),
    }
