"""Compose-time disclaimer detection (positive signal).

Separated from `medical_claim_vocab` because the two have inverted polarity:
- `medical_claim_vocab` is a **regulatory blacklist** — hits = bad
- `disclaimer` is a **writer-positive signal** — hits = good

Both feed into the compose-time `DraftComplianceV1` snapshot for HITL UI;
keeping them distinct keeps caller intent obvious at the call site.
"""

from __future__ import annotations

import re

DISCLAIMER_PATTERNS: tuple[str, ...] = (
    r"非\s*醫療\s*建議",
    r"僅供\s*參考",
    r"請\s*(?:諮詢|徵詢|洽詢)\s*(?:醫師|專業)",
    r"不\s*構成\s*(?:醫療|診斷)",
    r"This is not medical advice",
)


def has_disclaimer(text: str) -> bool:
    """Return True if any disclaimer-shape phrase is present in text."""
    for pattern in DISCLAIMER_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False
