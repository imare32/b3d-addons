"""算子：生成 / 删除 / 场景预设 / 参数回读 / 基因复制粘贴 / **HUD 编辑**。

HUD 编辑是本插件与参考实现最大的交互差别：原版的编辑发生在侧栏的基因列表里（改两个整数、
点两个小图标），预览靠三条临时曲线对象；这里是一个视口内的**模态 HUD**：点阵上直接拖节点、
段上点 R/M 切标记、滚轮换等级，视口里的预览（:mod:`preview`）与 HUD 同步刷新，全程不建
任何临时数据块。

模态算子的取巧点：

* 面板只画在**发起会话的那个 3D 视图**里（``hud`` 按区域指针过滤），别的视图不受影响；
* 鼠标坐标直接用 ``event.mouse_region_x/y`` —— 与 ``POST_PIXEL`` 是同一个像素空间，
  命中测试不必做投影换算；
* 拖节点时把像素坐标换算成整数格点（:func:`hud_layout.snap`），所以"怎么拖都是合法的基因"；
* 拖出零长度段时 :meth:`props.GenFractalFamilySettings.move_node` 会拒绝，表现为拖不过去。
"""

from __future__ import annotations

import bpy
from mathutils import Vector

from . import build, hud, hud_layout, live, preview, viewport
from .palette import BACKGROUND, hex_to_linear
from .props import LEVEL_MAX, LEVEL_MIN, read_design, suspend_auto_update

__all__ = [
    "GFF_OT_copy_gene",
    "GFF_OT_delete",
    "GFF_OT_generate",
    "GFF_OT_hud",
    "GFF_OT_item_add",
    "GFF_OT_item_move",
    "GFF_OT_item_remove",
    "GFF_OT_paste_gene",
    "GFF_OT_setup_scene",
    "GFF_OT_sync_from_object",
]


def _settings(context):
    return context.scene.gen_fractal_family


# ---------------------------------------------------------------------------
# 生成 / 删除
# ---------------------------------------------------------------------------


class GFF_OT_generate(bpy.types.Operator):
    """按当前基因生成分形曲线（HUD 与预览用的是同一份算法）"""

    bl_idname = "gen_fractal_family.generate"
    bl_label = "生成分形曲线"
    bl_options = {"REGISTER", "UNDO"}

    mode: bpy.props.EnumProperty(
        name="模式",
        items=[
            ("LAST_LEVEL", "最后一级", "只生成最高级一条曲线"),
            ("EACH_LEVEL", "每一级", "每一级一个对象"),
            ("SHAPE_KEYS", "形态键", "一个对象 + 每级一个形态键"),
        ],
        default="LAST_LEVEL",
    )

    def execute(self, context):
        settings = _settings(context)
        settings.error = ""
        live.cancel()
        try:
            summary = build.rebuild(settings, mode=self.mode, collection=context.collection)
        except Exception as exc:
            settings.error = str(exc)
            settings.dirty = True
            self.report({"ERROR"}, f"分形生成失败：{exc}")
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            f"gen_fractal_family: 等级 {summary['level']} · {summary['points']} 点 · "
            f"{len(summary['objects'])} 个对象 · {summary['ms']:.0f} ms",
        )
        return {"FINISHED"}


class GFF_OT_delete(bpy.types.Operator):
    """删除本插件生成的所有曲线对象"""

    bl_idname = "gen_fractal_family.delete"
    bl_label = "删除生成物"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        live.cancel()
        count = build.delete_generated()
        if not count:
            self.report({"WARNING"}, "没有可删的生成物")
            return {"CANCELLED"}
        _settings(context).dirty = True
        self.report({"INFO"}, f"删除了 {count} 个对象")
        return {"FINISHED"}


class GFF_OT_sync_from_object(bpy.types.Operator):
    """把生成对象上记录的参数读回面板"""

    bl_idname = "gen_fractal_family.sync_from_object"
    bl_label = "从对象回读参数"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        active = context.active_object
        return active is not None and active.get(build.LEVEL_KEY) is not None

    def execute(self, context):
        obj = context.active_object
        settings = _settings(context)
        live.cancel()
        # 回读的值本来就是这一份几何用的值：期间挂起自动更新，省一次白算
        with suspend_auto_update():
            found = read_design(obj, settings)
        if not found:
            self.report({"WARNING"}, "这个对象上没有记录参数")
            return {"CANCELLED"}
        settings.dirty = False
        hud.tag_redraw()
        self.report({"INFO"}, f"读回 {found} 项参数：{settings.current_gene()}")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# 场景预设
# ---------------------------------------------------------------------------


def _paint_canvas(scene) -> None:
    """世界背景铺项目色板的画布底色（转线性后写进节点）。"""
    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("Gen Fractal Family World")
        scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    colour = hex_to_linear(BACKGROUND)
    if background is not None:
        background.inputs[0].default_value = (*colour, 1.0)
        background.inputs[1].default_value = 0.8
    world.color = colour


def _bounds(settings):
    """取景用的包围盒：优先用生成物，没有就用预览点列。"""
    objects = [obj for obj in build.generated() if len(obj.bound_box)]
    if objects:
        corners = []
        for obj in objects:
            corners += [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
        return corners

    lines = preview.lines_for(settings)
    if not lines:
        return []
    points = lines[-1]
    return [Vector((float(x), float(y), 0.0)) for x, y in points[:100000]]


class GFF_OT_setup_scene(bpy.types.Operator):
    """建正交相机（按分形范围取景）、铺画布底色、切 Standard 色彩"""

    bl_idname = "gen_fractal_family.setup_scene"
    bl_label = "场景预设"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        corners = _bounds(_settings(context))
        if not corners:
            self.report({"WARNING"}, "没有可取景的内容：先生成或先预览一条有效基因")
            return {"CANCELLED"}

        centre = sum(corners, Vector()) / len(corners)
        radius = max((corner - centre).length for corner in corners)
        if radius < 1e-6:
            self.report({"WARNING"}, "内容尺寸为零")
            return {"CANCELLED"}

        camera = scene.camera
        if camera is None or camera.type != "CAMERA":
            data = bpy.data.cameras.new("Gen Fractal Family Camera")
            camera = bpy.data.objects.new("Gen Fractal Family Camera", data)
            (context.collection or scene.collection).objects.link(camera)
            scene.camera = camera

        # 分形在 XY 平面上：正交 + 俯视，留 8% 边距
        camera.data.type = "ORTHO"
        margin = 1.08
        camera.data.ortho_scale = radius * 2.0 * margin
        camera.location = Vector((centre.x, centre.y, max(1.0, radius * 4.0)))
        camera.rotation_euler = (0.0, 0.0, 0.0)

        scene.render.resolution_x = 2048
        scene.render.resolution_y = 2048
        scene.render.film_transparent = False
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"

        _paint_canvas(scene)
        moved = viewport.ensure_world_background(context)

        self.report(
            {"INFO"},
            f"正交俯视 · 取景 {camera.data.ortho_scale:.2f} · 2048×2048 · "
            f"画布 {BACKGROUND} · 视口底色切到世界（{moved} 个视图）",
        )
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# 基因列表（侧栏用；HUD 是另一条路）
# ---------------------------------------------------------------------------


class GFF_OT_item_add(bpy.types.Operator):
    """在末尾追加一段"""

    bl_idname = "gen_fractal_family.item_add"
    bl_label = "添加一段"

    def execute(self, context):
        settings = _settings(context)
        # 默认沿用最后一段的方向：新手最常做的是"再走一格"
        last = settings.items[-1] if settings.items else None
        settings.add_item(last.a if last else 1, last.b if last else 0)
        hud.tag_redraw()
        return {"FINISHED"}


class GFF_OT_item_remove(bpy.types.Operator):
    """删掉当前选中的一段（至少保留两段）"""

    bl_idname = "gen_fractal_family.item_remove"
    bl_label = "删除这一段"

    @classmethod
    def poll(cls, context):
        return len(_settings(context).items) > 2

    def execute(self, context):
        settings = _settings(context)
        settings.remove_item(settings.active_index)
        hud.tag_redraw()
        return {"FINISHED"}


class GFF_OT_item_move(bpy.types.Operator):
    """把当前选中的一段上移 / 下移"""

    bl_idname = "gen_fractal_family.item_move"
    bl_label = "移动这一段"

    direction: bpy.props.EnumProperty(items=(("UP", "上移", ""), ("DOWN", "下移", "")))

    @classmethod
    def poll(cls, context):
        return len(_settings(context).items) > 1

    def execute(self, context):
        settings = _settings(context)
        items = settings.items
        index = settings.active_index
        neighbour = index + (-1 if self.direction == "UP" else 1)
        if 0 <= neighbour < len(items):
            items.move(index, neighbour)
            settings.active_index = neighbour
            settings.set_elements(settings.elements())
            hud.tag_redraw()
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# 点阵域
# ---------------------------------------------------------------------------


class GFF_OT_set_domain(bpy.types.Operator):
    """切换点阵域（高斯 / 艾森斯坦）。

    为什么不用 ``layout.prop(..., expand=True)``：展开式枚举在 5.2 里画出来是**两块没有
    文字的按钮**（实测：标签被吞掉，只剩一个蓝色高亮块）。改成两个普通按钮 + ``depress``
    标当前值，文字由 ``text=`` 显式给出，稳定可控。
    """

    bl_idname = "gen_fractal_family.set_domain"
    bl_label = "切换点阵域"

    domain: bpy.props.EnumProperty(
        items=(("G", "高斯 G", ""), ("E", "艾森斯坦 E", "")),
        default="G",
    )

    @classmethod
    def poll(cls, context):
        return _settings(context) is not None

    def execute(self, context):
        settings = _settings(context)
        if settings.domain != self.domain:
            # 赋值会触发 _on_domain_change（标脏 + 排 timer），这里只补一次重绘
            settings.domain = self.domain
        hud.reset_view()
        hud.tag_redraw()
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# 基因复制 / 粘贴
# ---------------------------------------------------------------------------


class GFF_OT_copy_gene(bpy.types.Operator):
    """把当前基因复制到剪贴板（就是一串 ``G 1 0 0 0 ...``）"""

    bl_idname = "gen_fractal_family.copy_gene"
    bl_label = "复制基因"

    def execute(self, context):
        settings = _settings(context)
        context.window_manager.clipboard = settings.current_gene()
        self.report({"INFO"}, settings.current_gene())
        return {"FINISHED"}


class GFF_OT_paste_gene(bpy.types.Operator):
    """从剪贴板读一条基因（格式不对就报错，不会写坏当前基因）"""

    bl_idname = "gen_fractal_family.paste_gene"
    bl_label = "粘贴基因"

    def execute(self, context):
        settings = _settings(context)
        text = (context.window_manager.clipboard or "").strip()
        if not text:
            self.report({"WARNING"}, "剪贴板是空的")
            return {"CANCELLED"}
        try:
            settings.apply_gene(text)
        except (ValueError, IndexError) as exc:
            settings.error = f"粘贴失败：{exc}"
            self.report({"ERROR"}, f"粘贴失败：{exc}")
            return {"CANCELLED"}
        settings.error = ""
        hud.tag_redraw()
        self.report({"INFO"}, f"已导入：{settings.family_name()}")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# HUD 编辑（模态）
# ---------------------------------------------------------------------------

#: 悬停光标。节点可以拖，用手形；R/M 只有 12 px 见方，**手形会把两个方块一起挡住**
#: （实测：手形图标比方块还大，悬停时根本看不见 R/M 的字），所以小方块用细十字准星。
_CURSOR_NODE = "HAND"
_CURSOR_FLAG = "CROSSHAIR"
_CURSOR_DEFAULT = "DEFAULT"


def _cursor_for(hotspot) -> str:
    """悬停到某个 hotspot 时该用哪个光标。"""
    if hotspot is None:
        return _CURSOR_DEFAULT
    if hotspot["kind"] == "node":
        return _CURSOR_NODE
    if hotspot["kind"] == "flag":
        return _CURSOR_FLAG
    return _CURSOR_DEFAULT


#: 模态里的按键（**只在指针停在面板上时**生效，其余按键照常走视口快捷键）。
_KEY_DOMAIN = {"TAB", "Q"}
_KEY_ADD = {"A"}
_KEY_REMOVE = {"X", "DELETE"}
_KEY_FIT = {"F"}
_KEY_EXIT = {"ESC"}


class GFF_OT_hud(bpy.types.Operator):
    """在 3D 视图里打开基因 HUD：拖节点改坐标、R/M 切标记、滚轮换等级"""

    bl_idname = "gen_fractal_family.hud"
    bl_label = "HUD 编辑基因"
    bl_description = ("打开视口内的基因 HUD：拖节点改坐标、点 R/M 切标记、滚轮换等级。"
                      "不挡视口操作 —— 指针离开面板后选中 / 摆视角 / 快捷键照常；"
                      "指针在面板上按 Esc 或点 × 收起")

    @classmethod
    def poll(cls, context):
        return getattr(context, "area", None) is not None and context.area.type == "VIEW_3D"

    def invoke(self, context, event):
        if hud.session() is not None:
            # 已经开着：再点一次就是收起来
            self._shutdown(context)
            return {"FINISHED"}
        if not _settings(context).items:
            # 第一次用：给一条能看的基因（龙形曲线）
            _settings(context).apply_gene("G 1 0 0 0 0 1 1 0")
        self._drag = ""
        self._drag_offset = (0.0, 0.0)
        self._region = getattr(context, "region", None)
        hud.begin(context, self._region)
        self._set_cursor(context, _CURSOR_NODE)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    # --- 生命周期 ---------------------------------------------------------

    def _set_cursor(self, context, cursor: str) -> None:
        try:
            context.window.cursor_set(cursor)
        except (AttributeError, TypeError):
            pass

    def _shutdown(self, context) -> None:
        hud.end(context)
        self._drag = ""
        self._set_cursor(context, _CURSOR_DEFAULT)

    # --- 事件 -------------------------------------------------------------

    def modal(self, context, event):
        """**不抢视口**的模态：只吃指针落在面板上的事件，其余一律 ``PASS_THROUGH``。

        这一点很重要 —— 早期版本把所有事件都吃掉，HUD 一开就没法摆视角 / 选中 / 用别的
        面板。现在的规则是：

        * 指针在面板外：所有事件照常交给视口与快捷键（面板只是"看得见、不挡路"）；
        * 指针在面板内：点击 / 滚轮 / 面板上写明的几个按键归 HUD；
        * 正在拖（节点或标题栏）时：不管指针跑到哪都归 HUD，否则松手事件被别人吃掉、
          节点会卡在拖拽状态。
        """
        if hud.session() is None:
            self._set_cursor(context, _CURSOR_DEFAULT)
            return {"FINISHED"}

        settings = _settings(context)
        layout = hud.layout_for(context)
        if layout is None:
            self._shutdown(context)
            return {"FINISHED"}

        x = event.mouse_region_x
        y = event.mouse_region_y
        inside = self._inside(layout, x, y)
        dragging = bool(self._drag)

        if event.type == "MOUSEMOVE":
            self._on_mouse_move(context, settings, layout, x, y)
            return {"RUNNING_MODAL"} if dragging else {"PASS_THROUGH"}

        if event.type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            if not inside:
                return {"PASS_THROUGH"}
            step = 1 if event.type == "WHEELUPMOUSE" else -1
            if event.shift:
                # Shift + 滚轮：缩放 HUD 视图（不改基因，只是看得细一点）
                session = hud.session()
                zoom = session.get("zoom") or layout["zoom"]
                factor = 1.15 if step > 0 else 1.0 / 1.15
                session["zoom"] = max(
                    hud_layout.MIN_ZOOM, min(hud_layout.MAX_ZOOM * 2.0, zoom * factor)
                )
            else:
                settings.level = max(LEVEL_MIN, min(int(settings.level) + step, LEVEL_MAX))
            hud.tag_redraw()
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE":
            if event.value == "PRESS":
                target = hud_layout.hit(layout["hotspots"], x, y)
                if target is None:
                    # 面板是实心的：点空白处不该"穿透"去选中它后面的物体
                    return {"RUNNING_MODAL"} if inside else {"PASS_THROUGH"}
                if self._on_press(context, settings, layout, target, x, y) == "EXIT":
                    self._shutdown(context)
                    return {"FINISHED"}
                return {"RUNNING_MODAL"}
            if not dragging:
                return {"PASS_THROUGH"}
            self._drag = ""
            if hud.session() is not None:
                hud.session()["drag"] = ""
            hud.tag_redraw()
            return {"RUNNING_MODAL"}

        if event.type == "RIGHTMOUSE" and event.value == "PRESS":
            if not inside and not dragging:
                return {"PASS_THROUGH"}
            # 面板上右键 = 收起 HUD（和 × 一样）；拖拽中右键 = 松手，避免节点卡住
            if dragging:
                self._drag = ""
                hud.session()["drag"] = ""
                hud.tag_redraw()
                return {"RUNNING_MODAL"}
            self._shutdown(context)
            return {"FINISHED"}

        if event.value != "PRESS":
            return {"PASS_THROUGH"}

        # 键盘：只有指针停在面板上才接管，否则让快捷键照常工作
        if not inside and not dragging:
            return {"PASS_THROUGH"}

        if event.type in _KEY_EXIT:
            if dragging:
                self._drag = ""
                hud.session()["drag"] = ""
                hud.tag_redraw()
                return {"RUNNING_MODAL"}
            self._shutdown(context)
            return {"FINISHED"}

        if event.type in _KEY_DOMAIN:
            settings.domain = "E" if settings.domain == "G" else "G"
            hud.reset_view()
            return {"RUNNING_MODAL"}

        if event.type in _KEY_ADD:
            self._append_segment(settings)
            return {"RUNNING_MODAL"}

        if event.type in _KEY_REMOVE:
            settings.remove_item(-1)
            return {"RUNNING_MODAL"}

        if event.type in _KEY_FIT:
            hud.reset_view()
            return {"RUNNING_MODAL"}

        return {"PASS_THROUGH"}

    # --- 交互细节 ---------------------------------------------------------

    @staticmethod
    def _append_segment(settings) -> None:
        """末尾追加一段（沿用最后一段的方向：新手最常做的是"再走一格"）。"""
        last = settings.items[-1] if settings.items else None
        settings.add_item(last.a if last else 1, last.b if last else 0)

    def _on_press(self, context, settings, layout, target, x, y):
        """处理一次点击；返回 ``"EXIT"`` 表示要收起面板。"""
        if target is None:
            return None
        kind = target["kind"]
        ident = target["id"]

        if kind == "node":
            self._drag = ident
            hud.session()["drag"] = ident
        elif kind == "flag":
            settings.toggle_flag(int(target["index"]), str(target["flag"]))
        elif kind == "bar":
            # 拖标题栏搬面板
            self._drag = "bar"
            origin = hud.session()["origin"]
            self._drag_offset = (x - origin[0], y - origin[1])
        elif ident == "btn:close":
            return "EXIT"
        elif ident.startswith("btn:domain:"):
            settings.domain = ident.rsplit(":", 1)[-1]
            hud.reset_view()
        elif ident == "btn:level:+":
            settings.level = min(LEVEL_MAX, int(settings.level) + 1)
        elif ident == "btn:level:-":
            settings.level = max(LEVEL_MIN, int(settings.level) - 1)
        elif ident == "btn:add":
            self._append_segment(settings)
            hud.reset_view()
        elif ident == "btn:remove":
            settings.remove_item(-1)
            hud.reset_view()
        elif ident == "btn:fit":
            hud.reset_view()
        hud.tag_redraw()
        return None

    def _on_mouse_move(self, context, settings, layout, x, y) -> None:
        session = hud.session()
        if self._drag == "bar":
            # 搬面板：留一点边在外面，别让用户把整块面板拖进看不见的地方
            region = getattr(context, "region", None)
            if region is not None:
                session["size"] = [region.width, region.height]
            _panel_x, _panel_y, panel_w, panel_h = layout["panel"]
            area_w, area_h = session["size"]
            session["origin"] = [
                max(-panel_w + 80.0, min(x - self._drag_offset[0], area_w - 80.0)),
                max(-panel_h + 40.0, min(y - self._drag_offset[1], area_h - 30.0)),
            ]
            hud.tag_redraw()
            return
        if self._drag.startswith("node:"):
            index = int(self._drag.split(":", 1)[1])
            origin = layout["lattice_origin"]
            a, b = hud_layout.snap(settings.domain, x, y, origin[0], origin[1], layout["zoom"])
            if settings.move_node(index, a, b):
                hud.tag_redraw()
            return
        hovered = hud_layout.hit(layout["hotspots"], x, y)
        ident = hovered["id"] if hovered else ""
        if ident != session.get("hover"):
            session["hover"] = ident
            self._set_cursor(context, _cursor_for(hovered))
            hud.tag_redraw()

    @staticmethod
    def _inside(layout, x, y) -> bool:
        px, py, pw, ph = layout["panel"]
        return px <= x <= px + pw and py <= y <= py + ph
