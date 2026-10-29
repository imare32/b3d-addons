"""Boids 群集（flocking）：每帧推进分离 / 对齐 / 聚集，把鸟画成一网格三角箭头。

和仓库里另外几个插件一样分成两半：

**算法层**（不 import bpy，可以脱离 Blender 单独跑测试）::

    solver         —— 二维 boids（三条规则 + 边界）+ 后端工厂 :func:`create`
    taichi_solver  —— 同一套方程的 Taichi GPU kernel（可选，取不到就回退 NumPy）
    colour         —— 速度 / 朝向 → 逐鸟颜色（只用项目色板）

**Blender 层**（只在 :func:`register` 里导入）::

    mesh     —— 三角形箭头几何、``velocity`` / ``speed`` / ``color`` 属性、材质
    sim      —— frame_change_pre handler：推进 → 写几何与颜色
    scene    —— 世界底色 / 按模拟区域取景的正交相机 / 渲染设置
    viewport —— Solid 视图的着色颜色检查（颜色写在属性里，Material 着色看不出速度色）
    props    —— ``scene.sim_flocking`` 参数组
    ops      —— 构建、重新撒点、步进、撒鸟（模态）、场景预设、视口修正、删除
    ui       —— ``View3D > 侧栏 > FLOCK``

模拟是**平面上的 2D 群体**（XY 平面，Z = 0）：鸟的位置与速度只有两个分量，方向决定箭头
朝向。与参考脚本 ``scripts/flocking.py`` 的差别在工程结构 —— 多一个 NumPy 后端、结果直接
写成网格而不是几何节点实例化、鸟数可实时增减、边界方式可选。详见 ``README.md``。
"""

from __future__ import annotations

from .solver import (
    BACKEND_AUTO,
    BACKEND_NUMPY,
    BACKEND_TAICHI,
    BOUNDARIES,
    BOUNDARY_BOUNCE,
    BOUNDARY_SOFT,
    BOUNDARY_WRAP,
    DEFAULTS,
    MAX_BOIDS,
    NumPyFlock,
    create,
    random_flock,
    steer,
    taichi_available,
)

__all__ = [
    # solver
    "BACKEND_AUTO",
    "BACKEND_NUMPY",
    "BACKEND_TAICHI",
    "BOUNDARIES",
    "BOUNDARY_BOUNCE",
    "BOUNDARY_SOFT",
    "BOUNDARY_WRAP",
    "DEFAULTS",
    "MAX_BOIDS",
    "NumPyFlock",
    "create",
    "random_flock",
    "steer",
    "taichi_available",
]


def _classes():
    """要注册的 RNA 类（惰性导入，顺序即注册顺序）。"""
    from . import ops, props, ui

    return (
        props.SimFlockingSettings,
        ops.FLOCK_OT_build,
        ops.FLOCK_OT_reset,
        ops.FLOCK_OT_step,
        ops.FLOCK_OT_paint,
        ops.FLOCK_OT_setup_scene,
        ops.FLOCK_OT_fix_viewport,
        ops.FLOCK_OT_delete,
        ui.FLOCK_PT_main,
    )


def register():
    import bpy

    from . import props, sim

    for cls in _classes():
        bpy.utils.register_class(cls)
    # 指针必须在 SimFlockingSettings 注册之后挂到 Scene 上
    props.register()

    # 场景不会被自动改写：用户点「构建」才建对象。handler 在没有对象时直接返回。
    sim.purge_handlers()
    bpy.app.handlers.frame_change_pre.append(sim.flocking_frame_handler)


def unregister():
    import bpy

    from . import props, sim

    sim.purge_handlers()
    # 清掉会话状态：重载后旧求解器（含 Taichi 字段）不该被复用
    sim.STATE.update(
        solver=None,
        signature=None,
        frame=0,
        capacity=0,
        backend="",
        note="",
        count=0,
        neighbours=0.0,
        speed=0.0,
        ms=0.0,
        build_ms=0.0,
        error="",
        busy=False,
    )

    props.unregister()
    for cls in reversed(_classes()):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
