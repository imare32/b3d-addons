"""物理驱动的实时同步：``frame_change_pre`` 逐帧推进弹簧质点，把点列写进 GP 笔划。

流程（每帧）::

    读面板参数（物理 / 子步 / 后端）
      → 签名不一致就重建（曲线拓扑 + 求解器 + GP 对象）
      → 求解器推进到当前帧（回退时先回到初始格子）
      → 位置 / 速度 / 应变 → 逐点颜色
      → foreach_set 写回 GP 的 position / vertex_color / speed / strain

三条硬约定（与 ``sim_catenary`` / ``sim_dendrite`` 一致）：

* **几何一变就重建**：阶数 / 尺寸 / 落差 / 邻域 / 后端进签名，签名不一致时 handler 里
  自动重建（顶点数变了，GP 的笔划拓扑也得换）；
* **物理参数实时生效**：重力 / 刚度 / 阻尼 / 质量 / 时间步 / 子步 / 地面只是求解器实参，
  拖动滑块后下一帧就变，不重建；
* **向前推进、向后重置**：弹簧系统的状态不是缓存，拖时间轴回退只能从初始格子重算。

一个细节：**回退之后不会自动播回去**。跳到第 100 帧要重算 100 帧的物理（每帧
``substeps`` 个子步），NumPy 后端在 4 阶上会很慢 —— 面板读数里会显示每帧耗时，先看它。
"""

from __future__ import annotations

import time

import bpy
import numpy as np

from . import colour, curve, gp
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
    #: 当前拓扑（:class:`sim_hilbert.curve.Network`）
    "network": None,
    "solver": None,
    "signature": None,
    "frame": 0,
    #: 后端名与说明（"taichi/cuda"、"numpy"、回退原因……）
    "backend": "",
    "note": "",
    #: 面板读数：速度峰值 / 应变均值与峰值
    "speed_peak": 0.0,
    "strain_mean": 0.0,
    "strain_peak": 0.0,
    #: 每帧耗时（指数滑动平均）与上次构建耗时
    "ms": 0.0,
    "build_ms": 0.0,
    "error": "",
    "busy": False,
}


# --- 参数换算 ---------------------------------------------------------------


def signature(settings) -> tuple:
    """几何签名：变了就得重建拓扑与求解器（判据放在参数组里，只有一份）。"""
    return settings.geometry_signature()


def params(settings) -> dict:
    """面板 → 求解器物理参数（键名与 :data:`solver.DEFAULTS` 一致）。"""
    return {key: float(getattr(settings, key)) for key in solver_mod.PARAM_KEYS}


def _layer_frame(scene) -> int:
    """GP 图层帧号：放在场景起始帧上（图层帧之间是"保持"，一帧 drawing 覆盖整段动画）。"""
    return int(getattr(scene, "frame_start", 1) or 1)


# --- 构建 / 重置 ------------------------------------------------------------


def build(settings, scene=None) -> dict:
    """按面板参数重建：曲线拓扑 → 求解器 → GP 对象 → 场景（世界 / 相机 / 地面）。"""
    scene = scene or bpy.context.scene
    started = time.perf_counter()

    net = curve.network(
        order=int(settings.order),
        size=float(settings.size),
        drop_height=float(settings.drop_height),
        neighbours=int(settings.neighbours),
    )
    active = solver_mod.create(
        net.positions,
        net.pairs,
        net.rest_lengths,
        backend=str(settings.backend),
        params=params(settings),
    )

    STATE.update(
        network=net,
        solver=active,
        signature=signature(settings),
        frame=0,
        backend=active.backend,
        note=active.note,
        error="",
        ms=0.0,
    )

    # 对象进当前激活的 collection（ensure_object 内部取 context.collection）
    obj = gp.find_object() or gp.ensure_object()
    _write(settings, obj)

    if scene.world is None:
        # 场景本来就没有世界：补一个项目画布底色，否则渲染出来是全黑
        scene_mod.apply_world(scene)
    span, target_z = scene_mod.frame_of(
        float(settings.size), float(settings.drop_height)
    )
    if settings.show_ground:
        scene_mod.ensure_ground(scene, span, float(settings.ground_height))
    scene_mod.ensure_camera(scene, span, target_z)

    settings.dirty = False
    STATE["build_ms"] = (time.perf_counter() - started) * 1000.0
    return {
        "backend": active.backend,
        "note": active.note,
        "vertices": net.vertex_count,
        "springs": net.spring_count,
        "ms": STATE["build_ms"],
    }


def reset(settings, scene=None) -> bool:
    """回到初始格子（帧 0），不重建拓扑、不重算颜色以外的东西。"""
    active = STATE.get("solver")
    if active is None:
        return False
    active.reset()
    STATE["frame"] = 0
    obj = gp.find_object()
    return _write(settings, obj) if obj is not None else False


# --- 推进 -------------------------------------------------------------------


def advance(settings, scene, target: int) -> None:
    """把求解器推进到 ``target`` 帧（回退则先回到初始格子）。"""
    active = STATE.get("solver")
    if active is None:
        return

    target = int(target)
    if target < int(STATE["frame"]):
        active.reset()
        STATE["frame"] = 0

    steps = target - int(STATE["frame"])
    if steps > 0:
        active.set_params(**params(settings))
        active.step(steps * max(1, int(settings.substeps)))
        STATE["frame"] = target


# --- 写入 GP ----------------------------------------------------------------


def _stroke(settings) -> tuple:
    """当前状态 → 一个笔划组（点列 + 逐点颜色 + 两个标量属性）。"""
    net = STATE["network"]
    active = STATE["solver"]
    positions = np.asarray(active.positions(), np.float32)
    speed = np.asarray(active.speed(), np.float32)
    strain = np.asarray(active.strain(), np.float32)

    count = int(net.vertex_count)
    if positions.shape[0] != count:
        raise ValueError(f"position count {positions.shape[0]} != topology {count}")

    colours = colour.colours(
        str(settings.colour_mode),
        speed,
        strain,
        float(settings.speed_max),
        float(settings.strain_max),
        tuple(settings.mono_colour),
    )

    STATE["speed_peak"] = float(speed.max()) if speed.size else 0.0
    STATE["strain_mean"] = float(strain.mean()) if strain.size else 0.0
    STATE["strain_peak"] = float(strain.max()) if strain.size else 0.0

    stroke = gp.StrokeSet(
        layer=gp.LAYER_CURVE,
        # 一整条曲线就是一个笔划：顶点顺序 = 希尔伯特序号，0..N-1 依次相连
        counts=np.array([count], np.int32),
        points=positions,
        radius=float(settings.line_width),
        colours=colours,
        cyclic=np.array([False]),
        extras=(("speed", speed), ("strain", strain)),
    )
    return (stroke,)


def _write(settings, obj) -> bool:
    """把当前状态写进 GP：拓扑没变就只刷点列，变了就整层重写。"""
    if obj is None:
        return False
    strokes = _stroke(settings)
    if not gp.refresh(obj, strokes):
        gp.write(obj, strokes, frame=_layer_frame(bpy.context.scene))
    return True


def sync(settings, obj=None) -> bool:
    """把当前状态写进 GP（没对象 / 没求解器就什么都不做）。"""
    obj = obj or gp.find_object()
    if obj is None or STATE.get("solver") is None:
        return False
    return _write(settings, obj)


def redraw(settings) -> bool:
    """显示参数（配色 / 线宽 / 地面）改了：不推进物理，只重刷当前帧。"""
    return sync(settings)


# --- Frame Change Handler ---------------------------------------------------


def _sync_ground(settings) -> None:
    """地面高度改了：把那张纸面挪过去（只有物理在动，不需要重建）。"""
    obj = bpy.data.objects.get(scene_mod.GROUND_NAME)
    if obj is None or not settings.show_ground:
        return
    height = float(settings.ground_height)
    if abs(float(obj.location.z) - height) > 1e-6:
        obj.location.z = height


def tick(scene) -> None:
    """handler 的实现体：签名变了就重建，然后推进到当前帧并写 GP。"""
    settings = getattr(scene, "sim_hilbert", None)
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
        sync(settings, obj)
        _sync_ground(settings)
        # 一次 tick 可能补多帧（拖时间轴跳过一段），所以除以实际推进的帧数
        steps = max(1, int(STATE["frame"]) - previous)
        cost = (time.perf_counter() - started) * 1000.0 / steps
        STATE["ms"] = cost if not STATE["ms"] else 0.6 * STATE["ms"] + 0.4 * cost
        STATE["error"] = ""
    except Exception as exc:
        # 出错时 handler 每帧都会再进来一次，同一条错误只报一次，免得刷屏
        message = str(exc)
        if message != STATE["error"]:
            print("[sim_hilbert] handler error:", message)
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
    net = STATE.get("network")
    return {
        "frame": int(STATE.get("frame", 0)),
        "vertices": int(net.vertex_count) if net is not None else 0,
        "springs": int(net.spring_count) if net is not None else 0,
        "speed_peak": float(STATE.get("speed_peak", 0.0)),
        "strain_mean": float(STATE.get("strain_mean", 0.0)),
        "strain_peak": float(STATE.get("strain_peak", 0.0)),
        "backend": str(STATE.get("backend", "")),
        "note": str(STATE.get("note", "")),
        "ms": float(STATE.get("ms", 0.0)),
        "build_ms": float(STATE.get("build_ms", 0.0)),
        "error": str(STATE.get("error", "")),
    }
