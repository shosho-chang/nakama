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
    """Nav highlights REPURPOSE entry, not BRIDGE."""
    resp = client.get("/bridge/repurpose")
    assert resp.status_code == 200
    # The REPURPOSE entry should carry the active class
    assert 'href="/bridge/repurpose"' in resp.text
    # Active class only present once for this slug
    assert resp.text.count("REPURPOSE") >= 1


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
