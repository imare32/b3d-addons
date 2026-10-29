"""场景参数：``scene.sim_pendulum``。

面板上所有可调项都在这里，handler 每帧只读这套值，算子只做编排。三档参数语义不同：

* **几何**（网格边长 / 初值 / 间距 / 初角速度 / 输出后端）：变了要重建相空间场与笔划拓扑，
  改它们把 :attr:`SimPendulumSettings.dirty` 置位，下一帧由 handler 自动重建；
* **物理**（重力 / 杆长 / 质量 / 时间步 / 子步）：积分器实参，下一帧就生效，不重建；
* **显示**（配色 / 满标 / 笔半径 / 扣子倍数 / 单色）：不推进物理，直接重刷当前帧。

**不要**加 ``from __future__ import annotations``：``bpy.props`` 的注解必须是真实的
``Property`` 对象，PEP 563 的字符串注解会让注册失败。
"""

import bpy

from . import field, sim, strokes
from .palette import hex_to_rgb

__all__ = [
    "INIT_ITEMS",
    "OUTPUT_ITEMS",
    "SimPendulumSettings",
    "register",
    "unregister",
]

#: 初值排布。
INIT_ITEMS = list(field.INIT_MODES)

#: 求解器后端：自动（有 GPU 就用）/ 纯 NumPy / 强制 Taichi（失败会报错并说明原因）。
BACKEND_ITEMS = [
    (
        field.BACKEND_AUTO,
        "自动",
        "能 import taichi 且 GPU 可用就走 Taichi（1 万摆约 1 ms/帧），否则回退纯 NumPy",
    ),
    (
        field.BACKEND_NUMPY,
        "NumPy",
        "永远走纯 NumPy：零依赖，1 万摆积分约 10 ms/帧",
    ),
    (
        field.BACKEND_TAICHI,
        "Taichi",
        "强制 GPU 后端；初始化失败会直接报错（面板显示原因），不会悄悄回退",
    ),
]

#: 输出后端。
OUTPUT_ITEMS = [
    (
        sim.OUTPUT_GP,
        "Grease Pencil",
        (
            "画成一个摆三条 GP 笔划（下摆 / 上摆 / 扣子）：杆等宽、两端圆头，"
            "扣子压住接缝 —— 本仓库的默认画法"
        ),
    ),
    (
        sim.OUTPUT_MESH,
        "网格 + 几何节点",
        (
            "画成网格顶点（5 顶点 / 2 条边）+ 几何节点扫成圆管：数据量与 GP 完全一样，"
            "用来对照两条同步路径的开销；孤立顶点没有几何，所以网格后端没有扣子"
        ),
    ),
    (
        sim.OUTPUT_IMAGE,
        "图像（一像素一个摆）",
        (
            "把 N×N 个摆直接写成一张 N×N 的图，逐帧 image.pixels.foreach_set —— "
            "点阵画法的本命后端：百万摆时不用建 100 万条笔划，每帧只写 4 个 float32/摆"
        ),
    ),
]


def _mark_geometry(self, context):
    """几何 / 后端变了：场与拓扑都要重建。"""
    self.dirty = True


#: 换到点阵时的缺省规模：26 万像素，Taichi 上 ~76 fps。
#: 没有 GPU（上一次构建走的是 NumPy）就退回 128²，否则一帧要等一秒多。
DOT_DEFAULT_N = 512
DOT_DEFAULT_N_NUMPY = 128

#: 换回杆画法时，N 超过这个值就收回 100（40 万条笔划会卡死渲染）。
ROD_MAX_N = 128
ROD_DEFAULT_N = 100


def _apply_layout(self, context):
    """换画法时把"对位的后端 + 合理规模 + 配色"一并摆好。

    * 点阵 → 图像后端、N=512、配色切全色相（斜纹色相就是给这个规模看的）；
    * 杆 → GP、N 太大就收回 100、配色不动（用户选的档位不该被抢）。
    """
    if str(self.layout) == strokes.LAYOUT_DOTS:
        if str(self.output) != sim.OUTPUT_IMAGE:
            self.output = sim.OUTPUT_IMAGE
        fallback = (
            DOT_DEFAULT_N_NUMPY if sim.STATE.get("solver") == "numpy" else DOT_DEFAULT_N
        )
        if int(self.n_grid) < fallback:
            self.n_grid = fallback
        if str(self.colour_mode) != strokes.MODE_SPECTRUM:
            self.colour_mode = strokes.MODE_SPECTRUM
    else:
        if str(self.output) == sim.OUTPUT_IMAGE:
            self.output = sim.OUTPUT_GP
        if int(self.n_grid) > ROD_MAX_N:
            self.n_grid = ROD_DEFAULT_N
    self.dirty = True


def _apply_display(self, context):
    """显示参数变了：不推进物理，把当前帧重刷一遍。"""
    sim.redraw(self)


class SimPendulumSettings(bpy.types.PropertyGroup):
    """``scene.sim_pendulum``：一次模拟所需的全部输入。"""

    # --- 几何（改后重建） --------------------------------------------------

    n_grid: bpy.props.IntProperty(
        name="边长 N",
        description="N×N 个双摆。杆画法：16 → 256 个摆（1280 点），100 → 1 万个摆；"
        "点阵画法：512 → 26 万个点，1000 → 100 万个点（要 Taichi 后端）",
        default=16,
        min=2,
        max=field.MAX_N,
        step=1,
        update=_mark_geometry,
    )

    init_mode: bpy.props.EnumProperty(
        name="初值",
        description="阵列怎么铺初值：摆角平面（θ1–θ2）或相空间切片（θ1–dθ1）",
        items=INIT_ITEMS,
        default=field.INIT_ANGLES,
        update=_mark_geometry,
    )

    spacing: bpy.props.FloatProperty(
        name="间距",
        description="相邻摆的间距（世界单位）。要比 杆长 1 + 杆长 2 大，否则上一排的摆会"
        "垂到下一排的格子里",
        default=2.4,
        min=0.5,
        max=12.0,
        precision=2,
        step=10,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_mark_geometry,
    )

    spin_max: bpy.props.FloatProperty(
        name="初角速度",
        description="「相空间 θ1–dθ1」档位下纵轴的满量程 [rad/s]：从 −该值 到 +该值，"
        "其余初值为 θ2 = 0、dθ2 = 0。摆角平面档位不用它",
        default=9.0,
        min=0.0,
        max=40.0,
        precision=2,
        step=1,
        update=_mark_geometry,
    )

    layout: bpy.props.EnumProperty(
        name="画法",
        description="一个双摆画成什么：三笔杆（小/中规模）还是一个圆点（一像素一个摆，"
        "N 上千也能放得下）",
        items=strokes.LAYOUTS,
        default=strokes.LAYOUT_RODS,
        update=_apply_layout,
    )

    output: bpy.props.EnumProperty(
        name="输出",
        description="结果落到哪种对象：GP 笔划（默认）或网格 + 几何节点（对照用）",
        items=OUTPUT_ITEMS,
        default=sim.OUTPUT_GP,
        update=_mark_geometry,
    )

    backend: bpy.props.EnumProperty(
        name="求解器",
        description="积分在哪跑：自动（有 GPU 用 GPU）/ 纯 NumPy / 强制 Taichi",
        items=BACKEND_ITEMS,
        default=field.BACKEND_AUTO,
        update=_mark_geometry,
    )

    # --- 物理（实时生效） --------------------------------------------------

    gravity: bpy.props.FloatProperty(
        name="重力",
        description="重力加速度 [m/s²]：9.81 地球 / 1.62 月球 / 0 失重（摆会匀速转下去）",
        default=field.DEFAULTS["gravity"],
        min=0.0,
        max=40.0,
        precision=2,
        step=10,
    )

    l1: bpy.props.FloatProperty(
        name="上摆杆长",
        description="上摆杆长 l1 [m]：下一帧生效；取景按「杆长 − 间距」算，改完点「场景预设」重新构图",
        default=field.DEFAULTS["l1"],
        min=0.05,
        max=5.0,
        precision=3,
        step=10,
        subtype="DISTANCE",
        unit="LENGTH",
    )

    l2: bpy.props.FloatProperty(
        name="下摆杆长",
        description="下摆杆长 l2 [m]",
        default=field.DEFAULTS["l2"],
        min=0.05,
        max=5.0,
        precision=3,
        step=10,
        subtype="DISTANCE",
        unit="LENGTH",
    )

    m1: bpy.props.FloatProperty(
        name="上摆质量",
        description="上摆质量 m1 [kg]：质量比会影响混沌的样子，m1 = m2 时最「对称」",
        default=field.DEFAULTS["m1"],
        min=0.05,
        max=10.0,
        precision=2,
        step=1,
    )

    m2: bpy.props.FloatProperty(
        name="下摆质量",
        description="下摆质量 m2 [kg]",
        default=field.DEFAULTS["m2"],
        min=0.05,
        max=10.0,
        precision=2,
        step=1,
    )

    dt: bpy.props.FloatProperty(
        name="子步 dt",
        description="RK4 子步时长 [s]。四阶格式很稳，0.005 已经够；画面再乱也别超过 0.02",
        default=0.005,
        min=1e-4,
        max=0.02,
        precision=5,
    )

    substeps: bpy.props.IntProperty(
        name="子步数",
        description="每帧几个 RK4 子步 —— 唯一的「播放速度」旋钮：物理时间/帧 = dt × 子步。"
        "缺省 5 × 0.005 = 0.025 s，24 fps 下约 0.6 倍实时",
        default=5,
        min=1,
        max=60,
        step=1,
    )

    # --- 显示（直接重刷） --------------------------------------------------

    colour_mode: bpy.props.EnumProperty(
        name="配色",
        description="两根杆各按什么取色：全色相 / 角速度 / 相角 / 单色",
        items=strokes.MODES,
        default=strokes.MODE_SPEED,
        update=_apply_display,
    )

    speed_max: bpy.props.FloatProperty(
        name="角速度满标",
        description="角速度色带的满标 [rad/s]：到这个转速就是朱砂。缺省 12 对应约 2 圈/秒",
        default=12.0,
        min=0.5,
        max=80.0,
        precision=2,
        step=1,
        update=_apply_display,
    )

    pen_radius: bpy.props.FloatProperty(
        name="笔半径",
        description="杆的笔划半径（世界单位）：整根杆等宽、两端圆头（参考实现取 0.08）",
        default=0.08,
        min=0.005,
        max=0.5,
        precision=4,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_apply_display,
    )

    dot_scale: bpy.props.FloatProperty(
        name="扣子倍数",
        description="关节处那个圆点（扣子）的半径 = 笔半径 × 该值。参考实现的配比是 5/8 "
        "= 0.625（比杆细，收敛在两根杆的圆头里）；调到 1 以上就变成盖住接缝的大扣子",
        default=strokes.DOT_SCALE_REFERENCE,
        min=0.1,
        max=8.0,
        precision=3,
        step=1,
        update=_apply_display,
    )

    dual_upper: bpy.props.FloatVectorProperty(
        name="上摆色",
        description="配色为「双色」时上摆用的颜色（缺省取参考实现的 C1_DATA #796BAF）",
        size=3,
        subtype="COLOR_GAMMA",
        default=hex_to_rgb(strokes.DUAL_UPPER),
        min=0.0,
        max=1.0,
        update=_apply_display,
    )

    dual_lower: bpy.props.FloatVectorProperty(
        name="下摆色",
        description="配色为「双色」时下摆（连同扣子）用的颜色"
        "（缺省取参考实现的 C2_DATA #719C73）",
        size=3,
        subtype="COLOR_GAMMA",
        default=hex_to_rgb(strokes.DUAL_LOWER),
        min=0.0,
        max=1.0,
        update=_apply_display,
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
            int(self.n_grid),
            str(self.init_mode),
            round(float(self.spacing), 6),
            round(float(self.spin_max), 6),
            str(self.layout),
            str(self.output),
            str(self.backend),
        )


def register():
    bpy.types.Scene.sim_pendulum = bpy.props.PointerProperty(type=SimPendulumSettings)


def unregister():
    del bpy.types.Scene.sim_pendulum
