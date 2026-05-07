"""Frozen defaults for Brook synthesize (ADR-021 §3 Freeze 2026-05-07).

These constants are baked from the #457 mini-bench HITL freeze documented in
`docs/decisions/ADR-021-annotation-substance-store-and-brook-synthesize.md`
§3 — bench results in `docs/research/2026-05-07-brook-synthesize-bench.md`.

ADR-021 §3 explicitly names hybrid + K=15 as the chosen point on the recall /
precision frontier (recall 1.00, precision 0.76 over 5 topics × 13 pubmed
sources). Re-bench after >10 real Project synthesize runs per the same §3
"long-term plan" caveat — until then, treat these as the contract.
"""

from __future__ import annotations

from typing import Final

# ── Frozen defaults (ADR-021 §3) ─────────────────────────────────────────────

#: top_k passed to `shared.kb_hybrid_search.search` per query lane.
BROOK_SYNTHESIZE_TOP_K: Final[int] = 15

#: Retrieval engine. Currently the only supported value; named so future ADRs
#: (e.g. ADR-022 multilingual embeddings) can flip the default in one place.
BROOK_SYNTHESIZE_ENGINE: Final[str] = "hybrid"

#: Whether to fan out into multiple language-specific queries. ADR-021 §3
#: marks this as a *transition* — once ADR-022 (multilingual embeddings) ships
#: Brook should fall back to single-query (tracked in #452).
MULTI_QUERY: Final[bool] = True

#: Outline drafter target — ADR-021 §3 says "5-7 段". Lower bound is enforced
#: in the prompt; upper bound is a soft target (LLM may exceed by 1).
OUTLINE_MIN_SECTIONS: Final[int] = 5
OUTLINE_MAX_SECTIONS: Final[int] = 7

#: Minimum evidence_refs each section must cite. ADR-021 §3 phrases this as
#: "每段引用 N 條 evidence" — N=2 is the operational minimum (one ref alone
#: is too thin to call "synthesized").
OUTLINE_MIN_REFS_PER_SECTION: Final[int] = 2


__all__ = [
    "BROOK_SYNTHESIZE_ENGINE",
    "BROOK_SYNTHESIZE_TOP_K",
    "MULTI_QUERY",
    "OUTLINE_MAX_SECTIONS",
    "OUTLINE_MIN_REFS_PER_SECTION",
    "OUTLINE_MIN_SECTIONS",
]
