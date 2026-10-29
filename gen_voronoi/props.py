"""场景参数：``scene.gen_voronoi``。

面板上所有可调项都在这里；算子与实时处理器只读这套值。

参数分两档，update 回调也不同：

* **几何档**（形状 / 间距 / 分辨率 / 边界 / 气泡半径 ……）改的是坐标，必须重新划分；
  改完**不必**再点「生成」：``live`` 开着时改动会排一次自动重算（见 :mod:`live`）；
* **外观档**（线宽 / 线色 / 填充）只重写 GP 属性，拖滑块是实时的，不重算单元。

**不要**加 ``from __future__ import annotations``：``bpy.props`` 的注解必须是真实的
``Property`` 对象，PEP 563 的字符串注解会让注册失败。
"""

import bpy

from . import gp
from .palette import INK, SILK, hex_to_rgb

__all__ = [
    "DESIGN_KEYS",
    "GEOMETRY_FIELDS",
    "LIVE_FIELDS",
    "GenVoronoiSettings",
    "read_design",
    "register",
    "store_design",
    "unregister",
]

#: 改坐标的一档：动它必须重新划分。
GEOMETRY_FIELDS = (
    "shape",
    "spacing",
    "min_edge",
    "resolution",
    "boundary",
    "clip_margin",
    "boundary_width",
    "boundary_height",
    "use_max_radius",
    "max_radius",
)

#: 就地生效的一档：只改 GP 属性。
LIVE_FIELDS = (
    "width",
    "colour",
    "fill",
    "fill_colour",
    "fill_alpha",
)

#: 面板字段 → 写到生成对象上的自定义属性名（换文件 / 换场景后仍能取回参数）。
DESIGN_KEYS = {name: f"gen_voronoi_{name}" for name in GEOMETRY_FIELDS + LIVE_FIELDS}
DESIGN_KEYS.update(
    {
        "source": "gen_voronoi_source",
        "geometry": "gen_voronoi_geometry",
        "params": "gen_voronoi_params",
    }
)

#: 线的默认色：色板的墨玄（sRGB 0..1，写进 GP 属性前由 ``gp`` 转线性）。
HEX_INK = hex_to_rgb(INK)
#: 填充的默认色：色板的绢白（要淡底就配浅色，见 :attr:`fill_alpha` 的说明）。
HEX_SILK = hex_to_rgb(SILK)


def _poll_source(_self, obj):
    """源只能是网格或 Grease Pencil（其它类型没有「点」可取）。"""
    return obj.type in {"MESH", "GREASEPENCIL"}


def _mark_dirty(self, context):
    """几何参数变了：单元必须重划，标一下「待生成」，并排一次自动重算。"""
    self.dirty = True
    if not self.live:
        return
    # 函数内 import：live → build → props 是个环，模块级 import 会死锁
    from . import live

    live.request()


def _mark_live(self, context):
    """重新打开实时更新：立刻补一次，别等下次源物体变化才追上。"""
    if not self.live:
        return
    # 函数内 import：理由同 :func:`_mark_dirty`
    from . import live

    live.request()


def _apply_width(self, context):
    """线宽只进 GP 的 ``radius`` 属性：就地改，笔划点数一点不变。"""
    obj = gp.find_object()
    if obj is None:
        return
    if gp.set_layer_radius(obj, float(self.width)):
        obj[DESIGN_KEYS["width"]] = float(self.width)


def _apply_colour(self, context):
    """线色只重写 ``vertex_color``。"""
    obj = gp.find_object()
    if obj is None:
        return
    if gp.set_colour(obj, tuple(self.colour)):
        obj[DESIGN_KEYS["colour"]] = list(self.colour)


def _apply_fill(self, context):
    """填充开关 / 颜色只重写两条 ``CURVE`` 域属性，不重算单元。"""
    obj = gp.find_object()
    if obj is None:
        return
    alpha = float(self.fill_alpha) if self.fill else 0.0
    if gp.set_fill(obj, bool(self.fill), tuple(self.fill_colour), alpha):
        obj[DESIGN_KEYS["fill"]] = bool(self.fill)
        obj[DESIGN_KEYS["fill_colour"]] = list(self.fill_colour)
        obj[DESIGN_KEYS["fill_alpha"]] = float(self.fill_alpha)


class GenVoronoiSettings(bpy.types.PropertyGroup):
    """``scene.gen_voronoi``：一次生成所需的全部输入。"""

    # --- 输入 -------------------------------------------------------------

    source: bpy.props.PointerProperty(
        name="种子源",
        description="种子从这儿取：网格的每个顶点一个，或 Grease Pencil 每一层的每个点一个。"
        "在 GP 编辑模式里画点 / 挪点，单元会跟着实时重划",
        type=bpy.types.Object,
        poll=_poll_source,
        update=_mark_dirty,
    )

    # --- 几何（改完自动重划） ---------------------------------------------

    shape: bpy.props.EnumProperty(
        name="单元形状",
        description="直边 = 数学上原本的 Voronoi 多边形；曲线两档把拐角磨圆，"
        "三次曲线上短于「最短边」的边会被整条吃掉",
        items=[
            ("EDGES", "直边", "沿多边形顶点走一圈，忠实于 Voronoi 的原始形状"),
            ("QUADRATIC", "二次", "每个拐点一段二次曲线，拐角磨圆"),
            ("CUBIC", "三次", "每个拐点一段三次曲线，可以吃掉太短的边"),
        ],
        default="EDGES",
        update=_mark_dirty,
    )

    spacing: bpy.props.FloatProperty(
        name="间距",
        description="每个单元沿边往里缩多少（世界单位）：单元之间因此留出等宽的缝，"
        "0 = 紧贴不留缝，缩过头单元会消失",
        default=0.0,
        min=0.0,
        soft_max=0.2,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_mark_dirty,
    )

    min_edge: bpy.props.FloatProperty(
        name="最短边",
        description="「三次」形状专用：比它短的边整条吃掉，两边的中点直接连起来。"
        "种子很密时调大它，单元才不会被一堆碎边画成一团",
        default=0.0,
        min=0.0,
        soft_max=0.2,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_mark_dirty,
    )

    resolution: bpy.props.IntProperty(
        name="采样",
        description="曲线形状每段插多少点（直边不用）：GP 的笔划是一条点链，"
        "点数决定曲线看起来有多圆，也决定笔划数据量",
        default=8,
        min=2,
        max=64,
        update=_mark_dirty,
    )

    # --- 边界 -------------------------------------------------------------

    boundary: bpy.props.EnumProperty(
        name="边界",
        description="要不要把图案收进一个矩形里：不裁的话最外圈种子的单元会一直延伸到"
        "很远的地方（参考实现靠垫出来的包围盒兜住，看上去是几块巨大的多边形）",
        items=[
            ("NONE", "不裁", "保留完整的单元（最外圈会很大）"),
            ("AUTO", "自动", "按种子包围盒外扩一圈"),
            ("FIXED", "固定", "按面板给的宽高，以原点为中心"),
        ],
        default="AUTO",
        update=_mark_dirty,
    )

    clip_margin: bpy.props.FloatProperty(
        name="边距",
        description="「自动」边界相对种子包围盒的对角线比例（0.1 = 外扩 10%）",
        default=0.1,
        min=0.0,
        soft_max=2.0,
        precision=3,
        update=_mark_dirty,
    )

    boundary_width: bpy.props.FloatProperty(
        name="宽",
        description="「固定」边界的宽度",
        default=4.0,
        min=0.01,
        soft_max=20.0,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_mark_dirty,
    )

    boundary_height: bpy.props.FloatProperty(
        name="高",
        description="「固定」边界的高度",
        default=4.0,
        min=0.01,
        soft_max=20.0,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_mark_dirty,
    )

    # --- 气泡 -------------------------------------------------------------

    use_max_radius: bpy.props.BoolProperty(
        name="气泡",
        description="把每个单元收进自己种子周围半径「气泡半径」的圆里："
        "单元互相拉开、形状变圆（肥皂泡）。关掉就是铺满平面的经典 Voronoi",
        default=True,
        update=_mark_dirty,
    )

    max_radius: bpy.props.FloatProperty(
        name="气泡半径",
        description="一个单元离自己种子最远能到多少（世界单位）："
        "调小 = 单元缩成小圆点，调大 = 慢慢长回互相贴合的完整单元",
        default=0.5,
        min=0.01,
        soft_max=4.0,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_mark_dirty,
    )

    # --- 外观（就地生效） -------------------------------------------------

    width: bpy.props.FloatProperty(
        name="线宽",
        description="GP 笔划的粗细（半径，世界单位）：只写 POINT 域的 ``radius`` 属性，"
        "改它不重算单元",
        default=0.01,
        min=0.0,
        soft_max=0.2,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_apply_width,
    )

    colour: bpy.props.FloatVectorProperty(
        name="线色",
        description="单元轮廓的颜色（默认墨玄）；GP 属性按线性存，面板显示的是 sRGB",
        size=3,
        subtype="COLOR_GAMMA",
        default=HEX_INK,
        min=0.0,
        max=1.0,
        update=_apply_colour,
    )

    fill: bpy.props.BoolProperty(
        name="填充",
        description="给每个单元铺一层色（逐笔划写 ``fill_id`` / ``fill_color``，"
        "相邻单元不会被合并成一片）",
        default=False,
        update=_apply_fill,
    )

    fill_colour: bpy.props.FloatVectorProperty(
        name="填充色",
        description="单元内部的颜色（默认绢白：配默认浓度就是一层淡底；"
        "想要成片的暖色/冷色就选赭石、石青）",
        size=3,
        subtype="COLOR_GAMMA",
        default=HEX_SILK,
        min=0.0,
        max=1.0,
        update=_apply_fill,
    )

    fill_alpha: bpy.props.FloatProperty(
        name="填充浓度",
        description="GP 的填充色是**预乘 alpha** 的：这个值乘在颜色上，1.0 才是原色，"
        "越小越暗（约 0.1 只剩三分之一亮度）——它不透出背景。想要「淡底」就配浅色",
        default=0.6,
        min=0.0,
        max=1.0,
        precision=3,
        update=_apply_fill,
    )

    # --- 实时更新 ---------------------------------------------------------

    live: bpy.props.BoolProperty(
        name="实时更新",
        description="源物体一变就重划（GP 编辑模式里画点 / 拖点、网格编辑模式里挪顶点、"
        "改修改器参数都算）；每次更新先比指纹，没变就直接跳过",
        default=True,
        update=_mark_live,
    )

    # --- 状态（只读） -----------------------------------------------------

    dirty: bpy.props.BoolProperty(
        name="待生成",
        description="参数改过但还没重新生成",
        default=True,
    )

    last_cells: bpy.props.IntProperty(
        name="单元数",
        default=0,
    )

    last_points: bpy.props.IntProperty(
        name="笔划点数",
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


# --- 参数回读 -------------------------------------------------------------


def _hard_range(name: str):
    """字段的硬上下限（``None`` 表示这头不限）。"""
    prop = GenVoronoiSettings.bl_rna.properties.get(name)
    return getattr(prop, "hard_min", None), getattr(prop, "hard_max", None)


def _cast(name: str, value, current):
    """按当前值的类型把回读到的值转回去，并夹进硬范围（枚举只认合法标识符）。"""
    low, high = _hard_range(name)
    if isinstance(current, bool):
        return bool(value)
    if isinstance(current, str):
        prop = GenVoronoiSettings.bl_rna.properties.get(name)
        valid = {item.identifier for item in getattr(prop, "enum_items", [])}
        text = str(value)
        return text if not valid or text in valid else current
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
    bpy.types.Scene.gen_voronoi = bpy.props.PointerProperty(type=GenVoronoiSettings)


def unregister():
    # 判一下再删：同一份代码可能同时被扩展命名空间（``bl_ext.*``）和开发用
    # ``sys.path`` 导入两份，谁先注销谁就把属性删了，另一份再来就会炸在这里
    if hasattr(bpy.types.Scene, "gen_voronoi"):
        del bpy.types.Scene.gen_voronoi
