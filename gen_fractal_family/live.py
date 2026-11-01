"""自动更新：基因 / 等级一改，已生成的对象就跟着重算（防抖 timer）。

参数改动是在 RNA 的 ``update`` 回调里收到的，但**不能**在那里直接写几何：拖滑块的当口、
撤销 / 重做的时候 Blender 正在换数据块，这时候删图层、换 ``obj.data`` 等于把数据从它脚下
抽走（``gen_celtic`` 的实时更新踩过这个坑，直接把 Blender 干掉了）。所以回调只负责
**排一次 timer**，真正的重算等这一轮求值 / 撤销结束之后再跑。

防抖：HUD 里拖节点会连着来一串改动（每次移动都改两段），每步都重算一遍是白费力气 ——
每次改动都重排 timer，连续改动只留最后一次（松手约 0.1 s 后重算一次）。

什么时候不重算：

* 场景里没有本插件生成的对象（预览是立即模式画的，不重建任何东西）；
* 自动更新关着（只标「待生成」）；
* 文件加载期间（``load_pre`` / ``load_post``）：加载也会触发参数的 update 回调；
* 回读参数时（:func:`gen_fractal_family.props.suspend_auto_update`）。
"""

from __future__ import annotations

import bpy

from . import build

__all__ = ["DEBOUNCE", "cancel", "install", "remove", "request", "run"]

#: 防抖：改动停下来多久之后重算（秒）。
DEBOUNCE = 0.10

#: 排队中的重算属于哪个场景（timer 里按它取参数组）。
_scene = None

#: 文件加载期间不重算。
_loading = False

#: 挂着的 timer 记在 ``driver_namespace``：热重载后靠它把上一次的 timer 摘掉。
_TIMER_KEY = "gen_fractal_family_live_timer"


def request(settings) -> None:
    """排一次自动重算（连续改动只算最后一次）。"""
    global _scene
    if _loading:
        return
    if not build.generated():
        # 还没生成过：不凭空长出对象（预览不需要对象）
        return
    scene = getattr(settings, "id_data", None)
    if scene is None:
        return
    _scene = scene
    cancel_timer()
    bpy.app.timers.register(_tick, first_interval=DEBOUNCE)
    bpy.app.driver_namespace[_TIMER_KEY] = _tick


def cancel() -> None:
    """取消待跑的重算。"""
    global _scene
    _scene = None
    cancel_timer()


def cancel_timer() -> None:
    """把挂着的 timer 摘掉（认 namespace 里记的那个，热重载后也认得）。"""
    for func in (bpy.app.driver_namespace.pop(_TIMER_KEY, None), _tick):
        if func is None:
            continue
        try:
            if bpy.app.timers.is_registered(func):
                bpy.app.timers.unregister(func)
        except (TypeError, RuntimeError, ValueError):
            pass


def _tick():
    """timer 回调：转给**当前**模块的实现（热重载后旧回调也不会跑旧代码）。"""
    from . import live  # 自己 import 自己：拿到的永远是当前模块

    return live.run()


def run() -> None:
    """按当前参数重算一次并写进已有对象（timer 里调）。"""
    global _scene
    scene = _scene
    _scene = None
    if scene is None or _loading:
        return
    try:
        settings = getattr(scene, "gen_fractal_family", None)
    except ReferenceError:  # 场景被关掉了
        return
    if settings is None or not settings.auto_update:
        return
    if not build.generated():
        return

    try:
        build.rebuild(settings)
    except Exception as exc:  # 不把 traceback 甩给用户：面板上一句话就够
        settings.error = str(exc)
        settings.dirty = True
        return
    settings.error = ""


def _ours(handler) -> bool:
    """按「模块名 + 函数名」认自己的处理器，不认函数对象（热重载会换掉函数对象）。"""
    return getattr(handler, "__module__", None) == __name__ and getattr(
        handler, "__qualname__", ""
    ) in {"_on_load_pre", "_on_load_post"}


def _on_load_pre(*_args) -> None:
    """文件开始加载：加载期间不重算。"""
    global _loading
    _loading = True
    cancel_timer()


def _on_load_post(*_args) -> None:
    """文件加载完：恢复自动重算。"""
    global _loading
    _loading = False


def install() -> None:
    """挂上加载期的手势（重复调用不会重复挂）。"""
    remove()
    bpy.app.handlers.load_pre.append(_on_load_pre)
    bpy.app.handlers.load_post.append(_on_load_post)


def remove() -> None:
    """摘掉处理器与待跑的 timer（``unregister`` 必须调）。"""
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.load_post):
        for handler in [item for item in handlers if _ours(item)]:
            handlers.remove(handler)
    cancel()
