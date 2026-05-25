"""LLM creative planner — narrative → beats with layout + B-roll proposals (ADR-032 §6).

Loads:
- docs/design-system.md (brand context)
- agents/foundry/STYLE.md (editorial rubric)
- agents/foundry/guardrails.yaml (allow/deny lists)
- agents/foundry/examples/ (cold-start: NOT loaded in Phase 1; gate `len(examples) >= 5`)

LLM contract: every beat must include start_quote / end_quote that are
exact substring copies of the supplied normalized transcript. Paraphrasing
or rewriting characters is forbidden — beat_aligner will hard-fail on miss.

PR-3 implementation.
"""

from __future__ import annotations


def plan_episode(flat_text, episode_meta):  # pragma: no cover — PR-3
    raise NotImplementedError("PR-3 — see issue #714")
