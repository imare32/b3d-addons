"""视口预览：在 3D 视图里直接画分级曲线，**不创建任何临时对象 / 数据块**。

这是相对原插件最主要的改动之一。原版预览的做法是建一个隐藏集合
（``__fractalfamily_preview__``）加三条曲线对象，再往 ``curve.splines`` 里写点：
它会在大纲里留垃圾、会被误选 / 被删 / 被保存进文件，关掉预览还得把集合藏起来。
这里是 ``SpaceView3D.draw_handler_add(..., 'POST_VIEW')`` 的**立即模式绘制** ——
一帧画一次、画完什么都不留，视口里看到的和最后生成的是同一份算法结果。

绘制策略（"洋葱皮"），三个层次一眼分得开：

* **第 0 级**（起点 → 终点的初始线段）：最细最淡，只是一条基准线；
* **第 1 级**（生成器本身，也就是 HUD 里编辑的那条线）：**最粗 + 专属的朱砂色**，
  当参考骨架看 —— 你在 HUD 里改的就是它；
* **第 2 级往上**：逐级变细，颜色取一组彼此隔得很远的固定色（天青 / 翠绿 / 橘黄 / 品红 /
  明黄，见 :data:`DETAIL_COLOURS`），相邻两级的 Lab 色差 ≥ 82（旧那套等差色带只有 25）。
  **颜色只跟级号有关**（见 :func:`level_style`）：第 2 级永远是天青，不会因为
  "现在显示到第几级"而换色；
* 所有线都落在 XY 平面（z = 0），也就是最终生成物所在的平面；
* 3D 视图走色彩管理，所以颜色写进去前转线性。

**画哪几级跟着输出模式走**（见 :func:`preview_indices`）：输出「每一级」/「形态键」时逐级都画
（生成物里每级都在）；输出「最后一级」时只画 **第 1 级骨架 + 最高级** —— 中间级在生成物里
根本不存在，画出来只会误导。

**线宽是写死的整数台阶**（见 :data:`LEVEL_WIDTHS`）：驱动会把 `line_width` 量化
（5.2 实测 1.2 / 1.45 / 1.7 / 1.96 / 2.5 全都画成 2 px，3.4 画成 4 px），小数宽度白费，
所以颜色和宽度都**不留给滑块** —— 第 1 级骨架 6 px，第 2 级往上 5 / 4 / 3 / 2 / 1 px。

缓存：分级点列只在"基因 / 域 / 等级 / 初始曲线"变化时重算 —— 键是
:meth:`props.GenFractalFamilySettings.geometry_signature`。滚动视口、拖面板滑块都命中缓存。
"""

from __future__ import annotations

import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from .lattice import FractalGenerator, teragon
from .palette import CINNABAR, hex_to_rgb, srgb_to_linear

#: 最近一次绘制失败的原因（面板「状态」里显示；成功画一帧就清掉）。
_LAST_ERROR = None


def last_error():
    """最近一次预览绘制失败的原因；没有则返回 ``None``。"""
    return _LAST_ERROR


#: 第 1 级（生成器 = HUD 里编辑的那条线）的专属色。
SKELETON_COLOUR = CINNABAR

#: 第 0 级（基准线）的不透明度。
_ALPHA_INITIATOR = 0.32

#: 第 2 ~ 6 级的固定色（**一对一绑死级号**：``DETAIL_COLOURS[0]`` 是第 2 级）。
#:
#: 这**不是"色带"**，是一组彼此隔得尽量远的独特色：色相 天青 201° / 翠绿 137° /
#: 橘黄 34° / 品红 307° / 明黄 53°，顺序也不是冷→暖 —— 橘黄夹在翠绿和品红之间、
#: 明黄放到最后，就是为了让**每一对相邻级**都拉开距离。
#:
#: 实测（按默认深色视口背景 ``#303030`` 合成后算 CIE76 色差，见 README）：相邻两级
#: 最少差 **82**；旧的那套"石青 198° → 琥珀 36° 等差"只有 **25** —— 第 4/5 级那对
#: 绿和黄绿挤在一起，蓝和绿挨着的那两级也分不出来。**明黄给最细的 1 px 那一级**：
#: 细线最容易被底色吃掉，就给它最亮的一档。
DETAIL_COLOURS = (
    "#4FA8D8",  # 第 2 级  天青
    "#45C96B",  # 第 3 级  翠绿
    "#EE9A2E",  # 第 4 级  橘黄
    "#D46BC8",  # 第 5 级  品红
    "#EFDC55",  # 第 6 级  明黄
)

#: 每一级的固定线宽（**整数像素**），下标就是级号。
#:
#: 驱动会把 ``line_width`` 量化（5.2 实测：0.9 / 1.2 / 1.45 / 1.7 / 1.96 / 2.5 全画成
#: 1～2 px，3.4 → 4 px），所以小数宽度是白费的、也更不该做成滑块。直接写死台阶：
#: 第 0 级 1 px（基准线）、第 1 级骨架最粗 6 px、第 2 级往上逐级 −1 px 直到第 6 级的 1 px。
LEVEL_WIDTHS = {0: 1, 1: 6, 2: 5, 3: 4, 4: 3, 5: 2, 6: 1}


def level_style(index: int):
    """第 ``index`` 级的固定 ``(色值, 不透明度)``。

    **按绝对级号取，不按"当前显示到第几级"**：早期是相对映射（``index / total``），
    拖一下等级，所有已存在的级颜色都会整体搬家 —— 第 2 级在等级 2 时是琥珀、到等级 3
    突然变成深钢蓝，看着就像"这一级的线消失了"。现在整张表写死，级号 → 颜色是死映射。

    第 0 级（基准线）借第 2 级的颜色画得极淡；1 级起步一律不透明 —— 半透明的粗线会把
    压在下面的细线染成浑浊的中间色，那才是"看不清"的主要来源。
    """
    if int(index) <= 0:
        return DETAIL_COLOURS[0], _ALPHA_INITIATOR
    if int(index) == 1:
        return SKELETON_COLOUR, 1.0
    slot = min(int(index) - 2, len(DETAIL_COLOURS) - 1)
    return DETAIL_COLOURS[slot], 1.0


def level_widths(total: int):
    """每一级的线宽（**整数像素**），下标 = 级号，直接查 :data:`LEVEL_WIDTHS`。

    写死台阶而不是按级数摊：预览最多到第 6 级（``props.PREVIEW_MAX_LEVEL``），
    6 个能一眼分清的宽度刚好是 6/5/4/3/2/1，再摊只会让相邻级挤成一样粗。
    """
    return [LEVEL_WIDTHS.get(index, 1) for index in range(max(0, int(total)) + 1)]


def preview_indices(mode: str, count: int):
    """输出模式下该画哪几级（``lines`` 的下标集合）；``None`` = 全画。

    * ``EACH_LEVEL`` / ``SHAPE_KEYS``：生成物逐级都在（每级一个对象 / 每级一个形态键），
      预览就把 ``0..count-1`` 全画出来对照；
    * ``LAST_LEVEL``：生成物只有最后一级一条曲线，预览也只留 **第 1 级（骨架，当参考）**
      加 **最高级** —— 中间级在生成物里根本不存在，画出来只会误导。
    """
    if str(mode) != "LAST_LEVEL":
        return None
    last = max(0, int(count) - 1)
    return {1, last} if last > 1 else {last}


__all__ = [
    "install",
    "line_vertices",
    "lines_for",
    "remove",
    "tag_redraw",
]

#: 预览最多画多少个点（再大就只画到能画的那一级，面板上另有提示）。
POINT_BUDGET = 120_000

#: 句柄：模块变量是主，``driver_namespace`` 只是跨热重载的兜底（见根 README）。
_HANDLE = None
_HANDLE_KEY = "gen_fractal_family_preview_handle"
_HANDLE_REGION = "WINDOW"

#: 点列缓存（键 = :meth:`props.GenFractalFamilySettings.geometry_signature`）。
#: ``closed`` 与 ``lines`` 一起缓存：它只由初始折线决定，重算点列时顺手带出来。
_CACHE = {"key": None, "lines": [], "closed": False}

#: 每次绘制重建的 batch 缓存（键同上；文件加载后 GPU 资源可能失效，出错就清空重来）。
_BATCH = {"key": None, "items": []}


# ---------------------------------------------------------------------------
# 几何：取参数 → 分级折线
# ---------------------------------------------------------------------------


def _initiator(settings):
    """初始折线 ``(points, closed)``；直线时返回 ``(None, False)``。

    来源（直线 / 正多边形 / 自定义曲线）由
    :meth:`props.GenFractalFamilySettings.initiator` 决定，与生成共用同一份。
    """
    return settings.initiator()


def line_vertices(line, closed: bool = False) -> np.ndarray:
    """折线 → ``(n, 3)`` 顶点数组（``z = 0``；闭合时补上首点）。

    闭合的初始折线（正多边形 / 闭合样条）在 :func:`lattice.teragon` 里**已经去掉**了那个
    "回到起点"的重复点 —— 生成物靠 ``spline.use_cyclic_u`` 自己闭合，而预览用的是
    ``LINE_STRIP``：不在这里补一段"末点 → 首点"，画出来就差最后一条边（生成的是闭合的，
    预览却开着一个口子）。
    """
    vertices = np.column_stack([line[:, 0], line[:, 1], np.zeros(len(line))])
    if closed and len(vertices) > 2:
        vertices = np.vstack([vertices, vertices[:1]])
    return vertices


def _cached(settings):
    """``(lines, closed)``：当前参数对应的分级折线与闭合标记（带缓存）。"""
    key = settings.geometry_signature()
    if _CACHE["key"] == key:
        return _CACHE["lines"], _CACHE["closed"]

    lines = []
    closed = False
    if settings.is_valid():
        generator = FractalGenerator(settings.elements())
        initiator, closed = _initiator(settings)
        segments = 1 if initiator is None else max(1, len(initiator) - 1 + int(closed))
        level = settings.preview_level()
        # 点数预算：按公式从最高级往下退，别先算出来再看（9 段的第 6 级就是 53 万点）
        while level > 0 and generator.estimate_points(level, segments) > POINT_BUDGET:
            level -= 1
        try:
            lines = teragon(generator, level, initiator, closed)
        except (ValueError, ZeroDivisionError):
            lines = []
            closed = False
    _CACHE["key"] = key
    _CACHE["lines"] = lines
    _CACHE["closed"] = closed
    _BATCH["key"] = None
    return lines, closed


def lines_for(settings):
    """当前参数对应的分级折线（``[np.ndarray (n, 2), ...]``，带缓存）。"""
    return _cached(settings)[0]


def _batches(settings):
    """``[(batch, rgba, width)]``：每级一个颜色，骨架最粗、细节逐级变细。

    画哪几级**跟着输出模式走**（见 :func:`preview_indices`）：输出「最后一级」时预览也只留
    骨架 + 最高级，免得视口里出现一批生成物里根本不存在的中间级。
    """
    lines, closed = _cached(settings)
    if not lines:
        return []
    ramp = getattr(settings, "preview_ramp", "FAMILY")
    mode = str(getattr(settings, "output_mode", "LAST_LEVEL"))
    # 键里必须带上配色 / 输出模式：它们只影响绘制，不改变几何，改完要重建 batch 才看得到
    key = (
        settings.geometry_signature(),
        str(ramp),
        str(mode),
        tuple(round(float(component), 4) for component in settings.preview_colour),
    )
    if _BATCH["key"] == key:
        return _BATCH["items"]

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    chosen = tuple(float(component) for component in settings.preview_colour)
    total = max(1, len(lines) - 1)
    widths = level_widths(total)               # 整数像素台阶：6 / 5 / 4 / 3 / 2 / 1
    keep = preview_indices(mode, len(lines))
    items = []
    for index, line in enumerate(lines):
        if keep is not None and index not in keep:
            continue
        if len(line) < 2:
            continue
        # 绝对级号 → 固定颜色 + 固定宽度：第 2 级永远是那档天青，不会因为"显示到第几级"而换色
        hex_colour, alpha = level_style(index)
        # 单色模式只换第 2 级往上（骨架仍是朱砂、基准线仍极淡）：粗细还照旧区分
        base = chosen if (ramp == "MONO" and index >= 2) else hex_to_rgb(hex_colour)
        colour = (
            srgb_to_linear(base[0]),
            srgb_to_linear(base[1]),
            srgb_to_linear(base[2]),
            alpha,
        )
        # 闭合的初始折线要补上"末点 → 首点"那一段，否则 LINE_STRIP 画出来是开口的
        vertices = line_vertices(line, closed)
        batch = batch_for_shader(shader, "LINE_STRIP", {"pos": vertices.astype(np.float32)})
        items.append((batch, colour, float(widths[min(index, len(widths) - 1)]), shader))
    _BATCH["key"] = key
    _BATCH["items"] = items
    return items


# ---------------------------------------------------------------------------
# 绘制
# ---------------------------------------------------------------------------


def _settings():
    scene = getattr(bpy.context, "scene", None)
    if scene is None:
        return None
    return getattr(scene, "gen_fractal_family", None)


def _draw():
    """``POST_VIEW`` 回调：每个 3D 视图各调一次，只画线，别在这里做重活。"""
    global _LAST_ERROR
    settings = _settings()
    if settings is None or not settings.show_preview:
        return
    try:
        items = _batches(settings)
        _LAST_ERROR = None
    except Exception as exception:
        # 这里**必须**兜住：draw handler 抛异常会刷一屏报错。但也不能白吞 ——
        # 记住最后一条，面板「状态」里会显示出来（早期就是这样静默失败，
        # 表现为"等级 1/2 时预览什么都不显示"，查了很久）。
        message = f"{type(exception).__name__}: {exception}"
        if message != _LAST_ERROR:  # 每帧都打一遍会刷屏，只在变化时打
            import traceback

            _LAST_ERROR = message
            traceback.print_exc()
        _BATCH["key"] = None
        _CACHE["key"] = None
        return
    if not items:
        return

    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("LESS_EQUAL")
    try:
        for batch, colour, width, shader in items:
            gpu.state.line_width_set(max(1.0, width))
            shader.bind()
            shader.uniform_float("color", colour)
            batch.draw(shader)
    finally:
        gpu.state.line_width_set(1.0)
        gpu.state.blend_set("NONE")
        gpu.state.depth_test_set("LESS_EQUAL")


# ---------------------------------------------------------------------------
# 句柄装卸
# ---------------------------------------------------------------------------


def tag_redraw() -> None:
    """请求所有 3D 视图重绘（参数一变就调，立即模式绘制才会跟上）。"""
    window_manager = getattr(bpy.context, "window_manager", None)
    if window_manager is None:
        return
    for window in window_manager.windows:
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def install() -> None:
    """挂上 ``POST_VIEW`` 句柄（重复调用不会重复挂）。"""
    global _HANDLE
    remove()
    _HANDLE = bpy.types.SpaceView3D.draw_handler_add(_draw, (), _HANDLE_REGION, "POST_VIEW")
    bpy.app.driver_namespace[_HANDLE_KEY] = (_HANDLE, _HANDLE_REGION)


def remove() -> None:
    """摘掉句柄（``unregister`` 必须调，否则重载会残留两份）。"""
    global _HANDLE
    stored = bpy.app.driver_namespace.pop(_HANDLE_KEY, None)
    candidates = [_HANDLE]
    if isinstance(stored, tuple):
        candidates.append(stored[0])
    elif stored is not None:
        candidates.append(stored)
    for handle in candidates:
        if handle is None:
            continue
        try:
            bpy.types.SpaceView3D.draw_handler_remove(handle, _HANDLE_REGION)
        except (ValueError, TypeError):
            pass
    _HANDLE = None
    _BATCH["key"] = None
    _CACHE["key"] = None
