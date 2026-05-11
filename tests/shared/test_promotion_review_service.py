"""Behaviour tests for ``shared.promotion_review_service`` (ADR-024 Slice 8 / #516).

5 tests covering Brief §5 ST1-ST5 — record decision persistence, start_review
chain order, commit-approved filtering, plus subprocess gates for forbidden
imports (mirror of #515 T12/T13).

Tests use ``tempfile.TemporaryDirectory()`` for vault_root, in-memory dict
fakes for ``ManifestStore`` / ``SourceResolver`` / ``ClaimExtractor`` /
``ConceptMatcher`` / ``KBConceptIndex`` so the suite runs without LLM calls
or vault writes (Brief §6 boundaries 3 + 11).
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

from shared.concept_promotion_engine import ConceptPromotionEngine
from shared.promotion_commit import PromotionCommitService
from shared.promotion_preflight import PromotionPreflight
from shared.promotion_review_service import (
    FilesystemManifestStore,
    PromotionReviewService,
)
from shared.schemas.concept_promotion import MatchOutcome
from shared.schemas.promotion_manifest import (
    CanonicalMatch,
    PromotionManifest,
)
from shared.schemas.reading_source import ReadingSource, SourceVariant
from shared.schemas.source_map import ClaimExtractionResult, QuoteAnchor
from shared.source_map_builder import SourceMapBuilder

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "promotion_review"


# ── Fakes ─────────────────────────────────────────────────────────────────────


class _DictManifestStore:
    """In-memory ``ManifestStore`` for tests."""

    def __init__(self) -> None:
        self._store: dict[str, PromotionManifest] = {}

    def load(self, source_id):
        return self._store.get(source_id)

    def save(self, manifest):
        self._store[manifest.source_id] = manifest

    def list_source_ids(self):
        return list(self._store.keys())


class _DictResolver:
    def __init__(self, sources):
        self._sources = {rs.source_id: rs for rs in sources}

    def resolve(self, source_id):
        return self._sources.get(source_id)


class _CountingExtractor:
    """Records call order so ST2 can verify the chain ran preflight → builder → engine."""

    def __init__(self, call_log: list[str]):
        self._call_log = call_log

    def extract(self, chapter_text, chapter_title, primary_lang):
        self._call_log.append("extractor")
        return ClaimExtractionResult(
            claims=["claim a", "claim b", "claim c"],
            key_numbers=[],
            figure_summaries=[],
            table_summaries=[],
            short_quotes=[
                QuoteAnchor(
                    locator="L1-L1",
                    excerpt="Sample evidence excerpt for testing chain.",
                    confidence=0.8,
                ),
                QuoteAnchor(
                    locator="L2-L2",
                    excerpt="Another excerpt to drive concept extraction.",
                    confidence=0.8,
                ),
            ],
            extraction_confidence=0.8,
        )


class _NoneMatcher:
    """Always reports ``match_basis="none"`` — keeps engine output deterministic."""

    def __init__(self, call_log: list[str] | None = None):
        self._call_log = call_log

    def match(self, candidate, kb_index, primary_lang):
        if self._call_log is not None:
            self._call_log.append("matcher")
        return MatchOutcome(
            canonical_match=CanonicalMatch(
                match_basis="none", confidence=0.0, matched_concept_path=None
            ),
            conflict_signals=[],
        )


class _EmptyKBIndex:
    def lookup(self, alias):
        return None

    def aliases_starting_with(self, prefix):
        return []


# ── Helpers ───────────────────────────────────────────────────────────────────


def _build_service(
    *,
    manifest_store=None,
    sources=None,
    call_log=None,
    vault_root=None,
):
    """Compose a service with deterministic upstream wiring."""
    if manifest_store is None:
        manifest_store = _DictManifestStore()

    # blob_loader is required by both preflight and builder; tests don't
    # actually invoke them on real blobs in ST1 / ST3 / ST4 / ST5, but ST2
    # exercises the chain so we wire a real markdown blob loader keyed by
    # variant.path string.
    md_blob = (
        "---\nlang: en\ntitle: Sample\n---\n\n# Heading\n\nThis is a sample article body. " * 50
    ).encode("utf-8")

    def blob_loader(path: str) -> bytes:
        if path.endswith(".md"):
            return md_blob
        raise KeyError(path)

    preflight = PromotionPreflight(blob_loader=blob_loader)
    builder = SourceMapBuilder(blob_loader=blob_loader)
    concept_engine = ConceptPromotionEngine()
    commit_service = PromotionCommitService()
    extractor = _CountingExtractor(call_log if call_log is not None else [])
    matcher = _NoneMatcher(call_log)
    kb_index = _EmptyKBIndex()
    resolver = _DictResolver(sources or [])

    return PromotionReviewService(
        manifest_store=manifest_store,
        preflight=preflight,
        builder=builder,
        concept_engine=concept_engine,
        commit_service=commit_service,
        extractor=extractor,
        matcher=matcher,
        kb_index=kb_index,
        source_resolver=resolver,
    )


def _load_mixed_manifest() -> PromotionManifest:
    raw = (FIXTURE_DIR / "manifest_mixed_decisions.json").read_text(encoding="utf-8")
    return PromotionManifest.model_validate(json.loads(raw))


# ── ST1 — record_decision persists ────────────────────────────────────────────


def test_st1_service_record_decision_updates_manifest():
    """ST1: record_decision sets human_decision on item and saves manifest."""
    store = _DictManifestStore()
    manifest = _load_mixed_manifest()
    store.save(manifest)

    service = _build_service(manifest_store=store)
    updated = service.record_decision(
        source_id="ebook:alpha-book",
        item_id="concept_hrv_001",
        decision="approve",
        note="strong evidence across chapters",
    )

    target = next(it for it in updated.items if it.item_id == "concept_hrv_001")
    assert target.human_decision is not None
    assert target.human_decision.decision == "approve"
    assert target.human_decision.note == "strong evidence across chapters"

    # Persisted to store.
    reloaded = store.load("ebook:alpha-book")
    assert reloaded is not None
    target_again = next(it for it in reloaded.items if it.item_id == "concept_hrv_001")
    assert target_again.human_decision is not None
    assert target_again.human_decision.decision == "approve"


# ── ST2 — start_review chains preflight → builder → engine ────────────────────


def test_st2_service_start_review_chains_preflight_builder_engine():
    """ST2: start_review invokes preflight, then builder (extractor),
    then concept engine (matcher), and persists the resulting manifest.
    """
    rs = ReadingSource(
        source_id="inbox:Inbox/kb/sample.md",
        annotation_key="sample",
        kind="inbox_document",
        title="Sample",
        primary_lang="en",
        has_evidence_track=True,
        evidence_reason=None,
        variants=[
            SourceVariant(
                role="original",
                format="markdown",
                lang="en",
                path="Inbox/kb/sample.md",
            )
        ],
    )
    call_log: list[str] = []
    store = _DictManifestStore()
    service = _build_service(manifest_store=store, sources=[rs], call_log=call_log)

    manifest = service.start_review("inbox:Inbox/kb/sample.md")
    assert manifest.source_id == "inbox:Inbox/kb/sample.md"
    assert manifest.status == "needs_review"

    # Builder extractor was called BEFORE the engine matcher.
    assert "extractor" in call_log
    assert "matcher" in call_log
    assert call_log.index("extractor") < call_log.index("matcher")

    # Manifest persisted via the store.
    reloaded = store.load("inbox:Inbox/kb/sample.md")
    assert reloaded is not None
    assert reloaded.manifest_id == manifest.manifest_id


# ── ST2b — start_review refuses to overwrite a manifest with persisted state ──


def test_st2b_service_start_review_refuses_overwrite_when_decisions_exist():
    """Re-running ``/start`` on a source whose manifest already carries
    ``human_decision`` records (or commit batches) raises ``ValueError`` and
    leaves the existing manifest untouched. Brief §3 labels ``/start`` as
    "First-time start review"; this guard prevents reload / double-POST data
    loss until the explicit ``replaces_manifest_id`` flow lands.
    """
    store = _DictManifestStore()
    existing = _load_mixed_manifest()  # has human_decision on concept_hrv_001
    store.save(existing)

    rs = ReadingSource(
        source_id="ebook:alpha-book",
        annotation_key="alpha-book",
        kind="ebook",
        title="Alpha Book",
        primary_lang="en",
        has_evidence_track=True,
        evidence_reason=None,
        variants=[
            SourceVariant(
                role="original",
                format="markdown",
                lang="en",
                path="Inbox/kb/sample.md",
            )
        ],
    )
    service = _build_service(manifest_store=store, sources=[rs])

    import pytest

    with pytest.raises(ValueError, match="would overwrite a manifest"):
        service.start_review("ebook:alpha-book")

    # Existing manifest preserved — same manifest_id, same items.
    reloaded = store.load("ebook:alpha-book")
    assert reloaded is not None
    assert reloaded.manifest_id == existing.manifest_id
    assert any(it.human_decision is not None for it in reloaded.items)


# ── ST3 — commit_approved filters to approve-only ─────────────────────────────


def test_st3_service_commit_approved_filters_to_approve_only(tmp_path: Path):
    """ST3: manifest has approved + rejected + deferred + undecided items;
    commit_approved invokes the commit service with ONLY the approve ids.
    """

    class _RecordingCommit:
        """Stub commit service that captures item_ids and returns a stub
        successful outcome so the schema invariants hold."""

        def __init__(self):
            self.received_item_ids: list[str] | None = None

        def commit(self, manifest, batch_id, item_ids, vault_root, *, write_adapter=None):
            from shared.schemas.promotion_commit import CommitOutcome
            from shared.schemas.promotion_manifest import CommitBatch, TouchedFile

            self.received_item_ids = list(item_ids)
            touched = [
                TouchedFile(
                    path=f"KB/Wiki/Sources/alpha-book/chapter-{i + 1}.md",
                    operation="create",
                    before_hash=None,
                    after_hash="a" * 64,
                    backup_path=None,
                )
                for i, _ in enumerate(item_ids)
            ]
            batch = CommitBatch(
                batch_id=batch_id,
                created_at="2026-05-10T14:00:00Z",
                approved_item_ids=list(item_ids),
                deferred_item_ids=[],
                rejected_item_ids=[],
                touched_files=touched,
                errors=[],
                promotion_status="partial",
            )
            return CommitOutcome(batch=batch, acceptance_results=[], error=None)

    store = _DictManifestStore()
    manifest = _load_mixed_manifest()
    store.save(manifest)

    recording = _RecordingCommit()
    md_blob = b"---\nlang: en\n---\n\n# H\n\nbody"

    def blob_loader(path: str) -> bytes:
        return md_blob

    service = PromotionReviewService(
        manifest_store=store,
        preflight=PromotionPreflight(blob_loader=blob_loader),
        builder=SourceMapBuilder(blob_loader=blob_loader),
        concept_engine=ConceptPromotionEngine(),
        commit_service=recording,
        extractor=_CountingExtractor([]),
        matcher=_NoneMatcher(),
        kb_index=_EmptyKBIndex(),
    )

    outcome = service.commit_approved("ebook:alpha-book", "batch_001", tmp_path)

    # Only the one item with human_decision.decision == "approve" should
    # have been forwarded — src_ch1_001 in the fixture.
    assert recording.received_item_ids == ["src_ch1_001"]
    # Outcome batch landed in manifest.commit_batches.
    reloaded = store.load("ebook:alpha-book")
    assert reloaded is not None
    assert len(reloaded.commit_batches) == 1
    assert reloaded.commit_batches[0].batch_id == "batch_001"
    # Status reflects partial commit (some items still undecided).
    assert reloaded.status == "partial"
    assert outcome.error is None


# ── ST4 — subprocess gate: no shared.book_storage ─────────────────────────────


def test_st4_service_no_book_storage_import():
    """ST4: importing shared.promotion_review_service must NOT pull
    shared.book_storage into sys.modules. Mirrors #515 T12 pattern.
    """
    src = textwrap.dedent(
        """
        import sys
        import shared.promotion_review_service  # noqa: F401

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


# ── ST5 — subprocess gate: no LLM clients / fastapi / agents / thousand_sunny ──


def test_st5_service_no_llm_client_import():
    """ST5: importing shared.promotion_review_service must NOT pull LLM
    clients (anthropic / openai / google.generativeai), fastapi,
    thousand_sunny.*, or agents.* into sys.modules. Mirrors #515 T13 pattern.
    """
    src = textwrap.dedent(
        """
        import sys
        import shared.promotion_review_service  # noqa: F401

        offending = sorted(
            m
            for m in sys.modules
            if m.startswith((
                "fastapi",
                "thousand_sunny",
                "agents.",
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
        cwd=Path(__file__).resolve().parents[2],
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "OK" in result.stdout


# ── Filesystem store smoke ────────────────────────────────────────────────────


def test_filesystem_store_round_trip(tmp_path: Path):
    """Sanity: the default FilesystemManifestStore round-trips manifests
    keyed by base64url(source_id) — defends Brief §3 source_id encoding
    invariant on the persistence side.
    """
    store = FilesystemManifestStore(manifest_root=tmp_path)
    manifest = _load_mixed_manifest()
    store.save(manifest)

    listed = store.list_source_ids()
    assert listed == ["ebook:alpha-book"]

    reloaded = store.load("ebook:alpha-book")
    assert reloaded is not None
    assert reloaded.manifest_id == manifest.manifest_id
    assert reloaded.source_id == manifest.source_id
