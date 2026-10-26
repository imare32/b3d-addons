"""场景参数：``scene.gen_rosette``。

面板上所有可调项都在这里，算子只读这套值，渲染后端只认算子算好的宽度。
**不要**加 ``from __future__ import annotations``：``bpy.props`` 的注解必须是
真实的 ``Property`` 对象，PEP 563 的字符串注解会让注册失败。
"""

import bpy

from . import gp
from .palette import INK, SILK, hex_to_rgb
from .rng import SEED_MAX
from .rosette import MIN_PETALS, STAR_TYPE_LABELS, STAR_TYPES, max_level_cap
from .scene import LAYER_BAND, LAYER_CASE, LAYER_FACES, LAYER_GUIDES

__all__ = [
    "DESIGN_KEYS",
    "LAYER_VISIBILITY",
    "PETAL_MAX",
    "STAR_TYPE_ITEMS",
    "GenRosetteSettings",
    "register",
    "unregister",
]

#: 编织带的默认色：色板的绢白（sRGB 0..1）——和 zellij 的石膏勾缝同一个色。
HEX_SILK = hex_to_rgb(SILK)
#: 编织描边的默认色：色板的墨玄，给绢白带子勾一圈细边。
HEX_INK = hex_to_rgb(INK)

#: 花瓣数上限。再往上一条曲线上的点会多到画不动，图案本身也糊成一圈。
PETAL_MAX = 32

#: 花型下拉（存进文件的是 ASCII 标识，显示用带说明的写法）。
STAR_TYPE_ITEMS = [
    (name, STAR_TYPE_LABELS[name], f"star_type = {name!r}") for name in STAR_TYPES
]

#: 参数 → 写到生成对象上的自定义属性名（换文件 / 换场景后仍能取回参数）。
DESIGN_KEYS = {
    "seed": "gen_rosette_seed",
    "petals": "gen_rosette_petals",
    "level": "gen_rosette_level",
    "star_type": "gen_rosette_star_type",
    "canvas": "gen_rosette_canvas",
    "margin": "gen_rosette_margin",
    "band_radius": "gen_rosette_band_radius",
    "casing": "gen_rosette_casing",
    "gap": "gen_rosette_gap",
    "tuck": "gen_rosette_tuck",
}

#: 图层 → 控制可见性的属性名（四个图层永远都写进对象，只是隐藏与否）。
LAYER_VISIBILITY = {
    LAYER_FACES: "show_faces",
    LAYER_CASE: "show_case",
    LAYER_BAND: "show_band",
    LAYER_GUIDES: "show_guides",
}


def _mark_dirty(self, context):
    """几何参数变了：设计必须重建（会改坐标的东西都在这一档）。"""
    self.dirty = True


def _apply_guide_width(self, context):
    """辅助线宽不影响几何，直接就地改半径，不重建设计。"""
    obj = gp.find_object()
    if obj is None:
        return
    gp.set_layer_radius(obj, LAYER_GUIDES, self.guide_radius)


def _apply_visibility(self, context):
    """图层显隐只是 ``layer.hide``，不重建任何东西。"""
    obj = gp.find_object()
    if obj is None:
        return
    for layer_name, prop_name in LAYER_VISIBILITY.items():
        gp.set_layer_visible(obj, layer_name, getattr(self, prop_name))


class GenRosetteSettings(bpy.types.PropertyGroup):
    """``scene.gen_rosette``：一次生成所需的全部输入。"""

    # --- 花型 -------------------------------------------------------------

    seed: bpy.props.IntProperty(
        name="种子",
        description="只决定配色：同一 seed + 同一花型必然生成同一张图；改它就换一套配色",
        default=1,
        min=0,
        # IntProperty 是 32 位有符号，这里跟 rng.SEED_MAX 对齐，越界赋值会抛 ValueError
        max=SEED_MAX,
        soft_max=99999,
        update=_mark_dirty,
    )

    random_seed: bpy.props.BoolProperty(
        name="每次换种子",
        description="每次生成都抽一个新种子（抽到的值会写回种子框，便于复现）",
        default=False,
    )

    petals: bpy.props.IntProperty(
        name="花瓣数",
        description="玫瑰线的角数（>= 4；3 会让核退化成一条径向线）",
        default=10,
        min=MIN_PETALS,
        max=PETAL_MAX,
        soft_max=20,
        update=_mark_dirty,
    )

    level: bpy.props.IntProperty(
        name="阶数",
        description="越高内部连接越复杂；每个 (花瓣数, 花型) 都有一个可实现的上限，"
        "超过时自动收回并在下方提示",
        default=2,
        min=0,
        max=max_level_cap(),
        soft_max=4,
        update=_mark_dirty,
    )

    star_type: bpy.props.EnumProperty(
        name="花型",
        description="第 2 阶核的分叉方式：首位 P = 边平行、C = 边汇聚、D = 边发散；"
        "次位 A / O / R 是收尾边的角度（锐角 / 钝角 / 直角）；S 是单段直核",
        items=STAR_TYPE_ITEMS,
        default="PA",
        update=_mark_dirty,
    )

    # --- 画布 -------------------------------------------------------------

    canvas: bpy.props.FloatProperty(
        name="画布",
        description="画布边长（世界单位），图案等比缩放后居中落在这个正方形内",
        default=10.0,
        min=0.1,
        soft_max=100.0,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_mark_dirty,
    )

    margin: bpy.props.FloatProperty(
        name="边距",
        description="画布内留白（世界单位）；0 = 图案贴住画布边缘",
        default=0.5,
        min=0.0,
        soft_max=2.0,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_mark_dirty,
    )

    # --- 图层 -------------------------------------------------------------

    show_faces: bpy.props.BoolProperty(
        name="色块",
        description="带子围出来的封闭区域，按质心半径分三圈取色（颜色来自种子的色板）；"
        "关掉就是纯线稿（只是隐藏图层，不重建几何）",
        default=True,
        update=_apply_visibility,
    )

    show_case: bpy.props.BoolProperty(
        name="编织描边",
        description="带子下面那条更粗的描边，露出来的一圈就是蕾丝边",
        default=True,
        update=_apply_visibility,
    )

    show_band: bpy.props.BoolProperty(
        name="编织",
        description="把玫瑰线当带子编起来：交叉点上交替压上 / 压下，压下的那一段"
        "在几何里切掉缺口（缺口宽度按带宽算，所以改带宽要重新生成）",
        default=True,
        update=_apply_visibility,
    )

    show_guides: bpy.props.BoolProperty(
        name="辅助线",
        description=(
            "构造辅助线：第「阶数」阶的核 + 连续的玫瑰线中心线"
            "（一条轨道一笔画到底，可直接驱动生长动画）"
        ),
        default=False,
        update=_apply_visibility,
    )

    # --- 编织 -------------------------------------------------------------

    band_radius: bpy.props.FloatProperty(
        name="编织带宽",
        description="带子的笔划半径（总宽 = 2x半径）；缺口按带宽算，属于几何，"
        "改它必须重新生成。画布 10 时 0.025 左右是一根看得清的丝带宽度",
        default=0.025,
        min=0.0,
        precision=4,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_mark_dirty,
    )

    casing: bpy.props.FloatProperty(
        name="描边倍数",
        description="描边半径按「编织带宽」的倍数给：1.0 = 一样宽（露不出边），默认 1.5 时"
        "每侧露出 0.5 倍带宽。跟着带宽走，所以改带宽不会把蕾丝边挤没或撑爆；"
        "它同时决定缺口要让开多少，改它也要重新生成",
        default=1.5,
        min=1.0,
        max=4.0,
        soft_max=2.0,
        precision=3,
        update=_mark_dirty,
    )

    gap: bpy.props.FloatProperty(
        name="编织间隙",
        description="缺口在「刚好贴住上方带子」之外再多留的量，单位是半宽的倍数；"
        "0 = 压下的带子正好顶到上方带子的边（编织的默认形态），越大缝越明显",
        default=0.0,
        min=0.0,
        soft_max=2.0,
        precision=2,
        update=_mark_dirty,
    )

    tuck: bpy.props.BoolProperty(
        name="平头交叉",
        description="斜交叉也让压下的带子钻到对方身下、用平头贴平：切口按 tan(θ/2) 算，"
        "露在外面的轮廓与对方边缘完全重合。关掉则只有接近 90° 的交叉用平头",
        default=True,
        update=_mark_dirty,
    )

    band_color: bpy.props.FloatVectorProperty(
        name="编织色",
        description="编织带子的颜色（一根带子通常横跨多个色块，所以是单色而不是按块派生）。"
        "默认取色板的绢白——和真迹里的石膏勾缝一个道理",
        size=3,
        subtype="COLOR_GAMMA",
        default=HEX_SILK,
        min=0.0,
        max=1.0,
        update=_mark_dirty,
    )

    case_color: bpy.props.FloatVectorProperty(
        name="描边色",
        description="编织描边的颜色（默认墨玄，给绢白带子勾一圈细边）",
        size=3,
        subtype="COLOR_GAMMA",
        default=HEX_INK,
        min=0.0,
        max=1.0,
        update=_mark_dirty,
    )

    guide_radius: bpy.props.FloatProperty(
        name="辅助线宽",
        description="辅助线的笔划半径，改它不用重新生成",
        default=0.0035,
        min=0.0,
        precision=4,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_apply_guide_width,
    )

    # --- 状态（只读） -----------------------------------------------------

    dirty: bpy.props.BoolProperty(
        name="待更新",
        description="参数改过但还没重新生成",
        default=True,
    )

    last_seed: bpy.props.IntProperty(
        name="上次种子",
        default=-1,
    )

    last_shapes: bpy.props.IntProperty(
        name="形状数",
        default=0,
    )

    last_ms: bpy.props.FloatProperty(
        name="耗时",
        default=0.0,
    )

    last_layers: bpy.props.StringProperty(
        name="分层",
        default="",
    )

    last_note: bpy.props.StringProperty(
        name="提示",
        default="",
    )

    error: bpy.props.StringProperty(
        name="错误",
        default="",
    )

    # --- 派生量 -----------------------------------------------------------

    def design_params(self) -> dict:
        """转成 ``build_design`` 的关键字参数。"""
        return {
            "petals": int(self.petals),
            "level": int(self.level),
            "star_type": str(self.star_type),
        }

    @property
    def casing_radius(self) -> float:
        """描边的笔划半径（世界单位）：面板给的是倍数，这里乘上编织带宽。"""
        return float(self.band_radius) * float(self.casing)

    def layer_widths(self) -> dict:
        """``{图层名: 笔划半径}``；色块层不画描边，半径给 0。"""
        return {
            LAYER_FACES: 0.0,
            LAYER_CASE: self.casing_radius,
            LAYER_BAND: float(self.band_radius),
            LAYER_GUIDES: float(self.guide_radius),
        }

    def layer_colors(self) -> dict:
        """面板可覆盖的描边色：编织两层与辅助线（色块跟种子色板走，不在这里）。"""
        return {
            LAYER_CASE: tuple(self.case_color),
            LAYER_BAND: tuple(self.band_color),
            LAYER_GUIDES: tuple(self.case_color),
        }

    def weave_style(self):
        """编织的几何参数（带宽与描边宽都进几何，见 ``scene.build_layers``）。"""
        from .scene import WeaveStyle

        return WeaveStyle(
            half_width=float(self.band_radius),
            gap_ratio=float(self.gap),
            casing=self.casing_radius,
            tuck=bool(self.tuck),
        )


def register():
    bpy.types.Scene.gen_rosette = bpy.props.PointerProperty(type=GenRosetteSettings)


def unregister():
    del bpy.types.Scene.gen_rosette
