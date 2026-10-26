"""生成式 Zellij：算法层 + Blender 集成。

分成两半，互不依赖：

**算法层**（不 import bpy，可以脱离 Blender 单独跑测试）::

    design  —— 构造线晶格、瓷砖生长、签名匹配、簇合并
    fillers —— ``fillers.json`` 图案库的惰性读取
    scene   —— 输出契约：``build_layers()`` 得到 图层名 → ``Shape`` 列表
    weave   —— 把色块边网编成上下交替的带子（缺口在几何里）
    geom    —— 点 / 仿射矩阵 / 包围盒适配 / 轮廓合并
    rng     —— ``Mulberry32``，同 seed 同结果

**Blender 层**（只在 :func:`register` 里导入，因此本模块可被纯 Python 导入）::

    gp      —— Grease Pencil v3 渲染后端（分层 / 顶点色 / fill_id / 虚线）
    props   —— ``scene.gen_zellij`` 参数组
    ops     —— 生成、换种子、删除、场景预设、回读参数
    ui      —— ``View3D > 侧栏 > Gen Zellij`` 面板

设计取舍、图层语义、常量表见本目录 ``README.md``。
"""

from __future__ import annotations

from .design import (
    CLUSTER_FOCUS,
    DENSITY_TO_LINES,
    FOCUS_VALUES,
    Design,
    Features,
    Grid,
    Tile,
    build_design,
    create_lines,
    get_signature,
    match_filler,
    merge_cluster,
    rand_fill,
)
from .fillers import clusters, has, load, signatures
from .scene import (
    LAYER_FILLS,
    LAYER_INTERLACE,
    LAYER_LINES,
    LAYER_ORDER,
    LAYER_OUTLINES,
    LAYER_TILING,
    STROKE_SATURATION,
    VIEW_LINES,
    VIEW_MODES,
    VIEW_POLYGONS,
    VIEW_TILING,
    VIEW_ZELLIJ,
    Shape,
    WeaveStyle,
    build_layers,
    build_shapes,
    derived_stroke,
)
from .weave import Weave, build_weave

__all__ = [
    # design
    "DENSITY_TO_LINES",
    "FOCUS_VALUES",
    "CLUSTER_FOCUS",
    "Features",
    "Tile",
    "Design",
    "Grid",
    "build_design",
    "create_lines",
    "get_signature",
    "match_filler",
    "rand_fill",
    "merge_cluster",
    # fillers
    "load",
    "has",
    "clusters",
    "signatures",
    # scene
    "Shape",
    "VIEW_MODES",
    "VIEW_ZELLIJ",
    "VIEW_TILING",
    "VIEW_LINES",
    "VIEW_POLYGONS",
    "LAYER_FILLS",
    "LAYER_OUTLINES",
    "LAYER_INTERLACE",
    "LAYER_TILING",
    "LAYER_LINES",
    "LAYER_ORDER",
    "STROKE_SATURATION",
    "derived_stroke",
    "build_shapes",
    "build_layers",
    # weave
    "Weave",
    "WeaveStyle",
    "build_weave",
]


def _classes():
    """要注册的 RNA 类（惰性导入，顺序即注册顺序）。"""
    from . import ops, props, ui

    return (
        props.GenZellijSettings,
        ops.GENZ_OT_generate,
        ops.GENZ_OT_random_seed,
        ops.GENZ_OT_delete,
        ops.GENZ_OT_sync_from_object,
        ops.GENZ_OT_setup_scene,
        ui.GENZ_PT_main,
    )


def register():
    import bpy

    from . import props

    for cls in _classes():
        bpy.utils.register_class(cls)
    # 指针必须在 GenZellijSettings 注册之后挂到 Scene 上
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
