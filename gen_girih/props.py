"""场景参数：``scene.gen_girih``。

面板上所有可调项都在这里；算子与回读只读这套值。

参数分两档，update 回调也不同：

* **几何档**（镶嵌 / 接触角 / 间隙 / 单元边长 / 画布 / 样式）改的是"砖怎么摆、射线
  怎么打、出几层"，必须重新生成；
* **外观档**（线宽 / 描边倍数 / 两个颜色 / 不透明度 / 图层显隐）只重写 GP 属性
  （``radius`` / ``vertex_color`` / ``opacity`` / ``layer.hide``），拖滑块是实时的，
  一笔都不重算。

> 阶段三（交织）之后描边倍数会变成几何参数——缺口是按"可见半宽"算出来的。那时把它
> 挪进 ``GEOMETRY_FIELDS`` 即可，别在面板里留一个"看起来能实时但其实不会"的滑块。

**不要**加 ``from __future__ import annotations``：``bpy.props`` 的注解必须是真实的
``Property`` 对象，PEP 563 的字符串注解会让注册失败。
"""

import bpy

from . import girih, gp
from .palette import AMBER, INK, OCHRE, PAPER, hex_to_rgb
from .presets import PRESET_ITEMS, PRESET_PROJECT, preset_of
from .scene import (
    LAYER_BAND,
    LAYER_CASE,
    LAYER_LINES,
    LAYER_TILING,
    STYLE_INTERLACE,
    STYLE_LINES,
)

__all__ = [
    "APPEARANCE_FIELDS",
    "DESIGN_KEYS",
    "GEOMETRY_FIELDS",
    "GenGirihSettings",
    "STYLE_ITEMS",
    "TILING_BY_ID",
    "TILING_ITEMS",
    "register",
    "unregister",
]

#: 镶嵌的中文显示名（存进文件的是 ``TILING_BY_ID`` 里的标识符）。
TILING_LABELS = {
    "4.8.8": "八边形与正方形",
    "4.6.12": "十二边形、六边形与正方形",
    "3.12.12": "十二边形与三角形",
    "6.6.6": "六边形",
    "3.4.6.4": "六边形、正方形与三角形",
    "3.6.3.6": "六边形与三角形",
    "4.4.4.4": "正方形",
    "3.3.4.3.4": "扭棱正方形",
    "3.3.3.4.4": "正方形与三角形（成行）",
    "3.3.3.3.3.3": "三角形",
}

#: 顶点构型 → 一句话说明（面板下拉里的提示）。
TILING_NOTES = {
    "4.8.8": "八点星的标准来源，规则最干净",
    "4.6.12": "十二点玫瑰纹，参考实现的默认",
    "3.12.12": "十二边形连成链，星形很细",
    "6.6.6": "六点星，格子最稀疏",
    "3.4.6.4": "大马士革式的混合星",
    "3.6.3.6": "六边形与三角形交替，星形密而小",
    "4.4.4.4": "正方格，只有折线与方角",
    "3.3.4.3.4": "扭棱方格，线最密（生成最慢）",
    "3.3.3.4.4": "成行的正方形与三角形",
    "3.3.3.3.3.3": "全三角形，线最密",
}

#: 标识符一律 ASCII 且以字母开头（枚举标识符会被写进 RNA，别用 "4.6.12" 这种带点的）。
TILING_ITEMS = [
    (
        "T" + key.replace(".", "_"),
        f"{key} · {TILING_LABELS[key]}",
        TILING_NOTES[key],
    )
    for key in girih.TILINGS
]
TILING_BY_ID = {item[0]: item[0][1:].replace("_", ".") for item in TILING_ITEMS}

#: 样式。``scene.STYLES`` 里的每一项这里都要有。
STYLE_ITEMS = [
    ("LINES", "线条", "单层单色线，一笔到底，最省"),
    ("BANDS", "带子", "描边 + 本体两层：露出来的那一圈就是蕾丝边"),
    ("INTERLACE", "交织", "在交叉点分上下：压下的那一段切缺口、用补片贴平"),
]

#: 改砖的摆法、射线、层数、缺口、出不出填充/调试层 → 必须重新生成。
GEOMETRY_FIELDS = (
    "tiling",
    "angle",
    "gap",
    "size",
    "canvas",
    "style",
    "seam",
    "fills",
    "fill_colour",
    "fill_by_sides",
    "show_tiles",
)

#: 只改 GP 属性（或世界背景）→ 就地生效。
#:
#: ``palette`` 不在两档里：它只是"把一组颜色写进面板"的入口，颜色本身才是真相；
#: 回读时读颜色就够了，否则预设会反过来盖掉对象上记录的颜色。
APPEARANCE_FIELDS = ("width", "casing", "colour", "case_colour", "opacity", "canvas_colour")

#: 面板字段 → 写到生成对象上的自定义属性名（换文件 / 换场景后仍能取回参数）。
DESIGN_KEYS = {name: f"gen_girih_{name}" for name in GEOMETRY_FIELDS + APPEARANCE_FIELDS}


def _band_layer(style: str) -> str:
    """本体画在哪一层：只有线条样式是 ``Lines``，带子与交织都是 ``Band``。"""
    return LAYER_LINES if style == STYLE_LINES else LAYER_BAND


def _mark_dirty(self, context):
    """几何参数变了：标脏 + 排一次自动重建（0.25 s 防抖，拖动滑块只重建一次）。"""
    self.dirty = True
    # 延迟导入：ops 依赖本模块的 store/read，模块级导入会成环
    from . import ops

    ops.schedule_rebuild()


def _apply_canvas_colour(self, context):
    """画布底色只重铺世界背景，几何一个点都不用重算。"""
    from . import ops

    ops.repaint_canvas(context)


def _apply_palette(self, context):
    """切配色预设：整组颜色一起写进面板。

    分色表（按砖的边数取色）不在属性里，是随预设走的常量表，所以换预设后要重建一次
    填充才认得出新表——只在"确实在用填充 + 分色"时才排重建，其它情况纯改颜色。
    """
    from . import ops

    preset = preset_of(self.palette)
    self.canvas_colour = preset.canvas
    self.colour = preset.body
    self.case_colour = preset.case
    self.fill_colour = preset.fill
    self.fill_by_sides = bool(preset.by_sides)
    if self.fills and self.fill_by_sides:
        self.dirty = True
        ops.schedule_rebuild()


def _apply_auto_rebuild(self, context):
    """关掉「自动重建」时把已排上的那次撤掉；打开时若正脏就补排一次。"""
    from . import ops

    if self.auto_rebuild:
        ops.schedule_rebuild()
    else:
        ops.cancel_rebuild()


def _width_is_geometry(settings) -> bool:
    """交织样式里线宽是几何参数：缺口按"可见半宽"算，改宽度必须重算。"""
    return settings.style == STYLE_INTERLACE


def _apply_widths(self, context):
    """线宽（以及跟着它走的描边宽度）只重写 ``radius`` 属性。

    描边 = 线宽 × 倍数，所以线宽一动也要顺手把 ``Case`` 层改掉，否则两层会脱开。
    交织样式例外：缺口是按可见半宽切出来的，改宽度得重新生成。
    """
    if _width_is_geometry(self):
        self.dirty = True
        return
    obj = gp.find_object()
    if obj is None:
        return
    gp.set_layer_radius(obj, _band_layer(self.style), float(self.width))
    if self.style != STYLE_LINES:
        gp.set_layer_radius(obj, LAYER_CASE, float(self.width) * float(self.casing))
    obj[DESIGN_KEYS["width"]] = float(self.width)


def _apply_casing(self, context):
    """描边倍数：两层样式只改 ``Case`` 层的半径；交织样式得重新生成。

    交织的缺口按 ``max(线宽, 描边半径)`` 切，描边一变，刀口位置就变了。
    """
    if _width_is_geometry(self):
        self.dirty = True
        return
    obj = gp.find_object()
    if obj is None:
        return
    if self.style != STYLE_LINES:
        gp.set_layer_radius(obj, LAYER_CASE, float(self.width) * float(self.casing))
    obj[DESIGN_KEYS["casing"]] = float(self.casing)


def _apply_colour(self, context):
    """本体线色只重写本层的 ``vertex_color``。"""
    obj = gp.find_object()
    if obj is None:
        return
    if gp.set_colour(obj, _band_layer(self.style), tuple(self.colour)):
        obj[DESIGN_KEYS["colour"]] = list(self.colour)


def _apply_case_colour(self, context):
    """描边色只重写 ``Case`` 层。"""
    obj = gp.find_object()
    if obj is None:
        return
    if gp.set_colour(obj, LAYER_CASE, tuple(self.case_colour)):
        obj[DESIGN_KEYS["case_colour"]] = list(self.case_colour)


def _apply_opacity(self, context):
    """不透明度对两层一起生效（只改一层会露出"半透的描边"）。"""
    obj = gp.find_object()
    if obj is None:
        return
    layers = (LAYER_LINES,) if self.style == STYLE_LINES else (LAYER_CASE, LAYER_BAND)
    for layer in layers:
        gp.set_opacity(obj, layer, float(self.opacity))
    obj[DESIGN_KEYS["opacity"]] = float(self.opacity)


def _apply_show_band(self, context):
    obj = gp.find_object()
    if obj is not None:
        gp.set_layer_visible(obj, _band_layer(self.style), bool(self.show_band))


def _apply_show_case(self, context):
    obj = gp.find_object()
    if obj is not None:
        gp.set_layer_visible(obj, LAYER_CASE, bool(self.show_case))


class GenGirihSettings(bpy.types.PropertyGroup):
    """``scene.gen_girih``：一次生成所需的全部输入。"""

    # --- 配色 -------------------------------------------------------------
    palette: bpy.props.EnumProperty(
        name="配色",
        description="整套配色：画布 / 本体 / 描边 / 填充一起换",
        items=PRESET_ITEMS,
        default=PRESET_PROJECT,
        update=_apply_palette,
    )

    canvas_colour: bpy.props.FloatVectorProperty(
        name="画布底色",
        description="世界背景色，「场景预设」会把它铺成画布；改它立刻重铺",
        size=3,
        subtype="COLOR_GAMMA",
        default=hex_to_rgb(PAPER),
        min=0.0,
        max=1.0,
        update=_apply_canvas_colour,
    )

    # --- 图案 -------------------------------------------------------------
    tiling: bpy.props.EnumProperty(
        name="镶嵌",
        description="用哪种正多边形镶嵌来长星形纹样",
        items=TILING_ITEMS,
        default="T4_6_12",
        update=_mark_dirty,
    )

    angle: bpy.props.FloatProperty(
        name="接触角",
        description="射线与边的夹角：小 = 星形钝、贴着镶嵌；大 = 星形瘦长。"
        "10°~80° 是参考实现的范围",
        default=62.0,
        min=10.0,
        max=80.0,
        precision=1,
        update=_mark_dirty,
    )

    gap: bpy.props.FloatProperty(
        name="双线间隙",
        description="把每条边的两个出发点沿边推开（按边长计）：0 = 单线，"
        "0.1~0.3 = 交叉处拆成两条平行线（参考实现的 Gap）",
        default=0.0,
        min=0.0,
        max=0.4,
        precision=2,
        update=_mark_dirty,
    )

    size: bpy.props.FloatProperty(
        name="单元边长",
        description="一块砖的边长（世界单位）。它同时决定图案的尺度与密度："
        "画布固定时越小格子越多、线越密",
        default=0.64,
        min=0.02,
        soft_max=3.0,
        precision=3,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_mark_dirty,
    )

    canvas: bpy.props.FloatProperty(
        name="画布",
        description="生成区域的边长（世界单位），图案以第一块砖的中心对齐画布中心",
        default=10.0,
        min=0.1,
        soft_max=100.0,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_mark_dirty,
    )

    style: bpy.props.EnumProperty(
        name="样式",
        description="出几层、长什么样。默认「交织」：一上一下、切口补片，也就是蕾丝边",
        items=STYLE_ITEMS,
        default="INTERLACE",
        update=_mark_dirty,
    )

    seam: bpy.props.FloatProperty(
        name="缝隙",
        description="补片与对方边缘之间**额外**留的缝（按可见半宽计）。0 = 补片正好贴住"
        "对方的描边边缘，只看得见那圈描边（推荐）；0.2 以上会在每个交叉上加一道明显的"
        "深色缺口，编织感更强，但带子看着会像断了一截。只对交织样式有效",
        default=0.0,
        min=0.0,
        max=0.6,
        precision=2,
        update=_mark_dirty,
    )

    fills: bpy.props.BoolProperty(
        name="填充",
        description="给每块砖的星形轮廓铺底（画在带子下面）",
        default=False,
        update=_mark_dirty,
    )

    fill_colour: bpy.props.FloatVectorProperty(
        name="填充色",
        description="星形填充的颜色（打开「按边数分色」时被覆盖）",
        size=3,
        subtype="COLOR_GAMMA",
        default=hex_to_rgb(OCHRE),
        min=0.0,
        max=1.0,
        update=_mark_dirty,
    )

    fill_by_sides: bpy.props.BoolProperty(
        name="按边数分色",
        description="三角形苔绿、正方形石青、六边形赭石、十二边形朱砂，"
        "表里没有的边数用「填充色」",
        default=False,
        update=_mark_dirty,
    )

    show_tiles: bpy.props.BoolProperty(
        name="砖轮廓",
        description="出 Tiling 调试层：把镶嵌本身的砖画出来（石青，压在最上层）",
        default=False,
        update=_mark_dirty,
    )

    # --- 线条（就地生效） -------------------------------------------------
    width: bpy.props.FloatProperty(
        name="线宽",
        description="本体笔划半径（总宽 = 2×半径）。宽度不进几何，拖滑块是实时的",
        default=0.035,
        min=0.0,
        soft_max=0.3,
        precision=4,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_apply_widths,
    )

    casing: bpy.props.FloatProperty(
        name="描边倍数",
        description="描边半径 = 线宽 × 这个倍数。要 > 1 才露得出来；"
        "1.3~1.4 是细蕾丝边，再大就是粗边框",
        default=1.35,
        min=1.0,
        max=4.0,
        precision=2,
        update=_apply_casing,
    )

    colour: bpy.props.FloatVectorProperty(
        name="本体色",
        description="带子（本体）颜色，默认琥珀 #E8B362",
        size=3,
        subtype="COLOR_GAMMA",
        default=hex_to_rgb(AMBER),
        min=0.0,
        max=1.0,
        update=_apply_colour,
    )

    case_colour: bpy.props.FloatVectorProperty(
        name="描边色",
        description="描边（蕾丝边）颜色，默认墨玄",
        size=3,
        subtype="COLOR_GAMMA",
        default=hex_to_rgb(INK),
        min=0.0,
        max=1.0,
        update=_apply_case_colour,
    )

    opacity: bpy.props.FloatProperty(
        name="不透明度",
        description="两层共用的不透明度（逐点 ``opacity`` 属性）",
        default=1.0,
        min=0.0,
        max=1.0,
        precision=3,
        update=_apply_opacity,
    )

    show_band: bpy.props.BoolProperty(
        name="本体",
        description="显示本体层",
        default=True,
        update=_apply_show_band,
    )

    show_case: bpy.props.BoolProperty(
        name="描边",
        description="显示描边层（关掉就只剩纯线稿）",
        default=True,
        update=_apply_show_case,
    )

    # --- 状态 -------------------------------------------------------------
    sweep_from: bpy.props.FloatProperty(
        name="起始角",
        description="扫掠的起始接触角",
        default=30.0,
        min=10.0,
        max=80.0,
        precision=1,
    )

    sweep_to: bpy.props.FloatProperty(
        name="结束角",
        description="扫掠的结束接触角",
        default=75.0,
        min=10.0,
        max=80.0,
        precision=1,
    )

    sweep_frames: bpy.props.IntProperty(
        name="帧数",
        description="烘焙成多少帧（每帧重算一次几何）",
        default=24,
        min=2,
        max=240,
    )

    auto_rebuild: bpy.props.BoolProperty(
        name="自动重建",
        description="几何参数改完停手 0.25 秒后自动重建，不用再点「更新」",
        default=True,
        update=_apply_auto_rebuild,
    )

    dirty: bpy.props.BoolProperty(
        name="待生成",
        description="参数改过但还没重新生成",
        default=True,
        options={"HIDDEN"},
    )

    last_tiles: bpy.props.IntProperty(name="砖数", default=0)
    last_segments: bpy.props.IntProperty(name="线段数", default=0)
    last_strands: bpy.props.IntProperty(name="股线数", default=0)
    last_crossings: bpy.props.IntProperty(name="交叉数", default=0)
    last_fills: bpy.props.IntProperty(name="填充块数", default=0)
    last_cuts: bpy.props.IntProperty(name="切口", default=0)
    last_pruned: bpy.props.IntProperty(name="收刀", default=0)
    last_patches: bpy.props.IntProperty(name="补片", default=0)
    last_mismatches: bpy.props.IntProperty(name="非交替", default=0)
    last_points: bpy.props.IntProperty(name="笔划点数", default=0)
    last_ms: bpy.props.FloatProperty(name="耗时", default=0.0)
    last_note: bpy.props.StringProperty(name="提示", default="")
    error: bpy.props.StringProperty(name="错误", default="")

    # --- 派生量 -----------------------------------------------------------
    @property
    def tiling_key(self) -> str:
        """面板里的枚举标识符 → 镶嵌表的键（``"T4_6_12"`` → ``"4.6.12"``）。"""
        return TILING_BY_ID.get(self.tiling, "4.6.12")

    def layer_widths(self) -> dict[str, float]:
        """各图层的笔划半径（描边 = 线宽 × 倍数；调试层用细线）。

        ``Fills`` 层不用列：那里的形状只有填充没有描边，``gp`` 会把半径写 0。
        """
        if self.style == STYLE_LINES:
            widths = {LAYER_LINES: float(self.width)}
        else:
            widths = {
                LAYER_CASE: float(self.width) * float(self.casing),
                LAYER_BAND: float(self.width),
            }
        widths[LAYER_TILING] = float(self.width) * 0.25
        return widths

    def fill_colours(self) -> dict:
        """当前配色预设的「按砖的边数分色」表（空表 = 这套不分色）。"""
        return dict(preset_of(self.palette).by_sides)


# --- 参数回读 -------------------------------------------------------------


def store_design(obj, settings) -> None:
    """把面板参数记到生成对象上（换文件 / 换场景后还能回读）。

    枚举值也是 ``str``，而 ``str`` 有 ``__len__`` —— 顺手 ``list()`` 会把它存成
    ``["T", "4", ...]``，回读时就认不出来了（实测踩过）。字符串必须原样存。
    """
    for name in GEOMETRY_FIELDS + APPEARANCE_FIELDS:
        value = getattr(settings, name)
        if isinstance(value, str) or not hasattr(value, "__len__"):
            obj[DESIGN_KEYS[name]] = value
        else:
            obj[DESIGN_KEYS[name]] = list(value)


def read_design(obj, settings) -> int:
    """把生成对象上记的参数读回面板，返回读到几项。"""
    found = 0
    enums = {"tiling": TILING_BY_ID, "style": {item[0] for item in STYLE_ITEMS}}
    for name in GEOMETRY_FIELDS + APPEARANCE_FIELDS:
        key = DESIGN_KEYS[name]
        if key not in obj:
            continue
        value = obj[key]
        try:
            if name in enums:
                if isinstance(value, str) and value in enums[name]:
                    setattr(settings, name, value)
                    found += 1
                continue
            setattr(settings, name, value)
            found += 1
        except (TypeError, ValueError):
            continue
    return found


def register():
    bpy.types.Scene.gen_girih = bpy.props.PointerProperty(type=GenGirihSettings)


def unregister():
    # 判一下再删：同一份代码可能同时被扩展命名空间（``bl_ext.*``）和开发用
    # ``sys.path`` 导入两份，谁先注销谁就把属性删了，另一份再来就会炸在这里
    if hasattr(bpy.types.Scene, "gen_girih"):
        del bpy.types.Scene.gen_girih
