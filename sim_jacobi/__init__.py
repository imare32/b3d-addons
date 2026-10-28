"""Jacobi theta 除数环面：环面上运动的零点 + 相位干涉格纹，逐帧驱动顶点与顶点色。

和仓库里另外几个插件一样分成两半：

**算法层**（不 import bpy，可以脱离 Blender 单独跑测试）::

    solver         —— theta 除子 + 零点弹性碰撞（纯 NumPy）+ 后端工厂 :func:`create`
    taichi_solver  —— 同一套公式的 Taichi GPU kernel（可选，取不到就回退 NumPy）
    colour         —— 四张标量场 → 顶点色（只用项目色板）

**Blender 层**（只在 :func:`register` 里导入）::

    mesh    —— nu×nv 环面网格、四张标量属性 + 顶点色、材质、零点小球网格
    sim     —— frame_change_pre handler：推进零点 → 写顶点与属性
    scene   —— 世界底色 / 3/4 视角相机 / 渲染设置
    viewport —— Solid 视图的着色颜色检查（颜色写在属性里，Material 着色看不出来）
    props   —— ``scene.sim_jacobi`` 参数组
    ops     —— 构建、重置、步进、场景预设、视口修正、删除
    ui      —— ``View3D > 侧栏 > Jacobi``

数学式子与参考实现的对应关系见 :mod:`sim_jacobi.solver` 的模块文档
（``ga2/common/jacobi_theta.py`` + ``ga2/scripts/blender_jacobi_theta_torus.py``）。
"""

from __future__ import annotations

from .solver import (
    BACKEND_AUTO,
    BACKEND_NUMPY,
    BACKEND_TAICHI,
    DEFAULTS,
    MAX_ZEROS,
    NumPyJacobi,
    SphereParticles,
    create,
    taichi_available,
    theta1,
)

__all__ = [
    # solver
    "BACKEND_AUTO",
    "BACKEND_NUMPY",
    "BACKEND_TAICHI",
    "DEFAULTS",
    "MAX_ZEROS",
    "NumPyJacobi",
    "SphereParticles",
    "create",
    "taichi_available",
    "theta1",
]


def _classes():
    """要注册的 RNA 类（惰性导入，顺序即注册顺序）。"""
    from . import ops, props, ui

    return (
        props.SimJacobiSettings,
        ops.SJAC_OT_build,
        ops.SJAC_OT_reset,
        ops.SJAC_OT_step,
        ops.SJAC_OT_setup_scene,
        ops.SJAC_OT_fix_viewport,
        ops.SJAC_OT_delete,
        ui.SJAC_PT_main,
    )


def register():
    import bpy

    from . import props, sim

    for cls in _classes():
        bpy.utils.register_class(cls)
    # 指针必须在 SimJacobiSettings 注册之后挂到 Scene 上
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
        backend="",
        note="",
        zeros=0,
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
