"""帧驱动的实时演化：``frame_change_pre`` 推代数，把状态写进面属性。

流程（每帧）::

    读面板参数（目标 / 规则 / 每代帧数 / 插值 / 后端）
      → 拓扑签名不一致（换了网格 / 面数变了 / 换了后端）就重建
      → 初始条件改过（种子 / 密度）就重新播第 0 代
      → 推进到第 frame // frames_per_gen 代（回退则从第 0 代重算）
      → state 与 next 之间按过渡帧数插值
      → 写 alive / age / color 三个属性

三条硬约定（与 ``sim_dendrite`` / ``sim_catenary`` 一致）：

* **拓扑变了就重建**：目标名、顶点数、面数、后端进签名，签名不一致时 handler 里自动重建；
* **节奏 / 规则 / 显示实时生效**：每代帧数、过渡帧数、插值、规则、配色只是实参，拖动后
  下一帧就变（规则改了从当前状态重算下一代，不清场）；
* **向前推进、向后重置**：状态不是缓存，拖时间轴回退只能从第 0 代重算 —— 于是同一条
  时间轴每次播出来的演化完全一样。跳到很远的帧会逐代补算（面越多、代数越多越慢）。

细胞数是面数：几千个面在 NumPy 后端也就零点几毫秒一代，Taichi 后端是几万个面才需要。
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
    "frame_handler",
    "purge_handlers",
    "redraw",
    "reset_fields",
    "stats",
    "sync",
    "target_object",
    "tick",
    "topology_signature",
]

#: 模块级会话状态（handler / 算子 / 面板共用）。
STATE: dict = {
    "solver": None,
    #: 拓扑签名（目标 / 面数 / 后端），与求解器一起重建
    "topology": None,
    #: 邻域体检（面数 / 8 邻域占比 / 邻居数范围），面板警告用
    "neighbours": {"faces": 0, "standard": 0.0, "min": 0, "max": 0},
    #: 已经推进到第几代、时间轴停在第几帧
    "generation": 0,
    "frame": 0,
    #: 每个细胞的连续存活代数（第 g 代的状态，随代数一起推进）
    "age": None,
    #: 后端名与说明（"taichi/cuda"、"numpy"、回退原因……）
    "backend": "",
    "note": "",
    "alive": 0.0,
    #: 每帧耗时（指数滑动平均）与上次构建耗时
    "ms": 0.0,
    "build_ms": 0.0,
    "error": "",
    "busy": False,
}


# --- 参数换算 ---------------------------------------------------------------


def target_object(settings):
    """当前要跑的目标网格：面板指定的优先，否则插件自建的缺省环面。"""
    obj = settings.target
    if obj is not None and obj.type == "MESH":
        return obj
    return mesh.find_object()


def topology_signature(settings, obj) -> tuple:
    """拓扑签名：变了就得重新提取邻域、重建求解器（判据只有这一份）。"""
    return (
        obj.name,
        len(obj.data.vertices),
        len(obj.data.polygons),
        str(settings.backend),
    )


def initial_state(settings, count: int) -> np.ndarray:
    """按种子 / 密度铺初始状态（0 / 1）。"""
    rng = np.random.default_rng(int(settings.seed))
    return (rng.random(int(count)) < float(settings.density)).astype(np.float32)


# --- 构建 / 重置 ------------------------------------------------------------


def build(settings, scene=None, obj=None) -> dict:
    """按面板参数重建：提拓扑 → 求解器 → 第 0 代 → 属性 / 材质 → 写第一帧。"""
    scene = scene or bpy.context.scene
    started = time.perf_counter()

    obj = obj or target_object(settings)
    if obj is None:
        # 缺省环面加进**当前激活的 collection**（不是场景根），与其它生成类插件一致
        obj = mesh.ensure_default_object()
        settings.target = obj

    neighbors, counts = mesh.extract_topology(obj)
    topology = mesh.topology_stats(counts)
    birth, survive = solver_mod.rule_masks(settings.rule)
    solver = solver_mod.create(
        len(counts),
        backend=str(settings.backend),
        birth=birth,
        survive=survive,
    )
    solver.load_topology(neighbors, counts)

    mesh.ensure_attributes(obj.data)
    mesh.ensure_material(obj)

    STATE.update(
        solver=solver,
        topology=topology_signature(settings, obj),
        neighbours=topology,
        generation=0,
        frame=0,
        age=None,
        backend=solver.backend,
        note=solver.note,
        alive=0.0,
        ms=0.0,
        error="",
    )
    reset_fields(settings)
    _write(settings, obj)

    if scene.world is None:
        # 场景本来没有世界：补一个项目画布底色，否则渲染出来是全黑
        scene_mod.apply_world(scene)
    scene_mod.ensure_camera(scene, obj)

    settings.dirty = False
    STATE["build_ms"] = (time.perf_counter() - started) * 1000.0
    return {
        "cells": len(counts),
        "backend": solver.backend,
        "note": solver.note,
        "ms": STATE["build_ms"],
    }


def reset_fields(settings) -> None:
    """只重新播第 0 代（不动拓扑、不重建求解器）。"""
    solver = STATE.get("solver")
    if solver is None:
        return
    states = initial_state(settings, solver.count)
    solver.set_rule(*solver_mod.rule_masks(settings.rule))
    solver.set_state(states)
    STATE["age"] = states.copy()
    STATE["generation"] = 0
    STATE["frame"] = 0


# --- 推进 -------------------------------------------------------------------


def _age_step(states: np.ndarray) -> None:
    """年龄推进一代：活着 +1，死掉归零。"""
    age = STATE.get("age")
    if age is None or age.size != states.size:
        age = np.zeros(states.size, np.float32)
    STATE["age"] = np.where(states > 0.5, age + 1.0, 0.0).astype(np.float32)


def advance(settings, scene, target: int) -> int:
    """把求解器推进到 ``target`` 帧（回退则从第 0 代重算），返回补了几代。"""
    solver = STATE.get("solver")
    if solver is None:
        return 0

    target = max(0, int(target))
    per_generation = max(1, int(settings.frames_per_gen))
    generation = target // per_generation

    if target < int(STATE["frame"]) or generation < int(STATE["generation"]):
        reset_fields(settings)

    steps = generation - int(STATE["generation"])
    for _ in range(max(0, steps)):
        solver.step(1)
        _age_step(solver.state_flat())
    if steps > 0:
        STATE["generation"] = generation
    STATE["frame"] = target
    return max(0, steps)


# --- 写入网格 ---------------------------------------------------------------


def _progress(settings) -> float:
    """当前帧在这一代里走了多少（0 = 刚换代，1 = 已经是完整的新一代）。"""
    per_generation = max(1, int(settings.frames_per_gen))
    transition = min(int(settings.transition_frames), per_generation)
    if transition <= 0:
        return 1.0
    return min(1.0, (int(STATE["frame"]) % per_generation) / float(transition))


def _write(settings, obj) -> None:
    """state / next 插值 → 面颜色，写进 alive / age / color 并刷新面板读数。"""
    solver = STATE["solver"]
    states = solver.state_flat()
    following = solver.next_flat()
    alive = solver_mod.blend(
        states, following, _progress(settings), str(settings.interpolation_mode)
    )
    age = STATE.get("age")
    if age is None or age.size != alive.size:
        age = np.zeros(alive.size, np.float32)

    colours = colour.colours(
        str(settings.colour_mode), alive, age, float(settings.age_scale)
    )
    mesh.write_state(obj, alive, age, colours)
    STATE["alive"] = float((states > 0.5).mean()) if states.size else 0.0


def sync(settings, obj=None) -> bool:
    """把当前状态写进网格（没对象 / 没求解器就什么都不做）。"""
    obj = obj or target_object(settings)
    if obj is None or STATE.get("solver") is None:
        return False
    mesh.ensure_attributes(obj.data)
    _write(settings, obj)
    return True


def redraw(settings) -> bool:
    """显示参数（配色 / 年龄尺度）改了：不推进代数，只重刷当前帧。"""
    return sync(settings)


# --- Frame Change Handler ---------------------------------------------------


def tick(scene) -> None:
    """handler 的实现体：签名变了就重建，然后推进到当前帧并写网格。"""
    settings = getattr(scene, "sim_game_of_life", None)
    if settings is None or STATE["busy"]:
        return

    obj = target_object(settings)
    if obj is None:
        return
    if obj.mode != "OBJECT":
        # 编辑模式下属性由 BMesh 掌控，foreach_set 会被丢掉；退出编辑就恢复正常
        return

    STATE["busy"] = True
    started = time.perf_counter()
    try:
        if STATE.get("solver") is None or STATE["topology"] != topology_signature(
            settings, obj
        ):
            build(settings, scene, obj)
        elif settings.dirty:
            reset_fields(settings)
            settings.dirty = False

        solver = STATE["solver"]
        solver.set_rule(*solver_mod.rule_masks(settings.rule))
        steps = advance(settings, scene, int(scene.frame_current))
        sync(settings, obj)

        # 一次 tick 可能补多代（拖时间轴跳过一段），所以除以实际推进的代数
        cost = (time.perf_counter() - started) * 1000.0 / max(1, steps)
        STATE["ms"] = cost if not STATE["ms"] else 0.6 * STATE["ms"] + 0.4 * cost
        STATE["error"] = ""
    except Exception as exc:
        # 出错时 handler 每帧都会再进来一次，同一条错误只报一次，免得刷屏
        message = str(exc)
        if message != STATE["error"]:
            print("[sim_game_of_life] handler error:", message)
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
    solver = STATE.get("solver")
    neighbours = STATE.get("neighbours") or {}
    return {
        "cells": int(getattr(solver, "count", 0)) if solver is not None else 0,
        "frame": int(STATE.get("frame", 0)),
        "generation": int(STATE.get("generation", 0)),
        "alive": float(STATE.get("alive", 0.0)),
        "standard": float(neighbours.get("standard", 0.0)),
        "neighbours_min": int(neighbours.get("min", 0)),
        "neighbours_max": int(neighbours.get("max", 0)),
        "backend": str(STATE.get("backend", "")),
        "note": str(STATE.get("note", "")),
        "ms": float(STATE.get("ms", 0.0)),
        "build_ms": float(STATE.get("build_ms", 0.0)),
        "error": str(STATE.get("error", "")),
    }
