"""场景参数：``scene.sim_differential``。

面板上所有可调项都在这里，handler 每帧只读这套值，算子只做编排。三档参数的语义不同：

* **几何 / 初始条件**（初始节点数、初始半径、抖动、随机种子、节点上限、后端）：变了要
  重建求解器（节点缓冲与 GP 笔划都是定长的），改它们把 :attr:`Settings.dirty` 置位，
  下一帧由 handler 按签名自动重建；
* **物理**（斥力半径 / 权重、平滑权重、边界半径 / 刚性、分裂距离、生长速率、锁 Z、
  子步）：求解器实参，下一帧就生效，不重建；
* **显示**（配色、线宽、单色）：不推进物理，直接重刷当前帧。

**不要**加 ``from __future__ import annotations``：``bpy.props`` 的注解必须是真实的
``Property`` 对象，PEP 563 的字符串注解会让注册失败。
"""

import bpy

from . import colour, solver
from .palette import INK, hex_to_rgb

__all__ = ["BACKENDS", "SimDifferentialSettings", "register", "unregister"]

#: 后端选项。
BACKENDS = [
    (
        solver.BACKEND_AUTO,
        "自动",
        "能 import taichi 就用它的 GPU kernel，否则回退纯 NumPy（空间哈希，几千节点也能实时）",
    ),
    (
        solver.BACKEND_TAICHI,
        "Taichi GPU",
        "强制 Taichi：需要 Blender 的 Python 环境里装了 taichi（scripts/modules 或 wheel）。"
        "斥力是 O(n²) 暴力扫描，节点上限别拉到几万",
    ),
    (
        solver.BACKEND_NUMPY,
        "NumPy CPU",
        "强制纯 NumPy 后端：不依赖 taichi，斥力走空间哈希，1500 节点约 9 ms/帧",
    ),
]


def _mark_geometry(self, context):
    """几何 / 初始条件变了：求解器与定长缓冲都要重建。"""
    self.dirty = True


def _apply_display(self, context):
    """显示参数变了：不推进物理，把当前帧重刷一遍。"""
    from . import sim

    sim.redraw(self)


class SimDifferentialSettings(bpy.types.PropertyGroup):
    """``scene.sim_differential``：一次模拟所需的全部输入。"""

    # --- 几何 / 初始条件（改后重建） --------------------------------------

    init_segments: bpy.props.IntProperty(
        name="初始节点数",
        description="初始圆环上的节点数。缺省 60，节点间距约等于斥力半径，起始状态本来就"
        "接近平衡 —— 序列开头不会先甩一下再稳定；调小（24 以下）开头会明显甩动。"
        "它只是种子，之后节点数由分裂决定",
        default=60,
        min=3,
        max=512,
        step=10,
        update=_mark_geometry,
    )

    init_radius: bpy.props.FloatProperty(
        name="初始半径",
        description="初始圆环的半径（世界单位）。增长后曲线会自己打褶，实际范围由"
        "「边界半径」限制",
        default=1.0,
        min=0.05,
        max=10.0,
        precision=3,
        step=10,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_mark_geometry,
    )

    jitter: bpy.props.FloatProperty(
        name="初始抖动",
        description="初始圆环的随机扰动（占半径的比例）：完美的圆在对称力场里只会均匀膨胀、"
        "永远不打褶，所以必须有它。0.05 是参考实现的值，加大褶子会更不对称、更有机",
        default=0.05,
        min=0.0,
        max=0.5,
        precision=3,
        step=1,
        update=_mark_geometry,
    )

    seed: bpy.props.IntProperty(
        name="随机种子",
        description="抖动与分裂中点的随机源：同一个种子每次重建都长成同一个形状",
        default=0,
        min=0,
        max=100000,
        update=_mark_geometry,
    )

    max_nodes: bpy.props.IntProperty(
        name="节点上限",
        description="节点缓冲的容量，也就是生长上限：GP 笔划是定长的，所以这个值同时决定"
        "笔划点数。到了上限就停止分裂（画面继续弛豫）。改它要重建",
        default=1500,
        min=50,
        max=20000,
        step=100,
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

    substeps: bpy.props.IntProperty(
        name="子步",
        description="每帧推进多少个物理子步（拓扑生长固定在帧尾做一次）。子步越多，弛豫越"
        "收敛、运动越快也越连贯，代价是每帧开销成正比上升；「惯性」按帧计，和它无关",
        default=8,
        min=1,
        max=60,
        step=1,
    )

    rep_radius: bpy.props.FloatProperty(
        name="斥力半径 R",
        description="节点之间开始互斥的距离。它是图案尺度的总开关：R 越大折痕越粗、"
        "节点越稀；R 太小的话分裂后立刻又被推开，曲线会抖",
        default=solver.DEFAULTS["rep_radius"],
        min=0.005,
        max=1.0,
        precision=4,
        step=1,
    )

    rep_weight: bpy.props.FloatProperty(
        name="斥力权重",
        description="斥力的强度：太小压不住，图案会挤成一团；太大曲线会被撑爆、抖成锯齿",
        default=solver.DEFAULTS["rep_weight"],
        min=0.0,
        max=1.0,
        precision=3,
        step=1,
    )

    smooth_weight: bpy.props.FloatProperty(
        name="平滑权重",
        description="拉普拉斯平滑的强度（往相邻两点的中点靠）：越大曲线越紧、折痕越圆；"
        "太大（超过 0.6）会把褶子直接抹平成一根皱缩的线",
        default=solver.DEFAULTS["smooth_weight"],
        min=0.0,
        max=1.0,
        precision=3,
        step=1,
    )

    bound_radius: bpy.props.FloatProperty(
        name="边界半径",
        description="培养皿半径：超出就被推回来。它是差分生长的核心约束 —— 弧长在涨，"
        "外面不许扩，多出来的长度只能自己打褶。0 = 不设边界（曲线会一直往外长）",
        default=solver.DEFAULTS["bound_radius"],
        min=0.0,
        max=20.0,
        precision=3,
        step=10,
        subtype="DISTANCE",
        unit="LENGTH",
    )

    bound_weight: bpy.props.FloatProperty(
        name="边界刚性",
        description="边界的推回强度：太小曲线会明显冲出边界，太大边缘会硬折",
        default=solver.DEFAULTS["bound_weight"],
        min=0.0,
        max=2.0,
        precision=3,
        step=1,
    )

    split_dist: bpy.props.FloatProperty(
        name="分裂距离",
        description="边长超过它就在中点切开。它和斥力半径的比值决定图案的疏密比："
        "分裂距离远小于 R 时，节点被斥力撑开、又被切开，生长最快",
        default=solver.DEFAULTS["split_dist"],
        min=0.002,
        max=0.5,
        precision=4,
        step=1,
    )

    growth_rate: bpy.props.IntProperty(
        name="生长速率",
        description="每帧最多切开几条边 —— 这就是生长速度。每一条切口都会在画面上留下一点"
        "局部变化，所以想更顺就往小调；上限是节点上限，到了就自然停下来",
        default=int(solver.DEFAULTS["growth_rate"]),
        min=1,
        max=100,
        step=1,
    )

    inertia: bpy.props.FloatProperty(
        name="惯性",
        description="速度低通的记忆长度（按帧计）。0 = 纯位置约束（缺省，参考实现的行为，"
        "图案最均匀）；调到 0.5~0.8 运动更像流体、更顺，代价是打褶会开始偏科（一侧密、"
        "一侧光滑），所以缺省不开",
        default=solver.DEFAULTS["inertia"],
        min=0.0,
        max=0.95,
        precision=2,
        step=1,
    )

    split_noise: bpy.props.FloatProperty(
        name="切口抖动",
        description="分裂时新节点相对弦中点的随机偏移（占分裂距离的比例）。0 = 切口完全"
        "落在弦上（最干净），参考实现用 0.1；它只影响细节，不影响整体图案",
        default=solver.DEFAULTS["split_noise"],
        min=0.0,
        max=0.5,
        precision=2,
        step=1,
    )

    flatten: bpy.props.BoolProperty(
        name="锁定到 Z = 0",
        description="严格 2D：每步把 Z 清零，边界与斥力都在平面内算。关掉就是 3D 生长"
        "（初始抖动的 Z 分量会让曲线自己绕出去）",
        default=True,
    )

    # --- 显示（直接重刷） --------------------------------------------------

    colour_mode: bpy.props.EnumProperty(
        name="配色",
        description="逐点颜色按什么走：曲率 / 拉伸 / 单色",
        items=colour.MODES,
        default=colour.MODE_CURVATURE,
        update=_apply_display,
    )

    line_width: bpy.props.FloatProperty(
        name="线宽",
        description="笔划半径（世界单位，实际线宽是它的 2 倍）",
        default=0.01,
        min=0.0005,
        max=0.2,
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

    # --- 状态（只读） ------------------------------------------------------

    dirty: bpy.props.BoolProperty(
        name="待重建",
        description="几何 / 初始条件改过但还没重建（下一帧自动重建）",
        default=True,
    )

    def geometry_signature(self) -> tuple:
        """去重用的签名（与 ``sim.signature`` 判据一致，只有这一份）。"""
        return (
            int(self.init_segments),
            round(float(self.init_radius), 6),
            round(float(self.jitter), 6),
            int(self.seed),
            int(self.max_nodes),
            str(self.backend),
        )


def register():
    bpy.types.Scene.sim_differential = bpy.props.PointerProperty(
        type=SimDifferentialSettings
    )


def unregister():
    del bpy.types.Scene.sim_differential
