"""Unit tests for shared.digest_study_detail — abstract fetch + cache + translate.

The state.db cache is isolated per-test by the autouse ``isolated_db`` fixture
(tests/conftest.py). ``fetch`` and ``llm`` are injected, so nothing hits NCBI
or an LLM.
"""

from __future__ import annotations

from pathlib import Path

from shared import pubmed_abstract_store
from shared.digest_indexer import DigestIndexer
from shared.digest_study_detail import load_study_detail

PUBMED_SAMPLE = """---
date: '2026-05-24'
created_by: robin
selected_count: 1
editor_pick_count: 1
type: digest
---

# PubMed 每日精選 — 2026-05-24

> 測試用。

## ⭐ Editor's Picks

### 1. Semaglutide trial

- **Journal**: NEJM (Q1 · SJR 18.500)
- **Domain**: `metabolic`
- **Score**: 3.6  (R4/I4/C3/A2/F4/N4)
- **Verdict**: Semaglutide 對代謝症候群有效。
- **Why**: 規模大、follow-up 長。
- **→** [[pubmed-42174253]] · [PubMed](https://pubmed.ncbi.nlm.nih.gov/42174253/)
"""

PMID = "42174253"


def _seed_vault(tmp_path: Path) -> DigestIndexer:
    pm = tmp_path / "KB" / "Wiki" / "Digests" / "PubMed"
    pm.mkdir(parents=True)
    (pm / "2026-05-24.md").write_text(PUBMED_SAMPLE, encoding="utf-8")
    return DigestIndexer(tmp_path)


def _article(**over) -> dict:
    base = {
        "pmid": PMID,
        "title": "Semaglutide trial",
        "journal": "New England Journal of Medicine",
        "abstract": "BACKGROUND: A large trial.\nRESULTS: It worked.",
        "pub_date": "2026 May 1",
        "authors": "Jane Doe, John Roe",
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{PMID}/",
        "issn": "0028-4793",
        "doi": "10.1056/NEJMoa000000",
        "pmcid": "PMC12345678",
    }
    base.update(over)
    return base


def test_cache_miss_fetches_and_translates(tmp_path):
    idx = _seed_vault(tmp_path)
    fetch_calls: list[list[str]] = []
    llm_calls: list[tuple] = []

    def fake_fetch(pmids):
        fetch_calls.append(list(pmids))
        return [_article()]

    def fake_llm(prompt, **kw):
        llm_calls.append((prompt, kw))
        return "背景：一項大型試驗。\n結果：有效。"

    d = load_study_detail(idx, "2026-05-24", PMID, llm=fake_llm, fetch=fake_fetch)

    assert d is not None
    assert d.has_abstract
    assert d.abstract.startswith("BACKGROUND")
    assert d.abstract_zh == "背景：一項大型試驗。\n結果：有效。"
    assert d.authors == "Jane Doe, John Roe"
    assert d.pub_date == "2026 May 1"
    assert d.doi == "10.1056/NEJMoa000000"
    assert d.pmcid == "PMC12345678"
    assert d.study.title == "Semaglutide trial"
    assert fetch_calls == [[PMID]]
    assert len(llm_calls) == 1
    # Translation call is routed to the pinned model + carries the abstract.
    assert llm_calls[0][1]["model"] == "claude-sonnet-4-6"
    assert "A large trial" in llm_calls[0][0]


def test_second_view_is_cache_hit(tmp_path):
    idx = _seed_vault(tmp_path)
    fetch_calls: list = []
    llm_calls: list = []

    def fake_fetch(pmids):
        fetch_calls.append(list(pmids))
        return [_article()]

    def fake_llm(prompt, **kw):
        llm_calls.append(prompt)
        return "翻譯結果"

    load_study_detail(idx, "2026-05-24", PMID, llm=fake_llm, fetch=fake_fetch)
    d2 = load_study_detail(idx, "2026-05-24", PMID, llm=fake_llm, fetch=fake_fetch)

    assert d2.abstract_zh == "翻譯結果"
    assert fetch_calls == [[PMID]]  # not fetched again
    assert len(llm_calls) == 1  # not translated again


def test_no_abstract_skips_translation(tmp_path):
    idx = _seed_vault(tmp_path)
    llm_called = False

    def fake_fetch(pmids):
        return [_article(abstract="")]

    def fake_llm(prompt, **kw):
        nonlocal llm_called
        llm_called = True
        return "should not run"

    d = load_study_detail(idx, "2026-05-24", PMID, llm=fake_llm, fetch=fake_fetch)

    assert d is not None
    assert not d.has_abstract
    assert d.abstract_zh == ""
    assert not llm_called
    # Publisher link still available for letters/correspondence.
    assert d.doi == "10.1056/NEJMoa000000"


def test_pmid_not_in_digest_returns_none(tmp_path):
    idx = _seed_vault(tmp_path)
    d = load_study_detail(
        idx, "2026-05-24", "99999999", llm=lambda *a, **k: "x", fetch=lambda p: [_article()]
    )
    assert d is None


def test_fetch_failure_surfaces_error_and_does_not_cache(tmp_path):
    idx = _seed_vault(tmp_path)

    def boom(pmids):
        raise RuntimeError("network down")

    d = load_study_detail(idx, "2026-05-24", PMID, llm=lambda *a, **k: "x", fetch=boom)

    assert d is not None
    assert not d.has_abstract
    assert d.fetch_error is not None
    assert "RuntimeError" in d.fetch_error
    # Nothing cached → a later view can retry the fetch.
    assert pubmed_abstract_store.get(PMID) is None


def test_empty_fetch_result_surfaces_error(tmp_path):
    idx = _seed_vault(tmp_path)
    d = load_study_detail(idx, "2026-05-24", PMID, llm=lambda *a, **k: "x", fetch=lambda p: [])
    assert d.fetch_error is not None
    assert pubmed_abstract_store.get(PMID) is None


def test_translation_failure_keeps_english_and_retries(tmp_path):
    idx = _seed_vault(tmp_path)
    fetch_calls: list = []
    state = {"llm_calls": 0}

    def fake_fetch(pmids):
        fetch_calls.append(list(pmids))
        return [_article()]

    def flaky_llm(prompt, **kw):
        state["llm_calls"] += 1
        if state["llm_calls"] == 1:
            raise RuntimeError("llm outage")
        return "重試後的翻譯"

    d1 = load_study_detail(idx, "2026-05-24", PMID, llm=flaky_llm, fetch=fake_fetch)
    assert d1.has_abstract
    assert d1.abstract  # English still shown
    assert d1.abstract_zh == ""
    assert d1.translate_error is not None
    # English abstract cached; translation left NULL for retry.
    row = pubmed_abstract_store.get(PMID)
    assert row["abstract"].startswith("BACKGROUND")
    assert row["abstract_zh"] is None

    d2 = load_study_detail(idx, "2026-05-24", PMID, llm=flaky_llm, fetch=fake_fetch)
    assert d2.abstract_zh == "重試後的翻譯"
    assert d2.translate_error is None
    assert fetch_calls == [[PMID]]  # abstract came from cache, no re-fetch
