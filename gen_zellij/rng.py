"""确定性随机源 mulberry32。

32 位状态、``[0, 1)`` 输出的伪随机数发生器。用它而不是 ``random.Random``：

* 纯 32 位整数运算，同一种子在**任何**平台、任何 Python 版本上都得到完全相同的
  序列 —— 作品可以只靠一个种子复现；
* 状态就是一个整数，随时能存下来接着走：设计阶段结束时把 ``state`` 交给
  :class:`~.design.Design`，场景阶段从那里继续，整张图只有一条随机流；
* 换成别的语言重实现（导出工具里的简化版），照着这几行位运算抄即可。

:func:`draw_seed` 只给宿主（面板）用：抽种子是"随机"唯一允许发生的地方。
"""

from __future__ import annotations

import os

__all__ = ["MASK32", "SEED_MAX", "Mulberry32", "draw_seed"]

MASK32 = 0xFFFFFFFF

#: 对外暴露的种子上限：Blender 的 ``IntProperty`` 是**有符号** 32 位，超过
#: ``2**31 - 1`` 直接赋值会抛 ``ValueError: value not in 'int' range``。
#: mulberry32 内部本来就会 ``& MASK32``，所以只用正的一半不影响算法。
SEED_MAX = 0x7FFFFFFF

_INCREMENT = 0x6D2B79F5


class Mulberry32:
    """32 位状态、``[0, 1)`` 输出的确定性随机源（只需 ``random()``）。"""

    __slots__ = ("state",)

    def __init__(self, seed: int):
        self.state = seed & MASK32

    @classmethod
    def from_state(cls, state: int) -> Mulberry32:
        rng = cls(0)
        rng.state = state & MASK32
        return rng

    def clone(self) -> Mulberry32:
        return Mulberry32.from_state(self.state)

    def random(self) -> float:
        self.state = (self.state + _INCREMENT) & MASK32
        t = self.state
        t = ((t ^ (t >> 15)) * (t | 1)) & MASK32
        t = (t ^ ((t + (((t ^ (t >> 7)) * (t | 61)) & MASK32)) & MASK32)) & MASK32
        t = (t ^ (t >> 14)) & MASK32
        return t / 4294967296.0


def draw_seed() -> int:
    """抽一个新种子（``0..SEED_MAX``，即 31 位，见 :data:`SEED_MAX`）。

    抽到的值必须显示出来、可回填，否则这张图无法复现。
    """
    return int.from_bytes(os.urandom(4), "little") & SEED_MAX
