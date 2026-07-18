"""Tests for thousand_sunny.routers.bridge_digests — Tier A read viewer."""

from __future__ import annotations

import importlib
from datetime import datetime as _stdlib_datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

# Pin the indexer's clock to the sample-data date so the 7-day window
# always covers the fixture digests, no matter when the test runs. The
# indexer reads ``datetime.now(ZoneInfo("Asia/Taipei"))`` in three places
# (``last_n_days`` / ``last_n_days_by_date`` / ``today_taipei``); we patch
# the module-level ``datetime`` attribute so all three see the same fixed
# date.
_PINNED_TODAY = _stdlib_datetime(2026, 5, 24, 12, 0, tzinfo=ZoneInfo("Asia/Taipei"))


class _PinnedDatetime(_stdlib_datetime):
    """``datetime`` subclass whose ``now()`` returns the pinned instant.

    Subclass (not a plain stub) so any ``datetime.fromisoformat`` /
    ``datetime.combine`` calls in the indexer keep working — only
    ``now()`` is overridden.
    """

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        if tz is None:
            return _PINNED_TODAY.replace(tzinfo=None)
        return _PINNED_TODAY.astimezone(tz)


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

- **Journal**: NEJM (Q1 · SJR 18.500)
- **Domain**: `metabolic`
- **Score**: 3.6  (R4/I4/C3/A2/F4/N4)
- **Verdict**: Semaglutide 對代謝症候群有效。
- **Why**: 規模大、follow-up 長。
- **→** [[pubmed-42174253]] · [PubMed](https://pubmed.ncbi.nlm.nih.gov/42174253/)
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

    import shared.digest_indexer as di_module
    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.bridge_digests as bd_module

    # Pin "today" BEFORE reloading bd_module so its module-level
    # ``today_taipei`` reference (used in template context) sees the
    # frozen clock.
    monkeypatch.setattr(di_module, "datetime", _PinnedDatetime)

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
        # Intro (before first H2) renders via markdown
        assert "<h1>PubMed 每日精選 — 2026-05-24</h1>" in body
        assert "blockquote" in body
        assert "Nature 研究" in body
        # Per-entry sections render as cards (new design — no full body dump)
        assert "Semaglutide trial" in body
        assert 'class="digest-card' in body

    def test_pubmed_card_external_link(self, client):
        """The new card layout exposes the PubMed URL via the card footer's
        external_url anchor, not via inline wikilink rendering."""
        r = client.get("/bridge/digests/pubmed/2026-05-24")
        assert 'href="https://pubmed.ncbi.nlm.nih.gov/42174253/"' in r.text
        # Sanity: rendered as `PubMed →` footer link, not wikilink
        assert "PubMed →" in r.text

    def test_pubmed_card_journal_meta(self, client):
        """Card meta row shows journal + quartile + domain."""
        r = client.get("/bridge/digests/pubmed/2026-05-24")
        body = r.text
        assert "NEJM" in body
        assert "Q1" in body
        assert "metabolic" in body

    def test_pubmed_card_verdict_and_why(self, client):
        """Verdict + Why blocks render with their kicker labels."""
        r = client.get("/bridge/digests/pubmed/2026-05-24")
        body = r.text
        assert "Verdict" in body
        assert "Semaglutide 對代謝症候群有效" in body
        assert "Why this matters" in body
        assert "規模大、follow-up 長" in body

    def test_missing_digest_404(self, client):
        r = client.get("/bridge/digests/pubmed/2026-01-01")
        assert r.status_code == 404

    def test_unknown_type_404(self, client):
        r = client.get("/bridge/digests/podcast/2026-05-24")
        assert r.status_code == 404

    def test_invalid_date_404(self, client):
        r = client.get("/bridge/digests/pubmed/not-a-date")
        assert r.status_code == 404


class TestStudyDetail:
    """Per-study PubMed detail page: abstract fetch + cache + zh-TW translation.

    ``efetch_abstracts`` is patched on ``shared.digest_study_detail`` (its
    module-level import); the LLM goes through the ``mock_llm_response``
    facade fixture. The state.db cache is isolated by the autouse fixture.
    """

    def _patch_fetch(self, monkeypatch, article=None, *, counter=None):
        import shared.digest_study_detail as dsd

        def fake_fetch(pmids):
            if counter is not None:
                counter.append(list(pmids))
            art = (
                dict(article)
                if article
                else {
                    "pmid": pmids[0],
                    "title": "Semaglutide trial",
                    "journal": "New England Journal of Medicine",
                    "abstract": "BACKGROUND: A large trial.\nRESULTS: It worked.",
                    "pub_date": "2026 May 1",
                    "authors": "Jane Doe, John Roe",
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmids[0]}/",
                    "issn": "0028-4793",
                    "doi": "10.1056/NEJMoa000000",
                    "pmcid": "PMC12345678",
                }
            )
            art.setdefault("pmid", pmids[0])
            return [art]

        monkeypatch.setattr(dsd, "efetch_abstracts", fake_fetch)

    def test_renders_abstract_and_translation(self, client, monkeypatch, mock_llm_response):
        self._patch_fetch(monkeypatch)
        mock_llm_response("背景：一項大型試驗。\n結果：有效。")

        r = client.get("/bridge/digests/pubmed/2026-05-24/42174253")
        assert r.status_code == 200
        body = r.text
        assert "Semaglutide trial" in body
        assert "中文翻譯" in body
        assert "背景：一項大型試驗" in body  # translation
        assert "BACKGROUND: A large trial" in body  # english original
        assert "Jane Doe" in body  # authors
        assert "PMID" in body
        # Digest-side framing carried over.
        assert "Semaglutide 對代謝症候群有效" in body

    def test_publisher_and_pmc_links_render(self, client, monkeypatch, mock_llm_response):
        self._patch_fetch(monkeypatch)
        mock_llm_response("譯文")
        r = client.get("/bridge/digests/pubmed/2026-05-24/42174253")
        assert r.status_code == 200
        body = r.text
        # DOI resolver → publisher's own page (e.g. Nature/NEJM/Lancet).
        assert "https://doi.org/10.1056/NEJMoa000000" in body
        assert "期刊原文" in body
        # PMC free-full-text link when a PMCID is present.
        assert "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12345678/" in body

    def test_no_abstract_still_links_to_publisher(self, client, monkeypatch, mock_llm_response):
        self._patch_fetch(
            monkeypatch,
            article={
                "title": "Letter",
                "journal": "The Lancet",
                "abstract": "",
                "pub_date": "2026 Jul",
                "authors": "",
                "url": "",
                "issn": "",
                "doi": "10.1016/S0140-6736(26)00001-0",
                "pmcid": "",
            },
        )
        mock_llm_response("unused")
        r = client.get("/bridge/digests/pubmed/2026-05-24/42174253")
        assert r.status_code == 200
        assert "未提供此文獻的 abstract" in r.text
        assert "https://doi.org/10.1016/S0140-6736(26)00001-0" in r.text

    def test_translation_model_is_sonnet(self, client, monkeypatch, mock_llm_response):
        self._patch_fetch(monkeypatch)
        llm = mock_llm_response("譯文")
        client.get("/bridge/digests/pubmed/2026-05-24/42174253")
        assert llm.ask.call_args.kwargs["model"] == "claude-sonnet-4-6"

    def test_cache_hit_second_view_no_refetch(self, client, monkeypatch, mock_llm_response):
        calls: list = []
        self._patch_fetch(monkeypatch, counter=calls)
        llm = mock_llm_response("譯文")

        client.get("/bridge/digests/pubmed/2026-05-24/42174253")
        client.get("/bridge/digests/pubmed/2026-05-24/42174253")

        assert calls == [["42174253"]]  # fetched once
        assert llm.ask.call_count == 1  # translated once

    def test_no_abstract_shows_notice_no_llm(self, client, monkeypatch, mock_llm_response):
        self._patch_fetch(
            monkeypatch,
            article={
                "title": "Letter to editor",
                "journal": "",
                "abstract": "",
                "pub_date": "",
                "authors": "",
                "url": "",
                "issn": "",
            },
        )
        llm = mock_llm_response("should-not-run")

        r = client.get("/bridge/digests/pubmed/2026-05-24/42174253")
        assert r.status_code == 200
        assert "未提供此文獻的 abstract" in r.text
        llm.ask.assert_not_called()

    def test_pmid_not_in_digest_404(self, client):
        r = client.get("/bridge/digests/pubmed/2026-05-24/99999999")
        assert r.status_code == 404

    def test_non_digit_pmid_404(self, client):
        r = client.get("/bridge/digests/pubmed/2026-05-24/not-a-pmid")
        assert r.status_code == 404

    def test_missing_digest_day_404(self, client):
        r = client.get("/bridge/digests/pubmed/2026-01-01/42174253")
        assert r.status_code == 404

    def test_card_title_links_to_detail(self, client):
        """Detail page PubMed cards expose a title link to the study page."""
        r = client.get("/bridge/digests/pubmed/2026-05-24")
        assert "/bridge/digests/pubmed/2026-05-24/42174253" in r.text
        assert "digest-card-title-lk" in r.text

    def test_requires_auth(self, monkeypatch, tmp_path):
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
        r = c.get("/bridge/digests/pubmed/2026-05-24/42174253", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/login?next=/bridge/digests/pubmed/2026-05-24/42174253"


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

    def test_ask_redirects_to_login(self, monkeypatch, tmp_path):
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
        r = c.get("/bridge/digests/ask", follow_redirects=False)
        assert r.status_code == 302


class TestAsk:
    def test_get_renders_empty_form(self, client):
        r = client.get("/bridge/digests/ask")
        assert r.status_code == 200
        assert "查詢" in r.text
        assert 'name="question"' in r.text
        assert 'name="days"' in r.text
        assert "PubMed" in r.text
        assert "AI News" in r.text

    def test_post_invalid_question_shows_error(self, client):
        r = client.post(
            "/bridge/digests/ask",
            data={"question": "", "days": "7"},
        )
        assert r.status_code == 200
        assert "請輸入問題" in r.text

    def test_post_invalid_days_shows_error(self, client):
        r = client.post(
            "/bridge/digests/ask",
            data={"question": "test", "days": "9999"},
        )
        assert r.status_code == 200
        assert "天數" in r.text

    def test_post_dispatches_to_llm_and_renders(self, client, mock_llm_response):
        llm = mock_llm_response("LLM 模擬回答：找到 1 篇相關研究")

        r = client.post(
            "/bridge/digests/ask",
            data={"question": "今天 PubMed 有什麼？", "days": "7", "types": "pubmed"},
        )
        assert r.status_code == 200
        assert "LLM 模擬回答" in r.text
        assert "今天 PubMed 有什麼" in r.text
        assert "引用來源" in r.text
        prompt = llm.ask.call_args.args[0]
        assert "Nature 研究" in prompt  # PubMed digest body in context
        assert llm.ask.call_args.kwargs["model"] == "claude-sonnet-4-6"

    def test_post_llm_failure_renders_error(self, client, mock_llm_response):
        mock_llm_response(side_effect=RuntimeError("simulated outage"))

        r = client.post(
            "/bridge/digests/ask",
            data={"question": "q", "days": "7"},
        )
        assert r.status_code == 200
        assert "查詢失敗" in r.text

    def test_post_empty_scope_no_llm_call(self, client, monkeypatch, tmp_path, mock_llm_response):
        # Re-build empty vault client
        monkeypatch.setenv("VAULT_PATH", str(tmp_path / "empty"))
        (tmp_path / "empty").mkdir()
        import thousand_sunny.app as app_module
        import thousand_sunny.routers.bridge_digests as bd_module

        importlib.reload(bd_module)
        importlib.reload(app_module)
        c = TestClient(app_module.app)

        llm = mock_llm_response("x")

        r = c.post("/bridge/digests/ask", data={"question": "q", "days": "7"})
        assert r.status_code == 200
        llm.ask.assert_not_called()
        assert "無 digest 可查" in r.text

    def test_post_shows_truncation_date_range_and_drop_count(self, client, monkeypatch):
        import thousand_sunny.routers.bridge_digests as bd_module
        from shared.digest_ask import AskResult
        from shared.digest_indexer import DigestEntry

        fake_entry = DigestEntry(
            type="pubmed",
            date="2026-05-24",
            relative_path="KB/Wiki/Digests/PubMed/2026-05-24.md",
            selected_count=5,
            editor_pick_count=3,
            summary="test",
        )
        fake_result = AskResult(
            question="test truncation",
            answer="some answer",
            sources=(fake_entry,),
            days=30,
            types=("pubmed",),
            context_chars=100,
            truncated=True,
            dropped_count=9,
            oldest_included_date="2026-05-04",
        )
        monkeypatch.setattr(bd_module, "ask", lambda req, idx: fake_result)

        r = client.post(
            "/bridge/digests/ask",
            data={"question": "test truncation", "days": "30", "types": "pubmed"},
        )
        assert r.status_code == 200
        assert "2026-05-04" in r.text
        assert "9" in r.text
        assert "已捨棄" in r.text

    def test_post_answer_rendered_as_markdown(self, client, mock_llm_response):
        mock_llm_response("**bold** text\n\n- list item")

        r = client.post(
            "/bridge/digests/ask",
            data={"question": "test markdown rendering", "days": "7", "types": "pubmed"},
        )
        assert r.status_code == 200
        assert "<strong>bold</strong>" in r.text
        assert "<li>list item</li>" in r.text

    def test_landing_has_ask_cta(self, client):
        r = client.get("/bridge/digests")
        assert "/bridge/digests/ask" in r.text

    def test_post_renders_scope_audit_details(self, client, mock_llm_response):
        mock_llm_response("answer text")

        r = client.post(
            "/bridge/digests/ask",
            data={"question": "audit test", "days": "7", "types": "pubmed"},
        )
        assert r.status_code == 200
        assert "<details" in r.text
        assert "ask-scope-audit" in r.text
        assert "查詢範圍（audit）" in r.text
        assert "state.api_calls.scope_json" in r.text
        assert "audit test" in r.text  # question echoed in details block


class TestConflictBanner:
    def test_no_banner_when_no_conflicts(self, client):
        r = client.get("/bridge/digests")
        assert r.status_code == 200
        assert "Syncthing 衝突檔" not in r.text
        assert 'role="alert"' not in r.text

    def test_banner_appears_with_count_and_path(self, monkeypatch, tmp_path):
        monkeypatch.delenv("WEB_PASSWORD", raising=False)
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))
        monkeypatch.setenv("DISABLE_ROBIN", "1")
        monkeypatch.setenv("NAKAMA_DOC_INDEX_DB_PATH", str(tmp_path / "x.db"))

        pm = tmp_path / "KB" / "Wiki" / "Digests" / "PubMed"
        pm.mkdir(parents=True)
        (pm / "2026-05-24.md").write_text(PUBMED_SAMPLE, encoding="utf-8")
        (pm / "2026-05-24.sync-conflict-20260524-101530-WIN.md").write_text("x", encoding="utf-8")

        import thousand_sunny.app as app_module
        import thousand_sunny.routers.bridge_digests as bd_module

        importlib.reload(bd_module)
        importlib.reload(app_module)
        c = TestClient(app_module.app)
        r = c.get("/bridge/digests")
        assert r.status_code == 200
        assert "digest-conflict-banner" in r.text
        assert "Syncthing 衝突檔 · 1" in r.text
        assert "2026-05-24.sync-conflict-20260524-101530-WIN.md" in r.text
        assert "WIN" in r.text
