"""生成式 Voronoi：算法层 + Blender 集成。

分成两半，互不依赖：

**算法层**（不 import bpy，可以脱离界面单独跑测试）::

    voronoi —— Delaunay 三角剖分 → 外心 → 单元；圆形 / 矩形裁剪、内缩、曲线采样

**Blender 层**（只在 :func:`register` 里导入，因此本模块可被纯 Python 导入）::

    seeds  —— 源物体（网格 / Grease Pencil）→ 世界坐标点列 + 指纹
    gp     —— Grease Pencil v3 后端（笔划 / 顶点色 / 填充 / ``cell_index``）
    build  —— 生成入口：种子 → 单元 → 对象（算子与实时处理器共用）
    props  —— ``scene.gen_voronoi`` 参数组
    live   —— ``depsgraph_update_post`` → timer，实时重划
    ops    —— 生成 / 撒点 / 删除 / 回读参数 / 场景预设
    ui     —— ``View3D > 侧栏 > Voronoi``

设计取舍、实测数据见本目录 ``README.md``。
"""

from __future__ import annotations

from .voronoi import (
    ARC_SEGMENTS,
    CIRCLE_SEGMENTS,
    CUBIC,
    EDGES,
    MIN_AREA,
    PADDING,
    QUADRATIC,
    SHAPES,
    Cell,
    build_cells,
    cell_stroke,
    circle_polygon,
    clean_polygon,
    clip_circle,
    clip_polygon,
    clip_rect,
    inset_polygon,
    polygon_area,
)

__all__ = [
    "PADDING",
    "MIN_AREA",
    "CIRCLE_SEGMENTS",
    "ARC_SEGMENTS",
    "Cell",
    "SHAPES",
    "EDGES",
    "QUADRATIC",
    "CUBIC",
    "build_cells",
    "cell_stroke",
    "circle_polygon",
    "clean_polygon",
    "clip_circle",
    "clip_polygon",
    "clip_rect",
    "inset_polygon",
    "polygon_area",
]


def _classes():
    """要注册的 RNA 类（惰性导入，顺序即注册顺序）。"""
    from . import ops, props, ui

    return (
        props.GenVoronoiSettings,
        ops.GENV_OT_generate,
        ops.GENV_OT_scatter,
        ops.GENV_OT_delete,
        ops.GENV_OT_edit_source,
        ops.GENV_OT_sync_from_object,
        ops.GENV_OT_toggle_source_wire,
        ops.GENV_OT_setup_scene,
        ui.GENV_PT_main,
    )


def register():
    import bpy

    from . import live, props

    for cls in _classes():
        bpy.utils.register_class(cls)
    # 指针必须在 GenVoronoiSettings 注册之后挂到 Scene 上
    props.register()
    live.install()


def unregister():
    import bpy

    from . import live, props

    live.remove()
    props.unregister()
    for cls in reversed(_classes()):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
