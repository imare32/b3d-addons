"""生成式交错编织（Gen Interlace）：股线 → 一上一下的蕾丝，实时画成 GP 笔划。

**入口**：3D 视图侧栏 → ``Interlace`` 面板；参数在 ``scene.gen_interlace`` 上。

管线（细节见各模块说明）::

    strands（曲线 / GP → 折线）
      → interlace.find_crossings（交叉事件）
      → interlace.solve_weave（沿股线交替的一上一下）
      → interlace.cut_pieces（被压住的弧段切掉）
      → gp.build_object（GP v3：每截一条笔划）

算法只依赖 numpy（不 import bpy，可以脱离界面单独跑）。
"""

from __future__ import annotations

from . import build, gp, interlace, live, ops, props, strands, ui
from .interlace import (
    ALTERNATE,
    CROSSING,
    RULES,
    STRAND,
    Crossing,
    Piece,
    Strand,
    cut_pieces,
    find_crossings,
    solve_weave,
)

__all__ = [
    "build",
    "gp",
    "interlace",
    "live",
    "ops",
    "props",
    "register",
    "strands",
    "ui",
    "unregister",
    # 算法层的常用名字也顺手导出，方便在 Python 控制台里直接试
    "ALTERNATE",
    "CROSSING",
    "RULES",
    "STRAND",
    "Crossing",
    "Piece",
    "Strand",
    "cut_pieces",
    "find_crossings",
    "solve_weave",
]


def _classes():
    """要注册的 RNA 类（惰性导入，顺序即注册顺序）。"""
    from . import ops, props, ui

    return (
        props.GenInterlaceSettings,
        ops.GENI_OT_generate,
        ops.GENI_OT_scatter,
        ops.GENI_OT_delete,
        ops.GENI_OT_edit_source,
        ops.GENI_OT_sync_from_object,
        ops.GENI_OT_toggle_source_wire,
        ops.GENI_OT_setup_scene,
        ui.GENI_PT_main,
    )


def register():
    import bpy

    from . import live, props

    for cls in _classes():
        bpy.utils.register_class(cls)
    # 指针必须在 GenInterlaceSettings 注册之后挂到 Scene 上
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
