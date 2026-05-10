"""Reading Context Package + Writing Assist Surface schemas (ADR-024 Slice 9 / issue #517).

Pure pydantic value-objects describing the Stage 3 → Stage 4 handoff package
emitted by ``agents.robin.reading_context_package.ReadingContextPackageBuilder``
and the structural rendering produced by
``shared.writing_assist_surface.WritingAssistSurface``.

ADR-024 §Decision: "Robin may produce a Reading Context Package... A Brook-owned
or shared Writing Assist Surface may present this package and help insert links,
references, and prompts. **It must not generate finished prose or ghostwrite
Line 2 atomic content.**"

The ``WritingAssistOutput`` shape is the architectural commitment to that boundary.
W1-W7 invariants from Brief §4.2 are enforced at TWO layers:

1. **Per-field schema validators** (this module) — every ``SectionBlock``,
   ``Question``, ``MissingPiecePrompt``, and ``WritingAssistOutput`` rejects
   ghostwriting-shaped values at construct time. Future LLM-backed enrichment
   that builds these objects directly cannot bypass this.
2. **Surface-render validation** (``shared.writing_assist_surface``) — the
   ``WritingAssistSurface.render`` method re-checks all rules end-to-end before
   returning, so structural composition that constructs values through other
   paths is also blocked.

W1: NO ``SectionBlock`` field contains a sentence ending with ``.`` ``。`` ``!``
    ``！`` outside ``evidence_pointers[*].excerpt``.
W2: NO ``SectionBlock.heading`` ends with terminal punctuation
    (``.`` ``。`` ``!`` ``?`` ``？``).
W3: NO output field contains first-person tokens (``我``, ``我們``, ``I``,
    ``we``, ``my``, ``we'll``, ``our``, etc.) outside excerpts.
W4: NO output field contains "I think" / "I believe" / "我認為" / "我覺得" /
    "我相信" patterns.
W5: ``Question.text`` ends with ``?`` or ``？``.
W6: ``MissingPiecePrompt.text`` does NOT end with ``.`` ``。``.
W7: Total non-excerpt char count ≤ 5000.

Closed-set extension protocol (mirrors #509 N6 / #511 / #512 / #513 / #514
contract): every ``Literal`` enum is frozen for ``schema_version=1``. Adding
a new member or invariant requires (a) bumping ``schema_version`` on
``ReadingContextPackage`` / ``WritingAssistOutput``, (b) updating this
docstring + the value-object docstring, (c) reviewing W1-W7 invariants for
the new shape. Silent extension is forbidden.

Hard invariant on ``ReadingContextPackage`` (Pydantic ``model_validator``):

- ``error is not None`` ⇒ all aggregated lists empty AND
  ``outline_skeleton is None`` AND ``missing_piece_prompts == []``.
  Builder failures MUST surface as empty package + error message; downstream
  Stage-4 surface MUST NOT render an error+populated combination. Mirrors the
  F1-analog fix on ``PreflightReport`` (#511) / ``SourceMapBuildResult`` (#513) /
  ``ConceptPromotionResult`` (#514).

Hard invariant on ``WritingAssistOutput`` (W7 size budget validator):

- Sum of all string field lengths excluding ``evidence_pointers[*].excerpt`` ≤
  5000 chars. Constructing a value over-budget raises ``ValueError`` at the
  schema layer regardless of surface validation.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ── Boundary patterns (W1-W7) ────────────────────────────────────────────────

_TERMINAL_HEADING_PUNCT = ("。", ".", "!", "！", "?", "？")
"""W2: ``SectionBlock.heading`` MUST NOT end with any of these."""

_TERMINAL_SENTENCE_PUNCT = ("。", ".", "!", "！")
"""W1: non-excerpt ``SectionBlock`` text MUST NOT end with these.
W6: ``MissingPiecePrompt.text`` MUST NOT end with ``。`` or ``.``."""

_TERMINAL_QUESTION_PUNCT = ("?", "？")
"""W5: ``Question.text`` MUST end with one of these."""

# W3: word-bounded English first-person tokens. Use regex word boundaries so
# words like "swept" / "answer" are not misflagged. The Chinese tokens are
# substring matches because Chinese has no word boundaries; the chance of
# false positives ("我" appearing inside an unrelated heading) is low for
# a structural scaffold.
_FIRST_PERSON_EN = re.compile(r"\b(I|we|my|our|us|me|we'll|I'll|I've|we've)\b", re.IGNORECASE)
_FIRST_PERSON_ZH = ("我", "我們", "我们", "咱們", "咱们")

# W4: opinion patterns. Both English (case-insensitive) and Chinese.
_I_THINK_EN = re.compile(r"\b(I\s+think|I\s+believe|I\s+feel|I\s+suppose)\b", re.IGNORECASE)
_I_THINK_ZH = ("我認為", "我覺得", "我相信", "我认为", "我觉得")

_W7_BUDGET_CHARS = 5000
"""Total non-excerpt char count budget for one ``WritingAssistOutput``."""


def _has_first_person(text: str) -> bool:
    """W3 helper. ``True`` when ``text`` contains any first-person token."""
    if not text:
        return False
    if _FIRST_PERSON_EN.search(text) is not None:
        return True
    return any(token in text for token in _FIRST_PERSON_ZH)


def _has_i_think(text: str) -> bool:
    """W4 helper. ``True`` when ``text`` contains an opinion pattern."""
    if not text:
        return False
    if _I_THINK_EN.search(text) is not None:
        return True
    return any(token in text for token in _I_THINK_ZH)


def _ends_with_sentence_terminal(text: str) -> bool:
    """W1 helper. ``True`` when stripped ``text`` ends in sentence-terminal punct."""
    stripped = text.rstrip()
    return bool(stripped) and stripped.endswith(_TERMINAL_SENTENCE_PUNCT)


# ── Idea cluster + question + evidence + outline ──────────────────────────────


class IdeaCluster(BaseModel):
    """A grouping of related annotations / claims that suggest a section.

    Frozen value-object. ``label`` is a short descriptor (NOT a sentence)
    used as the candidate section heading; the surface enforces W2 on
    derived ``SectionBlock.heading`` so an over-long or terminal-punctuated
    label is rejected at the boundary.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    cluster_id: str
    label: str
    annotation_refs: list[str] = Field(default_factory=list)
    claim_refs: list[str] = Field(default_factory=list)


class Question(BaseModel):
    """An open question the writing should address. NOT an answer.

    W5: ``text`` MUST end with ``?`` or ``？``. The schema validator rejects
    constructions that omit the terminal so a future LLM-backed
    question-generation cannot accidentally produce an assertive statement.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    question_id: str
    text: str
    related_clusters: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _w5_question_ends_with_question_mark(self) -> Question:
        stripped = self.text.rstrip()
        if not stripped or not stripped.endswith(_TERMINAL_QUESTION_PUNCT):
            raise ValueError(
                f"W5 violation: Question.text must end with '?' or '？'; "
                f"got text={self.text!r}. Questions are open prompts, not "
                f"assertions; the writing-assist surface refuses to mint a "
                f"Question whose text reads like a statement."
            )
        return self


class EvidenceItem(BaseModel):
    """A pointer to evidence (annotation / source page quote / concept page).

    ``item_kind`` is closed for ``schema_version=1`` (``annotation`` /
    ``source_quote`` / ``concept_link``). Adding a new kind requires the
    extension protocol described in the module docstring.

    The ``excerpt`` field is the only place quoted prose is allowed to
    appear; W1/W3/W4 explicitly EXCLUDE excerpt content from their sweeps
    because the excerpt IS quoted source content, not authored.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    item_kind: Literal["annotation", "source_quote", "concept_link"]
    locator: str
    excerpt: str = Field(max_length=200)
    source: str


class OutlineSkeleton(BaseModel):
    """Outline candidate — section headings only. NO content under each.

    ``section_labels`` ordering mirrors the cluster order chosen by the
    builder (count-desc then alphabetical). Each label is a heading like
    ``HRV 在訓練中的角色`` — not a sentence.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    skeleton_id: str
    section_labels: list[str] = Field(default_factory=list)


class MissingPiecePrompt(BaseModel):
    """Identifies what evidence/argument is missing — does NOT supply it.

    W6: ``text`` MUST NOT end with sentence-terminal punctuation
    (``.`` / ``。``). Phrasing is a need-statement (``HRV 訓練: 需要更多 evidence``),
    not an assertion (``HRV 訓練 needs more evidence.``). The schema validator
    rejects accidental terminal punctuation at construct time.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_id: str
    text: str

    @model_validator(mode="after")
    def _w6_missing_piece_no_terminal_punct(self) -> MissingPiecePrompt:
        stripped = self.text.rstrip()
        if stripped.endswith((".", "。")):
            raise ValueError(
                f"W6 violation: MissingPiecePrompt.text must not end with '.' "
                f"or '。'; got text={self.text!r}. A missing-piece prompt is "
                f"phrased as a need (e.g. '...: 需要更多 evidence'), not an "
                f"assertion; terminal punctuation reads as a closed claim."
            )
        return self


# ── Reading Context Package ───────────────────────────────────────────────────


class ReadingContextPackage(BaseModel):
    """Stage 3 → Stage 4 handoff. NOT a draft.

    Frozen value-object. Carries enough material for 修修's hand-writing
    (annotations, notes, digest, source quotes, concept links, idea clusters,
    questions, outline candidates, missing-piece prompts) without crossing
    into authorship.

    F1-analog invariant: ``error is not None`` ⇒ all aggregated lists empty
    AND ``outline_skeleton is None`` AND ``missing_piece_prompts == []``.
    Builder failures must surface as a clean error envelope; downstream
    Stage-4 surface MUST NOT consume an error+populated combination.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    """First field — closed-set extension protocol marker. Adding any new
    Literal member or invariant requires bumping this and updating the
    surface + downstream consumers."""

    source_id: str
    """Mirrors ``ReadingSource.source_id``. Transport string only — the
    builder NEVER parses it (per #509 N3 contract)."""

    annotations: list[EvidenceItem] = Field(default_factory=list)
    digest_excerpts: list[EvidenceItem] = Field(default_factory=list)
    notes_excerpts: list[EvidenceItem] = Field(default_factory=list)
    source_quotes: list[EvidenceItem] = Field(default_factory=list)
    concept_links: list[EvidenceItem] = Field(default_factory=list)
    idea_clusters: list[IdeaCluster] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    outline_skeleton: OutlineSkeleton | None = None
    missing_piece_prompts: list[MissingPiecePrompt] = Field(default_factory=list)
    error: str | None = None
    """``None`` on success. Set to a short, code-prefixed reason
    (e.g. ``"digest_load_failed: ..."``) when the builder's narrow exception
    tuple caught a documented failure. On error, all aggregated lists are
    empty and the caller is responsible for routing to a defer/retry surface
    (mirrors #511 inspector_error / #513 / #514 error policy)."""

    @model_validator(mode="after")
    def _hard_invariant_error_implies_empty(self) -> ReadingContextPackage:
        if self.error is None:
            return self

        non_empty = []
        if self.annotations:
            non_empty.append(("annotations", len(self.annotations)))
        if self.digest_excerpts:
            non_empty.append(("digest_excerpts", len(self.digest_excerpts)))
        if self.notes_excerpts:
            non_empty.append(("notes_excerpts", len(self.notes_excerpts)))
        if self.source_quotes:
            non_empty.append(("source_quotes", len(self.source_quotes)))
        if self.concept_links:
            non_empty.append(("concept_links", len(self.concept_links)))
        if self.idea_clusters:
            non_empty.append(("idea_clusters", len(self.idea_clusters)))
        if self.questions:
            non_empty.append(("questions", len(self.questions)))
        if self.missing_piece_prompts:
            non_empty.append(("missing_piece_prompts", len(self.missing_piece_prompts)))
        if self.outline_skeleton is not None:
            non_empty.append(("outline_skeleton", 1))

        if non_empty:
            raise ValueError(
                f"error is not None requires all aggregated lists empty + "
                f"outline_skeleton=None + missing_piece_prompts=[]; got "
                f"non-empty fields {non_empty} with error={self.error!r}. "
                f"Builder failures must surface as empty package + error per "
                f"Brief §6 / W7-style invariant; downstream Stage-4 surface "
                f"MUST NOT consume an error+populated combination. Mirrors "
                f"#511 / #513 / #514 F1 inspector_error/defer pattern."
            )
        return self


# ── Writing Assist Surface output ─────────────────────────────────────────────


class SectionBlock(BaseModel):
    """One outline section's scaffold. NOT body content.

    W1: non-excerpt fields (``heading``, ``question_prompts[*]``,
    ``missing_piece_prompts[*]``) MUST NOT end with sentence-terminal
    punctuation. Excerpts inside ``evidence_pointers`` are quoted source
    content, not authored, and are exempt.

    W2: ``heading`` MUST NOT end with ``.`` ``。`` ``!`` ``?`` ``？``.

    W5: every entry in ``question_prompts`` MUST end with ``?`` or ``？``.

    The schema validator enforces W2 + W5 at construct time; the surface
    renderer enforces W1 + W3 + W4 across the whole output (including
    cross-block first-person sweeps).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    heading: str = Field(max_length=80)
    question_prompts: list[str] = Field(default_factory=list)
    evidence_pointers: list[EvidenceItem] = Field(default_factory=list)
    missing_piece_prompts: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _w2_heading_no_terminal_punct(self) -> SectionBlock:
        stripped = self.heading.rstrip()
        if stripped.endswith(_TERMINAL_HEADING_PUNCT):
            raise ValueError(
                f"W2 violation: SectionBlock.heading must not end with "
                f"terminal punctuation (one of {_TERMINAL_HEADING_PUNCT!r}); "
                f"got heading={self.heading!r}. Headings are labels, not "
                f"sentences or questions."
            )
        return self

    @model_validator(mode="after")
    def _w5_question_prompts_end_with_question(self) -> SectionBlock:
        for idx, prompt in enumerate(self.question_prompts):
            stripped = prompt.rstrip()
            if not stripped or not stripped.endswith(_TERMINAL_QUESTION_PUNCT):
                raise ValueError(
                    f"W5 violation: SectionBlock.question_prompts[{idx}] must "
                    f"end with '?' or '？'; got {prompt!r}. Question prompts "
                    f"are open prompts, not assertions."
                )
        return self


class WritingAssistOutput(BaseModel):
    """Surface output. ONLY structural — no completed prose.

    Frozen value-object — emit a new output on re-render; do not mutate.

    The W7 budget validator enforces a hard cap (5000 chars) on total
    non-excerpt string content. Excerpts are excluded because they are
    quoted source material, not authored content. Crossing the budget at
    construct time raises ``ValueError`` regardless of how the output was
    composed; this defends against an LLM-backed enrichment that constructs
    a ``WritingAssistOutput`` directly.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    """First field — closed-set extension protocol marker. See module docstring."""

    package_source_id: str
    section_blocks: list[SectionBlock] = Field(default_factory=list)
    pointer_index: dict[str, str] = Field(default_factory=dict)
    """label → URL-safe identifier. Used by the rendering surface to build
    in-page nav links. Values are deterministic ASCII identifiers — never
    free-text prose."""

    @model_validator(mode="after")
    def _w7_size_budget(self) -> WritingAssistOutput:
        total = compute_non_excerpt_char_count(self)
        if total > _W7_BUDGET_CHARS:
            raise ValueError(
                f"W7 violation: total non-excerpt char count exceeds budget "
                f"({total} > {_W7_BUDGET_CHARS}); writing-assist scaffolds "
                f"are short — long output suggests prose creep. Excerpts are "
                f"NOT counted (quoted source content, not authored). Reduce "
                f"heading / prompt / pointer-index content."
            )
        return self


def compute_non_excerpt_char_count(output: WritingAssistOutput) -> int:
    """Sum of all string-field char counts in ``output`` excluding
    ``evidence_pointers[*].excerpt``. Used by the W7 budget validator and
    by the surface-render double-check.
    """
    total = 0
    total += len(output.package_source_id)
    for block in output.section_blocks:
        total += len(block.heading)
        for prompt in block.question_prompts:
            total += len(prompt)
        for pointer in block.evidence_pointers:
            # Count locator + source; EXCLUDE excerpt per W7 contract.
            total += len(pointer.locator)
            total += len(pointer.source)
        for missing in block.missing_piece_prompts:
            total += len(missing)
    for key, value in output.pointer_index.items():
        total += len(key)
        total += len(value)
    return total
