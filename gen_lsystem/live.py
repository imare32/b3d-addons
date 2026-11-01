"""自动更新：几何参数一改就重算（防抖 timer）。

参数改动是在 RNA 的 ``update`` 回调里收到的，但**不能**在那里直接写几何：拖滑块的当口、
撤销 / 重做的时候，Blender 正在换数据块，这时候把图层删了重建等于把数据从它脚下抽走
（``gen_celtic`` 的实时更新踩过这个坑，直接把 Blender 干掉了）。所以回调只负责
**排一次 timer**，真正的重算在 timer 里跑 —— 那时求值 / 撤销都结束了。

**防抖**：拖滑块会连着来一串改动，每一步都重算一遍是白费力气（L-system 连展开带 turtle
是毫秒到百毫秒量级，视口还要整个重求值）。这里每次改动都把 timer **重排**一遍
（``first_interval = DEBOUNCE``），连续改动只留最后一次：松手后 ``DEBOUNCE`` 秒重算一次。

**什么时候不重算**：

* 场景里还没有生成对象：不凭空长出对象来 —— 想建就点「生成」；
* 自动更新关着：只标「待生成」，让人工点按钮；
* 文件加载期间（``load_pre`` / ``load_post``）：加载也会触发参数的 update 回调；
* 回读参数 / 套用预设时（:func:`gen_lsystem.props.suspend_auto_update`）。
"""

from __future__ import annotations

import time

import bpy

from . import gp

__all__ = ["DEBOUNCE", "cancel", "cancel_timer", "install", "remove", "request", "run"]

#: 防抖：改动停下来多久之后重算（秒）。0.2 s 比 ``gen_torus_knot`` 的 0.08 s 宽一档 ——
#: L-system 的重算可能到几十毫秒，太灵敏反而会跟拖滑块抢时间。
DEBOUNCE = 0.2

#: 排队中的重算属于哪个场景（timer 回调里按它取参数组）。
_scene = None

#: 文件加载期间不重算（加载也会触发参数的 update 回调）。
_loading = False

#: 挂着的 timer 回调记在 ``bpy.app.driver_namespace`` 里：热重载换了模块之后，
#: 靠它还能把上一次挂的那次 timer 摘掉（不然会重算两遍）。
_TIMER_KEY = "gen_lsystem_live_timer"


def request(settings) -> None:
    """排一次自动重算（连续改动只算最后一次）。

    :param settings: ``scene.gen_lsystem``。
    """
    global _scene
    if _loading:
        return
    if gp.find_object() is None:
        # 还没生成过：不凭空长出对象（想建就点「生成」）
        return
    scene = getattr(settings, "id_data", None)
    if scene is None:
        return
    _scene = scene
    cancel_timer()
    bpy.app.timers.register(_tick, first_interval=DEBOUNCE)
    bpy.app.driver_namespace[_TIMER_KEY] = _tick


def cancel() -> None:
    """取消待跑的重算（关掉自动更新、卸载插件时用）。"""
    global _scene
    _scene = None
    cancel_timer()


def cancel_timer() -> None:
    """把挂着的 timer 摘掉。

    认 ``driver_namespace`` 里记的那个（热重载后也认得），外加本模块的 ``_tick``。
    """
    for func in (bpy.app.driver_namespace.pop(_TIMER_KEY, None), _tick):
        if func is None:
            continue
        try:
            if bpy.app.timers.is_registered(func):
                bpy.app.timers.unregister(func)
        except (TypeError, RuntimeError, ValueError):
            pass


def _tick():
    """timer 回调：转给**当前**模块的实现（热重载后旧回调也不跑旧代码）。

    没有返回值（= ``None``）就是「一次性 timer，跑完摘掉」。
    """
    # 自己 import 自己：热重载之后旧 timer 里跑的是旧函数，但 ``live`` 拿到的是**当前**
    # 模块（新代码），所以旧回调不会跑旧逻辑
    from . import live  # noqa: PLW0406

    return live.run()


def run() -> None:
    """按当前参数重算一次（timer 里调）。"""
    global _scene
    scene = _scene
    _scene = None
    if scene is None or _loading:
        return

    try:
        settings = getattr(scene, "gen_lsystem", None)
        if settings is None or not settings.auto_update:
            return
    except ReferenceError:  # pragma: no cover - 场景被关掉了
        return
    if gp.find_object() is None:
        return

    # 函数内 import：builder → props 是个环（props 的 update 回调里要 import live），
    # 模块级 import 会死锁
    from . import builder

    started = time.perf_counter()
    try:
        skeleton = builder.rebuild(settings)
    except Exception as exc:
        # 不能把 traceback 甩给用户：面板上写一句话就够了（规则写错是最常见的）
        settings.error = str(exc)
        settings.dirty = True
        return

    settings.last_symbols = skeleton.symbols
    settings.last_paths = skeleton.path_count
    settings.last_points = skeleton.point_count
    settings.last_depth = skeleton.depth
    settings.last_ms = (time.perf_counter() - started) * 1000.0
    settings.last_note = _note(skeleton)
    settings.error = ""
    settings.dirty = False


def _note(skeleton) -> str:
    """面板上的一行提示：被忽略的符号（参考实现里有、GP 用不上的那些）。"""
    if not skeleton.ignored:
        return ""
    return "忽略的符号：" + " ".join(skeleton.ignored)


def _ours(handler) -> bool:
    """按「模块名 + 函数名」认自己的处理器，**不认函数对象**。

    热重载会把 ``sys.modules`` 里的模块整个换掉，旧实例是另一个对象，靠
    ``handler is _on_load_pre`` 根本摘不掉。
    """
    return getattr(handler, "__module__", None) == __name__ and (
        getattr(handler, "__qualname__", "") in {"_on_load_pre", "_on_load_post"}
    )


def _on_load_pre(*_args) -> None:
    """文件开始加载：加载期间不重算（加载也会触发参数的 update 回调）。"""
    global _loading
    _loading = True
    cancel_timer()


def _on_load_post(*_args) -> None:
    """文件加载完：恢复自动重算。"""
    global _loading
    _loading = False


def install() -> None:
    """挂上加载期的手势（重复调用不会重复挂，顺带清掉上次加载残留的旧实例）。"""
    remove()
    bpy.app.handlers.load_pre.append(_on_load_pre)
    bpy.app.handlers.load_post.append(_on_load_post)


def remove() -> None:
    """摘掉处理器与待跑的 timer（``unregister`` 必须调，否则重载会残留）。"""
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.load_post):
        for handler in [item for item in handlers if _ours(item)]:
            handlers.remove(handler)
    cancel()
