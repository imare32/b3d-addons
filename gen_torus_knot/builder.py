"""重算入口：算点 → 写 GP 笔划 → 挂扫管修改器 → 把参数记回对象。

手动生成（算子）与自动更新（:mod:`live`）走**同一条**路 —— 口径一致，行为可预期：
面板上看到的几何永远是「当前参数」算出来的那一份。

对象已存在时就地更新（:func:`gen_torus_knot.gp.build_object` 重建图层，对象上的修改器
与自定义属性都留着），所以反复重算不会把关掉的管状实体又打开、也不会把参数记录抖掉。
"""

from __future__ import annotations

from . import gp, tube
from .knot import build
from .props import store_design

__all__ = ["rebuild"]


def rebuild(settings, *, collection=None) -> tuple[int, int, int]:
    """按参数重算结并写进生成对象，返回 ``(点数, 股数, 实际分段)``。

    :param settings: ``scene.gen_torus_knot``。
    :param collection: 新建对象时链到哪个 collection；``None`` = 不动已有对象的归属
        （自动更新走这条：结在哪个 collection 里是用户的事，重算不该把它搬家）。
    :raises Exception: 算点 / 写属性 / 挂修改器任一步失败，异常原样抛给调用方。
    """
    core = build(**settings.knot_arguments())
    obj = gp.build_object(
        core,
        width=float(settings.width),
        colour=settings.stroke_colour,
        collection=collection,
    )
    # 修改器一直挂着：管状实体是个开关，打开是瞬时的，参数也能从对象上回读
    found = tube.ensure_modifier(
        obj, float(settings.tube_radius), int(settings.tube_resolution)
    )
    tube.set_geometry_material(
        found.node_group, tube.material(settings.linear_colour())
    )
    tube.set_enabled(obj, bool(settings.tube))
    store_design(obj, settings)
    return core.vertex_count, core.strand_count, core.resolution
