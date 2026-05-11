"""Concept Maturity Model classifier for ADR-020 S3.

4 quantifiable high-value rules (all deterministic — no LLM):
  1. section_heading   — term appears in a H1-H4 heading line
  2. bolded_define     — term is **bolded** or *italicised* in the text
  3. freq_multi_section — term appears ≥3 times across ≥2 H-delimited sections
  4. definition_phrase  — term followed by a definitional construct

Routing (route_concept):
  L1 alias  — no rule triggered → append to _alias_map.md, no concept page
  L2 stub   — ≥1 rule triggered, single source → concept page, status: stub
  L3 active — ≥1 rule triggered, ≥2 sources → full 4-action dispatcher (S2)

False-Consensus guard (detect_scope_conflict):
  Delegates to an LLM to compare two bodies and classify as same_facet /
  different_facet / different_concept.  In tests, pass _ask_llm=<stub>.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Literal

from shared.log import get_logger

logger = get_logger("nakama.shared.concept_classifier")

_VALID_SCOPE_LABELS = ("same_facet", "different_facet", "different_concept")

_SCOPE_CONFLICT_PROMPT = """\
You are a knowledge base editor. Compare two descriptions of the same concept slug.

EXISTING BODY:
{existing}

NEW EXTRACT:
{new}

Are these:
- same_facet: different angles on the same concept (mergeable)
- different_facet: distinct but complementary sub-aspects (add separate section)
- different_concept: same term, genuinely different concepts (split + disambiguate)

Respond with exactly one of: same_facet, different_facet, different_concept
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_high_value(term: str, context: str) -> tuple[bool, list[str]]:
    """Apply the 4 high-value rules and return (is_high_value, signals).

    ``signals`` lists every triggered rule name; empty list means L1 alias.
    """
    signals: list[str] = []
    if _rule_section_heading(term, context):
        signals.append("section_heading")
    if _rule_bolded_define(term, context):
        signals.append("bolded_define")
    if _rule_freq_multi_section(term, context):
        signals.append("freq_multi_section")
    if _rule_definition_phrase(term, context):
        signals.append("definition_phrase")
    return bool(signals), signals


def route_concept(
    term: str,
    context: str,
    *,
    source_count: int = 1,
) -> tuple[Literal["L1", "L2", "L3"], list[str]]:
    """Route a concept term to L1 (alias) / L2 (stub) / L3 (active).

    Args:
        term:         The concept term to classify.
        context:      Chapter text (or excerpt) containing the term.
        source_count: Number of distinct sources that mention this concept.
                      source_count ≥ 2 enables L3 promotion.

    Returns:
        (level, signals) — signals is empty for L1.
    """
    is_high_value, signals = classify_high_value(term, context)
    if not is_high_value:
        return "L1", []
    if source_count >= 2:
        return "L3", signals
    return "L2", signals


def detect_scope_conflict(
    existing_body: str,
    new_extract: str,
    *,
    _ask_llm: Callable[[str], str] | None = None,
) -> Literal["same_facet", "different_facet", "different_concept"]:
    """False-Consensus guard: compare two bodies of text for scope conflicts.

    Returns one of: ``same_facet`` / ``different_facet`` / ``different_concept``.
    Defaults to ``same_facet`` if the LLM response is unrecognised.
    """
    if _ask_llm is None:
        from shared.kb_writer import _ask_llm as _default

        _ask_llm = _default

    prompt = _SCOPE_CONFLICT_PROMPT.format(
        existing=existing_body[:2000],
        new=new_extract[:2000],
    )
    response = _ask_llm(prompt)
    for label in _VALID_SCOPE_LABELS:
        if label in response:
            return label  # type: ignore[return-value]
    logger.warning(
        "detect_scope_conflict: unexpected LLM response '%s'; defaulting to same_facet",
        response[:80],
    )
    return "same_facet"


def append_alias_entry(term: str, source_link: str, vault_path: Path) -> None:
    """Append an L1 alias entry to ``KB/Wiki/_alias_map.md``.

    Format: ``term | source_link``.  Idempotent — duplicate (term, source_link)
    pairs are silently skipped.  Different source links for the same term are
    each recorded as separate rows.
    """
    alias_file = vault_path / "KB" / "Wiki" / "_alias_map.md"
    entry = f"{term} | {source_link}"

    if alias_file.exists():
        content = alias_file.read_text(encoding="utf-8")
        if entry in content:
            return
        alias_file.write_text(content.rstrip("\n") + f"\n{entry}\n", encoding="utf-8")
    else:
        alias_file.write_text(
            "# L1 Alias Map\n\nterm | source\n--- | ---\n" + entry + "\n",
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# Internal rule implementations
# ---------------------------------------------------------------------------


def _rule_section_heading(term: str, context: str) -> bool:
    pattern = re.compile(
        r"^#{1,4}\s+.*" + re.escape(term) + r".*$",
        re.IGNORECASE | re.MULTILINE,
    )
    return bool(pattern.search(context))


def _rule_bolded_define(term: str, context: str) -> bool:
    esc = re.escape(term)
    patterns = [
        re.compile(r"\*\*" + esc + r"\*\*", re.IGNORECASE),
        re.compile(r"(?<!\*)\*" + esc + r"\*(?!\*)", re.IGNORECASE),
    ]
    return any(p.search(context) for p in patterns)


def _rule_freq_multi_section(term: str, context: str) -> bool:
    sections = re.split(r"(?m)^#{1,4}\s+.+$", context)
    sections = [s for s in sections if s.strip()]

    esc = re.escape(term)
    pattern = re.compile(r"\b" + esc + r"\b", re.IGNORECASE)

    sections_with_term = 0
    total_count = 0
    for section in sections:
        count = len(pattern.findall(section))
        if count > 0:
            sections_with_term += 1
            total_count += count

    return total_count >= 3 and sections_with_term >= 2


def _rule_definition_phrase(term: str, context: str) -> bool:
    esc = re.escape(term)
    patterns = [
        re.compile(esc + r"\s+is\s+defined\s+as", re.IGNORECASE),
        re.compile(esc + r"\s+is\s+referred\s+to\s+as", re.IGNORECASE),
        re.compile(esc + r"\s+refers?\s+to\b", re.IGNORECASE),
        re.compile(esc + r"[,，]?\s*defined\s+as", re.IGNORECASE),
        re.compile(esc + r"\s+稱為", re.IGNORECASE),
        re.compile(esc + r"\s+定義為", re.IGNORECASE),
    ]
    return any(p.search(context) for p in patterns)
