"""逐帧推进：``frame_change_pre`` 里跑 RK4，把点列 / 半径 / 颜色写进后端对象。

每帧流程::

    读面板参数 → 签名不一致就重建（相位场 + 后端对象 + 拓扑）
      → 推进到当前帧（回退则从初始阵列重算）
      → 场 → 笔划缓冲（点列 / 逐点半径 / 逐点颜色 / 4 个标量）
      → 后端的 refresh()（全是 foreach_set）；拓扑对不上就退化成 write()

三条硬约定（与 ``sim_hilbert`` / ``sim_catenary`` 一致）：

* **几何一变就重建**：网格边长 / 初值模式 / 间距 / 输出后端进签名，签名不一致时 handler
  自动重建（点数变了，笔划拓扑也得换）；
* **物理参数实时生效**：重力 / 杆长 / 质量 / 时间步 / 子步只是积分器实参，拖动滑块后
  下一帧就变，不重建；
* **向前推进、向后重置**：相空间阵列不是缓存，拖时间轴回退只能从初始阵列重算
  （RK4 跑 2304 个双摆约 0.1 ms/子步，回退 100 帧也就几十毫秒）。

**计时**：``ms`` 是每帧总耗时（指数滑动平均），``step_ms`` 是积分、``sync_ms`` 是写后端
—— 这两个数就是 README 里 GP 与网格同步效率对比的来源（面板上也直接显示）。
"""

from __future__ import annotations

import time

import bpy

from . import field as field_mod
from . import gp, image, mesh, strokes
from . import scene as scene_mod

__all__ = [
    "OUTPUT_GP",
    "OUTPUT_IMAGE",
    "OUTPUT_MESH",
    "OUTPUTS",
    "STATE",
    "advance",
    "backend_of",
    "benchmark",
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

#: 输出后端标识（与 props 的枚举一致）。
OUTPUT_GP = "GP"
OUTPUT_MESH = "MESH"
OUTPUT_IMAGE = "IMAGE"

OUTPUTS = (OUTPUT_GP, OUTPUT_MESH, OUTPUT_IMAGE)

#: 面板 → 积分器的物理参数（键名与 :data:`field.DEFAULTS` 一致）。
PARAM_KEYS = tuple(field_mod.DEFAULTS)

#: 一个双摆几个笔划点（三条笔划：下摆 2 + 上摆 2 + 扣子 1）。
_POINTS_PER_PENDULUM = strokes.POINTS_PER_PENDULUM

#: 模块级会话状态（handler / 算子 / 面板共用）。
STATE: dict = {
    #: 当前相空间场（:class:`sim_pendulum.field.PhaseField`）
    "field": None,
    "signature": None,
    "frame": 0,
    #: 当前输出后端名（"GP" / "MESH"）
    "output": "",
    #: 当前求解器后端（"numpy" / "taichi"）与说明（"taichi/cuda"、回退原因……）
    "solver": "",
    "note": "",
    #: 面板读数
    "speed_peak": 0.0,
    "energy": 0.0,
    "energy0": 0.0,
    "energy_scale": 1.0,
    "param_key": None,
    "drift": 0.0,
    #: 每帧耗时（指数滑动平均）：总 / 积分 / 摊缓冲 / 纯写后端
    "ms": 0.0,
    "step_ms": 0.0,
    "buffer_ms": 0.0,
    "sync_ms": 0.0,
    "build_ms": 0.0,
    "error": "",
    "busy": False,
}


def backend_of(settings):
    """当前输出后端的模块（``gp`` / ``mesh`` / ``image``）。"""
    output = str(settings.output)
    if output == OUTPUT_MESH:
        return mesh
    if output == OUTPUT_IMAGE:
        return image
    return gp


def backend_name(settings) -> str:
    output = str(settings.output)
    return output if output in OUTPUTS else OUTPUT_GP


def signature(settings) -> tuple:
    """几何签名：变了就得重建拓扑与场（判据放在参数组里，只有一份）。"""
    return settings.geometry_signature()


def params(settings) -> dict:
    """面板 → 积分器物理参数。"""
    return {key: float(getattr(settings, key)) for key in PARAM_KEYS}


def _layer_frame(scene) -> int:
    """GP 图层帧号：放在场景起始帧上（图层帧之间是"保持"，一帧 drawing 覆盖整段动画）。"""
    return int(getattr(scene, "frame_start", 1) or 1)


# --- 构建 / 重置 ------------------------------------------------------------


def build(settings, scene=None) -> dict:
    """按面板参数重建：相空间场 → 笔划缓冲 → 输出对象 → 场景（世界 / 相机）。"""
    scene = scene or bpy.context.scene
    started = time.perf_counter()

    active_field = field_mod.create(
        n=int(settings.n_grid),
        init=str(settings.init_mode),
        spacing=float(settings.spacing),
        spin_max=float(settings.spin_max),
        backend=str(settings.backend),
        **params(settings),
    )
    # 点阵画法的位置是静态的：让 GPU 后端别每帧回拷点列（百万摆时那是 60 MB/帧）
    if hasattr(active_field, "pull_points"):
        active_field.pull_points = str(settings.layout) != strokes.LAYOUT_DOTS

    buffers = strokes.build(
        active_field,
        mode=str(settings.colour_mode),
        speed_max=float(settings.speed_max),
        width=float(settings.pen_radius),
        dot_scale=float(settings.dot_scale),
        dual=(tuple(settings.dual_upper), tuple(settings.dual_lower)),
        layout=str(settings.layout),
        extras=str(settings.output) != OUTPUT_IMAGE,
    )

    # 换后端时把另外两边清掉，免得场景里躺着一个不动的旧对象
    active_backend = backend_of(settings)
    for other in (gp, mesh, image):
        if other is not active_backend:
            other.remove_object()

    obj = active_backend.ensure_object(scene=scene)
    active_backend.write(obj, buffers, frame=_layer_frame(scene))

    energy = active_field.energies()
    STATE.update(
        field=active_field,
        signature=signature(settings),
        frame=0,
        output=backend_name(settings),
        solver=str(getattr(active_field, "backend", "numpy")),
        note=str(getattr(active_field, "note", "")),
        speed_peak=float(active_field.omega().max()),
        energy=float(energy.mean()),
        energy0=float(energy.mean()),
        energy_scale=_energy_scale(active_field),
        param_key=_param_key(active_field),
        drift=0.0,
        error="",
        points=len(buffers.points),
        ms=0.0,
        step_ms=0.0,
        buffer_ms=0.0,
        sync_ms=0.0,
    )

    if scene.world is None:
        # 场景本来就没有世界：补一个项目画布底色，否则渲染出来是全黑
        scene_mod.apply_world(scene)
    if str(settings.output) != OUTPUT_IMAGE:
        # 杆 / 网格输出才需要相机与渲染设置；图像后端的结果在图像数据块里，
        # 场景里一个对象都不建（在图像编辑器里看）。
        span, center_z = scene_mod.frame_of(
            int(settings.n_grid),
            float(settings.spacing),
            active_field.reach,
        )
        scene_mod.ensure_camera(scene, span, center_z)
        scene_mod.apply_render(
            scene,
            scene_mod.resolution_for(
                int(settings.n_grid), float(settings.spacing), active_field.reach
            ),
        )

    settings.dirty = False
    STATE["build_ms"] = (time.perf_counter() - started) * 1000.0
    return {
        "output": backend_name(settings),
        "solver": STATE["solver"],
        "note": STATE["note"],
        "pendulums": active_field.count,
        "points": len(buffers.points),
        "strokes": len(buffers.sizes),
        "ms": STATE["build_ms"],
    }


def reset(settings, scene=None) -> bool:
    """回到初始阵列（第 0 步），不重建拓扑。"""
    active_field = STATE.get("field")
    if active_field is None:
        return False
    active_field.reset()
    STATE["frame"] = 0
    STATE["drift"] = 0.0
    obj = backend_of(settings).find_object()
    return sync(settings, obj)


# --- 推进 -------------------------------------------------------------------


def advance(settings, target: int) -> None:
    """把相空间场推进到 ``target`` 帧（回退则先回到初始阵列）。"""
    active_field = STATE.get("field")
    if active_field is None:
        return

    target = int(target)
    if target < int(STATE["frame"]):
        active_field.reset()
        STATE["frame"] = 0

    steps = target - int(STATE["frame"])
    if steps <= 0:
        return

    active_field.set_params(**params(settings))
    substeps = max(1, int(settings.substeps))
    active_field.step(float(settings.dt), steps * substeps)
    STATE["frame"] = target


# --- 写入后端 ---------------------------------------------------------------


def _buffers(settings, layout=None) -> strokes.Strokes:
    """当前场状态 → 笔划缓冲（``layout`` 不给就用面板上的画法）。"""
    return strokes.build(
        STATE["field"],
        mode=str(settings.colour_mode),
        speed_max=float(settings.speed_max),
        width=float(settings.pen_radius),
        dot_scale=float(settings.dot_scale),
        dual=(tuple(settings.dual_upper), tuple(settings.dual_lower)),
        layout=layout or str(settings.layout),
        # 图像后端没有属性可消费：那 6 个标量不用摊（百万点时省 ~20 ms）
        extras=str(settings.output) != OUTPUT_IMAGE,
    )


def _param_key(active_field) -> tuple:
    """物理参数指纹：改了就把能量基准重取（否则"漂移"会把参数跳变算成积分误差）。"""
    return (
        round(active_field.gravity, 6),
        round(active_field.l1, 6),
        round(active_field.l2, 6),
        round(active_field.m1, 6),
        round(active_field.m2, 6),
    )


def _energy_scale(active_field) -> float:
    """能量尺子：势阱深度 ``(m1+m2)·g·l1 + m2·g·l2`` [J]。"""
    return max(
        (active_field.m1 + active_field.m2) * active_field.gravity * active_field.l1
        + active_field.m2 * active_field.gravity * active_field.l2,
        1e-9,
    )


#: 超过这个摆数，面板读数（能量 / 漂移）改成**抽样**估计（每 4^k 个取一个）。
_ENERGY_SAMPLE_LIMIT = 65536


def _refresh_stats() -> None:
    """面板读数：速度峰值 / 能量 / 相对漂移。

    大 N 时按抽样估计能量（100 万摆全量算一次要 12 ms，而读数只是诊断用）。
    """
    active_field = STATE["field"]
    count = int(active_field.count)
    step = 1
    while count // step > _ENERGY_SAMPLE_LIMIT:
        step *= 4
    if step > 1:
        sample = slice(0, count, step)
        mean_energy = float(active_field.energies()[sample].mean())
    else:
        mean_energy = float(active_field.energies().mean())
    STATE["speed_peak"] = float(active_field.omega().max())
    STATE["energy"] = mean_energy

    key = _param_key(active_field)
    if STATE.get("param_key") != key:
        # 重力 / 杆长 / 质量变了：能量本身就会跳，基准跟着走，漂移才只反映积分误差
        STATE["param_key"] = key
        STATE["energy0"] = mean_energy
        STATE["energy_scale"] = _energy_scale(active_field)

    STATE["drift"] = (mean_energy - float(STATE["energy0"])) / float(
        STATE["energy_scale"]
    )


def sync(settings, obj=None) -> bool:
    """把当前状态写进后端（没对象 / 没场就什么都不做）。

    分两段计时：``buffer_ms`` 是把场摊成缓冲（NumPy），``sync_ms`` 是纯写后端
    （``foreach_set``）。README 的 GP / 网格对比用的是后者 —— 只有它和"用哪种对象"有关。
    """
    obj = obj or backend_of(settings).find_object()
    if obj is None or STATE.get("field") is None:
        return False

    started = time.perf_counter()
    buffers = _buffers(settings)
    built = time.perf_counter()
    backend = backend_of(settings)
    if not backend.refresh(obj, buffers):
        backend.write(obj, buffers, frame=_layer_frame(bpy.context.scene))
    written = time.perf_counter()
    _refresh_stats()

    def smooth(key: str, value: float) -> None:
        previous = STATE.get(key) or 0.0
        STATE[key] = value if not previous else 0.5 * previous + 0.5 * value

    smooth("buffer_ms", (built - started) * 1000.0)
    smooth("sync_ms", (written - built) * 1000.0)
    STATE["points"] = len(buffers.points)
    return True


def redraw(settings) -> bool:
    """显示参数（配色 / 半径 / 单色）改了：不推进物理，只重刷当前帧。"""
    return sync(settings)


# --- 对照实测 ---------------------------------------------------------------


def benchmark(settings, repeats: int = 40) -> dict:
    """同一条缓冲在两个后端上各刷 ``repeats`` 次，返回每帧同步耗时 [ms]。

    量完把**非当前**输出后端建出来的临时对象删掉，场景状态不变。这个函数就是 README
    里那张对比表的来源（面板上也有「对照实测」按钮）。
    """
    active_field = STATE.get("field")
    if active_field is None:
        raise RuntimeError("先构建模拟")

    # 对照实测只在"杆"画法下有意义：网格后端本来就是按 5 顶点 2 边建的
    buffers = _buffers(settings, layout=strokes.LAYOUT_RODS)
    report: dict[str, float] = {}
    scene = bpy.context.scene
    for backend in (gp, mesh):
        obj = backend.ensure_object(scene=scene)
        backend.write(obj, buffers, frame=_layer_frame(bpy.context.scene))
        # 预热一次：首次写属性会带走建属性的开销
        backend.refresh(obj, buffers)
        started = time.perf_counter()
        for _ in range(max(1, int(repeats))):
            backend.refresh(obj, buffers)
        elapsed = (time.perf_counter() - started) * 1000.0 / max(1, int(repeats))
        report[backend.OBJECT_NAME] = elapsed

    if backend_of(settings) is gp:
        mesh.remove_object()
    else:
        gp.remove_object()
    return report


# --- Frame Change Handler ---------------------------------------------------


def tick(scene) -> None:
    """handler 的实现体：签名变了就重建，然后推进到当前帧并写后端。"""
    settings = getattr(scene, "sim_pendulum", None)
    if settings is None or STATE["busy"]:
        return

    backend = backend_of(settings)
    obj = backend.find_object()
    if obj is None:
        return
    if getattr(obj, "mode", "OBJECT") != "OBJECT":
        # 编辑模式下点列由编辑器掌管，foreach_set 会被丢掉；退出编辑就恢复
        # （图像后端没有 mode 属性，getattr 兜住）
        return

    STATE["busy"] = True
    started = time.perf_counter()
    try:
        if STATE.get("field") is None or STATE["signature"] != signature(settings):
            build(settings, scene)
        previous = int(STATE["frame"])
        step_started = time.perf_counter()
        advance(settings, int(scene.frame_current))
        step_cost = (time.perf_counter() - step_started) * 1000.0
        sync(settings, obj)
        # 一次 tick 可能补多帧（拖时间轴跳过一段），所以除以实际推进的帧数
        steps = max(1, int(STATE["frame"]) - previous)
        step_ms = step_cost / steps
        STATE["step_ms"] = (
            step_ms if not STATE["step_ms"] else 0.6 * STATE["step_ms"] + 0.4 * step_ms
        )
        cost = (time.perf_counter() - started) * 1000.0 / steps
        STATE["ms"] = cost if not STATE["ms"] else 0.6 * STATE["ms"] + 0.4 * cost
        STATE["error"] = ""
    except Exception as exc:
        # 出错时 handler 每帧都会再进来一次，同一条错误只报一次，免得刷屏
        message = str(exc)
        if message != STATE["error"]:
            print("[sim_pendulum] handler error:", message)
        STATE["error"] = message
    finally:
        STATE["busy"] = False


def frame_handler(scene) -> None:
    tick(scene)


def purge_handlers() -> None:
    """只移除**本包注册过**的 handler，防止重载后堆叠。

    判据是模块命名空间（``__package__`` 前缀），不是函数名：``bpy.app.handlers`` 是一串
    平铺的函数对象，仓库里各 sim 插件的 handler 全叫 ``frame_handler``，按名字清理会把
    别人家的也摘掉。
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
    active_field = STATE.get("field")
    return {
        "frame": int(STATE.get("frame", 0)),
        "pendulums": int(active_field.count) if active_field is not None else 0,
        "points": int(STATE.get("points") or 0),
        "speed_peak": float(STATE.get("speed_peak", 0.0)),
        "energy": float(STATE.get("energy", 0.0)),
        "drift": float(STATE.get("drift", 0.0)),
        "output": str(STATE.get("output", "")),
        "solver": str(STATE.get("solver", "")),
        "note": str(STATE.get("note", "")),
        "ms": float(STATE.get("ms", 0.0)),
        "step_ms": float(STATE.get("step_ms", 0.0)),
        "buffer_ms": float(STATE.get("buffer_ms", 0.0)),
        "sync_ms": float(STATE.get("sync_ms", 0.0)),
        "build_ms": float(STATE.get("build_ms", 0.0)),
        "error": str(STATE.get("error", "")),
    }


def field():
    """当前相空间场（没构建时返回 ``None``）。"""
    return STATE.get("field")
