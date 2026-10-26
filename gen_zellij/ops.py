"""算子：生成 / 换种子 / 删除 / 场景设置 / 从对象回读参数。

生成流程是「算法层出 ``Shape`` → GP 后端写对象」，算子只做编排与参数校验；
所有能失败的地方都收成 ``report``，不把 traceback 甩给用户。
"""

import time

import bpy

from . import gp, viewport
from .palette import BACKGROUND, hex_to_linear
from .props import DESIGN_KEYS, LAYER_VISIBILITY
from .rng import SEED_MAX
from .scene import LAYER_FILLS, LAYER_LINES

__all__ = [
    "GENZ_OT_delete",
    "GENZ_OT_generate",
    "GENZ_OT_random_seed",
    "GENZ_OT_setup_scene",
    "GENZ_OT_sync_from_object",
]

#: 一块可填充形状都没有时的重试次数（确定性步进：seed+1）。
_EMPTY_RETRIES = 8


def _build(settings):
    """跑一次「设计 → 图层」，返回 ``(shapes, seed_used, attempts)``。

    零填充兜底：万一这张图一块可填充瓷砖都没有（并块把瓷砖吃光、或密度太低），
    按确定性步进换 seed 重试；同一个输入永远得到同一个结果。
    """
    from .design import build_design
    from .scene import build_layers

    seed = int(settings.seed)
    params = settings.design_params()
    for attempt in range(_EMPTY_RETRIES):
        design = build_design(seed, **params)
        shapes = build_layers(
            design,
            size=float(settings.canvas),
            margin=float(settings.margin),
            # 编织的缺口是几何：带宽 / 间隙进这里，由 build_layers 一次算好
            weave_style=settings.weave_style(),
        )
        if any(shape.fill is not None for shape in shapes):
            return shapes, seed, attempt + 1
        seed = (seed + 1) & SEED_MAX
    raise RuntimeError("no fillable shapes after 8 seeds; adjust density or focus")


def _recolor(shapes, colours: dict):
    """面板可覆盖的颜色：编织两层与两个调试图层，其余图层原样返回。

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
        world = bpy.data.worlds.new("Gen Zellij World")
        scene.world = world

    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    colour = hex_to_linear(BACKGROUND)
    if background is not None:
        background.inputs[0].default_value = (*colour, 1.0)
    # 无光照图层不吃世界光，这里同步一份给物体的"世界底色"显示
    world.color = colour


def _frame_box(settings):
    """相机取景：优先用生成对象的实际包围盒，没有对象时退回画布正方形。

    设计是按「包围盒左下角对齐 (margin, margin)」摆的（见 ``scene._flip_matrix``），
    等比缩放后总有一条边留白，所以按图案本身取景比按画布取景构图更好。
    """
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


def _store(settings, obj, seed: int) -> None:
    """把输入参数写进对象：换机器、换文件后还能取回这次生成的依据。

    自定义属性里的整数同样是 **32 位有符号**（实测写 4e9 会 OverflowError），
    所以 seed 必须落在 ``rng.SEED_MAX`` 以内 —— 面板属性与抽种子都已按此收敛。
    """
    obj[DESIGN_KEYS[0]] = int(seed)
    obj[DESIGN_KEYS[1]] = int(settings.density)
    obj[DESIGN_KEYS[2]] = str(settings.focus)
    obj[DESIGN_KEYS[3]] = bool(settings.shimmer)
    obj[DESIGN_KEYS[4]] = float(settings.canvas)
    obj[DESIGN_KEYS[5]] = float(settings.margin)
    obj[DESIGN_KEYS[6]] = float(settings.outline_radius)
    obj[DESIGN_KEYS[7]] = float(settings.interlace_radius)
    obj[DESIGN_KEYS[8]] = float(settings.interlace_gap)
    obj[DESIGN_KEYS[9]] = float(settings.interlace_casing)
    obj[DESIGN_KEYS[10]] = bool(settings.interlace_tuck)


class GENZ_OT_generate(bpy.types.Operator):
    """按面板参数生成一块 zellij 图案（Grease Pencil v3）"""

    bl_idname = "gen_zellij.generate"
    bl_label = "生成 Zellij"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .rng import draw_seed

        settings = context.scene.gen_zellij
        settings.error = ""

        if settings.random_seed:
            settings.seed = draw_seed()

        # 先确认 Solid 模式看得见顶点色，否则生成完是一片黑（见 viewport 文档）
        view_status, view_fixed = viewport.ensure_vertex_colour(context)

        started = time.perf_counter()
        try:
            shapes, seed, attempts = _build(settings)
        except Exception as exc:
            settings.error = str(exc)
            settings.dirty = True
            self.report({"ERROR"}, f"design failed: {exc}")
            return {"CANCELLED"}

        try:
            counts = gp.build_object(
                _recolor(shapes, settings.layer_colors()),
                name=gp.OBJECT_NAME,
                width=float(settings.line_radius),
                widths=settings.layer_widths(),
                dash_layer=LAYER_LINES,
                # 填充层只填不描边：描边在 Outlines 层，两者共用同一套几何
                hide_stroke_layers={LAYER_FILLS},
                collection=context.collection,
            )
            obj = gp.find_object()
            if obj is not None:
                modifier = obj.modifiers.get(gp.DASH_MODIFIER_NAME)
                if modifier is not None:
                    modifier.show_viewport = bool(settings.dashed)
                    modifier.show_render = bool(settings.dashed)
                for layer_name, prop_name in LAYER_VISIBILITY.items():
                    gp.set_layer_visible(obj, layer_name, getattr(settings, prop_name))
                _store(settings, obj, seed)
        except Exception as exc:
            settings.error = str(exc)
            self.report({"ERROR"}, f"grease pencil write failed: {exc}")
            return {"CANCELLED"}

        elapsed = (time.perf_counter() - started) * 1000.0
        settings.seed = seed
        settings.last_seed = seed
        settings.last_shapes = len(shapes)
        settings.last_ms = elapsed
        settings.last_layers = ", ".join(
            f"{name} x{count}" for name, count in counts.items()
        )
        settings.last_note = f"重试 {attempts} 次" if attempts > 1 else ""
        settings.dirty = False

        message = (
            f"gen_zellij: seed={seed} density={settings.density} "
            f"{len(shapes)} shapes ({settings.last_layers}), {elapsed:.0f} ms"
        )
        message += _viewport_note(view_status, view_fixed)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class GENZ_OT_random_seed(bpy.types.Operator):
    """换一个随机种子并重新生成"""

    bl_idname = "gen_zellij.random_seed"
    bl_label = "换种子并生成"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .rng import draw_seed

        settings = context.scene.gen_zellij
        settings.seed = draw_seed()
        return bpy.ops.gen_zellij.generate()


class GENZ_OT_delete(bpy.types.Operator):
    """删除生成对象"""

    bl_idname = "gen_zellij.delete"
    bl_label = "删除 Zellij 对象"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if not gp.remove_object():
            self.report({"WARNING"}, "no generated object found")
            return {"CANCELLED"}
        context.scene.gen_zellij.dirty = True
        return {"FINISHED"}


class GENZ_OT_sync_from_object(bpy.types.Operator):
    """把生成对象上记录的参数读回面板"""

    bl_idname = "gen_zellij.sync_from_object"
    bl_label = "从对象回读参数"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = gp.find_object()
        if obj is None:
            self.report({"WARNING"}, "no generated object found")
            return {"CANCELLED"}

        settings = context.scene.gen_zellij
        keys = DESIGN_KEYS
        if keys[0] not in obj:
            self.report({"WARNING"}, "object has no stored generation parameters")
            return {"CANCELLED"}

        # 旧对象里可能存着超出 IntProperty 范围的值（32 位无符号），先收进合法区间
        settings.seed = int(obj[keys[0]]) & SEED_MAX
        settings.density = str(obj[keys[1]])
        settings.focus = str(obj[keys[2]])
        settings.shimmer = bool(obj[keys[3]])
        settings.canvas = float(obj[keys[4]])
        settings.margin = float(obj[keys[5]])
        settings.outline_radius = float(obj[keys[6]])
        # 旧对象没有这几项（编织是后加的），缺了就保留面板当前值
        if keys[7] in obj:
            settings.interlace_radius = float(obj[keys[7]])
        if keys[8] in obj:
            settings.interlace_gap = float(obj[keys[8]])
        if keys[9] in obj:
            stored = float(obj[keys[9]])
            # 老对象存的是世界单位的描边半径（~0.0125），新的是倍数（≥ 1）
            if stored > 1.0:
                settings.interlace_casing = stored
            else:
                settings.interlace_casing = stored / max(
                    float(settings.interlace_radius), 1e-6
                )
        if keys[10] in obj:
            settings.interlace_tuck = bool(obj[keys[10]])
        settings.dirty = False
        self.report(
            {"INFO"}, f"read back seed={settings.seed} density={settings.density}"
        )
        return {"FINISHED"}


class GENZ_OT_setup_scene(bpy.types.Operator):
    """建正交相机、铺项目画布底色、切 Standard 色彩并打开 GP 抗锯齿"""

    bl_idname = "gen_zellij.setup_scene"
    bl_label = "场景预设"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.gen_zellij
        scene = context.scene
        centre, span = _frame_box(settings)
        view_status, view_fixed = viewport.ensure_vertex_colour(context)

        camera = scene.camera
        if camera is None or camera.type != "CAMERA":
            camera_data = bpy.data.cameras.new("Gen Zellij Camera")
            camera = bpy.data.objects.new("Gen Zellij Camera", camera_data)
            context.collection.objects.link(camera)
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

        message = (
            f"camera ortho_scale={span:g}, 2048x2048, Standard view transform, "
            f"background {BACKGROUND}"
        )
        message += _viewport_note(view_status, view_fixed)
        self.report({"INFO"}, message)
        return {"FINISHED"}
