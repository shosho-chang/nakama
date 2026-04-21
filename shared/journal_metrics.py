"""期刊 tier 查詢：以 journal name 或 ISSN 映射到 Scimago SJR / quartile。

資料源：data/scimago_journals.csv（由 scripts/update_scimago.py 從年度 Scimago CSV
萃取而來）。載入一次到記憶體，之後 lookup O(1)。

用法：
    from shared.journal_metrics import lookup

    info = lookup("Cell Death and Disease")
    # → {"title": "Cell Death and Disease", "issn": ["20414889"],
    #    "sjr": 3.291, "quartile": "Q1", "h_index": 202, ...}

    info = lookup(issn="20414889")  # 也可用 ISSN 查

    if info is None:
        # 未命中 — let the LLM judge from journal name alone
        ...

Journal name 正規化：抄 n8n Code 節點的策略（小寫 + 只留 a-z0-9），
能吸收 "&" / "and" / "-" / ":" 等標點差異。ISSN 去掉連字號。
"""

from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
_CSV_PATH = _ROOT / "data" / "scimago_journals.csv"

# 模組級 cache（一次性載入）
_by_title: dict[str, dict] = {}
_by_issn: dict[str, dict] = {}
_loaded = False


def _normalize_title(title: str) -> str:
    """Journal name 正規化 key：小寫 + `&`→`and` + 只留 a-z0-9。

    `&` / `and` 要 explicitly 統一，不能只靠 strip 標點 — 否則
    "Gut & Liver" vs "Gut and Liver" 會變成 "gutliver" vs "gutandliver"。
    """
    s = title.lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]", "", s)


def _load() -> None:
    global _loaded
    if _loaded:
        return
    if not _CSV_PATH.exists():
        # 還沒跑過 ETL — 不 raise，讓 caller 的 lookup() 拿 None 自行 degrade
        _loaded = True
        return

    with open(_CSV_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            issns = [s for s in row["issn"].split("|") if s]
            entry = {
                "title": row["title"],
                "issn": issns,
                "sjr": float(row["sjr"]) if row["sjr"] else None,
                "quartile": row["quartile"] or None,
                "h_index": int(row["h_index"]) if row["h_index"] else None,
                "country": row["country"] or None,
                "categories": row["categories"] or None,
            }
            key = _normalize_title(row["title"])
            # 若同 key 多筆（罕見），保留 SJR 高的那筆
            existing = _by_title.get(key)
            if existing is None or (entry["sjr"] or 0) > (existing["sjr"] or 0):
                _by_title[key] = entry
            # 額外索引去掉 leading "the" 的變體（"The Lancet" ↔ "Lancet"）
            if key.startswith("the"):
                alt = key[3:]
                if alt and alt not in _by_title:
                    _by_title[alt] = entry
            for issn in issns:
                _by_issn[issn] = entry
    _loaded = True


def lookup(journal_name: Optional[str] = None, issn: Optional[str] = None) -> Optional[dict]:
    """依 journal name 或 ISSN 查 Scimago 指標。

    兩者擇一（ISSN 優先，因為更精準）。回傳 dict 或 None（未命中）。
    """
    _load()
    if issn:
        normalized_issn = issn.replace("-", "").strip()
        hit = _by_issn.get(normalized_issn)
        if hit:
            return hit
    if journal_name:
        return _by_title.get(_normalize_title(journal_name))
    return None


@lru_cache(maxsize=1)
def total_journals() -> int:
    """Debug 用：載入了幾筆。"""
    _load()
    return len(_by_title)
