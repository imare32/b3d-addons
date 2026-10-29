"""场景参数：``scene.gen_torus_knot``。

面板上所有可调项都在这里；算子只读这套值，几何后端（``gp`` / ``tube``）只认算子算好的
分段与半径。

参数分两档，update 回调也不同：

* **几何档**（``p`` / ``q`` / 材质化参数 / 尺寸 / 分段 ……）改的是坐标，必须重新生成；
* **就地档**（线宽 / 线色 / 管状实体开关 / 管半径 / 圆周分段）改的是属性或几何节点输入，
  拖滑块是实时的，不重算点。

几何档改完**不必**再点「生成」：``auto_update`` 开着时，改动会排一次防抖 timer
（见 :mod:`live`），松手就自动重算到对象上；关掉就退回「点按钮生成」的老样子。

**不要**加 ``from __future__ import annotations``：``bpy.props`` 的注解必须是真实的
``Property`` 对象，PEP 563 的字符串注解会让注册失败。
"""

from contextlib import contextmanager

import bpy

from . import gp, tube
from .knot import adaptive_resolution, link_count
from .palette import INK, hex_to_rgb, srgb_to_linear

__all__ = [
    "DESIGN_KEYS",
    "GEOMETRY_FIELDS",
    "LIVE_FIELDS",
    "GenTorusKnotSettings",
    "read_design",
    "store_design",
    "suspend_auto_update",
]

#: 改坐标的一档：动它必须重新生成。
GEOMETRY_FIELDS = (
    "p",
    "q",
    "flip_p",
    "flip_q",
    "multiple_links",
    "u_mult",
    "v_mult",
    "revolution_phase",
    "spin_phase",
    "major",
    "minor",
    "scale",
    "height",
    "resolution",
    "adaptive",
)

#: 就地生效的一档：只改属性 / 几何节点输入。
LIVE_FIELDS = (
    "width",
    "colour",
    "tube",
    "tube_radius",
    "tube_resolution",
)

#: 面板字段 → 写到生成对象上的自定义属性名（换文件 / 换场景后仍能取回参数）。
DESIGN_KEYS = {
    name: f"gen_torus_knot_{name}" for name in GEOMETRY_FIELDS + LIVE_FIELDS
}

#: 线的默认色：色板的墨玄（sRGB 0..1，写进 GP 属性前由 ``gp`` 转线性）。
HEX_INK = hex_to_rgb(INK)

#: 回读参数时挂起自动更新的层数（见 :func:`suspend_auto_update`）。
_suspend_depth = 0


@contextmanager
def suspend_auto_update():
    """挂起自动更新：期间改参数不排自动重算。

    回读参数（:func:`read_design`）时用 —— 回读的值就是这一根生成时用的值，几何本来
    就与它相符，再重算一遍是白算。
    """
    global _suspend_depth
    _suspend_depth += 1
    try:
        yield
    finally:
        _suspend_depth -= 1


def _mark_dirty(self, context):
    """几何参数变了：点必须重算，标一下「待重新生成」，并排一次自动重算。"""
    self.dirty = True
    if _suspend_depth or not self.auto_update:
        return
    # 函数内 import：live → builder → props 是个环，模块级 import 会死锁
    from . import live

    live.request(self)


def _toggle_auto(self, context):
    """自动更新开关：开就立刻补一次（把对象追到当前参数），关就撤掉待跑的重算。"""
    # 函数内 import：理由同 :func:`_mark_dirty`
    from . import live

    if self.auto_update:
        live.request(self)
    else:
        live.cancel()


def _apply_width(self, context):
    """线宽只进 GP 的 ``radius`` 属性：就地改，笔划数一点不变。"""
    obj = gp.find_object()
    if obj is None:
        return
    if gp.set_layer_radius(obj, float(self.width)):
        obj[DESIGN_KEYS["width"]] = float(self.width)


def _apply_colour(self, context):
    """线色：GP 笔划改 ``vertex_color``，管材质改基色。"""
    linear = self.linear_colour()
    obj = gp.find_object()
    if obj is None:
        return
    # GP 属性按线性存：面板是 sRGB，这里转过去
    gp.set_colour(obj, tuple(self.colour))
    obj[DESIGN_KEYS["colour"]] = list(self.colour)
    if tube.modifier(obj) is not None:
        tube.material(linear)


def _apply_tube(self, context):
    """开关管状实体：修改器一直在（参数可回读），开的时候才写输入并显示。"""
    obj = gp.find_object()
    if obj is None:
        return
    if self.tube:
        found = tube.ensure_modifier(obj, float(self.tube_radius), int(self.tube_resolution))
        tube.set_geometry_material(found.node_group, tube.material(self.linear_colour()))
    tube.set_enabled(obj, bool(self.tube))
    obj[DESIGN_KEYS["tube"]] = bool(self.tube)


def _apply_tube_radius(self, context):
    """管半径只进修改器输入：就地改，拖滑块是实时的。"""
    obj = gp.find_object()
    if obj is None:
        return
    tube.set_radius(obj, float(self.tube_radius))
    obj[DESIGN_KEYS["tube_radius"]] = float(self.tube_radius)


def _apply_tube_resolution(self, context):
    """圆周分段同样只进修改器输入。"""
    obj = gp.find_object()
    if obj is None:
        return
    tube.set_resolution(obj, int(self.tube_resolution))
    obj[DESIGN_KEYS["tube_resolution"]] = int(self.tube_resolution)


class GenTorusKnotSettings(bpy.types.PropertyGroup):
    """``scene.gen_torus_knot``：一次生成所需的全部输入。"""

    # --- 结形状（p、q 带符号，负号就是「翻转」） ---------------------------

    p: bpy.props.IntProperty(
        name="p",
        description="绕主圆的圈数（带符号，负数 = 反着绕）；与 q 互质时才是单股真结",
        default=2,
        min=-64,
        soft_min=-13,
        soft_max=13,
        update=_mark_dirty,
    )

    q: bpy.props.IntProperty(
        name="q",
        description="绕副圆的圈数（带符号）",
        default=3,
        min=-64,
        soft_min=-13,
        soft_max=13,
        update=_mark_dirty,
    )

    flip_p: bpy.props.BoolProperty(
        name="翻转 p",
        description="把 p 取负数（3₁ 结的镜像），与直接填负数等价",
        default=False,
        update=_mark_dirty,
    )

    flip_q: bpy.props.BoolProperty(
        name="翻转 q",
        default=False,
        update=_mark_dirty,
    )

    multiple_links: bpy.props.BoolProperty(
        name="多股",
        description="p、q 不互质时，结会裂成 gcd(p, q) 条同轴的股；关掉就恒为一条"
        "（图形不变，只是把各股首尾连成一条来回绕的曲线——闭合笔划会变成 8 字形），"
        "默认开",
        default=True,
        update=_mark_dirty,
    )

    # --- 参数化 -----------------------------------------------------------

    u_mult: bpy.props.IntProperty(
        name="绕转倍数",
        description="theta 的额外整数倍：结会在主向上多缠几圈（改的是参数化，不是 p）",
        default=1,
        min=-64,
        soft_min=-13,
        soft_max=13,
        update=_mark_dirty,
    )

    v_mult: bpy.props.IntProperty(
        name="自旋倍数",
        description="phi 的额外整数倍",
        default=1,
        min=-64,
        soft_min=-13,
        soft_max=13,
        update=_mark_dirty,
    )

    revolution_phase: bpy.props.FloatProperty(
        name="绕转相位",
        description="theta 的起始相位（弧度）：整体转一下，形状不变",
        default=0.0,
        soft_min=-6.28319,
        soft_max=6.28319,
        precision=3,
        update=_mark_dirty,
    )

    spin_phase: bpy.props.FloatProperty(
        name="自旋相位",
        description="phi 的起始相位（弧度）：让整根结沿着管的截面转一下",
        default=0.0,
        soft_min=-6.28319,
        soft_max=6.28319,
        precision=3,
        update=_mark_dirty,
    )

    # --- 尺寸 -------------------------------------------------------------

    major: bpy.props.FloatProperty(
        name="主半径",
        description="环面的主半径 R（中心到管轴）",
        default=1.0,
        min=0.0,
        soft_max=4.0,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_mark_dirty,
    )

    minor: bpy.props.FloatProperty(
        name="副半径",
        description="环面的副半径 r（管的粗细）；大于主半径时结会自穿",
        default=0.25,
        min=0.0,
        soft_max=1.0,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_mark_dirty,
    )

    scale: bpy.props.FloatProperty(
        name="整体缩放",
        description="同时乘主 / 副半径（点坐标与线宽、管半径不用跟着改）",
        default=1.0,
        min=0.01,
        soft_max=4.0,
        precision=3,
        update=_mark_dirty,
    )

    height: bpy.props.FloatProperty(
        name="高度",
        description="z 方向的拉伸：1 = 正圆截面，0 = 压成平面曲线，>1 = 拉成高塔",
        default=1.0,
        soft_min=0.0,
        soft_max=4.0,
        precision=3,
        update=_mark_dirty,
    )

    # --- 分段 -------------------------------------------------------------

    resolution: bpy.props.IntProperty(
        name="分段",
        description="每股的点数 + 1：100 已够圆滑；线宽与管半径是绝对尺寸，改分段只影响圆滑度",
        default=100,
        min=3,
        soft_max=512,
        update=_mark_dirty,
    )

    adaptive: bpy.props.BoolProperty(
        name="自动分段",
        description="按结的平均周长定分段（每单位 8 个点，最少 3）：p、q 或半径一改，"
        "点数自动跟上，手动分段框失效",
        default=False,
        update=_mark_dirty,
    )

    # --- 外观（就地生效） -------------------------------------------------

    width: bpy.props.FloatProperty(
        name="线宽",
        description="GP 笔划的粗细（半径，世界单位）：只写 POINT 域的 ``radius`` 属性，"
        "改它不用重算点。开了管状实体后这条线就不画了",
        default=0.03,
        min=0.0,
        soft_max=0.2,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_apply_width,
    )

    colour: bpy.props.FloatVectorProperty(
        name="线色",
        description="笔划与管共用的颜色（默认墨玄）；GP 属性按线性存，面板显示的是 sRGB",
        size=3,
        subtype="COLOR_GAMMA",
        default=HEX_INK,
        min=0.0,
        max=1.0,
        update=_apply_colour,
    )

    # --- 管状实体（就地生效） ---------------------------------------------

    tube: bpy.props.BoolProperty(
        name="管状实体",
        description="用几何节点（Grease Pencil to Curves → Curve to Mesh）把笔划扫成"
        "实体管；关掉就回到笔划。修改器一直在，开关只是切换可见性",
        default=False,
        update=_apply_tube,
    )

    tube_radius: bpy.props.FloatProperty(
        name="管半径",
        description="扫出来的管的半径（世界单位）：驱动截面圆环，只进修改器输入",
        default=0.04,
        min=0.0001,
        soft_max=0.5,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_apply_tube_radius,
    )

    tube_resolution: bpy.props.IntProperty(
        name="圆周分段",
        description="管截面的顶点数：8 已经够圆，16 更圆但面数翻倍（只进修改器输入）",
        default=8,
        min=3,
        max=64,
        update=_apply_tube_resolution,
    )

    # --- 自动更新 ---------------------------------------------------------

    auto_update: bpy.props.BoolProperty(
        name="自动更新",
        description="几何参数一改就自动重算结（松手后约 0.08 s，拖滑块时会合并成一次）；"
        "关掉就退回「改完点『生成』」的老样子",
        default=True,
        update=_toggle_auto,
    )

    # --- 状态（只读） -----------------------------------------------------

    dirty: bpy.props.BoolProperty(
        name="待生成",
        description="几何参数改过但还没重新生成",
        default=True,
    )

    last_points: bpy.props.IntProperty(
        name="点数",
        default=0,
    )

    last_strands: bpy.props.IntProperty(
        name="股数",
        default=0,
    )

    last_resolution: bpy.props.IntProperty(
        name="实际分段",
        description="这一根实际用到的分段（开了自动分段时和面板里的手动值不同）",
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

    # --- 派生 -------------------------------------------------------------

    @property
    def signed_p(self) -> int:
        """生效的 p（含「翻转」）。"""
        return int(self.p) * (-1 if self.flip_p else 1)

    @property
    def signed_q(self) -> int:
        """生效的 q（含「翻转」）。"""
        return int(self.q) * (-1 if self.flip_q else 1)

    @property
    def links(self) -> int:
        """股数（用**未翻转**的 p、q 算，符号只决定绕向）。"""
        return link_count(self.p, self.q, self.multiple_links)

    @property
    def stroke_colour(self) -> tuple:
        """给 ``gp`` 的颜色：sRGB（它会转线性）。"""
        return tuple(self.colour)

    def linear_colour(self) -> tuple:
        """给材质的颜色：线性。"""
        return tuple(srgb_to_linear(component) for component in self.colour)

    def effective_resolution(self) -> int:
        """这一根实际用的分段（自动分段时按形状算）。"""
        if not self.adaptive:
            return int(self.resolution)
        return adaptive_resolution(
            self.p, self.q, float(self.major), float(self.minor), self.links
        )

    def knot_arguments(self) -> dict:
        """交给 :func:`gen_torus_knot.knot.build` 的一整套参数。"""
        return {
            "p": self.signed_p,
            "q": self.signed_q,
            "u_mult": int(self.u_mult),
            "v_mult": int(self.v_mult),
            "major": float(self.major),
            "minor": float(self.minor),
            "height": float(self.height),
            "scale": float(self.scale),
            "revolution_phase": float(self.revolution_phase),
            "spin_phase": float(self.spin_phase),
            "links": self.links,
            "resolution": self.effective_resolution(),
        }


def _hard_range(name: str):
    """字段的硬上下限（``None`` 表示这头不限）。"""
    prop = GenTorusKnotSettings.bl_rna.properties.get(name)
    return getattr(prop, "hard_min", None), getattr(prop, "hard_max", None)


def _cast(name: str, value, current):
    """按当前值的类型把回读到的值转回去，并夹进硬范围。"""
    low, high = _hard_range(name)
    if isinstance(current, bool):
        return bool(value)
    if isinstance(current, int):
        number = int(value)
        return max(int(low), number) if low is not None else number
    if isinstance(current, float):
        number = float(value)
        if low is not None:
            number = max(float(low), number)
        if high is not None:
            number = min(float(high), number)
        return number
    numbers = [float(component) for component in value]
    if low is not None and high is not None:
        numbers = [min(max(float(low), number), float(high)) for number in numbers]
    return tuple(numbers)


def store_design(obj, settings) -> None:
    """把面板参数记到生成对象上（换文件 / 换场景后还能回读）。"""
    for name in GEOMETRY_FIELDS + LIVE_FIELDS:
        value = getattr(settings, name)
        obj[DESIGN_KEYS[name]] = list(value) if hasattr(value, "__len__") else value


def read_design(obj, settings) -> int:
    """把生成对象上记的参数读回面板，返回读到几项。"""
    found = 0
    for name in GEOMETRY_FIELDS + LIVE_FIELDS:
        key = DESIGN_KEYS[name]
        if key not in obj:
            continue
        setattr(settings, name, _cast(name, obj[key], getattr(settings, name)))
        found += 1
    return found


def register():
    bpy.types.Scene.gen_torus_knot = bpy.props.PointerProperty(type=GenTorusKnotSettings)


def unregister():
    del bpy.types.Scene.gen_torus_knot