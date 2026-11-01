"""重算入口：算路径 → 写 GP 笔划 → 记参数与包围盒。

手动生成（算子）与自动更新（:mod:`live`）走**同一条**路 —— 口径一致，行为可预期：
面板上看到的几何永远是「当前参数」算出来的那一份。

对象已存在时就地更新（:func:`gen_lsystem.gp.build_object` 清空图层重建，对象上的自定义
属性与修改器都留着），所以反复重算不会把参数记录抖掉。
"""

from __future__ import annotations

from . import gp, presets, turtle
from .props import store_design

__all__ = ["rebuild"]


def rebuild(settings, *, collection=None) -> turtle.Skeleton:
    """按参数重算并写进生成对象，返回 :class:`gen_lsystem.turtle.Skeleton`。

    :param settings: ``scene.gen_lsystem``。
    :param collection: 新建对象时链到哪个 collection；``None`` = 不动已有对象的归属
        （自动更新走这条：图案在哪个 collection 里是用户的事，重算不该把它搬家）。
    :raises Exception: 规则写错 / 参数不是数字 / 展开太大 —— 异常原样抛给调用方。
    """
    if not len(settings.rules):
        # 规则集合是空的（新文件 / 被清空）：兜底套回预设，免得「点生成什么都不出来」
        preset = presets.by_key(settings.preset) or presets.default()
        presets.apply_to(settings, preset)

    skeleton = turtle.build(**settings.system_arguments())
    obj = gp.build_object(
        skeleton,
        frame=int(settings.frame),
        collection=collection,
        **settings.look_arguments(),
    )
    store_design(obj, settings)
    return skeleton
