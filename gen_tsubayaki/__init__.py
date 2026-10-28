"""生成式弦线族（Tsubayaki）：算法层 + Blender 集成。

分成两半，互不依赖：

**算法层**（不 import bpy，可以脱离 Blender 单独跑测试）::

    pattern —— 两条轨道之间的一族弦（n / t → ``Pattern``），移植自
               ``ga2/common/tsubayaki_pattern.py``（Processing / p5.js 版）
    scene   —— 输出契约：``build_layers()`` 得到 ``StrokeSet`` 列表
               （等比缩放 + 居中 + y 翻转，并判定 GP 的深度排序）

**Blender 层**（只在 :func:`register` 里导入，因此本模块可被纯 Python 导入）::

    gp      —— Grease Pencil v3 渲染后端（逐点不透明度 / 顶点色 / 逐帧重写）
    anim    —— 帧 → (n, t) **闭式**参数 + ``frame_change_pre`` 处理器
    props   —— ``scene.gen_tsubayaki`` 参数组
    ops     —— 生成、删除、场景预设
    ui      —— ``View3D > 侧栏 > Gen Tsubayaki``

**这是生成式，不是模拟类**：``generate()`` 是纯函数、没有跨帧状态，``t`` 是参数而不是
被累积的时间 —— 第 N 帧由公式直接给出，所以时间轴能随便拖、能只渲染中间某一帧，
回退也不用重置。处理器每帧只是"重算一遍"，不是"往前推一步"。
设计取舍与实测记录见本目录 ``README.md``。
"""

from __future__ import annotations

from .pattern import (
    ALL_VARIANTS,
    CONSTRAIN_BOUND,
    MAX_N,
    OUTER_ORBIT,
    PATTERN_MODES,
    TILE_SIZE,
    VARIANT_LABELS,
    VARIANT_MAP,
    VARIANTS,
    Pattern,
    chord_count,
    frame_ink,
    generate,
)
from .scene import (
    LAYER_CHORDS,
    LAYER_ORDER,
    StrokeSet,
    build_layers,
    depth_order,
)

__all__ = [
    # pattern
    "PATTERN_MODES",
    "VARIANTS",
    "VARIANT_LABELS",
    "VARIANT_MAP",
    "ALL_VARIANTS",
    "CONSTRAIN_BOUND",
    "TILE_SIZE",
    "OUTER_ORBIT",
    "MAX_N",
    "Pattern",
    "chord_count",
    "frame_ink",
    "generate",
    # scene
    "StrokeSet",
    "LAYER_CHORDS",
    "LAYER_ORDER",
    "build_layers",
    "depth_order",
]


def _classes():
    """要注册的 RNA 类（惰性导入，顺序即注册顺序）。"""
    from . import ops, props, ui

    return (
        props.GenTsubayakiSettings,
        ops.GENT_OT_generate,
        ops.GENT_OT_delete,
        ops.GENT_OT_setup_scene,
        ui.GENT_PT_main,
    )


def register():
    import bpy

    from . import anim, props

    for cls in _classes():
        bpy.utils.register_class(cls)
    # 指针必须在 GenTsubayakiSettings 注册之后挂到 Scene 上
    props.register()
    # 只挂一个人的处理器：帧一变就按当帧参数重算（内部自己判断要不要动）
    anim.install()


def unregister():
    import bpy

    from . import anim, props

    # 先下处理器，再拆参数与类：处理器里用到 settings 时不会撞上已删的属性
    anim.purge_handlers()
    anim.reset_cache()
    props.unregister()
    for cls in reversed(_classes()):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
