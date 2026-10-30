"""算子：生成 / 换种子 / 删除 / 场景预设 / 从对象回读参数。

生成流程是「算法层出 ``Shape`` → GP 后端写对象」，算子只做编排与参数校验；
所有能失败的地方都收成 ``report``，不把 traceback 甩给用户。
"""

import time

import bpy

from . import gp, viewport
from .palette import BACKGROUND, hex_to_linear
from .props import DESIGN_KEYS, LAYER_VISIBILITY, STAR_TYPE_ITEMS
from .rng import SEED_MAX
from .scene import LAYER_BAND, LAYER_CASE, LAYER_FACES

__all__ = [
    "GENR_OT_delete",
    "GENR_OT_generate",
    "GENR_OT_random_seed",
    "GENR_OT_setup_scene",
    "GENR_OT_sync_from_object",
]


def _build(settings):
    """跑一次「设计 → 图层」，返回 ``(shapes, design)``。"""
    from .design import build_design
    from .scene import build_layers

    design = build_design(int(settings.seed), **settings.design_params())
    shapes = build_layers(
        design,
        size=float(settings.canvas),
        margin=float(settings.margin),
        # 编织的缺口是几何：带宽 / 描边倍数 / 间隙进这里，由 build_layers 一次算好
        weave_style=settings.weave_style(),
    )
    return shapes, design


def _recolor(shapes, colours: dict):
    """面板可覆盖的颜色：编织两层与辅助线，其余图层原样返回。

    编织层里既有笔划（本体 / 描边）也有只填不描的端头补片，两种都要跟着面板色走，
    所以这里按"有描边就换描边、有填充就换填充"处理。
    """
    from .scene import Shape

    recoloured = []
    for shape in shapes:
        colour = colours.get(shape.layer)
        if colour is None:
            recoloured.append(shape)
            continue
        recoloured.append(
            Shape(
                shape.points,
                None if shape.fill is None else colour,
                None if shape.stroke is None else colour,
                shape.closed,
                shape.layer,
                shape.caps,
            )
        )
    return recoloured


def _viewport_note(status: str, fixed: int) -> str:
    """把视口着色检查结果拼成报告尾巴（一切正常时不加噪音）。

    report 文本保持纯 ASCII：终端/任务输出的编码不一定支持中文，混进去会乱码。
    """
    if status == viewport.FIXED:
        return f" | viewport shading set to Attribute ({fixed} view(s))"
    if status == viewport.NO_VIEW:
        return " | tip: in Solid mode set viewport color to Attribute, else it renders black"
    return ""


def _paint_canvas(scene) -> None:
    """世界背景铺项目色板的画布底色（``BACKGROUND``），转线性后写进节点。"""
    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("Gen Rosette World")
        scene.world = world

    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    colour = hex_to_linear(BACKGROUND)
    if background is not None:
        background.inputs[0].default_value = (*colour, 1.0)
    # 无光照图层不吃世界光，这里同步一份给物体的"世界底色"显示
    world.color = colour


def _frame_box(settings):
    """相机取景：优先用生成对象的实际包围盒，没有对象时退回画布正方形。"""
    obj = gp.find_object()
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


def _store(settings, obj, design) -> None:
    """把输入参数写进对象：换机器、换文件后还能取回这次生成的依据。

    ``level`` 存**实际用的**那个（被收回上限时与面板值不同），回读时就能看到
    "这张图到底是多少阶"。
    """
    obj[DESIGN_KEYS["seed"]] = int(design.seed)
    obj[DESIGN_KEYS["petals"]] = int(design.petals)
    obj[DESIGN_KEYS["level"]] = int(design.level)
    obj[DESIGN_KEYS["star_type"]] = str(design.star_type)
    obj[DESIGN_KEYS["canvas"]] = float(settings.canvas)
    obj[DESIGN_KEYS["margin"]] = float(settings.margin)
    obj[DESIGN_KEYS["band_radius"]] = float(settings.band_radius)
    obj[DESIGN_KEYS["casing"]] = float(settings.casing)
    obj[DESIGN_KEYS["gap"]] = float(settings.gap)
    obj[DESIGN_KEYS["tuck"]] = bool(settings.tuck)


class GENR_OT_generate(bpy.types.Operator):
    """按面板参数生成一张星形玫瑰线（Grease Pencil v3）"""

    bl_idname = "gen_rosette.generate"
    bl_label = "生成玫瑰线"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .rng import draw_seed

        settings = context.scene.gen_rosette
        settings.error = ""

        if settings.random_seed:
            settings.seed = draw_seed()

        # 先确认 Solid 模式看得见顶点色，否则生成完是一片黑（见 viewport 文档）
        view_status, view_fixed = viewport.ensure_vertex_colour(context)

        started = time.perf_counter()
        try:
            shapes, design = _build(settings)
        except Exception as exc:
            settings.error = str(exc)
            settings.dirty = True
            self.report({"ERROR"}, f"rosette failed: {exc}")
            return {"CANCELLED"}

        try:
            counts = gp.build_object(
                _recolor(shapes, settings.layer_colors()),
                name=gp.OBJECT_NAME,
                width=float(settings.band_radius),
                widths=settings.layer_widths(),
                # 色块层只填不描边：它的"描边"就是编织带那一圈
                hide_stroke_layers={LAYER_FACES},
                # 编织两层用圆角连接（不写 miter_angle）：90° 转弯处是四分圆弧，
                # 和端头圆角一个观感；色块与辅助线保持斜接，尖角才立得起来
                round_join_layers={LAYER_CASE, LAYER_BAND},
                collection=context.collection,
            )
            obj = gp.find_object()
            if obj is not None:
                for layer_name, prop_name in LAYER_VISIBILITY.items():
                    gp.set_layer_visible(obj, layer_name, getattr(settings, prop_name))
                _store(settings, obj, design)
        except Exception as exc:
            settings.error = str(exc)
            self.report({"ERROR"}, f"grease pencil write failed: {exc}")
            return {"CANCELLED"}

        elapsed = (time.perf_counter() - started) * 1000.0
        settings.last_seed = design.seed
        settings.last_shapes = len(shapes)
        settings.last_ms = elapsed
        settings.last_layers = ", ".join(
            f"{name} x{count}" for name, count in counts.items()
        )
        notes = []
        if design.level_clamped:
            notes.append(f"阶数收回到 {design.level}（请求 {design.requested_level}）")
        settings.last_note = " · ".join(notes)
        settings.dirty = False

        message = (
            f"gen_rosette: seed={design.seed} n={design.petals} level={design.level} "
            f"{design.star_type} {len(shapes)} shapes ({settings.last_layers}), "
            f"{elapsed:.0f} ms"
        )
        message += _viewport_note(view_status, view_fixed)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class GENR_OT_random_seed(bpy.types.Operator):
    """换一个随机种子并重新生成（花型不变，只换配色）"""

    bl_idname = "gen_rosette.random_seed"
    bl_label = "换种子并生成"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .rng import draw_seed

        settings = context.scene.gen_rosette
        settings.seed = draw_seed()
        return bpy.ops.gen_rosette.generate()


class GENR_OT_delete(bpy.types.Operator):
    """删除生成对象"""

    bl_idname = "gen_rosette.delete"
    bl_label = "删除玫瑰线对象"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if not gp.remove_object():
            self.report({"WARNING"}, "no generated object found")
            return {"CANCELLED"}
        context.scene.gen_rosette.dirty = True
        return {"FINISHED"}


class GENR_OT_sync_from_object(bpy.types.Operator):
    """把生成对象上记录的参数读回面板"""

    bl_idname = "gen_rosette.sync_from_object"
    bl_label = "从对象回读参数"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = gp.find_object()
        if obj is None:
            self.report({"WARNING"}, "no generated object found")
            return {"CANCELLED"}

        settings = context.scene.gen_rosette
        if DESIGN_KEYS["seed"] not in obj:
            self.report({"WARNING"}, "object has no stored generation parameters")
            return {"CANCELLED"}

        # 旧对象里可能存着超出 IntProperty 范围的值，先收进合法区间
        settings.seed = int(obj[DESIGN_KEYS["seed"]]) & SEED_MAX
        settings.petals = int(obj.get(DESIGN_KEYS["petals"], settings.petals))
        settings.level = int(obj.get(DESIGN_KEYS["level"], settings.level))
        star_type = str(obj.get(DESIGN_KEYS["star_type"], settings.star_type))
        # 枚举只接受自己的标识；老对象里存了个别的值就别动面板
        if star_type in {item[0] for item in STAR_TYPE_ITEMS}:
            settings.star_type = star_type
        settings.canvas = float(obj.get(DESIGN_KEYS["canvas"], settings.canvas))
        settings.margin = float(obj.get(DESIGN_KEYS["margin"], settings.margin))
        settings.band_radius = float(
            obj.get(DESIGN_KEYS["band_radius"], settings.band_radius)
        )
        settings.casing = float(obj.get(DESIGN_KEYS["casing"], settings.casing))
        settings.gap = float(obj.get(DESIGN_KEYS["gap"], settings.gap))
        settings.tuck = bool(obj.get(DESIGN_KEYS["tuck"], settings.tuck))
        settings.dirty = False
        self.report(
            {"INFO"},
            f"read back seed={settings.seed} n={settings.petals} level={settings.level}",
        )
        return {"FINISHED"}


class GENR_OT_setup_scene(bpy.types.Operator):
    """建正交相机、铺项目画布底色、切 Standard 色彩并打开 GP 抗锯齿"""

    bl_idname = "gen_rosette.setup_scene"
    bl_label = "场景预设"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.gen_rosette
        scene = context.scene
        centre, span = _frame_box(settings)
        view_status, view_fixed = viewport.ensure_vertex_colour(context)

        camera = scene.camera
        if camera is None or camera.type != "CAMERA":
            camera_data = bpy.data.cameras.new("Gen Rosette Camera")
            camera = bpy.data.objects.new("Gen Rosette Camera", camera_data)
            (context.collection or scene.collection).objects.link(camera)
            scene.camera = camera

        camera.data.type = "ORTHO"
        camera.data.ortho_scale = span
        # 图案画在 XY 平面（z=0），所以正视相机放在 +Z、朝 -Z 看，屏幕上 +Y 朝上。
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

        _paint_canvas(scene)
        # 背景切到「世界」：否则 Solid 模式仍按主题色块铺底，看不见刚铺好的画布底色
        world_views = viewport.ensure_world_background(context)

        message = (
            f"camera ortho_scale={span:g}, 2048x2048, Standard view transform, "
            f"background {BACKGROUND}"
        )
        message += _viewport_note(view_status, view_fixed)
        if world_views:
            message += f" | viewport background set to World ({world_views} view(s))"
        self.report({"INFO"}, message)
        return {"FINISHED"}
