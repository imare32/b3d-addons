"""场景参数：``scene.gen_tsubayaki``。

面板上所有可调项都在这里；算子只读这套值。**不要**加
``from __future__ import annotations``：``bpy.props`` 的注解必须是真实的 ``Property``
对象，PEP 563 的字符串注解会让注册失败。

这些参数**没有"待更新"这一档**：本扩展是生成式（闭式、无状态），重算一帧只要
0.1 ms 量级，所以任何一项改动都当场重画当前帧（:func:`_mark_dirty` 里直接调
:func:`gen_tsubayaki.anim.redraw`），「生成」按钮只管第一次建对象。
"""

import bpy

from .palette import BACKGROUND, INK, SILK, hex_to_rgb, srgb_to_linear
from .pattern import (
    CONSTRAIN_BOUND,
    MAX_N,
    TILE_SIZE,
    VARIANT_LABELS,
    VARIANTS,
)

__all__ = [
    "SETTINGS_ID",
    "GenTsubayakiSettings",
    "backdrop_hex",
    "glow_kwargs",
    "ink_linear",
    "pattern_kwargs",
    "scene_kwargs",
    "signature",
]

#: 挂到 ``Scene`` 上的指针名（``scene.gen_tsubayaki``）。
SETTINGS_ID = "gen_tsubayaki"

_MODE_ITEMS = [
    (
        "SINGLE",
        "单轨星图",
        "内轨半径 n、频率 t，外轨半径 400 - n、频率 1：一圈弦围成星图",
    ),
    (
        "TILED",
        "分块裁剪",
        "端点在格子边界被裁到 ±裁剪边界：短弦贴边，拼出方形的开片骨架",
    ),
]

_VARIANT_ITEMS = [(key, VARIANT_LABELS[key], "") for key in VARIANTS]

#: 世界背景的两种取向 —— 同一套色板的两种用法，不引入新颜色。
_BACKDROP_ITEMS = [
    (
        "DARK",
        "墨底",
        (
            f"墨玄 {INK}：亮线落在深底上，反白像碑拓，「辉光」一开就是发光效果"
            "（本扩展默认）"
        ),
    ),
    (
        "PAPER",
        "纸底",
        f"项目画布底色 {BACKGROUND}：与其余扩展一致，墨玄落在纸底上",
    ),
]

_BACKDROP_HEX = {"DARK": INK, "PAPER": BACKGROUND}

_MODE_DEFAULT = "SINGLE"


def _mark_dirty(self, context):
    """任何一项参数变了：标记待更新，并当场把当前帧重画一遍。

    重画很便宜（实测 3000 根弦 ≈ 0.14 ms），所以拖滑块时图案是跟手的；
    对象还不存在时 :func:`gen_tsubayaki.anim.redraw` 会直接返回，什么也不做。
    """
    self.dirty = True
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    from . import anim

    anim.redraw(scene)


def _apply_visibility(self, context):
    """显隐开关只切图层显隐，不动几何（重画也会保住这个状态）。"""
    from . import gp
    from .scene import LAYER_CHORDS

    gp.set_visible(gp.find(), LAYER_CHORDS, self.show_lines)


def _apply_backdrop(self, context):
    """换背景只重铺世界底色 —— 颜色与几何是两回事，不必重算图案。"""
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    from . import ops

    ops.paint_canvas(scene, backdrop_hex(self.backdrop))


def _apply_glow(self, context):
    """辉光只动效果器，不动几何（重画也会顺手保一遍这个状态）。"""
    from . import gp

    gp.apply_glow(gp.find(), **glow_kwargs(self))


class GenTsubayakiSettings(bpy.types.PropertyGroup):
    """弦线族（Tsubayaki）的参数。"""

    # --- 图案 -------------------------------------------------------------
    mode: bpy.props.EnumProperty(
        name="模式",
        description="单轨星图 / 分块裁剪",
        items=_MODE_ITEMS,
        default=_MODE_DEFAULT,
        update=_mark_dirty,
    )

    variant: bpy.props.EnumProperty(
        name="象限变体",
        description="分块裁剪时用哪个象限；「四格整铺」把四个变体按 2x2 铺回各自所在"
        "的格子（参考只画一个变体、居中放在原点，所以那是本扩展补的一个选项）",
        items=_VARIANT_ITEMS,
        default="VAR_1",
        update=_mark_dirty,
    )

    constrain_bound: bpy.props.FloatProperty(
        name="裁剪边界",
        description="分块裁剪时端点被按到 ±这个值（参考 180，即单块 360 的一半）："
        "n 和单块边长 - n 常常大于边界，于是弦贴着方块边缘走",
        default=CONSTRAIN_BOUND,
        min=10.0,
        soft_max=360.0,
        precision=1,
        update=_mark_dirty,
    )

    tile_size: bpy.props.FloatProperty(
        name="单块边长",
        description="分块裁剪的单块大小（参考 360）；单块边长 - n 是外轨半径，"
        "所以它同时决定外轨的尺度",
        default=TILE_SIZE,
        min=10.0,
        soft_max=720.0,
        precision=1,
        update=_mark_dirty,
    )

    # --- 生长（帧 → 参数） -------------------------------------------------
    animate: bpy.props.BoolProperty(
        name="跟时间轴生长",
        description="开着：每帧按 n = 初始 n + (帧 - 起始帧) * 每帧增量 重算一遍"
        "（闭式，可任意跳帧 / 回退，不用重置）；关掉就只用初始值，图案不随时间轴变",
        default=True,
        update=_mark_dirty,
    )

    n_init: bpy.props.FloatProperty(
        name="初始 n",
        description="轨道半径 / 角步长的起点，弦数 = 2n（参考默认 1）",
        default=1.0,
        min=1.0,
        soft_max=float(MAX_N),
        precision=2,
        update=_mark_dirty,
    )

    n_growth: bpy.props.FloatProperty(
        name="n 每帧增量",
        description="每过一帧 n 长多少（参考 0.5）：n 变大 = 弦变多、星图变密",
        default=0.5,
        min=0.0,
        soft_max=4.0,
        precision=3,
        update=_mark_dirty,
    )

    t_init: bpy.props.FloatProperty(
        name="初始 t",
        description="动态频率的起点（参考 0）",
        default=0.0,
        soft_max=24.0,
        precision=3,
        update=_mark_dirty,
    )

    t_growth: bpy.props.FloatProperty(
        name="t 每帧增量",
        description="每过一帧 t 涨多少（参考 0.05）：t 变大 = 内轨拧得更紧，"
        "同时墨色按「墨色衰减」慢慢退去",
        default=0.05,
        min=0.0,
        soft_max=0.5,
        precision=4,
        update=_mark_dirty,
    )

    # --- 起伏 -------------------------------------------------------------
    z_wave_amp: bpy.props.FloatProperty(
        name="Z 向振幅",
        description="让弦沿 Z 起伏（单位是设计坐标，±180 的量级，随画布等比缩放）；"
        "不为 0 时深度排序自动切成 3D，让笔划真地互相遮挡",
        default=0.0,
        min=0.0,
        soft_max=180.0,
        precision=2,
        update=_mark_dirty,
    )

    z_wave_freq: bpy.props.FloatProperty(
        name="Z 向频率",
        description="起伏的频率倍数（参考 2）",
        default=2.0,
        min=0.0,
        soft_max=20.0,
        precision=2,
        update=_mark_dirty,
    )

    # --- 墨色 -------------------------------------------------------------
    ink_color: bpy.props.FloatVectorProperty(
        name="墨色",
        description="所有弦共用一个墨色；浓淡靠逐弦淡出做（面板上是 sRGB，"
        "写进顶点色前转线性）。默认绢白配墨底是反白——亮线落在深底上才像发光",
        subtype="COLOR",
        default=hex_to_rgb(SILK),
        min=0.0,
        max=1.0,
        update=_mark_dirty,
    )

    fade: bpy.props.BoolProperty(
        name="墨韵淡出",
        description="参考的 Color Ramp + 帧级墨色衰减：首弦最浓、末弦只剩一点"
        "（t 涨上去整族一起变淡）",
        default=True,
        update=_mark_dirty,
    )

    fade_rate: bpy.props.FloatProperty(
        name="墨色衰减",
        description="帧级墨色 = (256 - t * 这个值) / 255（参考硬编码 4）",
        default=4.0,
        min=0.0,
        soft_max=16.0,
        precision=2,
        update=_mark_dirty,
    )

    fade_tail: bpy.props.FloatProperty(
        name="末弦余墨",
        description="最后一根弦保留的墨量比例（参考 0.1）：0 = 淡到看不见",
        default=0.1,
        min=0.0,
        max=1.0,
        precision=3,
        update=_mark_dirty,
    )

    # --- 辉光（GP 自带的效果器，不是合成器）--------------------------------
    glow: bpy.props.BoolProperty(
        name="辉光",
        description="给对象挂 GP 自带的 FX_GLOW 效果器：深底 + 亮线 + 辉光就是"
        "「发光」。不碰合成器，GP v3 的 shader_effects 仍然参与渲染",
        default=True,
        update=_apply_glow,
    )

    glow_size: bpy.props.FloatProperty(
        name="辉光半径",
        description="光晕半径，单位是渲染像素（不随分辨率缩放）：给太大而采样不足"
        "会把光晕摊薄成一层灰雾，2048 见方用 24 左右才是贴边的一层光",
        default=24.0,
        min=0.0,
        soft_max=200.0,
        precision=1,
        update=_apply_glow,
    )

    glow_opacity: bpy.props.FloatProperty(
        name="辉光强度",
        description="光晕的不透明度：往 1 走就越糊、越亮",
        default=1.0,
        min=0.0,
        max=1.0,
        precision=2,
        update=_apply_glow,
    )

    glow_samples: bpy.props.IntProperty(
        name="辉光采样",
        description="取样数，越大越平滑也越慢（Blender 默认 8）",
        default=32,
        min=1,
        max=32,
        update=_apply_glow,
    )

    # --- 画布 -------------------------------------------------------------
    canvas: bpy.props.FloatProperty(
        name="画布",
        description="图案被等比缩放、居中放进这个边长的方块（参考的 scale = 1.5 "
        "相当于画布 3、边距 0）",
        default=10.0,
        min=0.1,
        soft_max=100.0,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_mark_dirty,
    )

    margin: bpy.props.FloatProperty(
        name="边距",
        description="画布内边距",
        default=0.5,
        min=0.0,
        soft_max=10.0,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_mark_dirty,
    )

    radius: bpy.props.FloatProperty(
        name="笔划半径",
        description="线宽 = 2 * 半径（世界单位，不随画布缩放）",
        default=0.008,
        min=1e-5,
        max=1.0,
        soft_max=0.05,
        precision=4,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_mark_dirty,
    )

    # --- 背景 -------------------------------------------------------------
    backdrop: bpy.props.EnumProperty(
        name="背景",
        description="世界背景的底色（换背景只重铺世界，不重算几何）",
        items=_BACKDROP_ITEMS,
        default="DARK",
        update=_apply_backdrop,
    )

    # --- 图层 -------------------------------------------------------------
    show_lines: bpy.props.BoolProperty(
        name="显示弦",
        description="关掉只是隐藏图层（不重建几何）",
        default=True,
        update=_apply_visibility,
    )

    # --- 状态（只读） -----------------------------------------------------
    dirty: bpy.props.BoolProperty(
        name="待更新",
        description="参数改过但还没重画（本扩展每改一次就当场重画，所以它只在"
        "还没生成对象时显示）",
        default=True,
    )

    error: bpy.props.StringProperty(
        name="错误",
        description="上一次重画的报错（处理器里不会抛，只记这里）",
        default="",
    )

    last_frame: bpy.props.IntProperty(name="帧", default=0)

    last_n: bpy.props.FloatProperty(name="n", default=0.0, precision=2)

    last_t: bpy.props.FloatProperty(name="t", default=0.0, precision=3)

    last_ink: bpy.props.FloatProperty(name="墨量", default=0.0, precision=3)

    last_chords: bpy.props.IntProperty(name="弦数", default=0)

    last_ms: bpy.props.FloatProperty(name="耗时", default=0.0, precision=2)


# --------------------------------------------------------------------------- #
# 参数 → 各层的入参
# --------------------------------------------------------------------------- #
def pattern_kwargs(settings) -> dict:
    """面板参数 → :func:`gen_tsubayaki.pattern.generate` 的关键字。"""
    return {
        "mode": settings.mode,
        "variant": settings.variant,
        "constrain_bound": float(settings.constrain_bound),
        "tile_size": float(settings.tile_size),
        "z_wave_amp": float(settings.z_wave_amp),
        "z_wave_freq": float(settings.z_wave_freq),
        "fade": bool(settings.fade),
        "fade_rate": float(settings.fade_rate),
        "fade_tail": float(settings.fade_tail),
    }


def scene_kwargs(settings) -> dict:
    """面板参数 → :func:`gen_tsubayaki.scene.build_layers` 的关键字。"""
    return {
        "size": float(settings.canvas),
        "margin": float(settings.margin),
        "radius": float(settings.radius),
    }


def ink_linear(settings) -> tuple[float, float, float]:
    """墨色 → 线性 RGB（顶点色按线性存，不转就会发白）。"""
    return tuple(srgb_to_linear(component) for component in settings.ink_color)


def backdrop_hex(key: str) -> str:
    """背景枚举 → 色板里的十六进制色值。"""
    return _BACKDROP_HEX.get(key, INK)


def glow_kwargs(settings) -> dict:
    """面板参数 → :func:`gen_tsubayaki.gp.apply_glow` 的关键字。

    光晕颜色直接跟着墨色走：亮线什么样，光晕就什么样。
    """
    return {
        "enabled": bool(settings.glow),
        "size": float(settings.glow_size),
        "samples": int(settings.glow_samples),
        "opacity": float(settings.glow_opacity),
        "colour": ink_linear(settings),
    }


def signature(settings) -> tuple:
    """全部参数的指纹（用来判断"这一帧算过了"）。"""
    return (
        settings.mode,
        settings.variant,
        round(float(settings.constrain_bound), 4),
        round(float(settings.tile_size), 4),
        round(float(settings.n_init), 6),
        round(float(settings.n_growth), 6),
        round(float(settings.t_init), 6),
        round(float(settings.t_growth), 6),
        bool(settings.animate),
        round(float(settings.z_wave_amp), 6),
        round(float(settings.z_wave_freq), 6),
        tuple(round(float(c), 6) for c in settings.ink_color),
        bool(settings.fade),
        round(float(settings.fade_rate), 6),
        round(float(settings.fade_tail), 6),
        round(float(settings.canvas), 6),
        round(float(settings.margin), 6),
        round(float(settings.radius), 6),
    )


def register() -> None:
    bpy.types.Scene.gen_tsubayaki = bpy.props.PointerProperty(type=GenTsubayakiSettings)


def unregister() -> None:
    del bpy.types.Scene.gen_tsubayaki
