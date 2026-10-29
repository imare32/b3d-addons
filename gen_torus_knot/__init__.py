"""生成式环面 (p, q) 结：几何核心 → Grease Pencil 笔划（可选扫成实体管）。

分成两半，互不依赖：

**算法层**（不 import bpy，可以脱离 Blender 单独跑测试）::

    knot —— 参数化 / 股拆分 / 自适应分段 / u_map、v_map（公式照搬 ga4 的 torus_knot）

**Blender 层**（只在 :func:`register` 里导入，因此本模块可被纯 Python 导入）::

    gp       —— 默认后端：一个 Grease Pencil 对象，每股一条闭合笔划 + POINT 域属性
    tube     —— 可选后端：几何节点「笔划 → 曲线 → 扫管」（+ 材质）
    builder  —— 重算入口：算点 → 写笔划 → 挂修改器 → 记参数（算子与自动更新共用）
    live     —— 自动更新：参数一改就重算（防抖 timer，推迟到空闲时再写几何）
    props    —— ``scene.gen_torus_knot`` 参数组
    ops      —— 生成 / 删除 / 回读参数 / 场景预设
    ui       —— ``View3D > 侧栏 > Torus Knot`` 面板
    palette  —— 项目色板（逐字副本）
    viewport —— 视口 Solid 着色检查（逐字副本）

默认输出是**笔划**而不是网格：环面结是一条一维的曲线，画成笔划最贴身，也便于用几何节点
扫成实体管（``tube``）。几何核心的两条属性 ``u_map`` / ``v_map`` 落在 **POINT（vertex）
域**，扫管后会跟着到网格顶点上。

取舍与实测记录见本目录 ``README.md``。
"""

from __future__ import annotations

from .knot import (
    ADAPTIVE_DENSITY,
    ATTR_U,
    ATTR_V,
    MIN_RESOLUTION,
    TorusKnot,
    adaptive_resolution,
    build,
    link_count,
)

__all__ = [
    "ADAPTIVE_DENSITY",
    "ATTR_U",
    "ATTR_V",
    "MIN_RESOLUTION",
    "TorusKnot",
    "adaptive_resolution",
    "build",
    "link_count",
]


def _classes():
    """要注册的 RNA 类（惰性导入，顺序即注册顺序）。"""
    from . import ops, props, ui

    return (
        props.GenTorusKnotSettings,
        ops.GENTK_OT_generate,
        ops.GENTK_OT_delete,
        ops.GENTK_OT_sync_from_object,
        ops.GENTK_OT_setup_scene,
        ui.GENTK_PT_panel,
    )


def register():
    import bpy

    from . import live, props

    for cls in _classes():
        bpy.utils.register_class(cls)
    # 指针必须在 GenTorusKnotSettings 注册之后挂到 Scene 上
    props.register()
    live.install()


def unregister():
    import bpy

    from . import live, props

    # 先摘掉 handler 与待跑的 timer：它们摸的是 RNA 类，类注销后就失效了
    live.remove()
    props.unregister()
    for cls in reversed(_classes()):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass