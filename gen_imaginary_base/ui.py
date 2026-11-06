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
    """数字集列表：每行 ``#NN  [实部] [虚部]``。

    a / b 就地编辑（照商店版的 gene 列表），数字框占满序号以外的整行；
    0 号行禁改 —— 它是数系的原点，恒为零。
    """

    bl_idname = "IMAGINARYBASE_UL_digits"

    def draw_item(
        self, context, layout, data, item, icon, active_data, active_propname, index
    ):
        # 左侧序号列定宽（比例即等效定宽，UIList 每行等宽），其余全给实部 / 虚部。
        head = layout.split(factor=0.16, align=True)
        head.label(text=f"#{index:02d}")
        fields = head.row(align=True)
        fields.enabled = index != 0  # 0 号恒为零，是数系的原点，不可改
        fields.prop(item, "a", text="")
        fields.prop(item, "b", text="")


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
        box = _box(layout, t("Editing"), "GREASEPENCIL")
        # 两个视口开关并排、等宽、放在最上面 —— 预览开关原先是埋在「输出」里的一个
        # 复选项，很难找。``split(factor=0.5)`` 才是真正的等宽：普通 row 会按文字长度
        # 分配，两个按钮看起来宽窄不一。
        split = box.split(factor=0.5, align=True)
        split.scale_y = 1.4
        split.operator(
            "imaginarybase.hud",
            text=t("HUD"),
            icon="WORKSPACE",
            depress=hud.active(),
        )
        split.prop(
            settings, "show_preview", text=t("Preview"), toggle=True, icon="HIDE_OFF"
        )
        box.label(
            text=t("Drag a digit on the lattice · wheel = places · Tab = field"),
            icon="INFO",
        )

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
        # 实部 / 虚部直接在列表行里就地编辑（见 IMAGINARYBASE_UL_digits），
        # 列表底下不再放一对 A/B 编辑框。

    # --- 位数与上色 -------------------------------------------------------

    def _places(self, layout, settings):
        box = _box(layout, t("Places & Colour"), "COLOR")
        # 上限跟着「数字个数 × 点数预算」变，直接写进标签的括号里，滑杆占满整行。
        # 右边不再挂单独的 "max N" 标签 —— 那会把滑杆挤成半行。
        ceiling = ring.max_places(max(2, len(settings.digits)), settings.point_budget)
        box.prop(
            settings,
            "places",
            text=tfmt("Places (max {max})", max=ceiling),
        )

        box.prop(settings, "colour_mode", text=t("Colour By"), expand=True)
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
