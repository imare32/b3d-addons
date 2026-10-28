"""场景参数：``scene.sim_hilbert``。

面板上所有可调项都在这里，handler 每帧只读这套值，算子只做编排。三档参数的语义不同：

* **几何**（阶数 / 尺寸 / 落差 / 邻域 / 后端）：变了要重建曲线拓扑与求解器，改它们把
  :attr:`SimHilbertSettings.dirty` 置位，下一帧由 handler 自动重建；
* **物理**（重力 / 刚度 / 阻尼 / 质量 / 时间步 / 子步 / 地面 / 弹性 / 摩擦）：求解器实参，
  下一帧就生效，不重建；
* **显示**（配色 / 尺度 / 线宽 / 单色 / 地面）：不推进物理，直接重刷当前帧。

**不要**加 ``from __future__ import annotations``：``bpy.props`` 的注解必须是真实的
``Property`` 对象，PEP 563 的字符串注解会让注册失败。
"""

import bpy

from . import colour, curve, solver
from .palette import INK, hex_to_rgb

__all__ = ["BACKENDS", "NEIGHBOURS", "SimHilbertSettings", "register", "unregister"]

#: 邻域档位（枚举标识必须是字符串，取用时再转回 int）。
NEIGHBOURS = [
    (str(value), name, description)
    for value, name, description in curve.NEIGHBOUR_ITEMS
]

#: 后端选项。
BACKENDS = [
    (
        solver.BACKEND_AUTO,
        "自动",
        "能 import taichi 就用它的 GPU kernel，否则回退纯 NumPy（慢一些，但没有依赖）",
    ),
    (
        solver.BACKEND_TAICHI,
        "Taichi GPU",
        "强制 Taichi：需要 Blender 的 Python 环境里装了 taichi（scripts/modules 或 wheel）",
    ),
    (
        solver.BACKEND_NUMPY,
        "NumPy CPU",
        "强制纯 NumPy 后端：不依赖 taichi。3 阶（512 顶点）约 40 ms/帧，4 阶会到几百毫秒",
    ),
]


def _mark_geometry(self, context):
    """几何 / 后端变了：拓扑与求解器都要重建。"""
    self.dirty = True


def _apply_display(self, context):
    """显示参数变了：不推进物理，把当前帧重刷一遍。"""
    from . import sim

    sim.redraw(self)


def _toggle_ground(self, context):
    """地面开关：当场建/删那片纸面（不重建物理）。"""
    from . import scene as scene_mod
    from . import sim

    scene = context.scene if context is not None else bpy.context.scene
    if self.show_ground:
        span, _target = scene_mod.frame_of(self.size, self.drop_height)
        scene_mod.ensure_ground(scene, span, float(self.ground_height))
    else:
        scene_mod.remove_ground()
    sim.redraw(self)


class SimHilbertSettings(bpy.types.PropertyGroup):
    """``scene.sim_hilbert``：一次模拟所需的全部输入。"""

    # --- 几何（改后重建） --------------------------------------------------

    order: bpy.props.IntProperty(
        name="阶数",
        description="希尔伯特曲线阶数：1 = 2³ = 8 点，3 = 8³ = 512 点（缺省），"
        "4 = 16³ = 4096 点。阶数变高时整条曲线的跨度不变、格子变密",
        default=3,
        min=1,
        max=curve.MAX_ORDER,
        step=1,
        update=_mark_geometry,
    )

    size: bpy.props.FloatProperty(
        name="尺寸",
        description="格子的包围盒边长（不是格距）：格距 = 尺寸 / (2^阶数 − 1)",
        default=4.0,
        min=0.5,
        max=20.0,
        precision=2,
        step=10,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_mark_geometry,
    )

    drop_height: bpy.props.FloatProperty(
        name="落差",
        description="初始整体抬高（z 偏移）：格子中心在 z = 落差，底面因此在地面上方"
        "落差 − 尺寸/2 处。小于 尺寸/2 时有一半格子是从地面里长出来的",
        default=3.0,
        min=0.0,
        max=30.0,
        precision=2,
        step=10,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_mark_geometry,
    )

    neighbours: bpy.props.EnumProperty(
        name="邻域",
        description="弹簧网络怎么连：档位越高越硬。曲线的走向（顶点 0→N−1）不受影响",
        items=NEIGHBOURS,
        default="26",
        update=_mark_geometry,
    )

    backend: bpy.props.EnumProperty(
        name="后端",
        description="计算后端：自动 = 有 taichi 就用 GPU，没有就纯 NumPy",
        items=BACKENDS,
        default=solver.BACKEND_AUTO,
        update=_mark_geometry,
    )

    # --- 物理（实时生效） --------------------------------------------------

    gravity: bpy.props.FloatProperty(
        name="重力",
        description="重力加速度 [m/s²]：9.81 地球 / 1.62 月球 / 0 失重。只影响下落与冲击的"
        "力度，不改弹簧本身",
        default=solver.DEFAULTS["gravity"],
        min=0.0,
        max=30.0,
        precision=2,
        step=10,
    )

    stiffness: bpy.props.FloatProperty(
        name="刚度",
        description="弹簧刚度 k：越大越硬。缺省 200 落下去会明显坍缩、摊开；"
        "2000 以上基本就是一块硬胶体在弹。稳态沉降量 ∝ 1/k",
        default=200.0,
        min=1.0,
        max=5000.0,
        precision=1,
        step=1,
    )

    damping: bpy.props.FloatProperty(
        name="阻尼",
        description="弹簧阻尼 c（只作用在沿弹簧轴的速度差上）：越大能量耗散越快，"
        "落地后晃动次数越少",
        default=20.0,
        min=0.0,
        max=200.0,
        precision=1,
        step=1,
    )

    mass: bpy.props.FloatProperty(
        name="质量",
        description="每个质点的质量 m：和刚度一起决定固有频率 ω ≈ √(k/m)。改它要同时"
        "看时间步会不会失稳",
        default=solver.DEFAULTS["mass"],
        min=0.01,
        max=100.0,
        precision=3,
        step=1,
    )

    dt: bpy.props.FloatProperty(
        name="时间步 dt",
        description="子步时长 [s]。显式欧拉对刚性弹簧很敏感：画面开始抖/炸之前先减 dt，"
        "想推进快就加子步",
        default=solver.DEFAULTS["dt"],
        min=1e-5,
        max=5e-3,
        precision=6,
    )

    substeps: bpy.props.IntProperty(
        name="子步",
        description="每帧推进几个子步 —— 唯一的「播放速度」旋钮。物理时间/帧 = dt × 子步；"
        "缺省 2e-4 × 150 = 0.03 s，也就是 24 fps 下 0.72 倍实时",
        default=150,
        min=1,
        max=600,
        step=1,
    )

    ground_height: bpy.props.FloatProperty(
        name="地面高度",
        description="碰撞平面的 z：格子落到这里就被钳住（并按下方的弹性 / 摩擦反弹）",
        default=solver.DEFAULTS["ground_height"],
        min=-20.0,
        max=20.0,
        precision=3,
        step=1,
        subtype="DISTANCE",
        unit="LENGTH",
    )

    restitution: bpy.props.FloatProperty(
        name="地面弹性",
        description="撞地时法向速度保留的比例：0 = 完全吸收，1 = 完全反弹。"
        "弹簧本身也会耗散能量，所以这个值对观感影响不大",
        default=solver.DEFAULTS["restitution"],
        min=0.0,
        max=1.0,
        precision=2,
        step=1,
    )

    friction: bpy.props.FloatProperty(
        name="地面摩擦",
        description="撞地时切向速度损失的比例：越大落地后越不容易滑开",
        default=solver.DEFAULTS["friction"],
        min=0.0,
        max=1.0,
        precision=2,
        step=1,
    )

    # --- 显示（直接重刷） --------------------------------------------------

    colour_mode: bpy.props.EnumProperty(
        name="配色",
        description="逐点颜色按什么走：速度 / 应变 / 单色",
        items=colour.MODES,
        default=colour.MODE_SPEED,
        update=_apply_display,
    )

    speed_max: bpy.props.FloatProperty(
        name="速度满标",
        description="速度色带的满标 [m/s]：落到这个速度就是朱砂。缺省 8 对应从 3 m 高度"
        "自由落体的冲击速度（约 7.7 m/s）",
        default=8.0,
        min=0.5,
        max=30.0,
        precision=2,
        step=1,
        update=_apply_display,
    )

    strain_max: bpy.props.FloatProperty(
        name="应变满标",
        description="应变色带的满标：逐点平均 |L−L0|/L0 到这里就是朱砂。冲击峰值约 0.23",
        default=0.25,
        min=0.01,
        max=1.0,
        precision=3,
        step=1,
        update=_apply_display,
    )

    line_width: bpy.props.FloatProperty(
        name="线宽",
        description="笔划半径（世界单位，实际线宽是它的 2 倍）",
        default=0.015,
        min=0.001,
        max=0.1,
        precision=4,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_apply_display,
    )

    mono_colour: bpy.props.FloatVectorProperty(
        name="单色",
        description="配色为「单色」时用的颜色",
        size=3,
        subtype="COLOR_GAMMA",
        default=hex_to_rgb(INK),
        min=0.0,
        max=1.0,
        update=_apply_display,
    )

    show_ground: bpy.props.BoolProperty(
        name="地面纸面",
        description="在碰撞平面上放一张自发光纸面：没有灯光也能渲染，落地的接触面看得清",
        default=True,
        update=_toggle_ground,
    )

    # --- 状态（只读） ------------------------------------------------------

    dirty: bpy.props.BoolProperty(
        name="待重建",
        description="几何参数改过但还没重建（下一帧自动重建）",
        default=True,
    )

    def geometry_signature(self) -> tuple:
        """去重用的签名（与 ``sim.signature`` 判据一致，只有这一份）。"""
        return (
            int(self.order),
            round(float(self.size), 6),
            round(float(self.drop_height), 6),
            int(self.neighbours),
            str(self.backend),
        )


def register():
    bpy.types.Scene.sim_hilbert = bpy.props.PointerProperty(type=SimHilbertSettings)


def unregister():
    del bpy.types.Scene.sim_hilbert
