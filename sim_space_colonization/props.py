"""场景参数：``scene.sim_space_colonization``。

面板上所有可调项都在这里，handler 每帧只读这套值，算子只做编排。三档参数的语义不同：

* **输入 / 取样**（目标网格、根顶点组、吸收点数、种子、后端、模式）：变了要重新取表面、
  重撒吸收点、重建求解器 —— 改它们把 :attr:`SimSpaceColonizationSettings.dirty` 置位，
  下一帧由 handler 按签名自动重建；
* **算法**（吸引半径 / 消耗半径 / 生长步长 / 方向持久度 / 抖动 / 子步 / 节点上限）：
  求解器实参，下一帧就生效，不重建；
* **显示**（配色 / 粗细倍数 / 圆周分段）：不推进生长，直接重刷当前帧。

**所有长度参数都是相对输入网格包围盒对角线的比例**（0.012 = 对角线的 1.2%），所以换一个
1 cm 的小零件和 10 m 的建筑，同一套参数都成立；换算成世界单位在 :mod:`sim` 里做。

**不要**加 ``from __future__ import annotations``：``bpy.props`` 的注解必须是真实的
``Property`` 对象，PEP 563 的字符串注解会让注册失败。
"""

import bpy

from . import colour, solver

__all__ = [
    "BACKENDS",
    "GROWTH_MODES",
    "MODE_SPACE",
    "MODE_SURFACE",
    "SimSpaceColonizationSettings",
    "register",
    "unregister",
]

MODE_SURFACE = "SURFACE"
MODE_SPACE = "SPACE"

#: 生长约束。
GROWTH_MODES = [
    (
        MODE_SURFACE,
        "贴表面",
        "枝条被约束在目标网格表面上（藤蔓 / 裂纹）：生长方向先压到切平面，再贴回表面",
    ),
    (
        MODE_SPACE,
        "自由空间",
        "只在目标网格表面上撒吸收点，枝条在空间里自由生长（树 / 珊瑚）",
    ),
]

#: 后端选项。
BACKENDS = [
    (
        solver.BACKEND_AUTO,
        "自动",
        "依次尝试 scipy 的 KD 树、taichi GPU，都没有就回退纯 NumPy（原因写在读数里）",
    ),
    (
        solver.BACKEND_TAICHI,
        "Taichi GPU",
        "强制 Taichi 并行暴力搜索：需要 Blender 的 Python 环境里有 taichi",
    ),
    (
        solver.BACKEND_NUMPY,
        "NumPy CPU",
        "强制纯 NumPy 暴力搜索：零依赖，但节点上万后会明显变慢",
    ),
]


def _is_result(obj) -> bool:
    """结果对象不能当输入网格 —— 否则会拿自己画出来的笔划再长一遍。"""
    from . import gp

    return gp.is_result(obj)


def _mark_geometry(self, context):
    """输入 / 取样变了：表面、吸收点、求解器全部要重来。"""
    self.dirty = True


def _apply_display(self, context):
    """显示参数变了：不推进生长，把当前帧重刷一遍。"""
    from . import sim

    sim.redraw(self)


class SimSpaceColonizationSettings(bpy.types.PropertyGroup):
    """``scene.sim_space_colonization``：一次生长所需的全部输入。"""

    # --- 输入 --------------------------------------------------------------

    target: bpy.props.PointerProperty(
        name="目标网格",
        description="任意网格对象：算法在它的表面上撒吸收点，枝条朝吸收点生长",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == "MESH" and not _is_result(obj),
        update=_mark_geometry,
    )

    mode: bpy.props.EnumProperty(
        name="约束",
        description="贴表面 = 藤蔓沿网格表面爬；自由空间 = 枝条在空间里长向表面",
        items=GROWTH_MODES,
        default=MODE_SURFACE,
        update=_mark_geometry,
    )

    root_group: bpy.props.StringProperty(
        name="根组",
        description="目标网格上用作根的顶点组名（组里放几个点就长出几丛）；"
        "没有这个组时用世界空间 z 最低的点",
        default="root",
        update=_mark_geometry,
    )

    backend: bpy.props.EnumProperty(
        name="后端",
        description="最近邻搜索的实现：自动 = KD 树 → taichi → NumPy",
        items=BACKENDS,
        default=solver.BACKEND_AUTO,
        update=_mark_geometry,
    )

    max_attractors: bpy.props.IntProperty(
        name="吸收点",
        description="在目标网格表面按面积加权撒多少个吸收点：越多枝条越密、每帧越贵",
        default=2500,
        min=100,
        max=20000,
        step=100,
        update=_mark_geometry,
    )

    seed: bpy.props.IntProperty(
        name="随机种子",
        description="吸收点撒点与生长抖动的随机数种子：同一个种子 + 同一套参数 = 同一棵树",
        default=42,
        min=0,
        update=_mark_geometry,
    )

    # --- 算法（实时生效） ---------------------------------------------------

    influence_radius: bpy.props.FloatProperty(
        name="吸引半径",
        description="吸收点能把方向投给多远的节点（相对包围盒对角线）：太大 = 整体一起冲，"
        "太小 = 只有尖端在长",
        default=solver.DEFAULTS["influence_radius"],
        min=0.01,
        max=0.6,
        precision=3,
        step=1,
    )

    kill_radius: bpy.props.FloatProperty(
        name="消耗半径",
        description="吸收点离节点多近就算被吃掉（相对对角线）：越小枝条越密、越长",
        default=solver.DEFAULTS["kill_radius"],
        min=0.001,
        max=0.1,
        precision=4,
        step=1,
    )

    growth_step: bpy.props.FloatProperty(
        name="生长步长",
        description="每子步每根枝条长多长（相对对角线）",
        default=solver.DEFAULTS["growth_step"],
        min=0.0005,
        max=0.05,
        precision=4,
        step=1,
    )

    dir_persistence: bpy.props.FloatProperty(
        name="持久度",
        description="0 = 完全跟着吸收点走（容易乱拐），0.8 = 很挺拔（容易一往无前）",
        default=solver.DEFAULTS["dir_persistence"],
        min=0.0,
        max=0.95,
        precision=2,
        step=1,
    )

    jitter: bpy.props.FloatProperty(
        name="抖动",
        description="每步给方向加的随机扰动幅度：0 = 完全确定，0.2 以上会有明显的绒毛感",
        default=solver.DEFAULTS["jitter"],
        min=0.0,
        max=0.5,
        precision=2,
        step=1,
    )

    surface_offset: bpy.props.FloatProperty(
        name="表面偏移",
        description="贴表面模式下，枝条沿法向抬起的高度（相对对角线）：防止和目标网格 z-fighting",
        default=0.0015,
        min=0.0,
        max=0.02,
        precision=5,
        step=1,
    )

    substeps: bpy.props.IntProperty(
        name="子步",
        description="每帧推进几个子步 —— 唯一的「生长速度」旋钮。子步 × 步长 ≈ 每帧长多少",
        default=2,
        min=1,
        max=20,
        step=1,
    )

    max_nodes: bpy.props.IntProperty(
        name="节点上限",
        description="节点数的硬上限：到了就停止生长（每帧的搜索开销按它封顶）",
        default=20000,
        min=500,
        max=60000,
        step=100,
    )

    # --- 外观（直接重刷） ---------------------------------------------------

    thickness: bpy.props.FloatProperty(
        name="最细半径",
        description="一个叶尖的半径（相对对角线）：主干按 Murray 定律自动更粗",
        default=solver.DEFAULTS["thickness"],
        min=0.0002,
        max=0.01,
        precision=5,
        step=1,
        update=_apply_display,
    )

    taper: bpy.props.FloatProperty(
        name="梯度",
        description="半径的 Murray 指数：r = 最细半径 × 叶尖数^梯度。0 = 等粗，0.5 以上根很粗",
        default=solver.DEFAULTS["taper"],
        min=0.1,
        max=0.7,
        precision=2,
        step=1,
        update=_apply_display,
    )

    radius_scale: bpy.props.FloatProperty(
        name="倍数",
        description="整体放大笔划半径：不动求解器，拖滑块当场重画",
        default=1.0,
        min=0.1,
        max=8.0,
        precision=2,
        step=1,
        update=_apply_display,
    )

    colour_mode: bpy.props.EnumProperty(
        name="配色",
        description="两种配色都由项目色板推出来",
        items=colour.MODES,
        default=colour.MODE_DEPTH,
        update=_apply_display,
    )

    # --- 状态（只读） ------------------------------------------------------

    dirty: bpy.props.BoolProperty(
        name="待重建",
        description="输入 / 取样改过但还没重建（下一帧自动重建）",
        default=True,
    )

    def geometry_signature(self) -> tuple:
        """重建判据（与 ``sim.signature`` 是同一份，只放这里）。"""
        target = self.target
        vertices = triangles = 0
        if target is not None and target.type == "MESH":
            vertices = len(target.data.vertices)
            triangles = len(target.data.polygons)
        return (
            target.name if target is not None else "",
            vertices,
            triangles,
            str(self.mode),
            str(self.root_group),
            str(self.backend),
            int(self.max_attractors),
            int(self.seed),
        )


def register():
    bpy.types.Scene.sim_space_colonization = bpy.props.PointerProperty(
        type=SimSpaceColonizationSettings
    )


def unregister():
    del bpy.types.Scene.sim_space_colonization
