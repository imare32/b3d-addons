"""图案库访问层（``fillers.js`` 的数据部分）。

``fillers.json`` 是 ``fillers.js`` 里的 ``fillers`` 对象直接转成的 JSON：
签名（如 ``"LCLCLCLC"``）→ 图案簇列表，每簇含 ``bounds``（两点的 x/y 平铺）
与 ``shapes``（``colour`` 索引 + ``path`` 坐标平铺数组）。
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
