"""物理驱动的实时同步：``frame_change_pre`` 逐帧推进 XPBD，把点列写进 GP。

流程（每帧）::

    读面板参数（重力 / 阻力 / 弹性 / 精度）
      → 求解器推进到当前帧（回退时先重置）
      → 由环心算环面轴 → 生成环的笔划点列
      → 张力 → 顶点色
      → foreach_set 写回 GP 的 position / vertex_color

三条硬约定：

* **几何参数一变就重建**：环数 / 跨度 / 环尺寸 / 段数 / 相扣开关进签名，
  签名不一致时 handler 里自动重建（求解器维度与笔划拓扑都得跟着换）；
* **物理参数实时生效**：重力 / 阻力 / 弹性 / 子步 / 迭代次数只是求解器实参，
  拖动滑块后下一帧就变，不重建；
* **向前推进、向后重置**：GP 不是缓存，拖时间轴回退只能重算，所以
  ``target < 当前帧`` 时先回到初始拱形（与参考实现一致）。
"""

from __future__ import annotations

import math
import time

import bpy
import numpy as np

from . import chain, gp
from . import scene as scene_mod
from .solver import XpbdChain

__all__ = [
    "ALPHA_MAX",
    "STATE",
    "advance",
    "build",
    "frame_handler",
    "purge_handlers",
    "redraw",
    "reset",
    "stats",
    "sync",
]

#: 弹性 100% 对应的 XPBD 柔度。离线标定：alpha = 128 时 600 帧稳态下垂多出约
#: 0.39 m（相比刚杆），肉眼可辨但不至于拉成一团。
ALPHA_MAX = 128.0

#: 环境阻力 100% 对应的子步速度阻尼损失。整条滑杆都落在有效区间 [0.98, 1.0]。
DRAG_DAMP_MAX = 0.02

#: 相扣检查不是每帧都算（它是 Python 循环，纯为面板读数）：每这么多帧量一次。
INTERLOCK_EVERY = 6

#: 模块级会话状态（handler / 算子 / 面板共用）。
STATE: dict = {
    "solver": None,
    "signature": None,
    "frame": 0,
    #: 关节位置（求解器自由度，环数的 +1）
    "joints": None,
    #: 环心 / 长轴方向 / 环面轴（都由关节推出来）
    "pos": None,
    "direction": None,
    "axes": None,
    "rings": None,
    "spine": None,
    "tension": None,
    "tension_ema": None,
    "theory": None,
    "theory_centres": None,
    "arch": None,
    "x_end": 0.0,
    "z_end": 0.0,
    "tmax": 1.0,
    "tmax_gravity": None,
    "rmse": 0.0,
    "interlock": (0, 0),
    "ms": 0.0,
    #: 实际写进 GP 的点数 / 笔划数（面板读数用，与几何层一致）
    "write_points": 0,
    "write_strokes": 0,
    "error": "",
    "busy": False,
}


# --- 参数换算 ---------------------------------------------------------------


def signature(settings) -> tuple:
    """几何签名：变了就得重建求解器与笔划拓扑（判据放在参数组里，只有一份）。"""
    return settings.geometry_signature()


def _damp(settings) -> float:
    """环境阻力 % → 子步速度阻尼（0% = 无阻力，100% = 最大）。"""
    return 1.0 - DRAG_DAMP_MAX * float(settings.drag) / 100.0


def _alpha(settings) -> float:
    """弹性 % → XPBD 柔度（0% = 刚杆）。"""
    return ALPHA_MAX * float(settings.elasticity) / 100.0


def _substep_dt(scene, settings) -> float:
    """子步时长：每帧走满 ``1/fps`` 的物理时间，与帧率解耦。"""
    fps = float(getattr(scene.render, "fps", 24) or 24)
    return 1.0 / (fps * max(1, int(settings.substeps)))


def _pitch(settings) -> float:
    """相扣节距 = 孔的内长 = ``直段 + 2(R - r)``（环是跑道形；直段 0 就是正圆）。"""
    return chain.pitch_for(
        float(settings.ring_major),
        float(settings.ring_minor),
        float(settings.ring_straight),
    )


# --- 构建 / 重置 ------------------------------------------------------------


def build(settings, scene=None) -> dict:
    """按面板参数重建：离散链 → 求解器 → 环几何 → GP 对象。

    返回 ``{"strokes": 笔划数, "points": 点数}``。改几何参数后由 handler 或
    「重建」算子调用；实测 101 环约 10 ms（NumPy 没有 kernel 要编译）。
    """
    scene = scene or bpy.context.scene
    started = time.perf_counter()

    pitch = _pitch(settings)
    # 关节数 = 环数 + 1：一环占一节，相邻环共用关节
    joints, arch, x_end, z_end = chain.arch_chain(
        int(settings.n_rings) + 1, float(settings.arch_a), pitch
    )
    solver = XpbdChain(arch, pitch=pitch, alpha=_alpha(settings))

    centres, direction = chain.chain_links(arch)
    axes = chain.link_axes(direction)
    theory_centres = 0.5 * (joints[:-1] + joints[1:])
    rings = chain.ring_geometry(
        centres,
        axes,
        float(settings.ring_major),
        int(settings.segments),
        curve=str(settings.curve_type),
        straight=float(settings.ring_straight),
        long=direction,
    )
    spine = chain.polyline_geometry(centres) if settings.show_spine else None

    STATE.update(
        solver=solver,
        signature=signature(settings),
        frame=0,
        pos=centres,
        joints=arch,
        direction=direction,
        axes=axes,
        rings=rings,
        spine=spine,
        tension=np.zeros(len(centres), np.float32),
        tension_ema=None,
        theory=joints,
        theory_centres=theory_centres,
        arch=arch,
        x_end=float(x_end),
        z_end=float(z_end),
        tmax=chain.theoretical_tension_max(joints, float(settings.gravity)),
        tmax_gravity=float(settings.gravity),
        rmse=float(np.sqrt(np.mean((centres - theory_centres) ** 2))),
        interlock=chain.interlock_stats(
            centres,
            axes,
            float(settings.ring_major),
            float(settings.ring_minor),
            float(settings.ring_straight),
            long=direction,
        )[:2],
        error="",
    )

    obj = gp.ensure_object(gp.OBJECT_NAME, scene.collection)
    sets = _stroke_sets(settings)
    gp.write(obj, sets, frame=_layer_frame(scene))
    STATE["write_points"] = int(sum(len(stroke_set.points) for stroke_set in sets))
    STATE["write_strokes"] = int(sum(len(stroke_set.counts) for stroke_set in sets))

    if scene.world is None:
        # 场景本来就没有世界：补一个项目画布底色，否则渲染出来是全黑
        scene_mod.apply_world(scene)
    scene_mod.ensure_camera(scene, x_end, z_end)
    settings.dirty = False
    STATE["ms"] = 0.0  # 帧耗时由 handler 测（构建耗时另算，不混进"每帧"读数）
    return {
        "strokes": STATE["write_strokes"],
        "points": STATE["write_points"],
    }


def _layer_frame(scene) -> int:
    """GP 图层帧号：放在场景起始帧上（之后靠"保持"显示到所有帧）。"""
    return int(getattr(scene, "frame_start", 0) or 0)


def reset(settings, scene=None) -> None:
    """回到初始拱形（帧 0），不重建对象、不重算拓扑。"""
    solver = STATE.get("solver")
    if solver is None:
        return
    solver.reset()
    STATE["frame"] = 0
    STATE["tension_ema"] = None
    STATE["tension"] = np.zeros(max(solver.n - 1, 0), np.float32)
    STATE["joints"] = solver.positions()
    _refresh_state(settings, _substep_dt(scene or bpy.context.scene, settings))
    sync(settings)


# --- 推进 -------------------------------------------------------------------


def advance(settings, scene, target: int) -> None:
    """把求解器推进到 ``target`` 帧（回退则先重置），并刷新派生几何。"""
    solver = STATE.get("solver")
    if solver is None:
        return
    substeps = max(1, int(settings.substeps))
    dt = _substep_dt(scene, settings)
    if target < STATE["frame"]:
        solver.reset()
        STATE["frame"] = 0
        STATE["tension_ema"] = None
    steps = int(target) - STATE["frame"]
    if steps > 0:
        gravity = float(settings.gravity)
        solver.advance(
            steps,
            dt,
            substeps=substeps,
            iters=int(settings.iterations),
            damp=_damp(settings),
            gravity=gravity,
            alpha=_alpha(settings),
            bend_limit=math.radians(float(settings.bend_limit)),
        )
        STATE["frame"] = int(target)
        if STATE["tmax_gravity"] != gravity and STATE.get("theory") is not None:
            # 张力色带上限 ∝ 重力：换重力就重标定，热力图语义保持不变
            STATE["tmax"] = chain.theoretical_tension_max(STATE["theory"], gravity)
            STATE["tmax_gravity"] = gravity
    _refresh_state(settings, dt)


def _refresh_state(settings, dt: float) -> None:
    """关节 → 环心/长轴 → 笔划点列 → 张力（每帧都走这条链）。"""
    solver = STATE["solver"]
    joints = np.asarray(solver.positions(), np.float32)
    centres, direction = chain.chain_links(joints)
    axes = chain.link_axes(direction)

    STATE["joints"] = joints
    STATE["pos"] = centres
    STATE["direction"] = direction
    STATE["axes"] = axes
    STATE["rings"] = chain.ring_geometry(
        centres,
        axes,
        float(settings.ring_major),
        int(settings.segments),
        curve=str(settings.curve_type),
        straight=float(settings.ring_straight),
        long=direction,
    )
    if settings.show_spine:
        STATE["spine"] = chain.polyline_geometry(centres)
    else:
        STATE["spine"] = None

    # 杆张力是"每节"的量，正好一环一个
    tension = solver.tension(dt)
    ema = float(settings.ema_alpha) if bool(settings.smooth_tension) else 0.0
    if ema > 0.0:
        previous = STATE["tension_ema"]
        if previous is None or previous.shape != tension.shape:
            tension = tension.copy()
        else:
            tension = previous * (1.0 - ema) + tension * ema
        STATE["tension_ema"] = tension
    else:
        STATE["tension_ema"] = None
    STATE["tension"] = tension

    theory = STATE.get("theory_centres")
    if theory is not None:
        STATE["rmse"] = float(np.sqrt(np.mean((centres - theory) ** 2)))

    frame = STATE["frame"]
    if frame % INTERLOCK_EVERY == 0:
        ok, total, _violated = chain.interlock_stats(
            centres,
            axes,
            float(settings.ring_major),
            float(settings.ring_minor),
            float(settings.ring_straight),
            long=direction,
        )
        STATE["interlock"] = (ok, total)


# --- 写入 GP ----------------------------------------------------------------


def _ring_colours(settings, tension: np.ndarray) -> np.ndarray:
    """每环颜色 ``(N,4)`` 线性。"""
    if str(settings.colour_mode) == "MONO":
        colour = gp.linear_rgba(tuple(settings.ring_colour))
        return np.tile(np.asarray(colour, np.float32), (len(tension), 1))
    return gp.tension_colours(tension, float(STATE["tmax"]))


def _stroke_sets(settings) -> list:
    """当前状态 → 要写给 GP 的笔划组（顺序即图层顺序：脊线在下、环在上）。"""
    rings = STATE["rings"]
    if rings is None:
        return []
    colours = _ring_colours(settings, STATE["tension"])
    sets = []
    spine = STATE.get("spine") if settings.show_spine else None
    if spine is None and settings.show_spine and STATE.get("pos") is not None:
        # 面板刚把脊线打开：物理没动，就地补一条，不用等下一帧
        spine = STATE["spine"] = chain.polyline_geometry(STATE["pos"])
    if spine is not None:
        sets.append(
            gp.StrokeSet(
                layer=gp.LAYER_SPINE,
                counts=spine.counts,
                points=spine.points,
                radius=float(settings.spine_width),
                colours=colours[spine.ring],
                cyclic=spine.cyclic,
            )
        )
    sets.append(
        gp.StrokeSet(
            layer=gp.LAYER_RINGS,
            counts=rings.counts,
            points=rings.points,
            radius=float(settings.line_width),
            colours=colours[rings.ring],
            cyclic=rings.cyclic,
            curve=rings.curve,
            handles_left=rings.handles_left,
            handles_right=rings.handles_right,
        )
    )
    return sets


def sync(settings, obj=None) -> bool:
    """把当前状态写进 GP：拓扑没变就只刷点列，变了就整层重写。"""
    obj = obj or gp.find_object()
    sets = _stroke_sets(settings)
    if obj is None or not sets:
        return False
    STATE["write_points"] = int(sum(len(stroke_set.points) for stroke_set in sets))
    STATE["write_strokes"] = int(sum(len(stroke_set.counts) for stroke_set in sets))
    if not gp.refresh(obj, sets):
        gp.write(obj, sets, frame=_layer_frame(bpy.context.scene))
    return True


def redraw(settings) -> bool:
    """显示参数（线宽 / 颜色模式 / 脊线）改了：不推进物理，只重刷当前帧。"""
    if STATE.get("solver") is None or STATE.get("rings") is None:
        return False
    return sync(settings)


# --- Frame Change Handler ---------------------------------------------------


def tick(scene) -> None:
    """handler 的实现体：参数签名变了就重建，然后推进到当前帧并写 GP。"""
    settings = getattr(scene, "sim_catenary", None)
    if settings is None or gp.find_object() is None or STATE.get("busy"):
        return
    STATE["busy"] = True
    started = time.perf_counter()
    try:
        if STATE.get("solver") is None or STATE["signature"] != signature(settings):
            build(settings, scene)
        previous = int(STATE["frame"])
        advance(settings, scene, int(scene.frame_current))
        sync(settings)
        # 一次 tick 可能补多帧（拖时间轴跳过一段），所以除以实际推进的帧数
        steps = max(1, int(STATE["frame"]) - previous)
        cost = (time.perf_counter() - started) * 1000.0 / steps
        STATE["ms"] = cost if not STATE["ms"] else 0.6 * STATE["ms"] + 0.4 * cost
        STATE["error"] = ""
    except Exception as exc:
        # 出错时 handler 每一帧都会再进来一次（比如环数填成了偶数），同一条错误只报一次，
        # 免得刷屏；错误本身留在 STATE 里给面板显示。
        message = str(exc)
        if message != STATE["error"]:
            print("[sim_catenary] handler error:", message)
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
    ok, total = STATE.get("interlock", (0, 0))
    return {
        "frame": int(STATE.get("frame", 0)),
        "lowest": float(STATE["pos"][:, 2].min())
        if STATE.get("pos") is not None
        else 0.0,
        "rmse": float(STATE.get("rmse", 0.0)),
        "interlock": (int(ok), int(total)),
        "tension": float(STATE["tension"].max())
        if STATE.get("tension") is not None
        else 0.0,
        "tmax": float(STATE.get("tmax", 1.0)),
        # 写进 GP 的实际数量（含脊线层）
        "strokes": int(STATE.get("write_strokes", 0)),
        "points": int(STATE.get("write_points", 0)),
        "ms": float(STATE.get("ms", 0.0)),
        "error": str(STATE.get("error", "")),
    }
