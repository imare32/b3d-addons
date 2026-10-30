"""算子：生成 / 删除 / 场景预设 / 从对象回读参数。

生成流程是「算法层出 ``Shape`` → GP 后端写对象」，算子只做编排与参数校验；
所有能失败的地方都收成 ``report``，不把 traceback 甩给用户。
"""

import time

import bpy

from . import gp, viewport
from .palette import PAPER, hex_to_linear, srgb_to_linear
from .props import read_design, store_design
from .scene import LAYER_BAND, LAYER_CASE, LAYER_LINES, STYLE_LINES, build_shapes

__all__ = [
    "GIRIH_OT_delete",
    "GIRIH_OT_generate",
    "GIRIH_OT_setup_scene",
    "GIRIH_OT_sync_from_object",
]


def _build(settings, angle: float | None = None):
    """跑一次「镶嵌 → 股线」，返回 ``(shapes, 统计)``。

    ``angle`` 可以覆盖面板上的接触角（扫掠逐帧调用时用），其余参数照常读面板。
    """
    return build_shapes(
        settings.tiling_key,
        float(settings.angle if angle is None else angle),
        float(settings.gap),
        style=settings.style,
        size=float(settings.size),
        canvas=float(settings.canvas),
        colour=tuple(settings.colour),
        case_colour=tuple(settings.case_colour),
        width=float(settings.width),
        casing=float(settings.casing),
        seam=float(settings.seam),
        fills=bool(settings.fills),
        fill_colour=tuple(settings.fill_colour),
        fill_by_sides=bool(settings.fill_by_sides),
        fill_colours=settings.fill_colours(),
        show_tiles=bool(settings.show_tiles),
    )


def _apply_visibility(obj, settings) -> None:
    """重建之后把面板上的图层显隐重新贴回去（重建会把图层全新建一遍）。"""
    if settings.style == STYLE_LINES:
        gp.set_layer_visible(obj, LAYER_LINES, True)
    else:
        gp.set_layer_visible(obj, LAYER_CASE, bool(settings.show_case))
        gp.set_layer_visible(obj, LAYER_BAND, bool(settings.show_band))


def _viewport_note(status: str, fixed: int) -> str:
    """把视口着色检查结果拼成报告尾巴（一切正常时不加噪音）。

    report 文本保持纯 ASCII：终端 / 任务输出的编码不一定支持中文，混进去会乱码。
    """
    if status == viewport.FIXED:
        return f" | viewport shading set to Attribute ({fixed} view(s))"
    if status == viewport.NO_VIEW:
        return " | tip: in Solid mode set viewport color to Attribute, else it renders black"
    return ""


def _paint_canvas(scene) -> None:
    """世界背景铺**面板上的画布底色**，转线性后写进节点。

    颜色来自 ``settings.canvas_colour``（默认是色板的 ``PAPER`` #F9F7F3）：girih 的默认
    观感是"暖金细线 + 墨玄描边"的线稿，比 ``BACKGROUND``（#E4D9D0）更浅、几乎无色偏，
    深色描边在它上面反差最大。填色块类的图案（zellij / rosette）继续用 ``BACKGROUND``：
    它们的本体是绢白，压到纸白上就看不见了。
    """
    settings = getattr(scene, "gen_girih", None)
    if settings is not None:
        colour = tuple(srgb_to_linear(component) for component in settings.canvas_colour)
    else:
        colour = hex_to_linear(PAPER)
    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("Gen Girih World")
        scene.world = world

    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs[0].default_value = (*colour, 1.0)
    # 无光照图层不吃世界光，这里同步一份给物体的"世界底色"显示
    world.color = colour


def repaint_canvas(context) -> None:
    """按面板上的画布底色重铺世界背景（换配色预设 / 改底色时立刻生效）。"""
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    _paint_canvas(scene)


def _frame_box(settings):
    """相机取景：**按画布正方形**取景，而不是按对象包围盒。

    图案是无限重复的，生成时会向外多要一圈砖（取景矩形 + 一格），所以对象包围盒比
    画布大一圈——按包围盒取景会把构图拉远、四周多出一堆半个星形。按画布取景等价于
    参考实现的 SVG ``viewBox``：画面就是画布，溢出的笔划被画面边缘切开。
    """
    canvas = float(settings.canvas)
    return (canvas * 0.5, canvas * 0.5), canvas


#: 自动重建的防抖延迟（秒）：拖动滑块会连发 update，合并成一次重建。
#:
#: 0.25 s 是"松手就出图"与"别在拖动中途卡 UI"的折中。默认参数整张图 30 ms 上下，
#: 最密的三角形 80 ms —— 都是可以接受的单次停顿。
AUTO_DELAY = 0.25


def _record(settings, stats, elapsed: float) -> None:
    """把统计写进面板读数（重建与自动重建共用）。"""
    settings.last_tiles = int(stats["tiles"])
    settings.last_segments = int(stats["segments"])
    settings.last_strands = int(stats["strands"])
    settings.last_crossings = int(stats["crossings"])
    settings.last_fills = int(stats.get("fills", 0))
    settings.last_cuts = int(stats.get("cuts", 0))
    settings.last_pruned = int(stats.get("pruned", 0))
    settings.last_patches = int(stats.get("patches", 0))
    settings.last_mismatches = int(stats.get("mismatches", 0))
    settings.last_points = int(stats["points"])
    settings.last_ms = elapsed
    if stats.get("degenerate"):
        settings.last_note = (
            "切口吃光了整条带子，已退回不切：调小描边倍数 / 缝隙，或换个接触角"
        )
    elif settings.last_pruned > 0.1 * max(1, settings.last_crossings):
        settings.last_note = (
            f"{settings.last_pruned} 处交叉退化为不编（收刀）：双线间隙会把股线拆碎，"
            "想要完整编织请把「双线间隙」调回 0"
        )
    else:
        settings.last_note = ""


def rebuild(context, settings) -> tuple[list, dict, float]:
    """按面板参数重建对象，返回 ``(shapes, 统计, 耗时 ms)``。

    算子与自动重建共用；出错时原样抛出，由调用方决定是弹报错还是写面板。
    """
    started = time.perf_counter()
    shapes, stats = _build(settings)
    gp.build_object(
        shapes,
        name=gp.OBJECT_NAME,
        width=float(settings.width),
        widths=settings.layer_widths(),
        opacity=float(settings.opacity),
        collection=context.collection or getattr(context.scene, "collection", None),
    )
    elapsed = (time.perf_counter() - started) * 1000.0
    obj = gp.find_object()
    if obj is not None:
        _apply_visibility(obj, settings)
        store_design(obj, settings)
    # 单帧生成会把扫掠留下的多帧覆盖掉，时间线也一起收回第 1 帧
    scene = getattr(context, "scene", None)
    if scene is not None:
        scene.frame_start = 1
        scene.frame_end = 1
        scene.frame_set(1)
    _record(settings, stats, elapsed)
    settings.dirty = False
    settings.error = ""
    return shapes, stats, elapsed


def _summary(settings, shapes, stats, elapsed: float) -> str:
    """一行 ASCII 摘要（终端编码不一定支持中文）。"""
    message = (
        f"gen_girih: {settings.tiling_key} {settings.style.lower()} "
        f"angle={settings.angle:g} {len(shapes)} strokes / {stats['points']} points "
        f"({stats['tiles']} tiles, {stats['strands']} strands, "
        f"{stats['crossings']} crossings"
    )
    if stats.get("fills"):
        message += f", {stats['fills']} fills"
    if stats.get("cuts"):
        message += (
            f", {stats['cuts']} cuts, {stats['patches']} patches, "
            f"{stats['pieces']} pieces"
        )
        if stats.get("stubs"):
            message += f", {stats['stubs']} stubs dropped"
    message += f"), {elapsed:.0f} ms"
    if settings.style == "INTERLACE" and settings.casing <= 1.0:
        message += " | casing=1.0 has no visible edge - the weave will not read"
    return message


def _rebuild_tick():
    """timer 回调：真正重建一次（失败只写到面板，不弹窗、不打断操作）。"""
    scene = getattr(bpy.context, "scene", None)
    settings = getattr(scene, "gen_girih", None)
    if settings is None or not settings.dirty:
        return None
    try:
        rebuild(bpy.context, settings)
    except Exception as exc:  # noqa: BLE001 —— 面板上显示就够了
        settings.error = str(exc)
        settings.dirty = True
    return None


def schedule_rebuild() -> None:
    """把「重建」排到 :data:`AUTO_DELAY` 之后；已排过就不重复排。

    几何参数一改就调它，于是拖动滑块停下来 0.25 s 图案自己更新——不用再点「更新」。
    """
    scene = getattr(bpy.context, "scene", None)
    settings = getattr(scene, "gen_girih", None)
    if settings is None or not settings.auto_rebuild or not settings.dirty:
        return
    if bpy.app.timers.is_registered(_rebuild_tick):
        return
    bpy.app.timers.register(_rebuild_tick, first_interval=AUTO_DELAY)


def cancel_rebuild() -> None:
    """取消待执行的重建（卸载、加载文件、关闭自动重建时用）。"""
    if bpy.app.timers.is_registered(_rebuild_tick):
        bpy.app.timers.unregister(_rebuild_tick)


class GIRIH_OT_generate(bpy.types.Operator):
    """按面板参数生成一张星形纹样（Grease Pencil v3）"""

    bl_idname = "gen_girih.generate"
    bl_label = "生成 Girih"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.gen_girih

        # 先确认 Solid 模式看得见顶点色，否则生成完是一片黑（见 viewport 文档）
        view_status, view_fixed = viewport.ensure_vertex_colour(context)

        try:
            shapes, stats, elapsed = rebuild(context, settings)
        except Exception as exc:
            settings.error = str(exc)
            settings.dirty = True
            self.report({"ERROR"}, f"generate failed: {exc}")
            return {"CANCELLED"}

        message = _summary(settings, shapes, stats, elapsed)
        message += _viewport_note(view_status, view_fixed)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class GIRIH_OT_sweep(bpy.types.Operator):
    """把接触角从起始扫到结束，逐帧烘焙成 Grease Pencil 多帧动画"""

    bl_idname = "gen_girih.sweep"
    bl_label = "烘焙扫掠"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        settings = scene.gen_girih
        count = max(2, int(settings.sweep_frames))
        start = float(settings.sweep_from)
        end = float(settings.sweep_to)
        if abs(end - start) < 1e-6:
            self.report({"WARNING"}, "start and end angle are the same")
            return {"CANCELLED"}

        viewport.ensure_vertex_colour(context)

        began = time.perf_counter()
        frames = []
        stats: dict = {}
        try:
            for index in range(count):
                angle = start + (end - start) * index / (count - 1)
                shapes, stats = _build(settings, angle)
                frames.append(shapes)
        except Exception as exc:
            settings.error = str(exc)
            self.report({"ERROR"}, f"sweep failed: {exc}")
            return {"CANCELLED"}

        try:
            gp.build_frames(
                frames,
                name=gp.OBJECT_NAME,
                frame_start=1,
                width=float(settings.width),
                widths=settings.layer_widths(),
                opacity=float(settings.opacity),
                collection=context.collection or scene.collection,
            )
            obj = gp.find_object()
            if obj is not None:
                _apply_visibility(obj, settings)
                store_design(obj, settings)
        except Exception as exc:
            settings.error = str(exc)
            self.report({"ERROR"}, f"grease pencil write failed: {exc}")
            return {"CANCELLED"}

        scene.frame_start = 1
        scene.frame_end = count
        scene.frame_set(1)

        elapsed = (time.perf_counter() - began) * 1000.0
        _record(settings, stats, elapsed / count)
        settings.dirty = False
        settings.error = ""
        self.report(
            {"INFO"},
            f"gen_girih: swept {start:g}..{end:g} deg over {count} frames, "
            f"{elapsed:.0f} ms total",
        )
        return {"FINISHED"}


class GIRIH_OT_delete(bpy.types.Operator):
    """删除生成对象"""

    bl_idname = "gen_girih.delete"
    bl_label = "删除 Girih 对象"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if not gp.remove_object():
            self.report({"WARNING"}, "no generated object found")
            return {"CANCELLED"}
        context.scene.gen_girih.dirty = True
        return {"FINISHED"}


class GIRIH_OT_sync_from_object(bpy.types.Operator):
    """把生成对象上记录的参数读回面板"""

    bl_idname = "gen_girih.sync_from_object"
    bl_label = "从对象回读参数"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = gp.find_object()
        if obj is None:
            self.report({"WARNING"}, "no generated object found")
            return {"CANCELLED"}

        settings = context.scene.gen_girih
        found = read_design(obj, settings)
        if not found:
            self.report({"WARNING"}, "object has no stored generation parameters")
            return {"CANCELLED"}
        settings.dirty = False
        self.report({"INFO"}, f"read back {found} parameter(s)")
        return {"FINISHED"}


class GIRIH_OT_setup_scene(bpy.types.Operator):
    """建正交相机、铺项目画布底色、切 Standard 色彩并打开 GP 抗锯齿"""

    bl_idname = "gen_girih.setup_scene"
    bl_label = "场景预设"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.gen_girih
        scene = context.scene
        centre, span = _frame_box(settings)
        view_status, view_fixed = viewport.ensure_vertex_colour(context)

        camera = scene.camera
        if camera is None or camera.type != "CAMERA":
            camera_data = bpy.data.cameras.new("Gen Girih Camera")
            camera = bpy.data.objects.new("Gen Girih Camera", camera_data)
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

        hexed = "#%02X%02X%02X" % tuple(round(component * 255) for component in settings.canvas_colour)
        message = (
            f"camera ortho_scale={span:g}, 2048x2048, Standard view transform, "
            f"background {hexed}"
        )
        message += _viewport_note(view_status, view_fixed)
        if world_views:
            message += f" | viewport background set to World ({world_views} view(s))"
        self.report({"INFO"}, message)
        return {"FINISHED"}
