"""Extract the 5 mandatory frontmatter keys from a vault page or issue body.

ADR-023 §6 — every proposal must declare 5 keys before it can enter the
`proposal_metrics` table:

    proposal_id      — slug, unique within state.db
    metric_type      — one of quantitative | checklist | human_judged
    success_metric   — free-text definition of "did this work?"
    related_adr      — JSON / YAML list of ADR ids (may be empty)
    related_issues   — JSON / YAML list of GH issue refs (may be empty)

Two input shapes are supported:

    1. Vault page (markdown with leading `---` YAML block, Stage 1 output of
       `agents/franky/news_synthesis.py`).
    2. GitHub issue body (markdown with a fenced ```yaml frontmatter:``` or
       a leading `---` YAML block — the same parser handles both).

Missing-key behaviour: raises `MissingFrontmatterKeyError` listing every
required key that was not found. Partial frontmatter is rejected as a whole
— S3 must produce all 5 keys or fail the candidate.

Public API:
    extract(text: str) -> ProposalFrontmatterV1
    extract_from_path(path: Path) -> ProposalFrontmatterV1
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from shared.schemas.proposal_metrics import (
    REQUIRED_FRONTMATTER_KEYS,
    ProposalFrontmatterV1,
)


class FrontmatterExtractError(ValueError):
    """Base class for extractor failures."""


class NoFrontmatterFoundError(FrontmatterExtractError):
    """Input text had no recognisable YAML frontmatter block."""


class FrontmatterParseError(FrontmatterExtractError):
    """The YAML block was located but could not be parsed (malformed YAML)."""


class MissingFrontmatterKeyError(FrontmatterExtractError):
    """The frontmatter block is missing one or more of the 5 required keys."""

    def __init__(self, missing: list[str]):
        self.missing = sorted(missing)
        super().__init__(
            "proposal frontmatter missing required keys: " + ", ".join(self.missing)
        )


# ---------------------------------------------------------------------------
# Internal: locate + parse the YAML block
# ---------------------------------------------------------------------------

# Leading `---\n...\n---` block (vault pages, Jekyll-style).
_LEADING_BLOCK_RE = re.compile(
    r"\A\s*---\s*\n(?P<body>.*?)\n---\s*(?:\n|$)",
    re.DOTALL,
)

# Fenced ```yaml frontmatter\n...\n``` block (used inside GH issue bodies
# where `---` would render as an `<hr>` and confuse the issue template).
_FENCED_BLOCK_RE = re.compile(
    r"```ya?ml\s+frontmatter\s*\n(?P<body>.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)


def _find_yaml_block(text: str) -> str:
    m = _LEADING_BLOCK_RE.match(text)
    if m:
        return m.group("body")
    m = _FENCED_BLOCK_RE.search(text)
    if m:
        return m.group("body")
    raise NoFrontmatterFoundError(
        "no leading `---` block or ```yaml frontmatter``` fence found"
    )


def _coerce_list(value: object) -> list[str]:
    """YAML may parse `[]` as an empty list, but if S3 emits a single string
    we still want it as a 1-element list so downstream JSON encoding is
    stable. None / missing is handled by the caller.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


# ---------------------------------------------------------------------------
# Public extract API
# ---------------------------------------------------------------------------


def extract(text: str) -> ProposalFrontmatterV1:
    """Parse a vault page / issue body and return the validated frontmatter.

    Raises:
        NoFrontmatterFoundError: no YAML block at all.
        FrontmatterParseError: block found but malformed YAML.
        MissingFrontmatterKeyError: block parsed but >=1 of the 5 keys absent.
        pydantic.ValidationError: a key was present but failed schema (e.g.
            invalid metric_type enum, malformed proposal_id slug).
    """
    block = _find_yaml_block(text)
    try:
        parsed = yaml.safe_load(block) or {}
    except yaml.YAMLError as exc:
        raise FrontmatterParseError(f"malformed YAML in frontmatter block: {exc}") from exc
    if not isinstance(parsed, dict):
        raise FrontmatterExtractError(
            f"frontmatter must be a YAML mapping, got {type(parsed).__name__}"
        )

    missing = [k for k in REQUIRED_FRONTMATTER_KEYS if k not in parsed]
    if missing:
        raise MissingFrontmatterKeyError(missing)

    return ProposalFrontmatterV1(
        proposal_id=parsed["proposal_id"],
        metric_type=parsed["metric_type"],
        success_metric=parsed["success_metric"],
        related_adr=_coerce_list(parsed["related_adr"]),
        related_issues=_coerce_list(parsed["related_issues"]),
    )


def extract_from_path(path: Path) -> ProposalFrontmatterV1:
    """Convenience wrapper: read a file and run `extract` on its contents."""
    return extract(Path(path).read_text(encoding="utf-8"))
