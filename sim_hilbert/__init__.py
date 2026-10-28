"""3D 希尔伯特曲线 + 弹簧质点物理：逐帧推进，实时画成 Grease Pencil 笔划。

和仓库里另外几个插件一样分成两半：

**算法层**（不 import bpy，可以脱离 Blender 单独跑测试）::

    curve   —— 序号 ↔ 3D 格子坐标（希尔伯特映射）、弹簧网络拓扑
    solver  —— 弹簧质点求解器（纯 NumPy 后端）+ 后端工厂 :func:`create`
    taichi_solver —— 同一套方程的 Taichi GPU kernel（可选，取不到就回退 NumPy）
    colour  —— 速度 / 应变 → 逐点颜色（只用项目色板）

**Blender 层**（只在 :func:`register` 里导入）::

    gp       —— Grease Pencil v3 后端（图层 / 逐点位置 / 逐点颜色 / 额外标量 / 逐帧重刷）
    sim      —— frame_change_pre handler：推进物理 → 写笔划
    scene    —— 世界底色 / 斜俯视正交相机 / 地面纸面 / 渲染设置
    viewport —— Solid 视图的着色颜色检查（颜色写在属性里，Material 着色下线是全黑的）
    props    —— ``scene.sim_hilbert`` 参数组
    ops      —— 构建、重置、步进、场景预设、视口修正、删除
    ui       —— ``View3D > 侧栏 > Hilbert``

模型见 :mod:`sim_hilbert.curve` 与 :mod:`sim_hilbert.solver` 的模块文档：顶点 = 希尔伯特
序号 = 质点，弹簧铺在格子的 6 / 18 / 26 邻域上，曲线本身（``0..N-1`` 依次相连）就是一个
笔划。缺省 3 阶（512 点 / 5068 弹簧，26 邻域）落下去会明显坍缩摊开；4 阶是 4096 点 /
4.7 万弹簧，只有 Taichi 后端能实时。
"""

from __future__ import annotations

from .colour import MODE_MONO, MODE_SPEED, MODE_STRAIN, MODES, colours
from .curve import (
    MAX_ORDER,
    NEIGHBOUR_ITEMS,
    Network,
    grid_size,
    hilbert_point,
    lattice_index,
    neighbour_offsets,
    network,
    vertex_count,
)
from .solver import (
    BACKEND_AUTO,
    BACKEND_NUMPY,
    BACKEND_TAICHI,
    DEFAULTS,
    NumPyMassSpring,
    create,
    taichi_available,
)

__all__ = [
    # curve
    "MAX_ORDER",
    "NEIGHBOUR_ITEMS",
    "Network",
    "grid_size",
    "hilbert_point",
    "lattice_index",
    "network",
    "neighbour_offsets",
    "vertex_count",
    # solver
    "BACKEND_AUTO",
    "BACKEND_NUMPY",
    "BACKEND_TAICHI",
    "DEFAULTS",
    "NumPyMassSpring",
    "create",
    "taichi_available",
    # colour
    "MODE_MONO",
    "MODE_SPEED",
    "MODE_STRAIN",
    "MODES",
    "colours",
]


def _classes():
    """要注册的 RNA 类（惰性导入，顺序即注册顺序）。"""
    from . import ops, props, ui

    return (
        props.SimHilbertSettings,
        ops.SHIL_OT_build,
        ops.SHIL_OT_reset,
        ops.SHIL_OT_step,
        ops.SHIL_OT_setup_scene,
        ops.SHIL_OT_fix_viewport,
        ops.SHIL_OT_delete,
        ui.SHIL_PT_main,
    )


def register():
    import bpy

    from . import props, sim

    for cls in _classes():
        bpy.utils.register_class(cls)
    # 指针必须在 SimHilbertSettings 注册之后挂到 Scene 上
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
        network=None,
        solver=None,
        signature=None,
        frame=0,
        backend="",
        note="",
        speed_peak=0.0,
        strain_mean=0.0,
        strain_peak=0.0,
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
