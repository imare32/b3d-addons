"""场景参数：``scene.sim_reaction_diffusion``。

面板上所有可调项都在这里，handler 每帧只读这套值，算子只做编排。三档参数的语义不同：

* **几何 / 初始条件**（分辨率、场域尺寸、后端、种子模式 / 半径 / 随机种子、参考种子
  对象）：变了要重新初始化场（甚至重建网格），改它们把
  :attr:`SimReactionDiffusionSettings.dirty` 置位，下一帧由 handler 按签名自动重建；
* **物理**（``Du`` / ``Dv`` / ``F`` / ``k`` / ``dt`` / 子步）：求解器实参，下一帧就生效，
  不重建也不重置 —— 换预设就是换一组 (F, k)，图案会就地变形；
* **显示**（``z_scale`` / 配色 / 对比度）：不推进物理，直接重刷当前帧。

**不要**加 ``from __future__ import annotations``：``bpy.props`` 的注解必须是真实的
``Property`` 对象，PEP 563 的字符串注解会让注册失败。
"""

import bpy

from . import colour, solver

__all__ = [
    "BACKENDS",
    "PRESET_ITEMS",
    "SEED_MODES",
    "SimReactionDiffusionSettings",
    "register",
    "unregister",
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
        "强制纯 NumPy 后端：不依赖 taichi，200² 网格也能实时，再大就明显变慢",
    ),
]

#: 预设下拉（顺序即显示顺序，与 :data:`solver.PRESETS` 一致）。
PRESET_ITEMS = [
    (key, preset["label"], preset["note"]) for key, preset in solver.PRESETS.items()
]

#: 种子模式（``solver.seed_arrays`` 的 ``mode``）。
SEED_MODES = [
    (
        solver.SEED_CENTER,
        "中心方核",
        "正中间一个方形核（与参考实现相同），图案从中心向四周长开",
    ),
    (solver.SEED_MULTI, "五处撒种", "中心 + 四角共五个核，图案在中间对撞"),
    (solver.SEED_GRID, "规则阵列", "等距阵列，得到整齐的重复单元"),
    (solver.SEED_NOISE, "随机撒点", "随机撒很多小点，最快铺满全域，适合直接看成熟图案"),
]


def _mark_geometry(self, context):
    """几何 / 初始条件变了：场要重新初始化（网格也可能要重建）。"""
    self.dirty = True


def _apply_display(self, context):
    """显示参数变了：不推进物理，把当前帧重刷一遍。"""
    from . import sim

    sim.redraw(self)


def _apply_preset(self, context):
    """换预设：只改物理参数，就地变形（不重置场、不重建网格）。"""
    params = solver.preset_params(self.preset)
    self.Du = params["Du"]
    self.Dv = params["Dv"]
    self.F = params["F"]
    self.k = params["k"]
    # 配色归一化跟着预设走，图案的明暗才一致
    _apply_display(self, context)


class SimReactionDiffusionSettings(bpy.types.PropertyGroup):
    """``scene.sim_reaction_diffusion``：一次模拟所需的全部输入。"""

    # --- 预设（就地变形） --------------------------------------------------

    preset: bpy.props.EnumProperty(
        name="预设",
        description="经典的 Gray-Scott 参数组合：换一组 (F, k) 图案就变成另一种形态，"
        "当前场不会被重置",
        items=PRESET_ITEMS,
        default=solver.DEFAULT_PRESET,
        update=_apply_preset,
    )

    # --- 初始条件（改后重新初始化） ----------------------------------------

    seed_mode: bpy.props.EnumProperty(
        name="种子模式",
        description="初始种子的铺法：只影响起点，之后的形态由 (F, k) 决定",
        items=SEED_MODES,
        default=solver.SEED_CENTER,
        update=_mark_geometry,
    )

    seed_radius: bpy.props.FloatProperty(
        name="种子半径",
        description="种子核的半径（网格格数）：太大一开始就是一块色斑，太小可能被扩散吃掉",
        default=5.0,
        min=1.0,
        max=40.0,
        precision=1,
        update=_mark_geometry,
    )

    seed_random: bpy.props.IntProperty(
        name="随机种子",
        description="「随机撒点」用的随机种子：同一个值复现同一批点位",
        default=0,
        min=0,
        max=100000,
        update=_mark_geometry,
    )

    seed_ref: bpy.props.PointerProperty(
        name="种子网格",
        description="可选的参考网格：它的顶点（世界空间 XY 投影，归一化到 [-1,1]）各放一颗"
        "种子，用来沿着一个形状起图案；留空则只用上面的种子模式",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == "MESH",
        update=_mark_geometry,
    )

    # --- 网格 / 精度（改后重建） -------------------------------------------

    nx: bpy.props.IntProperty(
        name="网格 NX",
        description="x 方向格数。200 就能画出细图案；GPU 上 400 也实时，NumPy 后端建议 ≤ 250",
        default=200,
        min=32,
        max=1024,
        step=1,
        update=_mark_geometry,
    )

    ny: bpy.props.IntProperty(
        name="网格 NY",
        description="y 方向格数",
        default=200,
        min=32,
        max=1024,
        step=1,
        update=_mark_geometry,
    )

    width: bpy.props.FloatProperty(
        name="场域宽度",
        description="网格在 x 方向的物理尺寸（Blender 单位）：只影响显示大小与相机取景，"
        "动力学在网格单位里算",
        default=6.0,
        min=0.1,
        max=100.0,
        precision=2,
        update=_mark_geometry,
    )

    height: bpy.props.FloatProperty(
        name="场域高度",
        description="网格在 y 方向的物理尺寸",
        default=6.0,
        min=0.1,
        max=100.0,
        precision=2,
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
        description="每帧推进多少个物理时间步。Gray-Scott 要几千步才长成型，所以这是"
        "「图案生长速度」的主旋钮：GPU 上 150 也实时，NumPy 后端建议 32~64",
        default=64,
        min=1,
        max=240,
        step=1,
    )

    # --- 物理（实时生效） --------------------------------------------------

    F: bpy.props.FloatProperty(
        name="补料率 F",
        description="u 的补充速率（feed）：与 k 一起决定图案形态，F 越大图案铺得越快",
        default=solver.DEFAULTS["F"],
        min=0.001,
        max=0.120,
        precision=4,
        step=1,
    )

    k: bpy.props.FloatProperty(
        name="移除率 k",
        description="v 的移除速率（kill）：k 越大图案越稀疏、越容易死掉",
        default=solver.DEFAULTS["k"],
        min=0.020,
        max=0.075,
        precision=4,
        step=1,
    )

    Du: bpy.props.FloatProperty(
        name="扩散 Du",
        description="u 的扩散系数（网格单位）。Du/Dv 的比例决定图案尺度：比值越大图案越粗",
        default=solver.DEFAULTS["Du"],
        min=0.05,
        max=1.2,
        precision=3,
        step=1,
    )

    Dv: bpy.props.FloatProperty(
        name="扩散 Dv",
        description="v 的扩散系数。必须小于 Du，否则 v 追不上 u，图案长不出来",
        default=solver.DEFAULTS["Dv"],
        min=0.05,
        max=1.0,
        precision=3,
        step=1,
    )

    dt: bpy.props.FloatProperty(
        name="时间步 dt",
        description="显式欧拉的时间步（网格单位）。九点 stencil 要求 dt·Du ≤ 1.25，"
        "超过会出棋盘状噪点；图案长得慢请加「子步」而不是加 dt",
        default=solver.DEFAULTS["dt"],
        min=0.05,
        max=1.25,
        precision=3,
        step=1,
    )

    # --- 显示（直接重刷） --------------------------------------------------

    z_scale: bpy.props.FloatProperty(
        name="高度 Z 缩放",
        description="把 v 当作高度场（z = v·缩放）的放大倍数：0 = 纯平面着色，"
        "大于 0 才有浮雕感",
        default=0.5,
        min=0.0,
        max=5.0,
        precision=3,
        step=1,
        update=_apply_display,
    )

    colour_mode: bpy.props.EnumProperty(
        name="配色",
        description="三种配色都由项目色板推出来：图案 / 水墨 / 轮廓",
        items=colour.MODES,
        default=colour.MODE_PATTERN,
        update=_apply_display,
    )

    contrast: bpy.props.FloatProperty(
        name="对比度",
        description="配色归一化的倍数：大于 1 更早饱和（图案更「实」），小于 1 更淡",
        default=1.0,
        min=0.2,
        max=4.0,
        precision=2,
        step=10,
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
            round(float(self.width), 4),
            round(float(self.height), 4),
            str(self.backend),
            str(self.seed_mode),
            round(float(self.seed_radius), 3),
            int(self.seed_random),
            ref.name if ref is not None else "",
            len(ref.data.vertices) if ref is not None else 0,
        )


def register():
    bpy.types.Scene.sim_reaction_diffusion = bpy.props.PointerProperty(
        type=SimReactionDiffusionSettings
    )


def unregister():
    del bpy.types.Scene.sim_reaction_diffusion
