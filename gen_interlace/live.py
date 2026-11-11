"""实时更新：源物体一变就重编（``depsgraph_update_post`` → timer → :func:`build.apply`）。

处理器只做**廉价判断**（实时开关、源物体类型、生成对象在不在、股线条数过不过上限），
通过就往 timer 里排一次「立刻执行」；真正的编织在 timer 里跑，那里走 :func:`build.apply`
（先比几何与参数的指纹，都没变就什么都不做）。

**为什么不能直接在处理器里编**（这条教训来自 ``gen_celtic``，5.2 实测代价是崩溃）：撤销 /
重做的时候 Blender 正在换掉一批数据块，处理器如果此时往生成对象里写几何
（``gp.build_object`` 开头就是把笔划全删了重建），等于把数据从它脚下抽走。挪进 timer 之后，
写操作一定发生在求值 / 撤销恢复之后。

**两种源都是「活」的**（5.2.2 实测）：

* 曲线编辑模式 —— 编辑模式改的就是 ``obj.data.splines`` 本身（不像网格另开一份 BMesh），
  拖控制点、拖手柄都会触发依赖图更新，所以直接读样条就是最新坐标；
* GP 编辑模式 —— 编辑的是同一份 drawing 数据（往 ``position`` 属性写值当场生效），
  画一笔、拖一下都会触发更新。

于是「在曲线 / GP 编辑模式里改股线、编织实时跟着变」不需要另开一条轮询。
"""

from __future__ import annotations

import bpy

from . import build, gp, strands

__all__ = [
    "STRAND_LIMIT",
    "TUCK_STRAND_LIMIT",
    "install",
    "limit_for",
    "on_update",
    "remove",
    "request",
]

#: 实时更新能接受的股线条数上限（超过就只在手动生成时重编），零重叠模式。
#: 实测（正弦辫子、采样步长 0.02、带宽 0.03）：5 条 3.4 ms / 10 条 11 ms / 20 条 43 ms /
#: 30 条 90 ms。交叉数随股线数超线性涨，30 条以后拖点明显跟不上手。
STRAND_LIMIT = 30

#: 平贴端头模式的上限：每处交叉还要多两块补片，整份重编差不多翻一倍
#: （7 条 11 ms / 10 条 23 ms / 20 条 91 ms），所以收紧到 12 条。
TUCK_STRAND_LIMIT = 12

#: 重编 timer 的首次间隔（秒）。0 = 下一个事件循环立刻跑，编辑模式里看起来就是实时的。
_TIMER_INTERVAL = 0.0

#: 文件加载期间不重编（加载也会触发依赖图更新）。
_loading = False


def limit_for(settings) -> int:
    """当前模式下的实时股线上限（平贴端头更贵，上限更低）。"""
    return TUCK_STRAND_LIMIT if getattr(settings, "tuck", False) else STRAND_LIMIT


def _wanted(scene, depsgraph=None):
    """这次求值值不值得重编一次；值得就返回参数组，否则 ``None``。"""
    settings = getattr(scene, "gen_interlace", None)
    if settings is None or not settings.live:
        return None
    # 只跟当前场景走：附属场景的更新不用管
    if scene is not bpy.context.scene:
        return None

    source = settings.source
    if not strands.is_source(source):
        return None
    if gp.find_object() is None:
        # 还没生成过：别在别人编辑曲线时凭空长出对象来
        return None
    if strands.count(source) > limit_for(settings):
        return None
    return settings


def request():
    """排一次重编（条件在 timer 里再查一遍，这里只管不重复排队）。"""
    if bpy.app.timers.is_registered(_rebuild):
        return  # 已经排上了，一次更新风暴只编一次
    bpy.app.timers.register(_rebuild, first_interval=_TIMER_INTERVAL)


def on_update(scene, depsgraph):
    """依赖图求值结束：只判断要不要编，真正的活交给 timer（见模块说明）。"""
    if _loading:
        return
    if _wanted(scene, depsgraph) is None:
        return
    request()


def _rebuild():
    """timer 回调：编一次（指纹没变时 :func:`build.apply` 自己会早退）。"""
    settings = _wanted(bpy.context.scene)
    if settings is None:
        return  # 条件没了：直接结束
    try:
        build.apply(settings)
    except Exception as exc:
        # 不能把 traceback 甩给用户；同一个错误只说一次（写属性本身也会触发依赖图
        # 更新，值没变就不再写，免得来回打转）
        message = str(exc)
        if settings.error != message:
            settings.error = message
            settings.dirty = True


def _ours(handler) -> bool:
    """按「模块名 + 函数名」认自己的处理器，**不认函数对象**。

    热重载会把 ``sys.modules`` 里的模块整个换掉，旧实例的 ``on_update`` 是另一个
    对象，靠 ``handler is on_update`` 根本摘不掉；它继续挂着就相当于用旧代码再跑一遍。
    """
    return getattr(handler, "__module__", None) == __name__ and getattr(
        handler, "__qualname__", ""
    ) in {"on_update", "_on_load_pre", "_on_load_post"}


#: 必须 ``persistent``：Blender 打开 / 新建文件时会摘掉没标的 handler，
#: 摘完 ``load_post`` 就再也不会来，``_loading`` 会永久停在 ``True``
#: （那时面板还在写「正在自动更新…」，用户只看到「改了没反应」）。
@bpy.app.handlers.persistent
def _on_load_pre(*_args):
    """文件开始加载：加载期间不重编。"""
    global _loading
    _loading = True


#: 同上，必须 ``persistent``。
@bpy.app.handlers.persistent
def _on_load_post(*_args):
    """文件加载完：恢复实时更新。"""
    global _loading
    _loading = False


def install():
    """挂上处理器（重复调用不会重复挂，顺带清掉上次加载残留的旧实例）。"""
    remove()
    bpy.app.handlers.depsgraph_update_post.append(on_update)
    bpy.app.handlers.load_pre.append(_on_load_pre)
    bpy.app.handlers.load_post.append(_on_load_post)


def remove():
    """摘掉处理器与待跑的 timer（``unregister`` 必须调，否则重载会残留）。"""
    for handlers in (
        bpy.app.handlers.depsgraph_update_post,
        bpy.app.handlers.load_pre,
        bpy.app.handlers.load_post,
    ):
        for handler in [item for item in handlers if _ours(item)]:
            handlers.remove(handler)
    if bpy.app.timers.is_registered(_rebuild):
        bpy.app.timers.unregister(_rebuild)
