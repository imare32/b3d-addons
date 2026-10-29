"""图像后端：一个双摆 = **一个像素**，只维护一张图像数据块（不建对象、不建材质）。

思路同 ``.lab/taichi_image.py``：算出 ``N×N`` 的 RGBA，用 ``image.pixels.foreach_set``
一次写进去（纯 memcpy）。**不**再挂一块面片去调材质 —— 想看效果直接在图像编辑器里看
（面板上有「在图像编辑器里查看」，或者按 Shift+F10 拖一个 Image Editor 出来）。

约定：

* 图像尺寸就是 ``N×N``（一像素一个摆），行序从下往上（j 轴 = 图像 y）；
* ``float_buffer=True``：颜色是**线性**的，浮点图原样就是着色/预览用的值，不做 sRGB 往返；
* 后端接口里的 "obj" 在这里就是那个 **Image 数据块**（``find_object`` / ``ensure_object``
  返回它），所以 ``sim`` 那边不用为它单开一套分支。
"""

from __future__ import annotations

import bpy
import numpy as np

__all__ = [
    "IMAGE_NAME",
    "OBJECT_NAME",
    "ensure_object",
    "find_object",
    "refresh",
    "remove_object",
    "show_in_editors",
    "write",
]

#: 图像名（也是后端接口里的"对象名"）。
IMAGE_NAME = "Sim Pendulum Pixels"
OBJECT_NAME = IMAGE_NAME

#: 占位尺寸：``ensure_object`` 拿不到 N，先建一张小的，``write`` 会按实际边长缩放。
_PLACEHOLDER = 4


def find_object():
    """返回结果图像；不存在时返回 ``None``。"""
    return bpy.data.images.get(IMAGE_NAME)


def remove_object() -> bool:
    """删掉结果图像。返回是否真的删掉了。"""
    image = bpy.data.images.get(IMAGE_NAME)
    if image is None:
        return False
    bpy.data.images.remove(image)
    return True


def _resize(image, side: int):
    if image.size[0] != side or image.size[1] != side:
        image.scale(side, side)
    image.alpha_mode = "STRAIGHT"
    return image


def _image(side: int = _PLACEHOLDER):
    image = bpy.data.images.get(IMAGE_NAME)
    if image is None:
        image = bpy.data.images.new(
            IMAGE_NAME, width=side, height=side, alpha=True, float_buffer=True
        )
    return _resize(image, side)


def ensure_object(scene=None):
    """拿（必要时建）结果图像。``scene`` 只是为了和后端接口对齐，这里用不到。"""
    return _image()


def _pixels(strokes) -> np.ndarray:
    """笔划缓冲 → ``(N, N, 4)`` 图像数据（行序为 j = 图像 y，从下往上）。"""
    count = int(strokes.pendulums)
    side = round(np.sqrt(count))
    if side * side != count:
        raise ValueError("点阵画法的摆数必须是完全平方（N×N）")
    rgba = np.ascontiguousarray(strokes.colours).reshape(side, side, 4)
    return np.ascontiguousarray(rgba.transpose(1, 0, 2))


def write(image, strokes, frame=None) -> int:
    """按 ``strokes`` 定尺寸并写满整张图。返回像素数。"""
    data = _pixels(strokes)
    side = data.shape[0]
    image = _image(side)
    image.pixels.foreach_set(data.reshape(-1))
    image.update()
    return side * side


def refresh(image, strokes) -> bool:
    """逐帧只换像素。尺寸变了返回 ``False``（调用方走 :func:`write`）。"""
    try:
        data = _pixels(strokes)
    except ValueError:
        return False
    if image.size[0] != data.shape[0] or image.size[1] != data.shape[1]:
        return False
    image.pixels.foreach_set(data.reshape(-1))
    image.update()
    return True


def show_in_editors(context, image=None) -> int:
    """把结果图塞进所有打开的 Image Editor，返回切换了多少个区域。"""
    image = image or find_object()
    if image is None:
        return 0
    screen = getattr(context, "screen", None)
    if screen is None:
        return 0
    switched = 0
    for area in screen.areas:
        for space in area.spaces:
            if space.type != "IMAGE_EDITOR":
                continue
            space.image = image
            # 顺手「钉住」，免得别的操作把这张图换掉；某些版本/上下文的 Space 上没有这个
            # 属性（`bpy.types` 上能查到、实例上不一定给），所以兜一层。
            try:
                space.use_image_pin = True
            except (AttributeError, TypeError):
                pass
            switched += 1
    return switched
