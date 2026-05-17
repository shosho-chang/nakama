"""Outline drafter — evidence pool → 5-7 section outline (ADR-021 §3).

The drafter is a deep module: caller hands in the evidence pool, gets back
a list of :class:`shared.schemas.brook_synthesize.OutlineSection`. JSON
parsing, ref-validation, and prompt construction are private — caller never
sees the raw LLM string.

Validation rules (enforced *after* parsing, before returning):

1. Section count is within ``[OUTLINE_MIN_SECTIONS, OUTLINE_MAX_SECTIONS]``
   inclusive. The LLM is instructed for the same range; we hard-fail if it
   misbehaves rather than silently accepting a 3-section drift.
2. Every ``evidence_refs`` entry must exist in the supplied pool — the LLM
   sometimes invents plausible-looking slugs. Unknown refs raise loudly.
3. Each section cites at least :data:`OUTLINE_MIN_REFS_PER_SECTION` refs.

Tests inject a fake LLM via the ``ask_fn`` parameter; production callers
let the default :func:`shared.llm.ask` resolve through the router.
"""

from __future__ import annotations

import json
from typing import Callable

from shared.llm import ask as _default_ask
from shared.llm_context import set_current_agent
from shared.log import get_logger
from shared.prompt_loader import load_prompt
from shared.schemas.brook_synthesize import EvidencePoolItem, OutlineSection

from ._constants import (
    OUTLINE_MAX_SECTIONS,
    OUTLINE_MIN_REFS_PER_SECTION,
    OUTLINE_MIN_SECTIONS,
)

logger = get_logger("nakama.brook.synthesize.outline")


AskFn = Callable[..., str]


class OutlineDraftError(ValueError):
    """LLM returned an outline that violates the ADR-021 §3 contract."""


def _format_evidence_block(pool: list[EvidencePoolItem]) -> str:
    """Render the pool as a compact prompt-ready block.

    Each line is one source: ``- <slug>: <best heading> (rrf=<score>)``. The
    drafter does not need full chunk_text — it picks slugs to cite, not
    sentences to quote, so a one-line digest keeps the prompt tight.
    """
    lines: list[str] = []
    for item in pool:
        if not item.chunks:
            lines.append(f"- {item.slug}: (no chunks)")
            continue
        best = item.chunks[0]
        heading = best.get("heading") or best.get("page_title") or ""
        score = best.get("rrf_score", 0.0)
        lines.append(f"- {item.slug}: {heading} (rrf={score:.4f})")
    return "\n".join(lines)


def _format_trending_angles_block(trending_angles: list[str] | None) -> str:
    """Render the optional Zoro trending-angles section (ADR-027 §Decision 4).

    Returns an empty string when no angles are supplied so the rendered prompt
    is byte-identical to the pre-trending-angles baseline (no orphan header).
    When supplied, returns a leading-newline block (placed between the
    evidence pool and the task statement) carrying the rules baked into the
    ADR: may use as heading only when strong evidence correspondence exists;
    must NOT fabricate evidence_refs to fit an angle.
    """
    if not trending_angles:
        return ""
    lines = "\n".join(f"- {angle}" for angle in trending_angles)
    return (
        "\nZoro trending angles（可選參考；不可為配 angle 編造 evidence_refs）：\n\n"
        f"{lines}\n\n"
        "規則：\n"
        "- 若某 angle 與上方 evidence pool 有強對應，**可** 用為 section heading "
        "並在該段 `trending_match` 列出對應的 angle 字串\n"
        "- 若 angle 與 evidence pool 無對應，**忽略** — 不可為配 angle 編造 evidence_refs\n"
    )


def _strip_code_fence(text: str) -> str:
    """LLMs sometimes ignore the no-fence instruction. Tolerate it.

    Matches ```json ... ``` or ``` ... ``` and returns the inner block. If
    no fence is present, returns the input unchanged.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    # Drop opening fence (with optional language tag) and closing fence.
    first_newline = stripped.find("\n")
    if first_newline == -1:
        return stripped
    body = stripped[first_newline + 1 :]
    if body.endswith("```"):
        body = body[:-3]
    return body.strip()


def draft_outline(
    topic: str,
    keywords: list[str],
    pool: list[EvidencePoolItem],
    *,
    ask_fn: AskFn | None = None,
    trending_angles: list[str] | None = None,
) -> list[OutlineSection]:
    """Generate the outline draft from the evidence pool.

    Raises :class:`OutlineDraftError` when the LLM returns a malformed or
    contract-violating outline. Caller (the public ``synthesize`` entry
    point) lets this propagate so the failure is visible in the Sunny route
    instead of writing a half-baked store on disk.
    """
    if not pool:
        raise OutlineDraftError("cannot draft outline: empty evidence pool")
    if len(pool) < OUTLINE_MIN_REFS_PER_SECTION:
        raise OutlineDraftError(
            f"evidence pool too small ({len(pool)} sources) — "
            f"need at least {OUTLINE_MIN_REFS_PER_SECTION} to satisfy "
            f"min refs/section"
        )

    ask = ask_fn or _default_ask
    set_current_agent("brook")

    prompt = load_prompt(
        "brook",
        "synthesize_outline",
        topic=topic,
        keywords=", ".join(keywords) if keywords else "（無）",
        evidence_block=_format_evidence_block(pool),
        trending_angles_block=_format_trending_angles_block(trending_angles),
        min_sections=str(OUTLINE_MIN_SECTIONS),
        max_sections=str(OUTLINE_MAX_SECTIONS),
        min_refs=str(OUTLINE_MIN_REFS_PER_SECTION),
    )

    raw = ask(prompt, max_tokens=2048, temperature=0.3)
    body = _strip_code_fence(raw)

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise OutlineDraftError(f"outline LLM returned non-JSON: {exc}; raw={raw!r}") from exc

    if not isinstance(parsed, dict) or "sections" not in parsed:
        raise OutlineDraftError(f"outline JSON missing `sections` key: {parsed!r}")

    sections_raw = parsed["sections"]
    if not isinstance(sections_raw, list):
        raise OutlineDraftError(f"`sections` is not a list: {sections_raw!r}")

    if not (OUTLINE_MIN_SECTIONS <= len(sections_raw) <= OUTLINE_MAX_SECTIONS):
        raise OutlineDraftError(
            f"outline has {len(sections_raw)} sections; "
            f"expected {OUTLINE_MIN_SECTIONS}–{OUTLINE_MAX_SECTIONS}"
        )

    valid_slugs = {item.slug for item in pool}
    sections: list[OutlineSection] = []
    for idx, raw_section in enumerate(sections_raw, start=1):
        if not isinstance(raw_section, dict):
            raise OutlineDraftError(f"section #{idx} is not an object: {raw_section!r}")
        try:
            section = OutlineSection.model_validate(raw_section)
        except Exception as exc:  # pydantic ValidationError; widen for clarity
            raise OutlineDraftError(f"section #{idx} failed schema validation: {exc}") from exc
        if section.section != idx:
            raise OutlineDraftError(
                f"section ordering broken: position {idx} has section={section.section}"
            )
        if len(section.evidence_refs) < OUTLINE_MIN_REFS_PER_SECTION:
            raise OutlineDraftError(
                f"section #{idx} cites {len(section.evidence_refs)} refs; "
                f"minimum {OUTLINE_MIN_REFS_PER_SECTION}"
            )
        unknown = [ref for ref in section.evidence_refs if ref not in valid_slugs]
        if unknown:
            raise OutlineDraftError(f"section #{idx} cites unknown evidence slugs: {unknown}")
        sections.append(section)

    logger.info(
        "synthesize.outline drafted sections=%d total_refs=%d",
        len(sections),
        sum(len(s.evidence_refs) for s in sections),
    )
    return sections


__all__ = ["OutlineDraftError", "draft_outline"]
