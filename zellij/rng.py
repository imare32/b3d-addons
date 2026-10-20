"""确定性随机源 mulberry32（与 JS 侧逐位一致）。

移植版需要和浏览器里的草图生成**同一份设计**，所以不能用 ``random.Random``，
而是用 mulberry32 这种纯 32 位整数算法：JS 侧把 ``myrand`` 换成同参数的
``mulberry32(seed)`` 后，两边每一步取到的随机数完全相同。

JS 参考实现::

    function mulberry32(a) {
      return function () {
        a |= 0; a = (a + 0x6D2B79F5) | 0;
        let t = Math.imul(a ^ (a >>> 15), 1 | a);
        t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
      };
    }
"""

from __future__ import annotations

__all__ = ["MASK32", "Mulberry32"]

MASK32 = 0xFFFFFFFF
_INCREMENT = 0x6D2B79F5


class Mulberry32:
    """32 位状态、``[0, 1)`` 输出的确定性随机源（只需 ``random()``）。"""

    __slots__ = ("state",)

    def __init__(self, seed: int):
        self.state = seed & MASK32

    @classmethod
    def from_state(cls, state: int) -> "Mulberry32":
        rng = cls(0)
        rng.state = state & MASK32
        return rng

    def clone(self) -> "Mulberry32":
        return Mulberry32.from_state(self.state)

    def random(self) -> float:
        self.state = (self.state + _INCREMENT) & MASK32
        t = self.state
        t = ((t ^ (t >> 15)) * (t | 1)) & MASK32
        t = (t ^ ((t + (((t ^ (t >> 7)) * (t | 61)) & MASK32)) & MASK32)) & MASK32
        t = (t ^ (t >> 14)) & MASK32
        return t / 4294967296.0
