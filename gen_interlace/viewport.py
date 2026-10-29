"""视口着色检查：让 Solid 模式的 3D 视图用「顶点色 / Attribute」显示 GP 颜色。

图案的颜色写在 Grease Pencil 的 ``vertex_color`` 属性里，不靠材质（``gp.py`` 还会
清空材质槽）。而 3D 视图默认是 **Solid + 着色颜色 = Material**：没有材质可显示，
Solid 里画出来就是**全黑** —— 每次新开 Blender 都要手动把「着色 → 颜色」改成
``Attribute``（``color_type = 'VERTEX'``）或切到材质预览/渲染模式才看得见。

所以生成之前做一次检查：把当前窗口里仍停在 ``MATERIAL`` 的 Solid 视图直接切成
``VERTEX``。材质预览 / 渲染模式本来就按材质与灯光走，不动它们；用户自己选了
Object / Random / Texture / Custom 的也尊重原样。

RNA 实测（5.2.2）：``space_data.shading.color_type`` 的枚举是
``MATERIAL`` / ``OBJECT`` / ``RANDOM`` / ``VERTEX``（界面显示为 "Attribute"）/
``TEXTURE`` / ``SINGLE``（界面显示为 "Custom"）；``shading.type`` 是
``WIREFRAME`` / ``SOLID`` / ``MATERIAL`` / ``RENDERED``。
"""

from __future__ import annotations

__all__ = [
    "SOLID",
    "VERTEX",
    "ensure_vertex_colour",
    "solid_spaces",
]

VIEW_3D = "VIEW_3D"
SOLID = "SOLID"
MATERIAL = "MATERIAL"
VERTEX = "VERTEX"

#: ``ensure_vertex_colour`` 的结果。
FIXED = "fixed"
ALREADY = "already"
OTHER = "other"
NOT_SOLID = "not_solid"
NO_VIEW = "no_view"


def solid_spaces(context) -> list:
    """当前窗口里所有 3D 视图的 ``shading`` 所在的 SpaceView3D 列表。

    后台模式（没有 window / screen）返回空列表。
    """
    window = getattr(context, "window", None)
    screen = getattr(window, "screen", None) if window is not None else None
    if screen is None:
        return []

    spaces = []
    for area in screen.areas:
        if area.type != VIEW_3D:
            continue
        for space in area.spaces:
            if getattr(space, "type", None) == VIEW_3D:
                spaces.append(space)
    return spaces


def ensure_vertex_colour(context) -> tuple[str, int]:
    """把停在 ``MATERIAL`` 的 Solid 视图切成 ``VERTEX``，返回 ``(状态, 改动数)``。

    状态：``"fixed"``（改了 n 个）、``"already"``（都已经在顶点色）、``"other"``
    （用户自选了 Object / Random / Texture / Custom，尊重原样）、``"not_solid"``
    （没有 Solid 视图）、``"no_view"``（没有 3D 视图，改不了）。
    """
    spaces = solid_spaces(context)
    if not spaces:
        return NO_VIEW, 0

    solid = [space for space in spaces if space.shading.type == SOLID]
    if not solid:
        return NOT_SOLID, 0

    fixed = 0
    for space in solid:
        if space.shading.color_type == MATERIAL:
            space.shading.color_type = VERTEX
            fixed += 1

    if fixed:
        return FIXED, fixed
    if all(space.shading.color_type == VERTEX for space in solid):
        return ALREADY, 0
    return OTHER, 0
