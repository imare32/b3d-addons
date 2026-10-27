"""实时更新：源网格一变就重编（``depsgraph_update_post`` 处理器）。

编辑模式里挪一个点，Blender 就报一次网格更新 —— 处理器里先比一次指纹，只有源网格
真的变了才走 :func:`weave.apply`（重编是 O(面数) 的，见 ``knot.py`` 的算法说明）。
指纹平时走 numpy 快路径，编辑模式下只能在编辑 BMesh 上算（那是唯一拿得到拖动中
坐标的地方，见 ``weave`` 的模块说明）。

**为什么设面数上限**：小网格拖点实时更新很舒服，十几万面的网格每次拖点都重编会把
Blender 拖死，所以超过 ``LIVE_FACE_LIMIT`` 直接跳过（面板上显示提示），手动点「更新」
仍然照常工作。

写生成对象会再触发一次本处理器 —— 那一次指纹没变，直接返回，不会递归。
"""

from __future__ import annotations

import bpy

from . import tube, weave

__all__ = ["LIVE_FACE_LIMIT", "install", "on_update", "remove"]

#: 实时更新能接受的源网格面数上限（超过就只在手动更新时重编）。
LIVE_FACE_LIMIT = 20000


def on_update(scene, depsgraph):
    """每次依赖图求值后来看看源网格变了没有。"""
    settings = getattr(scene, "gen_celtic", None)
    if settings is None or not settings.live:
        return
    # 只跟当前场景走：附属场景的更新不用管
    if scene is not bpy.context.scene:
        return

    source = settings.source
    if source is None or source.type != "MESH":
        return
    if tube.find_object() is None:
        # 还没生成过：别在别人编辑网格时凭空长出对象来
        return
    if len(source.data.polygons) > LIVE_FACE_LIMIT:
        return

    try:
        weave.apply(settings)
    except Exception as exc:
        # 处理器里不能把 traceback 甩给用户；同一个错误只说一次（写属性本身也会
        # 触发依赖图更新，值没变就不再写，免得来回打转）
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
    """摘掉处理器（``unregister`` 必须调，否则重载会残留）。"""
    handlers = bpy.app.handlers.depsgraph_update_post
    for handler in [item for item in handlers if _ours(item)]:
        handlers.remove(handler)