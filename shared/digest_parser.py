"""Parse Robin PubMed / Franky AI digest body markdown into structured studies.

Robin and Franky both write digests as markdown with a fixed bullet-list
schema per entry. The Bridge digest viewer was previously dumping the
rendered markdown as-is; this module gives the viewer enough structure to
render each entry as a card (score chip, journal meta, domain tag, etc).

Schema (PubMed)::

    ### N. <title>

    - **Journal**: <name> (Q<n> · SJR <number>)
    - **Domain**: `<slug>`
    - **Score**: <X.X>  (R<r>/I<i>/C<c>/A<a>/F<f>/N<n>)
    - **Verdict**: <繁中 1-sentence summary>
    - **Why**: <繁中 paragraph>
    - **全文**: <OA badge + DOI / attachment link>
    - **→** [[pubmed-<id>]] · [PubMed](<url>)

Editor's picks live under ``## ⭐ Editor's Picks``; other studies under
``## 其他精選``.

Schema (AI)::

    ## N. <title>

    - **Publisher**: <name>
    - **Category**: `<slug>`
    - **Published**: <ISO ts> (<age>)
    - **Score**: <X.X> (5-dim) / <Y.Y> (4-dim)  (S<s>/N<n>/A<a>/Q<q>/R<r>)
    - **Verdict**: <繁中 summary>
    - **Why**: <繁中 paragraph>
    - **Key**: <繁中 key point>
    - **Noise note**: <繁中 noise/hype call-out>
    - **→** [<url>](<url>)

Defensive parsing — a malformed line skips the field, never raises;
callers fall back to rendering the raw markdown for that entry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class DigestStudy:
    """One parsed entry from a Robin / Franky digest.

    Field set is the union of PubMed + AI schemas; type-specific fields are
    Optional. ``type`` lets templates branch on which fields are populated.
    """

    type: str  # "pubmed" | "ai"
    idx: int
    is_editor_pick: bool
    title: str

    # Source (one of the pair populated)
    journal_name: Optional[str] = None  # PubMed
    journal_quartile: Optional[str] = None  # PubMed (e.g. "Q1")
    journal_sjr: Optional[float] = None  # PubMed
    publisher: Optional[str] = None  # AI

    # Category / domain tag
    domain: Optional[str] = None  # PubMed
    category: Optional[str] = None  # AI

    # Time
    published_at: Optional[str] = None  # AI ISO timestamp
    published_age: Optional[str] = None  # AI human age e.g. "22.5h ago"

    # Score
    score: Optional[float] = None
    score_secondary: Optional[float] = None  # AI 4-dim score
    score_breakdown: Optional[str] = None  # raw "R4/I4/C3/A2/F4/N4"
    score_dims: tuple[tuple[str, int], ...] = ()  # parsed (("R",4),("I",4),...)

    # Narrative
    verdict: Optional[str] = None
    why: Optional[str] = None
    key_point: Optional[str] = None  # AI
    noise_note: Optional[str] = None  # AI

    # Access / OA status (PubMed)
    full_text_label: Optional[str] = None
    full_text_url: Optional[str] = None

    # Links
    kb_wikilink: Optional[str] = None  # PubMed: "pubmed-42178714"
    external_url: Optional[str] = None  # PubMed: pubmed.ncbi; AI: blog URL
    external_id: Optional[str] = None  # PubMed numeric id


# ── PubMed body parsing ────────────────────────────────────────────────────

# Entry header: ### N. <title>
_PUBMED_ENTRY_RE = re.compile(r"^###\s+(\d+)\.\s+(.+?)\s*$", re.MULTILINE)
# Section header to detect editor's-picks region
_PUBMED_PICK_HEADER_RE = re.compile(r"^##\s+⭐\s*Editor.+$", re.MULTILINE)
_PUBMED_OTHER_HEADER_RE = re.compile(r"^##\s+其他精選\s*$", re.MULTILINE)
_PUBMED_JOURNAL_RE = re.compile(
    r"^- \*\*Journal\*\*:\s*(.+?)\s*\(\s*(Q\d)\s*·\s*SJR\s*([\d.]+)\s*\)\s*$",
    re.MULTILINE,
)
_PUBMED_DOMAIN_RE = re.compile(r"^- \*\*Domain\*\*:\s*`?([a-z_]+)`?\s*$", re.MULTILINE)
_SCORE_LINE_RE = re.compile(
    r"^- \*\*Score\*\*:\s*([\d.]+)(?:\s*\(5-dim\)\s*/\s*([\d.]+)\s*\(4-dim\))?\s*"
    r"(?:\(([A-Z]\d(?:/[A-Z]\d)+)\))?\s*$",
    re.MULTILINE,
)
_PUBMED_VERDICT_RE = re.compile(r"^- \*\*Verdict\*\*:\s*(.+?)\s*$", re.MULTILINE)
_PUBMED_WHY_RE = re.compile(r"^- \*\*Why\*\*:\s*(.+?)\s*$", re.MULTILINE)
_PUBMED_FULLTEXT_RE = re.compile(r"^- \*\*全文\*\*:\s*(.+?)\s*$", re.MULTILINE)
# → [[pubmed-XXX]] · [PubMed](url)
_PUBMED_REF_RE = re.compile(
    r"^- \*\*→\*\*\s+\[\[(pubmed-(\d+))\]\][^[]*\[PubMed\]\(([^)]+)\)\s*$",
    re.MULTILINE,
)
# Inside the full-text line, extract first URL if present
_INLINE_URL_RE = re.compile(r"\[(?:DOI:[^\]]*|[^\]]*)\]\((https?://[^)]+)\)")
_DIM_PART_RE = re.compile(r"([A-Z])(\d)")


# ── AI body parsing ────────────────────────────────────────────────────────

_AI_ENTRY_RE = re.compile(r"^##\s+(\d+)\.\s+(.+?)\s*$", re.MULTILINE)
_AI_PUBLISHER_RE = re.compile(r"^- \*\*Publisher\*\*:\s*(.+?)\s*$", re.MULTILINE)
_AI_CATEGORY_RE = re.compile(r"^- \*\*Category\*\*:\s*`?([a-z_]+)`?\s*$", re.MULTILINE)
_AI_PUBLISHED_RE = re.compile(
    r"^- \*\*Published\*\*:\s*([\d\-T:+]+)\s*(?:\(([^)]+)\))?\s*$",
    re.MULTILINE,
)
_AI_KEY_RE = re.compile(r"^- \*\*Key\*\*:\s*(.+?)\s*$", re.MULTILINE)
_AI_NOISE_RE = re.compile(r"^- \*\*Noise note\*\*:\s*(.+?)\s*$", re.MULTILINE)
# AI links use [url](url) or [text](url)
_AI_REF_RE = re.compile(
    r"^- \*\*→\*\*\s+\[[^\]]+\]\((https?://[^)]+)\)\s*$",
    re.MULTILINE,
)


def _parse_score_dims(breakdown: str | None) -> tuple[tuple[str, int], ...]:
    if not breakdown:
        return ()
    out: list[tuple[str, int]] = []
    for m in _DIM_PART_RE.finditer(breakdown):
        out.append((m.group(1), int(m.group(2))))
    return tuple(out)


def _split_entries(body: str, header_re: re.Pattern) -> list[tuple[int, str, str]]:
    """Split body into ``(idx, title, entry_body)`` tuples by entry header.

    Entry body = text from after header line up to (but not including) the
    next entry header or the next ``## `` section header.
    """
    matches = list(header_re.finditer(body))
    if not matches:
        return []
    out: list[tuple[int, str, str]] = []
    for i, m in enumerate(matches):
        idx = int(m.group(1))
        title = m.group(2).strip()
        body_start = m.end()
        if i + 1 < len(matches):
            body_end = matches[i + 1].start()
        else:
            body_end = len(body)
        # Also cut at next ## section if it appears before next entry
        section_cut = re.search(r"^##\s+\S", body[body_start:body_end], re.MULTILINE)
        if section_cut:
            body_end = body_start + section_cut.start()
        out.append((idx, title, body[body_start:body_end]))
    return out


def parse_pubmed_digest(body: str) -> list[DigestStudy]:
    """Parse a PubMed daily digest body into ``DigestStudy`` entries.

    Editor's-pick detection uses byte offsets — anything between the
    ``## ⭐ Editor's Picks`` header and the ``## 其他精選`` header is
    flagged ``is_editor_pick=True``.
    """
    studies: list[DigestStudy] = []

    pick_match = _PUBMED_PICK_HEADER_RE.search(body)
    other_match = _PUBMED_OTHER_HEADER_RE.search(body)
    pick_start = pick_match.end() if pick_match else None
    other_start = other_match.start() if other_match else None

    for idx, title, entry_body in _split_entries(body, _PUBMED_ENTRY_RE):
        # Determine entry's start offset in body for editor-pick check
        # (re-find since _split_entries strips the header from the body slice)
        m = re.search(rf"^###\s+{idx}\.\s+", body, re.MULTILINE)
        offset = m.start() if m else -1
        is_pick = False
        if pick_start is not None and offset >= pick_start:
            if other_start is None or offset < other_start:
                is_pick = True

        journal_name = quartile = None
        sjr: Optional[float] = None
        jm = _PUBMED_JOURNAL_RE.search(entry_body)
        if jm:
            journal_name = jm.group(1).strip()
            quartile = jm.group(2)
            try:
                sjr = float(jm.group(3))
            except ValueError:
                sjr = None

        domain = None
        dm = _PUBMED_DOMAIN_RE.search(entry_body)
        if dm:
            domain = dm.group(1)

        score: Optional[float] = None
        score_secondary: Optional[float] = None
        score_breakdown: Optional[str] = None
        sm = _SCORE_LINE_RE.search(entry_body)
        if sm:
            try:
                score = float(sm.group(1))
            except ValueError:
                pass
            if sm.group(2):
                try:
                    score_secondary = float(sm.group(2))
                except ValueError:
                    pass
            score_breakdown = sm.group(3)

        verdict = why = None
        vm = _PUBMED_VERDICT_RE.search(entry_body)
        if vm:
            verdict = vm.group(1).strip()
        wm = _PUBMED_WHY_RE.search(entry_body)
        if wm:
            why = wm.group(1).strip()

        full_text_label = full_text_url = None
        ftm = _PUBMED_FULLTEXT_RE.search(entry_body)
        if ftm:
            ft_line = ftm.group(1).strip()
            url_m = _INLINE_URL_RE.search(ft_line)
            if url_m:
                full_text_url = url_m.group(1)
            # label = ft_line stripped of inline link markdown
            label = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", ft_line)
            full_text_label = label.strip(" —()")

        kb_wikilink = external_url = external_id = None
        rm = _PUBMED_REF_RE.search(entry_body)
        if rm:
            kb_wikilink = rm.group(1)
            external_id = rm.group(2)
            external_url = rm.group(3)

        studies.append(
            DigestStudy(
                type="pubmed",
                idx=idx,
                is_editor_pick=is_pick,
                title=title,
                journal_name=journal_name,
                journal_quartile=quartile,
                journal_sjr=sjr,
                domain=domain,
                score=score,
                score_breakdown=score_breakdown,
                score_dims=_parse_score_dims(score_breakdown),
                verdict=verdict,
                why=why,
                full_text_label=full_text_label,
                full_text_url=full_text_url,
                kb_wikilink=kb_wikilink,
                external_url=external_url,
                external_id=external_id,
                score_secondary=score_secondary,
            )
        )

    studies.sort(key=lambda s: (not s.is_editor_pick, s.idx))
    return studies


def parse_ai_digest(body: str) -> list[DigestStudy]:
    """Parse an AI daily digest body into ``DigestStudy`` entries."""
    studies: list[DigestStudy] = []
    for idx, title, entry_body in _split_entries(body, _AI_ENTRY_RE):
        publisher = None
        pm = _AI_PUBLISHER_RE.search(entry_body)
        if pm:
            publisher = pm.group(1).strip()

        category = None
        cm = _AI_CATEGORY_RE.search(entry_body)
        if cm:
            category = cm.group(1)

        published_at = published_age = None
        pubm = _AI_PUBLISHED_RE.search(entry_body)
        if pubm:
            published_at = pubm.group(1).strip()
            published_age = (pubm.group(2) or "").strip() or None

        score = score_secondary = None
        score_breakdown = None
        sm = _SCORE_LINE_RE.search(entry_body)
        if sm:
            try:
                score = float(sm.group(1))
            except ValueError:
                pass
            if sm.group(2):
                try:
                    score_secondary = float(sm.group(2))
                except ValueError:
                    pass
            score_breakdown = sm.group(3)

        verdict = why = None
        vm = _PUBMED_VERDICT_RE.search(entry_body)
        if vm:
            verdict = vm.group(1).strip()
        wm = _PUBMED_WHY_RE.search(entry_body)
        if wm:
            why = wm.group(1).strip()

        key_point = noise_note = None
        km = _AI_KEY_RE.search(entry_body)
        if km:
            key_point = km.group(1).strip()
        nm = _AI_NOISE_RE.search(entry_body)
        if nm:
            noise_note = nm.group(1).strip()

        external_url = None
        rm = _AI_REF_RE.search(entry_body)
        if rm:
            external_url = rm.group(1)

        studies.append(
            DigestStudy(
                type="ai",
                idx=idx,
                is_editor_pick=False,
                title=title,
                publisher=publisher,
                category=category,
                published_at=published_at,
                published_age=published_age,
                score=score,
                score_secondary=score_secondary,
                score_breakdown=score_breakdown,
                score_dims=_parse_score_dims(score_breakdown),
                verdict=verdict,
                why=why,
                key_point=key_point,
                noise_note=noise_note,
                external_url=external_url,
            )
        )

    studies.sort(key=lambda s: s.idx)
    return studies


# Human-readable labels for the score dimensions — for hover tooltips on
# the per-entry score chip. Source: Robin/Franky scoring rubric docs.
PUBMED_DIM_LABELS: dict[str, str] = {
    "R": "Rigor 嚴謹度",
    "I": "Impact 影響力",
    "C": "Clarity 清晰度",
    "A": "Audience-fit 對讀者相關度",
    "F": "Freshness 新穎度",
    "N": "Novelty 原創性",
}
AI_DIM_LABELS: dict[str, str] = {
    "S": "Signal 訊號強度",
    "N": "Novelty 新穎度",
    "A": "Actionability 可操作性",
    "Q": "Quality 來源品質",
    "R": "Relevance 與 Nakama 相關度",
}


def dim_label(type_: str, dim: str) -> str:
    """Return the human label for a score dimension code, or the code itself."""
    table = PUBMED_DIM_LABELS if type_ == "pubmed" else AI_DIM_LABELS
    return table.get(dim, dim)
