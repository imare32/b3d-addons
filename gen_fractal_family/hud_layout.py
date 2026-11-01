"""HUD 的**纯布局层**：把"当前基因 + 交互状态"算成一张图元表 + 一张命中区表。

这一层不 import bpy（也不碰 gpu / blf），只依赖 :mod:`palette` 与 :mod:`lattice`，
因此可以在系统 Python 里直接跑，把图元画成 PNG 检查排版（本插件开发时就是这么验的）；
命中测试也只是一堆圆 / 矩形的包含判断，modal 算子拿到的坐标直接喂进来即可。

由它产出的两张表：

``primitives``
    绘制指令，``kind`` 是 ``rect`` / ``poly`` / ``line`` / ``circle`` / ``text`` 之一。
    颜色一律是 **sRGB 0..1 四元组**，由 :mod:`gen_fractal_family.hud` 直接交给 ``gpu`` / ``blf``。

``hotspots``
    命中区，``id`` 形如 ``node:3`` / ``flag:2:mirror`` / ``btn:level:+``。``hit()`` 按
    **后画的在上**（逆序遍历）返回第一个命中的。

坐标系：区域左下角为原点、单位是像素（与 ``SpaceView3D`` 的 ``POST_PIXEL`` 一致）。
面板默认贴在视图**左下角**，可拖动；位置与缩放由调用方（``hud``）记住。

**颜色一律不透明**（alpha = 1.0，模拟半透明的地方用 :func:`mix` 预先混好）。原因见
:mod:`gen_fractal_family.hud` 里 ``_Canvas._flush`` 的注释：``blf`` 会把 GPU 的混合状态
清掉，低 alpha 的填充会变成"直写"——浅色方块直接渲染成实心黑。
"""

from __future__ import annotations

import math

from .lattice import ComplexInteger, FractalGenerator
from .palette import PALETTE, hex_to_rgb

__all__ = [
    "BADGE_SIZE",
    "FOOTER_HEIGHT",
    "GRID_SIZE",
    "HEADER_HEIGHT",
    "HINT_HEIGHT",
    "NODE_HIT_RADIUS",
    "NODE_RADIUS",
    "PANEL_WIDTH",
    "THEME",
    "auto_zoom",
    "build",
    "hit",
    "lattice_basis",
    "lattice_to_screen",
    "mix",
    "panel_height",
    "screen_to_lattice",
    "snap",
]

# --- 尺寸（像素） -----------------------------------------------------------

#: 面板宽度。
PANEL_WIDTH = 300.0
#: 面板内边距（网格区左右各留一份，所以左右留白一样宽）。
PADDING = 8.0
#: 网格区：正方形，边长 = 面板宽 - 左右内边距。
GRID_SIZE = PANEL_WIDTH - 2 * PADDING
#: 标题栏高度。
HEADER_HEIGHT = 26.0
#: 底部参数行高度。
FOOTER_HEIGHT = 26.0
#: 底部提示行高度。
HINT_HEIGHT = 18.0
#: 节点圆半径（可编辑的节点画粗一点，和虚线的分形区分开）。
NODE_RADIUS = 6.5
#: 命中半径（比画出来的圆大一圈，方便点）。
NODE_HIT_RADIUS = 11.5
#: 可编辑折线的线宽。
CURVE_WIDTH = 3.2
#: 段上的 R / M 小方块边长。
BADGE_SIZE = 12.0
#: 圆角半径。
CORNER = 6.0
#: 网格区与参数行之间的缝。
GAP = 2.0
#: 自动取景时每个格点最少 / 最多的像素数。
MIN_ZOOM = 6.0
MAX_ZOOM = 90.0
#: HUD 网格里画的分形级数（可编辑的折线是第 1 级，这里是它再展开两级）。
FRACTAL_LEVEL = 3
#: 分形虚线超过这个点数就不画了（大基因的第 3 级会爆）。
FRACTAL_MAX_POINTS = 4000
#: 虚线的实线段 / 空隙长度（像素）。
DASH_ON = 5.0
DASH_OFF = 3.5
#: 虚线一次最多画多少段（防止极端基因画上千条图元）。
DASH_MAX = 900


# --- 配色 -------------------------------------------------------------------


def _rgb(name: str):
    """色板常量 → sRGB 三元组。"""
    return hex_to_rgb(PALETTE[name])


def mix(base, tint, amount: float):
    """把 ``tint`` 按 ``amount`` 混进 ``base``，返回**不透明**四元组。

    半透明在前景绘制里不可靠（见模块文档），所以"浅一点的墨"这种效果干脆在
    这里按 sRGB 直接混出来，颜色也不依赖底下的画面。
    """
    amount = max(0.0, min(1.0, float(amount)))
    return tuple(base[index] + (tint[index] - base[index]) * amount for index in range(3)) + (1.0,)


_SILK = _rgb("SILK")
_INK = _rgb("INK")
_MOSS = _rgb("MOSS")
_AZURITE = _rgb("AZURITE")
_CINNABAR = _rgb("CINNABAR")
_AMBER = _rgb("AMBER")
_OCHRE = _rgb("OCHRE")
_WHITE = (1.0, 1.0, 1.0)

#: 网格区底色（比面板再亮一档，虚线和格线在上面才看得清）。
GRID_BG = mix(_SILK, _WHITE, 0.55)

#: 配色（sRGB，全部不透明）。
THEME = {
    "panel": _SILK + (1.0,),
    "border": mix(_SILK, _INK, 0.45),
    "header": _AZURITE + (1.0,),
    "header_text": _SILK + (1.0,),
    "header_hover": mix(_AZURITE, _WHITE, 0.24),
    "text": _INK + (1.0,),
    "text_dim": mix(_SILK, _INK, 0.62),
    "grid_bg": GRID_BG,
    "grid_dot": mix(GRID_BG, _MOSS, 0.30),
    "grid_axis": mix(GRID_BG, _MOSS, 0.65),
    # 分形虚线：比可编辑折线淡一档的墨（同色系，不抢主线）
    "fractal": mix(GRID_BG, _AZURITE, 0.42),
    "curve": _CINNABAR + (1.0,),
    "curve_halo": mix(GRID_BG, _SILK, 0.85),
    "node": _SILK + (1.0,),
    "node_ring": _INK + (1.0,),
    "node_hover": _AMBER + (1.0,),
    "badge_on": _CINNABAR + (1.0,),
    "badge_off": mix(_SILK, _INK, 0.13),
    "badge_text_on": _SILK + (1.0,),
    "badge_text_off": mix(_SILK, _INK, 0.72),
    "button": mix(_SILK, _INK, 0.10),
    "button_hover": _AMBER + (1.0,),
    "button_text": _INK + (1.0,),
    "active": _AZURITE + (1.0,),
    "error": _CINNABAR + (1.0,),
    "warn": mix(GRID_BG, _OCHRE, 0.95),
}


def panel_height() -> float:
    """面板总高（标题 + 网格 + 参数行 + 提示行 + 两圈内边距）。"""
    return HEADER_HEIGHT + GRID_SIZE + FOOTER_HEIGHT + HINT_HEIGHT + PADDING + GAP


#: HUD 上的文案（中英两套）。``state["lang"]`` 选哪一套。
#:
#: 为什么要两套：HUD 用 ``blf`` 直接画字，字体得自己找 —— 找不到中文字体时
#: （极少见，但非 Windows 平台有可能）退回英文，免得画一屏豆腐块。面板（RNA UI）
#: 不走这条路，永远是中文。
LABELS = {
    "title": {"zh": "分形基因", "en": "Fractal Gene"},
    "level": {"zh": "等级", "en": "Level"},
    "add": {"zh": "加段", "en": "+ Seg"},
    "remove": {"zh": "删段", "en": "- Seg"},
    "fit": {"zh": "适应", "en": "Fit"},
    "hint": {
        "zh": "拖节点 · R/M 切标记 · 滚轮换层 · 面板内 Tab/Esc",
        "en": "Drag · R/M flags · wheel level · Tab/Esc on panel",
    },
}


def label(state, key: str) -> str:
    """按 ``state["lang"]`` 取一条文案（缺省中文）。"""
    entry = LABELS.get(key, {})
    lang = state.get("lang") or "zh"
    return entry.get(lang) or entry.get("zh") or key


# --- 图元构造 ---------------------------------------------------------------


def _rect(x, y, w, h, colour, radius=0.0):
    """矩形图元；``radius > 0`` 时是圆角矩形（hud 侧按 ``poly`` 画）。"""
    return {"kind": "rect", "x": x, "y": y, "w": w, "h": h, "colour": colour, "radius": radius}


def _line(points, width, colour):
    return {"kind": "line", "points": list(points), "width": width, "colour": colour}


def _circle(x, y, r, colour, filled=True, width=1.6):
    return {"kind": "circle", "x": x, "y": y, "r": r, "colour": colour,
            "filled": filled, "width": width}


def _poly(points, colour):
    return {"kind": "poly", "points": list(points), "colour": colour}


def _text(x, y, text, size, colour, align="LEFT"):
    return {"kind": "text", "x": x, "y": y, "text": text, "size": size, "colour": colour, "align": align}


def _rounded_top_points(x, y, w, h, radius, segments=5):
    """上两角圆、下两角方的矩形轮廓（标题栏用：它贴在面板顶上）。"""
    radius = min(radius, h, w / 2.0)
    points = [(x, y), (x + w, y), (x + w, y + h - radius)]
    for step in range(1, segments + 1):
        angle = (math.pi / 2.0) * step / segments
        points.append((x + w - radius + math.cos(angle) * radius,
                       y + h - radius + math.sin(angle) * radius))
    for step in range(1, segments + 1):
        angle = math.pi / 2.0 + (math.pi / 2.0) * step / segments
        points.append((x + radius + math.cos(angle) * radius,
                       y + h - radius + math.sin(angle) * radius))
    points.append((x, y + h - radius))
    return points


def _clip_line(primitives, start, end, rect, width, colour):
    """把一条线段按矩形裁剪（Liang–Barsky），只留下区域内的部分。

    四条边界各给一组 ``(p, q)``：``p`` 是方向在法向上的分量、``q`` 是起点到边界的距离；
    ``p == 0`` 表示平行于该边界（此时 ``q < 0`` 整条都在外面，直接丢掉）。
    """
    rx, ry, rw, rh = rect
    x0, y0 = start
    x1, y1 = end
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in (
        (-dx, x0 - rx),
        (dx, rx + rw - x0),
        (-dy, y0 - ry),
        (dy, ry + rh - y0),
    ):
        if abs(p) < 1e-9:
            if q < 0:
                return
            continue
        ratio = q / p
        if p < 0:
            if ratio > t1:
                return
            t0 = max(t0, ratio)
        else:
            if ratio < t0:
                return
            t1 = min(t1, ratio)
    if t1 - t0 <= 1e-9:
        return
    primitives.append(_line(
        [(x0 + dx * t0, y0 + dy * t0), (x0 + dx * t1, y0 + dy * t1)], width, colour
    ))


def _dash_polyline(primitives, points, rect, colour, width=1.0):
    """把折线画成虚线（按像素长度切），并且按网格区裁剪。"""
    drawing = True
    remain = DASH_ON
    count = 0
    for index in range(len(points) - 1):
        x0, y0 = points[index]
        x1, y1 = points[index + 1]
        length = math.hypot(x1 - x0, y1 - y0)
        if length < 1e-6:
            continue
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        pos = 0.0
        while pos < length:
            step = min(remain, length - pos)
            if drawing:
                start = (x0 + ux * pos, y0 + uy * pos)
                end = (x0 + ux * (pos + step), y0 + uy * (pos + step))
                _clip_line(primitives, start, end, rect, width, colour)
                count += 1
                if count >= DASH_MAX:
                    return
            pos += step
            remain -= step
            if remain <= 1e-6:
                drawing = not drawing
                remain = DASH_ON if drawing else DASH_OFF


def _ring_hotspot(x, y, radius, ident, kind, payload=None):
    hotspot = {"id": ident, "kind": kind, "x": x, "y": y, "r": radius}
    if payload:
        hotspot.update(payload)
    return hotspot


def _rect_hotspot(x, y, w, h, ident, kind, payload=None):
    hotspot = {"id": ident, "kind": kind, "x": x, "y": y, "w": w, "h": h}
    if payload:
        hotspot.update(payload)
    return hotspot


# --- 点阵换算 ---------------------------------------------------------------


def lattice_basis(domain: str):
    """点阵基矢（与 :func:`gen_fractal_family.lattice.lattice_basis` 同值，这里不引 numpy）。"""
    if domain == "E":
        return (1.0, 0.0), (-0.5, math.sqrt(3.0) / 2.0)
    return (1.0, 0.0), (0.0, 1.0)


def lattice_to_screen(domain, a, b, origin_x, origin_y, zoom):
    """格点 ``(a, b)`` → 屏幕像素坐标（``origin`` 是平面原点在屏幕上的位置）。"""
    (ax, ay), (bx, by) = lattice_basis(domain)
    return origin_x + (a * ax + b * bx) * zoom, origin_y + (a * ay + b * by) * zoom


def screen_to_lattice(domain, x, y, origin_x, origin_y, zoom):
    """屏幕像素 → 格点（**不取整**；取整交给 :func:`snap`）。"""
    dx = (x - origin_x) / max(zoom, 1e-6)
    dy = (y - origin_y) / max(zoom, 1e-6)
    if domain == "E":
        b = dy / (math.sqrt(3.0) / 2.0)
        return dx + b * 0.5, b
    return dx, dy


def snap(domain, x, y, origin_x, origin_y, zoom):
    """屏幕像素 → 最近的整数格点。"""
    a, b = screen_to_lattice(domain, x, y, origin_x, origin_y, zoom)
    return int(round(a)), int(round(b))


def auto_zoom(domain, nodes, available_w, available_h):
    """按节点包围盒算一个刚好装得下的缩放（像素 / 格点）。"""
    if not nodes:
        return 24.0
    xs, ys = [], []
    (ax, ay), (bx, by) = lattice_basis(domain)
    for a, b in nodes:
        xs.append(a * ax + b * bx)
        ys.append(a * ay + b * by)
    span_x = max(max(xs) - min(xs), 1.0)
    span_y = max(max(ys) - min(ys), 1.0)
    zoom = min(available_w / (span_x + 1.6), available_h / (span_y + 1.6))
    return max(MIN_ZOOM, min(MAX_ZOOM, zoom))


# --- 分形虚线（第 3 级） -----------------------------------------------------

#: 分形点列缓存：``key → [(x, y), ...]``（平面坐标，已补上原点）。
_FRACTAL_CACHE: dict = {}


def _fractal_path(elements, domain: str, level: int):
    """基因第 ``level`` 级的点列（**平面坐标**，不是整数格点）。

    和 :meth:`lattice.FractalGenerator.level_points` 是同一套算法，只是换成元组方便画；
    带一层缓存，拖节点时每帧重算也不心疼（第 3 级通常几十到几百个点）。
    """
    key = (domain, level, tuple(
        (int(element["a"]), int(element["b"]), bool(element["reverse"]), bool(element["mirror"]))
        for element in elements
    ))
    cached = _FRACTAL_CACHE.get(key)
    if cached is not None:
        return cached
    path = []
    try:
        items = [
            (ComplexInteger(int(element["a"]), int(element["b"]), domain),
             (bool(element["reverse"]), bool(element["mirror"])))
            for element in elements
        ]
        generator = FractalGenerator(items)
        if generator.is_valid and generator.estimate_points(level) <= FRACTAL_MAX_POINTS:
            points = generator.level_points(level)[level]
            path = [(0.0, 0.0)] + [(float(point[0]), float(point[1])) for point in points]
    except (ValueError, IndexError):
        path = []
    if len(_FRACTAL_CACHE) > 64:
        _FRACTAL_CACHE.clear()
    _FRACTAL_CACHE[key] = path
    return path


# --- 主入口 -----------------------------------------------------------------


def build(state) -> dict:
    """把状态算成 ``{"panel", "grid", "primitives", "hotspots", "zoom", "lattice_offset"}``。

    ``state`` 的字段（都可缺省，缺省值见下面的 ``get``）：

    ==================  ==========================================================
    ``elements``        ``[{"a","b","reverse","mirror"}, ...]``
    ``domain``          ``"G"`` / ``"E"``
    ``level``           预览等级
    ``origin``          面板左下角在区域里的像素坐标（**左下有原点**，y 向上）
    ``zoom``            ``None`` = 按内容自动取景
    ``lattice_offset``  平面原点相对网格区左下角的偏移；``None`` = 按内容居中
                        （用**相对量**存，面板一搬格线才跟着搬）
    ``hover``           当前悬停的 hotspot id
    ``drag``            正在拖的 hotspot id
    ``family``          家族名（标题右侧）
    ``note``            出错提示（底部红字），空则显示操作提示
    ==================  ==========================================================
    """
    elements = state.get("elements") or []
    domain = state.get("domain", "G")
    level = int(state.get("level", 4))
    origin_x, origin_y = state.get("origin") or (24.0, 24.0)
    hover = state.get("hover") or ""
    note = state.get("note") or ""
    theme = THEME

    width = PANEL_WIDTH
    height = panel_height()
    primitives = []
    hotspots = []

    # 面板底色与标题栏（标题栏只在上两角圆，才不会从面板圆角里探出来）
    primitives.append(_rect(origin_x, origin_y, width, height, theme["panel"], CORNER))
    primitives.append(_poly(
        _rounded_top_points(origin_x, origin_y + height - HEADER_HEIGHT, width, HEADER_HEIGHT, CORNER),
        theme["header"],
    ))

    # 标题栏文字
    family = state.get("family") or ""
    title = f"{label(state, 'title')} · {family}" if family else label(state, "title")
    primitives.append(_text(origin_x + 10, origin_y + height - HEADER_HEIGHT + 8,
                            title, 11.5, theme["header_text"]))
    hotspots.append(_rect_hotspot(origin_x, origin_y + height - HEADER_HEIGHT,
                                  width, HEADER_HEIGHT, "bar", "bar"))

    # 标题栏右侧：域切换 + 关闭
    button_size = HEADER_HEIGHT - 8
    close_x = origin_x + width - button_size - 6
    close_y = origin_y + height - HEADER_HEIGHT + 4
    _window_button(primitives, hotspots, close_x, close_y, button_size, "×",
                   "btn:close", hover, theme)

    domain_x = close_x - button_size - 4
    for value in ("E", "G"):
        ident = f"btn:domain:{value}"
        active = domain == value
        # 选中的域用浅色底 + 深字（在深色标题栏上最清楚）；未选中的只留字，悬停才给底色
        if hover == ident and not active:
            primitives.append(_rect(domain_x, close_y, button_size, button_size,
                                    theme["header_hover"], 3.0))
        if active:
            primitives.append(_rect(domain_x, close_y, button_size, button_size,
                                    theme["panel"], 3.0))
        primitives.append(_text(domain_x + button_size / 2, close_y + 3.4, value, 11.5,
                                theme["header"] if active else theme["header_text"], "CENTER"))
        hotspots.append(_rect_hotspot(domain_x, close_y, button_size, button_size, ident, "button"))
        domain_x -= button_size + 3

    # 网格区
    grid_x = origin_x + PADDING
    grid_y = origin_y + FOOTER_HEIGHT + HINT_HEIGHT + GAP
    grid_w = grid_h = GRID_SIZE
    grid_rect = (grid_x, grid_y, grid_w, grid_h)
    primitives.append(_rect(grid_x, grid_y, grid_w, grid_h, theme["grid_bg"], 4.0))

    nodes = [(0, 0)]
    running_a = running_b = 0
    for element in elements:
        running_a += int(element["a"])
        running_b += int(element["b"])
        nodes.append((running_a, running_b))

    # 第 3 级分形的点列（平面坐标；画成虚线，给"这个基因会长成什么"一个直观提示）
    fractal = _fractal_path(elements, domain, FRACTAL_LEVEL) if elements else []

    # 取景：能连虚线的分形一起装下最好；装不下（缩放掉太低）就只装基因本身
    zoom = state.get("zoom")
    if not zoom:
        zoom = auto_zoom(domain, nodes, grid_w - 18.0, grid_h - 18.0)
        if fractal:
            zoom = _fit_with_fractal(domain, nodes, fractal, zoom, grid_w - 18.0, grid_h - 18.0)

    # 平面原点的屏幕位置：会话里存**相对网格区的偏移**，面板搬走时格线跟着搬
    offset = state.get("lattice_offset")
    (ax, ay), (bx, by) = lattice_basis(domain)
    if offset:
        lattice_origin = (grid_x + offset[0], grid_y + offset[1])
    else:
        xs = [a * ax + b * bx for a, b in nodes]
        ys = [a * ay + b * by for a, b in nodes]
        if fractal:
            xs += [point[0] for point in fractal]
            ys += [point[1] for point in fractal]
        mid_x = (max(xs) + min(xs)) / 2.0 if xs else 0.0
        mid_y = (max(ys) + min(ys)) / 2.0 if ys else 0.0
        lattice_origin = (
            grid_x + grid_w / 2.0 - mid_x * zoom,
            grid_y + grid_h / 2.0 - mid_y * zoom,
        )
    lattice_offset = (lattice_origin[0] - grid_x, lattice_origin[1] - grid_y)

    def to_screen(a, b):
        return lattice_to_screen(domain, a, b, lattice_origin[0], lattice_origin[1], zoom)

    def plane_to_screen(x, y):
        return lattice_origin[0] + x * zoom, lattice_origin[1] + y * zoom

    # 点阵背景：两个基矢方向的整数线 + 交点上的点（一根一根画，超出网格区就剪掉）
    reach_a = int(grid_w / zoom) + 2
    reach_b = int(grid_h / zoom) + 2
    for step in range(-reach_b, reach_b + 1):
        _clip_line(primitives, to_screen(-reach_a, step), to_screen(reach_a, step),
                   grid_rect, 1.0, theme["grid_dot"])
    for step in range(-reach_a, reach_a + 1):
        _clip_line(primitives, to_screen(step, -reach_b), to_screen(step, reach_b),
                   grid_rect, 1.0, theme["grid_dot"])
    for a in range(-reach_a, reach_a + 1):
        for b in range(-reach_b, reach_b + 1):
            x, y = to_screen(a, b)
            if grid_x <= x <= grid_x + grid_w and grid_y <= y <= grid_y + grid_h:
                radius = 1.9 if (a, b) == (0, 0) else 1.2
                colour = theme["grid_axis"] if (a, b) == (0, 0) else theme["grid_dot"]
                primitives.append(_circle(x, y, radius, colour))

    # 第 3 级分形：细虚线（在可编辑折线下面，不抢主线）
    if len(fractal) > 1:
        _dash_polyline(primitives, [plane_to_screen(x, y) for x, y in fractal],
                       grid_rect, theme["fractal"], 1.2)

    # 可编辑的基因折线：先用底色描一道粗"衬线"，再压上主线，压在虚线上也读得清
    path = [to_screen(a, b) for a, b in nodes]
    if len(path) > 1:
        primitives.append(_line(path, CURVE_WIDTH + 3.0, theme["curve_halo"]))
        primitives.append(_line(path, CURVE_WIDTH, theme["curve"]))

    # 段上的 R / M 按钮（画在节点之下，命中优先级也低于节点）
    for index in range(1, len(nodes)):
        x0, y0 = path[index - 1]
        x1, y1 = path[index]
        _segment_badges(
            primitives, hotspots, elements[index - 1], index - 1, x0, y0, x1, y1, hover, theme
        )

    # 节点（最后画 = 在最上层；命中区也最后追加 = 优先命中）
    for index in range(len(nodes)):
        x, y = path[index]
        hovered = hover == f"node:{index}"
        radius = NODE_RADIUS + (1.5 if hovered or f"node:{index}" == state.get("drag") else 0.0)
        primitives.append(_circle(x, y, radius, theme["node_hover"] if hovered else theme["node"],
                                  filled=True))
        primitives.append(_circle(x, y, radius, theme["node_ring"], filled=False))
        if index > 0:
            hotspots.append(_ring_hotspot(x, y, NODE_HIT_RADIUS, f"node:{index}", "node",
                                          {"index": index}))

    # 底部：等级 + 结构按钮（等级用 ‹ ›，免得和"加段 / 删段"的加减号撞脸）
    footer_y = origin_y + HINT_HEIGHT
    primitives.append(_text(grid_x, footer_y + 6, label(state, "level"), 10.5, theme["text"]))
    primitives.append(_text(grid_x + 62, footer_y + 6, str(level), 11.5, theme["text"], "CENTER"))
    _small_button(primitives, hotspots, grid_x + 34, footer_y, 22, "‹", "btn:level:-", hover, theme)
    _small_button(primitives, hotspots, grid_x + 78, footer_y, 22, "›", "btn:level:+", hover, theme)

    _small_button(primitives, hotspots, grid_x + 108, footer_y, 48, label(state, "add"),
                  "btn:add", hover, theme)
    _small_button(primitives, hotspots, grid_x + 160, footer_y, 48, label(state, "remove"),
                  "btn:remove", hover, theme)
    _small_button(primitives, hotspots, grid_x + 212, footer_y, 48, label(state, "fit"),
                  "btn:fit", hover, theme)

    # 底部提示行：出错 > 非收缩警告 > 操作提示
    hint_y = origin_y + 4
    contraction = float(state.get("contraction") or 0.0)
    if note:
        primitives.append(_text(grid_x, hint_y + 3, note, 10.0, theme["error"]))
    elif contraction > 1.0001:
        primitives.append(_text(grid_x, hint_y + 3,
                                f"发散：每级放大 {contraction:.2f}×", 9.5, theme["warn"]))
    elif contraction >= 0.9999:
        primitives.append(_text(grid_x, hint_y + 3,
                                "非收缩：等级越高范围越大", 9.5, theme["warn"]))
    else:
        primitives.append(_text(grid_x, hint_y + 3, label(state, "hint"), 9.5, theme["text_dim"]))

    return {
        "panel": (origin_x, origin_y, width, height),
        "grid": grid_rect,
        "lattice_origin": lattice_origin,
        "lattice_offset": lattice_offset,
        "zoom": zoom,
        "primitives": primitives,
        "hotspots": hotspots,
    }


def _fit_with_fractal(domain, nodes, fractal, gene_zoom, available_w, available_h):
    """把第 3 级分形也算进取景范围；算出来太挤（比基因本身还小很多）就退回基因取景。"""
    (ax, ay), (bx, by) = lattice_basis(domain)
    xs = [a * ax + b * bx for a, b in nodes] + [point[0] for point in fractal]
    ys = [a * ay + b * by for a, b in nodes] + [point[1] for point in fractal]
    span_x = max(max(xs) - min(xs), 1.0)
    span_y = max(max(ys) - min(ys), 1.0)
    zoom = min(available_w / (span_x + 1.6), available_h / (span_y + 1.6))
    if zoom < MIN_ZOOM * 2.0 or zoom < gene_zoom * 0.15:
        return gene_zoom
    return max(MIN_ZOOM, min(MAX_ZOOM, zoom))


def _segment_badges(primitives, hotspots, element, index, x0, y0, x1, y1, hover, theme):
    """一段的 R / M 两个小按钮（画在段中点偏法向一侧）。"""
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    mid_x, mid_y = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    offset = BADGE_SIZE * 0.5 + 13.0
    spread = BADGE_SIZE * 0.5 + 1.0
    for flag, text in (("reverse", "R"), ("mirror", "M")):
        sign = -1.0 if flag == "reverse" else 1.0
        cx = mid_x + px * offset + ux * spread * sign
        cy = mid_y + py * offset + uy * spread * sign
        ident = f"flag:{index}:{flag}"
        active = bool(element[flag])
        colour = theme["badge_on"] if active else theme["badge_off"]
        if hover == ident:
            colour = theme["button_hover"]
        half = BADGE_SIZE / 2.0
        primitives.append(_rect(cx - half, cy - half, BADGE_SIZE, BADGE_SIZE, colour, 3.0))
        primitives.append(_text(cx, cy - 3.6, text, 10.0,
                                theme["badge_text_on"] if active else theme["badge_text_off"],
                                "CENTER"))
        hotspots.append(_rect_hotspot(cx - half, cy - half, BADGE_SIZE, BADGE_SIZE, ident, "flag",
                                      {"index": index, "flag": flag}))


def _small_button(primitives, hotspots, x, y, w, text, ident, hover, theme):
    """底部朴素按钮（底色是不透明的浅墨，文字是深墨 —— 不管底下是什么都读得清）。"""
    height = FOOTER_HEIGHT - 4
    colour = theme["button_hover"] if hover == ident else theme["button"]
    primitives.append(_rect(x, y, w, height, colour, 4.0))
    primitives.append(_text(x + w / 2, y + 5.6, text, 10.5, theme["button_text"], "CENTER"))
    hotspots.append(_rect_hotspot(x, y, w, height, ident, "button"))


def _window_button(primitives, hotspots, x, y, size, text, ident, hover, theme):
    """标题栏上的方形按钮：平时只有字，悬停才铺底（深底 + 浅字，怎么都是亮的）。"""
    if hover == ident:
        primitives.append(_rect(x, y, size, size, theme["header_hover"], 3.0))
    primitives.append(_text(x + size / 2, y + 3.5, text, 12.0, theme["header_text"], "CENTER"))
    hotspots.append(_rect_hotspot(x, y, size, size, ident, "button"))


# --- 命中测试 ---------------------------------------------------------------


def hit(hotspots, x, y):
    """返回 ``(x, y)`` 命中的 hotspot；没命中返回 ``None``。

    后画的在上（节点 / 按钮是在折线之后追加的），所以从后往前找。
    """
    for hotspot in reversed(hotspots):
        if "r" in hotspot:
            dx = x - hotspot["x"]
            dy = y - hotspot["y"]
            if dx * dx + dy * dy <= hotspot["r"] * hotspot["r"]:
                return hotspot
        else:
            if (hotspot["x"] <= x <= hotspot["x"] + hotspot["w"]
                    and hotspot["y"] <= y <= hotspot["y"] + hotspot["h"]):
                return hotspot
    return None
