"""HUD 后端：把 :mod:`hud_layout` 算出的图元画到 3D 视图上。

分工与 `fractal_family` 相同：**布局在** :mod:`hud_layout`（纯函数、可离线跑），
**这里只负责画** —— 字体、立即模式图元（``gpu``）、文字（``blf``）。

必须照顾到的几个实测行为：

* ``POST_PIXEL`` 的坐标系是"区域左下角为原点、单位像素"，与 ``event.mouse_region_x/y``
  同一个空间 —— 命中测试直接拿鼠标坐标喂 :func:`hud_layout.hit`，不做投影换算；
* ``draw_handler_add`` 的回调**每个 3D 视图各调一次**：只画在会话所属的那个区域里；
* 句柄存两处（模块变量 + ``driver_namespace``）：加载文件会清空 namespace，而句柄在加载
  之后还活着，只存 namespace 就再也删不掉；
* ``blf`` 默认字体画不了汉字，优先加载 Blender 自带的 Noto Sans CJK，加载不到就退回英文
  msgid（:data:`hud_layout.LABELS`），不会画出一屏豆腐块。
"""

from __future__ import annotations

import math
import os

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from . import hud_layout, preview
from .i18n import t as _translation

__all__ = [
    "active",
    "begin",
    "end",
    "fit_scope",
    "install",
    "layout_for",
    "remove",
    "reset_view",
    "session",
    "tag_redraw",
]

DEFAULT_MARGIN = 18.0
REDO_CLEARANCE = 24.0

_HANDLE = None
_HANDLE_KEY = "imaginarybase_hud_handle"
_HANDLE_REGION = "WINDOW"
_SESSION = None

_FONT = None
_FONT_PATH = None
_FONT_DRAW_CJK = False

THEME = hud_layout.THEME


# ---------------------------------------------------------------------------
# 字体
# ---------------------------------------------------------------------------


def _font_candidates():
    """``(path, draws_cjk)``，只用 Blender 自带的字体（不碰系统字体、不要文件权限）。"""
    try:
        bundled = bpy.utils.system_resource("DATAFILES", path="fonts")
    except (AttributeError, TypeError):
        bundled = ""
    if not bundled:
        return []
    return [
        (os.path.join(bundled, "Noto Sans CJK Regular.woff2"), True),
        (os.path.join(bundled, "Inter.woff2"), False),
        (os.path.join(bundled, "DejaVuSansMono.woff2"), False),
    ]


def _font() -> int:
    global _FONT, _FONT_PATH, _FONT_DRAW_CJK
    if _FONT is not None:
        return _FONT
    _FONT = 0
    for path, draws_cjk in _font_candidates():
        if not os.path.exists(path):
            continue
        try:
            font_id = blf.load(path)
        except (OSError, RuntimeError, ValueError):
            continue
        if font_id is not None and font_id > 0:
            _FONT = font_id
            _FONT_PATH = path
            _FONT_DRAW_CJK = draws_cjk
            break
    return _FONT


def _translator():
    return _translation if (_FONT_DRAW_CJK and _font()) else None


# ---------------------------------------------------------------------------
# 画布
# ---------------------------------------------------------------------------


class _Canvas:
    """把图元按顺序画出来；同一批（模式 + 颜色 + 线宽）连续的图元合并成一次绘制。"""

    def __init__(self, font_id: int):
        self.font = font_id
        self.shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        self._mode = None
        self._colour = None
        self._width = 1.0
        self._vertices = []

    def rect(self, x, y, w, h, colour, radius=0.0):
        if radius <= 0.0:
            self.poly([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], colour)
            return
        radius = min(radius, h / 2.0, w / 2.0)
        points = []
        corners = (
            (x + w - radius, y + radius, -math.pi / 2.0, 0.0),
            (x + w - radius, y + h - radius, 0.0, math.pi / 2.0),
            (x + radius, y + h - radius, math.pi / 2.0, math.pi),
            (x + radius, y + radius, math.pi, math.pi * 1.5),
        )
        for centre_x, centre_y, start, end in corners:
            for step in range(5):
                angle = start + (end - start) * step / 4.0
                points.append(
                    (
                        centre_x + math.cos(angle) * radius,
                        centre_y + math.sin(angle) * radius,
                    )
                )
        self.poly(points, colour)

    def poly(self, points, colour):
        if len(points) < 3:
            return
        self._start("TRIS", colour)
        anchor = points[0]
        for index in range(1, len(points) - 1):
            self._vertices += [*anchor, *points[index], *points[index + 1]]

    def circle(self, x, y, radius, colour, filled=True, segments=18, width=1.6):
        points = [
            (
                x + math.cos(2.0 * math.pi * index / segments) * radius,
                y + math.sin(2.0 * math.pi * index / segments) * radius,
            )
            for index in range(segments)
        ]
        if filled:
            self.poly(points, colour)
            return
        self._start("LINES", colour, width)
        for index in range(segments):
            self._vertices += [*points[index], *points[(index + 1) % segments]]

    def line(self, points, width, colour):
        """粗折线用屏幕空间四边形画（``line_width`` 在 Metal 上超过 1 px 会被忽略）。"""
        if len(points) < 2:
            return
        half = max(0.5, float(width) / 2.0)
        for index in range(len(points) - 1):
            x0, y0 = points[index]
            x1, y1 = points[index + 1]
            dx, dy = x1 - x0, y1 - y0
            length = math.hypot(dx, dy)
            if length < 1e-6:
                continue
            nx, ny = -dy / length * half, dx / length * half
            self.poly(
                [
                    (x0 + nx, y0 + ny),
                    (x1 + nx, y1 + ny),
                    (x1 - nx, y1 - ny),
                    (x0 - nx, y0 - ny),
                ],
                colour,
            )
        for x, y in points[1:-1]:
            self.circle(x, y, half, colour, filled=True, segments=10)

    def text(self, x, y, text, size, colour, align="LEFT"):
        if not text:
            return
        self._flush()
        font = self.font
        blf.size(font, size)
        blf.color(font, *colour)
        if align != "LEFT":
            width = blf.dimensions(font, text)[0]
            x -= width / 2.0 if align == "CENTER" else width
        blf.position(font, x, y, 0.0)
        blf.draw(font, text)

    def _start(self, mode, colour, width=1.0):
        if (mode, colour, width) != (self._mode, self._colour, self._width):
            self._flush()
            self._mode, self._colour, self._width = mode, colour, width
            self._vertices = []

    def _flush(self):
        if not self._vertices or self._mode is None:
            self._vertices = []
            return
        # 顶点必须给 (x, y, z) 三元组：5.2 上传扁平浮点列表会静默画不出东西
        positions = [
            (self._vertices[index], self._vertices[index + 1], 0.0)
            for index in range(0, len(self._vertices), 2)
        ]
        batch = batch_for_shader(self.shader, self._mode, {"pos": positions})
        # 每次绘制都重设一次混合状态：blf.draw() 会把它清掉
        gpu.state.blend_set("ALPHA")
        self.shader.bind()
        self.shader.uniform_float("color", self._colour)
        if self._mode == "LINES":
            gpu.state.line_width_set(max(1.0, self._width))
        batch.draw(self.shader)
        if self._mode == "LINES":
            gpu.state.line_width_set(1.0)
        self._vertices = []

    def flush(self):
        self._flush()
        self._mode = self._colour = None


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------


def _default_origin(height: float):
    room = float(height) - hud_layout.panel_height() - DEFAULT_MARGIN
    return DEFAULT_MARGIN, max(
        DEFAULT_MARGIN, min(DEFAULT_MARGIN + REDO_CLEARANCE, room)
    )


def session():
    """HUD 会话字典；没在运行时是 ``None``。"""
    return _SESSION


def active() -> bool:
    """HUD 是否正在运行。

    面板按钮的 ``depress`` 之类只吃 ``bool``，别把 :func:`session` 的返回值直接塞进去
    ——``None`` 会让 ``UILayout.operator()`` 抛 TypeError，整块面板都画不出来。
    """
    return _SESSION is not None


def begin(context, region=None):
    global _SESSION
    region = region or getattr(context, "region", None)
    width = getattr(region, "width", 800)
    height = getattr(region, "height", 600)
    _SESSION = {
        "region": region.as_pointer() if region is not None else 0,
        "origin": list(_default_origin(height)),
        "zoom": None,
        "lattice_offset": None,
        "hover": "",
        "drag": "",
        # 取景档：``digits`` = 只框可拖的标记（改数字时的默认），``all`` = 连点集一起框
        "fit": "digits",
        "size": [width, height],
    }
    tag_redraw()
    return _SESSION


def end(context=None):
    global _SESSION
    if _SESSION is None:
        return
    _SESSION = None
    tag_redraw()


def _settings(context=None):
    context = context or bpy.context
    scene = getattr(context, "scene", None)
    return getattr(scene, "imaginarybase", None) if scene is not None else None


def reset_view(scope=None):
    """「适应」：缩放与格点原点回到自动值。

    :param scope: ``"digits"`` = 只框数字与底数（改数字用），``"all"`` = 连点集一起框
        （看整体形状用），``None`` = 在两个档之间切换。
    """
    if _SESSION is None:
        return
    if scope is None:
        scope = "all" if _SESSION.get("fit") == "digits" else "digits"
    _SESSION["fit"] = scope
    _SESSION["zoom"] = None
    _SESSION["lattice_offset"] = None
    tag_redraw()


def fit_scope() -> str:
    """当前取景档（``"digits"`` / ``"all"``）。"""
    return (_SESSION or {}).get("fit") or "digits"


def _sampled_points(settings):
    """点集降采样到 :data:`hud_layout.MAP_POINTS` 以内（HUD 只有 300 px，全画没意义）。"""
    xy = settings.point_data()["xy"]
    count = len(xy)
    if count == 0:
        return []
    stride = max(1, math.ceil(count / float(hud_layout.MAP_POINTS)))
    slots = settings.point_data()["slot"]
    return [
        (float(xy[index, 0]), float(xy[index, 1]), int(slots[index]))
        for index in range(0, count, stride)
    ]


def layout_for(context=None):
    """按当前参数 + 会话状态算一次布局（绘制与命中测试共用这一份）。"""
    context = context or bpy.context
    settings = _settings(context)
    if settings is None:
        return None
    hud = _SESSION or {}
    from . import ring

    state = {
        "domain": settings.domain,
        "base": (settings.base_a, settings.base_b),
        "base_text": ring.format_qint(
            settings.domain, settings.base_a, settings.base_b
        ),
        "digits": settings.digit_list(),
        "places": int(settings.places),
        "points": _sampled_points(settings),
        "origin": tuple(
            hud.get("origin") or _default_origin((hud.get("size") or (800, 600))[1])
        ),
        "zoom": hud.get("zoom"),
        "lattice_offset": hud.get("lattice_offset"),
        "fit": hud.get("fit") or "digits",
        "hover": hud.get("hover") or "",
        "drag": hud.get("drag") or "",
        "note": settings.notice or "",
        "t": _translator(),
    }
    layout = hud_layout.build(state)
    if _SESSION is not None:
        if _SESSION.get("zoom") is None:
            _SESSION["zoom"] = layout["zoom"]
        if not _SESSION.get("lattice_offset"):
            _SESSION["lattice_offset"] = layout["lattice_offset"]
    return layout


def _current_region_pointer() -> int:
    region = getattr(bpy.context, "region", None)
    return region.as_pointer() if region is not None else 0


def _draw():
    settings = _settings()
    hud = _SESSION
    if settings is None or hud is None:
        return
    if hud["region"] and hud["region"] != _current_region_pointer():
        return
    try:
        layout = layout_for()
    except Exception:
        return
    if not layout:
        return

    canvas = _Canvas(_font())
    gpu.state.blend_set("ALPHA")
    try:
        for primitive in layout["primitives"]:
            kind = primitive.get("kind")
            if kind is None:
                continue
            if kind == "rect":
                canvas.rect(
                    primitive["x"],
                    primitive["y"],
                    primitive["w"],
                    primitive["h"],
                    primitive["colour"],
                    primitive.get("radius", 0.0),
                )
            elif kind == "poly":
                canvas.poly(primitive["points"], primitive["colour"])
            elif kind == "line":
                canvas.line(
                    primitive["points"], primitive["width"], primitive["colour"]
                )
            elif kind == "circle":
                canvas.circle(
                    primitive["x"],
                    primitive["y"],
                    primitive["r"],
                    primitive["colour"],
                    primitive["filled"],
                    width=primitive.get("width", 1.6),
                )
            elif kind == "text":
                canvas.text(
                    primitive["x"],
                    primitive["y"],
                    primitive["text"],
                    primitive["size"],
                    primitive["colour"],
                    primitive.get("align", "LEFT"),
                )
        canvas.flush()
    finally:
        gpu.state.line_width_set(1.0)
        gpu.state.blend_set("NONE")


def tag_redraw():
    preview.tag_redraw()


def install():
    global _HANDLE
    remove()
    _HANDLE = bpy.types.SpaceView3D.draw_handler_add(
        _draw, (), _HANDLE_REGION, "POST_PIXEL"
    )
    bpy.app.driver_namespace[_HANDLE_KEY] = (_HANDLE, _HANDLE_REGION)


def remove():
    global _HANDLE, _FONT, _FONT_PATH, _FONT_DRAW_CJK
    stored = bpy.app.driver_namespace.pop(_HANDLE_KEY, None)
    handles = [_HANDLE]
    if isinstance(stored, tuple):
        handles.append(stored[0])
    elif stored is not None:
        handles.append(stored)
    for handle in handles:
        if handle is None:
            continue
        try:
            bpy.types.SpaceView3D.draw_handler_remove(handle, _HANDLE_REGION)
        except (ValueError, TypeError):
            pass
    _HANDLE = None
    end()
    if _FONT_PATH:
        try:
            blf.unload(_FONT_PATH)
        except (RuntimeError, ValueError):
            pass
    _FONT = None
    _FONT_PATH = None
    _FONT_DRAW_CJK = False
