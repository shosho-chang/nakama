"""Behaviour tests for the Promotion Review routes (ADR-024 Slice 8 / #516).

10 tests covering Brief §5 RT1-RT10:

- RT1  list page renders preflighted sources.
- RT2  per-source review surface renders manifest items.
- RT3  /decide approve persists.
- RT4  /decide reject persists.
- RT5  /decide defer persists.
- RT6  /commit invokes commit service with approved item ids.
- RT7  Commit button disabled when no approvals.
- RT8  /start runs builder + engine and persists a fresh manifest.
- RT9  Static check: routes module does NOT import upstream
        (#511 / #513 / #514 / #515) modules directly. U1 invariant.
- RT10 base64url ``source_id`` round-trip — handler decodes opaque string.

Tests inject a fake ``PromotionReviewService`` via ``set_service``; no real
LLM, no real vault writes. Mirrors the fake-injection pattern in
``tests/shared/test_promotion_review_service.py``.
"""

from __future__ import annotations

import base64
import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shared.schemas.promotion_commit import CommitOutcome
from shared.schemas.promotion_manifest import (
    CommitBatch,
    PromotionManifest,
    TouchedFile,
)
from shared.schemas.promotion_review_state import PromotionReviewState

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "promotion_review"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _b64(source_id: str) -> str:
    return base64.urlsafe_b64encode(source_id.encode("utf-8")).decode("ascii").rstrip("=")


def _load_mixed_manifest() -> PromotionManifest:
    raw = (FIXTURE_DIR / "manifest_mixed_decisions.json").read_text(encoding="utf-8")
    return PromotionManifest.model_validate(json.loads(raw))


def _load_no_decisions_manifest() -> PromotionManifest:
    raw = (FIXTURE_DIR / "manifest_no_decisions.json").read_text(encoding="utf-8")
    return PromotionManifest.model_validate(json.loads(raw))


# ── Fake service ─────────────────────────────────────────────────────────────


class FakePromotionReviewService:
    """In-memory stand-in for ``PromotionReviewService`` used by route tests.

    Records calls so route tests can assert the route delegated correctly.
    """

    def __init__(self):
        self.manifests: dict[str, PromotionManifest] = {}
        self.states: list[PromotionReviewState] = []
        self.commit_calls: list[tuple[str, str, list[str]]] = []
        self.start_calls: list[str] = []
        # When set, commit_approved returns this CommitOutcome and sticks the
        # batch into the manifest.
        self.next_commit_outcome: CommitOutcome | None = None

    def list_pending(self):
        return self.states

    def state_for(self, source_id):
        for state in self.states:
            if state.source_id == source_id:
                return state
        return None

    def load_review_session(self, source_id):
        return self.manifests.get(source_id)

    def record_decision(self, source_id, item_id, decision, note=None, *, decided_by="shosho"):
        from shared.schemas.promotion_manifest import HumanDecision, now_iso_utc

        manifest = self.manifests.get(source_id)
        if manifest is None:
            raise ValueError(f"no manifest for {source_id!r}")
        target = next((it for it in manifest.items if it.item_id == item_id), None)
        if target is None:
            raise ValueError(f"no item {item_id!r}")
        target.human_decision = HumanDecision(
            decision=decision,
            decided_at=now_iso_utc(),
            decided_by=decided_by,
            note=note,
        )
        return manifest

    def commit_approved(self, source_id, batch_id, vault_root):
        manifest = self.manifests.get(source_id)
        if manifest is None:
            raise ValueError(f"no manifest for {source_id!r}")
        approved = [
            it.item_id
            for it in manifest.items
            if it.human_decision is not None and it.human_decision.decision == "approve"
        ]
        self.commit_calls.append((source_id, batch_id, approved))
        if self.next_commit_outcome is not None:
            outcome = self.next_commit_outcome
        else:
            touched = [
                TouchedFile(
                    path=f"KB/Wiki/Sources/test/{i}.md",
                    operation="create",
                    before_hash=None,
                    after_hash="a" * 64,
                    backup_path=None,
                )
                for i, _ in enumerate(approved)
            ]
            batch = CommitBatch(
                batch_id=batch_id,
                created_at="2026-05-10T15:00:00Z",
                approved_item_ids=approved,
                deferred_item_ids=[],
                rejected_item_ids=[],
                touched_files=touched,
                errors=[],
                promotion_status="partial" if approved else "failed",
            )
            outcome = CommitOutcome(batch=batch, acceptance_results=[], error=None)
        manifest.commit_batches.append(outcome.batch)
        manifest.status = "partial" if outcome.batch.approved_item_ids else "failed"
        return outcome

    def start_review(self, source_id):
        from shared.schemas.promotion_manifest import RecommenderMetadata, now_iso_utc

        self.start_calls.append(source_id)
        manifest = PromotionManifest(
            schema_version=1,
            manifest_id=f"mfst_{source_id}_started",
            source_id=source_id,
            created_at=now_iso_utc(),
            status="needs_review",
            replaces_manifest_id=None,
            recommender=RecommenderMetadata(
                model_name="claude-opus-4-7",
                model_version="2026-04",
                run_params={},
                recommended_at=now_iso_utc(),
            ),
            items=[],
            commit_batches=[],
            metadata={},
        )
        self.manifests[source_id] = manifest
        return manifest


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_service():
    return FakePromotionReviewService()


@pytest.fixture
def app_client(fake_service, monkeypatch):
    """TestClient on the real app with auth disabled and a fake review service."""
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.promotion_review as pr_module

    importlib.reload(auth_module)
    importlib.reload(pr_module)
    importlib.reload(app_module)

    pr_module.set_service(fake_service)

    return TestClient(app_module.app, follow_redirects=False)


# ── RT1 — list pending ───────────────────────────────────────────────────────


def test_rt1_list_pending_renders_preflighted_sources(
    app_client: TestClient, fake_service: FakePromotionReviewService
):
    fake_service.states = [
        PromotionReviewState(
            source_id="ebook:alpha-book",
            primary_lang="en",
            preflight_action="proceed_full_promotion",
            preflight_summary="proceed_full_promotion · 12ch · 80000w",
            has_existing_manifest=False,
            manifest_status=None,
        ),
        PromotionReviewState(
            source_id="inbox:Inbox/kb/beta-article.md",
            primary_lang="en",
            preflight_action="proceed_with_warnings",
            preflight_summary="proceed_with_warnings · 1ch · 1500w · risks: weak_toc",
            has_existing_manifest=True,
            manifest_status="needs_review",
        ),
    ]
    r = app_client.get("/promotion-review/")
    assert r.status_code == 200
    body = r.text
    assert "ebook:alpha-book" in body
    assert "inbox:Inbox/kb/beta-article.md" in body
    assert "proceed_full_promotion" in body
    assert "proceed_with_warnings" in body
    # The list view links to encoded URLs.
    assert _b64("ebook:alpha-book") in body
    assert _b64("inbox:Inbox/kb/beta-article.md") in body


# ── RT2 — review surface renders items ──────────────────────────────────────


def test_rt2_review_surface_renders_items(
    app_client: TestClient, fake_service: FakePromotionReviewService
):
    manifest = _load_mixed_manifest()
    fake_service.manifests[manifest.source_id] = manifest
    fake_service.states = [
        PromotionReviewState(
            source_id=manifest.source_id,
            primary_lang="en",
            preflight_action="proceed_full_promotion",
            preflight_summary="proceed_full_promotion · 5ch · 50000w",
            has_existing_manifest=True,
            manifest_status="needs_review",
        )
    ]
    r = app_client.get(f"/promotion-review/source/{_b64(manifest.source_id)}")
    assert r.status_code == 200
    body = r.text
    # Each item card: recommendation, reason, evidence excerpt, risk badge,
    # confidence (numeric).
    assert "src_ch1_001" in body
    assert "src_ch3_001" in body
    assert "concept_hrv_001" in body
    assert "Heart Rate Variability" in body
    # Reason copy renders.
    assert "Chapter 1 introduces autonomic balance with strong evidence." in body
    # Evidence excerpts render.
    assert "Heart rate variability reflects autonomic balance" in body
    # Risk codes render somewhere.
    assert "ocr_artifact" in body
    assert "duplicate_concept" in body
    # Confidence numbers render — fixture has 0.91, 0.87, 0.6, 0.74, 0.91.
    assert "0.91" in body


# ── RT3 / RT4 / RT5 — decision persistence ──────────────────────────────────


@pytest.mark.parametrize(
    "decision",
    ["approve", "reject", "defer"],
    ids=["rt3-approve", "rt4-reject", "rt5-defer"],
)
def test_rt3_4_5_decision_persists(
    app_client: TestClient, fake_service: FakePromotionReviewService, decision: str
):
    manifest = _load_no_decisions_manifest()
    fake_service.manifests[manifest.source_id] = manifest
    encoded = _b64(manifest.source_id)
    r = app_client.post(
        f"/promotion-review/source/{encoded}/decide/src_whole_001",
        data={"decision": decision, "note": ""},
    )
    # Plain form post (no HTMX header) → 303 redirect to review surface.
    assert r.status_code == 303
    assert r.headers["location"] == f"/promotion-review/source/{encoded}"
    persisted = fake_service.manifests[manifest.source_id]
    target = next(it for it in persisted.items if it.item_id == "src_whole_001")
    assert target.human_decision is not None
    assert target.human_decision.decision == decision


# ── RT6 — commit invokes service ────────────────────────────────────────────


def test_rt6_commit_invokes_commit_service(
    app_client: TestClient, fake_service: FakePromotionReviewService
):
    manifest = _load_mixed_manifest()
    fake_service.manifests[manifest.source_id] = manifest
    encoded = _b64(manifest.source_id)
    r = app_client.post(
        f"/promotion-review/source/{encoded}/commit",
        data={"batch_id": "batch_test_001"},
    )
    assert r.status_code == 200
    # Service was called exactly once with the approved item_ids from the fixture.
    assert len(fake_service.commit_calls) == 1
    source_id, batch_id, approved = fake_service.commit_calls[0]
    assert source_id == manifest.source_id
    assert batch_id == "batch_test_001"
    # Mixed-decisions fixture has src_ch1_001 approved (others reject/defer/None).
    assert approved == ["src_ch1_001"]
    body = r.text
    assert "batch_test_001" in body


# ── RT7 — commit disabled when no approvals ─────────────────────────────────


def test_rt7_commit_disabled_when_no_approvals(
    app_client: TestClient, fake_service: FakePromotionReviewService
):
    manifest = _load_no_decisions_manifest()
    fake_service.manifests[manifest.source_id] = manifest
    encoded = _b64(manifest.source_id)
    r = app_client.get(f"/promotion-review/source/{encoded}")
    assert r.status_code == 200
    body = r.text
    # The commit button must carry the disabled HTML attribute when zero
    # items have human_decision.decision == "approve".
    assert 'disabled aria-disabled="true"' in body
    # The "no approvals yet" copy is the disabled-state label.
    assert "no approvals yet" in body


# ── RT8 — start runs builder + engine ───────────────────────────────────────


def test_rt8_start_review_runs_builder_and_engine(
    app_client: TestClient, fake_service: FakePromotionReviewService
):
    encoded = _b64("inbox:Inbox/kb/gamma.md")
    r = app_client.post(f"/promotion-review/source/{encoded}/start")
    assert r.status_code == 303
    assert r.headers["location"] == f"/promotion-review/source/{encoded}"
    assert fake_service.start_calls == ["inbox:Inbox/kb/gamma.md"]
    # Manifest now exists in the fake store.
    assert "inbox:Inbox/kb/gamma.md" in fake_service.manifests


# ── RT9 — static import-set check (U1 invariant) ────────────────────────────


def test_rt9_route_handlers_use_service_only():
    """U1: thousand_sunny/routers/promotion_review.py must NOT import
    shared.promotion_preflight, shared.source_map_builder,
    shared.concept_promotion_engine, shared.promotion_commit. Only the
    service facade (shared.promotion_review_service) is allowed.
    """
    routes_path = (
        Path(__file__).resolve().parents[2] / "thousand_sunny" / "routers" / "promotion_review.py"
    )
    text = routes_path.read_text(encoding="utf-8")
    forbidden_modules = [
        "shared.promotion_preflight",
        "shared.source_map_builder",
        "shared.concept_promotion_engine",
        "shared.promotion_commit",
    ]
    offenders = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for mod in forbidden_modules:
            if (
                stripped.startswith(f"import {mod}")
                or stripped.startswith(f"from {mod} ")
                or stripped.startswith(f"from {mod}\t")
                or stripped == f"from {mod} import"
            ):
                offenders.append((mod, line))
    assert offenders == [], (
        f"thousand_sunny/routers/promotion_review.py imports forbidden upstream "
        f"modules directly (U1 violation): {offenders}. Routes must only call "
        f"shared.promotion_review_service."
    )


# ── RT10 — source_id_b64 round-trip ─────────────────────────────────────────


def test_rt10_source_id_b64_encoding_round_trips(
    app_client: TestClient, fake_service: FakePromotionReviewService
):
    """U4: handler decodes base64url source_id back to the original opaque
    string and does NOT parse the inner namespace prefix. Test with a
    source_id containing both ``:`` and ``/`` characters."""
    weird_source_id = "inbox:Inbox/kb/foo bar/baz.md"
    manifest = _load_no_decisions_manifest()
    # Reuse the no-decisions manifest but override the source_id to exercise
    # the decode round-trip with funky characters.
    object.__setattr__(manifest, "source_id", weird_source_id)
    fake_service.manifests[weird_source_id] = manifest
    fake_service.states = [
        PromotionReviewState(
            source_id=weird_source_id,
            primary_lang="en",
            preflight_action="proceed_full_promotion",
            preflight_summary="proceed_full_promotion · 1ch · 1500w",
            has_existing_manifest=True,
            manifest_status="needs_review",
        )
    ]
    encoded = _b64(weird_source_id)
    # No '/' or ':' should appear in the encoded URL segment.
    assert "/" not in encoded
    assert ":" not in encoded
    r = app_client.get(f"/promotion-review/source/{encoded}")
    assert r.status_code == 200
    # The original (decoded) source_id surfaces in the rendered surface — the
    # handler decoded it from the URL without parsing the namespace prefix.
    assert weird_source_id in r.text


# ── Negative — invalid base64 returns 400 ───────────────────────────────────


def test_invalid_source_id_b64_returns_400(app_client: TestClient):
    """U4 sanity: a source_id_b64 that is not valid base64url returns 400."""
    r = app_client.get("/promotion-review/source/!!!not-base64!!!")
    assert r.status_code == 400
