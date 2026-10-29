"""侧栏（N 面板）「Torus Knot」：生成按钮 + 全部参数 + 状态 + 数据接口说明。

面板只读 ``scene.gen_torus_knot`` 与生成对象上的自定义属性，不做任何几何计算；
「待重新生成」也是从 ``settings.dirty`` 来的，不是在绘制时去比对参数 —— 画界面不该有副作用。
"""

from __future__ import annotations

import bpy

from . import gp, tube
from .knot import ADAPTIVE_DENSITY, ATTR_U, ATTR_V
from .ops import (
    GENTK_OT_delete,
    GENTK_OT_generate,
    GENTK_OT_setup_scene,
    GENTK_OT_sync_from_object,
)

__all__ = ["GENTK_PT_panel"]

#: 侧栏标签页名。
CATEGORY = "Torus Knot"


def _row(layout, label: str, icon: str = "NONE"):
    """一个盒子：首行是标题，后面照 ``GENTK_PT_panel`` 的排版写属性。"""
    box = layout.box()
    box.use_property_split = True
    box.use_property_decorate = False
    box.label(text=label, icon=icon)
    return box


def _status(layout, settings, obj) -> None:
    box = _row(layout, "状态", "INFO")

    if settings.auto_update:
        box.label(text="自动更新：改参数即刻重算", icon="PLAY")
    else:
        box.label(text="自动更新已关（改完点「生成」）", icon="INFO")

    if settings.error:
        box.label(text=settings.error, icon="ERROR")
    elif obj is None:
        box.label(text="还没有结对象，点「生成」建一个", icon="INFO")
    elif settings.dirty:
        box.label(text="参数改过，正在自动更新…", icon="TIME")
    else:
        box.label(text="已生成", icon="CHECKMARK")

    if obj is None:
        return

    count = tube.modifier(obj)
    tube_state = "管状实体" if count is not None and count.show_viewport else "笔划"
    box.label(
        text=f"{settings.last_points} 点 · {settings.last_strands} 股 · {tube_state}",
        icon="PRESET",
    )
    box.label(
        text=f"分段 {settings.last_resolution} · {settings.last_ms:.0f} ms",
        icon="TIME",
    )
    if settings.last_note:
        box.label(text=settings.last_note, icon="SHADING_SOLID")


def _knot(layout, settings) -> None:
    box = _row(layout, "(p, q) 结", "MESH_TORUS")

    row = box.row(align=True)
    row.prop(settings, "flip_p", icon="TRIA_DOWN_BAR" if settings.flip_p else "TRIA_UP_BAR")
    row.prop(settings, "p")

    row = box.row(align=True)
    row.prop(settings, "flip_q", icon="TRIA_DOWN_BAR" if settings.flip_q else "TRIA_UP_BAR")
    row.prop(settings, "q")

    box.prop(settings, "multiple_links")
    box.label(text=f"股数 {settings.links}", icon="DUPLICATE")


def _parameterisation(layout, settings) -> None:
    box = _row(layout, "参数化", "CON_FOLLOWPATH")
    box.prop(settings, "u_mult")
    box.prop(settings, "v_mult")
    box.prop(settings, "revolution_phase")
    box.prop(settings, "spin_phase")


def _size(layout, settings) -> None:
    box = _row(layout, "尺寸", "ARROW_LEFTRIGHT")
    box.prop(settings, "major")
    box.prop(settings, "minor")
    box.prop(settings, "scale")
    box.prop(settings, "height")


def _resolution(layout, settings) -> None:
    box = _row(layout, "分段", "MOD_DECIM")
    box.prop(settings, "adaptive")
    if settings.adaptive:
        box.label(text=f"按形状算（每单位 {ADAPTIVE_DENSITY} 点）", icon="PRESET")
        box.label(text=f"当前 {settings.effective_resolution()} 段", icon="CHECKMARK")
    else:
        box.prop(settings, "resolution")


def _look(layout, settings) -> None:
    box = _row(layout, "外观", "LINE_DATA")
    # 线宽 / 线色只管笔划；开了管状实体笔划就不画了，这两项当场失效
    column = box.column()
    column.enabled = not settings.tube
    column.prop(settings, "width")
    column.prop(settings, "colour")
    box.label(text="笔划的粗细与颜色（管状实体下不生效）", icon="INFO")


def _tube(layout, settings) -> None:
    box = _row(layout, "管状实体", "GEOMETRY_NODES")
    box.prop(settings, "tube")
    column = box.column()
    column.enabled = bool(settings.tube)
    column.prop(settings, "tube_radius")
    column.prop(settings, "tube_resolution")
    box.label(text="几何节点：笔划 → 曲线 → 扫管（实体网格）", icon="INFO")


def _tools(layout, settings) -> None:
    box = _row(layout, "工具", "TOOL_SETTINGS")
    box.operator(GENTK_OT_setup_scene.bl_idname, icon="CAMERA_DATA")
    box.operator(GENTK_OT_sync_from_object.bl_idname, icon="IMPORT")
    box.operator(GENTK_OT_delete.bl_idname, icon="TRASH")


def _interface(layout, settings) -> None:
    box = _row(layout, "数据接口", "PROPERTIES")
    box.label(text="几何核心的属性（vertex / POINT 域）", icon="INFO")
    box.label(text=f"{ATTR_U}   float · 绕主圆进度 [0,1) | {ATTR_V}   float · 绕副圆进度 [0,1)")
    box.label(text="扫成实体管后这两条属性跟着到网格顶点上", icon="CHECKMARK")


class GENTK_PT_panel(bpy.types.Panel):
    """侧栏面板。"""

    bl_idname = "GENTK_PT_panel"
    bl_label = "Gen Torus Knot"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY

    def draw(self, context):
        settings = context.scene.gen_torus_knot
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        head = layout.row(align=True)
        head.scale_y = 1.4
        head.operator(GENTK_OT_generate.bl_idname, icon="PLAY", text="生成")
        # 自动更新：几何参数一改就重算结，不用再点「生成」
        head.prop(settings, "auto_update", text="自动", toggle=True)

        _status(layout, settings, gp.find_object())
        _knot(layout, settings)
        _parameterisation(layout, settings)
        _size(layout, settings)
        _resolution(layout, settings)
        _look(layout, settings)
        _tube(layout, settings)
        _tools(layout, settings)
        _interface(layout, settings)