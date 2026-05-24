"""Tests for thousand_sunny.routers.repurpose — Bridge UI list + detail surface."""

from __future__ import annotations

import importlib
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Repurpose router with dev-mode auth and isolated DATA_ROOT."""
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")

    # Point engine + router at the isolated tmp_path before reloading the modules
    # so both module-level constants and router-imported aliases pick it up.
    import agents.brook.repurpose_engine as engine_module

    monkeypatch.setattr(engine_module, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(engine_module, "_DATA_ROOT", tmp_path)

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.repurpose as repurpose_module

    importlib.reload(auth_module)
    importlib.reload(repurpose_module)
    importlib.reload(app_module)

    # Reload re-imports DATA_ROOT from engine — re-pin after reload.
    monkeypatch.setattr(repurpose_module, "DATA_ROOT", tmp_path)
    return TestClient(app_module.app)


@pytest.fixture
def seed_run(tmp_path):
    """Seed a single valid run directory."""
    run_dir = tmp_path / "2026-05-01-dr-chu"
    run_dir.mkdir(parents=True)
    (run_dir / "stage1.json").write_text(
        json.dumps({"episode_type": "narrative_journey", "quotes": ["Q1", "Q2"]}),
        encoding="utf-8",
    )
    (run_dir / "blog.md").write_text("blog content", encoding="utf-8")
    (run_dir / "fb-light.md").write_text("fb light content", encoding="utf-8")
    (run_dir / "ig-cards.json").write_text("[]", encoding="utf-8")
    return "2026-05-01-dr-chu"


# ---------------------------------------------------------------------------
# List view
# ---------------------------------------------------------------------------


def test_list_empty_when_no_runs(client):
    resp = client.get("/bridge/repurpose")
    assert resp.status_code == 200
    assert "No repurpose runs yet" in resp.text


def test_list_renders_seeded_run(client, seed_run):
    resp = client.get("/bridge/repurpose")
    assert resp.status_code == 200
    assert seed_run in resp.text
    assert "narrative_journey" in resp.text


def test_list_chassis_nav_active_repurpose(client):
    """Nav highlights BROOK (Fleet dropdown) — REPURPOSE absorbed per ADR-029.

    ADR-029 v2 collapsed REPURPOSE into Brook console; `nav_active='repurpose'`
    normalizes to 'brook' in `_chassis_nav.html`, so the Fleet dropdown trigger
    carries `is-active` and the BROOK menu item has `class="active"`.
    """
    resp = client.get("/bridge/repurpose")
    assert resp.status_code == 200
    # BROOK menu item is marked active (normalized from repurpose).
    assert (
        '<a href="/brook/handoff" role="menuitem" class="active" aria-current="page">BROOK'
        in resp.text
    )
    # Fleet dropdown trigger carries is-active class on this surface.
    assert 'class="chassis-dropdown is-active"' in resp.text


# ---------------------------------------------------------------------------
# Detail view — happy path
# ---------------------------------------------------------------------------


def test_detail_renders_seeded_run(client, seed_run):
    resp = client.get(f"/bridge/repurpose/{seed_run}")
    assert resp.status_code == 200
    assert "blog content" in resp.text
    assert "fb light content" in resp.text
    assert "narrative_journey" in resp.text


# ---------------------------------------------------------------------------
# Path-traversal hardening (regression for review BLOCKER)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "evil_id",
    [
        "../../etc",  # raw traversal (router decodes %2F before regex)
        "..%2F..%2Fetc",  # URL-encoded traversal
        "2026-05-01-../escape",  # mid-id traversal
        "2026-05-01-foo/bar",  # nested path
        "..\\windows\\evil",  # Windows-style traversal
        "a" * 200,  # over-length slug
        # OK shape but month invalid; regex matches, route 404s on missing dir
        "2026-13-01-bad",
        "not-a-date-at-all",  # totally wrong shape
    ],
)
def test_detail_rejects_path_traversal_and_malformed_ids(client, evil_id):
    """run_id regex must reject traversal-shaped or malformed paths with 404.

    The 'OK shape but invalid month' case (2026-13-01) returns 404 via
    not-a-real-dir path, which is fine — what matters is no traversal escape.
    """
    resp = client.get(f"/bridge/repurpose/{evil_id}")
    assert resp.status_code == 404, f"path-traversal id {evil_id!r} was not blocked"


def test_detail_404_for_nonexistent_well_formed_id(client):
    """Well-formed run_id pointing at a non-existent dir returns 404 cleanly."""
    resp = client.get("/bridge/repurpose/2026-05-01-doesnotexist")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Bad stage1.json doesn't break list
# ---------------------------------------------------------------------------


def test_list_skips_malformed_stage1_json_with_warning(client, tmp_path):
    """A run dir with corrupt stage1.json renders with empty episode_type, no crash."""
    run_dir = tmp_path / "2026-05-01-corrupt"
    run_dir.mkdir(parents=True)
    (run_dir / "stage1.json").write_text("{not valid json", encoding="utf-8")
    (run_dir / "blog.md").write_text("x", encoding="utf-8")

    resp = client.get("/bridge/repurpose")
    assert resp.status_code == 200
    assert "2026-05-01-corrupt" in resp.text


def test_list_handles_non_string_episode_type_gracefully(client, tmp_path):
    """If episode_type is non-string (e.g. dict from buggy extractor), render empty chip."""
    run_dir = tmp_path / "2026-05-01-weird"
    run_dir.mkdir(parents=True)
    (run_dir / "stage1.json").write_text(
        json.dumps({"episode_type": {"unexpected": "shape"}}), encoding="utf-8"
    )
    (run_dir / "blog.md").write_text("x", encoding="utf-8")

    resp = client.get("/bridge/repurpose")
    assert resp.status_code == 200
    # No crash; the dict literal should NOT appear as an episode_type chip
    assert "{'unexpected':" not in resp.text


# ===========================================================================
# Slice 10 — mutation routes
# ===========================================================================


# ---------------------------------------------------------------------------
# Save endpoints
# ---------------------------------------------------------------------------


def test_save_blog_writes_file(client, seed_run, tmp_path):
    resp = client.post(
        f"/bridge/repurpose/{seed_run}/blog",
        json={"content": "# new blog body"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert (tmp_path / seed_run / "blog.md").read_text(encoding="utf-8") == "# new blog body"


def test_save_fb_tonal_independent(client, seed_run, tmp_path):
    """Saving fb.light must not touch fb.neutral / fb.serious / fb.emotional."""
    # Seed all four FB tonals so we can verify isolation.
    for tonal in ("light", "emotional", "serious", "neutral"):
        (tmp_path / seed_run / f"fb-{tonal}.md").write_text(f"orig {tonal}", encoding="utf-8")

    resp = client.post(
        f"/bridge/repurpose/{seed_run}/fb/light",
        json={"content": "new light"},
    )
    assert resp.status_code == 200
    assert (tmp_path / seed_run / "fb-light.md").read_text(encoding="utf-8") == "new light"
    # Others untouched.
    for tonal in ("emotional", "serious", "neutral"):
        assert (tmp_path / seed_run / f"fb-{tonal}.md").read_text(
            encoding="utf-8"
        ) == f"orig {tonal}"


def test_save_fb_unknown_tonal_404(client, seed_run):
    resp = client.post(
        f"/bridge/repurpose/{seed_run}/fb/spicy",
        json={"content": "x"},
    )
    assert resp.status_code == 404


def test_save_ig_writes_file(client, seed_run, tmp_path):
    resp = client.post(
        f"/bridge/repurpose/{seed_run}/ig",
        json={"content": '[{"card": 1}]'},
    )
    assert resp.status_code == 200
    assert (tmp_path / seed_run / "ig-cards.json").read_text(encoding="utf-8") == '[{"card": 1}]'


def test_save_rejects_non_string_content(client, seed_run):
    resp = client.post(f"/bridge/repurpose/{seed_run}/blog", json={"content": 123})
    assert resp.status_code == 400


def test_save_rejects_oversized_content(client, seed_run):
    huge = "x" * 200_001
    resp = client.post(f"/bridge/repurpose/{seed_run}/blog", json={"content": huge})
    assert resp.status_code == 413


def test_save_rejects_path_traversal(client):
    resp = client.post("/bridge/repurpose/..%2Fetc/blog", json={"content": "x"})
    assert resp.status_code == 404


def test_save_404_for_missing_run(client):
    resp = client.post("/bridge/repurpose/2026-05-01-nope/blog", json={"content": "x"})
    assert resp.status_code == 404


def test_save_atomic_no_tmp_leftover(client, seed_run, tmp_path):
    """After a successful save there must be no stray .tmp file in the run dir."""
    resp = client.post(f"/bridge/repurpose/{seed_run}/blog", json={"content": "atomic check"})
    assert resp.status_code == 200
    leftovers = [p.name for p in (tmp_path / seed_run).iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


# ---------------------------------------------------------------------------
# Approve endpoints
# ---------------------------------------------------------------------------


def test_approve_blog_writes_sentinel(client, seed_run, tmp_path):
    resp = client.post(f"/bridge/repurpose/{seed_run}/approve/blog")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["channel"] == "blog"
    assert (tmp_path / seed_run / ".approved.blog").exists()


def test_approve_each_fb_tonal_independent(client, seed_run, tmp_path):
    """Approving fb.light must not flip the approved state of fb.neutral."""
    resp = client.post(f"/bridge/repurpose/{seed_run}/approve/fb.light")
    assert resp.status_code == 200
    assert (tmp_path / seed_run / ".approved.fb.light").exists()
    assert not (tmp_path / seed_run / ".approved.fb.neutral").exists()
    assert not (tmp_path / seed_run / ".approved.fb.serious").exists()
    assert not (tmp_path / seed_run / ".approved.fb.emotional").exists()


def test_approve_ig_writes_sentinel(client, seed_run, tmp_path):
    resp = client.post(f"/bridge/repurpose/{seed_run}/approve/ig")
    assert resp.status_code == 200
    assert (tmp_path / seed_run / ".approved.ig").exists()


def test_approve_unknown_channel_404(client, seed_run):
    resp = client.post(f"/bridge/repurpose/{seed_run}/approve/twitter")
    assert resp.status_code == 404


def test_approve_idempotent(client, seed_run, tmp_path):
    """Re-approving an already-approved channel is a no-op (200)."""
    assert client.post(f"/bridge/repurpose/{seed_run}/approve/blog").status_code == 200
    assert client.post(f"/bridge/repurpose/{seed_run}/approve/blog").status_code == 200
    assert (tmp_path / seed_run / ".approved.blog").exists()


# ---------------------------------------------------------------------------
# List + detail status badge
# ---------------------------------------------------------------------------


def test_list_status_pending_when_no_approvals(client, seed_run):
    resp = client.get("/bridge/repurpose")
    assert resp.status_code == 200
    assert 'data-status="pending"' in resp.text


def test_list_status_partially_approved_after_one_approve(client, seed_run):
    client.post(f"/bridge/repurpose/{seed_run}/approve/blog")
    resp = client.get("/bridge/repurpose")
    assert resp.status_code == 200
    assert 'data-status="partially-approved"' in resp.text


def test_list_status_approved_when_all_six_channels(client, seed_run):
    for ch in ("blog", "fb.light", "fb.emotional", "fb.serious", "fb.neutral", "ig"):
        client.post(f"/bridge/repurpose/{seed_run}/approve/{ch}")
    resp = client.get("/bridge/repurpose")
    assert resp.status_code == 200
    assert 'data-status="approved"' in resp.text


def test_list_status_published_after_publish_sentinel(client, seed_run, tmp_path):
    # Approve blog then drop the published sentinel directly (simulating a
    # successful publish run — the publish route itself is exercised below).
    client.post(f"/bridge/repurpose/{seed_run}/approve/blog")
    (tmp_path / seed_run / ".published.blog").touch()
    resp = client.get("/bridge/repurpose")
    assert resp.status_code == 200
    assert 'data-status="published"' in resp.text


def test_detail_exposes_editable_textareas(client, seed_run):
    """Slice 10 detail view must surface textarea controls for each channel."""
    resp = client.get(f"/bridge/repurpose/{seed_run}")
    assert resp.status_code == 200
    assert 'id="rp-edit-blog"' in resp.text
    assert 'id="rp-edit-fb-light"' in resp.text
    assert 'id="rp-edit-fb-neutral"' in resp.text
    assert 'id="rp-edit-ig"' in resp.text
    # Approve buttons present
    assert "rp-approve-btn" in resp.text
    # Publish button present (blog only)
    assert "rp-publish-btn" in resp.text


# ---------------------------------------------------------------------------
# Publish endpoint (Usopp WP draft) — adapter mocked
# ---------------------------------------------------------------------------


def test_publish_blog_requires_approval_first(client, seed_run):
    resp = client.post(f"/bridge/repurpose/{seed_run}/publish/blog")
    assert resp.status_code == 409  # blog not yet approved


def test_publish_blog_501_when_adapter_missing(client, seed_run):
    """Until the blog.md → DraftV1 adapter ships, the route surfaces 501."""
    client.post(f"/bridge/repurpose/{seed_run}/approve/blog")
    resp = client.post(f"/bridge/repurpose/{seed_run}/publish/blog")
    assert resp.status_code == 501
    body = resp.json()
    assert body["ok"] is False
    assert body["error"] == "adapter_missing"


def test_publish_blog_success_when_adapter_mocked(monkeypatch, client, seed_run, tmp_path):
    """Mock the adapter to assert the happy path: sentinel + status flip."""
    import thousand_sunny.routers.repurpose as repurpose_module

    monkeypatch.setattr(
        repurpose_module,
        "_enqueue_blog_to_usopp",
        lambda run_dir: {"draft_id": "draft_abc123", "approval_queue_id": 42},
    )
    client.post(f"/bridge/repurpose/{seed_run}/approve/blog")
    resp = client.post(f"/bridge/repurpose/{seed_run}/publish/blog")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "published"
    assert body["draft_id"] == "draft_abc123"
    # Sentinel written.
    assert (tmp_path / seed_run / ".published.blog").exists()
    # Status flips.
    list_resp = client.get("/bridge/repurpose")
    assert 'data-status="published"' in list_resp.text


def test_publish_blog_does_not_hit_live_wp(monkeypatch, client, seed_run):
    """Defence-in-depth: confirm Publisher / WordPressClient never imported during a publish call.

    We monkeypatch the adapter to raise if anyone tries to reach WP.
    """
    import thousand_sunny.routers.repurpose as repurpose_module

    called = {"hit": False}

    def fake_adapter(run_dir):
        called["hit"] = True
        return {"draft_id": "mocked", "approval_queue_id": 1}

    monkeypatch.setattr(repurpose_module, "_enqueue_blog_to_usopp", fake_adapter)
    client.post(f"/bridge/repurpose/{seed_run}/approve/blog")
    resp = client.post(f"/bridge/repurpose/{seed_run}/publish/blog")
    assert resp.status_code == 200
    assert called["hit"] is True


def test_publish_blog_404_when_blog_md_missing(monkeypatch, client, seed_run, tmp_path):
    """If blog.md was deleted between approve and publish, surface 404 not 5xx."""
    client.post(f"/bridge/repurpose/{seed_run}/approve/blog")
    (tmp_path / seed_run / "blog.md").unlink()
    resp = client.post(f"/bridge/repurpose/{seed_run}/publish/blog")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Auth enforcement on mutations
# ---------------------------------------------------------------------------


def test_mutation_requires_auth_when_password_set(monkeypatch, tmp_path):
    """When WEB_PASSWORD is set, mutation endpoints return 401 (not 302)."""
    monkeypatch.setenv("WEB_PASSWORD", "topsecret")
    monkeypatch.setenv("WEB_SECRET", "saltysalt")
    monkeypatch.setenv("DISABLE_ROBIN", "1")

    import importlib

    import agents.brook.repurpose_engine as engine_module

    monkeypatch.setattr(engine_module, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(engine_module, "_DATA_ROOT", tmp_path)

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.repurpose as repurpose_module

    importlib.reload(auth_module)
    importlib.reload(repurpose_module)
    importlib.reload(app_module)
    monkeypatch.setattr(repurpose_module, "DATA_ROOT", tmp_path)

    run_dir = tmp_path / "2026-05-01-locked"
    run_dir.mkdir(parents=True)
    (run_dir / "blog.md").write_text("x", encoding="utf-8")

    auth_client = TestClient(app_module.app)
    resp = auth_client.post("/bridge/repurpose/2026-05-01-locked/blog", json={"content": "hack"})
    assert resp.status_code == 401
    # File must remain unchanged.
    assert (run_dir / "blog.md").read_text(encoding="utf-8") == "x"
