"""实时更新：源物体一变就重划（``depsgraph_update_post`` → timer → :func:`build.apply`）。

处理器只做**廉价判断**（实时开关、源物体类型、生成对象在不在、种子数过不过上限），
通过就往 timer 里排一次「立刻执行」；真正的划分在 timer 里跑，那里走 :func:`build.apply`
（先比种子与参数的指纹，都没变就什么都不做）。

**为什么不能直接在处理器里划**（5.2 实测，代价是崩溃）：撤销 / 重做的时候 Blender 正在
换掉一批数据块，处理器如果此时往生成对象里写几何（``gp.build_object`` 开头就是把笔画
全删了重建），等于把数据从它脚下抽走。挪进 timer 之后，写操作一定发生在求值 / 撤销
恢复之后（这条教训来自 ``gen_celtic``：编辑模式里拖点后按 Ctrl+Z 直接把 Blender 干掉了）。

**GP 编辑模式**：实测 5.2.2 里往 drawing 的 ``position`` 属性写字面量、移动点，都会触发
``depsgraph_update_post``，而且编辑的就是同一份 drawing 数据 —— 所以「在 GP 编辑模式里
画点、单元实时长出来」不需要另开一条轮询；网格编辑模式同理（走路不同，见 :mod:`seeds`）。

**为什么设种子上限**：几百个种子时整份重划不到 1 ms，拖点很舒服；几千个种子之后
Delaunay + 裁剪会明显掉帧，所以超过 :data:`SEED_LIMIT` 直接跳过（面板上亮一条提示），
手动点「生成」照常工作。
"""

from __future__ import annotations

import bpy

from . import build, gp, seeds

__all__ = ["SEED_LIMIT", "install", "on_update", "remove", "request"]

#: 实时更新能接受的种子数上限（超过就只在手动生成时重划）。
#: 实测 1600 个种子整份重划约 60 ms（直边）/ 140 ms（曲线），拖点已经跟不上了。
SEED_LIMIT = 2000

#: 重划 timer 的首次间隔（秒）。0 = 下一个事件循环立刻跑，编辑模式里看起来就是实时的。
_TIMER_INTERVAL = 0.0

#: 文件加载期间不重划（加载也会触发依赖图更新）。
_loading = False


def _wanted(scene, depsgraph=None):
    """这次求值值不值得重划一次；值得就返回参数组，否则 ``None``。"""
    settings = getattr(scene, "gen_voronoi", None)
    if settings is None or not settings.live:
        return None
    # 只跟当前场景走：附属场景的更新不用管
    if scene is not bpy.context.scene:
        return None

    source = settings.source
    if not seeds.is_source(source):
        return None
    if gp.find_object() is None:
        # 还没生成过：别在别人编辑网格时凭空长出对象来
        return None
    if seeds.count(source) > SEED_LIMIT:
        return None
    return settings


def request():
    """排一次重划（条件在 timer 里再查一遍，这里只管不重复排队）。"""
    if bpy.app.timers.is_registered(_rebuild):
        return  # 已经排上了，一次更新风暴只划一次
    bpy.app.timers.register(_rebuild, first_interval=_TIMER_INTERVAL)


def on_update(scene, depsgraph):
    """依赖图求值结束：只判断要不要划，真正的活交给 timer（见模块说明）。"""
    if _loading:
        return
    if _wanted(scene, depsgraph) is None:
        return
    request()


def _rebuild():
    """timer 回调：划一次（指纹没变时 :func:`build.apply` 自己会早退）。"""
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


def _on_load_pre(*_args):
    """文件开始加载：加载期间不重划。"""
    global _loading
    _loading = True


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
