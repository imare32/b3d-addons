"""场景参数：``scene.sim_dendrite``。

面板上所有可调项都在这里，handler 每帧只读这套值，算子只做编排。三档参数的语义不同：

* **几何 / 初始条件**（分辨率、格距、后端、种子半径、初始过冷度、参考种子对象）：变了
  要重新初始化场（甚至重建网格），改它们把 :attr:`SimDendriteSettings.dirty` 置位，
  下一帧由 handler 按签名自动重建；
* **物理**（``N_sym`` / ``c_ani`` / ``alpha`` / ``theta_off`` / ``tau`` / ``kappa`` /
  ``DT_heat`` / ``dt`` / 子步）：求解器实参，下一帧就生效，不重建；
* **显示**（``z_scale`` / 配色）：不推进物理，直接重刷当前帧。

**不要**加 ``from __future__ import annotations``：``bpy.props`` 的注解必须是真实的
``Property`` 对象，PEP 563 的字符串注解会让注册失败。
"""

import math

import bpy

from . import colour, solver

__all__ = ["BACKENDS", "SimDendriteSettings", "register", "unregister"]

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
        "强制纯 NumPy 后端：不依赖 taichi，小网格也能实时，大网格会明显变慢",
    ),
]


def _mark_geometry(self, context):
    """几何 / 初始条件变了：场要重新初始化（网格也可能要重建）。"""
    self.dirty = True


def _apply_display(self, context):
    """显示参数变了：不推进物理，把当前帧重刷一遍。"""
    from . import sim

    sim.redraw(self)


class SimDendriteSettings(bpy.types.PropertyGroup):
    """``scene.sim_dendrite``：一次模拟所需的全部输入。"""

    # --- 初始条件（改后重新初始化） ----------------------------------------

    seed_ref: bpy.props.PointerProperty(
        name="种子网格",
        description="可选的参考网格：它的顶点（世界空间 XY 投影，归一化到 [-1,1]）各放一个"
        "晶核，用来做多晶/沿形状生长；留空则只有中心一个核",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == "MESH",
        update=_mark_geometry,
    )

    seed_radius: bpy.props.FloatProperty(
        name="种子半径",
        description="晶核半径（网格格数）：太小可能被相场弛豫吃掉，太大会一开始就长成圆盘",
        default=5.0,
        min=1.0,
        max=50.0,
        precision=1,
        update=_mark_geometry,
    )

    initial_dT: bpy.props.FloatProperty(
        name="初始过冷度",
        description="液体的初始 ΔT = T − T_m（< 0）：越负长得越快、枝晶越细。潜热会把界面附近"
        "的 ΔT 拉回 0，所以不能填 0",
        default=-0.5,
        min=-2.0,
        max=-0.001,
        precision=3,
        step=1,
        update=_mark_geometry,
    )

    # --- 网格 / 精度（改后重建） -------------------------------------------

    nx: bpy.props.IntProperty(
        name="网格 NX",
        description="x 方向格数。GPU 上 250 就能实时；NumPy 后端建议 ≤ 200",
        default=250,
        min=32,
        max=1024,
        step=1,
        update=_mark_geometry,
    )

    ny: bpy.props.IntProperty(
        name="网格 NY",
        description="y 方向格数",
        default=250,
        min=32,
        max=1024,
        step=1,
        update=_mark_geometry,
    )

    dx: bpy.props.FloatProperty(
        name="格距 DX",
        description="x 方向格距（决定场的物理尺寸 = NX·DX）",
        default=0.025,
        min=0.001,
        max=0.2,
        precision=4,
        update=_mark_geometry,
    )

    dy: bpy.props.FloatProperty(
        name="格距 DY",
        description="y 方向格距",
        default=0.025,
        min=0.001,
        max=0.2,
        precision=4,
        update=_mark_geometry,
    )

    backend: bpy.props.EnumProperty(
        name="后端",
        description="计算后端：自动 = 有 taichi 就用 GPU，没有就纯 NumPy",
        items=BACKENDS,
        default=solver.BACKEND_AUTO,
        update=_mark_geometry,
    )

    substeps: bpy.props.IntProperty(
        name="子步",
        description="每帧推进多少个物理时间步。相场是显式欧拉，子步多了画面推进快、"
        "单帧也贵；GPU 上 50 约 5 ms/帧，NumPy 后端建议 10~25",
        default=50,
        min=1,
        max=200,
        step=1,
    )

    # --- 物理（实时生效） --------------------------------------------------

    N_sym: bpy.props.FloatProperty(
        name="对称次数 N",
        description="枝晶的旋转对称次数：6 = 雪花状，4 = 十字/立方，3 = 三叉",
        default=solver.DEFAULTS["N_sym"],
        min=2.0,
        max=12.0,
        precision=1,
        step=1,
    )

    c_ani: bpy.props.FloatProperty(
        name="各向异性强度",
        description="界面刚度的方向调制幅度（0 = 各向同性，长成圆团；0.01~0.03 才有明显枝晶）",
        default=solver.DEFAULTS["c_ani"],
        min=0.0,
        max=0.2,
        precision=4,
        step=1,
    )

    theta_off: bpy.props.FloatProperty(
        name="晶向偏角",
        description="各向异性的基准方向（弧度）：π/8 让枝晶斜着长，避开网格轴",
        default=solver.DEFAULTS["theta_off"],
        min=-math.pi,
        max=math.pi,
        precision=4,
    )

    alpha: bpy.props.FloatProperty(
        name="界面尺度 α",
        description="界面宽度尺度（扩散张量的前置系数）：越大界面越厚、越稳，越小越锐利也越容易"
        "出现网格噪声",
        default=solver.DEFAULTS["alpha"],
        min=0.001,
        max=0.1,
        precision=4,
        step=1,
    )

    tau: bpy.props.FloatProperty(
        name="弛豫时间 τ",
        description="相场弛豫时间：越小界面推进越快。别低于参考值太多 —— 实测 1e-4 时界面会被"
        "数值振荡糊掉（59% 的格子落在 0.05~0.95 之间），3e-4 只有 4.7%。生长快慢请用「子步」调",
        default=solver.DEFAULTS["tau"],
        min=1e-4,
        max=1e-2,
        precision=6,
    )

    kappa1: bpy.props.FloatProperty(
        name="耦合 κ1",
        description="过冷度对相场驱动力的耦合系数 1",
        default=solver.DEFAULTS["kappa1"],
        min=0.0,
        max=5.0,
        precision=3,
    )

    kappa2: bpy.props.FloatProperty(
        name="耦合 κ2",
        description="过冷度耦合系数 2：arctan(κ2·ΔT) 的斜率，越大对 ΔT 越敏感",
        default=solver.DEFAULTS["kappa2"],
        min=0.0,
        max=100.0,
        precision=2,
    )

    DT_heat: bpy.props.FloatProperty(
        name="热扩散 DT",
        description="热扩散 / 潜热系数：越大热量散得越快，枝晶越细密",
        default=solver.DEFAULTS["DT_heat"],
        min=0.1,
        max=10.0,
        precision=3,
    )

    dt: bpy.props.FloatProperty(
        name="时间步 dt",
        description="显式欧拉的时间步。相场方程刚性很强，改大前先看画面有没有爆成噪点",
        default=solver.DEFAULTS["dt"],
        min=1e-7,
        max=1e-3,
        precision=7,
    )

    # --- 显示（直接重刷） --------------------------------------------------

    z_scale: bpy.props.FloatProperty(
        name="高度 Z 缩放",
        description="把相场当作高度场（z = φ·缩放）的放大倍数：0 = 纯平面着色，"
        "大于 0 才有浮雕感",
        default=0.6,
        min=0.0,
        max=5.0,
        precision=3,
        step=1,
        update=_apply_display,
    )

    colour_mode: bpy.props.EnumProperty(
        name="配色",
        description="相场 / 过冷度两种配色，都由项目色板推出来",
        items=colour.MODES,
        default=colour.MODE_PHASE,
        update=_apply_display,
    )

    # --- 状态（只读） ------------------------------------------------------

    dirty: bpy.props.BoolProperty(
        name="待重建",
        description="几何 / 初始条件改过但还没重建（下一帧自动重建）",
        default=True,
    )

    def geometry_signature(self) -> tuple:
        """去重用的签名（与 ``sim.signature`` 判据一致，只有这一份）。"""
        ref = self.seed_ref
        return (
            int(self.nx),
            int(self.ny),
            round(float(self.dx), 6),
            round(float(self.dy), 6),
            str(self.backend),
            round(float(self.seed_radius), 4),
            round(float(self.initial_dT), 5),
            ref.name if ref is not None else "",
            len(ref.data.vertices) if ref is not None else 0,
        )


def register():
    bpy.types.Scene.sim_dendrite = bpy.props.PointerProperty(type=SimDendriteSettings)


def unregister():
    del bpy.types.Scene.sim_dendrite
