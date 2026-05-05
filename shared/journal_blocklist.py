"""期刊黑名單：在 Robin PubMed digest pipeline 早期過濾掉指定 publisher 的期刊。

設計目的：修修觀察 PubMed RSS 池被 MDPI / 部分 Frontiers 期刊高頻發表淹沒，
頂刊（JAMA / Lancet / Nature 等）擠不進前 N。本模組提供 negative filter，
把已知不想看的期刊從 candidate pool 直接踢掉，LLM curate 不會看到。

正向擴充頂刊由 ``config/pubmed_feeds.yaml`` 的 eutils-type feed 處理（orthogonal）。

用法::

    from shared.journal_blocklist import is_blocked

    if is_blocked(candidate["journal"]):
        continue  # 丟棄
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

_ROOT = Path(__file__).resolve().parent.parent
_BLOCKLIST_PATH = _ROOT / "config" / "journal_blocklist.yaml"


def _normalize(name: str) -> str:
    """Journal name 正規化：小寫 + `&`→`and` + 只留 a-z0-9。

    與 ``shared.journal_metrics._normalize_title`` 一致，能吸收
    "Sensors (Basel, Switzerland)" vs "Sensors (Basel)" 等標點/補述差異。
    """
    s = name.lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]", "", s)


@lru_cache(maxsize=1)
def _load_blocked_keys(path: Optional[Path] = None) -> frozenset[str]:
    """載入 blocklist yaml，回傳 normalized key set（cached）。"""
    p = path or _BLOCKLIST_PATH
    if not p.exists():
        return frozenset()
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    raw = data.get("block") or []
    return frozenset(_normalize(name) for name in raw if isinstance(name, str) and name.strip())


def is_blocked(journal_name: str, *, blocklist_path: Optional[Path] = None) -> bool:
    """期刊名是否在黑名單。空字串回 False（無法判斷則不擋）。

    Args:
        journal_name: 期刊全名（PubMed RSS / esummary 的 source / fulljournalname 欄位）
        blocklist_path: 測試用，預設讀 ``config/journal_blocklist.yaml``
    """
    if not journal_name or not journal_name.strip():
        return False
    keys = _load_blocked_keys(blocklist_path)
    return _normalize(journal_name) in keys


def reload() -> None:
    """清 cache 重新載入 yaml（測試 / 動態 reload 用）。"""
    _load_blocked_keys.cache_clear()
