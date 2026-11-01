"""场景参数：``scene.gen_fractal_family``。

面板、HUD、预览、生成算子读的都是这一套值；改基因的入口有三个（预设下拉、基因列表、
HUD），写进去的都是同一份 ``items``。

参数分两档：

* **几何档**（域 / 基因 / 等级 / 初始曲线 / 形态）：动它就得重算点列。改动只会把
  ``dirty`` 标起来并排一次防抖 timer（见 :mod:`live`），不在 RNA 回调里直接写几何 ——
  拖滑块 / 撤销的当口写数据块等于从脚下抽走数据（``gen_celtic`` 踩过这个坑）；
* **就地档**（预览配色 / 预览颜色 / 预览与 HUD 开关）：只影响 ``gpu`` 立即模式绘制的
  视口预览，没有几何可重算，拖滑块是实时的。

**不要**加 ``from __future__ import annotations``：``bpy.props`` 的注解必须是真实的
``Property`` 对象，PEP 563 的字符串注解会让注册失败。
"""

import bpy

from .lattice import DOMAINS, ComplexInteger, FractalGenerator, format_gene, gene_family, parse_gene
from .palette import CINNABAR, hex_to_rgb
from .presets import DEFAULT_GENES, GENES_BY_NAME

__all__ = [
    "DESIGN_KEYS",
    "GeneratorItem",
    "GenFractalFamilySettings",
    "read_design",
    "register",
    "store_design",
    "suspend_auto_update",
]

#: 预览最多展开到第几级（再往上点数按指数涨，视口预览没有意义）。
PREVIEW_MAX_LEVEL = 6

#: 等级允许的硬范围（输出也按它夹）。
LEVEL_MIN = 1
LEVEL_MAX = 20

#: 面板字段 → 写到生成对象上的自定义属性名（换文件后还能回读）。
DESIGN_KEYS = {
    "gene": "gen_fractal_family_gene",
    "level": "gen_fractal_family_level",
    "spline_type": "gen_fractal_family_spline",
    "initiator_reverse": "gen_fractal_family_initiator_reverse",
}

#: 预览线的默认色：色板的朱砂。
_HEX_PREVIEW = hex_to_rgb(CINNABAR)

#: 回读参数时挂起自动更新的层数（见 :func:`suspend_auto_update`）。
_suspend_depth = 0


class _Suspend:
    """``suspend_auto_update()`` 的上下文：期间改参数不排自动重算。"""

    def __enter__(self):
        global _suspend_depth
        _suspend_depth += 1
        return self

    def __exit__(self, *_exc):
        global _suspend_depth
        _suspend_depth -= 1
        return False


def suspend_auto_update():
    """挂起自动更新（回读参数、应用预设时用）。"""
    return _Suspend()


def _settings(context):
    """从 context 取参数组；拿不到（后台 / 加载中）返回 ``None``。"""
    scene = getattr(context, "scene", None)
    if scene is None:
        return None
    return getattr(scene, "gen_fractal_family", None)


def _mark_dirty(self, context):
    """几何档参数变了：标脏 + 排一次防抖重算。"""
    settings = _settings(context)
    if settings is None:
        return
    settings.dirty = True
    if _suspend_depth or not settings.auto_update:
        return
    from . import live

    live.request(settings)


def _on_domain_change(self, context):
    """切域：基因不变，只是换点阵映射 —— 重算但不必改坐标。"""
    _mark_dirty(self, context)


def _preset_items(self, context):
    """预设下拉的条目：名字 + 家族名做提示。"""
    return [(item["name"], item["name"], f"{item['name']} · {item['family']}") for item in DEFAULT_GENES]


def _on_preset_change(self, context):
    """选中预设 → 直接把基因写进去（与原版一致：选完就能看）。"""
    item = GENES_BY_NAME.get(self.preset)
    if item:
        self.apply_gene(item["gene"])


def _on_geometry_toggle(self, context):
    """预览开关 / 线宽 / 颜色 / 输出模式：只影响视口立即模式绘制，标一次重绘即可。"""
    from . import hud

    hud.tag_redraw()


def _on_auto_update_toggle(self, context):
    """自动更新开关。

    * **开**：立刻补一次重算，并顺手把「视口预览」打开 —— 用户既然在主动用这个插件，
      视口里跟着出曲线是想要的（这也是原来的默认体验，只是不再默认启用）；
    * **关**：撤掉排队中的重算，但**不动预览**（关自动更新通常只是"别在后台重算"，
      曲线还想看）。

    联动是**单向**的：只有"从关到开"那一下会带开预览；之后用户手动关掉预览，
    再开关自动更新不会再抢回去。默认两个都是关的 —— 装了扩展但没在用它的人，
    视口里不该多出东西。
    """
    from . import live

    if self.auto_update:
        if not self.show_preview:
            self.show_preview = True  # 触发预览的 update：标脏 + 请求重绘
        live.request(self)
    else:
        live.cancel()
    self.dirty = True


class GeneratorItem(bpy.types.PropertyGroup):
    """基因里的一段：复整数 ``(a, b)`` + 两个变换标记。"""

    a: bpy.props.IntProperty(
        name="A",
        description="复整数第一分量（G 域是实部，E 域是 ω 的系数）",
        default=0,
        soft_min=-16,
        soft_max=16,
        update=_mark_dirty,
    )

    b: bpy.props.IntProperty(
        name="B",
        description="复整数第二分量",
        default=1,
        soft_min=-16,
        soft_max=16,
        update=_mark_dirty,
    )

    reverse: bpy.props.BoolProperty(
        name="反转",
        description="这一段放入的上一级曲线首尾反向（R）",
        default=False,
        update=_mark_dirty,
    )

    mirror: bpy.props.BoolProperty(
        name="镜像",
        description="这一段放入的上一级曲线镜面翻转（M）",
        default=False,
        update=_mark_dirty,
    )


class GenFractalFamilySettings(bpy.types.PropertyGroup):
    """``scene.gen_fractal_family``：一次生成所需的全部输入。"""

    # --- 基因 -------------------------------------------------------------

    domain: bpy.props.EnumProperty(
        name="域",
        description="点阵：G = 高斯整数（方点阵），E = 艾森斯坦整数（三角点阵）",
        items=[
            ("G", "高斯 G", "方点阵：a + bi"),
            ("E", "艾森斯坦 E", "三角点阵：a + bω"),
        ],
        default="G",
        update=_on_domain_change,
    )

    items: bpy.props.CollectionProperty(type=GeneratorItem)

    active_index: bpy.props.IntProperty(name="当前段", default=0, min=0)

    preset: bpy.props.EnumProperty(
        name="预设",
        description="选一个经典基因（选完直接写入，之后可以继续改）",
        items=_preset_items,
        update=_on_preset_change,
    )

    gene: bpy.props.StringProperty(
        name="基因",
        description="当前基因的字符串形式（只读显示；要粘贴基因请用「导入基因」）",
        default="",
    )

    # --- 预览（就地生效） -------------------------------------------------

    show_preview: bpy.props.BoolProperty(
        name="视口预览",
        description=("直接在 3D 视口里画出分级曲线（gpu 立即模式，不创建任何临时对象/数据块）。"
                     "默认关闭：装了这个扩展但没在用它的人，视口里不该多出东西"),
        default=False,
        update=_on_geometry_toggle,
    )

    preview_ramp: bpy.props.EnumProperty(
        name="预览配色",
        description="分级预览怎么上色：分级配色每级一个专属色（天青→翠绿→橘黄→品红→明黄），单色只用粗细区分",
        items=(
            ("FAMILY", "分级配色", "每一级一个固定颜色，相邻两级色差拉满，层级一眼分得清"),
            ("MONO", "单色", "全部用「预览颜色」，只靠粗细区分层级"),
        ),
        default="FAMILY",
        update=_on_geometry_toggle,
    )

    preview_colour: bpy.props.FloatVectorProperty(
        name="预览颜色",
        description=("单色模式下其余各级的颜色（sRGB）。第 1 级=生成器/骨架，永远是朱砂粗线，"
                     "不受这里影响"),
        size=3,
        subtype="COLOR_GAMMA",
        default=_HEX_PREVIEW,
        min=0.0,
        max=1.0,
        update=_on_geometry_toggle,
    )

    # --- 等级 -------------------------------------------------------------

    level: bpy.props.IntProperty(
        name="等级",
        description="替换次数：等级 0 是初始线段，等级 1 是基因本身，再往上每级把每段换成整条上一级",
        default=4,
        min=LEVEL_MIN,
        max=LEVEL_MAX,
        update=_mark_dirty,
    )

    # --- 输出 -------------------------------------------------------------

    spline_type: bpy.props.EnumProperty(
        name="样条",
        description="生成曲线的样条类型：多段线（折线）或平滑（Bezier + 细分）",
        items=[("POLY", "多段线", "折线，点对点相连"), ("SMOOTH", "平滑", "Bezier 自动手柄 + 细分")],
        default="POLY",
        update=_mark_dirty,
    )

    initiator_curve: bpy.props.PointerProperty(
        name="初始曲线",
        description="留空 = 用从原点到总向量的一条直线；指定一条曲线可做 Koch 雪花这类闭合分形",
        type=bpy.types.Curve,
        update=_mark_dirty,
    )

    initiator_reverse: bpy.props.BoolProperty(
        name="初始曲线反向",
        description="反过来读初始曲线的点序（分形朝里长还是朝外长）",
        default=False,
        update=_mark_dirty,
    )

    output_mode: bpy.props.EnumProperty(
        name="输出模式",
        description="上一次生成用的是哪种模式（自动更新按它重算）",
        items=[
            ("LAST_LEVEL", "最后一级", "只生成最高级一条曲线"),
            ("EACH_LEVEL", "每一级", "每一级一个对象（顶点数相同，便于逐级对照）"),
            ("SHAPE_KEYS", "形态键", "一个对象 + 每级一个形态键，拖 Evaluation Time 生长"),
        ],
        default="LAST_LEVEL",
        update=_on_geometry_toggle,
    )

    auto_update: bpy.props.BoolProperty(
        name="自动更新",
        description=("改基因/等级就自动重算已生成的对象（松手约 0.1 s），并顺手打开视口预览。"
                     "默认关闭：关着就是「改完点生成」，视口里也不多画东西"),
        default=False,
        update=_on_auto_update_toggle,
    )

    # --- 状态（只读，面板上显示） -----------------------------------------

    dirty: bpy.props.BoolProperty(name="待生成", default=True)

    last_points: bpy.props.IntProperty(name="点数", default=0)

    last_level: bpy.props.IntProperty(name="生成等级", default=0)

    last_ms: bpy.props.FloatProperty(name="耗时", default=0.0)

    note: bpy.props.StringProperty(name="提示", default="")

    error: bpy.props.StringProperty(name="错误", default="")

    # --- 基因读写 ---------------------------------------------------------

    def elements(self):
        """当前基因：``[(ComplexInteger, (reverse, mirror)), ...]``。"""
        domain = self.domain
        return [
            (ComplexInteger(item.a, item.b, domain), (bool(item.reverse), bool(item.mirror)))
            for item in self.items
        ]

    def set_elements(self, elements, *, domain=None) -> None:
        """整条基因一次写完（期间挂起自动更新，最后只排一次重算）。"""
        with suspend_auto_update():
            if domain is not None and domain in DOMAINS:
                self.domain = domain
            self.items.clear()
            for element, flags in elements:
                item = self.items.add()
                item.a = int(element.a)
                item.b = int(element.b)
                item.reverse = bool(flags[0])
                item.mirror = bool(flags[1])
            self.active_index = max(0, min(self.active_index, len(self.items) - 1))
            self.gene = self.current_gene()
        self.dirty = True
        from . import live

        if self.auto_update:
            live.request(self)

    def apply_gene(self, gene: str) -> None:
        """按基因字符串写入（预设 / 导入 / 回读都走这里）。"""
        domain, elements = parse_gene(gene)
        self.set_elements(elements, domain=domain)

    def current_gene(self) -> str:
        """当前基因的字符串形式。"""
        return format_gene(self.domain, self.elements())

    def family_name(self) -> str:
        """家族名，形如 ``G(2, 0)``。"""
        return gene_family(self.domain, self.elements())

    def is_valid(self) -> bool:
        """能否展开（至少两段、总向量不为零）。"""
        elements = self.elements()
        if len(elements) < 2:
            return False
        total = ComplexInteger(domain=self.domain)
        for element, _flags in elements:
            total = total + element
        return total.norm != 0 and not any(element.norm == 0 for element, _f in elements)

    def contraction(self) -> float:
        """自相似比 ``max(|e_i|) / |总向量|``（含义见 :attr:`lattice.FractalGenerator.contraction`）。"""
        elements = self.elements()
        if not elements:
            return 0.0
        try:
            return FractalGenerator(elements).contraction
        except ValueError:
            return 0.0

    def contraction_note(self) -> str:
        """把自相似比翻译成一句人话（面板 / HUD 直接用）。"""
        ratio = self.contraction()
        if ratio <= 0.0:
            return ""
        if ratio > 1.0001:
            return f"发散：每级放大 {ratio:.2f}×，等级一高范围会爆掉"
        if ratio > 0.9999:
            return "非收缩：长度每级 ×段数，等级越高范围越大"
        if ratio > 0.75:
            return f"收缩 {ratio:.2f}（偏弱，高级别会明显长大）"
        return f"收缩 {ratio:.2f}"

    def preview_level(self) -> int:
        """预览实际画的等级（被 :data:`PREVIEW_MAX_LEVEL` 截断）。"""
        return max(0, min(int(self.level), PREVIEW_MAX_LEVEL))

    def preview_truncated(self) -> bool:
        """等级是否被预览上限截断（面板据此提示）。"""
        return int(self.level) > PREVIEW_MAX_LEVEL

    def geometry_signature(self):
        """预览缓存键：基因 / 域 / 等级 / 初始曲线一变就换一份。"""
        elements = tuple((item.a, item.b, bool(item.reverse), bool(item.mirror)) for item in self.items)
        initiator = self.initiator_curve
        initiator_key = initiator.name if initiator else ""
        return (self.domain, elements, self.preview_level(), initiator_key, bool(self.initiator_reverse))

    # --- 基因编辑（HUD 与面板按钮共用） -----------------------------------

    def add_item(self, a: int = 1, b: int = 0) -> None:
        """末尾追加一段。"""
        item = self.items.add()
        item.a = int(a)
        item.b = int(b)
        self.active_index = len(self.items) - 1
        self._finish_edit()

    def remove_item(self, index: int = -1) -> None:
        """删掉一段（少于两段就不删：分形至少要两段）。"""
        count = len(self.items)
        if count <= 2:
            return
        if index < 0:
            index = count - 1
        self.items.remove(min(index, count - 1))
        self.active_index = max(0, min(self.active_index, len(self.items) - 1))
        self._finish_edit()

    def move_node(self, index: int, a: int, b: int) -> bool:
        """把第 ``index`` 个节点挪到格点 ``(a, b)``（整数格点），返回是否真的动了。

        节点是相邻两段的公共端点，所以一次改两段；挪出零长度段时撤回（HUD 上表现为
        "拖不过去"）。末节点没有下一段，总向量因此可以自由变化。
        """
        from .lattice import moved_node

        moved = moved_node(self.elements(), int(index), (int(a), int(b)))
        if moved is None:
            return False
        with suspend_auto_update():
            for item, (element, flags) in zip(self.items, moved):
                item.a = element.a
                item.b = element.b
                item.reverse = bool(flags[0])
                item.mirror = bool(flags[1])
            self.gene = self.current_gene()
        self._finish_edit()
        return True

    def toggle_flag(self, index: int, flag: str) -> None:
        """切某一端的 ``reverse`` / ``mirror``。"""
        if not 0 <= index < len(self.items):
            return
        item = self.items[index]
        if flag == "reverse":
            item.reverse = not item.reverse
        else:
            item.mirror = not item.mirror
        with suspend_auto_update():
            self.gene = self.current_gene()
        self._finish_edit()

    def _finish_edit(self) -> None:
        """一次编辑结束：更新基因字符串、标脏、排重算、请求重绘。"""
        self.gene = self.current_gene()
        self.dirty = True
        from . import hud, live

        hud.tag_redraw()
        if self.auto_update:
            live.request(self)


# ---------------------------------------------------------------------------
# 生成对象上的参数回读
# ---------------------------------------------------------------------------


def store_design(obj, settings) -> None:
    """把设计与生成参数记到对象上（换文件 / 换场景后能回读）。"""
    obj[DESIGN_KEYS["gene"]] = settings.current_gene()
    obj[DESIGN_KEYS["level"]] = int(settings.level)
    obj[DESIGN_KEYS["spline_type"]] = settings.spline_type
    obj[DESIGN_KEYS["initiator_reverse"]] = bool(settings.initiator_reverse)


def read_design(obj, settings) -> int:
    """把对象上记的参数读回面板，返回读到几项。"""
    found = 0
    gene = obj.get(DESIGN_KEYS["gene"])
    if isinstance(gene, str) and gene:
        with suspend_auto_update():
            settings.apply_gene(gene)
        found += 1
    for key, name in (("level", "level"), ("spline_type", "spline_type")):
        value = obj.get(DESIGN_KEYS[key])
        if value is not None:
            setattr(settings, name, value)
            found += 1
    reverse = obj.get(DESIGN_KEYS["initiator_reverse"])
    if reverse is not None:
        settings.initiator_reverse = bool(reverse)
        found += 1
    settings.dirty = False
    return found


def register():
    bpy.types.Scene.gen_fractal_family = bpy.props.PointerProperty(type=GenFractalFamilySettings)


def unregister():
    del bpy.types.Scene.gen_fractal_family


# ---------------------------------------------------------------------------
# 首次启用：给一条能看的基因
# ---------------------------------------------------------------------------

#: 计时器键（热重载后还能摘掉上一次挂的）。
_BOOTSTRAP_KEY = "gen_fractal_family_bootstrap_timer"


def bootstrap_default_gene(*_args):
    """基因列表为空时填第一条预设（龙形曲线）。

    用 timer 而不是在 ``register()`` 里直接写：注册那一刻不一定有可用的 scene context，
    而参数组是通过 ``Scene`` 上的指针挂上去的。返回值 ``None`` = 一次性 timer。
    """
    try:
        scene = bpy.context.scene
    except AttributeError:  # 还没有窗口 / 场景
        return 0.2
    settings = getattr(scene, "gen_fractal_family", None)
    if settings is None:
        return 0.2
    if settings.items:
        return None
    item = DEFAULT_GENES[0] if DEFAULT_GENES else {"name": "", "gene": "G 1 0 0 0 0 1 1 0"}
    if item["name"]:
        settings.preset = item["name"]
    settings.apply_gene(item["gene"])
    return None


def install_bootstrap() -> None:
    """挂上首次启用的填基因 timer。"""
    remove_bootstrap()
    bpy.app.timers.register(bootstrap_default_gene, first_interval=0.1)
    bpy.app.driver_namespace[_BOOTSTRAP_KEY] = bootstrap_default_gene


def remove_bootstrap() -> None:
    """摘掉 padding timer（``unregister`` 必须调）。"""
    for func in (bpy.app.driver_namespace.pop(_BOOTSTRAP_KEY, None), bootstrap_default_gene):
        if func is None:
            continue
        try:
            if bpy.app.timers.is_registered(func):
                bpy.app.timers.unregister(func)
        except (TypeError, RuntimeError, ValueError):
            pass
