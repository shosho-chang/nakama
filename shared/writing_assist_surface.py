"""Writing Assist Surface (ADR-024 Slice 9 / issue #517).

Pure structural mapping from a ``ReadingContextPackage`` to a
``WritingAssistOutput``. ZERO LLM calls, ZERO vault writes, ZERO
``shared.book_storage`` import. The surface is the architectural commitment
that "Stage 4 writing-assist surfaces 修修's own materials, but does NOT
ghostwrite Line 2 atomic content" (ADR-024 §Decision).

Rendering algorithm (Brief §4.4):

1. For each ``IdeaCluster`` → produce a ``SectionBlock`` carrying:
   - ``heading``    = ``cluster.label`` (truncated to 80, terminal punct stripped).
   - ``question_prompts`` = related questions' text.
   - ``evidence_pointers`` = ``EvidenceItem``s associated with the cluster's
     annotation / claim refs, plus any concept_link / source_quote items
     pointing at the cluster's chapter labels.
   - ``missing_piece_prompts`` = related missing-piece text.
2. Build ``pointer_index``: locator string → URL-safe identifier, deterministic.
3. Validate W1-W7 invariants end-to-end (defense-in-depth — schema validators
   already check W2/W5/W6 per-field and W7 at construct time, but this
   surface-level pass also catches W1/W3/W4 cross-field sweeps and re-checks
   W7 after composition).
4. Return the ``WritingAssistOutput``.

W1-W7 hard invariants from Brief §4.2 (see also schema docstring):

- W1: NO ``SectionBlock`` non-excerpt field ends with sentence-terminal punct.
- W2: NO heading ends with terminal punct (already enforced at schema layer).
- W3: NO non-excerpt field contains first-person tokens.
- W4: NO non-excerpt field contains "I think" / "我認為" patterns.
- W5: every question prompt ends with '?' or '？' (already enforced at schema).
- W6: missing-piece prompts do not end with '.' or '。' (already enforced).
- W7: total non-excerpt char count ≤ 5000 (already enforced at schema).

Layered enforcement intentionally duplicates the validation surface so a
future LLM-backed enrichment that constructs ``WritingAssistOutput`` via
some non-render path STILL trips W1-W7 at the schema layer, AND a future
surface that composes its own ``SectionBlock`` instances from arbitrary
sources STILL trips W1/W3/W4 at the surface layer.

Closed scope (Brief §6):

- NO LLM enrichment.
- NO vault write / publish to ``Inbox/writing-assist/*``.
- NO ``shared.book_storage`` import (subprocess-gated).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from shared.schemas.reading_context_package import (
    EvidenceItem,
    IdeaCluster,
    MissingPiecePrompt,
    Question,
    ReadingContextPackage,
    SectionBlock,
    WritingAssistOutput,
    _ends_with_sentence_terminal,
    _has_first_person,
    _has_i_think,
    compute_non_excerpt_char_count,
)

_HEADING_MAX_LEN = 80
"""Per Brief §4.4: ``cluster.label`` is truncated to 80 chars before becoming
``SectionBlock.heading``."""

_W7_BUDGET_CHARS = 5000
"""Mirror of ``shared.schemas.reading_context_package._W7_BUDGET_CHARS``;
duplicated here so the surface check is independent of schema-layer changes."""

_TERMINAL_HEADING_PUNCT = ("。", ".", "!", "！", "?", "？")
"""W2 sweep set — used during heading-truncation cleanup. The schema layer
re-validates this on ``SectionBlock`` construction."""

# Used by the pointer-index builder to derive a URL-safe identifier from a
# free-form locator. Replace runs of non-alphanumeric characters with a
# single ``-``; collapse leading/trailing dashes. Deterministic.
_NON_URL_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


class WritingAssistSurface:
    """Pure deterministic surface from ``ReadingContextPackage`` →
    ``WritingAssistOutput``.

    The surface is stateless — construct one and call ``render(package)``
    per package. There is no caching and no enumeration API; the caller
    chooses which package to render.
    """

    def render(self, package: ReadingContextPackage) -> WritingAssistOutput:
        """Build and return a ``WritingAssistOutput`` for ``package``.

        Raises ``ValueError("ghostwriting detected: <which> in <field>")``
        when any of W1-W7 is violated. The schema layer also rejects
        constructions that violate W2/W5/W6/W7, so this method should be
        the only path that surfaces W1/W3/W4 violations (which depend on
        cross-field sweeps).
        """
        # Build cluster → questions / missing-piece lookups so we can join
        # the section blocks deterministically.
        cluster_id_to_questions = _index_questions_by_cluster(package.questions)
        cluster_id_to_missing = _index_missing_piece_prompts_by_cluster(
            package.missing_piece_prompts, package.idea_clusters
        )

        # Index evidence by annotation / claim ref so we can attach to clusters.
        annotation_locator_to_item = {item.locator: item for item in package.annotations}
        # Source-quote and concept_link pointers may reference cluster labels
        # via their ``source`` (concept page) or ``locator`` (chapter ref).
        # We attach by string match; deterministic.

        section_blocks: list[SectionBlock] = []
        for cluster in package.idea_clusters:
            heading = _normalize_heading(cluster.label)
            block_questions = [q.text for q in cluster_id_to_questions.get(cluster.cluster_id, [])]
            evidence_pointers = _collect_evidence_for_cluster(
                cluster=cluster,
                annotation_locator_to_item=annotation_locator_to_item,
                source_quotes=package.source_quotes,
                concept_links=package.concept_links,
                digest_excerpts=package.digest_excerpts,
                notes_excerpts=package.notes_excerpts,
            )
            missing_prompts = [m.text for m in cluster_id_to_missing.get(cluster.cluster_id, [])]

            block = SectionBlock(
                heading=heading,
                question_prompts=block_questions,
                evidence_pointers=evidence_pointers,
                missing_piece_prompts=missing_prompts,
            )
            section_blocks.append(block)

        pointer_index = _build_pointer_index(section_blocks)

        output = WritingAssistOutput(
            package_source_id=package.source_id,
            section_blocks=section_blocks,
            pointer_index=pointer_index,
        )

        # Defense-in-depth: re-validate W1 / W3 / W4 / W7 across the composed
        # output. The schema layer already enforces W2 / W5 / W6 / W7 at
        # construct time; this pass catches cross-field sweeps and gives a
        # uniform error message that names the rule.
        _validate_no_ghostwriting(output)
        return output


# ── Cluster index helpers ─────────────────────────────────────────────────────


def _index_questions_by_cluster(questions: Iterable[Question]) -> dict[str, list[Question]]:
    """Group questions by their ``related_clusters`` membership. Deterministic
    — questions appear in the order they were emitted by the builder."""
    out: dict[str, list[Question]] = {}
    for q in questions:
        for cid in q.related_clusters:
            out.setdefault(cid, []).append(q)
    return out


def _index_missing_piece_prompts_by_cluster(
    prompts: Iterable[MissingPiecePrompt], clusters: Iterable[IdeaCluster]
) -> dict[str, list[MissingPiecePrompt]]:
    """Match missing-piece prompts to the cluster they reference.

    The builder uses ``MissingPiecePrompt.prompt_id == "miss_{cluster_id}"`` —
    this helper joins on that convention. If the convention drifts, any
    unmatched prompt is dropped from the surface (silently — the prompt is
    still in the package; only the surface display join is lossy). That's an
    acceptable degradation; the package remains the source of truth.
    """
    cluster_ids = {c.cluster_id for c in clusters}
    out: dict[str, list[MissingPiecePrompt]] = {}
    for prompt in prompts:
        # Join convention: prompt_id starts with ``miss_<cluster_id>``.
        if not prompt.prompt_id.startswith("miss_"):
            continue
        candidate = prompt.prompt_id.removeprefix("miss_")
        if candidate in cluster_ids:
            out.setdefault(candidate, []).append(prompt)
    return out


def _collect_evidence_for_cluster(
    *,
    cluster: IdeaCluster,
    annotation_locator_to_item: dict[str, EvidenceItem],
    source_quotes: list[EvidenceItem],
    concept_links: list[EvidenceItem],
    digest_excerpts: list[EvidenceItem],
    notes_excerpts: list[EvidenceItem],
) -> list[EvidenceItem]:
    """Walk all evidence sources and collect items associated with ``cluster``.

    Association rules:
    - Annotations: locator in ``cluster.annotation_refs``.
    - Source quotes: locator in ``cluster.claim_refs`` OR locator string
      contains the cluster label.
    - Concept links: ``source`` field equals or starts with the cluster label.
    - Digest / notes excerpts: locator contains the cluster label (loose
      match; the package retains every excerpt regardless).

    Empty list when nothing matches — that's a legitimate signal that the
    cluster has no source evidence (the builder will have emitted a
    missing-piece prompt for it).
    """
    items: list[EvidenceItem] = []

    for ref in cluster.annotation_refs:
        match = annotation_locator_to_item.get(ref)
        if match is not None:
            items.append(match)

    label = cluster.label

    for sq in source_quotes:
        if sq.locator in cluster.claim_refs or label in sq.locator:
            items.append(sq)

    for cl in concept_links:
        if cl.source.startswith(label) or label in cl.source:
            items.append(cl)

    for dx in digest_excerpts:
        if label in dx.locator:
            items.append(dx)

    for nx in notes_excerpts:
        if label in nx.locator:
            items.append(nx)

    return items


def _normalize_heading(label: str) -> str:
    """Truncate to 80 chars and strip terminal punctuation so the heading
    satisfies W2. Whitespace at edges is also stripped — headings are labels,
    not sentences.
    """
    candidate = label.strip()
    if len(candidate) > _HEADING_MAX_LEN:
        candidate = candidate[:_HEADING_MAX_LEN].rstrip()
    while candidate and candidate.endswith(_TERMINAL_HEADING_PUNCT):
        candidate = candidate[:-1].rstrip()
    return candidate


def _build_pointer_index(blocks: list[SectionBlock]) -> dict[str, str]:
    """Map each evidence locator across all blocks → a URL-safe identifier.

    Deterministic: identifier = ``slugify(locator) + "_<idx>"`` where ``idx``
    is the 1-based first-occurrence position of the locator across the
    flattened block evidence list. Duplicate locators get the SAME identifier
    (first occurrence wins).
    """
    out: dict[str, str] = {}
    for block in blocks:
        for pointer in block.evidence_pointers:
            if pointer.locator in out:
                continue
            slug = _NON_URL_SAFE.sub("-", pointer.locator).strip("-")
            if not slug:
                slug = "evidence"
            out[pointer.locator] = f"{slug}-{len(out) + 1}"
    return out


# ── W1-W7 validation (defense-in-depth) ───────────────────────────────────────


def _validate_no_ghostwriting(output: WritingAssistOutput) -> None:
    """Sweep the rendered output for W1/W3/W4/W7 violations.

    W2, W5, W6 are already enforced by the schema validators on
    ``SectionBlock`` / ``Question`` / ``MissingPiecePrompt``; this pass
    re-runs W7 (in case the schema check was skipped via some construction
    path, e.g. direct dict load) AND adds the cross-field W1 / W3 / W4
    sweeps.
    """
    # W7 — recompute the budget here so the surface fails closed even if
    # the schema-layer validator was bypassed.
    total = compute_non_excerpt_char_count(output)
    if total > _W7_BUDGET_CHARS:
        raise ValueError(
            f"ghostwriting detected: W7 size budget exceeded "
            f"({total} > {_W7_BUDGET_CHARS} chars) in WritingAssistOutput"
        )

    for block_idx, block in enumerate(output.section_blocks):
        # W1 sweep: heading + question_prompts + missing_piece_prompts must
        # not end with sentence-terminal punctuation. Question prompts end
        # with '?' / '？' which is NOT in the W1 set.
        if _ends_with_sentence_terminal(block.heading):
            raise ValueError(
                f"ghostwriting detected: W1 (sentence-terminal punct) in "
                f"section_blocks[{block_idx}].heading={block.heading!r}"
            )
        for prompt_idx, prompt in enumerate(block.missing_piece_prompts):
            if _ends_with_sentence_terminal(prompt):
                raise ValueError(
                    f"ghostwriting detected: W1 (sentence-terminal punct) in "
                    f"section_blocks[{block_idx}].missing_piece_prompts"
                    f"[{prompt_idx}]={prompt!r}"
                )

        # W3 / W4 sweeps: heading + question prompts + missing-piece prompts
        # MUST NOT contain first-person or "I think"-pattern tokens.
        # Excerpts in evidence_pointers are explicitly EXCLUDED — they are
        # quoted source content, not authored.
        _check_no_first_person_or_opinion(
            block.heading,
            field_path=f"section_blocks[{block_idx}].heading",
        )
        for prompt_idx, prompt in enumerate(block.question_prompts):
            _check_no_first_person_or_opinion(
                prompt,
                field_path=f"section_blocks[{block_idx}].question_prompts[{prompt_idx}]",
            )
        for prompt_idx, prompt in enumerate(block.missing_piece_prompts):
            _check_no_first_person_or_opinion(
                prompt,
                field_path=f"section_blocks[{block_idx}].missing_piece_prompts[{prompt_idx}]",
            )

    # Pointer index keys + values are short, ASCII identifiers; W3/W4 still
    # apply (the keys are locators; values are slug ids). Sweep both.
    for key, value in output.pointer_index.items():
        _check_no_first_person_or_opinion(key, field_path=f"pointer_index key={key!r}")
        _check_no_first_person_or_opinion(value, field_path=f"pointer_index[{key!r}]")


def _check_no_first_person_or_opinion(text: str, *, field_path: str) -> None:
    """Raise ``ValueError`` if ``text`` contains a W3 or W4 violation.

    ``field_path`` is included verbatim in the error message so a downstream
    test assertion can diagnose which field failed.
    """
    if _has_i_think(text):
        raise ValueError(f"ghostwriting detected: W4 (I-think pattern) in {field_path}={text!r}")
    if _has_first_person(text):
        raise ValueError(f"ghostwriting detected: W3 (first-person token) in {field_path}={text!r}")
