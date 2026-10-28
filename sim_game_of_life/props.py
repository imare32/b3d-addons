"""场景参数：``scene.sim_game_of_life``。

面板上所有可调项都在这里，handler 每帧只读这套值，算子只做编排。三档参数的语义不同：

* **拓扑 / 后端**（目标网格、后端）：变了要重新提取邻域、重建求解器 —— 进签名，下一帧由
  handler 自动重建（也可以手动点「构建」）；
* **初始条件**（种子、初始密度）：只重铺第 0 代，不重提拓扑；
* **每帧节奏 / 规则 / 显示**（每代帧数、过渡帧数、插值、规则、配色、年龄尺度）：下一帧就
  生效，连代都不用重跑（规则改了会从当前状态重算下一代）。

**不要**加 ``from __future__ import annotations``：``bpy.props`` 的注解必须是真实的
``Property`` 对象，PEP 563 的字符串注解会让注册失败。
"""

import bpy

from . import colour, solver

__all__ = ["BACKENDS", "SimGameOfLifeSettings", "register", "unregister"]

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
        "强制纯 NumPy 后端：不依赖 taichi，几千个面也够快，大网格会明显变慢",
    ),
]


def _mark_reset(self, context):
    """拓扑 / 初始条件变了：下一帧由 handler 重建或重新播种。"""
    self.dirty = True


def _apply_display(self, context):
    """显示参数变了：不推进代数，把当前帧重刷一遍。"""
    from . import sim

    sim.redraw(self)


class SimGameOfLifeSettings(bpy.types.PropertyGroup):
    """``scene.sim_game_of_life``：一次模拟所需的全部输入。"""

    # --- 目标网格 ----------------------------------------------------------

    target: bpy.props.PointerProperty(
        name="目标网格",
        description="生命游戏跑在它的面上（面 = 细胞，共顶点的面互为邻居）。"
        "留空则用插件自建的缺省环面；几何不会被改动，只写 alive / age / color 三个属性。"
        "只有「面大致均匀、每个面 8 个邻居」的四边形网格才跑得出棋盘效果："
        "低模猴头那种 500 个面的网格上一个面就是一大块，三角面和边界面会跑出不同的规则方言",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == "MESH",
        update=_mark_reset,
    )

    # --- 初始条件 ----------------------------------------------------------

    seed: bpy.props.IntProperty(
        name="种子",
        description="初始状态的随机种子：同一个种子 + 同一个网格 = 同一条演化路径",
        default=42,
        min=0,
        update=_mark_reset,
    )

    density: bpy.props.FloatProperty(
        name="初始密度",
        description="初始状态里活细胞的比例：0.3 附近最容易长出经典的振荡与滑翔机",
        default=0.3,
        min=0.0,
        max=1.0,
        precision=2,
        update=_mark_reset,
    )

    # --- 节奏 --------------------------------------------------------------

    frames_per_gen: bpy.props.IntProperty(
        name="每代帧数",
        description="多少动画帧推进一代。24 fps 下 6 帧 = 每秒 4 代；调小演化更快",
        default=6,
        min=1,
        max=60,
        step=1,
    )

    transition_frames: bpy.props.IntProperty(
        name="过渡帧数",
        description="每一代开头多少帧用来在上一代与这一代之间插值（0 = 硬切，"
        "细胞逐帧闪；有过渡才看得到生灭的中间态）",
        default=4,
        min=0,
        max=60,
        step=1,
    )

    interpolation_mode: bpy.props.EnumProperty(
        name="插值",
        description="代与代之间的过渡曲线",
        items=solver.INTERPOLATIONS,
        default="SMOOTHSTEP",
    )

    # --- 规则 / 后端 -------------------------------------------------------

    rule: bpy.props.EnumProperty(
        name="规则",
        description="B/S 记号：斜杠前是「几个活邻出生」，后面是「几个活邻存活」",
        items=solver.RULE_ITEMS,
        default=solver.DEFAULT_RULE,
    )

    backend: bpy.props.EnumProperty(
        name="后端",
        description="计算后端：自动 = 有 taichi 就用 GPU，没有就纯 NumPy",
        items=BACKENDS,
        default=solver.BACKEND_AUTO,
        update=_mark_reset,
    )

    # --- 显示 --------------------------------------------------------------

    colour_mode: bpy.props.EnumProperty(
        name="配色",
        description="生死两种配色，都由项目色板推出来",
        items=colour.MODES,
        default=colour.MODE_STATE,
        update=_apply_display,
    )

    age_scale: bpy.props.FloatProperty(
        name="年龄尺度",
        description="「年龄」配色里多少代算「老」：越小越快变暖（只在年龄配色下起作用）",
        default=30.0,
        min=1.0,
        max=500.0,
        step=1,
        update=_apply_display,
    )

    # --- 状态（只读） ------------------------------------------------------

    dirty: bpy.props.BoolProperty(
        name="待重建",
        description="拓扑 / 初始条件改过但还没生效（下一帧自动处理）",
        default=True,
    )


def register():
    bpy.types.Scene.sim_game_of_life = bpy.props.PointerProperty(
        type=SimGameOfLifeSettings
    )


def unregister():
    del bpy.types.Scene.sim_game_of_life
