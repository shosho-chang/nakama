"""seo_enrich enrichment modules（ADR-009 Phase 1.5 Slice E）。

Slice E 的 `seo-keyword-enrich` skill import 各 entry function 後串成主流程；
本套件只做 enrichment 計算（striking-distance filter / cannibalization detection /
SERP summarization），不負責 fetch GSC / firecrawl 原始資料。
"""

from __future__ import annotations

from shared.seo_enrich.cannibalization import (
    detect_cannibalization,
    load_cannibalization_thresholds,
)
from shared.seo_enrich.serp_summarizer import summarize_serp
from shared.seo_enrich.striking_distance import filter_striking_distance

__all__ = [
    "detect_cannibalization",
    "filter_striking_distance",
    "load_cannibalization_thresholds",
    "summarize_serp",
]
