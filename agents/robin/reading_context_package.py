"""Reading Context Package builder (ADR-024 Slice 9 / issue #517).

Pure deterministic aggregator that assembles a Stage 3 → Stage 4 handoff
package for one ``ReadingSource``. NO LLM call, NO vault write, NO
``shared.book_storage`` import. The builder reads on-disk artifacts
(digest.md, notes.md, KB/Annotations/{key}.md, source map pages, concept
pages) via narrow file-system primitives and emits a frozen
``ReadingContextPackage`` value-object.

ADR-024 §Decision: "Robin may produce a Reading Context Package... Robin
may not auto-generate book-review prose." This builder is the architectural
commitment: it ONLY aggregates 修修's existing materials; it never composes
sentences, never opens an LLM session, never publishes anything.

Behavior (Brief §4.3):

1. Read ``digest.md`` → extract H2-prefixed sections → emit one ``EvidenceItem``
   per non-empty section.
2. Read ``notes.md`` → same.
3. Read annotations via injected ``annotation_loader`` → emit one
   ``EvidenceItem`` per annotation.
4. Walk ``source_map_dir/{source_slug}/*.md`` → extract claim-list bullets +
   quote anchors → emit one ``EvidenceItem`` per quote.
5. Walk ``concepts_dir/*.md`` → for concepts where ``mentioned_in`` includes
   this source → emit one ``concept_link`` ``EvidenceItem``.
6. Cluster annotations deterministically (chapter_ref tag if present;
   otherwise 3-gram overlap fallback).
7. Generate ``Question`` entries from annotations tagged ``question`` (the
   loader returns those). Builder does NOT auto-generate new questions.
8. Build ``OutlineSkeleton.section_labels`` from cluster labels, ordered by
   annotation-count desc then alphabetical.
9. Emit ``MissingPiecePrompt`` for clusters that have annotations but no
   source-page evidence.
10. Return ``ReadingContextPackage``.

Determinism: rerunning ``build`` with the same inputs (same paths, same
loader output) MUST produce a value equal under ``model_dump`` to the prior
run. Tests assert this (BT6).

Failure modes (narrow exception tuple per #511 F5 lesson):

- ``OSError`` (file missing / unreadable) → return error envelope (lists empty).
- ``yaml.YAMLError`` (malformed frontmatter on a concept page) → return error.
- ``ValueError`` (unexpected structure) → return error.
- ``KeyError`` (annotation loader keyed by missing source) → return error.

Programmer errors (TypeError, AttributeError, KeyboardInterrupt) propagate
so test feedback stays meaningful.

Subprocess gates (Brief §5 BT10 / BT11):

- ``import agents.robin.reading_context_package`` MUST NOT pull
  ``anthropic`` / ``openai`` / ``google.generativeai`` into ``sys.modules``.
- Same module MUST NOT pull ``shared.book_storage`` into ``sys.modules``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from shared.log import get_logger
from shared.schemas.reading_context_package import (
    EvidenceItem,
    IdeaCluster,
    MissingPiecePrompt,
    OutlineSkeleton,
    Question,
    ReadingContextPackage,
)
from shared.schemas.reading_source import ReadingSource

_logger = get_logger("nakama.agents.robin.reading_context_package")


# ── Failure tuples (F5 narrow exception protocol) ────────────────────────────

_BUILDER_FAILURES: tuple[type[BaseException], ...] = (
    OSError,
    ValueError,
    KeyError,
    yaml.YAMLError,
)
"""Narrow tuple per #511 F5 lesson. Programmer errors (TypeError,
AttributeError, KeyboardInterrupt) propagate so test feedback stays
meaningful."""


# ── Annotation loader protocol ────────────────────────────────────────────────


# An ``Annotation`` is loosely typed here: the loader returns a list of
# dict-like records with at least the following keys. Tests can inject any
# ``Callable[[str], list[Annotation]]`` that satisfies this shape; the
# overlay/annotation-store schema lives in ``shared.schemas.annotations``
# but the builder does NOT depend on its concrete shape — only on the
# documented dict keys ``locator``, ``text``, ``tags``, ``chapter_ref``.
Annotation = dict[str, Any]
AnnotationLoader = Callable[[str], list[Annotation]]


# ── Markdown parsing helpers ──────────────────────────────────────────────────


_H2_HEADING = re.compile(r"^##\s+(.+)$")
_BULLET = re.compile(r"^\s*[-*]\s+(.+)$")
_FRONTMATTER_FENCE = "---"


def _split_h2_sections(content: str) -> list[tuple[str, str]]:
    """Walk ``content`` line by line; emit ``(heading, body)`` tuples per
    H2 section. Returns ``[]`` when there are no H2 headings.

    Body excludes the heading line itself; whitespace-only sections are
    dropped (per Brief §4.3 "non-empty section"). Body is truncated at
    the next H2 heading; H1 / H3+ are left in the body of the surrounding
    H2.
    """
    sections: list[tuple[str, str]] = []
    current_heading: str | None = None
    current_body: list[str] = []
    for line in content.splitlines():
        match = _H2_HEADING.match(line)
        if match is not None:
            if current_heading is not None:
                body = "\n".join(current_body).strip()
                if body:
                    sections.append((current_heading, body))
            current_heading = match.group(1).strip()
            current_body = []
        elif current_heading is not None:
            current_body.append(line)
    if current_heading is not None:
        body = "\n".join(current_body).strip()
        if body:
            sections.append((current_heading, body))
    return sections


def _strict_load_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Return ``(frontmatter_dict, body)`` for a YAML-frontmatter file.

    Mirrors the strict-parse pattern in ``shared.promotion_preflight`` /
    ``shared.reading_source_registry``: malformed YAML raises
    ``yaml.YAMLError`` so the caller can route to a documented error
    envelope. Empty / missing frontmatter returns ``({}, content)``.
    """
    if not content.startswith(_FRONTMATTER_FENCE):
        return {}, content
    parts = content.split(_FRONTMATTER_FENCE, 2)
    if len(parts) < 3:
        return {}, content
    fm = yaml.safe_load(parts[1])
    if not isinstance(fm, dict):
        fm = {}
    return fm, parts[2].lstrip("\n")


def _truncate_excerpt(text: str, *, limit: int = 200) -> str:
    """Truncate ``text`` to ``limit`` chars (default 200, matching the
    ``EvidenceItem.excerpt`` schema cap). Single-line collapse — newline
    runs collapsed to a single space so the rendered scaffold stays
    horizontally compact.
    """
    collapsed = re.sub(r"\s+", " ", text).strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit].rstrip()


# ── Public API ────────────────────────────────────────────────────────────────


class ReadingContextPackageBuilder:
    """Deterministic reading-context aggregator. One ``build()`` per
    package; no caching, no enumeration.

    Construction takes an optional ``annotation_loader`` so tests inject
    fake annotations. Production wiring will pass a loader backed by
    ``shared.annotation_store``; that wiring is out of scope for #517 (the
    Brief calls for the loader to be injected).
    """

    def __init__(self, *, annotation_loader: AnnotationLoader | None = None) -> None:
        self._annotation_loader = annotation_loader

    def build(
        self,
        reading_source: ReadingSource,
        *,
        digest_path: Path,
        notes_path: Path,
        annotations_path: Path,
        source_map_dir: Path,
        concepts_dir: Path,
    ) -> ReadingContextPackage:
        """Aggregate the reading-context package for ``reading_source``.

        On any documented failure, return a clean error envelope (all
        aggregated lists empty + ``error=...``). The F1-analog model
        validator on ``ReadingContextPackage`` enforces this is the only
        legitimate error shape.
        """
        try:
            digest_excerpts = _read_digest_excerpts(digest_path)
            notes_excerpts = _read_notes_excerpts(notes_path)
            annotations_items, raw_annotations = _read_annotations(
                self._annotation_loader,
                annotations_path,
                reading_source,
            )
            source_quotes = _read_source_quotes(source_map_dir, reading_source)
            concept_links = _read_concept_links(concepts_dir, reading_source)
        except _BUILDER_FAILURES as exc:
            _logger.warning(
                "reading-context-package build failed",
                extra={
                    "category": "rcp_build_failed",
                    "source_id": reading_source.source_id,
                    "error": str(exc),
                },
            )
            return ReadingContextPackage(
                source_id=reading_source.source_id,
                error=f"build_failed: {type(exc).__name__}: {exc!s}",
            )

        clusters = _cluster_annotations(raw_annotations)
        questions = _extract_questions(raw_annotations, clusters)
        outline_skeleton = _build_outline_skeleton(clusters)
        missing_piece_prompts = _build_missing_piece_prompts(
            clusters,
            source_quotes,
        )

        return ReadingContextPackage(
            source_id=reading_source.source_id,
            annotations=annotations_items,
            digest_excerpts=digest_excerpts,
            notes_excerpts=notes_excerpts,
            source_quotes=source_quotes,
            concept_links=concept_links,
            idea_clusters=clusters,
            questions=questions,
            outline_skeleton=outline_skeleton,
            missing_piece_prompts=missing_piece_prompts,
        )


# ── Step 1: digest excerpts ───────────────────────────────────────────────────


def _read_digest_excerpts(digest_path: Path) -> list[EvidenceItem]:
    """Read ``digest.md`` and emit one ``EvidenceItem`` per non-empty H2
    section. ``locator`` = ``"{digest_path}#<heading>"``.

    Missing file → ``[]`` (a missing digest is legitimate; a fresh source
    might have no digest yet). Other ``OSError`` propagates to the
    builder's narrow tuple.
    """
    if not digest_path.exists():
        return []
    content = digest_path.read_text(encoding="utf-8")
    sections = _split_h2_sections(content)
    items: list[EvidenceItem] = []
    for heading, body in sections:
        items.append(
            EvidenceItem(
                item_kind="annotation",
                locator=f"{digest_path.as_posix()}#{heading}",
                excerpt=_truncate_excerpt(body),
                source=f"digest · {heading}",
            )
        )
    return items


# ── Step 2: notes excerpts ────────────────────────────────────────────────────


def _read_notes_excerpts(notes_path: Path) -> list[EvidenceItem]:
    """Same shape as :func:`_read_digest_excerpts` for ``notes.md``."""
    if not notes_path.exists():
        return []
    content = notes_path.read_text(encoding="utf-8")
    sections = _split_h2_sections(content)
    items: list[EvidenceItem] = []
    for heading, body in sections:
        items.append(
            EvidenceItem(
                item_kind="annotation",
                locator=f"{notes_path.as_posix()}#{heading}",
                excerpt=_truncate_excerpt(body),
                source=f"notes · {heading}",
            )
        )
    return items


# ── Step 3: annotations ───────────────────────────────────────────────────────


def _read_annotations(
    loader: AnnotationLoader | None,
    annotations_path: Path,
    reading_source: ReadingSource,
) -> tuple[list[EvidenceItem], list[Annotation]]:
    """Load annotations for ``reading_source`` via the injected loader; if no
    loader was provided AND ``annotations_path`` exists, parse a JSON file
    keyed by the same shape (loader-less path is for fixture compatibility).

    Returns ``(EvidenceItem[], raw_annotations)``. The raw annotations are
    handed to clustering and question-extraction; the EvidenceItems are the
    user-visible package field.
    """
    raw: list[Annotation] = []
    if loader is not None:
        raw = list(loader(reading_source.source_id))
    elif annotations_path.exists():
        raw = _load_json_annotations(annotations_path)

    items: list[EvidenceItem] = []
    for ann in raw:
        # ``locator`` and ``text`` are the documented loader contract;
        # missing keys raise KeyError → caught by the narrow tuple.
        locator = str(ann["locator"])
        text = str(ann["text"])
        chapter_ref = ann.get("chapter_ref") or ann.get("chapter") or ""
        source_descriptor = f"annotation · {chapter_ref}" if chapter_ref else "annotation"
        items.append(
            EvidenceItem(
                item_kind="annotation",
                locator=locator,
                excerpt=_truncate_excerpt(text),
                source=source_descriptor,
            )
        )
    return items, raw


def _load_json_annotations(path: Path) -> list[Annotation]:
    """Load a JSON-encoded list of annotation dicts from ``path``.

    Used as a fixture-friendly fallback when no ``annotation_loader`` is
    injected. ``json.JSONDecodeError`` extends ``ValueError`` so it's caught
    by the builder's narrow tuple.
    """
    import json

    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError(f"annotations fixture must be a JSON list; got {type(data).__name__}")
    out: list[Annotation] = []
    for entry in data:
        if not isinstance(entry, dict):
            raise ValueError(
                f"annotations fixture entries must be objects; got {type(entry).__name__}"
            )
        out.append(entry)
    return out


# ── Step 4: source-quote anchors ──────────────────────────────────────────────


def _read_source_quotes(source_map_dir: Path, reading_source: ReadingSource) -> list[EvidenceItem]:
    """Walk ``{source_map_dir}/<source_slug>/*.md`` and emit one
    ``EvidenceItem(item_kind="source_quote", ...)`` per quote bullet line.

    Source slug derivation: the last segment of ``source_id`` after ``:`` (so
    ``ebook:alpha-book`` → ``alpha-book``, ``inbox:Inbox/kb/foo.md`` →
    ``foo.md``). The builder NEVER parses ``source_id`` beyond this slug
    extraction; per-source layout details live in #513 / #515 contracts.
    """
    slug = _extract_source_slug(reading_source.source_id)
    source_root = source_map_dir / slug
    if not source_root.exists():
        return []
    items: list[EvidenceItem] = []
    # Sort by name for deterministic iteration order across runs.
    for chapter_md in sorted(source_root.glob("*.md")):
        content = chapter_md.read_text(encoding="utf-8")
        # Skip front-matter; only need body bullets.
        try:
            _, body = _strict_load_frontmatter(content)
        except yaml.YAMLError:
            # Re-raise so the builder's narrow tuple records the failure;
            # silent skip would mask a malformed source page.
            raise
        for line in body.splitlines():
            match = _BULLET.match(line)
            if match is None:
                continue
            text = match.group(1).strip()
            # Quote bullets in source pages are conventionally fenced as
            # "> quote" or `"quote"` — accept both, but pass through the
            # raw text so tests can match on it.
            items.append(
                EvidenceItem(
                    item_kind="source_quote",
                    locator=f"{chapter_md.as_posix()}#L{_line_index(body, line)}",
                    excerpt=_truncate_excerpt(text),
                    source=f"{slug} · {chapter_md.stem}",
                )
            )
    return items


def _line_index(body: str, target_line: str) -> int:
    """Return 1-based line index of ``target_line`` within ``body``.
    Used to compose deterministic ``L<n>`` locators."""
    for idx, line in enumerate(body.splitlines(), start=1):
        if line == target_line:
            return idx
    return 0


def _extract_source_slug(source_id: str) -> str:
    """Last path segment of ``source_id`` after the ``:`` namespace prefix.

    Examples:
    - ``ebook:alpha-book`` → ``alpha-book``
    - ``inbox:Inbox/kb/foo.md`` → ``foo.md``
    - ``inbox:foo`` → ``foo``
    - ``foo`` → ``foo`` (degenerate; no namespace)
    """
    after_colon = source_id.split(":", 1)[-1]
    return after_colon.rsplit("/", 1)[-1] or after_colon


# ── Step 5: concept links ─────────────────────────────────────────────────────


def _read_concept_links(concepts_dir: Path, reading_source: ReadingSource) -> list[EvidenceItem]:
    """Walk ``concepts_dir/*.md``; for each concept page whose frontmatter
    ``mentioned_in`` list contains ``reading_source.source_id`` (or its
    slug), emit one ``concept_link`` ``EvidenceItem``.
    """
    if not concepts_dir.exists():
        return []
    slug = _extract_source_slug(reading_source.source_id)
    items: list[EvidenceItem] = []
    for concept_md in sorted(concepts_dir.glob("*.md")):
        content = concept_md.read_text(encoding="utf-8")
        try:
            fm, body = _strict_load_frontmatter(content)
        except yaml.YAMLError:
            # Surface as builder error per narrow tuple.
            raise
        mentioned_in = fm.get("mentioned_in") or []
        if not isinstance(mentioned_in, list):
            continue
        if not any(_mention_matches(m, reading_source.source_id, slug) for m in mentioned_in):
            continue
        # First non-empty body line as excerpt — concept page summary.
        excerpt_seed = ""
        for line in body.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                excerpt_seed = stripped
                break
        title = fm.get("title") or concept_md.stem
        items.append(
            EvidenceItem(
                item_kind="concept_link",
                locator=concept_md.as_posix(),
                excerpt=_truncate_excerpt(excerpt_seed),
                source=str(title),
            )
        )
    return items


def _mention_matches(mention: object, source_id: str, slug: str) -> bool:
    """Match a concept page's ``mentioned_in`` entry to a source.

    Accepts the full ``source_id`` or the slug — concept pages may store
    either form depending on how the canonicalizer wrote them.
    """
    if not isinstance(mention, str):
        return False
    return mention == source_id or mention == slug or mention.endswith(f":{slug}")


# ── Step 6: clustering ────────────────────────────────────────────────────────


def _cluster_annotations(
    annotations: list[Annotation],
) -> list[IdeaCluster]:
    """Cluster annotations by ``chapter_ref`` if available, otherwise by
    3-gram overlap on the annotation text. Deterministic — identical inputs
    produce identical output across runs.

    chapter_ref-based clustering: annotations with the same non-empty
    ``chapter_ref`` go into one cluster labeled with that ref.

    3-gram fallback: annotations without a ``chapter_ref`` are grouped by
    transitive 3-gram overlap (each annotation joins the first existing
    cluster it shares ≥1 3-gram with; otherwise starts a new cluster).
    Iteration order = annotation insertion order, so output is
    deterministic.
    """
    clusters_by_ref: dict[str, list[Annotation]] = {}
    no_ref: list[Annotation] = []
    for ann in annotations:
        chapter_ref = (ann.get("chapter_ref") or ann.get("chapter") or "").strip()
        if chapter_ref:
            clusters_by_ref.setdefault(chapter_ref, []).append(ann)
        else:
            no_ref.append(ann)

    out: list[IdeaCluster] = []

    # First emit chapter_ref clusters in alphabetical order of ref so the
    # cluster_ids are stable across runs.
    for ref in sorted(clusters_by_ref.keys()):
        cluster_anns = clusters_by_ref[ref]
        out.append(
            IdeaCluster(
                cluster_id=_cluster_id_from_ref(ref),
                label=ref,
                annotation_refs=[str(a["locator"]) for a in cluster_anns],
                claim_refs=[],
            )
        )

    # Then 3-gram fallback for the no_ref bucket.
    fallback_groups: list[list[Annotation]] = []
    for ann in no_ref:
        ngrams = _ngrams(str(ann.get("text") or ""))
        joined = False
        for group in fallback_groups:
            group_ngrams = set().union(*[_ngrams(str(g.get("text") or "")) for g in group])
            if ngrams & group_ngrams:
                group.append(ann)
                joined = True
                break
        if not joined:
            fallback_groups.append([ann])

    for idx, group in enumerate(fallback_groups, start=1):
        # Label: the shortest annotation text in the group (truncated) so
        # the heading is short and stable.
        seed = min(
            (str(g.get("text") or "") for g in group),
            key=lambda s: (len(s), s),
            default="",
        )
        label = _truncate_excerpt(seed, limit=60) or f"cluster {idx}"
        out.append(
            IdeaCluster(
                cluster_id=f"clu_fallback_{idx}",
                label=label,
                annotation_refs=[str(a["locator"]) for a in group],
                claim_refs=[],
            )
        )

    return out


def _cluster_id_from_ref(ref: str) -> str:
    """Deterministic cluster id derived from a chapter ref. Lower-case +
    non-alphanumeric → ``-``."""
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", ref).strip("-").lower()
    if not slug:
        slug = "ref"
    return f"clu_{slug}"


def _ngrams(text: str, *, n: int = 3) -> set[str]:
    """Return the set of n-grams in ``text``. Whitespace-collapsed,
    case-folded; n-grams cross language boundaries (works for both
    ASCII English and Chinese)."""
    if not text:
        return set()
    cleaned = re.sub(r"\s+", "", text).lower()
    if len(cleaned) < n:
        return {cleaned}
    return {cleaned[i : i + n] for i in range(len(cleaned) - n + 1)}


# ── Step 7: questions ─────────────────────────────────────────────────────────


def _extract_questions(
    annotations: list[Annotation],
    clusters: list[IdeaCluster],
) -> list[Question]:
    """Emit a ``Question`` per annotation tagged ``question``. Builder does
    NOT auto-generate new questions — it surfaces 修修's own.

    The question text is the annotation's ``text`` field; if it doesn't
    end with ``?`` / ``？`` the builder appends ``?`` so the resulting
    ``Question`` satisfies W5 at construct time. Empty-text annotations are
    dropped (they can't be a meaningful prompt).
    """
    locator_to_clusters: dict[str, list[str]] = {}
    for cluster in clusters:
        for locator in cluster.annotation_refs:
            locator_to_clusters.setdefault(locator, []).append(cluster.cluster_id)

    out: list[Question] = []
    counter = 0
    for ann in annotations:
        tags = ann.get("tags") or []
        if not isinstance(tags, list) or "question" not in tags:
            continue
        text = str(ann.get("text") or "").strip()
        if not text:
            continue
        if not text.rstrip().endswith(("?", "？")):
            text = f"{text}?"
        counter += 1
        locator = str(ann.get("locator") or f"q_{counter}")
        out.append(
            Question(
                question_id=f"q_{counter}",
                text=text,
                related_clusters=locator_to_clusters.get(locator, []),
            )
        )
    return out


# ── Step 8: outline skeleton ──────────────────────────────────────────────────


def _build_outline_skeleton(clusters: list[IdeaCluster]) -> OutlineSkeleton | None:
    """Section labels = cluster labels, ordered by annotation count desc
    then alphabetical. Returns ``None`` when no clusters exist (so an empty
    package surfaces ``outline_skeleton=None`` instead of an empty skeleton).
    """
    if not clusters:
        return None
    ordered = sorted(
        clusters,
        key=lambda c: (-len(c.annotation_refs), c.label),
    )
    return OutlineSkeleton(
        skeleton_id="outline_v1",
        section_labels=[c.label for c in ordered],
    )


# ── Step 9: missing-piece prompts ─────────────────────────────────────────────


def _build_missing_piece_prompts(
    clusters: list[IdeaCluster],
    source_quotes: list[EvidenceItem],
) -> list[MissingPiecePrompt]:
    """Emit a ``MissingPiecePrompt`` for each cluster that has annotations
    but no source-page evidence (no quote whose ``locator`` or ``source``
    references the cluster label).

    Phrasing: ``"<label>: 需要更多 evidence"`` — matches the Brief §4.3
    example. W6 (no terminal ``.``/``。``) is satisfied by the trailing
    word ``evidence``.
    """
    out: list[MissingPiecePrompt] = []
    for cluster in clusters:
        if not cluster.annotation_refs:
            continue
        has_quote = any(
            cluster.label in sq.locator or cluster.label in sq.source for sq in source_quotes
        )
        if has_quote:
            continue
        out.append(
            MissingPiecePrompt(
                prompt_id=f"miss_{cluster.cluster_id}",
                text=f"{cluster.label}: 需要更多 evidence",
            )
        )
    return out
