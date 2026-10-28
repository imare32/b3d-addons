"""曲面生命游戏（Conway's Game of Life）：逐帧推代数，实时画成面颜色。

和仓库里另外几个插件一样分成两半：

**算法层**（不 import bpy，可以脱离 Blender 单独跑测试）::

    solver         —— 面邻域求解器（纯 NumPy 后端）+ 后端工厂 :func:`create`
    taichi_solver  —— 同一套规则的 Taichi GPU kernel（可选，取不到就回退 NumPy）
    colour         —— 生死 / 年龄 → 面颜色（只用项目色板）

**Blender 层**（只在 :func:`register` 里导入）::

    mesh    —— 面邻域提取、``alive`` / ``age`` / ``color`` 属性、材质、缺省环面
    sim     —— frame_change_pre handler：推代数 → 插值 → 写面属性
    scene   —— 世界底色 / 按目标取景的相机 / 渲染设置
    viewport —— Solid 视图的着色颜色检查（颜色写在属性里，Material 着色看不出生死）
    props   —— ``scene.sim_game_of_life`` 参数组
    ops     —— 构建、重置、步进、场景预设、视口修正、删除
    ui      —— ``View3D > 侧栏 > GOL``

细胞是网格的**面**，邻域是至少共用一个顶点的其他面 —— 所以跑的是曲面上的细胞自动机，
不是平面棋盘。状态存在目标网格的面上（几何一个顶点都不动），``alive`` 是插值后的
0~1，几何节点 / 着色器按 ``> 0.5`` 取活格即可。

但**不是任意网格都好看**：康威规则假设「每个细胞 8 个邻居、每个细胞一样大」。低模上
一个面就是一大块，三角面是 11~12 个邻居，开口网格的边框面只有 3~5 个 —— 同一个规则
会在不同面上跑出不同的"方言"。要求与实测数据见 ``README.md`` 的「什么网格上才好看」。
"""

from __future__ import annotations

from .solver import (
    BACKEND_AUTO,
    BACKEND_NUMPY,
    BACKEND_TAICHI,
    DEFAULT_RULE,
    MAX_NEIGHBORS,
    RULES,
    NumPyLife,
    create,
    rule_masks,
    taichi_available,
)

__all__ = [
    # solver
    "BACKEND_AUTO",
    "BACKEND_NUMPY",
    "BACKEND_TAICHI",
    "DEFAULT_RULE",
    "MAX_NEIGHBORS",
    "RULES",
    "NumPyLife",
    "create",
    "rule_masks",
    "taichi_available",
]


def _classes():
    """要注册的 RNA 类（惰性导入，顺序即注册顺序）。"""
    from . import ops, props, ui

    return (
        props.SimGameOfLifeSettings,
        ops.GOL_OT_build,
        ops.GOL_OT_reset,
        ops.GOL_OT_step,
        ops.GOL_OT_setup_scene,
        ops.GOL_OT_fix_viewport,
        ops.GOL_OT_delete,
        ui.GOL_PT_main,
    )


def register():
    import bpy

    from . import props, sim

    for cls in _classes():
        bpy.utils.register_class(cls)
    # 指针必须在 SimGameOfLifeSettings 注册之后挂到 Scene 上
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
        topology=None,
        neighbours={"faces": 0, "standard": 0.0, "min": 0, "max": 0},
        generation=0,
        frame=0,
        age=None,
        backend="",
        note="",
        alive=0.0,
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
