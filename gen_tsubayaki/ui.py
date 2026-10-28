"""侧栏面板：``View3D > 侧栏 > Tsubayaki``。

面板只负责显示与调用算子：参数写进 ``scene.gen_tsubayaki``，生成/删除/场景预设走
``gen_tsubayaki.*`` 算子，所以同样的操作在搜索菜单（F3）里也能找到。

和模拟类扩展不同，这里**没有"待更新"这一档**：任何参数一改就当场按当前帧重画
（闭式重算 0.1 ms 量级，见 :mod:`.props`），所以面板上只留「生成」（第一次建对象）
「重画」「删除」三个动作，其余全是实时读数。
"""

import bpy

from . import gp

__all__ = ["GENT_PT_main"]


class GENT_PT_main(bpy.types.Panel):
    bl_idname = "GENT_PT_main"
    bl_label = "Gen Tsubayaki"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Tsubayaki"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.gen_tsubayaki
        obj = gp.find()

        # --- 图案 ---------------------------------------------------------
        box = layout.box()
        box.label(text="图案", icon="GP_SELECT_STROKES")
        column = box.column(align=True)
        column.prop(settings, "mode")
        column.prop(settings, "variant")

        tiled = settings.mode == "TILED"
        column = box.column(align=True)
        column.enabled = tiled
        column.prop(settings, "constrain_bound")
        column.prop(settings, "tile_size")
        if not tiled:
            box.label(text="单轨星图：一圈弦围成星形", icon="INFO")
        elif settings.variant == "ALL":
            box.label(text="2x2 铺四个变体（参考只画一个）", icon="INFO")

        # --- 生长（帧 → 参数） ---------------------------------------------
        box = layout.box()
        box.label(text="生长（帧 → 参数）", icon="TIME")
        box.prop(settings, "animate")
        column = box.column(align=True)
        column.enabled = settings.animate
        row = column.row(align=True)
        row.prop(settings, "n_init")
        row.prop(settings, "n_growth")
        row = column.row(align=True)
        row.prop(settings, "t_init")
        row.prop(settings, "t_growth")
        if settings.animate:
            box.label(text="n / t 由帧号闭式给出，可任意跳帧、回退", icon="LOOP_BACK")
        else:
            box.label(text="只用初始值，图案不随时间轴变", icon="PAUSE")

        # --- 动作 ---------------------------------------------------------
        row = layout.row(align=True)
        row.scale_y = 1.4
        text = "重新长一遍" if obj is not None else "生成"
        row.operator("gen_tsubayaki.generate", text=text, icon="MOD_BUILD")
        row.operator("gen_tsubayaki.delete", text="", icon="TRASH")

        # --- 墨色 ---------------------------------------------------------
        box = layout.box()
        box.label(text="墨色", icon="BRUSH_DATA")
        row = box.row(align=True)
        row.prop(settings, "ink_color", text="")
        row.prop(settings, "radius", text="线宽")

        row = box.row(align=True)
        row.prop(settings, "fade", text="墨韵淡出")
        sub = row.row(align=True)
        sub.enabled = settings.fade
        sub.prop(settings, "fade_rate", text="衰减")
        sub.prop(settings, "fade_tail", text="余墨")

        # 辉光是 GP 自带的效果器（深底 + 亮线 + 它 = 发光）
        box.prop(settings, "glow")
        column = box.column(align=True)
        column.enabled = settings.glow
        row = column.row(align=True)
        row.prop(settings, "glow_size", text="半径")
        row.prop(settings, "glow_opacity", text="强度")
        column.prop(settings, "glow_samples", text="采样")

        # --- 起伏 ---------------------------------------------------------
        box = layout.box()
        box.label(text="起伏", icon="MOD_WAVE")
        row = box.row(align=True)
        row.prop(settings, "z_wave_amp")
        sub = row.row(align=True)
        sub.enabled = settings.z_wave_amp > 0.0
        sub.prop(settings, "z_wave_freq", text="频率")
        if settings.z_wave_amp > 0.0:
            box.label(text="俯视看的是平面，想看起伏请转视角", icon="ORIENTATION_VIEW")

        # --- 画布 ---------------------------------------------------------
        box = layout.box()
        box.label(text="画布", icon="MESH_PLANE")
        column = box.column(align=True)
        column.prop(settings, "canvas")
        column.prop(settings, "margin")

        # --- 图层 ---------------------------------------------------------
        box = layout.box()
        box.label(text="图层", icon="LAYERGROUP_COLOR_04")
        box.prop(settings, "show_lines", text="弦 Chords")

        if settings.dirty and obj is not None:
            layout.label(text="参数已改，按当前帧重画即可", icon="INFO")

        # --- 渲染 ---------------------------------------------------------
        box = layout.box()
        box.label(text="渲染", icon="SCENE_DATA")
        box.prop(settings, "backdrop")
        box.operator("gen_tsubayaki.setup_scene", text="场景预设", icon="CAMERA_DATA")

        # --- 状态 ---------------------------------------------------------
        if settings.last_chords:
            box = layout.box()
            box.label(
                text=f"帧 {settings.last_frame} · n={settings.last_n:.2f} "
                f"t={settings.last_t:.3f}"
            )
            box.label(
                text=f"{settings.last_chords} 根弦 · 墨量 {settings.last_ink:.2f} · "
                f"{settings.last_ms:.2f} ms"
            )

        if settings.error:
            layout.label(text=settings.error, icon="ERROR")

        if obj is not None:
            layers = len(obj.data.layers)
            drawing = obj.data.layers[0].frames[0].drawing if layers else None
            strokes = len(drawing.strokes) if drawing is not None else 0
            box = layout.box()
            box.label(
                text=f"{layers} 图层 / {strokes} 笔划 / "
                f"{len(obj.data.attributes)} 属性",
                icon="OUTLINER_DATA_GREASEPENCIL",
            )
            box.label(text=f"深度排序 {obj.data.stroke_depth_order}")
