"""侧栏面板：``View3D > Sidebar > Fractal Base``。

面板是"看得全"的那一份（HUD 是"改得快"的那一份）：这里能改每一个数字的精确值、
看到数系是否有效、决定点数预算与点的粗细。
"""

from __future__ import annotations

import bpy

from . import hud, ring
from .i18n import t, tfmt

__all__ = ["classes"]


def _settings(context):
    return getattr(context.scene, "imaginarybase", None)


def _box(layout, title: str, icon: str = "NONE"):
    box = layout.box()
    row = box.row()
    row.label(text=title, icon=icon)
    return box


class IMAGINARYBASE_UL_digits(bpy.types.UIList):
    """数字集列表：每行 ``#k  a + bω``。"""

    bl_idname = "IMAGINARYBASE_UL_digits"

    def draw_item(
        self, context, layout, data, item, icon, active_data, active_propname, index
    ):
        settings = _settings(context)
        domain = settings.domain if settings is not None else "I"
        row = layout.row(align=True)
        row.label(text=f"{index:02d}", icon="DOT" if index else "RADIOBUT_OFF")
        text = ring.format_qint(domain, item.a, item.b)
        row.label(text=text)
        sub = row.row(align=True)
        sub.alignment = "RIGHT"
        sub.label(text=f"{item.a},{item.b}")


class IMAGINARYBASE_PT_panel(bpy.types.Panel):
    bl_idname = "IMAGINARYBASE_PT_panel"
    bl_label = "Fractal Base"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Fractal Base"

    def draw(self, context):
        layout = self.layout
        settings = _settings(context)
        if settings is None:
            layout.label(text=t("Settings not available"), icon="ERROR")
            return

        self._hud(layout, settings)
        self._system(layout, settings)
        self._digits(layout, settings)
        self._places(layout, settings)
        self._output(layout, settings)
        self._status(layout, settings)

    # --- HUD --------------------------------------------------------------

    def _hud(self, layout, settings):
        row = layout.row()
        row.scale_y = 1.55
        row.operator(
            "imaginarybase.hud",
            text=t("Open HUD"),
            icon="WINDOW",
            depress=hud.active(),
        )
        hint = layout.column(align=True)
        hint.scale_y = 0.85
        hint.label(text=t("Drag a digit on the lattice · wheel = places · Tab = field"))

    # --- 数系 -------------------------------------------------------------

    def _system(self, layout, settings):
        box = _box(layout, t("Numeral System"), "PRESET")
        box.prop(settings, "preset", text="")

        # 域做成两个并排按钮：切换 i / ω 是常用动作，藏在下拉里要多点一次
        box.row(align=True).prop(settings, "domain", expand=True)

        row = box.row(align=True)
        row.prop(settings, "base_a", text=t("Base Real"))
        row.prop(settings, "base_b", text=t("Base Imaginary"))

        column = box.column(align=True)
        column.label(
            text=tfmt(
                "base {base} · scaling √{scale}",
                base=ring.format_qint(
                    settings.domain, settings.base_a, settings.base_b
                ),
                scale=round(float(settings.base().norm) ** 0.5, 2),
            ),
            icon="DRIVER",
        )
        # 有效性紧挨着底数显示 —— 这是本插件比参考实现多出来的那一条判定，
        # 放在面板最底部就看不见了。
        check = settings.canonical()
        if check["ok"]:
            column.label(text=t("Every integer has one expansion"), icon="CHECKMARK")
        elif check["reason"]:
            column.label(text=check["reason"], icon="ERROR")

    # --- 数字集 -----------------------------------------------------------

    def _digits(self, layout, settings):
        box = _box(layout, tfmt("Digits ({count})", count=len(settings.digits)), "DOT")
        head = box.row(align=True)
        head.operator("imaginarybase.digit_add", text=t("Add"), icon="ADD")
        head.operator("imaginarybase.digit_remove", text=t("Remove"), icon="REMOVE")
        head.operator("imaginarybase.reset_digits", text="", icon="LOOP_BACK")

        box.template_list(
            "IMAGINARYBASE_UL_digits",
            "",
            settings,
            "digits",
            settings,
            "active_digit",
            rows=min(7, max(3, len(settings.digits))),
        )
        if len(settings.digits):
            index = min(settings.active_digit, max(0, len(settings.digits) - 1))
            item = settings.digits[index]
            row = box.row(align=True)
            row.enabled = index != 0  # 0 号恒为零，不可改
            row.prop(item, "a", text=t("A"))
            row.prop(item, "b", text=t("B"))

    # --- 位数与上色 -------------------------------------------------------

    def _places(self, layout, settings):
        box = _box(layout, t("Places & Colour"), "COLOR")
        ceiling = ring.max_places(max(2, len(settings.digits)), settings.point_budget)
        row = box.row(align=True)
        row.prop(settings, "places", text=t("Places"), slider=True)
        row.label(text=tfmt("max {max}", max=ceiling))

        box.prop(settings, "colour_mode", text=t("Colour By"))
        if settings.colour_mode == "PLACE":
            box.prop(settings, "colour_place", text=t("Place"), slider=True)

    # --- 输出 -------------------------------------------------------------

    def _output(self, layout, settings):
        box = _box(layout, t("Output"), "GREASEPENCIL")

        row = box.row(align=True)
        row.scale_y = 1.35
        row.operator("imaginarybase.generate", text=t("Generate"), icon="PLAY")
        row.operator("imaginarybase.remove", text="", icon="TRASH")

        box.operator("imaginarybase.fit", text=t("Frame in Viewport"), icon="VIEWZOOM")

        col = box.column(align=True)
        col.prop(settings, "show_preview", text=t("Viewport Preview"), toggle=True)
        col.prop(settings, "point_budget", text=t("Point Budget"))
        col.prop(settings, "dot_radius", text=t("Dot Radius"), slider=True)

    # --- 状态 -------------------------------------------------------------

    def _status(self, layout, settings):
        box = layout.box()
        data = settings.point_data()
        row = box.row()
        row.label(
            text=tfmt(
                "{count} points · {places} places",
                count=data["count"],
                places=data["places"],
            ),
            icon="INFO",
        )
        if settings.notice:
            box.label(text=settings.notice, icon="ERROR")


classes = (IMAGINARYBASE_UL_digits, IMAGINARYBASE_PT_panel)
