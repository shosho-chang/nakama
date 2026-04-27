"""seo_audit deterministic check modules（ADR-009 Phase 1.5 Slice D.1）。

Slice D.2 的 `seo-audit-post` skill import 各 entry function 後串成主流程；
本套件只做 deterministic 規則，LLM semantic check 放 `llm_review.py`（D.2）。
"""

from __future__ import annotations

from shared.seo_audit.headings import check_headings
from shared.seo_audit.html_fetcher import FetchResult, fetch_html, fetch_html_via_firecrawl
from shared.seo_audit.images import check_images
from shared.seo_audit.llm_review import LLMLevel
from shared.seo_audit.metadata import check_metadata
from shared.seo_audit.performance import check_performance
from shared.seo_audit.schema_markup import check_schema_markup
from shared.seo_audit.structure import check_structure
from shared.seo_audit.types import (
    AuditCheck,
    AuditResult,
    CheckCategory,
    CheckSeverity,
    CheckStatus,
)

__all__ = [
    "AuditCheck",
    "AuditResult",
    "CheckCategory",
    "CheckSeverity",
    "CheckStatus",
    "FetchResult",
    "LLMLevel",
    "check_headings",
    "check_images",
    "check_metadata",
    "check_performance",
    "check_schema_markup",
    "check_structure",
    "fetch_html",
    "fetch_html_via_firecrawl",
]
