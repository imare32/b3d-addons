"""Gray-Scott 反应扩散（Reaction-Diffusion）：逐帧推进浓度场，实时画成 3D 高度场。

和仓库里另外几个插件一样分成两半：

**算法层**（不 import bpy，可以脱离 Blender 单独跑测试）::

    solver         —— Gray-Scott 求解器（纯 NumPy 后端）+ 预设 + 后端工厂 :func:`create`
    taichi_solver  —— 同一套方程的 Taichi GPU kernel（可选，取不到就回退 NumPy）
    colour         —— 浓度场 → 顶点色（只用项目色板）

**Blender 层**（只在 :func:`register` 里导入）::

    mesh      —— nx×ny 高度场网格、顶点属性（u / v / edge / color）、材质、种子取样
    sim       —— frame_change_pre handler：推进浓度场 → 写顶点与属性
    scene     —— 世界底色 / 斜俯视正交相机 / 渲染设置
    viewport  —— Solid 视图的着色颜色检查（颜色写在属性里，Material 着色看不出图案）
    props     —— ``scene.sim_reaction_diffusion`` 参数组
    ops       —— 构建、重置、步进、注种、场景预设、视口修正、删除
    ui        —— ``View3D > 侧栏 > Reaction``

方程、九点各向同性拉普拉斯与稳定性条件见 :mod:`sim_reaction_diffusion.solver` 的模块
文档；Taichi 版与参考实现 ``ga2/common/reaction_diffusion.py`` 逐式一致，只把 kernel 的
字段命名与参数注入方式理顺了。200² 网格 × 每帧 8 子步在 GPU 上是实时档；没有 taichi
时自动回退 NumPy（同方程，小网格可用），回退原因写在面板读数里。
"""

from __future__ import annotations

from .solver import (
    BACKEND_AUTO,
    BACKEND_NUMPY,
    BACKEND_TAICHI,
    DEFAULTS,
    PRESETS,
    SEEDS,
    NumPyReactionDiffusion,
    create,
    preset_params,
    taichi_available,
)

__all__ = [
    # solver
    "BACKEND_AUTO",
    "BACKEND_NUMPY",
    "BACKEND_TAICHI",
    "DEFAULTS",
    "PRESETS",
    "SEEDS",
    "NumPyReactionDiffusion",
    "create",
    "preset_params",
    "taichi_available",
]


def _classes():
    """要注册的 RNA 类（惰性导入，顺序即注册顺序）。"""
    from . import ops, props, ui

    return (
        props.SimReactionDiffusionSettings,
        ops.SRD_OT_build,
        ops.SRD_OT_reset,
        ops.SRD_OT_step,
        ops.SRD_OT_inject,
        ops.SRD_OT_setup_scene,
        ops.SRD_OT_fix_viewport,
        ops.SRD_OT_delete,
        ui.SRD_PT_main,
    )


def register():
    import bpy

    from . import props, sim

    for cls in _classes():
        bpy.utils.register_class(cls)
    # 指针必须在 SimReactionDiffusionSettings 注册之后挂到 Scene 上
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
        topology=None,
        frame=0,
        backend="",
        note="",
        v_mean=0.0,
        v_max=0.0,
        cover=0.0,
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
