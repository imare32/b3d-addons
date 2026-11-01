"""分形家族（生成式）：用复整数点阵的"基因"生成分形曲线，编辑过程带视口 HUD。

一条**基因**是一串复整数 + 每段两个变换标记（反转 / 镜像），表示"把线段换成折线"的替换
规则；反复替换就是分形。G 域是方点阵（高斯整数），E 域是三角点阵（艾森斯坦整数）。
算法与参考实现（Fractal Family 1.1.6）逐点一致，本插件改的是**交互**：

=================  ======================================  ==========================
                  参考实现                                 本插件
=================  ======================================  ==========================
编辑基因          侧栏列表里填两个整数、点两个小图标        视口 HUD：点阵上直接拖节点、
                                                          点 R/M 切标记、滚轮换等级
预览              三条临时曲线对象 + 一个隐藏集合          ``POST_VIEW`` 立即模式绘制，
                                                          **不创建任何数据块**
实时反馈          改一次参数手动开预览                     预览与 HUD 同帧刷新；已生成的
                                                          对象走防抖自动更新
=================  ======================================  ==========================

分层与依赖方向（与仓库其它 ``gen_*`` 一致）：

**算法层**（不 import ``bpy``，可脱离 Blender 跑）::

    lattice     —— 复整数 / 基因解析 / 点阵吸附 / 等级点列 / Teragon 展开
    hud_layout  —— HUD 的图元表与命中区（纯函数，可以拿 Pillow 渲染出来看排版）
    presets     —— 内置基因（96 条经典分形）

**Blender 层**（只在 :func:`register` 里导入）::

    props       —— ``scene.gen_fractal_family`` 参数组 + 对象上的参数回存
    preview     —— 视口预览：``POST_VIEW`` 立即模式画分级曲线
    hud         —— 视口 HUD：``POST_PIXEL`` 画面板、字体、会话状态
    curves      —— 曲线装配（折线 / 平滑 / Bezier 保形细分）
    build       —— 生成入口：算 Teragon → 写曲线对象 → 记参数
    live        —— 防抖自动更新（已生成的对象）
    ops         —— 生成 / 删除 / 场景预设 / 回读 / 复制粘贴 / **HUD 模态算子**
    ui          —— ``View3D > 侧栏 > Fractal``
    palette     —— 项目色板（逐字副本）
    viewport    —— 视口着色检查（逐字副本）

取舍与实测记录见本目录 ``README.md``。
"""

from __future__ import annotations

from .lattice import (
    DOMAINS,
    Affine2D,
    ComplexInteger,
    FractalGenerator,
    cumulative_points,
    elements_of_nodes,
    estimate_points,
    format_gene,
    gene_family,
    lattice_basis,
    lattice_from_xy,
    nodes_of,
    parse_gene,
    snap_to_lattice,
    teragon,
)
from .presets import DEFAULT_GENES, GENES_BY_NAME

__all__ = [
    "DEFAULT_GENES",
    "DOMAINS",
    "GENES_BY_NAME",
    "Affine2D",
    "ComplexInteger",
    "FractalGenerator",
    "cumulative_points",
    "elements_of_nodes",
    "estimate_points",
    "format_gene",
    "gene_family",
    "lattice_basis",
    "lattice_from_xy",
    "nodes_of",
    "parse_gene",
    "snap_to_lattice",
    "teragon",
]


def _classes():
    """要注册的 RNA 类（惰性导入，顺序即注册顺序）。"""
    from . import ops, props, ui

    return (
        # 属性组必须排在用到它的算子 / 面板前面
        props.GeneratorItem,
        props.GenFractalFamilySettings,
        ui.GFF_UL_items,
        ops.GFF_OT_generate,
        ops.GFF_OT_delete,
        ops.GFF_OT_sync_from_object,
        ops.GFF_OT_setup_scene,
        ops.GFF_OT_item_add,
        ops.GFF_OT_item_remove,
        ops.GFF_OT_item_move,
        ops.GFF_OT_copy_gene,
        ops.GFF_OT_paste_gene,
        ops.GFF_OT_set_domain,
        ops.GFF_OT_hud,
        ui.GFF_PT_panel,
    )


def register():
    import bpy

    from . import hud, live, preview, props

    for cls in _classes():
        bpy.utils.register_class(cls)
    # 指针必须在 GenFractalFamilySettings 注册之后挂到 Scene 上
    props.register()
    # 两个 draw handler：3D 预览（POST_VIEW）与 HUD（POST_PIXEL）
    preview.install()
    hud.install()
    live.install()
    # 首次启用时用 timer 填一条默认基因（那一刻才有可用的 scene context）
    props.install_bootstrap()


def unregister():
    import bpy

    from . import hud, live, preview, props

    # 先摘句柄 / timer / 处理器：它们摸的是 RNA 类，类注销后就失效了
    props.remove_bootstrap()
    live.remove()
    hud.remove()
    preview.remove()
    props.unregister()
    for cls in reversed(_classes()):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
