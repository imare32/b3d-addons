"""实时更新：源网格一变就重编（``depsgraph_update_post`` → timer → :func:`weave.apply`）。

编辑模式里挪一个点，Blender 就报一次网格更新。处理器只做**廉价的判断**（实时更新开没开、
源物体是不是网格、结对象在不在、面数过不过上限），通过就往 timer 里挂一次「立刻执行」；
真正的编结在 timer 里跑，那里走 :func:`weave.apply`（先比指纹，没变就什么都不做）。

**为什么不能直接在处理器里编**（5.2 实测，踩到 Blender 崩溃）：撤销 / 重做的时候，
Blender 正在换掉一批数据块，处理器如果此时往生成对象里写网格（``write_strands`` 开头就是
``mesh.clear_geometry()``），等于把数据从它脚下抽走 —— 编辑模式拖点后按 Ctrl+Z 直接把
Blender 干掉了。挪进 timer 之后，写操作一定发生在求值 / 撤销恢复之后。

顺带的好处是处理器本身不再有 O(面数) 的开销：指纹现在只在 timer 里算。

**为什么设面数上限**：小网格拖点实时更新很舒服，十几万面的网格每次拖点都重编会把
Blender 拖死，所以超过 ``LIVE_FACE_LIMIT`` 直接跳过（面板上显示提示），手动点「更新」
仍然照常工作。

写生成对象会再触发一次本处理器 —— 那一次要么已经被登记成 pending，要么编完比指纹没变，
不会递归。
"""

from __future__ import annotations

import bpy

from . import tube, weave

__all__ = ["LIVE_FACE_LIMIT", "install", "on_update", "remove", "request"]

#: 实时更新能接受的源网格面数上限（超过就只在手动更新时重编）。
LIVE_FACE_LIMIT = 20000

#: 编结 timer 的首次间隔（秒）。0 = 下一个事件循环立刻跑，编辑模式里看起来就是实时的。
_TIMER_INTERVAL = 0.0


def _wanted(scene):
    """这次求值值不值得编一次；值得就返回参数组，否则 ``None``。"""
    settings = getattr(scene, "gen_celtic", None)
    if settings is None or not settings.live:
        return None
    # 只跟当前场景走：附属场景的更新不用管
    if scene is not bpy.context.scene:
        return None

    source = settings.source
    if source is None or source.type != "MESH":
        return None
    if tube.find_object() is None:
        # 还没生成过：别在别人编辑网格时凭空长出对象来
        return None
    if len(source.data.polygons) > LIVE_FACE_LIMIT:
        return None
    return settings


def request():
    """排一次重编（条件在 timer 里再查一遍，这里只管不重复排队）。"""
    if bpy.app.timers.is_registered(_rebuild):
        return  # 已经排上了，一次求值风暴只编一次
    bpy.app.timers.register(_rebuild, first_interval=_TIMER_INTERVAL)


def on_update(scene, depsgraph):
    """依赖图求值结束：只判断要不要编，真正的活交给 timer（见模块说明）。"""
    if _wanted(scene) is None:
        return
    request()


def _rebuild():
    """timer 回调：编一次（指纹没变时 :func:`weave.apply` 自己会早退）。

    没有返回值（= ``None``）就是「一次性 timer，跑完摘掉」；想常驻才需要返回到期时间。
    """
    settings = _wanted(bpy.context.scene)
    if settings is None:
        return  # 条件没了：直接结束
    try:
        weave.apply(settings)
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
    对象，靠 ``handler is on_update`` 根本摘不掉；它继续挂着就相当于用旧代码再跑一遍
    编结（实测踩过：旧处理器在退出编辑模式时把结写成空网格，指纹还对得上，之后
    永远不重编）。所以清理一律按名字扫。
    """
    return getattr(handler, "__module__", None) == __name__ and (
        getattr(handler, "__qualname__", "") == "on_update"
    )


def install():
    """挂上处理器（重复调用不会重复挂，顺带清掉上次加载残留的旧实例）。"""
    remove()
    bpy.app.handlers.depsgraph_update_post.append(on_update)


def remove():
    """摘掉处理器与待跑的 timer（``unregister`` 必须调，否则重载会残留）。"""
    handlers = bpy.app.handlers.depsgraph_update_post
    for handler in [item for item in handlers if _ours(item)]:
        handlers.remove(handler)
    if bpy.app.timers.is_registered(_rebuild):
        bpy.app.timers.unregister(_rebuild)
