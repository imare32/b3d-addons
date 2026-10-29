"""差分生长（differential growth）：逐帧推进，实时画成一条闭合的 Grease Pencil 笔划。

和仓库里另外几个插件一样分成两半：

**算法层**（不 import bpy，可以脱离 Blender 单独跑测试）::

    solver         —— PBD 位置约束 + 拓扑分裂求解器（纯 NumPy 后端）+ 后端工厂 :func:`create`
    taichi_solver  —— 同一套约束的 Taichi GPU kernel（可选，取不到就回退 NumPy）
    colour         —— 拉伸比 / 曲率 → 逐点颜色（只用项目色板）

**Blender 层**（只在 :func:`register` 里导入）::

    gp       —— Grease Pencil v3 后端（定长笔划缓冲 / 逐点半径与不透明度 / 逐帧重刷）
    sim      —— frame_change_pre handler：推进生长 → 写笔划
    scene    —— 世界底色 / 正俯视正交相机 / 渲染设置
    viewport —— Solid 视图的着色颜色检查（颜色写在属性里，Material 着色下线是全黑的）
    props    —— ``scene.sim_differential`` 参数组
    ops      —— 构建、重置、步进、场景预设、视口修正、删除
    ui       —— ``View3D > 侧栏 > Differential``

模型见 :mod:`sim_differential.solver` 的模块文档：一条闭合折线，平滑 + 斥力 + 培养皿边界
把节点推来推去，每帧再把最长的几条边切开 —— 弧长一直涨、外围不许扩，多出来的长度只能
自己打褶。参考实现 ``.ref/differential_growth.py``（Taichi）的观感照搬，工程上的差别是
斥力走空间哈希（NumPy 后端）、拓扑分裂放在 numpy 里、输出改成定长 GP 笔划而不是网格边。
缺省 1500 节点上限 / 8 子步，NumPy 后端约 9 ms/帧。
"""

from __future__ import annotations

from .colour import MODE_CURVATURE, MODE_MONO, MODE_STRETCH, MODES, colours
from .solver import (
    BACKEND_AUTO,
    BACKEND_NUMPY,
    BACKEND_TAICHI,
    DEFAULTS,
    NumPyDifferentialGrowth,
    create,
    curvature_of,
    edge_lengths,
    grow_topology,
    initial_loop,
    stretch_of,
    taichi_available,
)

__all__ = [
    # solver
    "BACKEND_AUTO",
    "BACKEND_NUMPY",
    "BACKEND_TAICHI",
    "DEFAULTS",
    "NumPyDifferentialGrowth",
    "create",
    "curvature_of",
    "edge_lengths",
    "grow_topology",
    "initial_loop",
    "stretch_of",
    "taichi_available",
    # colour
    "MODE_CURVATURE",
    "MODE_MONO",
    "MODE_STRETCH",
    "MODES",
    "colours",
]


def _classes():
    """要注册的 RNA 类（惰性导入，顺序即注册顺序）。"""
    from . import ops, props, ui

    return (
        props.SimDifferentialSettings,
        ops.SDIF_OT_build,
        ops.SDIF_OT_reset,
        ops.SDIF_OT_step,
        ops.SDIF_OT_setup_scene,
        ops.SDIF_OT_fix_viewport,
        ops.SDIF_OT_delete,
        ui.SDIF_PT_main,
    )


def register():
    import bpy

    from . import props, sim

    for cls in _classes():
        bpy.utils.register_class(cls)
    # 指针必须在 SimDifferentialSettings 注册之后挂到 Scene 上
    props.register()

    # 场景不会被自动改写：用户点「构建」才建对象。handler 在没有对象时直接返回。
    sim.purge_handlers()
    bpy.app.handlers.frame_change_pre.append(sim.frame_handler)


def unregister():
    import bpy

    from . import sim

    sim.purge_handlers()
    # 清掉会话状态：重载后旧求解器（含 Taichi 字段）不该被复用
    sim.STATE.update(
        solver=None,
        signature=None,
        frame=0,
        capacity=0,
        backend="",
        note="",
        nodes=0,
        stretch_mean=0.0,
        curvature_peak=0.0,
        ms=0.0,
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
