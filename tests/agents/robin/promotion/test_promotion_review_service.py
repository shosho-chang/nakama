"""Behaviour tests for ``agents.robin.promotion.promotion_review_service`` (ADR-024 Slice 8 / #516).

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

import pytest

from agents.robin.promotion.concept_promotion_engine import ConceptPromotionEngine
from agents.robin.promotion.promotion_commit import PromotionCommitService
from agents.robin.promotion.promotion_preflight import PromotionPreflight
from agents.robin.promotion.promotion_review_service import (
    CommitDisabledError,
    FilesystemManifestStore,
    PromotionReviewService,
)
from agents.robin.promotion.source_map_builder import SourceMapBuilder
from shared.schemas.concept_promotion import MatchOutcome
from shared.schemas.promotion_manifest import (
    CanonicalMatch,
    PromotionManifest,
)
from shared.schemas.reading_source import ReadingSource, SourceVariant
from shared.schemas.source_map import ClaimExtractionResult, QuoteAnchor

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "promotion_review"


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
        source_id="inbox:Inbox/web/sample.md",
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
                path="Inbox/web/sample.md",
            )
        ],
    )
    call_log: list[str] = []
    store = _DictManifestStore()
    service = _build_service(manifest_store=store, sources=[rs], call_log=call_log)

    manifest = service.start_review("inbox:Inbox/web/sample.md")
    assert manifest.source_id == "inbox:Inbox/web/sample.md"
    assert manifest.status == "needs_review"

    # Builder extractor was called BEFORE the engine matcher.
    assert "extractor" in call_log
    assert "matcher" in call_log
    assert call_log.index("extractor") < call_log.index("matcher")

    # Manifest persisted via the store.
    reloaded = store.load("inbox:Inbox/web/sample.md")
    assert reloaded is not None
    assert reloaded.manifest_id == manifest.manifest_id


# ── ST2c — Entity pipeline wiring (ADR-034 v2 PR4) ─────────────────────────


def test_st2c_entity_pipeline_optional_default_off_keeps_v1_manifest():
    """When entity components are NOT injected, start_review behaves
    identically to pre-PR4 — manifest stays schema_version=1, no entity
    items appended."""
    rs = ReadingSource(
        source_id="inbox:Inbox/web/sample-c1.md",
        annotation_key="sample-c1",
        kind="inbox_document",
        title="Sample C1",
        primary_lang="en",
        has_evidence_track=True,
        evidence_reason=None,
        variants=[
            SourceVariant(
                role="original", format="markdown", lang="en", path="Inbox/web/sample-c1.md"
            )
        ],
    )
    service = _build_service(sources=[rs])
    manifest = service.start_review("inbox:Inbox/web/sample-c1.md")
    assert manifest.schema_version == 1
    assert not any(it.item_kind == "entity" for it in manifest.items)


def test_st2c_entity_pipeline_partial_wiring_raises():
    """Constructor refuses partial wiring — three of four components
    is silently dropped entity items or crashes mid-flow."""
    from agents.robin.promotion.dry_run_entity_matcher import DryRunEntityMatcher
    from agents.robin.promotion.entity_extractor import DryRunEntityExtractor
    from agents.robin.promotion.entity_promotion_engine import EntityPromotionEngine

    with pytest.raises(ValueError, match="entity pipeline requires all of"):
        PromotionReviewService(
            manifest_store=_DictManifestStore(),
            preflight=PromotionPreflight(blob_loader=lambda p: b""),
            builder=SourceMapBuilder(blob_loader=lambda p: b""),
            concept_engine=ConceptPromotionEngine(),
            commit_service=PromotionCommitService(),
            extractor=_CountingExtractor([]),
            matcher=_NoneMatcher(None),
            kb_index=_EmptyKBIndex(),
            entity_engine=EntityPromotionEngine(),
            entity_extractor=DryRunEntityExtractor(),
            entity_matcher=DryRunEntityMatcher(),
            # kb_entity_index missing → partial wiring → ValueError
        )


def test_st2c_entity_pipeline_wired_appends_entity_items_and_bumps_schema_version():
    """When all four entity components are wired AND extractor returns
    candidates, start_review appends EntityReviewItem entries and bumps
    schema_version to 2 (V12 invariant)."""
    from agents.robin.promotion.dry_run_entity_matcher import DryRunEntityMatcher
    from agents.robin.promotion.entity_promotion_engine import EntityPromotionEngine
    from shared.schemas.entity_promotion import EntityCandidate
    from shared.schemas.promotion_manifest import PersonMetadata

    class _FixedEntityExtractor:
        """Returns one fixed Person candidate — exercises the wiring."""

        def extract(self, reading_source, source_map):  # noqa: ARG002, ANN001
            return [
                EntityCandidate(
                    candidate_id="cand_huberman",
                    entity_type="person",
                    label="Andrew Huberman",
                    evidence_language="en",
                    source_refs=["ch-1"],
                    raw_quotes=["Huberman discussed dopamine."],
                    metadata=PersonMetadata(affiliation="Stanford"),
                )
            ]

    class _EmptyKBEntityIndex:
        def lookup(self, alias):  # noqa: ARG002, ANN001
            return None

        def aliases_starting_with(self, prefix):  # noqa: ARG002, ANN001
            return []

    rs = ReadingSource(
        source_id="inbox:Inbox/web/sample-c2.md",
        annotation_key="sample-c2",
        kind="inbox_document",
        title="Sample C2",
        primary_lang="en",
        has_evidence_track=True,
        evidence_reason=None,
        variants=[
            SourceVariant(
                role="original", format="markdown", lang="en", path="Inbox/web/sample-c2.md"
            )
        ],
    )

    md_blob = (
        "---\nlang: en\ntitle: Sample\n---\n\n# Heading\n\nThis is a sample article body. " * 50
    ).encode("utf-8")

    def blob_loader(path: str) -> bytes:
        return md_blob

    store = _DictManifestStore()
    service = PromotionReviewService(
        manifest_store=store,
        preflight=PromotionPreflight(blob_loader=blob_loader),
        builder=SourceMapBuilder(blob_loader=blob_loader),
        concept_engine=ConceptPromotionEngine(),
        commit_service=PromotionCommitService(),
        extractor=_CountingExtractor([]),
        matcher=_NoneMatcher([]),
        kb_index=_EmptyKBIndex(),
        source_resolver=_DictResolver([rs]),
        entity_engine=EntityPromotionEngine(),
        entity_extractor=_FixedEntityExtractor(),
        entity_matcher=DryRunEntityMatcher(),
        kb_entity_index=_EmptyKBEntityIndex(),
    )

    manifest = service.start_review("inbox:Inbox/web/sample-c2.md")
    assert manifest.schema_version == 2
    entity_items = [it for it in manifest.items if it.item_kind == "entity"]
    assert len(entity_items) == 1
    entity_item = entity_items[0]
    assert entity_item.entity_label == "Andrew Huberman"
    # DryRunEntityMatcher returns match_basis="none" → row 7 → create_entity.
    assert entity_item.action == "create_entity"


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
                path="Inbox/web/sample.md",
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


# ── ST3b — commit disabled refuses to write ──────────────────────────────────


def test_st3b_commit_disabled_raises_and_skips_write(tmp_path: Path):
    """commit_enabled=False (placeholder-only pipeline pre-N519): commit_approved
    raises CommitDisabledError, never calls the commit service, leaves the
    manifest untouched."""

    class _RecordingCommit:
        def __init__(self):
            self.called = False

        def commit(self, *args, **kwargs):
            self.called = True
            raise AssertionError("commit must not be called when disabled")

    store = _DictManifestStore()
    store.save(_load_mixed_manifest())
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
        commit_enabled=False,
        extractor=_CountingExtractor([]),
        matcher=_NoneMatcher(),
        kb_index=_EmptyKBIndex(),
    )

    assert service.commit_enabled is False
    with pytest.raises(CommitDisabledError):
        service.commit_approved("ebook:alpha-book", "batch_001", tmp_path)
    assert recording.called is False
    reloaded = store.load("ebook:alpha-book")
    assert reloaded is not None
    assert reloaded.commit_batches == []


def test_commit_enabled_defaults_true():
    """commit_enabled defaults to True for backward compatibility."""
    md_blob = b"---\nlang: en\n---\n\n# H\n\nbody"

    def blob_loader(path: str) -> bytes:
        return md_blob

    service = PromotionReviewService(
        manifest_store=_DictManifestStore(),
        preflight=PromotionPreflight(blob_loader=blob_loader),
        builder=SourceMapBuilder(blob_loader=blob_loader),
        concept_engine=ConceptPromotionEngine(),
        commit_service=PromotionCommitService(),
        extractor=_CountingExtractor([]),
        matcher=_NoneMatcher(),
        kb_index=_EmptyKBIndex(),
    )
    assert service.commit_enabled is True


# ── ST4 — subprocess gate: no shared.book_storage ─────────────────────────────


def test_st4_service_no_book_storage_import():
    """ST4: importing agents.robin.promotion.promotion_review_service must NOT pull
    shared.book_storage into sys.modules. Mirrors #515 T12 pattern.
    """
    src = textwrap.dedent(
        """
        import sys
        import agents.robin.promotion.promotion_review_service  # noqa: F401

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
        cwd=Path(__file__).resolve().parents[4],
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "OK" in result.stdout


# ── ST5 — subprocess gate: no LLM clients / fastapi / agents / thousand_sunny ──


def test_st5_service_no_llm_client_import():
    """ST5: importing agents.robin.promotion.promotion_review_service must NOT pull LLM
    clients (anthropic / openai / google.generativeai), fastapi,
    thousand_sunny.*, or agents.* into sys.modules. Mirrors #515 T13 pattern.
    """
    src = textwrap.dedent(
        """
        import sys
        import agents.robin.promotion.promotion_review_service  # noqa: F401

        offending = sorted(
            m
            for m in sys.modules
            if (
                m.startswith((
                    "fastapi",
                    "thousand_sunny",
                    "anthropic",
                    "openai",
                    "google.generativeai",
                    "google.genai",
                ))
                # ADR-050: own bounded package is allowed; any OTHER agents
                # module (e.g. agents.robin.agent / ingest) is still forbidden.
                or (
                    m.startswith("agents")
                    and m not in ("agents", "agents.robin", "agents.robin.promotion")
                    and not m.startswith("agents.robin.promotion.")
                )
            )
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
        cwd=Path(__file__).resolve().parents[4],
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "OK" in result.stdout


# ── Filesystem store smoke ────────────────────────────────────────────────────


# ── ADR-035 §D6 / PR3b-i — start_review dispatch by reading_source.kind ──────


def _make_vtt(lines: list[tuple[str, str, str]]) -> bytes:
    out = ["WEBVTT", ""]
    for start, end, text in lines:
        out.append(f"{start} --> {end}")
        out.append(text)
        out.append("")
    return "\n".join(out).encode("utf-8")


def _build_video_service(
    *,
    manifest_store=None,
    sources=None,
    vtt_blob: bytes | None = None,
):
    """Compose a service whose blob_loader serves VTT bytes for video
    variants. Concept extractor / matcher are still wired but should NEVER
    be invoked on a video flow (PR3b-i dispatch contract)."""
    if manifest_store is None:
        manifest_store = _DictManifestStore()
    if vtt_blob is None:
        vtt_blob = _make_vtt(
            [
                ("00:00:00.000", "00:00:04.000", "Welcome to the show. " * 50),
                ("00:00:04.000", "00:00:08.000", "Today we cover sleep. " * 50),
                ("00:00:08.000", "00:00:12.000", "And caffeine. " * 50),
            ]
        )

    def blob_loader(path: str) -> bytes:
        if path.endswith(".vtt"):
            return vtt_blob
        raise KeyError(path)

    preflight = PromotionPreflight(blob_loader=blob_loader)
    builder = SourceMapBuilder(blob_loader=blob_loader)
    concept_engine = ConceptPromotionEngine()
    commit_service = PromotionCommitService()
    extractor = _CountingExtractor([])
    matcher = _NoneMatcher()
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


def _video_reading_source(video_id: str = "abcDEF12345") -> ReadingSource:
    return ReadingSource(
        schema_version=2,
        source_id=f"youtube:{video_id}",
        annotation_key=f"youtube_{video_id}",
        kind="youtube_video",
        title="Test Episode",
        primary_lang="en",
        has_evidence_track=True,
        evidence_reason=None,
        variants=[
            SourceVariant(
                role="original",
                format="vtt",
                lang="en",
                path=f"Watchlist/youtube/{video_id}/transcript.vtt",
            )
        ],
        cast=["Host", "Guest"],
    )


def _stub_video_build_result(rs: ReadingSource):
    """Return a deterministic SourceMapBuildResult mimicking
    build_video_source_map output — one include item with two
    timestamp_range anchors."""
    from shared.schemas.promotion_manifest import EvidenceAnchor, SourcePageReviewItem
    from shared.schemas.source_map import SourceMapBuildResult

    item = SourcePageReviewItem(
        item_id="abcDEF12345::whole",
        recommendation="include",
        action="create",
        reason="Test Episode: 2 annotations",
        evidence=[
            EvidenceAnchor(
                kind="timestamp_range",
                source_path=rs.variants[0].path,
                locator="t=4",
                excerpt="Today we cover sleep.",
                confidence=1.0,
            ),
            EvidenceAnchor(
                kind="timestamp_range",
                source_path=rs.variants[0].path,
                locator="t=8",
                excerpt="And caffeine.",
                confidence=1.0,
            ),
        ],
        risk=[],
        confidence=1.0,
        source_importance=0.5,
        reader_salience=0.0,
        target_kb_path="KB/Wiki/Sources/abcDEF12345/whole.md",
        chapter_ref="whole",
    )
    return SourceMapBuildResult(
        schema_version=2,
        source_id=rs.source_id,
        primary_lang=rs.primary_lang,
        has_evidence_track=True,
        chapters_inspected=1,
        items=[item],
        risks=[],
        error=None,
    )


def test_start_review_youtube_video_uses_video_builder_skips_concept_and_extractor(monkeypatch):
    """ADR-035 PR3b-i: youtube_video sources go through build_video_source_map;
    the LLM ClaimExtractor and ConceptPromotionEngine are NOT invoked."""
    rs = _video_reading_source()
    store = _DictManifestStore()
    service = _build_video_service(manifest_store=store, sources=[rs])

    calls: list[str] = []

    def _fake_build_video(reading_source):
        calls.append("video_builder")
        assert reading_source.source_id == rs.source_id
        return _stub_video_build_result(reading_source)

    import agents.robin.promotion.promotion_review_service as svc_mod

    monkeypatch.setattr(svc_mod, "build_video_source_map", _fake_build_video)
    monkeypatch.setattr(
        svc_mod.ConceptPromotionEngine,
        "propose",
        lambda *a, **kw: pytest.fail("concept engine must not run for video"),
    )

    manifest = service.start_review(rs.source_id)
    assert calls == ["video_builder"]
    assert manifest.source_id == rs.source_id
    assert manifest.schema_version == 2  # timestamp anchors require v=2
    source_page = [it for it in manifest.items if it.item_kind == "source_page"]
    concepts = [it for it in manifest.items if it.item_kind == "concept"]
    entities = [it for it in manifest.items if it.item_kind == "entity"]
    assert len(source_page) == 1
    assert source_page[0].chapter_ref == "whole"
    assert concepts == []
    assert entities == []
    assert all(a.kind == "timestamp_range" for a in source_page[0].evidence)
    # Manifest persisted via store.
    reloaded = store.load(rs.source_id)
    assert reloaded is not None
    assert reloaded.manifest_id == manifest.manifest_id


def test_start_review_youtube_video_surfaces_builder_error_as_value_error(monkeypatch):
    rs = _video_reading_source()
    service = _build_video_service(sources=[rs])

    from shared.schemas.source_map import SourceMapBuildResult

    def _fake_build_video(reading_source):
        return SourceMapBuildResult(
            schema_version=2,
            source_id=reading_source.source_id,
            primary_lang=reading_source.primary_lang,
            has_evidence_track=True,
            chapters_inspected=0,
            items=[],
            risks=[],
            error="annotation_store_load_failed: simulated",
        )

    import agents.robin.promotion.promotion_review_service as svc_mod

    monkeypatch.setattr(svc_mod, "build_video_source_map", _fake_build_video)

    with pytest.raises(ValueError, match="video source_map build failed"):
        service.start_review(rs.source_id)


def test_start_review_non_video_still_uses_legacy_builder_and_concept_engine():
    """Regression: PR3b-i dispatch must not break the inbox/ebook path —
    ClaimExtractor + ConceptPromotionEngine continue to run."""
    rs = ReadingSource(
        source_id="inbox:Inbox/web/sample.md",
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
                path="Inbox/web/sample.md",
            )
        ],
    )
    call_log: list[str] = []
    service = _build_service(sources=[rs], call_log=call_log)

    manifest = service.start_review(rs.source_id)
    assert "extractor" in call_log
    assert "matcher" in call_log
    assert manifest.source_id == rs.source_id


def test_start_review_youtube_video_with_entity_pipeline_emits_speaker_persons(monkeypatch):
    """ADR-035 PR4: when the entity pipeline is wired and rs is a video,
    speaker chips on annotations surface as EntityReviewItem entries
    alongside the SourcePageReviewItem from the video builder."""
    from agents.robin.promotion.dry_run_entity_matcher import DryRunEntityMatcher
    from agents.robin.promotion.entity_promotion_engine import EntityPromotionEngine
    from shared.schemas.entity_promotion import EntityCandidate
    from shared.schemas.promotion_manifest import PersonMetadata

    class _SpeakerExtractor:
        """Stand-in for VideoSpeakerEntityExtractor — returns two Person
        candidates so this test stays focused on service dispatch."""

        def extract(self, reading_source, source_map):  # noqa: ARG002, ANN001
            return [
                EntityCandidate(
                    candidate_id="speaker-00",
                    entity_type="person",
                    label="Host",
                    evidence_language="en",
                    source_refs=["t=15", "t=305"],
                    raw_quotes=["Host cue one."],
                    metadata=PersonMetadata(),
                ),
                EntityCandidate(
                    candidate_id="speaker-01",
                    entity_type="person",
                    label="Guest",
                    evidence_language="en",
                    source_refs=["t=120"],
                    raw_quotes=["Guest cue."],
                    metadata=PersonMetadata(),
                ),
            ]

    class _EmptyKBEntityIndex:
        def lookup(self, alias):  # noqa: ARG002, ANN001
            return None

        def aliases_starting_with(self, prefix):  # noqa: ARG002, ANN001
            return []

    rs = ReadingSource(
        schema_version=2,
        source_id="youtube:abcDEF12345",
        annotation_key="youtube_abcDEF12345",
        kind="youtube_video",
        title="Test Episode",
        primary_lang="en",
        has_evidence_track=True,
        evidence_reason=None,
        variants=[
            SourceVariant(
                role="original",
                format="vtt",
                lang="en",
                path="Watchlist/youtube/abcDEF12345/transcript.vtt",
            )
        ],
        cast=["Host", "Guest"],
    )

    vtt_blob = (
        b"WEBVTT\n\n"
        b"00:00:00.000 --> 00:00:04.000\n" + (b"Welcome to the show. " * 50) + b"\n\n"
        b"00:00:04.000 --> 00:00:08.000\n" + (b"Today we cover sleep. " * 50) + b"\n\n"
        b"00:00:08.000 --> 00:00:12.000\n" + (b"And caffeine. " * 50) + b"\n\n"
    )

    def blob_loader(path: str) -> bytes:
        if path.endswith(".vtt"):
            return vtt_blob
        raise KeyError(path)

    # Stub video builder so the test doesn't need annotation_store
    # round-tripping for the builder side — PR3a-ii covers that.
    def _fake_build_video(reading_source):
        from shared.schemas.promotion_manifest import EvidenceAnchor, SourcePageReviewItem
        from shared.schemas.source_map import SourceMapBuildResult

        item = SourcePageReviewItem(
            item_id=f"{reading_source.source_id.split(':', 1)[1]}::whole",
            recommendation="include",
            action="create",
            reason="Test Episode: 2 annotations",
            evidence=[
                EvidenceAnchor(
                    kind="timestamp_range",
                    source_path=reading_source.variants[0].path,
                    locator="t=15",
                    excerpt="Host cue one.",
                    confidence=1.0,
                ),
            ],
            risk=[],
            confidence=1.0,
            source_importance=0.5,
            reader_salience=0.0,
            target_kb_path="KB/Wiki/Sources/abcDEF12345/whole.md",
            chapter_ref="whole",
        )
        return SourceMapBuildResult(
            schema_version=2,
            source_id=reading_source.source_id,
            primary_lang=reading_source.primary_lang,
            has_evidence_track=True,
            chapters_inspected=1,
            items=[item],
            risks=[],
            error=None,
        )

    import agents.robin.promotion.promotion_review_service as svc_mod

    monkeypatch.setattr(svc_mod, "build_video_source_map", _fake_build_video)

    store = _DictManifestStore()
    service = PromotionReviewService(
        manifest_store=store,
        preflight=PromotionPreflight(blob_loader=blob_loader),
        builder=SourceMapBuilder(blob_loader=blob_loader),
        concept_engine=ConceptPromotionEngine(),
        commit_service=PromotionCommitService(),
        extractor=_CountingExtractor([]),
        matcher=_NoneMatcher([]),
        kb_index=_EmptyKBIndex(),
        source_resolver=_DictResolver([rs]),
        entity_engine=EntityPromotionEngine(),
        entity_extractor=_SpeakerExtractor(),
        entity_matcher=DryRunEntityMatcher(),
        kb_entity_index=_EmptyKBEntityIndex(),
    )

    manifest = service.start_review(rs.source_id)
    assert manifest.schema_version == 2

    source_pages = [it for it in manifest.items if it.item_kind == "source_page"]
    concepts = [it for it in manifest.items if it.item_kind == "concept"]
    entities = [it for it in manifest.items if it.item_kind == "entity"]

    assert len(source_pages) == 1
    assert concepts == []  # concept engine still skipped for video
    assert [e.entity_label for e in entities] == ["Host", "Guest"]
    assert all(e.metadata.entity_type == "person" for e in entities)


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
