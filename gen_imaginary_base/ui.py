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

        self._system(layout, settings)
        self._places(layout, settings)
        self._output(layout, settings, context)
        self._status(layout, settings)

    # --- 数系 -------------------------------------------------------------

    def _system(self, layout, settings):
        box = _box(layout, t("Numeral System"), "PRESET")
        box.prop(settings, "preset", text="")
        box.prop(settings, "domain", text="")

        row = box.row(align=True)
        row.prop(settings, "base_a", text=t("Base Real"))
        row.prop(settings, "base_b", text=t("Base Imaginary"))
        base = ring.format_qint(settings.domain, settings.base_a, settings.base_b)
        box.label(
            text=tfmt(
                "base {base} · scaling √{scale}",
                base=base,
                scale=round(
                    float(
                        ring.QInt(
                            settings.base_a, settings.base_b, settings.domain
                        ).norm
                    )
                    ** 0.5,
                    2,
                ),
            ),
            icon="DRIVER",
        )

        head = box.row(align=True)
        head.label(text=tfmt("Digits ({count})", count=len(settings.digits)))
        head.operator("imaginarybase.digit_add", text="", icon="ADD")
        head.operator("imaginarybase.digit_remove", text="", icon="REMOVE")

        box.template_list(
            "IMAGINARYBASE_UL_digits",
            "",
            settings,
            "digits",
            settings,
            "active_digit",
            rows=min(6, max(2, len(settings.digits))),
        )
        index = min(settings.active_digit, max(0, len(settings.digits) - 1))
        if len(settings.digits):
            item = settings.digits[index]
            row = box.row(align=True)
            row.enabled = index != 0  # 0 号恒为零，不可改
            row.prop(item, "a", text=t("A"))
            row.prop(item, "b", text=t("B"))
        box.operator(
            "imaginarybase.reset_digits", text=t("Reset to 2i Digits"), icon="LOOP_BACK"
        )

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

    def _output(self, layout, settings, context):
        box = _box(layout, t("Output"), "GREASEPENCIL")
        col = box.column(align=True)
        col.prop(settings, "point_budget", text=t("Point Budget"))
        col.prop(settings, "dot_radius", text=t("Dot Radius"), slider=True)

        row = box.row(align=True)
        row.scale_y = 1.4
        row.operator("imaginarybase.generate", text=t("Generate"), icon="PLAY")
        row.operator("imaginarybase.remove", text="", icon="TRASH")

        row = box.row(align=True)
        row.operator("imaginarybase.fit", text=t("Frame in Viewport"), icon="VIEWZOOM")
        row.operator(
            "imaginarybase.hud", text=t("HUD"), icon="WINDOW", depress=hud.active()
        )
        box.prop(settings, "show_preview", text=t("Viewport Preview"))

    # --- 状态 -------------------------------------------------------------

    def _status(self, layout, settings):
        box = layout.box()
        check = settings.canonical()
        if check["ok"]:
            box.label(text=t("Canonical numeral system"), icon="CHECKMARK")
        elif check["reason"]:
            box.label(text=check["reason"], icon="INFO")
        data = settings.point_data()
        box.label(
            text=tfmt(
                "places {places} · {count} points",
                places=data["places"],
                count=data["count"],
            ),
            icon="INFO",
        )
        if settings.notice:
            box.label(text=settings.notice, icon="ERROR")


classes = (IMAGINARYBASE_UL_digits, IMAGINARYBASE_PT_panel)
