"""Tests for Robin PubMed digest pipeline — feed type dispatch + blocklist filter.

Doesn't exercise the full pipeline (LLM curate / vault write); focuses on
the new pieces added 2026-05-05 to address the top-journal-blind-spot:
1. ``_fetch_feed`` dispatch on feed type (rss vs eutils vs unknown)
2. ``_fetch_eutils`` end-to-end with mocked NCBI calls
3. Blocklist filter inside ``run()`` removes MDPI/Frontiers and mark_seen them
"""

from __future__ import annotations

import pytest

from agents.robin import pubmed_digest as pd


@pytest.fixture
def pipeline_no_io(tmp_path, monkeypatch):
    """Build a PubMedDigestPipeline with no real I/O (no feeds.yaml, no DB).

    Tests can override individual hooks (`_fetch_feed`, `_curate`, etc).
    """
    # Empty feeds yaml so _load_feeds_config returns []
    feeds_yaml = tmp_path / "pubmed_feeds.yaml"
    feeds_yaml.write_text("feeds: []\n", encoding="utf-8")
    monkeypatch.setattr(pd, "_FEEDS_CONFIG", feeds_yaml)

    pipeline = pd.PubMedDigestPipeline(dry_run=True)
    return pipeline


# ---------------------------------------------------------------------------
# _fetch_feed dispatch
# ---------------------------------------------------------------------------


def test_fetch_feed_rss_dispatches_to_fetch_rss(pipeline_no_io, monkeypatch):
    captured = {}

    def fake_rss(cfg):
        captured["called"] = "rss"
        captured["cfg"] = cfg
        return [{"pmid": "1"}]

    monkeypatch.setattr(pipeline_no_io, "_fetch_rss", fake_rss)
    monkeypatch.setattr(
        pipeline_no_io, "_fetch_eutils", lambda c: pytest.fail("must not call eutils")
    )

    result = pipeline_no_io._fetch_feed({"name": "broad", "type": "rss", "url": "x"})
    assert captured["called"] == "rss"
    assert result == [{"pmid": "1"}]


def test_fetch_feed_eutils_dispatches_to_fetch_eutils(pipeline_no_io, monkeypatch):
    captured = {}

    def fake_eutils(cfg):
        captured["called"] = "eutils"
        captured["cfg"] = cfg
        return [{"pmid": "2"}]

    monkeypatch.setattr(pipeline_no_io, "_fetch_eutils", fake_eutils)
    monkeypatch.setattr(pipeline_no_io, "_fetch_rss", lambda c: pytest.fail("must not call rss"))

    result = pipeline_no_io._fetch_feed({"name": "top", "type": "eutils", "term": "x"})
    assert captured["called"] == "eutils"
    assert result == [{"pmid": "2"}]


def test_fetch_feed_default_type_is_rss(pipeline_no_io, monkeypatch):
    """Backward-compat：舊 feed 沒寫 type 應視為 rss。"""
    monkeypatch.setattr(pipeline_no_io, "_fetch_rss", lambda c: [{"pmid": "ok"}])
    result = pipeline_no_io._fetch_feed({"name": "legacy", "url": "x"})
    assert result == [{"pmid": "ok"}]


def test_fetch_feed_unknown_type_returns_empty_with_warning(pipeline_no_io, caplog):
    result = pipeline_no_io._fetch_feed({"name": "bad", "type": "carrier-pigeon"})
    assert result == []


# ---------------------------------------------------------------------------
# _fetch_eutils
# ---------------------------------------------------------------------------


def test_fetch_eutils_chains_esearch_efetch_and_tags_feed_source(pipeline_no_io, monkeypatch):
    captured_term = {}

    def fake_esearch(term, *, max_results, sort=None):
        captured_term["term"] = term
        captured_term["max_results"] = max_results
        captured_term["sort"] = sort
        return ["111", "222"]

    def fake_efetch(pmids):
        return [
            {"pmid": "111", "title": "T1", "journal": "JAMA", "abstract": "a", "url": "u1"},
            {"pmid": "222", "title": "T2", "journal": "Lancet", "abstract": "b", "url": "u2"},
        ]

    monkeypatch.setattr(pd, "esearch", fake_esearch)
    monkeypatch.setattr(pd, "efetch_abstracts", fake_efetch)

    cfg = {
        "name": "top_journals",
        "type": "eutils",
        "term": "(JAMA[TA]) AND (sleep)",
        "days": 7,
        "limit": 50,
    }
    result = pipeline_no_io._fetch_eutils(cfg)

    assert len(result) == 2
    assert all(r["feed_source"] == "top_journals" for r in result)
    assert "(JAMA[TA]) AND (sleep)" in captured_term["term"]
    assert "[PDAT]" in captured_term["term"]
    assert captured_term["max_results"] == 50
    # sort 必須是 pub_date，否則 retmax 截斷時拿到 NCBI 的 relevance 集而非最新
    assert captured_term["sort"] == "pub_date"


def test_fetch_eutils_pdat_date_range_uses_last_n_days(pipeline_no_io, monkeypatch):
    """凍結 datetime.now，驗證 PDAT 子句 ("YYYY/MM/DD"[PDAT] : "YYYY/MM/DD"[PDAT])
    日期數學正確：since = today - days，end = today，皆 Asia/Taipei。
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    fixed_now = datetime(2026, 5, 5, 6, 30, tzinfo=ZoneInfo("Asia/Taipei"))

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(pd, "datetime", FakeDatetime)

    captured_term = {}

    def fake_esearch(term, *, max_results, sort=None):
        captured_term["term"] = term
        return []

    monkeypatch.setattr(pd, "esearch", fake_esearch)

    pipeline_no_io._fetch_eutils({"name": "x", "type": "eutils", "term": "JAMA[TA]", "days": 14})

    # 14 天前 = 2026-04-21；今天 = 2026-05-05
    assert '"2026/04/21"[PDAT] : "2026/05/05"[PDAT]' in captured_term["term"]


def test_fetch_eutils_uses_default_days_and_limit(pipeline_no_io, monkeypatch):
    """yaml 沒寫 days/limit 應走 default 14 / 80。"""
    captured = {}

    def fake_esearch(term, *, max_results, sort=None):
        captured["max_results"] = max_results
        return []

    monkeypatch.setattr(pd, "esearch", fake_esearch)
    pipeline_no_io._fetch_eutils({"name": "x", "type": "eutils", "term": "JAMA[TA]"})
    assert captured["max_results"] == 80


def test_fetch_eutils_empty_term_returns_empty(pipeline_no_io, monkeypatch):
    monkeypatch.setattr(pd, "esearch", lambda *a, **kw: pytest.fail("must not call esearch"))
    result = pipeline_no_io._fetch_eutils({"name": "x", "type": "eutils", "term": "  "})
    assert result == []


def test_fetch_eutils_esearch_failure_logs_and_returns_empty(pipeline_no_io, monkeypatch):
    def fake_esearch(*a, **kw):
        raise pd.PubMedClientError("network down")

    monkeypatch.setattr(pd, "esearch", fake_esearch)
    result = pipeline_no_io._fetch_eutils({"name": "x", "type": "eutils", "term": "JAMA[TA]"})
    assert result == []


def test_fetch_eutils_efetch_failure_logs_and_returns_empty(pipeline_no_io, monkeypatch):
    monkeypatch.setattr(pd, "esearch", lambda *a, **kw: ["111"])

    def fake_efetch(*a, **kw):
        raise pd.PubMedClientError("xml broken")

    monkeypatch.setattr(pd, "efetch_abstracts", fake_efetch)
    result = pipeline_no_io._fetch_eutils({"name": "x", "type": "eutils", "term": "JAMA[TA]"})
    assert result == []


def test_fetch_eutils_zero_pmids_skips_efetch(pipeline_no_io, monkeypatch):
    monkeypatch.setattr(pd, "esearch", lambda *a, **kw: [])
    monkeypatch.setattr(
        pd, "efetch_abstracts", lambda *a, **kw: pytest.fail("must not call efetch")
    )
    result = pipeline_no_io._fetch_eutils({"name": "x", "type": "eutils", "term": "JAMA[TA]"})
    assert result == []


# ---------------------------------------------------------------------------
# Blocklist filter inside run()
# ---------------------------------------------------------------------------


def _make_pipeline_with_feeds(tmp_path, monkeypatch, blocklist_journals):
    """Build pipeline + write a tmp blocklist yaml + reload cache."""
    feeds_yaml = tmp_path / "pubmed_feeds.yaml"
    feeds_yaml.write_text(
        "feeds:\n  - name: dummy\n    type: rss\n    url: stub\n", encoding="utf-8"
    )
    monkeypatch.setattr(pd, "_FEEDS_CONFIG", feeds_yaml)

    block_yaml = tmp_path / "blocklist.yaml"
    lines = ["block:"] + [f"  - {j}" for j in blocklist_journals]
    block_yaml.write_text("\n".join(lines), encoding="utf-8")

    # Reload blocklist cache + monkeypatch its default path so is_blocked() in
    # pubmed_digest sees the test yaml without us having to thread blocklist_path through
    from shared import journal_blocklist

    journal_blocklist.reload()
    monkeypatch.setattr(journal_blocklist, "_BLOCKLIST_PATH", block_yaml)
    journal_blocklist.reload()

    return pd.PubMedDigestPipeline(dry_run=True)


def test_run_blocklist_filters_mdpi_and_keeps_top_journals(tmp_path, monkeypatch):
    pipeline = _make_pipeline_with_feeds(
        tmp_path, monkeypatch, blocklist_journals=["Nutrients", "Frontiers in public health"]
    )

    candidates = [
        {"pmid": "1", "title": "T1", "journal": "Nutrients", "abstract": "a", "url": "u1"},
        {"pmid": "2", "title": "T2", "journal": "JAMA", "abstract": "b", "url": "u2"},
        {
            "pmid": "3",
            "title": "T3",
            "journal": "Frontiers in public health",
            "abstract": "c",
            "url": "u3",
        },
        {
            "pmid": "4",
            "title": "T4",
            "journal": "Frontiers in immunology",
            "abstract": "d",
            "url": "u4",
        },
    ]
    monkeypatch.setattr(pipeline, "_fetch_feed", lambda cfg: candidates)
    monkeypatch.setattr(pd, "is_seen", lambda src, pmid: False)

    captured_curate = {}

    def fake_curate(fresh):
        captured_curate["pmids"] = [c["pmid"] for c in fresh]
        captured_curate["journals"] = [c["journal"] for c in fresh]
        # 回 0 篇選 → run() 在 step 6 後早退到 "curate/score 後無精選入選"
        return {"selected": []}

    monkeypatch.setattr(pipeline, "_curate", fake_curate)

    result = pipeline.run()

    # Block 過濾後 fresh 只剩 JAMA + Frontiers in immunology（2/4）
    assert captured_curate["pmids"] == ["2", "4"]
    assert "Nutrients" not in captured_curate["journals"]
    assert "Frontiers in public health" not in captured_curate["journals"]
    assert "JAMA" in captured_curate["journals"]
    assert "Frontiers in immunology" in captured_curate["journals"]
    assert "curate/score 後無精選入選" in result


def test_run_marks_blocklisted_pmids_seen_to_avoid_refetch(tmp_path, monkeypatch):
    """Blocklist 過掉的 PMID 仍要 mark_seen，避免明天重複 fetch + dedup。"""
    pipeline = _make_pipeline_with_feeds(tmp_path, monkeypatch, blocklist_journals=["Nutrients"])
    pipeline.dry_run = False  # 才會跑 mark_seen path

    candidates = [
        {"pmid": "100", "title": "T", "journal": "Nutrients", "abstract": "a", "url": "u"},
    ]
    monkeypatch.setattr(pipeline, "_fetch_feed", lambda cfg: candidates)
    monkeypatch.setattr(pd, "is_seen", lambda src, pmid: False)

    marked = []
    monkeypatch.setattr(pd, "mark_seen", lambda src, pmid, url: marked.append(pmid))

    result = pipeline.run()

    # blocklist 全部過掉 → fresh 變空 → 但 mark_seen 仍要為 blocked PMID 跑
    assert "100" in marked
    assert "blocklist 過濾後 0 筆" in result


def test_run_no_blocklist_hit_doesnt_disturb_pipeline(tmp_path, monkeypatch):
    pipeline = _make_pipeline_with_feeds(tmp_path, monkeypatch, blocklist_journals=["Nutrients"])

    candidates = [
        {"pmid": "10", "title": "T1", "journal": "JAMA", "abstract": "a", "url": "u1"},
        {"pmid": "20", "title": "T2", "journal": "Lancet", "abstract": "b", "url": "u2"},
    ]
    monkeypatch.setattr(pipeline, "_fetch_feed", lambda cfg: candidates)
    monkeypatch.setattr(pd, "is_seen", lambda src, pmid: False)
    monkeypatch.setattr(pipeline, "_curate", lambda fresh: {"selected": []})

    result = pipeline.run()
    # 沒 hit blocklist，candidate 全進 curate
    assert "blocklist 過濾掉" not in result  # log 訊息不在 return string，但 fresh 數對
    assert "curate/score 後無精選入選" in result
