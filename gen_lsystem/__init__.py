"""生成式 L-system：公理 + 产生式 → Grease Pencil 笔划（逐点半径 + 按粗细渐变）。

L-system（Lindenmayer system）是「一串符号按规则同时重写」的语法，再交给 turtle 解释成
几何 —— 本扩展把**几何那一半**做成纯路径：每条分支是一笔 GP 笔划，粗细逐点变化
（``!`` 变细、``#`` 变粗、``¤`` 直接设半径），所以树、分形曲线、空间填充曲线都能一笔画出来。

分成两半，互不依赖：

**算法层**（不 import bpy / mathutils，可以脱离 Blender 单独跑测试）::

    lsystem  —— 模块 / 规则 / 条件 / 参数表达式求值 / 并行重写
    turtle   —— 符号 → 折线路径（F f + - ^ & \\ / | $ [ ] ! # ¤ s £ %）
    presets  —— 十套预设（ABOP 与参考实现的经典例子）

**Blender 层**（只在 :func:`register` 里导入，因此本模块可被纯 Python 导入）::

    gp       —— 后端：一个 GP 对象，每条路径一笔 + POINT 域属性（radius / taper / depth）
    builder  —— 重算入口：算路径 → 写笔划 → 记参数与包围盒（算子与自动更新共用）
    live     —— 自动更新：几何参数一改就重算（防抖 timer）
    props    —— ``scene.gen_lsystem`` 参数组（含规则集合）
    ops      —— 生成 / 删除 / 回读参数 / 场景预设 / 应用预设 / 规则增删
    ui       —— ``View3D > 侧栏 > L-System`` 面板
    palette  —— 项目色板（逐字副本）
    viewport —— 视口 Solid 着色检查（逐字副本）

取舍与实测记录见本目录 ``README.md``；参考实现是 ``.ref/lsystem``（krljg/lsystem），
符号语义与之对齐，重写引擎按模块 / 参数个数精确匹配（见 ``lsystem.py`` 的说明）。
"""

from __future__ import annotations

from .lsystem import (
    DEFAULT_MAX_SYMBOLS,
    MAX_ITERATIONS,
    LSystemError,
    Rule,
    Token,
    evaluate,
    iterate,
    parse_rule,
    parse_tokens,
)
from .presets import DEFAULT_PRESET, PRESETS, Preset, by_key, default
from .turtle import DEFAULT_ANGLE, Path, Skeleton, build, interpret

__all__ = [
    "DEFAULT_ANGLE",
    "DEFAULT_MAX_SYMBOLS",
    "DEFAULT_PRESET",
    "MAX_ITERATIONS",
    "PRESETS",
    "LSystemError",
    "Path",
    "Preset",
    "Rule",
    "Skeleton",
    "Token",
    "build",
    "by_key",
    "default",
    "evaluate",
    "interpret",
    "iterate",
    "parse_rule",
    "parse_tokens",
]


def _classes():
    """要注册的 RNA 类（惰性导入，顺序即注册顺序）。"""
    from . import ops, props, ui

    return (
        props.GenLSystemRule,
        props.GenLSystemSettings,
        ops.GENLS_OT_generate,
        ops.GENLS_OT_delete,
        ops.GENLS_OT_sync_from_object,
        ops.GENLS_OT_setup_scene,
        ops.GENLS_OT_apply_preset,
        ops.GENLS_OT_rule_add,
        ops.GENLS_OT_rule_remove,
        ui.GENLS_UL_rules,
        ui.GENLS_PT_panel,
    )


def _seed_default_preset():
    """给还没有规则的场景铺一套默认预设（一次性 timer，跑完自己摘掉）。

    只在「面板上一条规则都没有」时动手：用户自己清空规则后重算，不该被它又填回来 ——
    不过那种情况下 ``builder.rebuild`` 的兜底同样会套一遍，两者口径一致。
    """
    import bpy

    from . import presets

    for scene in bpy.data.scenes:
        settings = getattr(scene, "gen_lsystem", None)
        if settings is not None and not len(settings.rules):
            presets.apply_to(settings, presets.default())
            settings.dirty = True


def register():
    import bpy

    from . import live, props

    for cls in _classes():
        bpy.utils.register_class(cls)
    # 指针必须在 GenLSystemSettings 注册之后挂到 Scene 上
    props.register()
    live.install()

    # 第一次启用时给各场景铺一套默认预设：面板一打开就有规则可看，而不是空列表。
    # 不能在 register 里直接做 —— 启用期间 ``bpy.data`` 是 ``_RestrictData``，取不到场景；
    # 排一次 timer，等启动 / 启用的那一拍过去再铺（后台没事件循环也没关系：
    # 「生成」时 :func:`builder.rebuild` 还会兜底套一次）。
    try:
        bpy.app.timers.register(_seed_default_preset, first_interval=0.0)
    except (AttributeError, ValueError):  # pragma: no cover - 极早期启用
        pass


def unregister():
    import bpy

    from . import live, props

    # 先摘掉 handler 与待跑的 timer：它们摸的是 RNA 类，类注销后就失效了
    live.remove()
    try:
        if bpy.app.timers.is_registered(_seed_default_preset):
            bpy.app.timers.unregister(_seed_default_preset)
    except (TypeError, ValueError):
        pass
    props.unregister()
    for cls in reversed(_classes()):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
