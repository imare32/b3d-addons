"""帧 → 参数（闭式） + ``frame_change_pre`` 处理器。

参考脚本里 ``t`` 是在 ``draw()`` 里**累加**的（``t = t + 0.05``），还带一个
``reset_on_frame_one`` 开关 —— 那是"模拟式"写法：状态必须一帧帧往前推。这里换成闭式：

    n = n_init + (frame - frame_start) * n_growth
    t = t_init + (frame - frame_start) * t_growth

于是第 N 帧的图案由公式**直接**给出，不用从第 1 帧算起：时间轴可以随便前后拖、可以只
渲染中间某一帧、回退不需要重置。参考那个开关在闭式下是恒等式（帧 1 时增量本来就是 0），
所以它的位置由"以场景起始帧为基准"取代。

处理器只做一件事：帧变了就按当帧参数重算一遍（实测 3000 根弦 ≈ 0.14 ms）。参数改了就
顺手重算当帧 —— 这也是为什么没有"更新"按钮：一切都便宜到可以当场算。
"""

from __future__ import annotations

import time

from . import gp
from .pattern import generate
from .props import (
    SETTINGS_ID,
    glow_kwargs,
    ink_linear,
    pattern_kwargs,
    scene_kwargs,
    signature,
)
from .scene import build_layers, depth_order

__all__ = ["frame_handler", "install", "params", "purge_handlers", "redraw", "stats"]

#: 上一次写进的参数指纹 + 读数（只是**跳过一次无谓重写**的缓存，不是几何状态：
#: 任何一项参数变了指纹就变了，缓存也检查图层还在不在）。
_STATE: dict = {"key": None, "info": {}}


def params(settings, frame: int, base: int) -> tuple[float, float]:
    """当帧的 ``(n, t)``：以场景起始帧为基准的线性生长。

    ``animate`` 关掉就用 ``n_init`` / ``t_init``（图案不再随时间轴变，等价于一张静帧）。
    """
    step = float(frame) - float(base)
    if not settings.animate:
        step = 0.0
    n = float(settings.n_init) + step * float(settings.n_growth)
    t = float(settings.t_init) + step * float(settings.t_growth)
    return n, t


def redraw(scene=None) -> dict:
    """按当前帧重算并写回（参数改了就当场生效，不需要单独点"更新"）。"""
    import bpy

    scene = scene if scene is not None else bpy.context.scene
    settings = getattr(scene, SETTINGS_ID, None)
    obj = gp.find()
    if settings is None or obj is None:
        return {"updated": False, "reason": "未生成"}

    frame = int(scene.frame_current)
    n, t = params(settings, frame, int(scene.frame_start))
    key = (obj.name, frame, n, t, signature(settings))
    if _STATE["key"] == key and _scaffold_ok(obj):
        return dict(_STATE["info"], updated=False)

    started = time.perf_counter()
    try:
        pattern = generate(n, t, **pattern_kwargs(settings))
        stroke_sets = build_layers(pattern, **scene_kwargs(settings))
        info = gp.update(
            obj,
            stroke_sets,
            frame=frame,
            colour=ink_linear(settings),
            depth_order=depth_order(stroke_sets),
        )
        # 辉光是对象级的效果器，跟几何无关，但每次重画顺手保一遍状态
        info["glow"] = gp.apply_glow(obj, **glow_kwargs(settings))
    except Exception as exc:  # 处理器里绝不能抛：抛了会刷屏并打断播放
        settings.error = f"{type(exc).__name__}: {exc}"
        return {"updated": False, "error": settings.error}

    elapsed = (time.perf_counter() - started) * 1000.0
    settings.error = ""
    settings.last_n = float(pattern.n)
    settings.last_t = float(pattern.t)
    settings.last_ink = float(pattern.ink)
    settings.last_chords = int(pattern.chords)
    settings.last_ms = float(elapsed)
    settings.last_frame = frame

    info.update(
        updated=True,
        n=float(pattern.n),
        t=float(pattern.t),
        ink=float(pattern.ink),
        chords=int(pattern.chords),
        ms=float(elapsed),
        frame=frame,
    )
    _STATE["key"] = key
    _STATE["info"] = dict(info)
    return info


def stats() -> dict:
    """面板读数（只读快照）。"""
    return dict(_STATE["info"])


def frame_handler(scene) -> None:
    """``frame_change_pre``：帧一变就按当帧参数重算（无对象 / 关掉生长时内部直接返回）。

    只要「跟时间轴生长」开着，这里每帧都会重写一遍（0.14 ms 量级）；关掉之后
    参数指纹不再变，缓存命中，等于什么也不做。
    """
    settings = getattr(scene, SETTINGS_ID, None)
    if settings is None or not settings.animate:
        return
    redraw(scene)


def install() -> None:
    """挂上处理器（先清一遍本包的残留，重载不会堆叠）。"""
    import bpy

    purge_handlers()
    bpy.app.handlers.frame_change_pre.append(frame_handler)


def purge_handlers() -> None:
    """只移除**本包注册过**的 handler，防止重载后堆叠（重载 = 禁用 → 清模块 → 启用）。

    判据是模块命名空间（``__package__`` 前缀），不是函数名：``bpy.app.handlers`` 是一串平铺
    的函数对象，没有"按插件分区"的机制，叫 ``frame_handler`` 的 handler 到处都是 —— 按名字清
    理会把别人家的也摘掉。模块名稳定（热重载后不变，函数对象换新），所以拿它判断归属。
    """
    import bpy

    package = __package__ or __name__

    def ours(handler) -> bool:
        module = getattr(handler, "__module__", "")
        return module == package or module.startswith(package + ".")

    bpy.app.handlers.frame_change_pre[:] = [
        handler for handler in bpy.app.handlers.frame_change_pre if not ours(handler)
    ]


def reset_cache() -> None:
    """丢掉重写缓存（删除对象 / 回读参数之后调）。"""
    _STATE["key"] = None
    _STATE["info"] = {}


def _scaffold_ok(obj) -> bool:
    """图层还在、且里面真有笔划（缓存命中前的一道保险：手动删掉笔划不会被跳过）。"""
    layer = obj.data.layers.get("Chords")
    if layer is None or not len(layer.frames):
        return False
    return len(layer.frames[0].drawing.strokes) > 0
