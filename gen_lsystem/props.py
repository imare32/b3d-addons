"""场景参数：``scene.gen_lsystem``（含规则集合）。

面板上所有可调项都在这里；算子只读这套值，GP 后端只认算好的路径。

参数分两档，update 回调也不同：

* **几何档**（公理 / 规则 / 迭代 / 转角 / 步长 / 基础线宽 / 粗细衰减 / 缩放 / 向重力 / 维度 /
  种子）改的是折线本身，必须重跑一遍「重写 + turtle」；
* **就地档**（宽度倍数 / 线色 / 渐变色 / 渐变开关 / 不透明度）只重写 GP 属性，
  拖滑块是实时的，路径一个点都不重算。

几何档改完**不必**再点「生成」：``auto_update`` 开着时，改动会排一次防抖 timer
（见 :mod:`live`），松手就自动重算到对象上。

设计参数以**一份 JSON** 存回生成对象（``gen_lsystem_design``）：规则是变长集合，塞进十几个
自定义属性既难读也容易漏项；一份 JSON 换个文件 / 换个场景照样能整组读回来。字符串属性没有
``int`` 那种 2³¹ 上限的坑。

**不要**加 ``from __future__ import annotations``：``bpy.props`` 的注解必须是真实的
``Property`` 对象，PEP 563 的字符串注解会让注册失败。
"""

import json
from contextlib import contextmanager

import bpy

from . import gp, presets
from .palette import CINNABAR, INK, hex_to_rgb
from .turtle import DEFAULT_ANGLE

__all__ = [
    "DESIGN_KEY",
    "GenLSystemRule",
    "GenLSystemSettings",
    "apply_preset",
    "read_design",
    "register",
    "store_design",
    "suspend_auto_update",
    "update_design",
]

#: 设计参数在生成对象上的自定义属性名（一份 JSON）。
DESIGN_KEY = "gen_lsystem_design"

#: 要回存的字段（几何 + 就地 + 预设名）；规则单独存。
DESIGN_FIELDS = (
    "axiom",
    "dimension",
    "iterations",
    "angle",
    "length",
    "radius",
    "radius_decay",
    "radius_fat",
    "tropism",
    "seed",
    "preset",
    "frame",
    "width_scale",
    "opacity",
    "colour",
    "tip_colour",
    "gradient",
)

#: 线色默认取项目色板的墨玄；渐变末色取朱砂（墨线走暖红梢，浅底上最耐看）。
HEX_INK = hex_to_rgb(INK)
HEX_CINNABAR = hex_to_rgb(CINNABAR)

#: 回读参数时挂起自动更新的层数（见 :func:`suspend_auto_update`）。
_suspend_depth = 0


@contextmanager
def suspend_auto_update():
    """挂起自动更新：期间改参数不排自动重算。

    回读参数 / 套用预设时用 —— 一次要改十几项，逐项排 timer 是白费力气。
    """
    global _suspend_depth
    _suspend_depth += 1
    try:
        yield
    finally:
        _suspend_depth -= 1


# --- 回调 -------------------------------------------------------------------


def _mark_dirty(self, context):
    """几何参数变了：路径必须重算，标「待生成」，并排一次自动重算。"""
    self.dirty = True
    if _suspend_depth or not self.auto_update:
        return
    # 函数内 import：live → builder → props 是个环，模块级 import 会死锁
    from . import live

    live.request(self)


def _rule_changed(self, context):
    """规则里任何一行改了，都等于几何参数变了（规则集合的条目没有 auto_update）。"""
    settings = getattr(self.id_data, "gen_lsystem", None)
    if settings is not None:
        _mark_dirty(settings, context)


def _toggle_auto(self, context):
    """自动更新开关：开就立刻补一次（把对象追到当前参数），关就撤掉待跑的重算。"""
    from . import live

    if self.auto_update:
        live.request(self)
    else:
        live.cancel()


def _apply_width(self, context):
    """宽度倍数只进 GP 的 ``radius`` 属性：就地改，路径一点不动。"""
    obj = gp.find_object()
    if obj is None:
        return
    if gp.set_width_scale(obj, float(self.width_scale)):
        update_design(obj, self, "width_scale")


def _apply_colours(self, context):
    """线色 / 末色 / 渐变档：只重写 ``vertex_color``。"""
    obj = gp.find_object()
    if obj is None:
        return
    if gp.set_colours(
        obj,
        tuple(self.colour),
        tuple(self.tip_colour),
        str(self.gradient),
    ):
        update_design(obj, self, "colour", "tip_colour", "gradient")


def _apply_opacity(self, context):
    """不透明度：只重写 ``opacity``。"""
    obj = gp.find_object()
    if obj is None:
        return
    if gp.set_opacity(obj, float(self.opacity)):
        update_design(obj, self, "opacity")


def _apply_preset(self, context):
    """切预设：整组写进面板（公理 / 规则 / 解释参数 / 维度），然后排一次重算。"""
    if _suspend_depth:
        # 正在套用预设 / 回读参数：这次赋值是那批写入的一部分，别再套一遍
        return
    preset = presets.by_key(self.preset)
    if preset is None:
        return
    with suspend_auto_update():
        presets.apply_to(self, preset)
    self.dirty = True
    if self.auto_update:
        from . import live

        live.request(self)


def apply_preset(settings, preset: presets.Preset) -> int:
    """把预设写进参数组并触发重算（算子 / 面板按钮共用）。返回写了几项。"""
    with suspend_auto_update():
        count = presets.apply_to(settings, preset)
        settings.preset = preset.key
    settings.dirty = True
    if settings.auto_update:
        from . import live

        live.request(settings)
    return count


# --- 规则 -------------------------------------------------------------------


class GenLSystemRule(bpy.types.PropertyGroup):
    """一条产生式：左侧（可带参数名）+ 可选条件 + 右侧。"""

    pattern: bpy.props.StringProperty(
        name="左侧",
        description="一个模块，可带参数名：X、F、A(l,w)。左侧只允许写一个模块",
        default="X",
        update=_rule_changed,
    )

    condition: bpy.props.StringProperty(
        name="条件",
        description="可选：gt(l,0.1) / eq(type,2) / lt(x,1)。空 = 永远成立；"
        "参数名取自左侧括号里写的那些",
        default="",
        update=_rule_changed,
    )

    result: bpy.props.StringProperty(
        name="右侧",
        description="替换成的模块串，可用函数式表达式：F(l)[+(45)B(mul(l,0.6),w)]",
        default="F",
        update=_rule_changed,
    )

    def label(self) -> str:
        """面板列表里显示的一行。"""
        head = self.pattern or "?"
        return f"{head} -> {self.result or '?'}"


# --- 参数组 -----------------------------------------------------------------


class GenLSystemSettings(bpy.types.PropertyGroup):
    """``scene.gen_lsystem``：一次生成所需的全部输入。"""

    # --- 系统（几何档） ---------------------------------------------------

    axiom: bpy.props.StringProperty(
        name="公理",
        description="起始模块串：它是第 0 代，之后每一代按规则同时重写一遍",
        default="X",
        update=_mark_dirty,
    )

    dimension: bpy.props.EnumProperty(
        name="维度",
        description="2D 把整张图躺平到 XY 平面（俯视出图，与其他二维插件一致）；"
        "3D 保留 +Z 向上（^ & \\ / 才有意义）",
        items=[
            ("2D", "2D", "躺平在 XY 平面，俯视正交出图"),
            ("3D", "3D", "保留立体（+Z 向上），3/4 透视出图"),
        ],
        default="2D",
        update=_mark_dirty,
    )

    iterations: bpy.props.IntProperty(
        name="迭代",
        description="重写多少代。每加一代，指数型系统的规模翻倍 —— 面板上的「模块数」"
        "超预算时会报错而不是卡死",
        default=5,
        min=0,
        soft_max=10,
        max=16,
        update=_mark_dirty,
    )

    seed: bpy.props.IntProperty(
        name="随机种子",
        description="决定随机规则（同一左侧写了多条）与 rand() / £ 的结果；固定种子 = 可复现",
        default=0,
        min=0,
        max=1_000_000,
        update=_mark_dirty,
    )

    rules: bpy.props.CollectionProperty(type=GenLSystemRule)

    active_rule: bpy.props.IntProperty(
        name="当前规则",
        description="列表里选中的那条（条件与右侧在下面编辑）",
        default=0,
        min=0,
    )

    # --- 解释（几何档） ---------------------------------------------------

    angle: bpy.props.FloatProperty(
        name="转角",
        description="符号 + - ^ & \\ / 不带括号时用的角度。写成 +(45) 就用括号里的（度）",
        default=DEFAULT_ANGLE,
        subtype="ANGLE",
        unit="ROTATION",
        update=_mark_dirty,
    )

    length: bpy.props.FloatProperty(
        name="步长",
        description="F 不带参数时走多远。整张图的尺寸基本由它和迭代数决定",
        default=0.035,
        min=0.0001,
        soft_max=0.5,
        subtype="DISTANCE",
        unit="LENGTH",
        precision=4,
        update=_mark_dirty,
    )

    radius: bpy.props.FloatProperty(
        name="基础线宽",
        description="F 落点时的笔划半径（世界单位）；¤ 可以中途改掉它，! / # 逐级变粗变细",
        default=0.012,
        min=0.0,
        soft_max=0.2,
        subtype="DISTANCE",
        unit="LENGTH",
        precision=4,
        update=_mark_dirty,
    )

    radius_decay: bpy.props.FloatProperty(
        name="变细倍率",
        description="符号 ! 不带参数时的倍率：每经过一次，线宽乘这个数（0.8 = 逐级变细 20%）",
        default=0.8,
        min=0.01,
        soft_max=1.0,
        precision=3,
        update=_mark_dirty,
    )

    radius_fat: bpy.props.FloatProperty(
        name="变粗倍率",
        description="符号 # 不带参数时的倍率",
        default=1.2,
        min=0.01,
        soft_max=3.0,
        precision=3,
        update=_mark_dirty,
    )

    tropism: bpy.props.FloatProperty(
        name="向重力",
        description="0 = 关。越大，枝条越被重力扳向 -Z（ABOP 图 2.8 那种下垂感）；"
        "主茎朝上时重力与朝向共线，不受影响",
        default=0.0,
        min=0.0,
        soft_max=1.0,
        precision=3,
        update=_mark_dirty,
    )

    # --- 外观（就地档） ---------------------------------------------------

    width_scale: bpy.props.FloatProperty(
        name="宽度倍数",
        description="整体乘一遍线宽：只重写 GP 的 radius 属性，路径不重算",
        default=1.0,
        min=0.01,
        soft_max=5.0,
        precision=2,
        update=_apply_width,
    )

    colour: bpy.props.FloatVectorProperty(
        name="线色",
        description="笔划主色（默认墨玄）；GP 属性按线性存，面板显示的是 sRGB",
        size=3,
        subtype="COLOR_GAMMA",
        default=HEX_INK,
        min=0.0,
        max=1.0,
        update=_apply_colours,
    )

    gradient: bpy.props.EnumProperty(
        name="渐变",
        description="按哪个量在「线色 → 末色」之间插值。只改 vertex_color，随时切换、不重算路径",
        items=[
            ("NONE", "关闭", "整层单色"),
            ("RADIUS", "按粗细", "越细越接近末色（! / # / ¤ 造成的锥度）"),
            ("DEPTH", "按深度", "分支越深越接近末色（枝梢）"),
            (
                "PROGRESS",
                "按进度",
                (
                    "沿每一笔从起点到终点：单笔图案（龙形 / 科赫 / 希尔伯特）整条扫过去，"
                    "分叉树则是每根枝条各自朝梢部推进（按弧长，每笔重置）"
                ),
            ),
        ],
        default="NONE",
        update=_apply_colours,
    )

    tip_colour: bpy.props.FloatVectorProperty(
        name="末色",
        description="渐变末端的颜色（最细的那头）",
        size=3,
        subtype="COLOR_GAMMA",
        default=HEX_CINNABAR,
        min=0.0,
        max=1.0,
        update=_apply_colours,
    )

    opacity: bpy.props.FloatProperty(
        name="不透明度",
        description="整层笔划的不透明度（只写 opacity 属性）",
        default=1.0,
        min=0.0,
        max=1.0,
        precision=3,
        update=_apply_opacity,
    )

    frame: bpy.props.IntProperty(
        name="落在帧",
        description="笔划写进哪一帧。GP 显示「不晚于当前帧的最后一帧」，所以 1 一直可见",
        default=1,
        min=-100_000,
        soft_max=250,
        update=_mark_dirty,
    )

    # --- 预设 -------------------------------------------------------------

    preset: bpy.props.EnumProperty(
        name="预设",
        description="整组写进面板：公理 / 规则 / 解释参数 / 维度。切换后自动重算",
        items=presets.items(),
        default=presets.DEFAULT_PRESET,
        update=_apply_preset,
    )

    # --- 自动更新 ---------------------------------------------------------

    auto_update: bpy.props.BoolProperty(
        name="自动更新",
        description="几何参数一改就自动重算（松手后约 0.2 s，拖滑块时会合并成一次）；"
        "关掉就退回「改完点『生成』」",
        default=True,
        update=_toggle_auto,
    )

    # --- 状态（只读） -----------------------------------------------------

    dirty: bpy.props.BoolProperty(name="待生成", default=True)

    last_symbols: bpy.props.IntProperty(name="模块数", default=0)

    last_paths: bpy.props.IntProperty(name="笔划", default=0)

    last_points: bpy.props.IntProperty(name="点数", default=0)

    last_depth: bpy.props.IntProperty(name="分支深度", default=0)

    last_ms: bpy.props.FloatProperty(name="耗时", default=0.0)

    last_note: bpy.props.StringProperty(name="提示", default="")

    error: bpy.props.StringProperty(name="错误", default="")

    # --- 派生 -------------------------------------------------------------

    def rule_objects(self) -> list:
        """把规则集合解析成算法层的 :class:`gen_lsystem.lsystem.Rule` 列表。

        空行（左侧或右侧为空）直接跳过 —— 面板上加一条新规则时它一开始就是空的。
        """
        from .lsystem import parse_rule

        rules = []
        for item in self.rules:
            if not item.pattern.strip() or not item.result.strip():
                continue
            rules.append(parse_rule(item.pattern, item.result, item.condition))
        return rules

    def system_arguments(self) -> dict:
        """交给 :func:`gen_lsystem.turtle.build` 的一整套参数。"""
        return {
            "axiom": self.axiom,
            "rules": self.rule_objects(),
            "iterations": int(self.iterations),
            "seed": int(self.seed),
            "angle": float(self.angle),
            "length": float(self.length),
            "radius": float(self.radius),
            "radius_decay": float(self.radius_decay),
            "radius_fat": float(self.radius_fat),
            "tropism": float(self.tropism),
            "dimension": self.dimension,
        }

    def look_arguments(self) -> dict:
        """交给 :func:`gen_lsystem.gp.build_object` 的外观参数。"""
        return {
            "width_scale": float(self.width_scale),
            "colour": tuple(self.colour),
            "tip_colour": tuple(self.tip_colour),
            "gradient": str(self.gradient),
            "opacity": float(self.opacity),
        }


# --- 设计参数的存取 ---------------------------------------------------------


def _cast(name: str, value, current):
    """按当前值的类型把回读到的值转回去，并夹进硬范围。"""
    prop = GenLSystemSettings.bl_rna.properties.get(name)
    if isinstance(current, bool):
        return bool(value)
    if isinstance(current, str):
        # 字符串字段（公理 / 枚举）本该原样存；万一存成了列表（手改过 JSON），拼回去
        if isinstance(value, (list, tuple)):
            return "".join(str(item) for item in value)
        return str(value)
    low = getattr(prop, "hard_min", None)
    high = getattr(prop, "hard_max", None)
    if isinstance(current, int):
        number = int(value)
        if low is not None:
            number = max(int(low), number)
        if high is not None:
            number = min(int(high), number)
        return number
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


def _encode(value):
    """一个 RNA 值 → JSON 能写的东西。

    字符串**先**判掉：它也有 ``__len__``，按长度判会把公理拆成一串字符；
    颜色是 bpy 的 ``Color`` / ``bpy_prop_array``，要 ``list()`` 出来才是浮点数。
    """
    if isinstance(value, str):
        return str(value)
    if hasattr(value, "__len__"):
        return [float(item) for item in value]
    return value


def _payload(obj) -> dict:
    """取回对象上记的 JSON（坏掉 / 没有都当空字典）。"""
    raw = obj.get(DESIGN_KEY)
    if not raw:
        return {}
    try:
        payload = json.loads(str(raw))
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def store_design(obj, settings) -> None:
    """把面板参数整组记到生成对象上（换文件 / 换场景后还能回读）。

    只在**真正重算过**之后调用（``builder.rebuild``）：面板上的几何参数可能已经改过但还没
    重算，这时候整组回存会把「这一张图是用什么参数生成的」记成半改完的样子。
    就地档（颜色 / 宽度倍数 / 不透明度）改单项请用 :func:`update_design`。
    """
    payload: dict = {}
    for name in DESIGN_FIELDS:
        payload[name] = _encode(getattr(settings, name))
    payload["rules"] = [
        [item.pattern, item.condition, item.result] for item in settings.rules
    ]
    obj[DESIGN_KEY] = json.dumps(payload, ensure_ascii=False)


def update_design(obj, settings, *names: str) -> None:
    """只更新几个字段（就地档改属性时用），其余字段保持原样。"""
    payload = _payload(obj)
    for name in names:
        payload[name] = _encode(getattr(settings, name))
    obj[DESIGN_KEY] = json.dumps(payload, ensure_ascii=False)


def read_design(obj, settings) -> int:
    """把生成对象上记的参数读回面板，返回读到几项。"""
    payload = _payload(obj)
    found = 0
    with suspend_auto_update():
        for name in DESIGN_FIELDS:
            if name not in payload:
                continue
            try:
                setattr(settings, name, _cast(name, payload[name], getattr(settings, name)))
            except (AttributeError, TypeError, ValueError):
                # 枚举里没有这个值（老版本记的）之类：跳过，别让整次回读失败
                continue
            found += 1

        rules = payload.get("rules")
        if isinstance(rules, list):
            settings.rules.clear()
            for entry in rules:
                if not isinstance(entry, (list, tuple)) or len(entry) < 3:
                    continue
                item = settings.rules.add()
                item.pattern = str(entry[0])
                item.condition = str(entry[1])
                item.result = str(entry[2])
            found += len(settings.rules)
    return found


def register():
    bpy.types.Scene.gen_lsystem = bpy.props.PointerProperty(type=GenLSystemSettings)


def unregister():
    del bpy.types.Scene.gen_lsystem
