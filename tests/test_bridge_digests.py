"""Tests for thousand_sunny.routers.bridge_digests — Tier A read viewer."""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

PUBMED_SAMPLE = """---
date: '2026-05-24'
created_by: robin
selected_count: 12
editor_pick_count: 8
type: digest
---

# PubMed 每日精選 — 2026-05-24

> 今日精選以多篇 Nature 研究為主。

**候選總數**：67　**入選**：12

## ⭐ Editor's Picks

### 1. Semaglutide trial

- **→** [[pubmed-42174253]]
- **→** [[ghost-reference]]
"""

AI_SAMPLE = """---
date: '2026-05-23'
created_by: franky
selected_count: 4
type: digest
---

# AI 每日情報 — 2026-05-23

> Claude Code 更新為主軸。
"""


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    monkeypatch.setenv("NAKAMA_DOC_INDEX_DB_PATH", str(tmp_path / "doc_index.db"))

    # Seed vault
    pm = tmp_path / "KB" / "Wiki" / "Digests" / "PubMed"
    ai = tmp_path / "KB" / "Wiki" / "Digests" / "AI"
    pm.mkdir(parents=True)
    ai.mkdir(parents=True)
    (pm / "2026-05-24.md").write_text(PUBMED_SAMPLE, encoding="utf-8")
    (ai / "2026-05-23.md").write_text(AI_SAMPLE, encoding="utf-8")

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.bridge_digests as bd_module

    importlib.reload(auth_module)
    importlib.reload(bd_module)
    importlib.reload(app_module)
    return TestClient(app_module.app)


class TestLanding:
    def test_renders_hero_and_timeline(self, client):
        r = client.get("/bridge/digests")
        assert r.status_code == 200
        body = r.text
        assert "PubMed" in body
        assert "2026-05-24" in body
        assert "AI News" in body
        assert "2026-05-23" in body
        assert "Nature 研究" in body  # PubMed summary
        assert "Claude Code" in body  # AI summary
        assert "DIGESTS" in body  # chassis nav slot

    def test_empty_vault_shows_placeholder(self, monkeypatch, tmp_path):
        monkeypatch.delenv("WEB_PASSWORD", raising=False)
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))
        monkeypatch.setenv("DISABLE_ROBIN", "1")
        monkeypatch.setenv("NAKAMA_DOC_INDEX_DB_PATH", str(tmp_path / "x.db"))
        import thousand_sunny.app as app_module
        import thousand_sunny.routers.bridge_digests as bd_module

        importlib.reload(bd_module)
        importlib.reload(app_module)
        c = TestClient(app_module.app)
        r = c.get("/bridge/digests")
        assert r.status_code == 200
        assert "尚無 digest" in r.text


class TestDetail:
    def test_renders_pubmed_detail(self, client):
        r = client.get("/bridge/digests/pubmed/2026-05-24")
        assert r.status_code == 200
        body = r.text
        assert "<h1>PubMed 每日精選 — 2026-05-24</h1>" in body
        assert "blockquote" in body
        assert "Nature 研究" in body

    def test_pubmed_wikilink_resolves_to_external(self, client):
        r = client.get("/bridge/digests/pubmed/2026-05-24")
        assert 'href="https://pubmed.ncbi.nlm.nih.gov/42174253/"' in r.text

    def test_unknown_wikilink_renders_broken(self, client):
        r = client.get("/bridge/digests/pubmed/2026-05-24")
        assert "wikilink-broken" in r.text
        assert "ghost-reference" in r.text

    def test_missing_digest_404(self, client):
        r = client.get("/bridge/digests/pubmed/2026-01-01")
        assert r.status_code == 404

    def test_unknown_type_404(self, client):
        r = client.get("/bridge/digests/podcast/2026-05-24")
        assert r.status_code == 404

    def test_invalid_date_404(self, client):
        r = client.get("/bridge/digests/pubmed/not-a-date")
        assert r.status_code == 404


class TestAuth:
    def test_redirects_to_login_without_cookie(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WEB_PASSWORD", "secret")
        monkeypatch.setenv("WEB_SECRET", "shh")
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))
        monkeypatch.setenv("DISABLE_ROBIN", "1")
        monkeypatch.setenv("NAKAMA_DOC_INDEX_DB_PATH", str(tmp_path / "x.db"))
        import thousand_sunny.app as app_module
        import thousand_sunny.auth as auth_module
        import thousand_sunny.routers.bridge_digests as bd_module

        importlib.reload(auth_module)
        importlib.reload(bd_module)
        importlib.reload(app_module)
        c = TestClient(app_module.app)
        r = c.get("/bridge/digests", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/login?next=/bridge/digests"
