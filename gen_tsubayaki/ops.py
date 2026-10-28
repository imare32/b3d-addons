"""算子：生成 / 删除 / 场景预设。

生成流程是「算法层出 :class:`~gen_tsubayaki.pattern.Pattern` → 装配层出笔划集合 →
GP 后端写对象」，算子只做编排与参数校验；所有能失败的地方都收成 ``report``，
不把 traceback 甩给用户。

没有「更新」按钮：本扩展每改一项参数就当场重画当前帧（见 :mod:`.props`），
「生成」只管第一次建对象（以及删掉之后的再来一次）。
"""

import bpy

from . import anim, gp, viewport
from .palette import hex_to_linear
from .props import SETTINGS_ID, backdrop_hex, glow_kwargs
from .scene import LAYER_CHORDS

__all__ = [
    "GENT_OT_delete",
    "GENT_OT_generate",
    "GENT_OT_setup_scene",
    "paint_canvas",
]


def _ensure_object(context):
    """建一个空的 GP 对象（几何由 :mod:`.anim` 按当前帧写进去）。"""
    data = bpy.data.grease_pencils.new(gp.OBJECT_NAME)
    obj = bpy.data.objects.new(gp.OBJECT_NAME, data)
    (context.collection or context.scene.collection).objects.link(obj)
    for other in context.selected_objects:
        other.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    return obj


def _viewport_note(status: str, fixed: int) -> str:
    """把视口着色检查结果拼成报告尾巴（一切正常时不加噪音）。

    report 文本保持纯 ASCII：终端/任务输出的编码不一定支持中文，混进去会乱码。
    """
    if status == viewport.FIXED:
        return f" | viewport shading set to Attribute ({fixed} view(s))"
    if status == viewport.NO_VIEW:
        return " | tip: in Solid mode set viewport color to Attribute, else it renders black"
    return ""


def paint_canvas(scene, colour_hex: str) -> str:
    """世界背景铺指定底色，转线性后写进节点，返回铺的色值。

    默认是色板里的墨玄（反白：亮线落在深底上才像发光），也可以切回纸底。
    """
    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("Gen Tsubayaki World")
        scene.world = world

    world.use_nodes = True
    colour = hex_to_linear(colour_hex)
    background = world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs[0].default_value = (*colour, 1.0)
    # 无光照图层不吃世界光，这里同步一份给物体的"世界底色"显示
    world.color = colour
    return colour_hex


def _frame_box(settings, obj):
    """相机取景：优先用生成对象的实际包围盒，没有对象时退回画布正方形。"""
    if obj is not None and len(obj.bound_box) == 8:
        from mathutils import Vector

        corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
        xs = [corner.x for corner in corners]
        ys = [corner.y for corner in corners]
        span = max(max(xs) - min(xs), max(ys) - min(ys)) * 1.02
        if span > 1e-6:
            centre = ((min(xs) + max(xs)) * 0.5, (min(ys) + max(ys)) * 0.5)
            return centre, span

    size = float(settings.canvas)
    return (size * 0.5, size * 0.5), size


class GENT_OT_generate(bpy.types.Operator):
    """按面板参数生成一张弦线族（Grease Pencil v3）"""

    bl_idname = "gen_tsubayaki.generate"
    bl_label = "生成"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        settings = getattr(scene, SETTINGS_ID, None)
        if settings is None:
            self.report({"ERROR"}, "settings not registered; reload the extension")
            return {"CANCELLED"}

        obj = gp.find()
        created = obj is None
        if created:
            obj = _ensure_object(context)

        anim.reset_cache()
        info = anim.redraw(scene)
        if not info.get("updated"):
            error = info.get("error")
            if error:
                self.report({"ERROR"}, f"generate failed: {error}")
                return {"CANCELLED"}
            self.report({"WARNING"}, "nothing to draw (no chords)")
            return {"CANCELLED"}

        gp.set_visible(obj, LAYER_CHORDS, bool(settings.show_lines))

        message = (
            f"{settings.mode}/{settings.variant}: n={info['n']:.2f} t={info['t']:.3f} "
            f"chords={info['chords']} ink={info['ink']:.2f} "
            f"({info['ms']:.2f} ms, {info['strokes']} strokes)"
        )
        if created:
            message = "created Grease Pencil object | " + message
        if float(settings.z_wave_amp) > 0.0:
            message += " | Z wave on: top view hides it, tilt the camera to see"
        message += _viewport_note(*viewport.ensure_vertex_colour(context))
        self.report({"INFO"}, message)
        return {"FINISHED"}


class GENT_OT_delete(bpy.types.Operator):
    """删掉生成的弦线族对象"""

    bl_idname = "gen_tsubayaki.delete"
    bl_label = "删除"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if not gp.remove():
            self.report({"WARNING"}, "no generated object to delete")
            return {"CANCELLED"}
        anim.reset_cache()
        getattr(context.scene, SETTINGS_ID).dirty = True
        self.report({"INFO"}, "deleted the generated object")
        return {"FINISHED"}


class GENT_OT_setup_scene(bpy.types.Operator):
    """建正交相机、铺面板上的背景底色、切 Standard 色彩、开 GP 抗锯齿与辉光"""

    bl_idname = "gen_tsubayaki.setup_scene"
    bl_label = "场景预设"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        settings = getattr(scene, SETTINGS_ID)
        centre, span = _frame_box(settings, gp.find())

        camera = scene.camera
        if camera is None or camera.type != "CAMERA":
            camera_data = bpy.data.cameras.new("Gen Tsubayaki Camera")
            camera = bpy.data.objects.new("Gen Tsubayaki Camera", camera_data)
            (context.collection or scene.collection).objects.link(camera)
            scene.camera = camera

        camera.data.type = "ORTHO"
        camera.data.ortho_scale = span
        # 图案画在 XY 平面（z 只用来起伏），所以俯视相机放在 +Z 朝 -Z 看，屏幕上 +Y 朝上。
        camera.location = (centre[0], centre[1], span * 2.0)
        camera.rotation_euler = (0.0, 0.0, 0.0)

        scene.render.resolution_x = 2048
        scene.render.resolution_y = 2048
        scene.render.film_transparent = False
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"

        gp_settings = getattr(scene, "grease_pencil_settings", None)
        if gp_settings is not None and hasattr(gp_settings, "aa_samples"):
            gp_settings.aa_samples = 16

        canvas_hex = paint_canvas(scene, backdrop_hex(settings.backdrop))

        message = (
            f"camera ortho_scale={span:g}, 2048x2048, Standard view transform, "
            f"background {canvas_hex}"
        )
        if gp.apply_glow(gp.find(), **glow_kwargs(settings)):
            message += f" | glow size={settings.glow_size:g}px"
        if float(settings.z_wave_amp) > 0.0:
            message += " | Z wave on: tilt the camera to see the relief"
        message += _viewport_note(*viewport.ensure_vertex_colour(context))
        self.report({"INFO"}, message)
        return {"FINISHED"}
