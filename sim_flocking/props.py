"""场景参数：``scene.sim_flocking``。

面板上所有可调项都在这里，handler 每帧只读这套值，算子只做编排。参数的语义分三档：

* **重建**（后端）：换了后端下一帧由 handler 自动重建求解器（容量与初始状态都要重来）；
* **重置**（种子）：改了下一帧按同一个种子重新撒一遍，其余参数不动；
* **实时**（鸟数、物理、边界、外观）：下一帧就生效，连重建都不用 —— 鸟数变了只是往缓冲
  尾巴上补一批 / 砍掉一批（见 :meth:`sim_flocking.solver.NumPyFlock.set_count`）。

**不要**加 ``from __future__ import annotations``：``bpy.props`` 的注解必须是真实的
``Property`` 对象，PEP 563 的字符串注解会让注册失败。
"""

import bpy

from . import colour, solver

__all__ = ["BACKENDS", "SimFlockingSettings", "register", "unregister"]

#: 后端选项。
BACKENDS = [
    (
        solver.BACKEND_AUTO,
        "自动",
        "能 import taichi 就用它的 GPU kernel，否则回退纯 NumPy（300 只约 3 ms/帧）",
    ),
    (
        solver.BACKEND_TAICHI,
        "Taichi GPU",
        (
            "强制 Taichi：需要 Blender 的 Python 环境里装了 taichi"
            "（scripts/modules 或 wheel）。邻居搜索是 O(n²)，几千只以上才有优势"
        ),
    ),
    (
        solver.BACKEND_NUMPY,
        "NumPy CPU",
        "强制纯 NumPy 后端：不依赖 taichi，300 只左右完全实时，上千只会明显变慢",
    ),
]


def _mark_reset(self, context):
    """初始条件变了：下一帧按同一个种子重新撒一遍（不重建求解器）。"""
    self.dirty = True


def _apply_live(self, context):
    """鸟数 / 显示参数变了：不推进物理，立刻把当前状态重算并写进网格。"""
    from . import sim

    sim.refresh(self)


class SimFlockingSettings(bpy.types.PropertyGroup):
    """``scene.sim_flocking``：一次群体模拟所需的全部输入。"""

    # --- 群体 --------------------------------------------------------------

    count: bpy.props.IntProperty(
        name="鸟数",
        description="群体规模。拖动它会立刻补鸟 / 减鸟（不是重新撒点，已经在飞的鸟不受"
        "影响），上限见 MAX_BOIDS = 20000。它是每帧 O(n²) 的开销来源：NumPy 后端到"
        "一两千只就开始掉帧，要更多请换 Taichi 后端",
        default=300,
        min=0,
        max=solver.MAX_BOIDS,
        step=10,
        update=_apply_live,
    )

    seed: bpy.props.IntProperty(
        name="随机种子",
        description="初始撒点与初速度的随机源：同一个种子 + 同参数 = 同一条演化路径"
        "（两个后端的初始状态都由 NumPy 生成，所以互相也一致）",
        default=0,
        min=0,
        max=100000,
        update=_mark_reset,
    )

    # --- 空间 / 边界 --------------------------------------------------------

    width: bpy.props.FloatProperty(
        name="宽度",
        description="模拟区域在 X 方向的尺寸（居中于原点）。它和高度一起决定「鸟的密度」："
        "同样的鸟数，区域越大平均间距越远，感知半径得跟着放大，否则群就散了",
        default=solver.DEFAULTS["width"],
        min=1.0,
        max=200.0,
        precision=2,
        step=100,
        subtype="DISTANCE",
        unit="LENGTH",
    )

    height: bpy.props.FloatProperty(
        name="高度",
        description="模拟区域在 Y 方向的尺寸（居中于原点）",
        default=solver.DEFAULTS["height"],
        min=1.0,
        max=200.0,
        precision=2,
        step=100,
        subtype="DISTANCE",
        unit="LENGTH",
    )

    boundary: bpy.props.EnumProperty(
        name="边界",
        description="鸟飞出模拟区域之后怎么处理。环绕没有边界感（参考实现），软边界会让"
        "整群沿边缘拐弯，反弹会留下明显的折线",
        items=solver.BOUNDARIES,
        default=solver.DEFAULTS["boundary"],
    )

    # --- 运动 --------------------------------------------------------------

    max_speed: bpy.props.FloatProperty(
        name="速度上限",
        description="每只鸟的速度上限（世界单位 / 秒）。配合「时间步长 × 子步」决定画面里"
        "飞得多快：位移 ≈ 速度上限 × 步长 × 子步 / 帧",
        default=solver.DEFAULTS["max_speed"],
        min=0.01,
        max=10.0,
        precision=3,
        step=1,
    )

    max_force: bpy.props.FloatProperty(
        name="操舵力上限",
        description="单条规则每步能给的最大加速度。太小转弯迟钝（整群像一群直线飞行的点），"
        "太大会抖 —— 它是把三条规则「压回同一个量级」的关键，缺省 0.05 约为速度上限的 1/16",
        default=solver.DEFAULTS["max_force"],
        min=0.0001,
        max=1.0,
        precision=4,
        step=1,
    )

    time_step: bpy.props.FloatProperty(
        name="时间步长",
        description="每个子步的 dt。它只影响「物理时间怎么映射到动画帧」，不影响规则；"
        "太大（> 1）会让分离力不够快、鸟互相穿过去",
        default=0.5,
        min=0.01,
        max=2.0,
        precision=3,
        step=1,
    )

    substeps: bpy.props.IntProperty(
        name="子步",
        description="每帧推进几个物理子步。子步多 = 飞得更快、更连贯，代价是每帧开销成正比",
        default=2,
        min=1,
        max=20,
        step=1,
    )

    noise: bpy.props.FloatProperty(
        name="随机扰动",
        description="占「操舵力上限」比例的随机加速度：0 = 纯 boids（参考实现），整群迟早"
        "锁成同向直飞；0.1~0.3 会一直有鸟在拐弯，画面更像活物",
        default=solver.DEFAULTS["noise"],
        min=0.0,
        max=1.0,
        precision=2,
        step=1,
    )

    # --- 行为（三个半径 + 三个权重） ---------------------------------------

    sep_rad: bpy.props.FloatProperty(
        name="分离半径",
        description="比这个距离近就互相推开（按 1/d 加权，越近推得越狠）。它是最需要和「鸟"
        "群密度」对齐的参数：密度高而半径小，鸟会撞在一起",
        default=solver.DEFAULTS["sep_rad"],
        min=0.01,
        max=20.0,
        precision=3,
        step=10,
    )

    ali_rad: bpy.props.FloatProperty(
        name="对齐半径",
        description="比这个距离近就跟着邻居的平均速度转向。半径大 = 整群同步、飞得整齐；"
        "小 = 各自为政，更像散沙",
        default=solver.DEFAULTS["ali_rad"],
        min=0.01,
        max=20.0,
        precision=3,
        step=10,
    )

    coh_rad: bpy.props.FloatProperty(
        name="聚集半径",
        description="比这个距离近就往邻居重心靠。它和分离半径的比值决定图案：聚集半径远大于"
        "分离半径时，鸟群会结成几个不断旋转的团",
        default=solver.DEFAULTS["coh_rad"],
        min=0.01,
        max=20.0,
        precision=3,
        step=10,
    )

    sep_weight: bpy.props.FloatProperty(
        name="分离权重",
        description="分离规则的权重。它压不住的时候（太小 / 鸟太密）整群会挤成一个点",
        default=solver.DEFAULTS["sep_weight"],
        min=0.0,
        max=10.0,
        precision=2,
        step=1,
    )

    ali_weight: bpy.props.FloatProperty(
        name="对齐权重",
        description="对齐规则的权重：越大越整齐、转向越慢",
        default=solver.DEFAULTS["ali_weight"],
        min=0.0,
        max=10.0,
        precision=2,
        step=1,
    )

    coh_weight: bpy.props.FloatProperty(
        name="聚集权重",
        description="聚集规则的权重：越大越像一个整体、也越容易缩成一团",
        default=solver.DEFAULTS["coh_weight"],
        min=0.0,
        max=10.0,
        precision=2,
        step=1,
    )

    # --- 外观 --------------------------------------------------------------

    colour_mode: bpy.props.EnumProperty(
        name="配色",
        description="逐鸟颜色按什么走：速度 / 朝向，都由项目色板推出来",
        items=colour.MODES,
        default=colour.MODE_SPEED,
        update=_apply_live,
    )

    size: bpy.props.FloatProperty(
        name="箭头长度",
        description="每只鸟画多长（世界单位）。它是纯显示量：改它只重画几何，不影响物理。"
        "缺省 0.55 是按 20×20 的区域调的：占画面宽度的 1/36 左右，成片时才连成纹理",
        default=0.55,
        min=0.01,
        max=5.0,
        precision=3,
        step=10,
        subtype="DISTANCE",
        unit="LENGTH",
        update=_apply_live,
    )

    backend: bpy.props.EnumProperty(
        name="后端",
        description="计算后端：自动 = 有 taichi 就用 GPU，没有就纯 NumPy",
        items=BACKENDS,
        default=solver.BACKEND_AUTO,
        update=_mark_reset,
    )

    # --- 画笔（撒鸟） ------------------------------------------------------

    brush_radius: bpy.props.FloatProperty(
        name="画笔半径",
        description="撒鸟时每只鸟在自己落点周围的散布半径：0 = 全部落在鼠标指向的同一点",
        default=1.0,
        min=0.0,
        max=20.0,
        precision=2,
        step=10,
        subtype="DISTANCE",
        unit="LENGTH",
    )

    brush_count: bpy.props.IntProperty(
        name="每次撒几只",
        description="一次落笔补几只鸟（还会被 MAX_BOIDS 与刷新节流挡一道）",
        default=5,
        min=1,
        max=200,
        step=1,
    )

    # --- 状态（只读 / 交互） -----------------------------------------------

    dirty: bpy.props.BoolProperty(
        name="待重置",
        description="种子 / 后端改过但还没生效（下一帧自动处理）",
        default=True,
    )

    painting: bpy.props.BoolProperty(
        name="撒鸟中",
        description="撒鸟操作符是否在等鼠标（ESC / 右键退出）",
        default=False,
    )

    dragging: bpy.props.BoolProperty(
        name="按住左键",
        description="撒鸟时左键是否按下（面板用来显示状态）",
        default=False,
    )

    def geometry_signature(self) -> tuple:
        """重建判据（与 ``sim.signature`` 一致，只有这一份）。

        鸟数**不在**里面：它是实时的，靠求解器的 ``set_count`` 补 / 砍；只有换后端才必须
        重建（缓冲的字段类型不一样）。
        """
        return (str(self.backend),)


def register():
    bpy.types.Scene.sim_flocking = bpy.props.PointerProperty(type=SimFlockingSettings)


def unregister():
    del bpy.types.Scene.sim_flocking
