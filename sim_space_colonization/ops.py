"""算子：构建 / 重置 / 步进 / 场景预设 / 建根顶点组 / 视口修正 / 删除。

算子只做编排与参数校验，算法与写笔划都在 :mod:`sim` 里；能失败的地方收成 ``report``，
不把 traceback 甩给用户。
"""

import bpy

from . import gp, sim, viewport
from . import scene as scene_mod
from . import surface as surface_mod

__all__ = [
    "DISPLAY_KEY",
    "SSCO_OT_build",
    "SSCO_OT_delete",
    "SSCO_OT_fix_viewport",
    "SSCO_OT_make_root",
    "SSCO_OT_reset",
    "SSCO_OT_setup_scene",
    "SSCO_OT_step",
    "SSCO_OT_toggle_target_wire",
]

#: 切到线框前，目标网格原来的「显示为」记在这儿（切回来照原样还原，不靠猜）。
DISPLAY_KEY = "sim_space_colonization_display_type"


def _resolve_target(context) -> str:
    """目标网格：面板里选了就用它，否则依次试激活对象 → 唯一选中对象 → 缺省输入网格。

    返回空串表示成功，否则是给 ``report`` 的错误文本。
    """
    settings = context.scene.sim_space_colonization
    if settings.target is not None:
        return ""
    # 结果对象是 GP，本来就不是 MESH；这里仍然挡一道（用户可能把它转成网格再选回来）
    active = context.active_object
    if active is not None and active.type == "MESH" and not gp.is_result(active):
        settings.target = active
        return ""
    selected = [
        obj
        for obj in context.selected_objects
        if obj.type == "MESH" and not gp.is_result(obj)
    ]
    if len(selected) == 1:
        settings.target = selected[0]
        return ""

    # 什么都没有：给一只缺省输入网格（细分过的猴子），省掉「先搭个目标」这一步
    fallback = surface_mod.default_target(context)
    if fallback is not None:
        settings.target = fallback
        return ""
    return "先选一个目标网格（面板「目标网格」或视图里选中它）"


def _viewport_note(status: str, fixed: int) -> str:
    """把视口着色检查结果拼成报告尾巴（一切正常时不加噪音）。

    report 文本保持纯 ASCII：终端编码不一定支持中文，混进去会乱码。
    """
    if status == viewport.FIXED:
        return f" | viewport shading set to Attribute ({fixed} view(s))"
    if status == viewport.NO_VIEW:
        return " | tip: set Solid viewport colour to Attribute, else it stays flat"
    return ""


def _start_frame(scene) -> None:
    """把时间轴放回起始帧：构建 / 重置之后画面停在根点。"""
    scene.frame_set(int(scene.frame_start))


class SSCO_OT_build(bpy.types.Operator):
    """按当前参数构建（或重建）太空殖民生长"""

    bl_idname = "sim_space_colonization.build"
    bl_label = "构建生长"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.sim_space_colonization
        problem = _resolve_target(context)
        if problem:
            self.report({"ERROR"}, problem)
            return {"CANCELLED"}

        obj = gp.find_object()
        if obj is not None and obj.mode != "OBJECT":
            self.report({"ERROR"}, "请先退出编辑模式：重建会替换整丛笔划")
            return {"CANCELLED"}

        status, fixed = viewport.ensure_vertex_colour(context)

        try:
            info = sim.build(settings, context.scene)
        except Exception as exc:
            self.report({"ERROR"}, f"构建失败: {exc}")
            return {"CANCELLED"}

        _start_frame(context.scene)
        message = (
            f"sim_space_colonization: {settings.max_attractors} attractors / "
            f"{info['roots']} roots / unit {info['unit']:.3f} / "
            f"{info['backend']} / {info['ms']:.0f} ms"
        )
        message += _viewport_note(status, fixed)
        if settings.target.name == surface_mod.DEFAULT_TARGET_NAME:
            message += " | default target mesh created"
        self.report({"INFO"}, message)
        return {"FINISHED"}


class SSCO_OT_reset(bpy.types.Operator):
    """从头再长一遍（重新撒吸收点），时间轴回到起始帧"""

    bl_idname = "sim_space_colonization.reset"
    bl_label = "重置生长"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.sim_space_colonization
        problem = _resolve_target(context)
        if problem:
            self.report({"ERROR"}, problem)
            return {"CANCELLED"}
        try:
            sim.reset(settings, context.scene)
        except Exception as exc:
            self.report({"ERROR"}, f"重置失败: {exc}")
            return {"CANCELLED"}
        _start_frame(context.scene)
        self.report({"INFO"}, "已重置到根点")
        return {"FINISHED"}


class SSCO_OT_step(bpy.types.Operator):
    """前进一帧（时间轴 +1，按子步生长）"""

    bl_idname = "sim_space_colonization.step"
    bl_label = "步进一帧"
    bl_options = {"REGISTER"}

    def execute(self, context):
        scene = context.scene
        if sim.STATE.get("solver") is None:
            return bpy.ops.sim_space_colonization.build()
        scene.frame_set(scene.frame_current + 1)
        return {"FINISHED"}


class SSCO_OT_make_root(bpy.types.Operator):
    """在目标网格最难看的低点建根顶点组（没有根也能马上开始）"""

    bl_idname = "sim_space_colonization.make_root"
    bl_label = "建根顶点组"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.sim_space_colonization
        problem = _resolve_target(context)
        if problem:
            self.report({"ERROR"}, problem)
            return {"CANCELLED"}

        obj = settings.target
        name = str(settings.root_group) or surface_mod.DEFAULT_ROOT_GROUP
        group = obj.vertex_groups.get(name) or obj.vertex_groups.new(name=name)

        index = surface_mod.lowest_vertex_index(obj)
        if index < 0:
            self.report({"ERROR"}, "目标网格没有顶点")
            return {"CANCELLED"}
        group.add([index], 1.0, "REPLACE")
        settings.root_group = name
        settings.dirty = True
        self.report({"INFO"}, f"root vertex group '{name}' -> vertex {index}")
        return {"FINISHED"}


class SSCO_OT_toggle_target_wire(bpy.types.Operator):
    """目标网格在线框与实体之间切换（线框能看穿输入网格，方便核对藤蔓怎么绕着它走）"""

    bl_idname = "sim_space_colonization.toggle_target_wire"
    bl_label = "目标网格线框显示"
    bl_options = {"REGISTER", "UNDO"}

    #: ``Object.display_type`` 的合法值；还原时拿到别的值就退回实体。
    _DISPLAY_TYPES = {"BOUNDS", "SOLID", "TEXTURED", "WIRE"}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "sim_space_colonization", None)
        return settings is not None and settings.target is not None

    def execute(self, context):
        target = context.scene.sim_space_colonization.target
        if target.display_type == "WIRE":
            previous = str(target.get(DISPLAY_KEY, "TEXTURED"))
            target.display_type = (
                previous if previous in self._DISPLAY_TYPES else "TEXTURED"
            )
            if DISPLAY_KEY in target:
                del target[DISPLAY_KEY]
            self.report(
                {"INFO"}, f"{target.name}: {target.display_type.lower()} display"
            )
        else:
            # 记下用户自己的显示方式（BOUNDS / SOLID / TEXTURED），切回来时原样还给他
            target[DISPLAY_KEY] = target.display_type
            target.display_type = "WIRE"
            self.report({"INFO"}, f"{target.name}: wireframe display")
        return {"FINISHED"}


class SSCO_OT_setup_scene(bpy.types.Operator):
    """铺项目画布底色、按目标网格建环绕正交相机、切 Standard 色彩"""

    bl_idname = "sim_space_colonization.setup_scene"
    bl_label = "场景预设"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.sim_space_colonization
        problem = _resolve_target(context)
        if problem:
            self.report({"ERROR"}, problem)
            return {"CANCELLED"}

        surface = surface_mod.load(settings.target)
        if surface is None:
            self.report({"ERROR"}, "目标网格没有可用几何")
            return {"CANCELLED"}

        status, fixed = viewport.ensure_vertex_colour(context)
        scene_mod.setup(context.scene, surface.center, surface.unit)
        message = (
            f"ortho camera span={scene_mod.span_of(surface.unit):.3f}, "
            f"{scene_mod.RESOLUTION[0]}x{scene_mod.RESOLUTION[1]}, Standard"
        )
        message += _viewport_note(status, fixed)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class SSCO_OT_fix_viewport(bpy.types.Operator):
    """把 Solid 视图的着色颜色切成「Attribute / 顶点色」"""

    bl_idname = "sim_space_colonization.fix_viewport"
    bl_label = "视口改用顶点色"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return bool(viewport.solid_spaces(context))

    def execute(self, context):
        status, fixed = viewport.ensure_vertex_colour(context)
        if status == viewport.FIXED:
            self.report({"INFO"}, f"viewport shading set to Attribute ({fixed} view)")
            return {"FINISHED"}
        if status == viewport.ALREADY:
            self.report({"INFO"}, "viewport already shows vertex colours")
            return {"CANCELLED"}
        if status == viewport.NOT_SOLID:
            self.report(
                {"INFO"}, "no Solid view; material/render preview already shows colours"
            )
            return {"CANCELLED"}
        self.report(
            {"WARNING"},
            "viewport colour is a custom mode; set it to Attribute by hand",
        )
        return {"CANCELLED"}


class SSCO_OT_delete(bpy.types.Operator):
    """删除生成的 GP 笔划对象"""

    bl_idname = "sim_space_colonization.delete"
    bl_label = "删除结果对象"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        removed = gp.remove_object()
        sim.STATE.update(
            solver=None,
            surface=None,
            signature=None,
            unit=1.0,
            frame=0,
            backend="",
            note="",
            nodes=0,
            strokes=0,
            attractors=0,
            ms=0.0,
            error="",
        )
        context.scene.sim_space_colonization.dirty = True
        if not removed:
            self.report({"WARNING"}, "没有找到生成的对象")
            return {"CANCELLED"}
        return {"FINISHED"}
