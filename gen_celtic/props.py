"""场景参数：``scene.gen_celtic``。

面板上所有可调项都在这里，算子只读这套值，几何后端只认算子算好的半径。
**不要**加 ``from __future__ import annotations``：``bpy.props`` 的注解必须是
真实的 ``Property`` 对象，PEP 563 的字符串注解会让注册失败。
"""

import bpy

from . import tube
from .palette import OCHRE, hex_to_linear

__all__ = [
    "DESIGN_KEYS",
    "GenCelticSettings",
    "register",
    "unregister",
]

#: 参数 → 写到生成对象上的自定义属性名（换文件 / 换场景后仍能取回参数）。
#: ``radius`` / ``resolution`` 存的是**实际用到的**世界单位值（回读时按平均边长还原比例）。
DESIGN_KEYS = {
    "source": "gen_celtic_source",
    "lift": "gen_celtic_lift",
    "radius": "gen_celtic_radius",
    "resolution": "gen_celtic_resolution",
    "spline_resolution": "gen_celtic_spline_resolution",
    "signature": "gen_celtic_signature",
}


def _poll_source(_self, obj):
    """源物体只能是网格（曲线 / GP / 空物体编不出结）。"""
    return obj.type == "MESH"


def _mark_dirty(self, context):
    """几何参数变了：带子必须重编（会改坐标的东西都在这一档）。"""
    self.dirty = True


def _apply_radius(self, context):
    """管半径只进修改器输入：就地改，不重编带子，拖滑块是实时的。"""
    obj = tube.find_object()
    if obj is None:
        return
    radius = tube.stored_mean(obj) * float(self.radius)
    tube.set_radius(obj, radius)
    if DESIGN_KEYS["radius"] in obj:
        obj[DESIGN_KEYS["radius"]] = radius


def _apply_resolution(self, context):
    """圆周分段同样只进修改器输入。"""
    obj = tube.find_object()
    if obj is None:
        return
    tube.set_resolution(obj, int(self.resolution))
    if DESIGN_KEYS["resolution"] in obj:
        obj[DESIGN_KEYS["resolution"]] = int(self.resolution)


def _apply_spline_resolution(self, context):
    """绳路采样密度同样只进修改器输入。"""
    obj = tube.find_object()
    if obj is None:
        return
    tube.set_spline_resolution(obj, int(self.spline_resolution))
    if DESIGN_KEYS["spline_resolution"] in obj:
        obj[DESIGN_KEYS["spline_resolution"]] = int(self.spline_resolution)


def _apply_material_color(self, context):
    """绳色只改材质基色，几何一点不动。"""
    if tube.find_object() is None:
        return
    tube.material(tuple(self.material_color))


class GenCelticSettings(bpy.types.PropertyGroup):
    """``scene.gen_celtic``：一次编结所需的全部输入。"""

    # --- 输入 -------------------------------------------------------------

    source: bpy.props.PointerProperty(
        name="源网格",
        description="要编成凯尔特结的网格物体；在编辑模式里挪点，结会跟着重编",
        type=bpy.types.Object,
        poll=_poll_source,
        update=_mark_dirty,
    )

    # --- 几何（比例都按平均边长算，换尺度 / 换密度都不用重调） --------------

    lift: bpy.props.FloatProperty(
        name="抬升",
        description="交叉处上下起伏的幅度，单位是平均边长（抬升量 = 平均边长 × 比例）；"
        "0 = 全贴在面上，绳管会互相穿插。属于几何，改它要重编",
        default=0.35,
        min=0.0,
        soft_max=1.5,
        precision=2,
        update=_mark_dirty,
    )

    radius: bpy.props.FloatProperty(
        name="管半径",
        description="绳管半径，单位是平均边长（默认 0.22：比抬升小，交叉处才分得开）；"
        "只进几何节点输入，改它不用重编",
        default=0.22,
        min=0.01,
        soft_max=0.5,
        precision=2,
        update=_apply_radius,
    )

    resolution: bpy.props.IntProperty(
        name="圆周分段",
        description="圆环截面的顶点数：8 已经够圆，16 更圆但面数翻倍；只进几何节点输入",
        default=8,
        min=3,
        max=32,
        update=_apply_resolution,
    )

    spline_resolution: bpy.props.IntProperty(
        name="绳路采样密度",
        description="Bezier 绳路每段插多少点（默认 12）；输出面数 ≈ 源面数 × 4 × 圆周分段"
        " × 这个值，源网格很密（结面数上百万）时把它调小。只进几何节点输入",
        default=tube.SPLINE_RESOLUTION,
        min=1,
        max=32,
        update=_apply_spline_resolution,
    )

    # --- 实时更新 ---------------------------------------------------------

    live: bpy.props.BoolProperty(
        name="实时更新",
        description="编辑源网格时自动重编（每次更新先比对指纹，没变就直接跳过）；"
        "网格很大时重编本身很贵，关掉它手动点更新",
        default=True,
    )

    # --- 外观 -------------------------------------------------------------

    material_color: bpy.props.FloatVectorProperty(
        name="绳色",
        description="绳管的基色（色板的赭石，线性存储 —— 面板显示的色块就是它）",
        size=3,
        subtype="COLOR_GAMMA",
        default=hex_to_linear(OCHRE),
        min=0.0,
        max=1.0,
        update=_apply_material_color,
    )

    # --- 状态（只读） -----------------------------------------------------

    dirty: bpy.props.BoolProperty(
        name="待更新",
        description="参数改过但还没重编",
        default=True,
    )

    last_points: bpy.props.IntProperty(
        name="交点数",
        default=0,
    )

    last_strands: bpy.props.IntProperty(
        name="带子数",
        default=0,
    )

    last_conflicts: bpy.props.IntProperty(
        name="异常收尾数",
        default=0,
    )

    last_ms: bpy.props.FloatProperty(
        name="耗时",
        default=0.0,
    )

    last_note: bpy.props.StringProperty(
        name="提示",
        default="",
    )

    error: bpy.props.StringProperty(
        name="错误",
        default="",
    )


def register():
    bpy.types.Scene.gen_celtic = bpy.props.PointerProperty(type=GenCelticSettings)


def unregister():
    del bpy.types.Scene.gen_celtic