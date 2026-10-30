"""生成式星形纹样（girih）：算法层 + Blender 集成。

分成两半，互不依赖：

**算法层**（不 import bpy，可以脱离 Blender 单独跑测试）::

    girih  —— 十种阿基米德镶嵌、Hankin 射线（motif）、股线与交叉
    scene  —— 输出契约：``build_shapes()`` 得到一份 ``Shape`` 列表

**Blender 层**（只在 :func:`register` 里导入，因此本模块可被纯 Python 导入）::

    gp        —— Grease Pencil v3 渲染后端（分层 / 顶点色 / 批量属性写）
    props     —— ``scene.gen_girih`` 参数组
    ops       —— 生成 / 删除 / 场景预设 / 回读参数
    ui        —— ``View3D > 侧栏 > Girih`` 面板
    viewport  —— Solid 模式的着色检查（否则 GP 全黑）

**当前进度（阶段四）**：三种样式全通——``线条``（单层）、``带子``（描边 + 本体两层）、
``交织``（在交叉点分上下，压下的那一段切缺口、用补片贴平，**默认**）；另有可选的
``Fills``（星形填充，可按砖的边数分色）与 ``Tiling``（砖轮廓调试层）。

几何（``TILINGS`` / ``tiles_in`` / ``motif`` / ``weave``）移植自 islamic-patterns 的
``js/girih.js``（MIT）；版权与许可全文内联在 ``girih.py`` 末尾。
"""

from __future__ import annotations

from .girih import (
    TILINGS,
    Crossing,
    Strand,
    Tile,
    Tiling,
    motif,
    motif_and_star,
    over_under,
    tiles_in,
    tiling_ids,
    weave,
)
from .scene import (
    FILL_BY_SIDES,
    LAYER_BAND,
    LAYER_CASE,
    LAYER_FILLS,
    LAYER_LINES,
    LAYER_ORDER,
    LAYER_TILING,
    STYLE_BANDS,
    STYLE_INTERLACE,
    STYLE_LINES,
    Shape,
    build_shapes,
)

__all__ = [
    # girih
    "TILINGS",
    "Tiling",
    "Tile",
    "Strand",
    "Crossing",
    "tiling_ids",
    "tiles_in",
    "motif",
    "motif_and_star",
    "weave",
    "over_under",
    # scene
    "Shape",
    "LAYER_FILLS",
    "LAYER_LINES",
    "LAYER_CASE",
    "LAYER_BAND",
    "LAYER_TILING",
    "LAYER_ORDER",
    "FILL_BY_SIDES",
    "STYLE_LINES",
    "STYLE_BANDS",
    "STYLE_INTERLACE",
    "build_shapes",
]


def _classes():
    """要注册的 RNA 类（惰性导入，顺序即注册顺序）。"""
    from . import ops, props, ui

    return (
        props.GenGirihSettings,
        ops.GIRIH_OT_generate,
        ops.GIRIH_OT_sweep,
        ops.GIRIH_OT_delete,
        ops.GIRIH_OT_sync_from_object,
        ops.GIRIH_OT_setup_scene,
        ui.GIRIH_PT_main,
    )


def register():
    import bpy

    from . import props

    for cls in _classes():
        bpy.utils.register_class(cls)
    # 指针必须在 GenGirihSettings 注册之后挂到 Scene 上
    props.register()


def unregister():
    import bpy

    from . import ops, props

    # 先撤掉可能排着的自动重建：timer 回调会去碰已经注销的属性
    ops.cancel_rebuild()
    props.unregister()
    for cls in reversed(_classes()):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
