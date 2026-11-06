"""HUD 的**纯布局层**：把「当前数系 + 交互状态」算成图元表 + 命中区表。

和 `fractal_family` 的分工一致：这一层不 import bpy / gpu / blf，只依赖 :mod:`palette`，
所以能在系统 Python 里直接跑（本插件开发时就靠它把图元画成 PNG 看排版）。命中测试也只是
一堆圆 / 矩形的包含判断，modal 算子拿到的鼠标坐标直接喂 :func:`hit` 即可。

坐标：区域左下角为原点、单位像素（``POST_PIXEL`` 与 ``event.mouse_region_x/y`` 同一空间）。

画什么
    标题栏（域切换 / 关闭）、点阵地图、页脚（位数、加减数字、适应）、提示行。
    地图里三样东西按顺序叠放：**点阵** → **点集**（当前数系长出来的形状）→ **可拖的标记**
    （每个数字一个圆、底数一个菱形）。拖动数字就是改数字集，形状当场跟着变。
"""

from __future__ import annotations

import math

from . import palette

__all__ = [
    "HEADER_HEIGHT",
    "HINT_HEIGHT",
    "FOOTER_HEIGHT",
    "GRID_SIZE",
    "MARKER_HIT",
    "MARKER_RADIUS",
    "PANEL_WIDTH",
    "THEME",
    "auto_zoom",
    "build",
    "frame_polygon",
    "hit",
    "inside_panel",
    "lattice_basis",
    "lattice_to_screen",
    "panel_height",
    "screen_to_lattice",
    "snap",
]

PANEL_WIDTH = 300.0
GRID_SIZE = PANEL_WIDTH
HEADER_HEIGHT = 26.0
FOOTER_HEIGHT = 26.0
HINT_HEIGHT = 18.0
GAP = 2.0
CORNER = 6.0
#: 数字 / 底数标记的半径与命中半径。
MARKER_RADIUS = 7.0
MARKER_HIT = 12.0
#: 地图里最多画多少个点集成员（超过就等距抽样）。
MAP_POINTS = 1400
#: 自动取景的缩放上下限（像素 / 格距）。
MIN_ZOOM = 7.0
MAX_ZOOM = 64.0


# ---------------------------------------------------------------------------
# 配色
# ---------------------------------------------------------------------------


def _rgb(value: str):
    return palette.hex_to_rgb(value)


def mix(first, second, amount: float):
    """sRGB 直接混，返回**不透明**四元组。

    前景绘制里半透明不可靠（``blf`` 会清掉混合状态），所以"浅一点的墨"在这里预混好。
    """
    amount = max(0.0, min(1.0, float(amount)))
    return tuple(
        first[index] + (second[index] - first[index]) * amount for index in range(3)
    ) + (1.0,)


_SILK = _rgb(palette.SILK)
_INK = _rgb(palette.INK)
_AZURITE = _rgb(palette.AZURITE)
_CINNABAR = _rgb(palette.CINNABAR)
_AMBER = _rgb(palette.AMBER)
_MOSS = _rgb(palette.MOSS)
_WHITE = (1.0, 1.0, 1.0)

GRID_BG = mix(_SILK, _WHITE, 0.55)

THEME = {
    "panel": _SILK + (1.0,),
    "border": mix(_SILK, _INK, 0.45),
    "header": _AZURITE + (1.0,),
    "header_text": _SILK + (1.0,),
    "header_hover": mix(_AZURITE, _WHITE, 0.24),
    "text": _INK + (1.0,),
    "text_dim": mix(_SILK, _INK, 0.62),
    "grid_bg": GRID_BG,
    "frame": mix(GRID_BG, _INK, 0.55),
    "grid_dot": mix(GRID_BG, _MOSS, 0.28),
    "axis": mix(GRID_BG, _MOSS, 0.70),
    "point": _AZURITE + (1.0,),
    "marker": _SILK + (1.0,),
    "marker_ring": _INK + (1.0,),
    "marker_hover": _AMBER + (1.0,),
    "base": _CINNABAR + (1.0,),
    "button": mix(_SILK, _INK, 0.10),
    "button_hover": _AMBER + (1.0,),
    "button_text": _INK + (1.0,),
    "error": _CINNABAR + (1.0,),
    "warn": mix(GRID_BG, _AMBER, 0.95),
}

#: HUD 文案（英文 msgid，翻译在 :mod:`hud` 里做 —— blf 画的是裸字符串）。
LABELS = {
    "title": "Imaginary Base",
    "places": "Places",
    "add": "+ Digit",
    "remove": "- Digit",
    "fit": "Fit",
    "hint": "Drag a digit · wheel places · Shift+wheel zoom · F fit · Tab ring",
}


def label(state, key: str) -> str:
    text = LABELS.get(key, key)
    translate = state.get("t")
    return translate(text) if translate else text


def panel_height() -> float:
    return HEADER_HEIGHT + GRID_SIZE + FOOTER_HEIGHT + HINT_HEIGHT + GAP


# ---------------------------------------------------------------------------
# 图元
# ---------------------------------------------------------------------------


def _inside_polygon(x, y, points) -> bool:
    """射线法判断点是否在多边形内（凸凹都行，顶点顺序不限）。"""
    inside = False
    count = len(points)
    for index in range(count):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % count]
        if (y1 > y) != (y2 > y):
            cross = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < cross:
                inside = not inside
    return inside


def inside_panel(panel, x, y) -> bool:
    """指针是否落在面板矩形里（``panel`` = ``(x, y, w, h)``，区域坐标，左下为原点）。"""
    if not panel:
        return False
    px, py, pw, ph = panel
    return px <= x <= px + pw and py <= y <= py + ph


def _rect(x, y, w, h, colour, radius=0.0):
    return {
        "kind": "rect",
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "colour": colour,
        "radius": radius,
    }


def _line(points, width, colour):
    return {"kind": "line", "points": list(points), "width": width, "colour": colour}


def _circle(x, y, r, colour, filled=True, width=1.6):
    return {
        "kind": "circle",
        "x": x,
        "y": y,
        "r": r,
        "colour": colour,
        "filled": filled,
        "width": width,
    }


def _poly(points, colour):
    return {"kind": "poly", "points": list(points), "colour": colour}


def _text(x, y, text, size, colour, align="LEFT"):
    return {
        "kind": "text",
        "x": x,
        "y": y,
        "text": text,
        "size": size,
        "colour": colour,
        "align": align,
    }


def _ring_hotspot(x, y, radius, ident, kind, payload=None):
    return {
        "shape": "ring",
        "x": x,
        "y": y,
        "r": radius,
        "id": ident,
        "kind": kind,
        "payload": payload or {},
    }


def _rect_hotspot(x, y, w, h, ident, kind, payload=None):
    return {
        "shape": "rect",
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "id": ident,
        "kind": kind,
        "payload": payload or {},
    }


def _rounded_top_points(x, y, w, h, radius, segments=5):
    """上两角圆、下两角方的矩形（标题栏用，免得从面板圆角里探出去）。"""
    radius = min(radius, h, w / 2.0)
    points = [(x, y), (x + w, y), (x + w, y + h - radius)]
    for step in range(segments + 1):
        angle = math.pi * step / (2 * segments)
        points.append(
            (
                x + w - radius + math.cos(angle) * radius,
                y + h - radius + math.sin(angle) * radius,
            )
        )
    points.append((x + radius, y + h))
    for step in range(segments + 1):
        angle = math.pi / 2.0 + math.pi * step / (2 * segments)
        points.append(
            (
                x + radius + math.cos(angle) * radius,
                y + h - radius + math.sin(angle) * radius,
            )
        )
    points.append((x, y + h - radius))
    return points


def _text_width(text: str, size: float) -> float:
    """粗略估算屏幕宽度（这一层没有字体度量）。"""
    return sum(size * (1.0 if ord(char) > 0x2E80 else 0.52) for char in text)


def _elide(text: str, size: float, max_width: float) -> str:
    if _text_width(text, size) <= max_width:
        return text
    while text and _text_width(text + "…", size) > max_width:
        text = text[:-1]
    return text + "…"


# ---------------------------------------------------------------------------
# 点阵 ↔ 屏幕
# ---------------------------------------------------------------------------


def lattice_basis(domain: str):
    if domain == "W":
        return (1.0, 0.0), (-0.5, math.sqrt(3.0) / 2.0)
    return (1.0, 0.0), (0.0, 1.0)


def frame_polygon(domain: str, half_w: float, half_h: float, fit: str = "inside"):
    """取景框的多边形（平面坐标，单位与格点相同），绕原点闭合。

    Gaussian（``I``）给正方形 —— 整数格本身就是方格；Eisenstein（``W``）给六边形 ——
    三角格的天然外框就是六边形（Voronoi 胞腔），参考实现的画布也是这个形状。
    六边形取**尖顶、左右竖直边**：三角格的三条格线方向是 0° / 60° / 120°，胞腔的边
    正对这三条方向，于是顶点落在 90° / 150° / … 上。

    :param fit: ``"inside"`` = 六边形内切于给定矩形（HUD 用，不能画出面板）；
        ``"contains"`` = 六边形包住给定矩形（3D 预览用，不能把点漏在框外）。
    """
    if domain != "W":
        return [
            (-half_w, -half_h),
            (half_w, -half_h),
            (half_w, half_h),
            (-half_w, half_h),
        ]
    # 尖顶六边形：高 2r、宽 √3·r。要内切就取两者都装得下的 r，要包住就取装得下整框的 r。
    limit = half_w / 0.8660254037844386
    radius = max(half_h, limit) if fit == "contains" else min(half_h, limit)
    x = radius * 0.8660254037844386
    return [
        (0.0, -radius),
        (x, -radius / 2.0),
        (x, radius / 2.0),
        (0.0, radius),
        (-x, radius / 2.0),
        (-x, -radius / 2.0),
    ]


def lattice_to_screen(domain, a, b, origin_x, origin_y, zoom):
    (ax, ay), (bx, by) = lattice_basis(domain)
    return origin_x + (a * ax + b * bx) * zoom, origin_y + (a * ay + b * by) * zoom


def screen_to_lattice(domain, x, y, origin_x, origin_y, zoom):
    dx = (x - origin_x) / max(zoom, 1e-6)
    dy = (y - origin_y) / max(zoom, 1e-6)
    if domain == "W":
        b = dy / (math.sqrt(3.0) / 2.0)
        return dx + b * 0.5, b
    return dx, dy


def snap(domain, x, y, origin_x, origin_y, zoom):
    a, b = screen_to_lattice(domain, x, y, origin_x, origin_y, zoom)
    return round(a), round(b)


def auto_zoom(domain, points, available_w, available_h):
    """按内容包围盒算一个刚好装得下的缩放（像素 / 格距）。"""
    if not points:
        return 22.0
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    span_x = max(max(xs) - min(xs), 1.0)
    span_y = max(max(ys) - min(ys), 1.0)
    zoom = min(available_w / (span_x + 2.2), available_h / (span_y + 2.2))
    return max(MIN_ZOOM, min(MAX_ZOOM, zoom))


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def build(state) -> dict:
    """算一次布局。

    ``state`` 字段（都可缺省）：

    ==================  =========================================================
    ``domain``          ``"I"`` / ``"W"``
    ``base``            底数 ``(a, b)``
    ``digits``          ``[(a, b), ...]``
    ``places``          当前位数
    ``points``          点集（**平面坐标**的 (x, y) 列表，已抽样）
    ``origin``          面板左下角的像素坐标
    ``zoom``            ``None`` = 自动取景
    ``lattice_offset``  平面原点相对网格区左下角的偏移；``None`` = 居中
    ``hover`` / ``drag`` 当前悬停 / 正在拖的 hotspot id
    ``note``            底部提示（空则显示快捷键提示）
    ``t``               翻译函数（``None`` = 用英文 msgid）
    ==================  =========================================================
    """
    domain = state.get("domain", "I")
    base = state.get("base") or (0, 0)
    digits = state.get("digits") or []
    places = int(state.get("places") or 1)
    points = state.get("points") or []
    origin_x, origin_y = state.get("origin") or (20.0, 20.0)
    hover = state.get("hover") or ""
    note = state.get("note") or ""
    theme = THEME

    width = PANEL_WIDTH
    height = panel_height()
    primitives = []
    hotspots = []

    # 面板与标题栏
    primitives.append(_rect(origin_x, origin_y, width, height, theme["panel"], CORNER))
    primitives.append(
        _poly(
            _rounded_top_points(
                origin_x,
                origin_y + height - HEADER_HEIGHT,
                width,
                HEADER_HEIGHT,
                CORNER,
            ),
            theme["header"],
        )
    )
    # 标题栏整条的兜底命中区。**必须在按钮之前追加**：``hit`` 是逆序遍历（后画的
    # 在上），把 ``bar`` 放最前，逆序时就最后才轮到它 —— 于是点关闭 / 切环按钮时
    # 先命中按钮，点标题栏空白才回落到 ``bar``（吞掉点击，不让它穿透到视口选物体）。
    # 早先 ``bar`` 是在按钮之后追加的，结果它把整条标题栏盖住，按钮永远点不到。
    hotspots.append(
        _rect_hotspot(
            origin_x,
            origin_y + height - HEADER_HEIGHT,
            width,
            HEADER_HEIGHT,
            "bar",
            "bar",
        )
    )

    button = HEADER_HEIGHT - 8.0
    close_x = origin_x + width - button - 6.0
    close_y = origin_y + height - HEADER_HEIGHT + 4.0
    _window_button(
        primitives, hotspots, close_x, close_y, button, "×", "btn:close", hover, theme
    )
    ring_x = close_x - button - 4.0
    for value in ("W", "I"):
        ident = f"btn:ring:{value}"
        active = domain == value
        if active:
            primitives.append(
                _rect(ring_x, close_y, button, button, theme["panel"], 3.0)
            )
        elif hover == ident:
            primitives.append(
                _rect(ring_x, close_y, button, button, theme["header_hover"], 3.0)
            )
        primitives.append(
            _text(
                ring_x + button / 2,
                close_y + 3.4,
                "i" if value == "I" else "ω",
                11.5,
                theme["header"] if active else theme["header_text"],
                "CENTER",
            )
        )
        hotspots.append(_rect_hotspot(ring_x, close_y, button, button, ident, "button"))
        ring_x -= button + 3.0

    # 标题：底数写成 "base 2i"
    title_right = ring_x + button - 8.0
    title = f"{label(state, 'title')} · base {state.get('base_text') or ''}".strip()
    primitives.append(
        _text(
            origin_x + 10,
            close_y + 4.0,
            _elide(title, 11.5, title_right - origin_x - 10),
            11.5,
            theme["header_text"],
        )
    )

    # 网格区
    grid_x = origin_x
    grid_y = origin_y + FOOTER_HEIGHT + HINT_HEIGHT + GAP
    grid_w = grid_h = GRID_SIZE
    grid_rect = (grid_x, grid_y, grid_w, grid_h)

    # 取景：把数字 / 底数标记与点集一起算进去
    content = [(float(a), float(b)) for a, b in digits] + [
        (float(base[0]), float(base[1]))
    ]
    # ``fit``：``"digits"`` 只把可拖的标记框进来（改数字时的默认档，否则数字会被几十格宽
    # 的点集压成一个墨点）；``"all"`` 连点集一起框（看整体形状）。
    fit = state.get("fit") or "digits"
    probe = list(content)
    if fit == "all":
        probe += [(float(point[0]), float(point[1])) for point in points]
    zoom = float(
        state.get("zoom") or auto_zoom(domain, probe, grid_w - 20.0, grid_h - 20.0)
    )

    offset = state.get("lattice_offset")
    if offset:
        lattice_origin = (grid_x + offset[0], grid_y + offset[1])
    else:
        # 居中用的集合必须与取景用的**同一个**（``probe``）：只框数字时却按整个点集居中，
        # 数字会被推出网格区外 —— 这是第一版实测踩到的坑。
        (ax, ay), (bx, by) = lattice_basis(domain)
        xs = [a * ax + b * bx for a, b in probe]
        ys = [a * ay + b * by for a, b in probe]
        mid_x = (max(xs) + min(xs)) / 2.0 if xs else 0.0
        mid_y = (max(ys) + min(ys)) / 2.0 if ys else 0.0
        lattice_origin = (
            grid_x + grid_w / 2.0 - mid_x * zoom,
            grid_y + grid_h / 2.0 - mid_y * zoom,
        )
    lattice_offset = (lattice_origin[0] - grid_x, lattice_origin[1] - grid_y)

    def to_screen(a, b):
        return lattice_to_screen(
            domain, a, b, lattice_origin[0], lattice_origin[1], zoom
        )

    def plane_to_screen(x, y):
        return lattice_origin[0] + x * zoom, lattice_origin[1] + y * zoom

    # 取景框：Gaussian 是方形、Eisenstein 是六边形（六边形就是三角格的胞腔形状）。
    # 先铺底色再描边；点阵与点集随后画在上面，并且按这个多边形裁剪 —— 否则六边形
    # 外面的四个角会漏出点来。
    half_w = (grid_w / 2.0) / zoom
    half_h = (grid_h / 2.0) / zoom
    centre_x = (grid_x + grid_w / 2.0 - lattice_origin[0]) / zoom
    centre_y = (grid_y + grid_h / 2.0 - lattice_origin[1]) / zoom
    frame = [
        plane_to_screen(centre_x + px, centre_y + py)
        for px, py in frame_polygon(domain, half_w, half_h)
    ]
    primitives.append(_poly(frame, theme["grid_bg"]))
    primitives.append(_line(frame + [frame[0]], 1.4, theme["frame"]))

    # 点阵：整数格点只画点，不画线 —— 这个插件的地图是"格子上的点"，线反而糊
    reach_a = int(grid_w / zoom) + 2
    reach_b = int(grid_h / zoom) + 2
    for a in range(-reach_a, reach_a + 1):
        for b in range(-reach_b, reach_b + 1):
            x, y = to_screen(a, b)
            if _inside_polygon(x, y, frame):
                axis = a == 0 and b == 0
                primitives.append(
                    _circle(
                        x,
                        y,
                        1.9 if axis else 1.1,
                        theme["axis"] if axis else theme["grid_dot"],
                    )
                )

    # 点集：小方块（比圆便宜得多，一千多个点也不会掉帧）。按数字上色 —— 和 GP 输出同一套
    # 色号，这样 HUD 里看到的分布就是生成出来会得到的东西。
    size = 2.4
    tint_cache: dict[int, tuple] = {}
    for point in points:
        sx, sy = plane_to_screen(point[0], point[1])
        if not _inside_polygon(sx, sy, frame):
            continue
        slot = int(point[2]) if len(point) > 2 else 0
        colour = tint_cache.get(slot)
        if colour is None:
            base = palette.hex_to_rgb(palette.digit_colour(slot))
            colour = mix(base, GRID_BG[:3], 0.18)
            tint_cache[slot] = colour
        primitives.append(_rect(sx - size / 2.0, sy - size / 2.0, size, size, colour))

    # 数字标记（0 号固定画成空心：它恒为零，不可拖）
    for index, (a, b) in enumerate(digits):
        x, y = to_screen(a, b)
        ident = f"digit:{index}"
        hovered = hover == ident
        dragging = state.get("drag") == ident
        colour = theme["marker_hover"] if hovered or dragging else theme["marker"]
        radius = MARKER_RADIUS + (1.5 if hovered or dragging else 0.0)
        primitives.append(_circle(x, y, radius, colour, filled=True))
        primitives.append(_circle(x, y, radius, theme["marker_ring"], filled=False))
        text = "0" if index == 0 else str(index)
        primitives.append(_text(x, y - 3.6, text, 9.5, theme["marker_ring"], "CENTER"))
        hotspots.append(
            _rect_hotspot(
                x - MARKER_HIT,
                y - MARKER_HIT,
                MARKER_HIT * 2,
                MARKER_HIT * 2,
                ident,
                "digit",
                {"index": index},
            )
        )

    # 底数：菱形（和数字区分开，它不是数字集的一员）
    bx, by = to_screen(base[0], base[1])
    hovered = hover == "base"
    dragging = state.get("drag") == "base"
    radius = MARKER_RADIUS + 2.0 + (1.5 if hovered or dragging else 0.0)
    diamond = [
        (bx, by + radius),
        (bx + radius, by),
        (bx, by - radius),
        (bx - radius, by),
    ]
    primitives.append(_poly(diamond, theme["base"]))
    primitives.append(_line(diamond + [diamond[0]], 1.4, theme["marker_ring"]))
    hotspots.append(_ring_hotspot(bx, by, MARKER_HIT + 2.0, "base", "base"))

    # 页脚
    footer_x = grid_x + 6.0
    footer_y = origin_y + HINT_HEIGHT
    primitives.append(
        _text(footer_x, footer_y + 7, label(state, "places"), 10.5, theme["text"])
    )
    primitives.append(
        _text(footer_x + 66, footer_y + 7, str(places), 11.5, theme["text"], "CENTER")
    )
    _small_button(
        primitives,
        hotspots,
        footer_x + 36,
        footer_y,
        22,
        "‹",
        "btn:places:-",
        hover,
        theme,
    )
    _small_button(
        primitives,
        hotspots,
        footer_x + 84,
        footer_y,
        22,
        "›",
        "btn:places:+",
        hover,
        theme,
    )
    _small_button(
        primitives,
        hotspots,
        footer_x + 114,
        footer_y,
        54,
        label(state, "add"),
        "btn:add",
        hover,
        theme,
    )
    _small_button(
        primitives,
        hotspots,
        footer_x + 174,
        footer_y,
        54,
        label(state, "remove"),
        "btn:remove",
        hover,
        theme,
    )
    _small_button(
        primitives,
        hotspots,
        footer_x + 234,
        footer_y,
        48,
        label(state, "fit"),
        "btn:fit",
        hover,
        theme,
    )

    # 提示行：出错 > 快捷键
    hint_y = origin_y + 4
    if note:
        primitives.append(
            _text(
                footer_x, hint_y + 3, _elide(note, 9.5, width - 16), 9.5, theme["error"]
            )
        )
    else:
        primitives.append(
            _text(
                footer_x,
                hint_y + 3,
                _elide(label(state, "hint"), 9.5, width - 16),
                9.5,
                theme["text_dim"],
            )
        )

    return {
        "panel": (origin_x, origin_y, width, height),
        "grid": grid_rect,
        "lattice_origin": lattice_origin,
        "lattice_offset": lattice_offset,
        "zoom": zoom,
        "primitives": primitives,
        "hotspots": hotspots,
    }


def _small_button(primitives, hotspots, x, y, w, text, ident, hover, theme):
    height = 18.0
    active = hover == ident
    primitives.append(
        _rect(
            x, y, w, height, theme["button_hover"] if active else theme["button"], 4.0
        )
    )
    primitives.append(
        _text(x + w / 2.0, y + 5.0, text, 9.5, theme["button_text"], "CENTER")
    )
    hotspots.append(_rect_hotspot(x, y, w, height, ident, "button"))


def _window_button(primitives, hotspots, x, y, size, text, ident, hover, theme):
    if hover == ident:
        primitives.append(_rect(x, y, size, size, theme["header_hover"], 3.0))
    primitives.append(
        _text(
            x + size / 2.0,
            y + size / 2.0 - 4.0,
            text,
            13.0,
            theme["header_text"],
            "CENTER",
        )
    )
    hotspots.append(_rect_hotspot(x, y, size, size, ident, "button"))


def hit(hotspots, x, y):
    """命中测试：**后画的在上**（逆序返回第一个命中的）。"""
    for spot in reversed(hotspots):
        if spot["shape"] == "ring":
            if math.hypot(x - spot["x"], y - spot["y"]) <= spot["r"]:
                return spot
        elif (
            spot["x"] <= x <= spot["x"] + spot["w"]
            and spot["y"] <= y <= spot["y"] + spot["h"]
        ):
            return spot
    return None
