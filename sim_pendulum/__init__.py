"""双摆相空间阵列：N×N 个理想双摆逐帧 RK4 推进，实时画成 GP 笔划（或网格）。

和仓库里另外几个插件一样分成两半：

**算法层**（不 import bpy，可以脱离 Blender 单独跑测试）::

    field   —— N×N 初值阵列 + RK4 积分器 + 能量（纯 NumPy）+ 后端选择器 create()
    strokes —— 场 → 笔划缓冲：三笔点列 / 逐点半径 / 逐点颜色 / 6 个标量

**Blender 层**（只在 :func:`register` 里导入）::

    taichi_field —— 同一套公式的 Taichi GPU 版（可选依赖；一个 kernel 跑完积分 + 点列）
    gp      —— Grease Pencil v3 后端（图层 / 逐点位置 / 逐点半径 / 逐点颜色 / 逐帧重刷）
    mesh    —— 网格后端：5 顶点 2 条边 + 几何节点扫成圆管（与参考实现同构，对照用）
    sim     —— frame_change_pre handler：推进 → 摊缓冲 → 写后端，并计时
    scene   —— 世界底色 / 正面正交相机 / 分辨率 / 渲染设置
    viewport —— Solid 视图的着色颜色检查（Material 着色下线是全黑的）
    props   —— ``scene.sim_pendulum`` 参数组
    ops     —— 构建、重置、步进、场景预设、视口修正、对照实测、删除
    ui      —— ``View3D > 侧栏 > Pendulum``

模型见 :mod:`sim_pendulum.field` 与 :mod:`sim_pendulum.strokes` 的模块文档：一个摆 =
**三条笔划**（下摆 2 点 / 上摆 2 点 / 关节上的扣子 1 点），所以 N² 个摆就是 3N² 条笔划、
5N² 个点。三笔是为了"一根杆一个颜色"——一条折线只能给逐点颜色，渲染出来是渐变；
扣子与下摆同色、上摆另一种颜色，交界处才是一个"扣住"的接头。

缺省 16² = 256 个摆（1280 点 / 768 条笔划）整帧约 1.0 ms，且**大头是积分不是同步**：
积分 0.58 ms（5 个子步 × 4 次向量求值）、摊缓冲 0.14 ms、写后端 0.04 ms —— GP 的写回是纯
``foreach_set``，几乎不要钱。1 万摆时 NumPy 8.2 ms/帧、Taichi GPU 3.4 ms/帧（面板「求解器」
可切）。README 有这两张实测表：GP 与网格的同步开销（同数据量下 GP 快 4~28 倍）、
NumPy 与 Taichi 的整帧对比。
"""

from __future__ import annotations

from .field import (
    DEFAULTS,
    INIT_ANGLES,
    INIT_MODES,
    INIT_PHASE,
    KEY_POINTS,
    MAX_N,
    PhaseField,
    accelerations,
    energy,
)
from .strokes import (
    LAYER,
    MODE_DUAL,
    MODE_PHASE,
    MODE_SPECTRUM,
    MODE_SPEED,
    MODES,
    POINTS_PER_PENDULUM,
    SIZES,
    STROKES_PER_PENDULUM,
    Strokes,
    build,
)

__all__ = [
    # field
    "DEFAULTS",
    "INIT_ANGLES",
    "INIT_MODES",
    "INIT_PHASE",
    "KEY_POINTS",
    "MAX_N",
    "PhaseField",
    "accelerations",
    "energy",
    # strokes
    "LAYER",
    "MODE_DUAL",
    "MODE_PHASE",
    "MODE_SPECTRUM",
    "MODE_SPEED",
    "MODES",
    "POINTS_PER_PENDULUM",
    "SIZES",
    "STROKES_PER_PENDULUM",
    "Strokes",
    "build",
]


def _classes():
    """要注册的 RNA 类（惰性导入，顺序即注册顺序）。"""
    from . import ops, props, ui

    return (
        props.SimPendulumSettings,
        ops.PEND_OT_build,
        ops.PEND_OT_reset,
        ops.PEND_OT_step,
        ops.PEND_OT_setup_scene,
        ops.PEND_OT_fix_viewport,
        ops.PEND_OT_open_image,
        ops.PEND_OT_benchmark,
        ops.PEND_OT_delete,
        ui.PEND_PT_main,
    )


def register():
    import bpy

    from . import props, sim

    for cls in _classes():
        bpy.utils.register_class(cls)
    # 指针必须在 SimPendulumSettings 注册之后挂到 Scene 上
    props.register()

    # 场景不会被自动改写：用户点「构建」才建对象。handler 在没有对象时直接返回。
    sim.purge_handlers()
    bpy.app.handlers.frame_change_pre.append(sim.frame_handler)


def unregister():
    import bpy

    from . import sim

    sim.purge_handlers()
    # 清掉会话状态：重载后旧场（含数组缓冲）不该被复用
    sim.STATE.update(
        field=None,
        signature=None,
        frame=0,
        output="",
        solver="",
        note="",
        speed_peak=0.0,
        energy=0.0,
        energy0=0.0,
        param_key=None,
        drift=0.0,
        ms=0.0,
        step_ms=0.0,
        buffer_ms=0.0,
        sync_ms=0.0,
        build_ms=0.0,
        error="",
        busy=False,
    )

    from . import props

    props.unregister()
    for cls in reversed(_classes()):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
