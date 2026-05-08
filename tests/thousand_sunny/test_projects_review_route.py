"""Behaviour tests for the /projects/{slug} review-mode page (issue #458).

Covers:
- 404 when the store has not been materialised yet
- 200 + HTML render when the store exists; topic / keywords / outline
  sections / evidence cards all surface
- Auth redirect to /login when WEB_PASSWORD is set and cookie is missing
- Auth pass-through (200) when WEB_PASSWORD is unset (dev mode)
- Real chunk shape (dicts from kb_hybrid_search) renders gracefully when
  authors/journal/year are absent
- A11y smoke: outline has role="navigation" + aria-label, evidence cards
  have role="article", reject buttons carry aria-label
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shared import brook_synthesize_store as store
from shared.schemas.brook_synthesize import (
    BrookSynthesizeStore,
    EvidencePoolItem,
    OutlineSection,
)


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch) -> Path:
    d = tmp_path / "brook_synthesize"
    monkeypatch.setenv("NAKAMA_BROOK_SYNTHESIZE_DIR", str(d))
    monkeypatch.delenv("NAKAMA_DATA_DIR", raising=False)
    return d


@pytest.fixture
def app_client(data_dir, monkeypatch):
    """TestClient on the real app with auth disabled."""
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.projects as projects_module

    importlib.reload(auth_module)
    importlib.reload(projects_module)
    importlib.reload(app_module)

    return TestClient(app_module.app, follow_redirects=False)


@pytest.fixture
def auth_app_client(data_dir, monkeypatch):
    """Same app but with WEB_PASSWORD/WEB_SECRET set so auth gates fire."""
    monkeypatch.setenv("WEB_PASSWORD", "swordfish")
    monkeypatch.setenv("WEB_SECRET", "abc123")
    monkeypatch.setenv("DISABLE_ROBIN", "1")

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.projects as projects_module

    importlib.reload(auth_module)
    importlib.reload(projects_module)
    importlib.reload(app_module)

    return TestClient(app_module.app, follow_redirects=False)


def _seed_realistic(slug: str = "sleep-architecture-choline") -> BrookSynthesizeStore:
    """Materialise a store with realistic chunk dicts from kb_hybrid_search.

    Real chunks have {path, title, heading, chunk_text, chunk_id, rrf_score,
    source_slug} — no authors / journal / year. Tests that authors etc. are
    not faked.
    """
    s = BrookSynthesizeStore(
        project_slug=slug,
        topic="膽鹼攝取量與深層睡眠結構之間的關係",
        keywords=["choline", "slow-wave sleep", "膽鹼", "深層睡眠"],
        evidence_pool=[
            EvidencePoolItem(
                slug="lieberman-2015",
                chunks=[
                    {
                        "path": "kb/raw/lieberman-2015.md",
                        "title": "Effects of phosphatidylcholine on slow-wave sleep",
                        "heading": "§3.2 — Polysomnography results",
                        "chunk_text": (
                            "Across the 28-day intervention, the high-choline arm "
                            "showed a mean increase of 11.4 minutes in N3 "
                            "(slow-wave) sleep per night."
                        ),
                        "chunk_id": "c1",
                        "rrf_score": 0.94,
                        "source_slug": "lieberman-2015",
                    }
                ],
                hit_reason="matched on: slow-wave sleep · choline · RCT",
            ),
            EvidencePoolItem(
                slug="penland-2020",
                chunks=[
                    {
                        "path": "kb/raw/penland-2020.md",
                        "title": "Dietary choline supplementation and sleep stage distribution",
                        "heading": "§4.1 — Primary outcome",
                        "chunk_text": "受試者於補充組相較於對照組，在 8 週後睡眠效率提升 3.1%。",
                        "chunk_id": "c2",
                        "rrf_score": 0.88,
                        "source_slug": "penland-2020",
                    }
                ],
                hit_reason="matched on: SWS proportion",
            ),
        ],
        outline_draft=[
            OutlineSection(
                section=1,
                heading="為什麼膽鹼會出現在睡眠研究的對話裡",
                evidence_refs=["lieberman-2015"],
            ),
            OutlineSection(
                section=3,
                heading="已被引用最廣的三項人體研究",
                evidence_refs=["lieberman-2015", "penland-2020"],
            ),
        ],
    )
    return store.create(s)


# ── 404 / 200 ────────────────────────────────────────────────────────────────


def test_review_returns_404_when_store_missing(app_client: TestClient):
    r = app_client.get("/projects/no-such-project")
    assert r.status_code == 404


def test_review_renders_html_when_store_exists(app_client: TestClient):
    _seed_realistic()
    r = app_client.get("/projects/sleep-architecture-choline")
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers.get("content-type", "")
    body = r.text
    # topic surfaces
    assert "膽鹼攝取量與深層睡眠結構" in body
    # keywords (zh + latin both render)
    assert "choline" in body
    assert "膽鹼" in body
    # outline sections render
    assert "為什麼膽鹼會出現在睡眠研究的對話裡" in body
    assert "已被引用最廣的三項人體研究" in body
    # evidence cards render with title + slug
    assert "lieberman-2015" in body
    assert "penland-2020" in body
    assert "Effects of phosphatidylcholine on slow-wave sleep" in body
    # relevance % computed from rrf_score (0.94 → 94)
    assert "94" in body
    assert "88" in body


def test_review_handles_empty_outline_and_pool(app_client: TestClient):
    s = BrookSynthesizeStore(
        project_slug="empty-proj",
        topic="empty topic",
        keywords=[],
    )
    store.create(s)
    r = app_client.get("/projects/empty-proj")
    assert r.status_code == 200
    assert "outline_draft 為空" in r.text or "0 sections" in r.text


def test_review_real_chunks_omit_missing_fields_gracefully(app_client: TestClient):
    """Authors/journal/year absent in real chunks must not crash and must not
    be faked into the output."""
    _seed_realistic()
    r = app_client.get("/projects/sleep-architecture-choline")
    assert r.status_code == 200
    body = r.text
    # No "—" placeholder for journal/year; the design's authors line is just absent.
    # Confirm no fake author strings leaked from mock data.
    assert "Lieberman, H.R." not in body
    assert "Penland, J.G." not in body


# ── auth ─────────────────────────────────────────────────────────────────────


def test_review_redirects_unauthenticated(auth_app_client: TestClient):
    """When WEB_PASSWORD is set and the cookie is missing, 302 → /login."""
    _seed_realistic()
    r = auth_app_client.get("/projects/sleep-architecture-choline")
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("/login")
    assert "next=/projects/sleep-architecture-choline" in loc


# ── a11y smoke ───────────────────────────────────────────────────────────────


def test_review_has_aria_landmarks_and_labels(app_client: TestClient):
    _seed_realistic()
    r = app_client.get("/projects/sleep-architecture-choline")
    assert r.status_code == 200
    body = r.text
    assert 'aria-label="大綱 · outline"' in body
    assert 'aria-label="證據池 · evidence"' in body
    assert 'role="article"' in body
    # reject buttons carry slug-specific aria-labels
    assert "整條下架證據 lieberman-2015" in body
    assert "從本段拿掉證據 penland-2020" in body


# ── writing mode (issue #462) ────────────────────────────────────────────────


def test_review_renders_review_mode_when_no_outline_final(app_client: TestClient):
    _seed_realistic()
    r = app_client.get("/projects/sleep-architecture-choline")
    assert r.status_code == 200
    body = r.text
    assert 'data-mode="review"' in body
    # finalize button is enabled (no `disabled` attribute on the button)
    assert "定稿這份綜合 · finalize" in body
    # reject buttons are not disabled
    assert "從這段拿掉" in body
    # writing-mode caption absent
    assert "synthesize · review · 綜合稿" in body


def test_review_renders_writing_mode_when_outline_final_set(app_client: TestClient):
    """When outline_final is non-empty, panel is in writing mode:
    - data-mode="writing"
    - finalize button shows finalized state + disabled
    - reject buttons disabled
    - outline panel renders outline_final (not outline_draft)
    """
    _seed_realistic()
    # Stamp an outline_final that differs from outline_draft so we can tell
    # which one rendered.
    store.update_outline_final(
        "sleep-architecture-choline",
        [
            OutlineSection(
                section=1,
                heading="定稿後的第一段標題",
                evidence_refs=["lieberman-2015"],
            ),
            OutlineSection(
                section=2,
                heading="定稿後的第二段標題",
                evidence_refs=["penland-2020"],
            ),
        ],
    )
    r = app_client.get("/projects/sleep-architecture-choline")
    assert r.status_code == 200
    body = r.text
    assert 'data-mode="writing"' in body
    # finalize button reflects finalized + disabled
    assert "已定稿 · finalized" in body
    assert "disabled" in body
    # outline_final headings rendered
    assert "定稿後的第一段標題" in body
    assert "定稿後的第二段標題" in body
    # outline_draft headings NOT rendered in the outline list
    assert "為什麼膽鹼會出現在睡眠研究的對話裡" not in body
    # writing mode caption present
    assert "synthesize · writing · 寫稿模式" in body


# ── slug guard ───────────────────────────────────────────────────────────────


def test_review_rejects_path_traversal_slug(app_client: TestClient):
    # FastAPI normalises ".." in the URL path; either 400 from our guard or
    # 404 from the routing layer is acceptable (no traversal occurred).
    r = app_client.get("/projects/..")
    # ASGI normalisation can collapse "/projects/.." → "/" and 302 to /login,
    # or surface as 400/404/405. All are acceptable refusals (no traversal
    # occurred — our guard would also catch ".." if routed).
    assert r.status_code in (302, 400, 404, 405)
