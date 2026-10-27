"""场景参数：``scene.gen_dome``。

面板上所有可调项都在这里，算子只读这套值。参数按**生效方式**分三档：

* 几何（点数 / 半径 / 高度比 / 缝宽 / 封底）—— 改了要重新生成；
* 几何节点（立柱 / 生长进度 / 时间轴区间）—— 就地改修改器输入，实时；
* 外观（面板色 / 立柱色）—— 只改材质基色。

**不要**加 ``from __future__ import annotations``：``bpy.props`` 的注解必须是
真实的 ``Property`` 对象，PEP 563 的字符串注解会让注册失败。
"""

import bpy

from . import dome, mesh

__all__ = [
    "DESIGN_KEYS",
    "GenDomeSettings",
    "match_frame_end",
    "register",
    "unregister",
]

#: 参数 → 写到生成对象上的自定义属性名（换文件 / 换场景后仍能取回参数）。
DESIGN_KEYS = {
    "count": "gen_dome_count",
    "radius": "gen_dome_radius",
    "height": "gen_dome_height",
    "gap": "gen_dome_gap",
    "close_bottom": "gen_dome_close_bottom",
    "studs": "gen_dome_studs",
    "stud_radius": "gen_dome_stud_radius",
    "animate": "gen_dome_animate",
    "start_frame": "gen_dome_start_frame",
    "length": "gen_dome_length",
}


def _mark_dirty(self, context):
    """几何参数变了：每一代都要重算（会改顶点坐标的东西都在这一档）。"""
    self.dirty = True


def _apply_studs(self, context):
    mesh.apply_studs(self)


def match_frame_end(settings, context) -> None:
    """播放区间盖不住生长区间时，把场景的「结束帧」拉到刚好够。

    生长是「起始帧 → 起始帧+时长」这段，如果场景的 ``frame_end`` 比它小，按播放键播放到
    场景末尾就停了，看不到最后几帧（穹顶长不满）。只往**大**改，从不缩短用户自己的区间 ——
    用户想留更长的尾巴没问题。
    """
    if context is None or not settings.animate:
        return
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    needed = int(settings.start_frame) + max(1, int(settings.length))
    scene.frame_end = max(scene.frame_end, needed)


def _apply_growth(self, context):
    mesh.apply_growth(self)
    match_frame_end(self, context)


def _apply_colors(self, context):
    mesh.set_colors(self)


class GenDomeSettings(bpy.types.PropertyGroup):
    """``scene.gen_dome``：一次生成所需的全部输入。"""

    # --- 几何（改完要重新生成） -------------------------------------------

    count: bpy.props.IntProperty(
        name="终点种子数",
        description="最后一代的点数（= 面板数）：斐波那契点按球面面积均匀铺开，"
        "每一代都比上一代多，面板因此越来越多、越来越小。第 1 代固定 3 个点"
        "（3 块大面板就铺满整个半球，和参考实现一样）",
        default=250,
        min=dome.MIN_POINTS,
        max=dome.MAX_POINTS,
        soft_max=400,
        update=_mark_dirty,
    )

    radius: bpy.props.FloatProperty(
        name="半径",
        description="赤道半径（世界单位）",
        default=1.0,
        min=0.001,
        soft_max=10.0,
        precision=3,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_mark_dirty,
    )

    height: bpy.props.FloatProperty(
        name="高度比",
        description="顶点高度 = 赤道半径 × 这个值：1 = 半球，小于 1 是扁穹顶，"
        "大于 1 是尖穹顶",
        default=1.0,
        min=0.02,
        max=8.0,
        soft_max=2.0,
        precision=3,
        update=_mark_dirty,
    )

    gap: bpy.props.FloatProperty(
        name="缝宽",
        description="面板之间留的缝（世界单位）：每块面板的边界各往里退一半，"
        "缝宽处处相等。0（默认）= 面板紧贴成完整球壳，值太大面板会被缝吃掉",
        default=0.0,
        min=0.0,
        soft_max=0.2,
        precision=4,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_mark_dirty,
    )

    close_bottom: bpy.props.BoolProperty(
        name="封底",
        description="用口沿那一圈顶点封一张底板：没缝时正好封成实心碗底。"
        "默认不封（穹顶就是个开口的碗），要实心底就勾上",
        default=False,
        update=_mark_dirty,
    )

    # --- 生长 -------------------------------------------------------------

    animate: bpy.props.BoolProperty(
        name="跟时间轴生长",
        description="生长进度改由场景时间算：(当前帧 - 起始帧) / 时长。"
        "播放时一帧挑一代，几何是烘好的，所以倒放 / 循环 / 拖时间轴都是实时的",
        default=True,
        update=_apply_growth,
    )

    start_frame: bpy.props.IntProperty(
        name="起始帧",
        description="从哪一帧开始长（只在「跟时间轴生长」打开时生效）",
        default=1,
        update=_apply_growth,
    )

    length: bpy.props.IntProperty(
        name="时长",
        description="长满用多少帧（只在「跟时间轴生长」打开时生效）。"
        "点数 3 → 终点种子数 会摊在这段帧数里：一代一点，代数受点数限制，"
        "所以帧数比点数多时每一代停一两帧。改它不用重新生成；"
        "场景的结束帧比「起始帧 + 时长」小时会自动跟着拉大，免得播放到不了最后一帧",
        default=250,
        min=1,
        soft_max=400,
        update=_apply_growth,
    )

    grow: bpy.props.FloatProperty(
        name="生长进度",
        description="0 = 第一代（点数最少），1 = 最后一代；每一代都是完整的 Voronoi 划分，"
        "所以拖动它看到的是「点数变多、单元变小」而不是「越删越少」",
        default=1.0,
        min=0.0,
        max=1.0,
        precision=2,
        update=_apply_growth,
    )

    # --- 几何节点（就地生效） ---------------------------------------------

    studs: bpy.props.BoolProperty(
        name="立柱",
        description="在每块面板的种子上放一个小球（几何节点实例化）；"
        "面板还没长出来的那一代，它的立柱也不在",
        default=True,
        update=_apply_studs,
    )

    stud_radius: bpy.props.FloatProperty(
        name="立柱半径",
        description="立柱小球的半径（世界单位）。点数多的时候面板很小，"
        "别让小球比面板还大",
        default=0.012,
        min=0.0,
        soft_max=0.2,
        precision=4,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_apply_studs,
    )

    # --- 外观 -------------------------------------------------------------

    panel_color: bpy.props.FloatVectorProperty(
        name="面板色",
        description="面板材质基色（默认色板的绢白，线性存储 —— 面板显示的色块就是它）",
        size=3,
        subtype="COLOR_GAMMA",
        default=mesh.DEFAULT_PANEL,
        min=0.0,
        max=1.0,
        update=_apply_colors,
    )

    stud_color: bpy.props.FloatVectorProperty(
        name="立柱色",
        description="立柱材质基色（默认色板的赭石）",
        size=3,
        subtype="COLOR_GAMMA",
        default=mesh.DEFAULT_STUD,
        min=0.0,
        max=1.0,
        update=_apply_colors,
    )

    # --- 状态（只读） -----------------------------------------------------

    dirty: bpy.props.BoolProperty(
        name="待更新",
        description="参数改过但还没重新生成",
        default=True,
    )

    last_faces: bpy.props.IntProperty(
        name="面板数",
        default=0,
    )

    last_verts: bpy.props.IntProperty(
        name="顶点数",
        default=0,
    )

    last_generations: bpy.props.IntProperty(
        name="代数",
        default=0,
    )

    last_ms: bpy.props.FloatProperty(
        name="耗时",
        default=0.0,
    )

    last_note: bpy.props.StringProperty(
        name="提示",
        default="",
    )

    error: bpy.props.StringProperty(
        name="错误",
        default="",
    )

    # --- 派生量 -----------------------------------------------------------

    def geometry_params(self) -> dict:
        """转成 ``dome.build_generations`` 的关键字参数。"""
        return {
            "count": int(self.count),
            "generations": self.generation_count(),
            "radius": float(self.radius),
            "height": float(self.height),
            "gap": float(self.gap),
            "close_bottom": bool(self.close_bottom),
        }

    def generation_count(self) -> int:
        """这次要烘几代（决定了网格里有多少块面）。"""
        return dome.generation_count(
            int(self.count), int(self.length), bool(self.animate)
        )

    def estimated_faces(self) -> int:
        """烘出来的大致面数（面板估算：代数 × 点数 / 2）。"""
        generations = self.generation_count()
        return max(1, generations * int(self.count) // 2 + generations)

    def points_per_generation(self) -> float:
        """每代加几个点：``(终点种子数 - 3) / (代数 - 1)``。

        代数被面数预算压下来时这个数会大于 1（跳点）；代数等于点数时正好 1。
        """
        generations = self.generation_count()
        if generations <= 1:
            return 0.0
        return (int(self.count) - dome.MIN_POINTS) / (generations - 1)

    def frames_per_generation(self) -> float:
        """每代停留几帧：``时长 / (代数 - 1)``（一代一帧时正好 1.0）。"""
        generations = self.generation_count()
        if generations <= 1:
            return 0.0
        return int(self.length) / (generations - 1)

    @property
    def apex_height(self) -> float:
        """顶点高度（世界单位）。"""
        return float(self.radius) * float(self.height)


def register():
    bpy.types.Scene.gen_dome = bpy.props.PointerProperty(type=GenDomeSettings)


def unregister():
    del bpy.types.Scene.gen_dome
