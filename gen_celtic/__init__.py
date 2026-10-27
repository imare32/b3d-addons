"""生成式凯尔特结：算法层 + Blender 集成。

分成两半，互不依赖：

**算法层**（不 import bpy，可以脱离 Blender 单独跑测试）::

    knot —— 扭转标记 / 带子遍历 / 抬升 / 网格指纹（遍历规则照搬 celtic-knot）

**Blender 层**（只在 :func:`register` 里导入，因此本模块可被纯 Python 导入）::

    tube  —— 几何节点后端：Mesh to Curve → Bezier → 环截面挤出（+ 材质）
    props —— ``scene.gen_celtic`` 参数组
    weave —— 编结：源网格 → 结 → 场景对象（算子与实时处理器走同一条路）
    ops   —— 编结 / 删除 / 回读参数 / 场景预设
    live  —— 实时更新：``depsgraph_update_post`` 处理器
    ui    —— ``View3D > 侧栏 > Celtic`` 面板

算法与取舍见 ``knot.py`` 的模块说明与本目录 ``README.md``。
"""

from __future__ import annotations

from .knot import (
    IGNORE,
    STRAIGHT,
    TWIST_CCW,
    TWIST_CW,
    Knot,
    build,
    celtic_twists,
    get_offset,
    mean_edge_length,
    mesh_signature,
    next_edge_loop,
    next_face_loop,
    signature,
    visit_strands,
)

__all__ = [
    "IGNORE",
    "STRAIGHT",
    "TWIST_CCW",
    "TWIST_CW",
    "Knot",
    "build",
    "celtic_twists",
    "get_offset",
    "mean_edge_length",
    "mesh_signature",
    "next_edge_loop",
    "next_face_loop",
    "signature",
    "visit_strands",
]


def _classes():
    """要注册的 RNA 类（惰性导入，顺序即注册顺序）。"""
    from . import ops, props, ui

    return (
        props.GenCelticSettings,
        ops.GENC_OT_generate,
        ops.GENC_OT_delete,
        ops.GENC_OT_sync_from_object,
        ops.GENC_OT_setup_scene,
        ui.GENC_PT_main,
    )


def register():
    import bpy

    from . import live, props

    for cls in _classes():
        bpy.utils.register_class(cls)
    # 指针必须在 GenCelticSettings 注册之后挂到 Scene 上
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