"""Behaviour tests for ``agents.robin.reading_context_package`` (ADR-024 Slice 9 / #517).

12 tests covering Brief §5 BT1-BT12:

- BT1  digest excerpts: H2 sections → EvidenceItem entries.
- BT2  notes excerpts: same shape as digest.
- BT3  annotations via injected loader → EvidenceItem entries.
- BT4  source quotes: chapter pages with quote bullets → quotes extracted.
- BT5  concept links: concepts with mentioned_in referencing the source → links.
- BT6  determinism: rerun produces identical model_dump.
- BT7  questions: annotations tagged ``question`` → Question entries with terminal '?'.
- BT8  outline skeleton: section_labels mirror cluster labels (no auto content).
- BT9  missing-piece prompts: cluster with no source-page evidence → prompt.
- BT10 subprocess: importing builder does NOT pull LLM clients.
- BT11 subprocess: importing builder does NOT pull shared.book_storage.
- BT12 round trips: model_dump + model_validate identity.

Tests use the fixture ``tests/fixtures/reading_context/`` for digest, notes,
annotations, source map, and concepts. The annotation loader is injected
inline so the builder is exercised both via the loader path AND the
JSON-fixture path.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from agents.robin.reading_context_package import ReadingContextPackageBuilder
from shared.schemas.reading_context_package import ReadingContextPackage
from shared.schemas.reading_source import ReadingSource, SourceVariant

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "reading_context"
REPO_ROOT = Path(__file__).resolve().parents[2]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _alpha_source() -> ReadingSource:
    return ReadingSource(
        source_id="ebook:alpha-book",
        annotation_key="alpha-book",
        kind="ebook",
        title="Alpha Book",
        primary_lang="en",
        has_evidence_track=True,
        variants=[
            SourceVariant(
                role="original",
                format="epub",
                lang="en",
                path="data/books/alpha-book/original.epub",
            )
        ],
    )


def _load_fixture_annotations() -> list[dict]:
    raw = (FIXTURE_DIR / "annotations.json").read_text(encoding="utf-8")
    return json.loads(raw)


def _build_with_fixture(
    builder: ReadingContextPackageBuilder | None = None,
) -> ReadingContextPackage:
    """Run the builder against the standard fixture set."""
    annotations = _load_fixture_annotations()
    if builder is None:
        builder = ReadingContextPackageBuilder(
            annotation_loader=lambda _source_id: annotations,
        )
    return builder.build(
        _alpha_source(),
        digest_path=FIXTURE_DIR / "digest.md",
        notes_path=FIXTURE_DIR / "notes.md",
        annotations_path=FIXTURE_DIR / "annotations.json",
        source_map_dir=FIXTURE_DIR / "source_map",
        concepts_dir=FIXTURE_DIR / "concepts",
    )


# ── BT1 — digest excerpts ────────────────────────────────────────────────────


def test_bt1_build_aggregates_digest_excerpts():
    package = _build_with_fixture()
    assert package.error is None
    # Fixture digest.md has 3 H2 sections (ch-1 / ch-3 / ch-5).
    assert len(package.digest_excerpts) == 3
    headings = [item.locator.split("#", 1)[1] for item in package.digest_excerpts]
    assert headings == ["ch-1", "ch-3", "ch-5"]
    for item in package.digest_excerpts:
        assert item.item_kind == "annotation"
        assert item.excerpt
        assert len(item.excerpt) <= 200


# ── BT2 — notes excerpts ─────────────────────────────────────────────────────


def test_bt2_build_aggregates_notes_excerpts():
    package = _build_with_fixture()
    assert len(package.notes_excerpts) == 3
    sources = [item.source for item in package.notes_excerpts]
    assert all(s.startswith("notes · ") for s in sources)


# ── BT3 — annotations via loader ────────────────────────────────────────────


def test_bt3_build_aggregates_annotations_via_injected_loader():
    captured: list[str] = []

    def _loader(source_id: str):
        captured.append(source_id)
        return _load_fixture_annotations()

    builder = ReadingContextPackageBuilder(annotation_loader=_loader)
    package = _build_with_fixture(builder)
    # Loader was called exactly once with the source_id (Brief §3 — loader is
    # injected; builder does not parse the source_id beyond passing it
    # through).
    assert captured == ["ebook:alpha-book"]
    # Fixture has 5 annotations.
    assert len(package.annotations) == 5
    locators = [item.locator for item in package.annotations]
    assert locators == [
        "anno:ebook:alpha-book:cfi-001",
        "anno:ebook:alpha-book:cfi-002",
        "anno:ebook:alpha-book:cfi-003",
        "anno:ebook:alpha-book:cfi-004",
        "anno:ebook:alpha-book:cfi-005",
    ]


# ── BT4 — source quotes from chapter pages ──────────────────────────────────


def test_bt4_build_aggregates_source_quotes():
    package = _build_with_fixture()
    # ch-1 page: 3 claim bullets; ch-3 page: 2 claim bullets → 5 quotes.
    assert len(package.source_quotes) == 5
    assert all(item.item_kind == "source_quote" for item in package.source_quotes)
    # Locators reference the chapter pages with deterministic L<n> suffixes.
    line_suffixes = tuple(f"#L{n}" for n in range(1, 12))
    assert all(item.locator.endswith(line_suffixes) for item in package.source_quotes)
    # Excerpt content includes the bulleted claim text.
    excerpts = [item.excerpt for item in package.source_quotes]
    assert any("HRV reflects" in e for e in excerpts)
    assert any("acute-to-chronic" in e for e in excerpts)


# ── BT5 — concept links ──────────────────────────────────────────────────────


def test_bt5_build_aggregates_concept_links():
    package = _build_with_fixture()
    # heart_rate_variability.md has mentioned_in: [ebook:alpha-book]; the
    # unrelated concept does NOT include alpha-book.
    assert len(package.concept_links) == 1
    link = package.concept_links[0]
    assert link.item_kind == "concept_link"
    assert link.source == "Heart Rate Variability"
    assert link.locator.endswith("heart_rate_variability.md")


# ── BT6 — clustering determinism ─────────────────────────────────────────────


def test_bt6_build_clusters_annotations_deterministically():
    package_a = _build_with_fixture()
    package_b = _build_with_fixture()
    assert package_a.model_dump() == package_b.model_dump()
    # And clustering produced expected chapter-ref clusters.
    cluster_labels = [c.label for c in package_a.idea_clusters]
    assert "ch-1" in cluster_labels
    assert "ch-3" in cluster_labels


# ── BT7 — questions from tagged annotations ─────────────────────────────────


def test_bt7_build_extracts_questions_from_tagged_annotations():
    package = _build_with_fixture()
    # Fixture has 2 annotations tagged ``question``.
    assert len(package.questions) == 2
    for q in package.questions:
        assert q.text.endswith(("?", "？"))
    # Each question's related_clusters must include the chapter-ref cluster
    # that owns the underlying annotation (BT8 covers cluster shape itself).
    cluster_ids = {c.cluster_id for c in package.idea_clusters}
    for q in package.questions:
        assert all(rel in cluster_ids for rel in q.related_clusters)


# ── BT8 — outline skeleton ──────────────────────────────────────────────────


def test_bt8_build_outline_skeleton_lists_cluster_labels():
    package = _build_with_fixture()
    skeleton = package.outline_skeleton
    assert skeleton is not None
    cluster_labels = [c.label for c in package.idea_clusters]
    assert sorted(skeleton.section_labels) == sorted(cluster_labels)
    # No injected content under each label — the schema only carries labels.
    for label in skeleton.section_labels:
        # Heading-shaped — no terminal punctuation.
        assert not label.rstrip().endswith((".", "。", "!", "?"))


# ── BT9 — missing-piece prompts ─────────────────────────────────────────────


def test_bt9_build_missing_piece_prompts_when_evidence_gap():
    """Add an extra annotation with a chapter ref that has no source page.
    The builder should emit a missing-piece prompt for that cluster.
    """
    base = _load_fixture_annotations()
    extra = base + [
        {
            "locator": "anno:ebook:alpha-book:cfi-extra-1",
            "text": "ch-7 mentions a recovery framework that we have no source page for yet",
            "chapter_ref": "ch-7",
            "tags": ["highlight"],
        }
    ]
    builder = ReadingContextPackageBuilder(annotation_loader=lambda _: extra)
    package = builder.build(
        _alpha_source(),
        digest_path=FIXTURE_DIR / "digest.md",
        notes_path=FIXTURE_DIR / "notes.md",
        annotations_path=FIXTURE_DIR / "annotations.json",
        source_map_dir=FIXTURE_DIR / "source_map",
        concepts_dir=FIXTURE_DIR / "concepts",
    )
    prompts = [p.text for p in package.missing_piece_prompts]
    assert any("ch-7" in t for t in prompts)
    # Brief §4.3 phrasing.
    assert any("需要更多 evidence" in t for t in prompts)
    # W6: missing-piece prompts must NOT end with terminal '.' / '。'.
    for prompt in package.missing_piece_prompts:
        assert not prompt.text.rstrip().endswith((".", "。"))


# ── BT10 — subprocess: no LLM clients ───────────────────────────────────────


def test_bt10_build_no_llm_call():
    """Importing the builder must NOT pull anthropic / openai /
    google.generativeai into sys.modules."""
    src = textwrap.dedent(
        """
        import sys
        import agents.robin.reading_context_package  # noqa: F401

        offending = sorted(
            m
            for m in sys.modules
            if m.startswith((
                "anthropic",
                "openai",
                "google.generativeai",
                "google.genai",
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
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "OK" in result.stdout


# ── BT11 — subprocess: no shared.book_storage ──────────────────────────────


def test_bt11_build_no_book_storage_import():
    """Importing the builder must NOT pull shared.book_storage into
    sys.modules. Mirrors #515 / #516 subprocess gate."""
    src = textwrap.dedent(
        """
        import sys
        import agents.robin.reading_context_package  # noqa: F401

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
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "OK" in result.stdout


# ── BT12 — round-trip ────────────────────────────────────────────────────────


def test_bt12_build_round_trips():
    package = _build_with_fixture()
    dumped = package.model_dump()
    reloaded = ReadingContextPackage.model_validate(dumped)
    assert reloaded.model_dump() == dumped
    # And JSON round-trip works (handles Pydantic-specific serialization).
    json_payload = package.model_dump_json()
    reloaded_from_json = ReadingContextPackage.model_validate_json(json_payload)
    assert reloaded_from_json.model_dump() == dumped


# ── Extra negative — error envelope when a path is unreadable ───────────────


def test_build_returns_error_envelope_when_concept_yaml_malformed(tmp_path: Path):
    """Sanity check on the F1-analog invariant: malformed concept frontmatter
    produces an error envelope with all aggregated lists empty."""
    bad_concepts = tmp_path / "concepts"
    bad_concepts.mkdir()
    (bad_concepts / "broken.md").write_text(
        "---\nmentioned_in: [unterminated\n---\nbody\n",
        encoding="utf-8",
    )
    builder = ReadingContextPackageBuilder(annotation_loader=lambda _: _load_fixture_annotations())
    package = builder.build(
        _alpha_source(),
        digest_path=FIXTURE_DIR / "digest.md",
        notes_path=FIXTURE_DIR / "notes.md",
        annotations_path=FIXTURE_DIR / "annotations.json",
        source_map_dir=FIXTURE_DIR / "source_map",
        concepts_dir=bad_concepts,
    )
    assert package.error is not None
    assert package.annotations == []
    assert package.digest_excerpts == []
    assert package.notes_excerpts == []
    assert package.source_quotes == []
    assert package.concept_links == []
    assert package.idea_clusters == []
    assert package.questions == []
    assert package.missing_piece_prompts == []
    assert package.outline_skeleton is None


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch):
    """Strip any env vars that could otherwise pull production wiring."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
