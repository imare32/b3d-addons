"""生成式斐波那契穹顶：算法层 + Blender 集成。

分成两半，互不依赖：

**算法层**（不 import bpy，可以脱离 Blender 单独跑测试）::

    dome —— 斐波那契点 → 球面 Voronoi（半空间裁剪，纯 numpy）→ 逐代的面板多边形

**Blender 层**（只在 :func:`register` 里导入，因此本模块可被纯 Python 导入）::

    gn    —— 几何节点组：按帧挑代（生长）+ 立柱（按 seed_pos 属性放小球）
    mesh  —— 几何后端：网格 + 逐面属性 + 两个修改器 + 两个材质
    props —— ``scene.gen_dome`` 参数组
    ops   —— 生成 / 删除 / 回读参数 / 场景预设
    ui    —— ``View3D > 侧栏 > Dome``

算法与取舍见 ``dome.py`` 的模块说明与本目录 ``README.md``；参考实现是 ``ga2`` 的
``blender_fibonacci_dome.py`` + ``fibonacci_voronoi.py``（那份依赖 scipy，且用
``frame_change_pre`` 逐帧重算 Voronoi、重建网格；这里把「代」烘进网格，播放时只切代）。
"""

from __future__ import annotations

from .dome import (
    GENERATION_MAX,
    GOLDEN_ANGLE,
    MAX_POINTS,
    MIN_POINTS,
    PANEL_BUDGET,
    Panels,
    build_generations,
    build_panels,
    fibonacci_cap,
    fibonacci_hemisphere,
    generation_count,
    spherical_voronoi,
)

__all__ = [
    "GENERATION_MAX",
    "GOLDEN_ANGLE",
    "MAX_POINTS",
    "MIN_POINTS",
    "PANEL_BUDGET",
    "Panels",
    "build_generations",
    "build_panels",
    "fibonacci_cap",
    "fibonacci_hemisphere",
    "generation_count",
    "spherical_voronoi",
]


def _classes():
    """要注册的 RNA 类（惰性导入，顺序即注册顺序）。"""
    from . import ops, props, ui

    return (
        props.GenDomeSettings,
        ops.GEND_OT_generate,
        ops.GEND_OT_delete,
        ops.GEND_OT_sync_from_object,
        ops.GEND_OT_setup_scene,
        ui.GEND_PT_main,
    )


def register():
    import bpy

    from . import props

    for cls in _classes():
        bpy.utils.register_class(cls)
    # 指针必须在 GenDomeSettings 注册之后挂到 Scene 上
    props.register()


def unregister():
    import bpy

    from . import props

    props.unregister()
    for cls in reversed(_classes()):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
