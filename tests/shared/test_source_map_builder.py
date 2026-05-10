"""Behavior tests for ``shared.source_map_builder`` (ADR-024 Slice 5 / #513).

17 tests covering Brief §5:

- T1  long-source ebook (5 chapters) → ≥5 items + 1 ``index`` overview
- T2  short-source markdown → 1 ``whole`` consolidated item
- T3  evidence anchors flow through; reason references first claim
- T4  deterministic fake extractor returns canned ClaimExtractionResult unchanged
- T5  subprocess gate — no LLM client modules pulled in
- T6  total emitted excerpt chars < 30% of inspected chapter chars (claim-dense)
- T7  per-excerpt cap honored (max_excerpt_chars=200 → no excerpt > 200)
- T8  has_evidence_track=False ⇒ ValueError at build() entry
- T9  extractor exception → SourceMapBuildResult(items=[], error="extractor_failed: ...")
- T10 blob_loader injection: called with variant.path exactly once per inspection
- T11 subprocess gate — no shared.book_storage import
- T12 subprocess gate — no fastapi / thousand_sunny / agents / LLM clients
- T13 chapter_ref unique within items
- T14 target_kb_path matches ``KB/Wiki/Sources/{slug}/{chapter_ref}.md`` (slug whitespace-safe)
- T15 extractor returning claims=[] → emits low_signal_count RiskFlag on item
- T16 markdown with 3 H2 sections → 3 items with chapter_ref sec-1/sec-2/sec-3
- T17 SourceMapBuildResult round-trips via model_dump / model_validate

Tests inject in-memory dict-backed ``blob_loader`` callables so no real
filesystem / vault is touched.
"""

from __future__ import annotations

import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from shared.schemas.promotion_manifest import EvidenceAnchor, RiskFlag
from shared.schemas.reading_source import ReadingSource, SourceVariant
from shared.schemas.source_map import (
    ClaimExtractionResult,
    QuoteAnchor,
    SourceMapBuildResult,
)
from shared.source_map_builder import SourceMapBuilder
from tests.shared._epub_fixtures import EPUBSpec, make_epub_blob

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "source_map"


# ── Fixture builders ────────────────────────────────────────────────────────


def _ebook_source(
    *,
    book_id: str = "alpha-book",
    primary_lang: str = "en",
) -> ReadingSource:
    return ReadingSource(
        source_id=f"ebook:{book_id}",
        annotation_key=book_id,
        kind="ebook",
        title="Test Book",
        author="Anon",
        primary_lang=primary_lang,
        has_evidence_track=True,
        evidence_reason=None,
        variants=[
            SourceVariant(
                role="original",
                format="epub",
                lang=primary_lang,
                path=f"data/books/{book_id}/original.epub",
            ),
            SourceVariant(
                role="display",
                format="epub",
                lang="bilingual",
                path=f"data/books/{book_id}/bilingual.epub",
            ),
        ],
        metadata={},
    )


def _ebook_source_no_evidence(
    *,
    book_id: str = "no-evidence-book",
) -> ReadingSource:
    """ReadingSource with has_evidence_track=False (only display variant)."""
    return ReadingSource(
        source_id=f"ebook:{book_id}",
        annotation_key=book_id,
        kind="ebook",
        title="No-Evidence Book",
        author=None,
        primary_lang="en",
        has_evidence_track=False,
        evidence_reason="no_original_uploaded",
        variants=[
            SourceVariant(
                role="display",
                format="epub",
                lang="bilingual",
                path=f"data/books/{book_id}/bilingual.epub",
            ),
        ],
        metadata={},
    )


def _inbox_source(
    *,
    relative_path: str = "Inbox/kb/foo.md",
    primary_lang: str = "en",
) -> ReadingSource:
    return ReadingSource(
        source_id=f"inbox:{relative_path}",
        annotation_key="foo",
        kind="inbox_document",
        title="Foo",
        author=None,
        primary_lang=primary_lang,
        has_evidence_track=True,
        evidence_reason=None,
        variants=[
            SourceVariant(
                role="original",
                format="markdown",
                lang=primary_lang,
                path=relative_path,
            ),
        ],
        metadata={},
    )


def _make_long_chapter(idx: int, word_count: int = 500) -> str:
    """Build a chapter XHTML with a clear ``<h1>`` so title extraction works
    even when the EPUB nav.xhtml uses a different label."""
    body = " ".join(f"ch{idx}word{i}" for i in range(word_count))
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Chapter {idx}</title></head>
<body><h1>Chapter {idx}</h1><p>{body}</p></body>
</html>
"""


def _make_long_epub_blob(num_chapters: int = 5, *, words_per_chapter: int = 500) -> bytes:
    """Build an EPUB with ``num_chapters`` chapters, each ``words_per_chapter``
    whitespace tokens. Default 5 chapters × 500 words ≈ 2500 words / ~16k
    chars — well above the long-source threshold (1500 × 3 = 4500 chars)."""
    chapters = {
        f"ch{i}.xhtml": _make_long_chapter(i, word_count=words_per_chapter)
        for i in range(1, num_chapters + 1)
    }
    nav_items = "\n    ".join(
        f'<li><a href="ch{i}.xhtml">Chapter {i}</a></li>' for i in range(1, num_chapters + 1)
    )
    nav = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Contents</title></head>
<body>
<nav epub:type="toc" id="toc">
  <ol>
    {nav_items}
  </ol>
</nav>
</body>
</html>
"""
    return make_epub_blob(EPUBSpec(language="en", chapters=chapters, nav_xhtml=nav))


def _dict_loader(mapping: dict[str, bytes]):
    """Build a blob_loader from a path→bytes dict; raise FileNotFoundError on miss.

    Returns the loader plus a list capturing every call's ``path`` arg so
    tests can assert call shape (T10).
    """
    calls: list[str] = []

    def loader(path: str) -> bytes:
        calls.append(path)
        if path not in mapping:
            raise FileNotFoundError(path)
        return mapping[path]

    loader.calls = calls  # type: ignore[attr-defined]
    return loader


# ── Fake extractor (deterministic; canonical for T1-T17) ────────────────────


class _CannedExtractor:
    """Returns the same ``ClaimExtractionResult`` for every chapter.

    Deterministic by construction — never inspects ``chapter_text``. Tests
    that need per-chapter variation use ``_PerChapterExtractor`` below.
    """

    def __init__(self, result: ClaimExtractionResult) -> None:
        self._result = result
        self.calls: list[tuple[str, str, str]] = []

    def extract(
        self, chapter_text: str, chapter_title: str, primary_lang: str
    ) -> ClaimExtractionResult:
        self.calls.append((chapter_text, chapter_title, primary_lang))
        return self._result


class _PerChapterExtractor:
    """Returns a different ``ClaimExtractionResult`` per chapter title."""

    def __init__(self, by_title: dict[str, ClaimExtractionResult]) -> None:
        self._by_title = by_title
        self.calls: list[tuple[str, str, str]] = []

    def extract(
        self, chapter_text: str, chapter_title: str, primary_lang: str
    ) -> ClaimExtractionResult:
        self.calls.append((chapter_text, chapter_title, primary_lang))
        if chapter_title in self._by_title:
            return self._by_title[chapter_title]
        return _empty_extraction()


class _RaisingExtractor:
    """Raises a documented exception (per ``_EXTRACTOR_FAILURES`` tuple)."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls: list[tuple[str, str, str]] = []

    def extract(
        self, chapter_text: str, chapter_title: str, primary_lang: str
    ) -> ClaimExtractionResult:
        self.calls.append((chapter_text, chapter_title, primary_lang))
        raise self._exc


def _full_extraction(*, prefix: str = "") -> ClaimExtractionResult:
    """A claim-rich extraction with quotes, claims, and figures — used as the
    default 'happy path' extractor return."""
    return ClaimExtractionResult(
        claims=[
            f"{prefix}Claim about hypothesis A backed by experimental data.",
            f"{prefix}Claim about mechanism B observed in the cohort.",
            f"{prefix}Claim about contraindication C for high-risk subjects.",
            f"{prefix}Claim about long-term outcome D over five years.",
            f"{prefix}Claim about methodological limitation E acknowledged.",
            f"{prefix}Claim about open question F for future work.",
        ],
        key_numbers=[f"{prefix}7.5 mmol/L", f"{prefix}0.42 SD"],
        figure_summaries=[f"{prefix}Figure 1: distribution of HRV by age band."],
        table_summaries=[f"{prefix}Table 1: nutrient breakdown across diets."],
        short_quotes=[
            QuoteAnchor(
                excerpt=f"{prefix}This is the first short quote excerpt for evidence.",
                locator=f"{prefix}cfi-1",
                confidence=0.9,
            ),
            QuoteAnchor(
                excerpt=f"{prefix}A second short quote backing the next claim.",
                locator=f"{prefix}cfi-2",
                confidence=0.85,
            ),
        ],
        extraction_confidence=0.8,
    )


def _empty_extraction() -> ClaimExtractionResult:
    """An extraction with zero claims and zero quotes — triggers
    ``low_signal_count`` risk + ``recommendation='defer'`` per Brief §4.2."""
    return ClaimExtractionResult(
        claims=[],
        key_numbers=[],
        figure_summaries=[],
        table_summaries=[],
        short_quotes=[],
        extraction_confidence=0.2,
    )


# ───────────────────────────────────────────────────────────────────────────
# T1 — long source: 5 chapters → ≥5 per-chapter items + 1 index overview
# ───────────────────────────────────────────────────────────────────────────


def test_build_long_source_emits_per_chapter_items():
    """T1: EPUB with 5 chapters → builder emits 6 items (1 index + 5 chapter)
    in the canonical order ``index`` first, then ``ch-1`` … ``ch-5``."""
    rs = _ebook_source(book_id="long-book")
    blob = _make_long_epub_blob(num_chapters=5, words_per_chapter=500)
    loader = _dict_loader({"data/books/long-book/original.epub": blob})

    builder = SourceMapBuilder(blob_loader=loader)
    extractor = _CannedExtractor(_full_extraction())
    result = builder.build(rs, extractor)

    assert result.error is None
    assert result.chapters_inspected == 5
    # 1 index + 5 chapters = 6 items.
    assert len(result.items) == 6
    refs = [item.chapter_ref for item in result.items]
    assert refs[0] == "index"
    assert refs[1:] == ["ch-1", "ch-2", "ch-3", "ch-4", "ch-5"]
    # Per-chapter items have evidence (extractor returned 2 quotes each).
    for item in result.items[1:]:
        assert item.recommendation == "include"
        assert len(item.evidence) >= 1


# ───────────────────────────────────────────────────────────────────────────
# T2 — short source: single inbox doc → 1 ``whole`` item
# ───────────────────────────────────────────────────────────────────────────


def test_build_short_source_emits_single_item():
    """T2: short markdown (no headings, well below long threshold) → exactly
    one item with ``chapter_ref='whole'``."""
    rs = _inbox_source(relative_path="Inbox/kb/short_no_headings.md")
    body = (FIXTURES_DIR / "short_no_headings.md").read_bytes()
    loader = _dict_loader({"Inbox/kb/short_no_headings.md": body})

    builder = SourceMapBuilder(blob_loader=loader)
    result = builder.build(rs, _CannedExtractor(_full_extraction()))

    assert result.error is None
    assert len(result.items) == 1
    assert result.items[0].chapter_ref == "whole"


# ───────────────────────────────────────────────────────────────────────────
# T3 — evidence anchors + reason synthesis
# ───────────────────────────────────────────────────────────────────────────


def test_build_includes_claims_and_evidence():
    """T3: emitted items have non-empty evidence list when extractor returned
    quotes; ``reason`` references the first claim."""
    rs = _ebook_source(book_id="evidence-book")
    blob = _make_long_epub_blob(num_chapters=3, words_per_chapter=500)
    loader = _dict_loader({"data/books/evidence-book/original.epub": blob})

    builder = SourceMapBuilder(blob_loader=loader)
    extraction = _full_extraction()
    result = builder.build(rs, _CannedExtractor(extraction))

    assert result.error is None
    # Skip the index overview; check per-chapter items.
    for item in result.items[1:]:
        assert len(item.evidence) >= 1
        assert all(isinstance(ev, EvidenceAnchor) for ev in item.evidence)
        # Reason should embed the first claim's distinctive text. The reason
        # is "<title>: <claim>" potentially truncated; the first claim text
        # contains "hypothesis A" so we check that distinctive substring is
        # present somewhere in the reason.
        assert "hypothesis A" in item.reason


# ───────────────────────────────────────────────────────────────────────────
# T4 — deterministic fake extractor threads through unchanged
# ───────────────────────────────────────────────────────────────────────────


def test_build_uses_deterministic_fake_extractor():
    """T4: the canned ClaimExtractionResult flows through to emitted items
    unchanged — confidence, evidence count, reason all derive from it."""
    rs = _ebook_source(book_id="canned-book")
    blob = _make_long_epub_blob(num_chapters=3, words_per_chapter=500)
    loader = _dict_loader({"data/books/canned-book/original.epub": blob})

    canned = _full_extraction()
    builder = SourceMapBuilder(blob_loader=loader)
    extractor = _CannedExtractor(canned)
    result = builder.build(rs, extractor)

    assert result.error is None
    # Extractor was called once per chapter (3 calls).
    assert len(extractor.calls) == 3
    # Per-chapter item confidence == extractor confidence.
    for item in result.items[1:]:
        assert item.confidence == pytest.approx(canned.extraction_confidence)
        # Evidence count ≤ len(short_quotes); when budget allows, equals.
        assert 1 <= len(item.evidence) <= len(canned.short_quotes)


# ───────────────────────────────────────────────────────────────────────────
# T5 — subprocess gate: no LLM client modules pulled in
# ───────────────────────────────────────────────────────────────────────────


def test_build_does_not_call_real_llm():
    """T5: subprocess assertion that ``shared.source_map_builder`` import does
    NOT pull anthropic / openai / claude_client / google.generativeai modules
    into ``sys.modules``."""
    src = textwrap.dedent(
        """
        import sys
        import shared.source_map_builder  # noqa: F401

        offending = sorted(
            m
            for m in sys.modules
            if m.startswith(("anthropic", "openai", "claude_client", "google.generativeai"))
        )
        if offending:
            print("OFFENDING:" + ",".join(offending))
            sys.exit(1)
        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", src],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "OK" in result.stdout


# ───────────────────────────────────────────────────────────────────────────
# T6 — claim-dense invariant: total excerpts < 30% of chapter chars
# ───────────────────────────────────────────────────────────────────────────


def test_build_excerpt_total_below_30pct_of_chapter():
    """T6: long-fixture run; sum of all emitted EvidenceAnchor.excerpt lengths
    across all items < 30% of total inspected chapter chars (B4 / claim-dense
    not mirror)."""
    rs = _ebook_source(book_id="dense-book")
    blob = _make_long_epub_blob(num_chapters=5, words_per_chapter=500)
    loader = _dict_loader({"data/books/dense-book/original.epub": blob})

    builder = SourceMapBuilder(blob_loader=loader)
    # Use full extraction with 2 short quotes per chapter — typical happy path.
    result = builder.build(rs, _CannedExtractor(_full_extraction()))

    assert result.error is None
    # Total chars of inspected chapter text — re-derive from the chapters in
    # the fixture by stripping XML and joining (mirrors builder logic).
    # Cheaper: just sum all evidence excerpts and assert against a generous
    # upper bound derived from the size of the EPUB body.
    total_excerpt_chars = sum(len(ev.excerpt) for item in result.items for ev in item.evidence)

    # Re-walk the EPUB body to compute total inspected chapter chars.
    from shared.source_map_builder import _extract_epub_spine_items, _strip_xml

    spine = _extract_epub_spine_items(blob)
    total_chapter_chars = sum(len(_strip_xml(xhtml)) for _, xhtml in spine)
    assert total_chapter_chars > 0

    ratio = total_excerpt_chars / total_chapter_chars
    assert ratio < 0.30, (
        f"emitted excerpts must be < 30% of chapter chars (claim-dense, "
        f"not full-text mirror); got {ratio:.2%} "
        f"({total_excerpt_chars} excerpts / {total_chapter_chars} chapter)"
    )


# ───────────────────────────────────────────────────────────────────────────
# T7 — per-excerpt cap honored
# ───────────────────────────────────────────────────────────────────────────


def test_build_excerpt_individual_length_capped():
    """T7: when ``max_excerpt_chars=200`` is passed, no emitted EvidenceAnchor
    excerpt exceeds 200 chars — even when the extractor returned a longer
    quote (truncation is the builder's job, B3)."""
    rs = _ebook_source(book_id="capped-book")
    blob = _make_long_epub_blob(num_chapters=3, words_per_chapter=500)
    loader = _dict_loader({"data/books/capped-book/original.epub": blob})

    # Extractor returns a deliberately-long quote (500 chars) so the builder
    # must truncate it at the user-supplied cap.
    long_quote = "x" * 500
    extraction = ClaimExtractionResult(
        claims=["A claim."] * 5,
        key_numbers=[],
        figure_summaries=[],
        table_summaries=[],
        short_quotes=[
            QuoteAnchor(excerpt=long_quote, locator="cfi-1", confidence=0.9),
        ],
        extraction_confidence=0.7,
    )

    builder = SourceMapBuilder(blob_loader=loader)
    result = builder.build(rs, _CannedExtractor(extraction), max_excerpt_chars=200)

    assert result.error is None
    for item in result.items:
        for ev in item.evidence:
            assert len(ev.excerpt) <= 200, (
                f"excerpt length {len(ev.excerpt)} exceeded max_excerpt_chars=200"
            )


# ───────────────────────────────────────────────────────────────────────────
# T8 — has_evidence_track=False ⇒ ValueError (B1)
# ───────────────────────────────────────────────────────────────────────────


def test_build_no_evidence_track_raises():
    """T8: ReadingSource(has_evidence_track=False) → builder raises
    ValueError at build() entry (B1). Caller must route to annotation-only
    sync via the Reading Overlay path, not Source Map Builder."""
    rs = _ebook_source_no_evidence(book_id="no-ev-book")
    builder = SourceMapBuilder(blob_loader=lambda _: b"")
    with pytest.raises(ValueError, match="has_evidence_track=True"):
        builder.build(rs, _CannedExtractor(_empty_extraction()))


# ───────────────────────────────────────────────────────────────────────────
# T9 — extractor failure → error state, items=[]
# ───────────────────────────────────────────────────────────────────────────


def test_build_extractor_failure_returns_error_state():
    """T9: extractor raises ValueError → SourceMapBuildResult(items=[],
    error="extractor_failed: ValueError(...)"). Programmer errors propagate;
    documented failure modes (ValueError, RuntimeError, OSError) are caught."""
    rs = _ebook_source(book_id="boom-book")
    blob = _make_long_epub_blob(num_chapters=3)
    loader = _dict_loader({"data/books/boom-book/original.epub": blob})

    builder = SourceMapBuilder(blob_loader=loader)
    extractor = _RaisingExtractor(ValueError("synthetic extractor failure"))
    result = builder.build(rs, extractor)

    assert result.items == []
    assert result.error is not None
    assert "extractor_failed" in result.error
    assert "ValueError" in result.error
    # chapters_inspected reflects what the inspector saw, even though
    # extraction blew up.
    assert result.chapters_inspected == 3


def test_build_extractor_runtime_error_caught():
    """T9b: RuntimeError is in the documented failure tuple."""
    rs = _ebook_source(book_id="runtime-book")
    blob = _make_long_epub_blob(num_chapters=2)
    loader = _dict_loader({"data/books/runtime-book/original.epub": blob})

    builder = SourceMapBuilder(blob_loader=loader)
    result = builder.build(rs, _RaisingExtractor(RuntimeError("oops")))
    assert result.items == []
    assert result.error is not None
    assert "RuntimeError" in result.error


def test_build_extractor_typeerror_propagates():
    """T9c: TypeError is a programmer error and must propagate (narrow tuple
    discipline per #511 F5 lesson)."""
    rs = _ebook_source(book_id="propagate-book")
    blob = _make_long_epub_blob(num_chapters=2)
    loader = _dict_loader({"data/books/propagate-book/original.epub": blob})

    builder = SourceMapBuilder(blob_loader=loader)
    with pytest.raises(TypeError):
        builder.build(rs, _RaisingExtractor(TypeError("intentional programmer bug")))


# ───────────────────────────────────────────────────────────────────────────
# T10 — blob_loader injection: called with variant.path
# ───────────────────────────────────────────────────────────────────────────


def test_build_blob_loader_injection():
    """T10: EPUB fixture; assert ``blob_loader`` was called with
    ``variant.path`` exactly once per inspection."""
    rs = _ebook_source(book_id="inject-book")
    blob = _make_long_epub_blob(num_chapters=3)
    loader = _dict_loader({"data/books/inject-book/original.epub": blob})

    builder = SourceMapBuilder(blob_loader=loader)
    builder.build(rs, _CannedExtractor(_full_extraction()))

    # Exactly one call with the original variant's path. Builder must not
    # touch the bilingual / display variant.
    assert loader.calls == ["data/books/inject-book/original.epub"]


# ───────────────────────────────────────────────────────────────────────────
# T11 — subprocess gate: no shared.book_storage import
# ───────────────────────────────────────────────────────────────────────────


def test_no_book_storage_import():
    """T11: importing ``shared.source_map_builder`` must NOT pull
    ``shared.book_storage`` into ``sys.modules`` — confirms B7 (builder reads
    via injected blob_loader, never via book_storage)."""
    src = textwrap.dedent(
        """
        import sys
        import shared.source_map_builder  # noqa: F401

        offending = sorted(
            m for m in sys.modules if m.startswith("shared.book_storage")
        )
        if offending:
            print("OFFENDING:" + ",".join(offending))
            sys.exit(1)
        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", src],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "OK" in result.stdout


# ───────────────────────────────────────────────────────────────────────────
# T12 — subprocess gate: no fastapi / thousand_sunny / agents / LLM clients
# ───────────────────────────────────────────────────────────────────────────


def test_no_runtime_imports_forbidden():
    """T12: importing ``shared.source_map_builder`` must NOT pull fastapi,
    thousand_sunny, agents.*, anthropic, openai, claude_client, or
    google.generativeai into ``sys.modules`` (B8 / Brief §6 boundaries)."""
    src = textwrap.dedent(
        """
        import sys
        import shared.source_map_builder  # noqa: F401

        offending = sorted(
            m
            for m in sys.modules
            if m.startswith((
                "fastapi",
                "thousand_sunny",
                "agents.",
                "anthropic",
                "openai",
                "claude_client",
                "google.generativeai",
            ))
        )
        if offending:
            print("OFFENDING:" + ",".join(offending))
            sys.exit(1)
        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", src],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "OK" in result.stdout


# ───────────────────────────────────────────────────────────────────────────
# T13 — chapter_ref unique within items (B5)
# ───────────────────────────────────────────────────────────────────────────


def test_build_chapter_ref_unique():
    """T13: long-fixture run; assert ``len({item.chapter_ref for item in items})
    == len(items)`` — builder must emit deterministic, distinct refs."""
    rs = _ebook_source(book_id="uniq-book")
    blob = _make_long_epub_blob(num_chapters=5, words_per_chapter=500)
    loader = _dict_loader({"data/books/uniq-book/original.epub": blob})

    builder = SourceMapBuilder(blob_loader=loader)
    result = builder.build(rs, _CannedExtractor(_full_extraction()))

    assert result.error is None
    refs = [item.chapter_ref for item in result.items]
    assert len(refs) == len(set(refs)), f"chapter_ref duplicates: {refs}"


# ───────────────────────────────────────────────────────────────────────────
# T14 — target_kb_path format
# ───────────────────────────────────────────────────────────────────────────


def test_build_target_kb_path_format():
    """T14: every emitted item has ``target_kb_path`` matching the canonical
    ``KB/Wiki/Sources/{slug}/{chapter_ref}.md`` shape; slug is whitespace-safe.

    Slug for a source_id like ``ebook:my book id`` should not contain raw
    spaces; the builder collapses whitespace + colons to hyphens."""
    rs = _ebook_source(book_id="My Book Id")
    blob = _make_long_epub_blob(num_chapters=3, words_per_chapter=500)
    loader = _dict_loader({"data/books/My Book Id/original.epub": blob})

    builder = SourceMapBuilder(blob_loader=loader)
    result = builder.build(rs, _CannedExtractor(_full_extraction()))

    assert result.error is None
    pattern = re.compile(r"^KB/Wiki/Sources/[A-Za-z0-9_\-]+/[A-Za-z0-9_\-]+\.md$")
    for item in result.items:
        assert item.target_kb_path is not None
        assert pattern.match(item.target_kb_path), (
            f"target_kb_path {item.target_kb_path!r} does not match canonical shape"
        )
        # No raw whitespace in the slug.
        assert " " not in item.target_kb_path


# ───────────────────────────────────────────────────────────────────────────
# T15 — extractor returning claims=[] → low_signal_count risk on item
# ───────────────────────────────────────────────────────────────────────────


def test_build_emits_low_signal_count_risk():
    """T15: when the extractor returns claims=[] for a chapter, the
    corresponding item carries a RiskFlag(code='low_signal_count')."""
    rs = _ebook_source(book_id="lowsig-book")
    blob = _make_long_epub_blob(num_chapters=3, words_per_chapter=500)
    loader = _dict_loader({"data/books/lowsig-book/original.epub": blob})

    builder = SourceMapBuilder(blob_loader=loader)
    result = builder.build(rs, _CannedExtractor(_empty_extraction()))

    assert result.error is None
    # Skip the index overview; check per-chapter items.
    for item in result.items[1:]:
        codes = [r.code for r in item.risk]
        assert "low_signal_count" in codes
        # And the risk objects are well-formed RiskFlag instances.
        assert all(isinstance(r, RiskFlag) for r in item.risk)


# ───────────────────────────────────────────────────────────────────────────
# T16 — markdown 3 H1 sections → 3 items sec-1/sec-2/sec-3
# ───────────────────────────────────────────────────────────────────────────


def test_build_inbox_section_split_on_headings():
    """T16: markdown with 3 H1 headings (long-source layout) → builder emits
    an ``index`` overview + 3 per-section items with ``chapter_ref`` ``sec-1``
    / ``sec-2`` / ``sec-3`` per Brief §4.2 step 3.

    Brief §5 wording uses 'H2' but the builder splits on H1+H2 (per Brief
    §4.2 step 3); H1 is exercised here. Body is constructed in-memory so we
    deterministically cross the long-source threshold (1500 chars × 3 = 4500
    chars) — the file fixture ``three_sections.md`` covers the heading-split
    path with shorter bodies that fall back to single ``whole`` consolidated
    output (covered by ``test_build_inbox_short_three_sections_collapses_to_whole``).
    """
    long_section_body = " ".join(f"word{i}" for i in range(2000))
    md = (
        "---\nlang: en\ntitle: Long Multi-Section\n---\n"
        f"# Section One\n\n{long_section_body}\n\n"
        f"# Section Two\n\n{long_section_body}\n\n"
        f"# Section Three\n\n{long_section_body}\n"
    )

    rs = _inbox_source(relative_path="Inbox/kb/long_three.md")
    loader = _dict_loader({"Inbox/kb/long_three.md": md.encode("utf-8")})

    extractor = _PerChapterExtractor(
        {
            "Section One": _full_extraction(prefix="s1-"),
            "Section Two": _full_extraction(prefix="s2-"),
            "Section Three": _full_extraction(prefix="s3-"),
        }
    )
    builder = SourceMapBuilder(blob_loader=loader)
    result = builder.build(rs, extractor)

    assert result.error is None
    refs = [item.chapter_ref for item in result.items]
    assert refs[0] == "index"
    assert refs[1:] == ["sec-1", "sec-2", "sec-3"]
    assert len(result.items) == 4


def test_build_inbox_short_three_sections_collapses_to_whole():
    """T16-companion: file-fixture ``three_sections.md`` has 3 H1 sections
    but body chars sit below the long-source threshold (1500 × 3 = 4500
    chars). Builder still splits on headings at inspection time, then the
    layout decision routes to the short-source path → single ``whole``
    consolidated item. Confirms file fixture is wired in correctly."""
    rs = _inbox_source(relative_path="Inbox/kb/three_sections.md")
    body = (FIXTURES_DIR / "three_sections.md").read_bytes()
    loader = _dict_loader({"Inbox/kb/three_sections.md": body})

    extractor = _PerChapterExtractor(
        {
            "Section One Heading": _full_extraction(prefix="s1-"),
            "Section Two Heading": _full_extraction(prefix="s2-"),
            "Section Three Heading": _full_extraction(prefix="s3-"),
        }
    )
    builder = SourceMapBuilder(blob_loader=loader)
    result = builder.build(rs, extractor)

    assert result.error is None
    # Builder inspected 3 candidates (one per H1) but layout chose short.
    assert result.chapters_inspected == 3
    refs = [item.chapter_ref for item in result.items]
    assert refs == ["whole"]


# ───────────────────────────────────────────────────────────────────────────
# T17 — SourceMapBuildResult round-trips
# ───────────────────────────────────────────────────────────────────────────


def test_build_result_round_trips():
    """T17: ``model_dump`` + ``model_validate`` identity holds on a
    representative result (long-source layout with evidence + risks)."""
    rs = _ebook_source(book_id="rt-book")
    blob = _make_long_epub_blob(num_chapters=4, words_per_chapter=500)
    loader = _dict_loader({"data/books/rt-book/original.epub": blob})

    builder = SourceMapBuilder(blob_loader=loader)
    result = builder.build(rs, _CannedExtractor(_full_extraction()))
    assert result.error is None

    # Dict round-trip
    raw_dict = result.model_dump()
    again = SourceMapBuildResult.model_validate(raw_dict)
    assert again == result

    # JSON round-trip
    raw_json = result.model_dump_json()
    again_json = SourceMapBuildResult.model_validate_json(raw_json)
    assert again_json == result

    # Invariants survive round-trip.
    assert again.schema_version == 1
    assert again.has_evidence_track is True
    assert again.chapters_inspected == 4


# ───────────────────────────────────────────────────────────────────────────
# Bonus regressions: build-level risk on EPUB with no nav
# ───────────────────────────────────────────────────────────────────────────


def test_build_emits_weak_toc_risk_on_empty_nav():
    """When the EPUB has no detectable TOC entries, the builder should attach
    a ``weak_toc`` build-level risk so caller can surface it on the manifest."""
    rs = _ebook_source(book_id="weaknav-book")
    # Build an EPUB with no nav.xhtml.
    chapters = {f"ch{i}.xhtml": _make_long_chapter(i, word_count=300) for i in range(1, 3)}
    blob = make_epub_blob(EPUBSpec(language="en", chapters=chapters, nav_xhtml=None))
    loader = _dict_loader({"data/books/weaknav-book/original.epub": blob})

    builder = SourceMapBuilder(blob_loader=loader)
    result = builder.build(rs, _CannedExtractor(_full_extraction()))

    # Chapter inspection still succeeds via spine; TOC weakness is a
    # build-level risk, not a hard failure.
    assert result.error is None
    codes = [r.code for r in result.risks]
    assert "weak_toc" in codes


def test_build_blob_load_failure_routes_to_error_state():
    """Loader raising OSError ⇒ SourceMapBuildResult(items=[], error='blob_load_failed: ...')."""
    rs = _ebook_source(book_id="missing-book")

    def boom(path: str) -> bytes:
        raise OSError(f"missing blob: {path}")

    builder = SourceMapBuilder(blob_loader=boom)
    result = builder.build(rs, _CannedExtractor(_full_extraction()))

    assert result.items == []
    assert result.error is not None
    assert "blob_load_failed" in result.error


def test_build_malformed_epub_routes_to_error_state():
    """Malformed EPUB (not a valid zip) → error state."""
    rs = _ebook_source(book_id="bad-book")
    loader = _dict_loader({"data/books/bad-book/original.epub": b"not a zip at all"})

    builder = SourceMapBuilder(blob_loader=loader)
    result = builder.build(rs, _CannedExtractor(_full_extraction()))

    assert result.items == []
    assert result.error is not None
    # Either epub_parse_failed or epub_body_failed depending on which step
    # fails first; both are acceptable.
    assert "epub_parse_failed" in result.error or "epub_body_failed" in result.error
