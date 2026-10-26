"""生成式玫瑰线（star rosette）：算法层 + Blender 集成。

分成两半，互不依赖：

**算法层**（不 import bpy，可以脱离 Blender 单独跑测试）::

    rosette —— 核 → 镜像成花瓣 → 旋转 n 份 → 跳步重排 → 串成闭合曲线
    faces   —— 平面化边网 → 封闭区域（色块）
    design  —— 花型参数 + 种子 → ``Design``（曲线 + 色板）
    scene   —— 输出契约：``build_layers()`` 得到 图层名 → ``Shape`` 列表
    weave   —— 把边网编成上下交替的带子（缺口在几何里；源自 ``gen_zellij``）
    rng     —— ``Mulberry32``，同 seed 同结果

**Blender 层**（只在 :func:`register` 里导入，因此本模块可被纯 Python 导入）::

    gp      —— Grease Pencil v3 渲染后端（分层 / 顶点色 / fill_id / 端头）
    props   —— ``scene.gen_rosette`` 参数组
    ops     —— 生成、换种子、删除、场景预设、回读参数
    ui      —— ``View3D > 侧栏 > Gen Rosette``

几何移植自 Manim 版的 ``.ref/rosette.py``；编织与渲染沿用 ``gen_zellij`` 的做法
（那份代码在 5.2.2 上实测过）。设计取舍见本目录 ``README.md``。
"""

from __future__ import annotations

from .design import (
    FACE_BANDS,
    FACE_SLOTS,
    Design,
    build_design,
    colour_for_radius,
    init_colours,
)
from .faces import Face, build_faces, interior_faces, signed_area
from .rosette import (
    MIN_PETALS,
    STAR_TYPE_LABELS,
    STAR_TYPES,
    Curve,
    build_curves,
    cal_kernels,
    get_petals_by_kernel,
    max_level_cap,
    merge_adjacent_petals,
    realizable_level,
    sort_petals,
)
from .scene import (
    LAYER_BAND,
    LAYER_CASE,
    LAYER_FACES,
    LAYER_GUIDES,
    LAYER_ORDER,
    Shape,
    build_layers,
)
from .weave import Weave, WeaveStyle, build_weave

__all__ = [
    # rosette
    "MIN_PETALS",
    "STAR_TYPES",
    "STAR_TYPE_LABELS",
    "Curve",
    "cal_kernels",
    "get_petals_by_kernel",
    "realizable_level",
    "max_level_cap",
    "sort_petals",
    "merge_adjacent_petals",
    "build_curves",
    # faces
    "Face",
    "signed_area",
    "build_faces",
    "interior_faces",
    # design
    "FACE_BANDS",
    "FACE_SLOTS",
    "Design",
    "init_colours",
    "colour_for_radius",
    "build_design",
    # scene
    "Shape",
    "LAYER_FACES",
    "LAYER_CASE",
    "LAYER_BAND",
    "LAYER_GUIDES",
    "LAYER_ORDER",
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
        props.GenRosetteSettings,
        ops.GENR_OT_generate,
        ops.GENR_OT_random_seed,
        ops.GENR_OT_delete,
        ops.GENR_OT_sync_from_object,
        ops.GENR_OT_setup_scene,
        ui.GENR_PT_main,
    )


def register():
    import bpy

    from . import props

    for cls in _classes():
        bpy.utils.register_class(cls)
    # 指针必须在 GenRosetteSettings 注册之后挂到 Scene 上
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
