"""Behavior tests for ``shared.promotion_commit`` (ADR-024 Slice 7 / #515).

15 tests covering Brief §5 T1-T15 — partial commit, hash mismatch, gate
failure, resumability, idempotency, status transitions, render fidelity,
adversarial path attempts, and subprocess-gated forbidden imports.

Tests use ``tempfile.TemporaryDirectory()`` for vault_root (T9 / Brief §6
boundary 1 — no real-vault writes). Subprocess gates (T12 / T13) shell out
to ``sys.executable`` to assert ``shared.promotion_commit`` /
``shared.promotion_acceptance_gate`` import surfaces stay clean.

Idempotency choice (T15): re-running ``commit()`` with the same item_id
where the rendered content hash matches the prior batch's after_hash is
still treated as a fresh write — the gate's G4 hash check passes (current
== prior_after_hash), and the commit pipeline records ``operation="update"``
with before_hash == after_hash (file content didn't actually change because
the renderer is deterministic). This avoids a special-case "skip" code path
and surfaces idempotent re-runs as zero-net-change touched files.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

from shared.promotion_commit import (
    FilesystemKbWriteAdapter,
    PromotionCommitService,
)
from shared.promotion_renderer import render_concept_page, render_source_page
from shared.schemas.promotion_commit import CommitOutcome
from shared.schemas.promotion_manifest import (
    CanonicalMatch,
    CommitBatch,
    ConceptReviewItem,
    EvidenceAnchor,
    HumanDecision,
    PromotionManifest,
    RecommenderMetadata,
    SourcePageReviewItem,
    TouchedFile,
)

# ── Fixture helpers ───────────────────────────────────────────────────────────

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "promotion_commit"


def _load_5_item_manifest() -> PromotionManifest:
    """Load the canonical 5-item fixture used by T1 / T8."""
    raw = (FIXTURE_DIR / "manifest_with_5_items.json").read_text(encoding="utf-8")
    return PromotionManifest.model_validate(json.loads(raw))


def _approved_decision() -> HumanDecision:
    return HumanDecision(
        decision="approve",
        decided_at="2026-05-10T13:00:00Z",
        decided_by="tester",
    )


def _evidence_clean() -> list[EvidenceAnchor]:
    return [
        EvidenceAnchor(
            kind="chapter_quote",
            source_path="data/books/x/original.epub",
            locator="epubcfi(/6/4[ch1]!/4/2/16/1:0,/6/4[ch1]!/4/2/16/1:200)",
            excerpt="HRV reflects autonomic balance.",
            confidence=0.9,
        )
    ]


def _src_item(
    *,
    item_id: str,
    target_kb_path: str,
    chapter_ref: str = "ch-1",
    human_decision: HumanDecision | None = None,
) -> SourcePageReviewItem:
    return SourcePageReviewItem(
        item_id=item_id,
        recommendation="include",
        action="create",
        reason="Reason text.",
        evidence=_evidence_clean(),
        risk=[],
        confidence=0.9,
        source_importance=0.9,
        reader_salience=0.8,
        target_kb_path=target_kb_path,
        chapter_ref=chapter_ref,
        human_decision=human_decision if human_decision is not None else _approved_decision(),
    )


def _build_manifest(
    *,
    items: list[SourcePageReviewItem | ConceptReviewItem],
    manifest_id: str = "mfst_test_001",
) -> PromotionManifest:
    return PromotionManifest(
        manifest_id=manifest_id,
        source_id="ebook:test-book",
        created_at="2026-05-10T12:00:00Z",
        status="needs_review",
        recommender=RecommenderMetadata(
            model_name="claude-opus-4-7",
            model_version="2026-04",
            recommended_at="2026-05-10T12:00:00Z",
        ),
        items=items,
        commit_batches=[],
    )


# ── T1 — partial writes approved items ─────────────────────────────────────


def test_commit_partial_writes_approved_items(tmp_path: Path):
    """T1: Manifest has 5 items, 3 approved → 3 written, batch.approved=3,
    deferred=0, rejected=2."""
    manifest = _load_5_item_manifest()
    service = PromotionCommitService()

    item_ids = [item.item_id for item in manifest.items]
    outcome = service.commit(manifest, "batch_001", item_ids, tmp_path)

    assert outcome.error is None
    assert len(outcome.batch.approved_item_ids) == 3
    assert len(outcome.batch.rejected_item_ids) == 2
    assert outcome.batch.deferred_item_ids == []
    # 3 source-page-or-concept files materialized.
    assert len(outcome.batch.touched_files) == 3

    # Verify the 3 approved targets exist on disk.
    for tf in outcome.batch.touched_files:
        assert (tmp_path / tf.path).exists()


# ── T2 — touched files have hashes ──────────────────────────────────────────


def test_commit_records_touched_files_with_hashes(tmp_path: Path):
    """T2: After commit, batch.touched_files entries have non-None
    after_hash; for create ops before_hash is None; for update ops both set."""
    item = _src_item(
        item_id="src_001",
        target_kb_path="KB/Wiki/Sources/test/ch1.md",
    )
    manifest = _build_manifest(items=[item])
    service = PromotionCommitService()

    outcome = service.commit(manifest, "batch_001", ["src_001"], tmp_path)
    assert len(outcome.batch.touched_files) == 1
    tf = outcome.batch.touched_files[0]
    assert tf.operation == "create"
    assert tf.before_hash is None
    assert tf.after_hash is not None
    assert len(tf.after_hash) == 64  # sha256 hex


# ── T3 — backup on update ──────────────────────────────────────────────────


def test_commit_creates_backup_on_update(tmp_path: Path):
    """T3: Pre-existing file → backup made; backup_path recorded on TouchedFile."""
    target = "KB/Wiki/Sources/test/ch1.md"
    full_target = tmp_path / target
    full_target.parent.mkdir(parents=True)
    full_target.write_text("OLD CONTENT", encoding="utf-8")

    item = _src_item(item_id="src_001", target_kb_path=target)
    manifest = _build_manifest(items=[item])

    # Manifest needs to record the prior after_hash so gate G4 passes; we
    # simulate by adding a CommitBatch with a TouchedFile carrying the
    # current file's hash.
    import hashlib

    prior_hash = hashlib.sha256(b"OLD CONTENT").hexdigest()
    manifest.commit_batches.append(
        CommitBatch(
            batch_id="batch_prior",
            created_at="2026-05-10T11:00:00Z",
            approved_item_ids=["src_001"],
            deferred_item_ids=[],
            rejected_item_ids=[],
            touched_files=[
                TouchedFile(
                    path=target,
                    operation="create",
                    before_hash=None,
                    after_hash=prior_hash,
                    backup_path=None,
                )
            ],
            errors=[],
            promotion_status="partial",
        )
    )
    # Bump manifest status to satisfy V3/V11 (commit_batches non-empty).
    object.__setattr__(manifest, "status", "partial")

    service = PromotionCommitService()
    outcome = service.commit(manifest, "batch_002", ["src_001"], tmp_path)

    assert outcome.error is None
    assert len(outcome.batch.touched_files) == 1
    tf = outcome.batch.touched_files[0]
    assert tf.operation == "update"
    assert tf.before_hash == prior_hash
    assert tf.after_hash is not None
    assert tf.before_hash != tf.after_hash  # content changed
    assert tf.backup_path is not None
    # backup file should exist on disk
    assert (tmp_path / tf.backup_path).exists()
    backup_content = (tmp_path / tf.backup_path).read_bytes()
    assert backup_content == b"OLD CONTENT"


# ── T4 — gate failure ──────────────────────────────────────────────────────


def test_commit_skips_failed_acceptance(tmp_path: Path):
    """T4: Item with target_kb_path traversal → not written;
    acceptance_results entry passed=False; batch records skip."""
    item = _src_item(item_id="src_bad", target_kb_path="../escape.md")
    manifest = _build_manifest(items=[item])
    service = PromotionCommitService()

    outcome = service.commit(manifest, "batch_001", ["src_bad"], tmp_path)

    assert outcome.batch.approved_item_ids == []
    # gate-failed approve goes to deferred_ids (not rejected — no reject decision)
    assert "src_bad" in outcome.batch.deferred_item_ids
    assert len(outcome.acceptance_results) == 1
    assert outcome.acceptance_results[0].passed is False
    codes = [f.code for f in outcome.acceptance_results[0].findings]
    assert "target_kb_path_traversal" in codes
    # No file written.
    assert outcome.batch.touched_files == []
    # Verify nothing escaped to ../
    assert not (tmp_path.parent / "escape.md").exists()


# ── T5 — hash mismatch blocks write ────────────────────────────────────────


def test_commit_hash_mismatch_blocks_write(tmp_path: Path):
    """T5: File hash differs from prior batch's after_hash → item NOT
    overwritten; finding=hash_mismatch_pre_write; treated as defer."""
    target = "KB/Wiki/Sources/test/ch1.md"
    full_target = tmp_path / target
    full_target.parent.mkdir(parents=True)
    full_target.write_text("CURRENT_CONTENT_DIFFERENT", encoding="utf-8")

    item = _src_item(item_id="src_001", target_kb_path=target)
    manifest = _build_manifest(items=[item])
    # Prior batch claims a different hash than what's actually on disk.
    manifest.commit_batches.append(
        CommitBatch(
            batch_id="batch_prior",
            created_at="2026-05-10T11:00:00Z",
            approved_item_ids=["src_001"],
            deferred_item_ids=[],
            rejected_item_ids=[],
            touched_files=[
                TouchedFile(
                    path=target,
                    operation="create",
                    before_hash=None,
                    after_hash="0" * 64,  # bogus prior hash
                    backup_path=None,
                )
            ],
            errors=[],
            promotion_status="partial",
        )
    )
    object.__setattr__(manifest, "status", "partial")

    service = PromotionCommitService()
    outcome = service.commit(manifest, "batch_002", ["src_001"], tmp_path)

    assert outcome.batch.approved_item_ids == []
    assert "src_001" in outcome.batch.deferred_item_ids
    codes = [f.code for f in outcome.acceptance_results[0].findings]
    assert "hash_mismatch_pre_write" in codes
    # File on disk should still hold its mismatched original content.
    assert full_target.read_text(encoding="utf-8") == "CURRENT_CONTENT_DIFFERENT"


# ── T6 — resumable after partial failure ───────────────────────────────────


def test_commit_resumable_after_partial_failure(tmp_path: Path):
    """T6: First call: 2 succeed, 1 fail. Second call (different batch_id)
    retries failed item successfully → second batch.approved=1."""
    item_a = _src_item(
        item_id="src_a",
        target_kb_path="KB/Wiki/Sources/test/a.md",
        chapter_ref="ch-1",
    )
    item_b = _src_item(
        item_id="src_b",
        target_kb_path="KB/Wiki/Sources/test/b.md",
        chapter_ref="ch-2",
    )
    item_bad = _src_item(
        item_id="src_bad",
        target_kb_path="../escape.md",  # traversal — fails gate
        chapter_ref="ch-3",
    )
    manifest = _build_manifest(items=[item_a, item_b, item_bad])
    service = PromotionCommitService()

    out1 = service.commit(manifest, "batch_001", ["src_a", "src_b", "src_bad"], tmp_path)
    assert len(out1.batch.approved_item_ids) == 2
    assert "src_bad" in out1.batch.deferred_item_ids
    # Caller would persist out1.batch; we simulate by appending it to manifest.
    manifest.commit_batches.append(out1.batch)
    object.__setattr__(manifest, "status", "partial")

    # Retry: caller fixes src_bad (changes target_kb_path) and reruns.
    object.__setattr__(item_bad, "target_kb_path", "KB/Wiki/Sources/test/bad-fixed.md")

    out2 = service.commit(manifest, "batch_002", ["src_bad"], tmp_path)
    assert out2.error is None
    assert "src_bad" in out2.batch.approved_item_ids
    assert out2.batch.deferred_item_ids == []


# ── T7 — duplicate batch_id raises ─────────────────────────────────────────


def test_commit_duplicate_batch_id_raises(tmp_path: Path):
    """T7: Calling commit() twice with same batch_id → ValueError."""
    item = _src_item(item_id="src_001", target_kb_path="KB/Wiki/Sources/test/a.md")
    manifest = _build_manifest(items=[item])
    service = PromotionCommitService()

    out = service.commit(manifest, "batch_001", ["src_001"], tmp_path)
    manifest.commit_batches.append(out.batch)
    object.__setattr__(manifest, "status", "partial")

    with pytest.raises(ValueError, match="duplicate batch_id"):
        service.commit(manifest, "batch_001", ["src_001"], tmp_path)


# ── T8 — status transitions ────────────────────────────────────────────────


def test_commit_status_transitions(tmp_path: Path):
    """T8: All items approved + committed → 'complete'; subset → 'partial';
    zero committed → 'failed'.

    Use fresh manifests / vaults per branch so approved-set and history are
    isolated."""
    # Branch 1 — all items approved + committed → complete.
    one = _src_item(item_id="src_only", target_kb_path="KB/Wiki/Sources/test/only.md")
    m1 = _build_manifest(items=[one])
    out1 = PromotionCommitService().commit(m1, "batch_complete", ["src_only"], tmp_path / "v1")
    assert out1.batch.promotion_status == "complete"

    # Branch 2 — subset → partial. Two items in manifest; only src_a has a
    # human_decision, src_b is still undecided. Caller commits only src_a so
    # the manifest is partly committed; status must be 'partial'.
    a = _src_item(item_id="src_a", target_kb_path="KB/Wiki/Sources/test/a.md")
    b_undecided = SourcePageReviewItem(
        item_id="src_b",
        recommendation="include",
        action="create",
        reason="r",
        evidence=_evidence_clean(),
        risk=[],
        confidence=0.9,
        source_importance=0.9,
        reader_salience=0.8,
        target_kb_path="KB/Wiki/Sources/test/b.md",
        chapter_ref="ch-2",
        human_decision=None,
    )
    m2 = _build_manifest(items=[a, b_undecided])
    out2 = PromotionCommitService().commit(m2, "batch_partial", ["src_a"], tmp_path / "v2")
    assert out2.batch.promotion_status == "partial"

    # Branch 3 — zero committed → failed. Bad item only.
    bad = _src_item(item_id="src_bad", target_kb_path="../escape.md")
    m3 = _build_manifest(items=[bad])
    out3 = PromotionCommitService().commit(m3, "batch_failed", ["src_bad"], tmp_path / "v3")
    assert out3.batch.promotion_status == "failed"


# ── T9 — does not write outside vault ──────────────────────────────────────


def test_commit_does_not_write_outside_vault(tmp_path: Path):
    """T9: Adversarial item with target_kb_path='../escape.md' → blocked by
    gate; no file outside vault_root."""
    vault = tmp_path / "vault"
    vault.mkdir()
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()
    # Plant a sentinel: if commit somehow escapes, the sentinel will be touched.
    sentinel = snapshot_dir / "untouched.txt"
    sentinel.write_text("UNTOUCHED", encoding="utf-8")

    item = _src_item(
        item_id="src_bad",
        target_kb_path="../snapshot/escape.md",
    )
    manifest = _build_manifest(items=[item])
    service = PromotionCommitService()

    outcome = service.commit(manifest, "batch_001", ["src_bad"], vault)
    assert outcome.batch.approved_item_ids == []
    # Confirm no file was written outside vault.
    assert not (snapshot_dir / "escape.md").exists()
    assert sentinel.read_text(encoding="utf-8") == "UNTOUCHED"


# ── T10 — render source page format ────────────────────────────────────────


def test_commit_render_source_page_format(tmp_path: Path):
    """T10: Generated source page contains frontmatter + claims + evidence
    + chapter_ref."""
    item = _src_item(item_id="src_001", target_kb_path="KB/Wiki/Sources/test/ch1.md")
    manifest = _build_manifest(items=[item])
    rendered = render_source_page(item, manifest)

    assert rendered.startswith("---\n")
    assert "type: source_page" in rendered
    assert "item_id: src_001" in rendered
    assert "chapter_ref: ch-1" in rendered
    assert f"promoted_from_manifest: {manifest.manifest_id}" in rendered
    assert "## Reason" in rendered
    assert "## Evidence" in rendered
    assert "Reason text." in rendered
    assert "HRV reflects autonomic balance." in rendered


# ── T11 — render concept page format ───────────────────────────────────────


def test_commit_render_concept_page_format(tmp_path: Path):
    """T11: Generated concept page contains canonical_label + aliases +
    evidence + cross-lingual match (if any)."""
    cm = CanonicalMatch(
        match_basis="exact_alias",
        confidence=0.97,
        matched_concept_path="KB/Wiki/Concepts/HRV.md",
    )
    item = ConceptReviewItem(
        item_id="concept_hrv_001",
        recommendation="include",
        action="create_global_concept",
        reason="HRV concept appears across multiple chapters.",
        evidence=_evidence_clean(),
        risk=[],
        confidence=0.91,
        source_importance=0.9,
        reader_salience=0.8,
        concept_label="Heart Rate Variability",
        evidence_language="en",
        canonical_match=cm,
        human_decision=_approved_decision(),
    )
    manifest = _build_manifest(items=[item])
    rendered = render_concept_page(item, manifest)

    assert rendered.startswith("---\n")
    assert "type: concept" in rendered
    assert "concept_label: Heart Rate Variability" in rendered
    assert "match_basis: exact_alias" in rendered
    assert "matched_concept_path: KB/Wiki/Concepts/HRV.md" in rendered
    assert "## Cross-source match" in rendered
    assert "## Evidence" in rendered
    assert "HRV reflects autonomic balance." in rendered


# ── T12 — subprocess gate: no shared.book_storage ──────────────────────────


def test_no_book_storage_import():
    """T12: importing shared.promotion_commit / shared.promotion_acceptance_gate
    must NOT pull shared.book_storage into sys.modules."""
    src = textwrap.dedent(
        """
        import sys
        import shared.promotion_commit  # noqa: F401
        import shared.promotion_acceptance_gate  # noqa: F401
        import shared.promotion_renderer  # noqa: F401

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


# ── T13 — subprocess gate: no fastapi / agents / thousand_sunny / LLM ─────


def test_no_runtime_imports_forbidden():
    """T13: importing the commit service must NOT pull fastapi,
    thousand_sunny, agents.*, or LLM clients into sys.modules."""
    src = textwrap.dedent(
        """
        import sys
        import shared.promotion_commit  # noqa: F401
        import shared.promotion_acceptance_gate  # noqa: F401
        import shared.promotion_renderer  # noqa: F401

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


# ── T14 — CommitOutcome round-trips ────────────────────────────────────────


def test_commit_outcome_round_trips(tmp_path: Path):
    """T14: model_dump() + model_validate() identity."""
    item = _src_item(item_id="src_001", target_kb_path="KB/Wiki/Sources/test/ch1.md")
    manifest = _build_manifest(items=[item])
    service = PromotionCommitService()

    out = service.commit(manifest, "batch_001", ["src_001"], tmp_path)
    dumped = out.model_dump()
    rehydrated = CommitOutcome.model_validate(dumped)
    assert rehydrated == out
    assert rehydrated.model_dump() == dumped


# ── T15 — idempotent rerun with same state ─────────────────────────────────


def test_commit_idempotent_rerun_with_same_state(tmp_path: Path):
    """T15: After committing item A, re-running with same item_id and same
    content → renderer is deterministic, file content is byte-identical to
    what's already on disk; G4 hash check passes (current == prior_after_hash);
    operation='update' with before_hash == after_hash (no net change).

    Documented choice: we do NOT special-case the no-op as
    operation='skip'. The service records the rewrite as a real update; the
    fact that before_hash == after_hash signals zero-net-change to the
    caller. This avoids a special skip code path AND surfaces idempotency
    explicitly in the manifest's TouchedFile log.
    """
    item = _src_item(item_id="src_001", target_kb_path="KB/Wiki/Sources/test/ch1.md")
    manifest = _build_manifest(items=[item])
    service = PromotionCommitService()

    out1 = service.commit(manifest, "batch_001", ["src_001"], tmp_path)
    assert out1.error is None
    assert len(out1.batch.touched_files) == 1
    first_after_hash = out1.batch.touched_files[0].after_hash
    manifest.commit_batches.append(out1.batch)
    object.__setattr__(manifest, "status", "partial")

    # Second run with same content / same state.
    out2 = service.commit(manifest, "batch_002", ["src_001"], tmp_path)
    assert out2.error is None
    assert len(out2.batch.touched_files) == 1
    tf2 = out2.batch.touched_files[0]
    # Idempotency contract: identical input ⇒ identical content hash.
    assert tf2.before_hash == first_after_hash
    assert tf2.after_hash == first_after_hash
    assert tf2.operation == "update"
    # Backup was made (file existed) but content is unchanged.
    assert tf2.backup_path is not None


# ── F1-analog schema invariant on CommitOutcome ────────────────────────────


def test_commit_outcome_error_implies_failed_batch_invariant():
    """Schema F1-analog: error not None ⇒ approved_item_ids=[] AND
    promotion_status='failed'. Mirrors #511 / #513 / #514 patterns."""
    from pydantic import ValidationError

    bad_batch = CommitBatch(
        batch_id="batch_001",
        created_at="2026-05-10T12:00:00Z",
        approved_item_ids=["src_001"],  # non-empty
        deferred_item_ids=[],
        rejected_item_ids=[],
        touched_files=[],
        errors=[],
        promotion_status="partial",
    )
    with pytest.raises(ValidationError, match="error is not None"):
        CommitOutcome(
            batch=bad_batch,
            acceptance_results=[],
            error="vault_root_invalid: simulated",
        )

    # Even with empty approved_item_ids, status must be 'failed'.
    empty_batch_partial = CommitBatch(
        batch_id="batch_002",
        created_at="2026-05-10T12:00:00Z",
        approved_item_ids=[],
        deferred_item_ids=["src_001"],
        rejected_item_ids=[],
        touched_files=[],
        errors=[],
        promotion_status="partial",  # not 'failed'
    )
    with pytest.raises(ValidationError, match="promotion_status='failed'"):
        CommitOutcome(
            batch=empty_batch_partial,
            acceptance_results=[],
            error="some_error",
        )

    # error=None is unaffected by the invariant.
    ok_batch = CommitBatch(
        batch_id="batch_003",
        created_at="2026-05-10T12:00:00Z",
        approved_item_ids=["src_001"],
        deferred_item_ids=[],
        rejected_item_ids=[],
        touched_files=[],
        errors=[],
        promotion_status="partial",
    )
    out = CommitOutcome(batch=ok_batch, acceptance_results=[], error=None)
    assert out.error is None


# ── G10 — adapter refuses path escape (defense in depth at adapter layer) ──


def test_filesystem_adapter_refuses_path_escape(tmp_path: Path):
    """Defense-in-depth: even if a caller bypasses the gate and calls the
    adapter directly with a path that escapes vault_root, the adapter
    raises ValueError. Mirrors G1/G10 enforcement at the adapter layer."""
    adapter = FilesystemKbWriteAdapter(tmp_path)
    with pytest.raises(ValueError, match="resolves outside vault_root"):
        adapter.write_file("../evil.md", b"x", backup_path=None)
    with pytest.raises(ValueError, match="resolves outside vault_root"):
        adapter.read_file("../evil.md")
    with pytest.raises(ValueError, match="resolves outside vault_root"):
        adapter.hash_file("../evil.md")


# ── No real-vault writes — assertion that tests use tempfile only ──────────


def test_tests_use_tempfile_only_no_real_vault():
    """Reflective sanity: the test module never references the real
    KB/Wiki/ directory at module-import time. (Brief §6 boundary 1 / 14.)"""
    # The fixture path is under tests/fixtures/promotion_commit/ — not KB/Wiki.
    assert "KB/Wiki" not in str(FIXTURE_DIR.resolve())
    # tempfile.TemporaryDirectory() is the standard route in tests above
    # (pytest's tmp_path fixture wraps it).
    with tempfile.TemporaryDirectory() as tmp:
        adapter = FilesystemKbWriteAdapter(Path(tmp))
        # Sanity: adapter writes only under tmp.
        adapter.write_file("foo.md", b"x", backup_path=None)
        assert (Path(tmp) / "foo.md").exists()
