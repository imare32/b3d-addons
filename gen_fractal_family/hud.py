"""HUD 后端：把 :mod:`gen_fractal_family.hud_layout` 算出的图元画到 3D 视图上。

分工很清楚：**布局在** :mod:`hud_layout`（纯函数、可离线跑），**这里只负责画**。
所以这一层只有三件事：字体、立即模式图元（``gpu``）、文字（``blf``）。

几个必须照顾到的实测行为：

* ``POST_PIXEL`` 的坐标系是"区域左下角为原点、单位像素"，与
  ``event.mouse_region_x/y`` 是同一个空间 —— 命中测试直接拿鼠标坐标喂给
  :func:`hud_layout.hit` 就行，不用做任何投影换算；
* ``draw_handler_add`` 的回调**每个 3D 视图各调一次**：面板只该画在会话所属的那个
  区域里（否则开两个视图会出现两个面板，还都能拖）；
* 句柄存两处（模块变量 + ``bpy.app.driver_namespace``）：文件加载会清空 namespace，
  而句柄在加载后还活着，只存 namespace 就再也删不掉了；
* ``blf`` 默认字体是拉丁字体，**画不了汉字**。这里优先加载 Blender 自带的
  "Noto Sans CJK"，其次找系统字体；都没有就把文案切成英文（
  :data:`hud_layout.LABELS` 里两套文案），不会画出一屏豆腐块。

颜色约定：2D 覆盖层直接写 sRGB 值（与面板上看到的色板一致）；3D 预览走色彩管理，
那边由 :mod:`preview` 转线性。**面板上的颜色一律不透明**（半透明由
:func:`hud_layout.mix` 预混），原因见 :meth:`_Canvas._flush` 的注释。
"""

from __future__ import annotations

import math
import os

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from . import hud_layout, preview

__all__ = [
    "begin",
    "end",
    "install",
    "layout_for",
    "remove",
    "session",
    "tag_redraw",
]

#: 面板默认离视图**左下角**的距离（像素）。

#: 放左下角是因为左上角常被用户自己的面板 / 工具栏 / 标题占住。
DEFAULT_MARGIN = 18.0

#: 句柄（同 :mod:`preview`：模块变量是主，namespace 是兜底）。
_HANDLE = None
_HANDLE_KEY = "gen_fractal_family_hud_handle"
_HANDLE_REGION = "WINDOW"

#: 当前 HUD 会话（``None`` = 没有在编辑）。
_SESSION = None

#: 字体：``None`` = 还没解析过；解析结果缓存在这里。
_FONT = None
_FONT_PATH = None
_FONT_LANG = "zh"

#: 本插件用到的主题色。配色**只在** :mod:`hud_layout` 里定义一份（它已经从 ``palette``
#: 取色并预混成不透明值），这里只是给别处一个别名。
THEME = hud_layout.THEME


# ---------------------------------------------------------------------------
# 字体
# ---------------------------------------------------------------------------


def _font_candidates():
    """按优先级给出可能带汉字的字体路径（Blender 自带的最稳）。"""
    paths = []
    try:
        bundled = bpy.utils.system_resource("DATAFILES", path="fonts")
    except (AttributeError, TypeError):
        bundled = ""
    if bundled:
        paths.append(os.path.join(bundled, "Noto Sans CJK Regular.woff2"))
        paths.append(os.path.join(bundled, "Inter.woff2"))
    paths += [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    ]
    return [path for path in paths if path and os.path.exists(path)]


def _font() -> int:
    """解析一次字体：优先带汉字的，实在没有就用 ``blf`` 默认字体 + 英文文案。"""
    global _FONT, _FONT_PATH, _FONT_LANG
    if _FONT is not None:
        return _FONT
    _FONT = 0
    for path in _font_candidates():
        try:
            font_id = blf.load(path)
        except (OSError, RuntimeError, ValueError):
            continue
        if font_id is not None and font_id > 0:
            _FONT = font_id
            _FONT_PATH = path
            break
    _FONT_LANG = "zh" if _FONT_PATH and "Inter" not in os.path.basename(_FONT_PATH) else "en"
    return _FONT


# ---------------------------------------------------------------------------
# 立即模式画布
# ---------------------------------------------------------------------------


class _Canvas:
    """把布局图元按顺序画出来：同一批次（模式 + 颜色 + 线宽）连续的图元合并成一次绘制。"""

    def __init__(self, font_id: int):
        self.font = font_id
        self.shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        self._mode = None
        self._colour = None
        self._width = 1.0
        self._vertices = []

    # --- 图元 -------------------------------------------------------------

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
                points.append((centre_x + math.cos(angle) * radius,
                               centre_y + math.sin(angle) * radius))
        self.poly(points, colour)

    def poly(self, points, colour):
        if len(points) < 3:
            return
        self._start("TRIS", colour)
        anchor = points[0]
        for index in range(1, len(points) - 1):
            self._vertices += [*anchor, *points[index], *points[index + 1]]

    def circle(self, x, y, radius, colour, filled=True, segments=20, width=1.6):
        points = [
            (x + math.cos(2.0 * math.pi * index / segments) * radius,
             y + math.sin(2.0 * math.pi * index / segments) * radius)
            for index in range(segments)
        ]
        if filled:
            self.poly(points, colour)
        else:
            self._start("LINES", colour, width)
            for index in range(segments):
                a = points[index]
                b = points[(index + 1) % segments]
                self._vertices += [*a, *b]

    def line(self, points, width, colour):
        if len(points) < 2:
            return
        self._start("LINES", colour, width)
        for index in range(len(points) - 1):
            self._vertices += [*points[index], *points[index + 1]]

    def text(self, x, y, text, size, colour, align="LEFT"):
        if not text:
            return
        self._flush()
        font = self.font
        blf.size(font, size)
        blf.color(font, *colour)
        width = blf.dimensions(font, text)[0] if align != "LEFT" else 0.0
        if align == "CENTER":
            x -= width / 2.0
        elif align == "RIGHT":
            x -= width
        blf.position(font, x, y, 0.0)
        blf.draw(font, text)

    # --- 批次 -------------------------------------------------------------

    def _start(self, mode, colour, width=1.0):
        key = (mode, colour, width)
        if key != (self._mode, self._colour, self._width):
            self._flush()
            self._mode, self._colour, self._width = mode, colour, width
            self._vertices = []

    def _flush(self):
        if not self._vertices or self._mode is None:
            self._vertices = []
            return
        # 顶点必须是 (x, y, z) 三元组：实测 5.2 上传**扁平浮点列表**会静默画不出东西
        # （不报错、也不抛异常），所以这里老老实实拼元组。
        positions = [
            (self._vertices[index], self._vertices[index + 1], 0.0)
            for index in range(0, len(self._vertices), 2)
        ]
        batch = batch_for_shader(self.shader, self._mode, {"pos": positions})
        # **每次绘制都要重设一次混合状态**：``blf.draw()`` 会把它清掉，之后画的图元
        # 就变成"直写"——浅色底纹会被渲染成实心黑（5.2 实测：底部按钮全黑就是这么来的）。
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
# 会话（谁在编辑、面板放在哪）
# ---------------------------------------------------------------------------


def session() -> dict | None:
    """当前 HUD 会话状态；没有会话返回 ``None``。"""
    return _SESSION


def begin(context, region=None) -> dict:
    """开始一次 HUD 会话（面板位置与缩放按当前内容初始化）。"""
    global _SESSION
    region = region or getattr(context, "region", None)
    width = getattr(region, "width", 800)
    height = getattr(region, "height", 600)
    # 贴在**左下角**：左上角常被用户自己的 N 面板 / 工具栏 / 视图标题压住，
    # 而 ``POST_PIXEL`` 的原点就在左下角，y 只需留一个边距。
    _SESSION = {
        "region": region.as_pointer() if region is not None else 0,
        "origin": [DEFAULT_MARGIN, DEFAULT_MARGIN],
        "zoom": None,
        "lattice_offset": None,
        "hover": "",
        "drag": "",
        "note": "",
        "size": [width, height],
    }
    tag_redraw()
    return _SESSION


def end(context=None) -> None:
    """结束会话：面板随之收起（HUD 只在编辑时存在，不做"看得见却点不动"的常驻面板）。"""
    global _SESSION
    if _SESSION is None:
        return
    _SESSION = None
    tag_redraw()


def _settings(context=None):
    context = context or bpy.context
    scene = getattr(context, "scene", None)
    if scene is None:
        return None
    return getattr(scene, "gen_fractal_family", None)


# ---------------------------------------------------------------------------
# 布局
# ---------------------------------------------------------------------------


def layout_for(context=None) -> dict | None:
    """按当前参数 + 会话状态算一次布局（绘制与命中测试共用同一份结果）。"""
    context = context or bpy.context
    settings = _settings(context)
    if settings is None:
        return None
    hud = _SESSION or {}
    note = settings.error or hud.get("note") or ""

    elements = [
        {"a": item.a, "b": item.b, "reverse": bool(item.reverse), "mirror": bool(item.mirror)}
        for item in settings.items
    ]
    state = {
        "elements": elements,
        "domain": settings.domain,
        "level": int(settings.level),
        "origin": tuple(hud.get("origin") or (DEFAULT_MARGIN, DEFAULT_MARGIN)),
        "zoom": hud.get("zoom"),
        "lattice_offset": hud.get("lattice_offset"),
        "hover": hud.get("hover") or "",
        "drag": hud.get("drag") or "",
        "family": settings.family_name(),
        "note": note,
        "contraction": settings.contraction(),
        "lang": _FONT_LANG if _FONT is not None else "zh",
    }
    layout = hud_layout.build(state)
    if _SESSION is not None:
        # 自动取景只在第一次算；之后记住一份，否则一拖节点视角就跟着内容跑。
        # 记的是**相对网格区的偏移**：面板被搬走时格线与折线才跟着搬（绝对坐标会留在原地）。
        if _SESSION.get("zoom") is None:
            _SESSION["zoom"] = layout["zoom"]
        if not _SESSION.get("lattice_offset"):
            _SESSION["lattice_offset"] = layout["lattice_offset"]
    return layout


def reset_view() -> None:
    """重新取景（HUD 的「适应」按钮）：缩放与格点原点都回到自动值。"""
    if _SESSION is None:
        return
    _SESSION["zoom"] = None
    _SESSION["lattice_offset"] = None
    tag_redraw()


# ---------------------------------------------------------------------------
# 绘制回调
# ---------------------------------------------------------------------------


def _current_region_pointer() -> int:
    region = getattr(bpy.context, "region", None)
    return region.as_pointer() if region is not None else 0


def _draw():
    """``POST_PIXEL`` 回调：每个 3D 视图各调一次。"""
    settings = _settings()
    hud = _SESSION
    if settings is None or hud is None:
        # 没有会话就不画：HUD 只存在于编辑过程中
        return
    if hud["region"] and hud["region"] != _current_region_pointer():
        # 会话属于另一个 3D 视图：别在别人的区域里画第二份面板
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
            kind = primitive["kind"]
            if kind == "rect":
                canvas.rect(primitive["x"], primitive["y"], primitive["w"], primitive["h"],
                            primitive["colour"], primitive.get("radius", 0.0))
            elif kind == "poly":
                canvas.poly(primitive["points"], primitive["colour"])
            elif kind == "line":
                canvas.line(primitive["points"], primitive["width"], primitive["colour"])
            elif kind == "circle":
                canvas.circle(primitive["x"], primitive["y"], primitive["r"],
                              primitive["colour"], primitive["filled"],
                              width=primitive.get("width", 1.6))
            elif kind == "text":
                canvas.text(primitive["x"], primitive["y"], primitive["text"],
                            primitive["size"], primitive["colour"], primitive.get("align", "LEFT"))
        canvas.flush()
    finally:
        gpu.state.line_width_set(1.0)
        gpu.state.blend_set("NONE")


# ---------------------------------------------------------------------------
# 句柄装卸
# ---------------------------------------------------------------------------


def tag_redraw() -> None:
    """请求重绘（视口预览与 HUD 都在 3D 视图里，一次全刷）。"""
    preview.tag_redraw()


def install() -> None:
    """挂上 ``POST_PIXEL`` 句柄（重复调用不会重复挂）。"""
    global _HANDLE
    remove()
    _HANDLE = bpy.types.SpaceView3D.draw_handler_add(_draw, (), _HANDLE_REGION, "POST_PIXEL")
    bpy.app.driver_namespace[_HANDLE_KEY] = (_HANDLE, _HANDLE_REGION)


def remove() -> None:
    """摘掉句柄并收起会话（``unregister`` 必须调）。"""
    global _HANDLE, _FONT, _FONT_PATH
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
    end()
    if _FONT_PATH:
        try:
            blf.unload(_FONT_PATH)
        except (RuntimeError, ValueError):
            pass
    _FONT = None
    _FONT_PATH = None
