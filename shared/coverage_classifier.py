"""Coverage manifest + acceptance gate for ADR-020 S4.

Per-chapter coverage manifest tracks claim extraction, figure/table counts,
concept dispatch log, and verbatim paragraph match rate.  The acceptance gate
enforces hard fail conditions before a chapter page is committed to the vault.

Fail conditions (gate-blocking):
  primary_claims_missing_pct > 5%
  figures_count != figures_embedded
  tables_transcluded > 0
  any concept_dispatch_log entry with action "phase-b-style-stub"

Warn-only (logged, not gate-blocking):
  secondary_claims_missing_pct > 25%

Not gated:
  nuance_claims_missing_pct (any value)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from shared.log import get_logger

logger = get_logger("nakama.shared.coverage_classifier")

_EXTRACT_CLAIMS_PROMPT = """\
You are a textbook claim extractor. Read the following chapter text and extract all factual claims.
Classify each claim as:
- primary: core scientific facts/mechanisms, the main teaching points
- secondary: supporting details, examples, quantitative values that support primary claims
- nuance: edge cases, exceptions, caveats, context-dependent statements

OUTPUT FORMAT — STRICT:
- Return EXACTLY one JSON array and nothing else.
- Do NOT wrap the JSON in markdown code fences (no ```json, no ```).
- Do NOT add prose, headings, commentary, or explanation before or after the JSON.
- Do NOT add a trailing comment.
- The first character of your response MUST be `[` and the last MUST be `]`.

Each item must have this exact shape:
{{"type": "primary"|"secondary"|"nuance", "text": "<claim text>"}}

Aim for completeness: extract every distinct factual claim in the chapter.
A typical textbook chapter yields 20-60 claims; do not stop after 5.

CHAPTER TEXT:
{chapter_text}
"""

_CHECK_CLAIM_PROMPT = """\
Does the following vault page cover this claim?

CLAIM: {claim_text}

VAULT PAGE:
{page_text}

Respond with exactly one word: true or false
"""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ClaimUnit:
    """A single factual claim extracted from a chapter."""

    text: str
    claim_type: Literal["primary", "secondary", "nuance"]
    found_in_page: bool = False


@dataclass
class ConceptDispatchEntry:
    """One concept dispatch action recorded during Phase 2 inline dispatch."""

    slug: str
    action: str


@dataclass
class CoverageManifest:
    """Per-chapter coverage manifest for ADR-020 acceptance gate."""

    chapter_index: int
    book_id: str
    claims_extracted_by_llm: list[ClaimUnit]
    figures_count: int
    figures_embedded: int
    tables_transcluded: int
    verbatim_paragraph_match_pct: float
    concept_dispatch_log: list[ConceptDispatchEntry]
    acceptance_status: str = "pending"
    acceptance_reasons: list[str] = field(default_factory=list)

    @property
    def primary_claims_missing_pct(self) -> float:
        primary = [c for c in self.claims_extracted_by_llm if c.claim_type == "primary"]
        if not primary:
            return 0.0
        missing = sum(1 for c in primary if not c.found_in_page)
        return missing / len(primary) * 100.0

    @property
    def secondary_claims_missing_pct(self) -> float:
        secondary = [c for c in self.claims_extracted_by_llm if c.claim_type == "secondary"]
        if not secondary:
            return 0.0
        missing = sum(1 for c in secondary if not c.found_in_page)
        return missing / len(secondary) * 100.0

    @property
    def nuance_claims_missing_pct(self) -> float:
        nuance = [c for c in self.claims_extracted_by_llm if c.claim_type == "nuance"]
        if not nuance:
            return 0.0
        missing = sum(1 for c in nuance if not c.found_in_page)
        return missing / len(nuance) * 100.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_acceptance_gate(manifest: CoverageManifest) -> tuple[bool, list[str]]:
    """Check all hard fail conditions.  Returns (passed, fail_reasons).

    Warn-only conditions (secondary > 25%) are logged but do not fail the gate.
    Nuance miss rate is not gated at all.
    """
    reasons: list[str] = []

    if manifest.primary_claims_missing_pct > 5.0:
        reasons.append(
            f"primary_claims_missing_pct={manifest.primary_claims_missing_pct:.1f}% > 5%"
        )

    if manifest.figures_count != manifest.figures_embedded:
        reasons.append(
            f"figures_count={manifest.figures_count}"
            f" != figures_embedded={manifest.figures_embedded}"
        )

    if manifest.tables_transcluded > 0:
        reasons.append(
            f"tables_transcluded={manifest.tables_transcluded} > 0 (inline only per ADR-020)"
        )

    stub_slugs = [e.slug for e in manifest.concept_dispatch_log if e.action == "phase-b-style-stub"]
    if stub_slugs:
        reasons.append(f"phase-b-style-stub actions found for slugs: {stub_slugs}")

    if manifest.secondary_claims_missing_pct > 25.0:
        logger.warning(
            "ch%d %s: secondary_claims_missing_pct=%.1f%% > 25%% (warn-only)",
            manifest.chapter_index,
            manifest.book_id,
            manifest.secondary_claims_missing_pct,
        )

    return (len(reasons) == 0), reasons


def _parse_json_array_tolerant(response: str) -> list | None:
    """Best-effort parse of an LLM response into a JSON array.

    Handles three common deviations from strict JSON:
      1. Markdown fences: ```json\n[...]\n``` or ```\n[...]\n```
      2. Leading/trailing prose around the array
      3. Whitespace padding

    Returns the parsed list, or None if no parse succeeds.
    """
    if not response:
        return None

    s = response.strip()

    # Strip markdown code fences if present (```json ... ``` or ``` ... ```)
    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1 :]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3].rstrip()

    s = s.strip()

    # Direct attempt
    try:
        parsed = json.loads(s)
        return parsed if isinstance(parsed, list) else None
    except json.JSONDecodeError:
        pass

    # Fallback: extract the outermost [...] substring
    start = s.find("[")
    end = s.rfind("]")
    if start != -1 and end != -1 and end > start:
        candidate = s[start : end + 1]
        try:
            parsed = json.loads(candidate)
            return parsed if isinstance(parsed, list) else None
        except json.JSONDecodeError:
            return None

    return None


def extract_claims(
    chapter_text: str,
    *,
    _ask_llm: Callable[[str], str] | None = None,
) -> list[ClaimUnit]:
    """LLM-extract and classify claim units from raw chapter text.

    Returns an empty list if the LLM response is not valid JSON.
    """
    if _ask_llm is None:
        from shared.kb_writer import _ask_llm as _default

        _ask_llm = _default

    prompt = _EXTRACT_CLAIMS_PROMPT.format(chapter_text=chapter_text[:8000])
    response = _ask_llm(prompt)

    raw = _parse_json_array_tolerant(response)
    if raw is None:
        snippet = (response or "")[:500].replace("\n", "\\n")
        logger.warning(
            "extract_claims: LLM returned non-JSON response; returning []. raw[:500]=%r",
            snippet,
        )
        return []

    if not isinstance(raw, list):
        return []

    claims: list[ClaimUnit] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        claim_type = item.get("type", "")
        text = item.get("text", "")
        if claim_type in ("primary", "secondary", "nuance") and text:
            claims.append(ClaimUnit(text=text, claim_type=claim_type))

    return claims


def check_claim_in_page(
    claim: ClaimUnit,
    vault_page_text: str,
    *,
    _ask_llm: Callable[[str], str] | None = None,
) -> bool:
    """Ask LLM whether the vault page covers the given claim.

    Defaults to False on unrecognised LLM response.
    """
    if _ask_llm is None:
        from shared.kb_writer import _ask_llm as _default

        _ask_llm = _default

    prompt = _CHECK_CLAIM_PROMPT.format(
        claim_text=claim.text,
        page_text=vault_page_text[:6000],
    )
    response = _ask_llm(prompt)
    lower = response.strip().lower()
    if "true" in lower:
        return True
    if "false" in lower:
        return False
    logger.warning(
        "check_claim_in_page: unexpected response '%s'; defaulting to False",
        response[:60],
    )
    return False


def write_coverage_manifest(manifest: CoverageManifest, path: Path) -> None:
    """Serialize the coverage manifest to JSON at the given path."""
    data = {
        "chapter_index": manifest.chapter_index,
        "book_id": manifest.book_id,
        "acceptance_status": manifest.acceptance_status,
        "acceptance_reasons": manifest.acceptance_reasons,
        "primary_claims_missing_pct": round(manifest.primary_claims_missing_pct, 2),
        "secondary_claims_missing_pct": round(manifest.secondary_claims_missing_pct, 2),
        "nuance_claims_missing_pct": round(manifest.nuance_claims_missing_pct, 2),
        "figures_count": manifest.figures_count,
        "figures_embedded": manifest.figures_embedded,
        "tables_transcluded": manifest.tables_transcluded,
        "verbatim_paragraph_match_pct": round(manifest.verbatim_paragraph_match_pct, 2),
        "claims_extracted_by_llm": [
            {
                "text": c.text,
                "claim_type": c.claim_type,
                "found_in_page": c.found_in_page,
            }
            for c in manifest.claims_extracted_by_llm
        ],
        "concept_dispatch_log": [
            {"slug": e.slug, "action": e.action} for e in manifest.concept_dispatch_log
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
