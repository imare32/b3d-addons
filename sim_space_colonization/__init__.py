"""太空殖民（space colonization）生长：逐帧在**任意输入网格**上长出藤蔓 / 树枝。

和仓库里另外几个插件一样分成两半：

**算法层**（不 import bpy，可以脱离 Blender 单独跑测试）::

    solver         —— 节点树 + 吸收点 + 生长规则（纯 NumPy）+ 搜索后端工厂 :func:`create`
    taichi_solver  —— Taichi GPU 并行最近邻搜索（可选，取不到就用 KD 树 / NumPy）
    colour         —— 深度 / 粗细 → 顶点色（只用项目色板）

**Blender 层**（只在 :func:`register` 里导入）::

    surface  —— 输入网格三角化、面积加权撒点、根点提取、表面投影（BVH 建在世界空间）
    gp       —— Grease Pencil v3 结果对象：一丛笔划 + radius / vertex_color 逐点属性
    sim      —— frame_change_pre handler：推进生长 → 拆链 → 重画笔划
    scene    —— 世界底色 / 环绕正交相机 / 渲染设置
    viewport —— Solid 视图的着色颜色检查（颜色写在属性里，Material 着色看不出渐变）
    props    —— ``scene.sim_space_colonization`` 参数组
    ops      —— 构建、重置、步进、建根顶点组、场景预设、视口修正、删除
    ui       —— ``View3D > 侧栏 > Colonize``

与参考实现（``ga2/scripts/blender_space_colonization.py`` +
``ga2/common/space_colonization.py``）的差别在工程结构，不在算法：吸收点从「网格顶点」
改成**按面积加权的表面取样**（粗网格也能长出东西）、表面模式先把方向压到切平面再贴回
（贴陡面时不会原地打转）、半径按 Murray 定律从叶尖数推（参考实现是逐节点加常数的近似）、
最近邻搜索三档可换（参考实现只有 Taichi kernel）。
"""

from __future__ import annotations

from .solver import (
    BACKEND_AUTO,
    BACKEND_NUMPY,
    BACKEND_TAICHI,
    DEFAULTS,
    KDTreeSearch,
    NumPySearch,
    SpaceColonization,
    create,
    taichi_available,
)

__all__ = [
    # solver
    "BACKEND_AUTO",
    "BACKEND_NUMPY",
    "BACKEND_TAICHI",
    "DEFAULTS",
    "KDTreeSearch",
    "NumPySearch",
    "SpaceColonization",
    "create",
    "taichi_available",
]


def _classes():
    """要注册的 RNA 类（惰性导入，顺序即注册顺序）。"""
    from . import ops, props, ui

    return (
        props.SimSpaceColonizationSettings,
        ops.SSCO_OT_build,
        ops.SSCO_OT_reset,
        ops.SSCO_OT_step,
        ops.SSCO_OT_make_root,
        ops.SSCO_OT_setup_scene,
        ops.SSCO_OT_fix_viewport,
        ops.SSCO_OT_toggle_target_wire,
        ops.SSCO_OT_delete,
        ui.SSCO_PT_main,
    )


def register():
    import bpy

    from . import props, sim

    for cls in _classes():
        bpy.utils.register_class(cls)
    # 指针必须在 SimSpaceColonizationSettings 注册之后挂到 Scene 上
    props.register()

    # 场景不会被自动改写：用户点「构建」才建对象。handler 在没有对象时直接返回。
    sim.purge_handlers()
    bpy.app.handlers.frame_change_pre.append(sim.frame_handler)


def unregister():
    import bpy

    from . import props, sim

    sim.purge_handlers()
    # 清掉会话状态：重载后旧求解器（含 Taichi 引用）不该被复用
    sim.STATE.update(
        solver=None,
        surface=None,
        signature=None,
        unit=1.0,
        frame=0,
        backend="",
        note="",
        nodes=0,
        strokes=0,
        attractors=0,
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
