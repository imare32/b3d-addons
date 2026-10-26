"""图案库访问层。

``fillers.json`` 是「签名 → 图案簇列表」的数据表：

* 签名形如 ``"LCLCLCLC"``，一位对应瓷砖的一个顶点（见 ``design.get_signature``）；
* 每簇含 ``bounds``（``(x0, y0, x1, y1)`` 的包围盒对角线）与 ``shapes``
  （``colour`` 色板索引 + ``path`` 坐标平铺数组）。

按签名查询，命中后由 ``design.match_filler`` 决定摆正到瓷砖的哪条边上。
"""

from __future__ import annotations

import json
from pathlib import Path

__all__ = ["load", "has", "clusters", "signatures"]

_CACHE: dict | None = None


def load() -> dict:
    """惰性加载 ``fillers.json``（约 150 KB，只解析一次）。"""
    global _CACHE
    if _CACHE is None:
        path = Path(__file__).with_name("fillers.json")
        with path.open("r", encoding="utf-8") as fh:
            _CACHE = json.load(fh)
    return _CACHE


def has(signature: str) -> bool:
    return signature in load()


def clusters(signature: str) -> list[dict]:
    return load().get(signature, [])


def signatures() -> list[str]:
    return list(load())
