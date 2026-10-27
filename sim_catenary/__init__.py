"""铁环相扣悬链线：XPBD 物理模拟，实时画成 Grease Pencil 笔划。

和仓库里另外两个插件（``gen_zellij`` / ``gen_rosette``）一样，分成两半：

**算法层**（不 import bpy，可以脱离 Blender 单独跑测试）::

    chain  —— 离散链理论（理论悬链线 + 镜像拱形）、环面轴、环 → 笔划点列、相扣校验
    solver —— XPBD 杆链求解器（纯 NumPy；重力预测 → 杆约束投影 → 速度更新）

**Blender 层**（只在 :func:`register` 里导入）::

    gp       —— Grease Pencil v3 后端（图层 / 逐点位置 / 逐点颜色 / 逐帧重刷）
    sim      —— frame_change_pre handler：推进物理 → 生成笔划 → 写进 GP
    scene    —— 世界底色 / 正交相机 / 渲染设置
    viewport —— Solid 视图的着色颜色检查（颜色写在属性里，Material 着色下是黑的）
    props    —— ``scene.sim_catenary`` 参数组
    ops      —— 构建、重置、场景预设、视口修正、删除
    ui       —— ``View3D > 侧栏 > Catenary``

与参考实现（``.ref/ring_catenary``）的差别：那边用「控制网格 + 几何节点实例化圆环」
表达链条，这边**直接画 GP 笔划** —— 一个环 = 一整圈闭合 3D 笔划，环面轴由局部切线
奇偶交错分配，谁压住谁交给 GP 的 3D 深度遮挡，颜色可以按张力走项目的色板。物理模型、
初始拱形、相扣判据沿用那边验证过的结论，只是求解器从 Taichi 换成了 NumPy（百来个节点
不需要 GPU，扩展也就不用背第三方依赖）。

环的轮廓按真实链条做：**「操场跑道」形**（两段直边 + 两个半圆端，长轴沿链方向；
``straight = 0`` 退回正圆）。笔划默认是**贝塞尔**（跑道形 6 个控制点，形状误差
0.017 mm），比 24 点折线少 4 倍控制点；``chain.ring_geometry(..., curve="POLY")``
仍可退回折线。
"""

from __future__ import annotations

from .chain import (
    ARCH_A,
    BEZIER_SEGMENTS,
    CURVE_BEZIER,
    CURVE_POLY,
    GRAVITY,
    PITCH,
    R_MAJOR,
    R_MINOR,
    RING_SEGMENTS,
    RING_STRAIGHT,
    RingGeometry,
    arch_chain,
    chain_links,
    interlock_stats,
    link_axes,
    pitch_for,
    polyline_geometry,
    recommended_segments,
    ring_geometry,
    tangents,
    theoretical_tension_max,
)
from .solver import XpbdChain

__all__ = [
    # chain
    "ARCH_A",
    "BEZIER_SEGMENTS",
    "CURVE_BEZIER",
    "CURVE_POLY",
    "GRAVITY",
    "PITCH",
    "R_MAJOR",
    "RING_SEGMENTS",
    "RING_STRAIGHT",
    "R_MINOR",
    "RingGeometry",
    "arch_chain",
    "chain_links",
    "link_axes",
    "tangents",
    "pitch_for",
    "ring_geometry",
    "polyline_geometry",
    "recommended_segments",
    "theoretical_tension_max",
    "interlock_stats",
    # solver
    "XpbdChain",
]


def _classes():
    """要注册的 RNA 类（惰性导入，顺序即注册顺序）。"""
    from . import ops, props, ui

    return (
        props.SimCatenarySettings,
        ops.SCAT_OT_build,
        ops.SCAT_OT_reset,
        ops.SCAT_OT_setup_scene,
        ops.SCAT_OT_fix_viewport,
        ops.SCAT_OT_delete,
        ui.SCAT_PT_main,
    )


def register():
    import bpy

    from . import props, sim

    for cls in _classes():
        bpy.utils.register_class(cls)
    # 指针必须在 SimCatenarySettings 注册之后挂到 Scene 上
    props.register()

    # 场景不会被自动改写：用户点「构建」才建对象。handler 在没有对象时直接返回。
    sim.purge_handlers()
    bpy.app.handlers.frame_change_pre.append(sim.frame_handler)


def unregister():
    import bpy

    from . import props, sim

    sim.purge_handlers()
    # 清掉会话状态：重载后旧求解器/旧几何不该被复用
    sim.STATE.update(
        solver=None,
        signature=None,
        frame=0,
        pos=None,
        axes=None,
        rings=None,
        spine=None,
        tension=None,
        tension_ema=None,
        error="",
    )
    props.unregister()
    for cls in reversed(_classes()):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
