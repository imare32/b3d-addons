"""算子：生成 / 删除 / 取景 / 加删数字 / **HUD 模态编辑**。

HUD 的模态算子是唯一"有状态"的地方：它把鼠标事件翻译成对 ``scene.imaginarybase``
的写入，写入再由 :mod:`props` 的回调触发重画，于是拖动数字时点集当场变形。
面板与 HUD 改的是同一套参数，两边永远同步 —— 这是 `fractal_family` 的做法，也照搬了。
"""

from __future__ import annotations

import bpy

from . import build, hud, hud_layout, ring
from .i18n import t, tfmt

__all__ = ["classes"]

#: 滚轮改「位数」的速度：一格一位。缩放走的另一个分支（Shift）。
ZOOM_MIN = 4.0
ZOOM_MAX = 140.0


def _settings(context):
    return getattr(context.scene, "imaginarybase", None)


def _redraw():
    hud.tag_redraw()


def _notice(settings, text: str) -> None:
    settings.notice = text


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------


class IMAGINARYBASE_OT_generate(bpy.types.Operator):
    """Write the point set into a grease pencil object"""

    bl_idname = "imaginarybase.generate"
    bl_label = "Generate"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        settings = _settings(context)
        return settings is not None and len(settings.digits) >= 2

    def execute(self, context):
        settings = _settings(context)
        summary = build.build(settings)
        if not summary["count"]:
            _notice(settings, t("Nothing to draw: check the base and the digits"))
            self.report({"WARNING"}, t("Nothing to draw"))
            return {"CANCELLED"}
        check = summary["check"]
        if check["ok"]:
            _notice(settings, "")
            self.report(
                {"INFO"},
                tfmt(
                    "Places {places} · {count} points · canonical numeral system",
                    places=summary["places"],
                    count=summary["count"],
                ),
            )
        else:
            _notice(settings, check["reason"])
            self.report(
                {"INFO"},
                tfmt(
                    "Places {places} · {count} points · {reason}",
                    places=summary["places"],
                    count=summary["count"],
                    reason=check["reason"],
                ),
            )
        _redraw()
        return {"FINISHED"}


class IMAGINARYBASE_OT_remove(bpy.types.Operator):
    """Delete the generated object"""

    bl_idname = "imaginarybase.remove"
    bl_label = "Delete Object"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        removed = build.remove_object()
        self.report({"INFO"}, t("Deleted") if removed else t("No object to delete"))
        return {"FINISHED"}


class IMAGINARYBASE_OT_fit(bpy.types.Operator):
    """Frame the 3D viewport on the generated object"""

    bl_idname = "imaginarybase.fit"
    bl_label = "Frame in Viewport"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        xy = settings.point_data()["xy"]
        if len(xy) == 0:
            self.report({"WARNING"}, t("Nothing to frame"))
            return {"CANCELLED"}
        low = (float(xy[:, 0].min()), float(xy[:, 1].min()), 0.0)
        high = (float(xy[:, 0].max()), float(xy[:, 1].max()), 0.0)
        center = tuple((low[index] + high[index]) / 2.0 for index in range(3))
        size = max(high[0] - low[0], high[1] - low[1], 1.0)
        for area in context.screen.areas:
            if area.type != "VIEW_3D":
                continue
            for space in area.spaces:
                if space.type != "VIEW_3D":
                    continue
                space.region_3d.view_location = center
                space.region_3d.view_distance = size * 1.4 + 2.0
                space.region_3d.view_perspective = "ORTHO"
                space.overlay.show_floor = False
                space.overlay.show_axis_x = False
                space.overlay.show_axis_y = False
                area.tag_redraw()
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# 数字 / 位数
# ---------------------------------------------------------------------------


def _free_digit(settings) -> tuple[int, int]:
    """找一个还没被占用的格点（从 1 开始往外螺旋）。"""
    taken = set(settings.digit_list())
    taken.add((settings.base_a, settings.base_b))
    for radius in range(1, 12):
        for a in range(-radius, radius + 1):
            for b in range(-radius, radius + 1):
                if max(abs(a), abs(b)) != radius:
                    continue
                if (a, b) not in taken:
                    return a, b
    return 1, 0


class IMAGINARYBASE_OT_digit_add(bpy.types.Operator):
    """Add a digit to the set"""

    bl_idname = "imaginarybase.digit_add"
    bl_label = "Add Digit"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        if len(settings.digits) >= 96:
            self.report({"WARNING"}, t("Digit limit reached"))
            return {"CANCELLED"}
        a, b = _free_digit(settings)
        item = settings.digits.add()
        item.a, item.b = a, b
        settings.active_digit = len(settings.digits) - 1
        _redraw()
        return {"FINISHED"}


class IMAGINARYBASE_OT_digit_remove(bpy.types.Operator):
    """Remove the last digit (0 and 1 stay: a numeral system needs both)"""

    bl_idname = "imaginarybase.digit_remove"
    bl_label = "Remove Digit"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        if len(settings.digits) <= 2:
            self.report({"WARNING"}, t("At least two digits are needed"))
            return {"CANCELLED"}
        settings.digits.remove(len(settings.digits) - 1)
        _redraw()
        return {"FINISHED"}


class IMAGINARYBASE_OT_place_step(bpy.types.Operator):
    """Change the number of places by one"""

    bl_idname = "imaginarybase.place_step"
    bl_label = "Step Places"
    bl_options = {"REGISTER", "UNDO"}

    delta: bpy.props.IntProperty(name="Delta", default=1, min=-1, max=1)

    def execute(self, context):
        settings = _settings(context)
        ceiling = ring.max_places(max(2, len(settings.digits)), settings.point_budget)
        target = max(1, min(ceiling, int(settings.places) + int(self.delta)))
        if target == settings.places:
            self.report(
                {"INFO"},
                tfmt("The point budget allows {places} places", places=ceiling),
            )
            return {"CANCELLED"}
        settings.places = target
        _redraw()
        return {"FINISHED"}


class IMAGINARYBASE_OT_reset_digits(bpy.types.Operator):
    """Drop back to the classic 0, 1, i, −1+i digits of base 2i"""

    bl_idname = "imaginarybase.reset_digits"
    bl_label = "Reset Digits"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        settings.set_base(0, 2)
        settings.apply_digits([(0, 0), (1, 0), (0, 1), (-1, 1)])
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# HUD 模态
# ---------------------------------------------------------------------------


class IMAGINARYBASE_OT_hud(bpy.types.Operator):
    """Edit the numeral system in the viewport HUD"""

    bl_idname = "imaginarybase.hud"
    bl_label = "Numeral System HUD"
    bl_options = {"REGISTER"}  # 不进撤销栈：它只改参数，生成才是动作

    def invoke(self, context, event):
        if context.area is None or context.area.type != "VIEW_3D":
            self.report({"WARNING"}, t("The HUD only runs in a 3D viewport"))
            return {"CANCELLED"}
        if hud.session() is not None:
            # 已经开着：再点一次面板按钮就是收起来（与 × / Q / Esc 等效）。
            # 少了这个分支，点击会走到下面再挂一个 modal handler —— HUD 关不掉，
            # 光标还被重新设成十字。
            self._finish(context)
            return {"FINISHED"}
        hud.begin(context)
        context.window_manager.modal_handler_add(self)
        context.window.cursor_set("CROSSHAIR")
        return {"RUNNING_MODAL"}

    # --- 事件 -------------------------------------------------------------

    def modal(self, context, event):
        """**不抢视口**的模态：只有指针落在面板上（或正在拖）时才吃事件。

        照 `fractal_family` 的规则：早期版本把所有事件都吃掉，HUD 一开就没法摆视角、
        选中物体、用别的面板，等于视口被锁住。现在：

        * 指针在面板外：一律 ``PASS_THROUGH``，视口与快捷键照常；
        * 指针在面板内：点击 / 滚轮 / 面板上写明的按键归 HUD；
        * 正在拖标记：不管指针跑到哪都归 HUD —— 否则松手事件被别人吃掉，标记会卡住。
        """
        session = hud.session()
        if session is None:
            self._finish(context)
            return {"CANCELLED"}
        settings = _settings(context)
        if settings is None:
            self._finish(context)
            return {"CANCELLED"}

        layout = hud.layout_for(context) or {}
        hotspots = layout.get("hotspots") or []
        mouse = (event.mouse_region_x, event.mouse_region_y)
        inside = hud_layout.inside_panel(layout.get("panel"), *mouse)
        dragging = bool(session.get("drag"))

        if event.type == "MOUSEMOVE":
            self._hover(context, session, hotspots, mouse)
            if not dragging:
                return {"PASS_THROUGH"}
            self._drag(settings, session, layout, mouse)
            return {"RUNNING_MODAL"}

        if event.type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            if not inside:
                return {"PASS_THROUGH"}
            if event.shift:
                self._zoom(
                    session, layout, 1.12 if event.type == "WHEELUPMOUSE" else 1 / 1.12
                )
            else:
                bpy.ops.imaginarybase.place_step(
                    delta=1 if event.type == "WHEELUPMOUSE" else -1
                )
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE":
            if event.value == "PRESS":
                spot = hud_layout.hit(hotspots, *mouse)
                if spot is None:
                    # 面板是实心的：点面板空白处不该穿透去选中它后面的物体
                    return {"RUNNING_MODAL"} if inside else {"PASS_THROUGH"}
                if spot["kind"] in {"digit", "base"}:
                    session["drag"] = spot["id"]
                    context.window.cursor_set("SCROLL_XY")
                    context.area.tag_redraw()
                    return {"RUNNING_MODAL"}
                self._press(context, settings, spot)
                return {"RUNNING_MODAL"}
            if not dragging:
                return {"PASS_THROUGH"}
            session["drag"] = ""
            context.window.cursor_set("CROSSHAIR")
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        if event.type in {"RIGHTMOUSE", "ESC"} and event.value == "PRESS":
            if not inside and not dragging:
                return {"PASS_THROUGH"}
            self._finish(context)
            return {"CANCELLED"}

        if event.value != "PRESS":
            return {"PASS_THROUGH"}

        # 键盘：只有指针停在面板上才接管，否则让视口快捷键照常工作
        if not inside and not dragging:
            return {"PASS_THROUGH"}

        if event.type == "F":
            # F = 只框数字（改数字），Shift+F = 连点集一起框（看形状）
            hud.reset_view("all" if event.shift else "digits")
        elif event.type == "TAB":
            settings.domain = "W" if settings.domain == "I" else "I"
        elif event.type == "A":
            bpy.ops.imaginarybase.digit_add()
        elif event.type == "X":
            bpy.ops.imaginarybase.digit_remove()
        elif event.type == "Q":
            self._finish(context)
            return {"CANCELLED"}
        context.area.tag_redraw()
        return {"RUNNING_MODAL"}

    def _hover(self, context, session, hotspots, mouse) -> None:
        spot = hud_layout.hit(hotspots, *mouse)
        ident = spot["id"] if spot else ""
        if ident != session.get("hover"):
            session["hover"] = ident
            context.area.tag_redraw()

    # --- 动作 -------------------------------------------------------------

    def _finish(self, context) -> None:
        try:
            context.window.cursor_set("DEFAULT")
        except AttributeError:
            pass
        hud.end(context)

    def _zoom(self, session, layout, factor: float) -> None:
        zoom = float(session.get("zoom") or layout.get("zoom") or 22.0)
        session["zoom"] = max(ZOOM_MIN, min(ZOOM_MAX, zoom * factor))

    def _drag(self, settings, session, layout, mouse) -> None:
        """把被拖的标记放到最近的格点上（**总是吸附**，这类图只有整数才有意义）。"""
        zoom = float(session.get("zoom") or layout.get("zoom") or 22.0)
        origin = layout.get("lattice_origin") or (0.0, 0.0)
        a, b = hud_layout.snap(
            settings.domain, mouse[0], mouse[1], origin[0], origin[1], zoom
        )
        ident = session["drag"]
        if ident == "base":
            if (a, b) == (0, 0):
                _notice(settings, t("The base cannot be zero"))
                return
            settings.set_base(a, b)
            _notice(settings, "")
            return
        index = int(ident.split(":")[1])
        if (settings.digits[index].a, settings.digits[index].b) == (a, b):
            return
        if not settings.move_digit(index, a, b):
            _notice(settings, t("That lattice point is already taken"))
        else:
            _notice(settings, "")

    def _press(self, context, settings, spot) -> None:
        ident = spot["id"]
        if ident == "btn:close":
            self._finish(context)
        elif ident == "btn:fit":
            hud.reset_view()  # 在两个取景档之间切换
        elif ident.startswith("btn:ring:"):
            settings.domain = ident.rsplit(":", 1)[1]
        elif ident == "btn:places:+":
            bpy.ops.imaginarybase.place_step(delta=1)
        elif ident == "btn:places:-":
            bpy.ops.imaginarybase.place_step(delta=-1)
        elif ident == "btn:add":
            bpy.ops.imaginarybase.digit_add()
        elif ident == "btn:remove":
            bpy.ops.imaginarybase.digit_remove()
        context.area.tag_redraw()


classes = (
    IMAGINARYBASE_OT_generate,
    IMAGINARYBASE_OT_remove,
    IMAGINARYBASE_OT_fit,
    IMAGINARYBASE_OT_digit_add,
    IMAGINARYBASE_OT_digit_remove,
    IMAGINARYBASE_OT_place_step,
    IMAGINARYBASE_OT_reset_digits,
    IMAGINARYBASE_OT_hud,
)
