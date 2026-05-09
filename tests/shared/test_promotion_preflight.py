"""Behavior tests for ``shared.promotion_preflight`` (ADR-024 Slice 3 / #511).

15 tests covering Brief §5:

- T1  high-quality ebook → proceed_full_promotion
- T2  high-quality inbox → proceed_full_promotion
- T3  bilingual-only inbox → defer + low-confidence lang
- T4  no-original ebook → defer
- T5  short + no evidence → annotation_only_sync
- T6  very short → skip
- T7  Pydantic invariant: proceed_full_promotion ⇒ has_evidence_track
- T8  lang normalization across BCP-47 inputs (high confidence)
- T9  inspector error → defer + error field populated
- T10 no vault writes (read-only assertion)
- T11 subprocess import gate — no LLM client modules
- T12 subprocess import gate — no fastapi / thousand_sunny / agents.*
- T13 reflective: no enumeration API (list_*, iter_*, enumerate_*)
- T14 subprocess import gate — no shared.book_storage
- T15 reflective: PreflightAction has no 'partial_promotion_only'

Tests inject in-memory dict-backed ``blob_loader`` callables so no real
filesystem / vault is touched (T10 asserts).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import typing
from pathlib import Path

import pytest
from pydantic import ValidationError

from shared.promotion_preflight import PromotionPreflight
from shared.schemas.preflight_report import (
    PreflightAction,
    PreflightReport,
    PreflightSizeSummary,
)
from shared.schemas.reading_source import ReadingSource, SourceVariant
from tests.shared._epub_fixtures import EPUBSpec, make_epub_blob

# ── Fixture builders ────────────────────────────────────────────────────────


def _ebook_source(
    *,
    book_id: str = "alpha-book",
    has_evidence_track: bool = True,
    primary_lang: str = "en",
    evidence_reason: str | None = None,
) -> ReadingSource:
    if has_evidence_track:
        variants = [
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
        ]
    else:
        variants = [
            SourceVariant(
                role="display",
                format="epub",
                lang=primary_lang,
                path=f"data/books/{book_id}/bilingual.epub",
            ),
        ]
    return ReadingSource(
        source_id=f"ebook:{book_id}",
        annotation_key=book_id,
        kind="ebook",
        title="Test Book",
        author="Anon",
        primary_lang=primary_lang,
        has_evidence_track=has_evidence_track,
        evidence_reason=evidence_reason,
        variants=variants,
        metadata={},
    )


def _inbox_source(
    *,
    relative_path: str = "Inbox/kb/foo.md",
    has_evidence_track: bool = True,
    primary_lang: str = "en",
    evidence_reason: str | None = None,
) -> ReadingSource:
    logical_original = relative_path
    if has_evidence_track:
        variants = [
            SourceVariant(
                role="original",
                format="markdown",
                lang=primary_lang,
                path=logical_original,
            ),
        ]
    else:
        # Bilingual-only: only the -bilingual sibling exists on disk.
        bilingual = logical_original[:-3] + "-bilingual.md"
        variants = [
            SourceVariant(
                role="display",
                format="markdown",
                lang="bilingual",
                path=bilingual,
            ),
        ]
    return ReadingSource(
        source_id=f"inbox:{logical_original}",
        annotation_key="foo",
        kind="inbox_document",
        title="Foo",
        author=None,
        primary_lang=primary_lang,
        has_evidence_track=has_evidence_track,
        evidence_reason=evidence_reason,
        variants=variants,
        metadata={},
    )


def _long_chapter(word_count: int, prefix: str = "word") -> str:
    """Build a chapter XHTML with ~``word_count`` whitespace-split tokens."""
    body = " ".join(f"{prefix}{i}" for i in range(word_count))
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Chapter</title></head>
<body><h1>Chapter</h1><p>{body}</p></body>
</html>
"""


def _long_inbox_md(word_count: int, *, lang: str = "en", title: str = "Test") -> bytes:
    """Build an inbox markdown body of ~``word_count`` tokens with three
    sections (so weak-toc doesn't fire)."""
    per_section = max(1, word_count // 3)
    section_body = " ".join(f"word{i}" for i in range(per_section))
    fm = f"---\nlang: {lang}\ntitle: {title}\n---\n"
    body = (
        f"# Section 1\n\n{section_body}\n\n"
        f"# Section 2\n\n{section_body}\n\n"
        f"# Section 3\n\n{section_body}\n"
    )
    return (fm + body).encode("utf-8")


def _dict_loader(mapping: dict[str, bytes]):
    """Build a blob_loader from a path→bytes dict; raise KeyError on miss."""

    def loader(path: str) -> bytes:
        if path not in mapping:
            raise FileNotFoundError(path)
        return mapping[path]

    return loader


# ── T1 — high-quality ebook ─────────────────────────────────────────────────


def test_preflight_high_quality_ebook():
    """T1: ebook with has_evidence_track=True, full TOC (3 chapters), large
    body (~5k words). Expect proceed_full_promotion + reasons=['ok']."""
    rs = _ebook_source(book_id="alpha-book", has_evidence_track=True, primary_lang="en")

    chapters = {f"ch{i}.xhtml": _long_chapter(2000, prefix=f"ch{i}word") for i in range(1, 4)}
    nav = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Contents</title></head>
<body>
<nav epub:type="toc" id="toc">
  <ol>
    <li><a href="ch1.xhtml">Chapter 1</a></li>
    <li><a href="ch2.xhtml">Chapter 2</a></li>
    <li><a href="ch3.xhtml">Chapter 3</a></li>
  </ol>
</nav>
</body>
</html>
"""
    blob = make_epub_blob(EPUBSpec(language="en", chapters=chapters, nav_xhtml=nav))
    loader = _dict_loader({"data/books/alpha-book/original.epub": blob})

    pf = PromotionPreflight(blob_loader=loader)
    report = pf.run(rs)

    assert report.recommended_action == "proceed_full_promotion"
    assert report.reasons == ["ok"]
    assert report.has_evidence_track is True
    assert report.error is None
    assert report.size.chapter_count == 3
    assert report.size.word_count_estimate >= 5000
    assert all(r.severity != "high" for r in report.risks)


# ── T2 — high-quality inbox ─────────────────────────────────────────────────


def test_preflight_high_quality_inbox():
    """T2: inbox doc with has_evidence_track=True, ~5k words across 3
    sections, full frontmatter. Expect proceed_full_promotion."""
    rs = _inbox_source(
        relative_path="Inbox/kb/foo.md",
        has_evidence_track=True,
        primary_lang="en",
    )
    loader = _dict_loader({"Inbox/kb/foo.md": _long_inbox_md(5000)})

    pf = PromotionPreflight(blob_loader=loader)
    report = pf.run(rs)

    assert report.recommended_action == "proceed_full_promotion"
    assert report.reasons == ["ok"]
    assert report.primary_lang_confidence == "high"


# ── T3 — bilingual-only inbox → defer + low-confidence lang ─────────────────


def test_preflight_bilingual_only_inbox_defaults_to_defer():
    """T3: inbox bilingual-only (has_evidence_track=False,
    evidence_reason='bilingual_only_inbox') with ~5k words. Expect defer +
    reasons containing 'missing_evidence_track' AND 'low_confidence_lang';
    primary_lang_confidence='low'."""
    rs = _inbox_source(
        relative_path="Inbox/kb/qux.md",
        has_evidence_track=False,
        primary_lang="en",
        evidence_reason="bilingual_only_inbox",
    )
    loader = _dict_loader({"Inbox/kb/qux-bilingual.md": _long_inbox_md(5000)})

    pf = PromotionPreflight(blob_loader=loader)
    report = pf.run(rs)

    assert report.recommended_action == "defer"
    assert "missing_evidence_track" in report.reasons
    assert "low_confidence_lang" in report.reasons
    assert report.primary_lang_confidence == "low"
    assert report.evidence_reason == "bilingual_only_inbox"


# ── T4 — no-original ebook → defer ──────────────────────────────────────────


def test_preflight_no_original_uploaded_defaults_to_defer():
    """T4: ebook with has_evidence_track=False (no_original_uploaded), large
    body. Expect defer + missing_evidence_track; lang confidence stays high
    (case (a) — bilingual EPUB has authoritative dc:language)."""
    rs = _ebook_source(
        book_id="beta-book",
        has_evidence_track=False,
        primary_lang="en",
        evidence_reason="no_original_uploaded",
    )
    chapters = {f"ch{i}.xhtml": _long_chapter(2000, prefix=f"ch{i}word") for i in range(1, 4)}
    nav = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Contents</title></head>
<body>
<nav epub:type="toc" id="toc">
  <ol>
    <li><a href="ch1.xhtml">Chapter 1</a></li>
    <li><a href="ch2.xhtml">Chapter 2</a></li>
    <li><a href="ch3.xhtml">Chapter 3</a></li>
  </ol>
</nav>
</body>
</html>
"""
    blob = make_epub_blob(EPUBSpec(language="en", chapters=chapters, nav_xhtml=nav))
    loader = _dict_loader({"data/books/beta-book/bilingual.epub": blob})

    pf = PromotionPreflight(blob_loader=loader)
    report = pf.run(rs)

    assert report.recommended_action == "defer"
    assert "missing_evidence_track" in report.reasons
    assert report.primary_lang_confidence == "high"


# ── T5 — short + no evidence → annotation_only_sync ─────────────────────────


def test_preflight_short_no_evidence_routes_to_annotation_only_sync():
    """T5: inbox doc with has_evidence_track=False, ~600 words (short range
    is 200..1000). Expect annotation_only_sync + reasons containing
    missing_evidence_track AND very_short."""
    rs = _inbox_source(
        relative_path="Inbox/kb/short.md",
        has_evidence_track=False,
        primary_lang="en",
        evidence_reason="bilingual_only_inbox",
    )
    loader = _dict_loader({"Inbox/kb/short-bilingual.md": _long_inbox_md(600)})

    pf = PromotionPreflight(blob_loader=loader)
    report = pf.run(rs)

    assert report.recommended_action == "annotation_only_sync"
    assert "missing_evidence_track" in report.reasons
    assert "very_short" in report.reasons


# ── T6 — very short → skip ──────────────────────────────────────────────────


def test_preflight_short_content_skips():
    """T6: inbox source ~150 words. Expect skip + reasons=['very_short']
    (skip dominates regardless of evidence)."""
    rs = _inbox_source(relative_path="Inbox/kb/tiny.md", has_evidence_track=True)
    loader = _dict_loader({"Inbox/kb/tiny.md": _long_inbox_md(150)})

    pf = PromotionPreflight(blob_loader=loader)
    report = pf.run(rs)

    assert report.recommended_action == "skip"
    assert report.reasons == ["very_short"]


# ── T7 — Pydantic invariant: proceed_full_promotion requires evidence ───────


def test_full_promotion_requires_evidence_track():
    """T7: constructing PreflightReport with proceed_full_promotion +
    has_evidence_track=False raises ValidationError mentioning
    'proceed_full_promotion'."""
    with pytest.raises(ValidationError, match="proceed_full_promotion"):
        PreflightReport(
            source_id="ebook:x",
            primary_lang="en",
            primary_lang_confidence="high",
            has_evidence_track=False,
            evidence_reason="no_original_uploaded",
            size=PreflightSizeSummary(
                chapter_count=10,
                word_count_estimate=50000,
                char_count_estimate=300000,
                rough_token_estimate=75000,
            ),
            risks=[],
            reasons=["ok"],
            recommended_action="proceed_full_promotion",
            error=None,
        )


# ── T8 — lang normalization (high-confidence cases) ─────────────────────────


@pytest.mark.parametrize("primary_lang", ["en", "en-US", "zh-Hant", "zh-CN"])
def test_preflight_lang_normalization_high_confidence(primary_lang):
    """T8: primary_lang variations with evidence_reason=None all yield
    primary_lang_confidence='high'."""
    rs = _inbox_source(
        relative_path="Inbox/kb/lang.md",
        has_evidence_track=True,
        primary_lang=primary_lang,
        evidence_reason=None,
    )
    loader = _dict_loader({"Inbox/kb/lang.md": _long_inbox_md(5000, lang=primary_lang)})

    pf = PromotionPreflight(blob_loader=loader)
    report = pf.run(rs)

    assert report.primary_lang_confidence == "high"
    assert report.primary_lang == primary_lang


# ── T9 — inspector error → defer ────────────────────────────────────────────


def test_preflight_inspector_error_falls_back_to_defer(caplog):
    """T9: blob_loader raises OSError → recommended_action='defer', error
    field populated, WARNING logged."""
    rs = _ebook_source(book_id="missing-book", has_evidence_track=True)

    def boom_loader(path: str) -> bytes:
        raise OSError(f"missing blob: {path}")

    caplog.set_level("WARNING", logger="nakama.shared.promotion_preflight")
    pf = PromotionPreflight(blob_loader=boom_loader)
    report = pf.run(rs)

    assert report.recommended_action == "defer"
    assert report.error is not None
    assert "blob_load_failed" in report.error
    assert any(
        getattr(rec, "category", None) == "preflight_ebook_load_failed" for rec in caplog.records
    )


# ── T10 — no vault writes ───────────────────────────────────────────────────


def test_preflight_no_kb_write(tmp_path: Path):
    """T10: running preflight against an in-memory loader against a tmp_path
    'vault' produces zero filesystem changes."""
    vault = tmp_path / "vault"
    vault.mkdir()
    inbox = vault / "Inbox" / "kb"
    inbox.mkdir(parents=True)

    before = sorted(p.relative_to(vault) for p in vault.rglob("*"))

    rs = _inbox_source(relative_path="Inbox/kb/foo.md", has_evidence_track=True)
    loader = _dict_loader({"Inbox/kb/foo.md": _long_inbox_md(5000)})

    pf = PromotionPreflight(blob_loader=loader)
    pf.run(rs)

    after = sorted(p.relative_to(vault) for p in vault.rglob("*"))
    assert before == after, f"vault mutated: before={before} after={after}"


# ── T11 — subprocess import gate: no LLM clients ────────────────────────────


def test_preflight_no_llm_client_import():
    """T11: importing shared.promotion_preflight must NOT pull anthropic /
    openai / google.generativeai into sys.modules — confirms preflight is
    deterministic per CONTEXT.md ('without heavy LLM spend')."""
    src = textwrap.dedent(
        """
        import sys
        import shared.promotion_preflight  # noqa: F401

        offending = sorted(
            m
            for m in sys.modules
            if m.startswith(("anthropic", "openai", "google.generativeai"))
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


# ── T12 — subprocess import gate: no fastapi / thousand_sunny / agents ──────


def test_preflight_no_fastapi_or_thousand_sunny_imports():
    """T12: importing shared.promotion_preflight must NOT pull fastapi,
    thousand_sunny, or agents.* into sys.modules — confirms reusability
    outside route handlers (mirrors #509 / #512 reusability gate)."""
    src = textwrap.dedent(
        """
        import sys
        import shared.promotion_preflight  # noqa: F401

        offending = sorted(
            m
            for m in sys.modules
            if m.startswith(("fastapi", "thousand_sunny", "agents."))
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


# ── T13 — no enumeration API on PromotionPreflight ──────────────────────────


def test_no_enumeration_api_exposed():
    """T13: dir(PromotionPreflight) must NOT contain any list_* / iter_* /
    enumerate_* members — per Brief §0 scope decision (enumeration is a
    separate slice via ReadingSourceRegistry extension)."""
    forbidden_prefixes = ("list_", "iter_", "enumerate_")
    offending = [
        name
        for name in dir(PromotionPreflight)
        if not name.startswith("_") and name.startswith(forbidden_prefixes)
    ]
    assert offending == [], f"PromotionPreflight exposes enumeration API: {offending}"


# ── T14 — subprocess import gate: no shared.book_storage ────────────────────


def test_preflight_no_book_storage_import():
    """T14: importing shared.promotion_preflight must NOT pull
    shared.book_storage into sys.modules — confirms Brief Correction 2
    (EPUB inspector reads via injected blob_loader, never via book_storage).
    """
    src = textwrap.dedent(
        """
        import sys
        import shared.promotion_preflight  # noqa: F401

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


# ── T15 — reflective: PreflightAction has no 'partial_promotion_only' ───────


def test_preflight_no_partial_promotion_only_in_action_enum():
    """T15: PreflightAction Literal must NOT contain 'partial_promotion_only'
    — per Brief Correction 1 (that state requires explicit human override
    and lives in #515 Commit Gate, not deterministic preflight)."""
    args = typing.get_args(PreflightAction)
    assert "partial_promotion_only" not in args, (
        f"PreflightAction must not contain 'partial_promotion_only'; found args={args}"
    )
    # Sanity: the 5 Brief-allowed values are present.
    expected = {
        "proceed_full_promotion",
        "proceed_with_warnings",
        "annotation_only_sync",
        "defer",
        "skip",
    }
    assert set(args) == expected, f"PreflightAction args mismatch: {set(args)} != {expected}"
