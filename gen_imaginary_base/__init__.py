"""Fractal Base：二次整数环上的**进制展开**，画成自仿射点集。

一个**数系**由底数 ``b`` 与数字集 ``D`` 决定，整数 ``n`` 写成 ``n = Σ d_k·b^k``。取遍
``|D|^N`` 个地址，把每个整数画在它自己的位置上，得到的就是那片自相似的点集 —— 底数是
负的、纯虚的、甚至带 ``ω`` 的，形状都会跟着变。

分层（数学层永不 import ``bpy``）::

    ring        -- 环算术（Z[i] / Z[ω]）、进制展开、完全剩余系判定、点集
    presets     -- 从参考项目移植的 43 套社区数系
    palette     -- 色板与 sRGB → 线性

Blender 层（只在 :func:`register` 里导入）::

    props       -- ``scene.imaginarybase`` 设置、点集缓存
    preview     -- 视口预览，``POST_VIEW`` 立即模式（不建数据块）
    hud         -- 视口 HUD，``POST_PIXEL`` 面板
    hud_layout  -- HUD 的图元与命中区（纯函数）
    build       -- 写 Grease Pencil（一个整数一条单顶点笔划）
    ops         -- 生成 / 取景 / 加删数字 / **HUD 模态**
    ui          -- ``View3D > Sidebar > Fractal Base``
"""

bl_info = {
    "name": "Fractal Base",
    "author": "imare32",
    "description": "Self-affine point sets from imaginary-base numeral systems.",
    "blender": (4, 2, 0),
    "version": (0, 1, 0),
    "location": "3D Viewport > Sidebar > Fractal Base",
    "warning": "",
    "category": "Generic",
}

#: 英文 msgid → 简体中文。``*`` 覆盖 RNA 名称 / 描述与 :func:`i18n.t` 过的字符串，
#: ``Operator`` 覆盖 ``bl_label``。
translation_dict = {
    "zh_HANS": {
        # --- 插件 ---------------------------------------------------------
        ("*", "Fractal Base"): "分形进制",
        (
            "*",
            "Self-affine point sets from imaginary-base numeral systems.",
        ): "虚数进制数系生成的自仿射点集。",
        # --- 面板 ---------------------------------------------------------
        ("*", "Numeral System"): "数系",
        ("*", "Places & Colour"): "位数与上色",
        ("*", "Output"): "输出",
        ("*", "Preset"): "预设",
        ("*", "Ring"): "环",
        ("*", "Base Real"): "底数实部",
        ("*", "Base Imaginary"): "底数虚部",
        ("*", "Digits ({count})"): "数字（{count}）",
        ("*", "Reset to 2i Digits"): "重置为 2i 数字",
        ("*", "Places"): "位数",
        ("*", "Places (max {max})"): "位数（最多 {max}）",
        ("*", "Colour By"): "上色依据",
        ("*", "Place"): "位号",
        ("*", "Point Budget"): "点数预算",
        ("*", "Dot Radius"): "点半径",
        ("*", "Generate"): "生成",
        ("*", "Delete Object"): "删除对象",
        ("*", "Frame in Viewport"): "视口取景",
        ("*", "Viewport Preview"): "视口预览",
        ("*", "HUD"): "HUD",
        ("*", "Canonical numeral system"): "有效数系（每个整数唯一表示）",
        ("*", "Settings not available"): "设置不可用",
        ("*", "Add Digit"): "加数字",
        ("*", "Remove Digit"): "删数字",
        # --- 枚举 ---------------------------------------------------------
        ("*", "Aggregate"): "聚合",
        ("*", "Leading Digit"): "最高位",
        ("*", "Custom"): "自定义",
        (
            "*",
            "Classic numeral systems, ported from TheGrayCuber's imaginary_bases",
        ): "经典数系，移植自 TheGrayCuber 的 imaginary_bases",
        # --- 提示 ---------------------------------------------------------
        (
            "*",
            "Nothing to draw: check the base and the digits",
        ): "没有可画的点：检查底数与数字",
        ("*", "Nothing to draw"): "没有可画的点",
        ("*", "Nothing to frame"): "没有可取景的内容",
        ("*", "At least two digits are needed"): "至少要两个数字",
        ("*", "Digit limit reached"): "数字数量已达上限",
        ("*", "The base cannot be zero"): "底数不能为零",
        ("*", "That lattice point is already taken"): "那个格点已经被占了",
        ("*", "The HUD only runs in a 3D viewport"): "HUD 只能在 3D 视图中运行",
        ("*", "Deleted"): "已删除",
        ("*", "No object to delete"): "没有可删除的对象",
        ("*", "Places {places} · {count} points"): "位数 {places} · {count} 点",
        ("*", "The point budget allows {places} places"): "点数预算只够 {places} 位",
        # --- HUD ----------------------------------------------------------
        ("*", "Imaginary Base"): "虚数进制",
        ("*", "+ Digit"): "+ 数字",
        ("*", "- Digit"): "- 数字",
        ("*", "Fit"): "适应",
        (
            "*",
            "Drag a digit · wheel places · Shift+wheel zoom · F fit · Tab ring",
        ): "拖数字 · 滚轮改位数 · Shift+滚轮缩放 · F 适应 · Tab 换环",
    }
}


def _classes():
    """要注册的 RNA 类（惰性导入，顺序即注册顺序）。"""
    from . import ops, props, ui

    return (
        props.DigitItem,
        props.ImaginaryBaseSettings,
        ui.IMAGINARYBASE_UL_digits,
        *ops.classes,
        ui.IMAGINARYBASE_PT_panel,
    )


def register():
    import bpy

    from . import hud, preview, props

    for cls in _classes():
        bpy.utils.register_class(cls)
    # 指针必须在设置组注册之后挂到 Scene 上
    props.register()
    preview.install()
    hud.install()
    bpy.app.translations.register(__name__, translation_dict)
    # 默认值要等有 scene context 才填得进去（那一刻才拿得到 bpy.context.scene）
    bpy.app.timers.register(props.bootstrap_default, first_interval=0.2)


def unregister():
    import bpy

    from . import hud, preview, props

    if bpy.app.timers.is_registered(props.bootstrap_default):
        bpy.app.timers.unregister(props.bootstrap_default)
    try:
        bpy.app.translations.unregister(__name__)
    except (RuntimeError, ValueError):
        pass
    # 先摘句柄：它们摸的是 RNA 类，类注销后就失效了
    hud.remove()
    preview.remove()
    props.unregister()
    for cls in reversed(_classes()):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
