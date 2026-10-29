"""场景参数：``scene.gen_interlace``。

面板上所有可调项都在这里；算子与实时处理器只读这套值。

参数分两档，update 回调也不同：

* **几何档**（上下规则 / 带宽 / 采样步长 / 浅角钳制 / 平贴端头 / 缝宽 / 间隙 / 端帽）改的是
  切在哪儿、切多宽，必须重算；改完**不必**再点「生成」：``live`` 开着时会排一次自动重算；
* **外观档**（线色 / 不透明度）只重写 GP 属性，拖滑块是实时的，一笔都不重算。

**不要**加 ``from __future__ import annotations``：``bpy.props`` 的注解必须是真实的
``Property`` 对象，PEP 563 的字符串注解会让注册失败。
"""

import bpy

from . import gp
from .palette import INK, hex_to_rgb
from .strands import SOURCE_TYPES

__all__ = [
    "APPEARANCE_FIELDS",
    "DESIGN_KEYS",
    "GEOMETRY_FIELDS",
    "GenInterlaceSettings",
    "read_design",
    "register",
    "store_design",
    "unregister",
]

#: 改坐标 / 切口的一档：动它必须重新编织。
GEOMETRY_FIELDS = (
    "rule",
    "width",
    "step",
    "min_sin",
    "tuck",
    "seam",
    "air",
    "round_caps",
)

#: 就地生效的一档：只改 GP 属性。
APPEARANCE_FIELDS = (
    "colour",
    "opacity",
)

#: 面板字段 → 写到生成对象上的自定义属性名（换文件 / 换场景后仍能取回参数）。
DESIGN_KEYS = {
    name: f"gen_interlace_{name}" for name in GEOMETRY_FIELDS + APPEARANCE_FIELDS
}
DESIGN_KEYS.update(
    {
        "source": "gen_interlace_source",
        "geometry": "gen_interlace_geometry",
        "params": "gen_interlace_params",
    }
)

#: 线的默认色：色板的墨玄（sRGB 0..1，写进 GP 属性前由 ``gp`` 转线性）。
HEX_INK = hex_to_rgb(INK)


def _poll_source(_self, obj):
    """股线源只能是曲线或 Grease Pencil。"""
    return obj.type in SOURCE_TYPES


def _mark_dirty(self, context):
    """几何参数变了：编织必须重算，标一下「待生成」，并排一次自动重算。"""
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


def _apply_colour(self, context):
    """线色只重写 ``vertex_color``。"""
    obj = gp.find_object()
    if obj is None:
        return
    if gp.set_colour(obj, tuple(self.colour)):
        obj[DESIGN_KEYS["colour"]] = list(self.colour)


def _apply_opacity(self, context):
    """不透明度只重写 ``opacity`` 属性。"""
    obj = gp.find_object()
    if obj is None:
        return
    if gp.set_opacity(obj, float(self.opacity)):
        obj[DESIGN_KEYS["opacity"]] = float(self.opacity)


class GenInterlaceSettings(bpy.types.PropertyGroup):
    """``scene.gen_interlace``：一次编织所需的全部输入。"""

    # --- 输入 -------------------------------------------------------------
    source: bpy.props.PointerProperty(
        name="股线源",
        description="曲线物体的每条样条 / Grease Pencil 的每一笔，各算一条股线",
        type=bpy.types.Object,
        poll=_poll_source,
        update=_mark_dirty,
    )

    # --- 编织（改完自动重算） ---------------------------------------------
    rule: bpy.props.EnumProperty(
        name="上下规则",
        description="谁压谁",
        items=[
            (
                "ALTERNATE",
                "交替",
                "沿每条股线按被经过的次序交替一上一下（真正的编织）",
            ),
            (
                "STRAND",
                "按股线",
                "偶数号股线恒在上、奇数号恒在下（是分层，不是编织）",
            ),
            (
                "CROSSING",
                "按交叉",
                "按交叉的发现次序交替（会自相矛盾，但有的图案好看）",
            ),
        ],
        default="ALTERNATE",
        update=_mark_dirty,
    )

    width: bpy.props.FloatProperty(
        name="带宽",
        description="股线的总宽度（笔划直径）；切口按它的一半算",
        default=0.03,
        min=0.0005,
        soft_max=0.3,
        precision=4,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_mark_dirty,
    )

    step: bpy.props.FloatProperty(
        name="采样步长",
        description="贝塞尔样条细分的目标步长；越小越贴合曲线，交叉检测也越贵",
        default=0.02,
        min=0.001,
        soft_max=0.2,
        precision=4,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_mark_dirty,
    )

    min_sin: bpy.props.FloatProperty(
        name="浅角钳制",
        description="两条股线越接近平行，切口越长（1/sin）；用这个下限钳住，别切出天际",
        default=0.2,
        min=0.02,
        max=1.0,
        precision=3,
        update=_mark_dirty,
    )

    tuck: bpy.props.BoolProperty(
        name="平贴端头",
        description="被压住的一段退到对方身外，退出来的斜缺口用一块补片沿对方边缘裁齐"
        "（端头一律平头、轮廓贴平）。关掉是旧的零重叠方案：只切到刚好被盖住，"
        "斜交叉会看到明显的缺口",
        default=True,
        update=_mark_dirty,
    )

    seam: bpy.props.FloatProperty(
        name="缝宽",
        description="平贴模式下，补片边缘与上方股线边缘之间留的那条缝，按半宽计"
        "（0 = 完全贴住，两条股线连成一片；0.25 = 一条细缝，编织看得更清楚）",
        default=0.25,
        min=0.0,
        max=2.0,
        precision=3,
        update=_mark_dirty,
    )

    air: bpy.props.FloatProperty(
        name="间隙",
        description="（只在关掉「平贴端头」时用）「刚好被上方股线盖住」之外再留的空隙，"
        "按带宽计（0 = 紧贴，0.35 = 每边留 0.35 个带宽的可见空气）",
        default=0.35,
        min=0.0,
        max=2.0,
        precision=3,
        update=_mark_dirty,
    )

    round_caps: bpy.props.BoolProperty(
        name="圆头端帽",
        description="切口两头用圆头（软一点）；圆头会往外多画半个带宽，已自动补进切口。"
        "关掉是平头，切口更利落。与「平贴端头」互斥（圆头补不平斜缺口）",
        default=False,
        update=_mark_dirty,
    )

    # --- 外观（就地生效） -------------------------------------------------
    colour: bpy.props.FloatVectorProperty(
        name="线色",
        description="笔划颜色（默认墨玄）",
        size=3,
        subtype="COLOR_GAMMA",
        default=HEX_INK,
        min=0.0,
        max=1.0,
        update=_apply_colour,
    )

    opacity: bpy.props.FloatProperty(
        name="不透明度",
        description="笔划不透明度（逐点 ``opacity`` 属性）",
        default=1.0,
        min=0.0,
        max=1.0,
        precision=3,
        update=_apply_opacity,
    )

    # --- 实时 -------------------------------------------------------------
    live: bpy.props.BoolProperty(
        name="实时更新",
        description="源物体一变就重编；关掉就只在点「生成」时重编",
        default=True,
        update=_mark_live,
    )

    # --- 运行时状态（不进参数回读） ---------------------------------------
    dirty: bpy.props.BoolProperty(
        name="待生成",
        default=True,
        options={"HIDDEN"},
    )

    last_strands: bpy.props.IntProperty(name="股线数", default=0)
    last_crossings: bpy.props.IntProperty(name="交叉数", default=0)
    last_pieces: bpy.props.IntProperty(name="片段数", default=0)
    last_patches: bpy.props.IntProperty(name="补片数", default=0)
    last_points: bpy.props.IntProperty(name="笔划点数", default=0)
    last_relaxed: bpy.props.IntProperty(name="放宽数", default=0)
    last_ms: bpy.props.FloatProperty(name="耗时", default=0.0)
    last_note: bpy.props.StringProperty(name="提示", default="")
    error: bpy.props.StringProperty(name="错误", default="")


# --- 参数回读 -------------------------------------------------------------


def _hard_range(name: str):
    """字段的硬上下限（``None`` 表示这头不限）。"""
    prop = GenInterlaceSettings.bl_rna.properties.get(name)
    return getattr(prop, "hard_min", None), getattr(prop, "hard_max", None)


def _cast(name: str, value, current):
    """按当前值的类型把回读到的值转回去，并夹进硬范围（枚举只认合法标识符）。"""
    low, high = _hard_range(name)
    if isinstance(current, bool):
        return bool(value)
    if isinstance(current, str):
        prop = GenInterlaceSettings.bl_rna.properties.get(name)
        valid = {item.identifier for item in getattr(prop, "enum_items", [])}
        # 老数据可能被存成了字符列表（见 store_design），拼回来再看合不合法
        text = value if isinstance(value, str) else "".join(str(part) for part in value)
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
    """把面板参数记到生成对象上（换文件 / 换场景后还能回读）。

    枚举值也是 ``str``，而 ``str`` 有 ``__len__`` —— 顺手 ``list()`` 会把它存成
    ``["S", "T", ...]``，回读时就认不出来了（实测踩过）。字符串必须原样存。
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
    for name in GEOMETRY_FIELDS + APPEARANCE_FIELDS:
        key = DESIGN_KEYS[name]
        if key not in obj:
            continue
        setattr(settings, name, _cast(name, obj[key], getattr(settings, name)))
        found += 1
    return found


def register():
    bpy.types.Scene.gen_interlace = bpy.props.PointerProperty(type=GenInterlaceSettings)


def unregister():
    # 判一下再删：同一份代码可能同时被扩展命名空间（``bl_ext.*``）和开发用
    # ``sys.path`` 导入两份，谁先注销谁就把属性删了，另一份再来就会炸在这里
    if hasattr(bpy.types.Scene, "gen_interlace"):
        del bpy.types.Scene.gen_interlace
