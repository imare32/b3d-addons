"""场景参数：``scene.sim_jacobi``。

面板上所有可调项都在这里，handler 每帧只读这套值，算子只做编排。两档参数的语义不同：

* **几何**（分辨率、后端）：变了要重建网格与求解器，改它们把
  :attr:`SimJacobiSettings.dirty` 置位，下一帧由 handler 按签名自动重建；
* **物理 / 显示**（零点个数、球半径、凹陷深度、涟漪幅度、τ、速度、子步、环面半径、
  配色）：求解器实参，下一帧就生效，不重建 —— 零点个数变了会重铺初值（与参考实现
  一致：个数一变，运动从头开始）。

**不要**加 ``from __future__ import annotations``：``bpy.props`` 的注解必须是真实的
``Property`` 对象，PEP 563 的字符串注解会让注册失败。
"""

import bpy

from . import colour, solver

__all__ = ["BACKENDS", "SimJacobiSettings", "register", "unregister"]

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
        "强制纯 NumPy 后端：不依赖 taichi，150×100 网格也够用，大网格会明显变慢",
    ),
]


def _mark_geometry(self, context):
    """几何变了：网格与求解器要重建。"""
    self.dirty = True


def _apply_display(self, context):
    """显示参数变了：不推进物理，把当前帧重刷一遍。"""
    from . import sim

    sim.redraw(self)


class SimJacobiSettings(bpy.types.PropertyGroup):
    """``scene.sim_jacobi``：一次模拟所需的全部输入。"""

    # --- 网格 / 精度（改后重建） -------------------------------------------

    nu: bpy.props.IntProperty(
        name="网格 NU",
        description="环面主方向的格数（u 方向，绕主环一圈）",
        default=150,
        min=30,
        max=400,
        step=1,
        update=_mark_geometry,
    )

    nv: bpy.props.IntProperty(
        name="网格 NV",
        description="环面管方向的格数（v 方向，绕管一圈）",
        default=100,
        min=20,
        max=300,
        step=1,
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
        description="每帧推进几个物理步：越大零点跑得越快（1 步约 110 帧绕环面一圈），"
        "单帧也更贵",
        default=1,
        min=1,
        max=8,
        step=1,
    )

    # --- 零点 / 物理（实时生效） -------------------------------------------

    num_zeros: bpy.props.IntProperty(
        name="零点个数",
        description="环面上移动的零点个数（最多 64）。个数一变会重铺初值，运动从头开始",
        default=8,
        min=1,
        max=solver.MAX_ZEROS,
        step=1,
    )

    sphere_radius: bpy.props.FloatProperty(
        name="球半径",
        description="零点的显示半径，同时是两球在环面上的三维接触距离（2×半径）的一半；"
        "越大越挤，碰撞越频繁",
        default=solver.DEFAULTS["sphere_radius"],
        min=0.02,
        max=0.25,
        precision=3,
    )

    zero_dip_depth: bpy.props.FloatProperty(
        name="凹陷深度",
        description="零点下方环面被压出的凹陷深度：0 = 只是光斑，不改几何",
        default=solver.DEFAULTS["zero_dip_depth"],
        min=0.0,
        max=1.0,
        precision=3,
    )

    z_disp_scale: bpy.props.FloatProperty(
        name="涟漪幅度",
        description="相位格纹在环面上的位移幅度：(phase_grid − 0.5)·幅度。0 = 环面光滑",
        default=solver.DEFAULTS["z_disp_scale"],
        min=0.0,
        max=0.5,
        precision=3,
        step=1,
    )

    tau_re: bpy.props.FloatProperty(
        name="τ 实部",
        description="模参数 τ 的实部：改它会让零点处的螺旋扭向变化（格纹跟着打旋）",
        default=solver.DEFAULTS["tau_re"],
        min=-1.0,
        max=1.0,
        precision=3,
    )

    tau_im: bpy.props.FloatProperty(
        name="τ 虚部",
        description="模参数 τ 的虚部（> 0）：越大级数收敛越快、零点越「圆」；接近 0 时"
        "θ1 的高阶项变强，格纹会更碎",
        default=solver.DEFAULTS["tau_im"],
        min=0.1,
        max=3.0,
        precision=3,
    )

    speed: bpy.props.FloatProperty(
        name="运动速度",
        description="每个物理步的时间增量（dt = 速度×1.5）：越小零点挪得越慢",
        default=solver.DEFAULTS["speed"],
        min=0.001,
        max=0.1,
        precision=4,
        step=1,
    )

    # --- 环面尺寸 ---------------------------------------------------------

    major_r: bpy.props.FloatProperty(
        name="主半径",
        description="环面主半径（圆心到管心的距离）",
        default=solver.DEFAULTS["major_r"],
        min=0.5,
        max=10.0,
        precision=3,
        step=10,
    )

    minor_r: bpy.props.FloatProperty(
        name="管半径",
        description="环面管半径（管的粗细）",
        default=solver.DEFAULTS["minor_r"],
        min=0.1,
        max=5.0,
        precision=3,
        step=10,
    )

    # --- 显示（直接重刷） --------------------------------------------------

    colour_mode: bpy.props.EnumProperty(
        name="配色",
        description="三种配色都由项目色板推出来",
        items=colour.MODES,
        default=colour.MODE_LATTICE,
        update=_apply_display,
    )

    # --- 状态（只读） ------------------------------------------------------

    dirty: bpy.props.BoolProperty(
        name="待重建",
        description="几何改过但还没重建（下一帧自动重建）",
        default=True,
    )

    def geometry_signature(self) -> tuple:
        """去重用的签名（与 ``sim.signature`` 判据一致，只有这一份）。"""
        return (int(self.nu), int(self.nv), str(self.backend))


def register():
    bpy.types.Scene.sim_jacobi = bpy.props.PointerProperty(type=SimJacobiSettings)


def unregister():
    del bpy.types.Scene.sim_jacobi
