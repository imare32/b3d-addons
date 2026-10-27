"""场景参数：``scene.sim_catenary``。

面板上所有可调项都在这里，handler 每帧只读这套值，算子只做编排。
三档参数的语义不同：

* **几何**（环数 / 跨度 / 环尺寸 / 段数 / 相扣）：变了要重建求解器与笔划拓扑，
  改它们先把 :attr:`SimCatenarySettings.dirty` 置位，下一帧由 handler 自动重建；
* **物理**（重力 / 阻力 / 弹性 / 子步 / 迭代）：求解器实参，下一帧就生效，不重建；
* **显示**（线宽 / 颜色 / 脊线）：不动物理，直接重刷当前帧。

**不要**加 ``from __future__ import annotations``：``bpy.props`` 的注解必须是
真实的 ``Property`` 对象，PEP 563 的字符串注解会让注册失败。
"""

import bpy

from . import chain
from .palette import INK, hex_to_rgb

__all__ = [
    "COLOUR_MODES",
    "CURVE_TYPES",
    "SimCatenarySettings",
    "register",
    "unregister",
]

#: 环的配色模式。
COLOUR_MODES = [
    ("TENSION", "张力", "按链的张力给环上色：石青（松）→ 赭石 → 朱砂（紧）"),
    ("MONO", "单色", "所有环用同一个颜色"),
]

#: 笔划曲线类型。
CURVE_TYPES = [
    (
        chain.CURVE_BEZIER,
        "贝塞尔",
        (
            "每环 4 段三次曲线：径向误差约 0.027% R（R=0.06 m 时 16 µm），肉眼与 24 段"
            "折线无差，但控制点只有 1/5，逐帧写入也更快"
        ),
    ),
    (
        chain.CURVE_POLY,
        "折线",
        "每环一圈折线点（推荐 24 点）：老做法，控制点多但不必算手柄",
    ),
]


def _mark_geometry(self, context):
    """几何参数变了：求解器维度与笔划拓扑都得换，标记待重建。"""
    self.dirty = True


def _switch_curve(self, context):
    """切换曲线类型：段数按该类型的推荐值重置，并标记重建。

    两种类型的「段数」含义一样（一个环几段），但推荐值差很多（贝塞尔 4 / 折线 24），
    留着旧值会得到一个多余密的贝塞尔或一个八边形的折线，所以直接换掉。
    """
    self.segments = chain.recommended_segments(self.curve_type)
    self.dirty = True


def _apply_display(self, context):
    """显示参数变了：不推进物理，直接把当前帧重刷一遍。"""
    from . import sim

    sim.redraw(self)


class SimCatenarySettings(bpy.types.PropertyGroup):
    """``scene.sim_catenary``：一次模拟所需的全部输入。"""

    # --- 几何（改后重建） --------------------------------------------------

    n_rings: bpy.props.IntProperty(
        name="环数",
        description="链条的环数：一环占一节，求解器里就是 环数+1 个关节（奇偶都行）",
        default=101,
        min=5,
        max=401,
        step=1,
        update=_mark_geometry,
    )

    arch_a: bpy.props.FloatProperty(
        name="跨度 A",
        description="悬链线特征长度 a：越大越平缓、越小下垂越深（初始是它的镜像拱形）",
        default=chain.ARCH_A,
        min=0.5,
        max=6.0,
        precision=2,
        step=10,
        update=_mark_geometry,
    )

    ring_major: bpy.props.FloatProperty(
        name="环主半径 R",
        description="环的半径（画出来的圆弧半径）",
        default=chain.R_MAJOR,
        min=0.02,
        max=0.2,
        precision=3,
        step=1,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_mark_geometry,
    )

    ring_minor: bpy.props.FloatProperty(
        name="环管半径 r",
        description="环的「线径」：与直段一起决定相扣节距 L+2(R−r)，不影响笔划粗细（那是线宽）",
        default=chain.R_MINOR,
        min=0.002,
        max=0.06,
        precision=3,
        step=1,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_mark_geometry,
    )

    ring_straight: bpy.props.FloatProperty(
        name="直段长度 L",
        description="环的直边长度：0 = 正圆；> 0 就是真实链环那种「操场跑道」形（两段直边 + "
        "两个半圆端）。长轴沿链方向，相扣节距 = L + 2(R−r)",
        default=chain.RING_STRAIGHT,
        min=0.0,
        max=0.3,
        precision=3,
        step=1,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_mark_geometry,
    )

    curve_type: bpy.props.EnumProperty(
        name="笔划",
        description="GP 笔划的曲线类型：贝塞尔用极少的控制点画出真空曲线，折线是老做法",
        items=CURVE_TYPES,
        default=chain.CURVE_BEZIER,
        update=_switch_curve,
    )

    segments: bpy.props.IntProperty(
        name="段数",
        description="一个环几段：折线 = 一圈的控制点数（按弧长均匀取，24 点看不出棱）；"
        "贝塞尔 = 几段三次曲线（正圆时 4 段 = 每段 90°；跑道形本来就精确，此项不参与）",
        default=chain.BEZIER_SEGMENTS,
        min=3,
        max=96,
        step=1,
        update=_mark_geometry,
    )

    # --- 物理（实时生效） --------------------------------------------------

    gravity: bpy.props.FloatProperty(
        name="重力",
        description="重力加速度 [m/s²]：9.81 地球 / 1.62 月球 / 0 失重。只影响下落快慢，"
        "不影响稳态形状；张力色带上限随它重标定",
        default=chain.GRAVITY,
        min=0.0,
        max=25.0,
        precision=2,
        step=10,
    )

    drag: bpy.props.IntProperty(
        name="环境阻力",
        description="链下落时受到的阻力（0~100%）：0% = 自由落体长摆，100% = 约 2m/s 匀速。"
        "整条滑杆都是有效区间",
        default=15,
        min=0,
        max=100,
        step=1,
    )

    elasticity: bpy.props.IntProperty(
        name="弹性",
        description="杆约束的柔度（0~100%）：0% = 刚杆（与理论离散链最吻合），"
        "100% 稳态下垂会多出约 0.4 m",
        default=0,
        min=0,
        max=100,
        step=1,
    )

    bend_limit: bpy.props.FloatProperty(
        name="折角上限",
        description="关节处允许的最大折角（°）。真实链环有体积，不可能对折；不限制的话"
        "锚点那根杆会整个甩翻折回来，长链环会看着'飞到邻居身上'。180 = 不限制",
        default=110.0,
        min=30.0,
        max=180.0,
        precision=0,
        step=10,
    )

    substeps: bpy.props.IntProperty(
        name="子步",
        description="每帧的物理子步数（每帧走满 1/fps 的物理时间，与帧率解耦）。"
        "起步就够用，加它是为了更硬的杆",
        default=10,
        min=1,
        max=24,
        step=1,
    )

    iterations: bpy.props.IntProperty(
        name="迭代",
        description="每子步的杆约束投影次数（精度档位）。实测 12 次与 20 次的稳态"
        "RMSE 差异在小数点后第四位，性价比最高的档位",
        default=12,
        min=2,
        max=40,
        step=1,
    )

    # --- 显示（直接重刷） --------------------------------------------------

    line_width: bpy.props.FloatProperty(
        name="线宽",
        description="环的笔划半径（世界单位，实际线宽 = 2 倍）",
        default=0.007,
        min=0.0005,
        max=0.05,
        precision=4,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_apply_display,
    )

    colour_mode: bpy.props.EnumProperty(
        name="配色",
        description="环的颜色：按张力上色，或全部单色",
        items=COLOUR_MODES,
        default="TENSION",
        update=_apply_display,
    )

    ring_colour: bpy.props.FloatVectorProperty(
        name="单色",
        description="配色为「单色」时用的环色",
        size=3,
        subtype="COLOR_GAMMA",
        default=hex_to_rgb(INK),
        min=0.0,
        max=1.0,
        update=_apply_display,
    )

    show_spine: bpy.props.BoolProperty(
        name="脊线",
        description="沿环心画一条折线（链的中心线），放在环下面当辅助线",
        default=False,
        update=_apply_display,
    )

    spine_width: bpy.props.FloatProperty(
        name="脊线宽",
        description="脊线的笔划半径（世界单位）",
        default=0.0035,
        min=0.0005,
        max=0.03,
        precision=4,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_apply_display,
    )

    smooth_tension: bpy.props.BoolProperty(
        name="张力平滑",
        description="对张力做指数滑动平均，避免下落/振荡阶段热力图闪烁；稳态无影响",
        default=True,
        update=_apply_display,
    )

    ema_alpha: bpy.props.FloatProperty(
        name="平滑系数",
        description="0 = 不平滑（瞬时值），1 = 完全不动；建议 0.1~0.3",
        default=0.2,
        min=0.0,
        max=1.0,
        precision=3,
        step=1,
        update=_apply_display,
    )

    # --- 状态（只读） ------------------------------------------------------

    dirty: bpy.props.BoolProperty(
        name="待重建",
        description="几何参数改过但还没重建（下一帧自动重建）",
        default=True,
    )

    def geometry_signature(self) -> tuple:
        """去重用的几何签名（与 ``sim.signature`` 判据一致）。"""
        return (
            str(self.curve_type),
            int(self.n_rings),
            round(float(self.arch_a), 6),
            round(float(self.ring_major), 6),
            round(float(self.ring_minor), 6),
            round(float(self.ring_straight), 6),
            int(self.segments),
        )


def register():
    bpy.types.Scene.sim_catenary = bpy.props.PointerProperty(type=SimCatenarySettings)


def unregister():
    del bpy.types.Scene.sim_catenary
