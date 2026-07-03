"""Tests for agents/robin/kb_search.py.

覆蓋：vault walking / frontmatter title fallback / type normalization /
preview truncation / Claude Haiku ranking response parsing 各路徑，
以及 engine="hybrid" 委派路徑（ADR-021 §2 — 預設 engine）。

ADR-021 §2 後 default engine 改 hybrid；haiku 路徑的 vault-walking 測試
顯式傳 ``engine="haiku"``。

LLM 全 mock 在 ``shared.llm`` facade（conftest ``mock_llm_response``；
2026-07-03 架構審計 — 業務測試不 mock provider client 內部）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.robin.kb_search import search_kb

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mk_page(dir_path: Path, stem: str, title: str, body: str) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    content = f"---\ntitle: {title}\n---\n{body}"
    (dir_path / f"{stem}.md").write_text(content, encoding="utf-8")


def _sent_prompt(llm) -> str:
    """Return the prompt the production code sent through the facade."""
    return llm.ask.call_args.args[0]


@pytest.fixture
def vault(tmp_path):
    """Minimal KB/Wiki vault with 3 subdirs (initially empty)."""
    wiki = tmp_path / "KB" / "Wiki"
    (wiki / "Sources").mkdir(parents=True)
    (wiki / "Concepts").mkdir()
    (wiki / "Entities").mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# Empty / missing vault edge cases
# ---------------------------------------------------------------------------


def test_empty_vault_returns_empty_list(tmp_path, mock_llm_response):
    """KB/Wiki dirs 不存在 → 不打 LLM、回空 list。"""
    llm = mock_llm_response("[]")

    assert search_kb("anything", tmp_path, engine="haiku") == []
    llm.ask.assert_not_called()


def test_vault_with_only_empty_subdirs_returns_empty(vault, mock_llm_response):
    """3 個 subdir 存在但無 .md 檔案 → 回 []、不打 LLM。"""
    llm = mock_llm_response("[]")

    assert search_kb("topic", vault, engine="haiku") == []
    llm.ask.assert_not_called()


# ---------------------------------------------------------------------------
# Page collection from 3 subdirs + type normalization
# ---------------------------------------------------------------------------


def test_type_normalization_sources_concepts_entities(vault, mock_llm_response):
    """Sources → source / Concepts → concept / Entities → entity（特別處理 Entit → entity）。"""
    _mk_page(vault / "KB" / "Wiki" / "Sources", "paper1", "Paper 1", "摘要 A")
    _mk_page(vault / "KB" / "Wiki" / "Concepts", "circadian", "生理時鐘", "概念 B")
    _mk_page(vault / "KB" / "Wiki" / "Entities", "matt-walker", "Matt Walker", "人物 C")

    mock_llm_response(
        '[{"index": 1, "relevance_reason": "r1"},'
        ' {"index": 2, "relevance_reason": "r2"},'
        ' {"index": 3, "relevance_reason": "r3"}]'
    )

    results = search_kb("睡眠", vault, engine="haiku")
    types = {r["type"] for r in results}
    assert types == {"source", "concept", "entity"}


def test_title_from_frontmatter_preferred_over_filename(vault, mock_llm_response):
    _mk_page(vault / "KB" / "Wiki" / "Sources", "why-we-sleep", "為什麼要睡覺", "body")
    mock_llm_response('[{"index": 1, "relevance_reason": "主題相關"}]')

    results = search_kb("睡眠科學", vault, engine="haiku")
    assert results[0]["title"] == "為什麼要睡覺"


def test_title_falls_back_to_filename_when_frontmatter_missing(vault, mock_llm_response):
    """無 frontmatter title → 用 file stem。"""
    (vault / "KB" / "Wiki" / "Sources" / "no-frontmatter.md").write_text(
        "just body no frontmatter at all", encoding="utf-8"
    )
    mock_llm_response('[{"index": 1, "relevance_reason": "r"}]')

    results = search_kb("query", vault, engine="haiku")
    assert results[0]["title"] == "no-frontmatter"


def test_preview_truncated_to_200_chars(vault, mock_llm_response):
    """body > 200 char 的 page，送給 LLM 的 preview 應該截到 200。"""
    long_body = "A" * 500
    _mk_page(vault / "KB" / "Wiki" / "Sources", "long", "Long", long_body)

    llm = mock_llm_response("[]")

    search_kb("q", vault, engine="haiku")
    # preview 在 prompt 裡；不該含完整 500 個 A
    prompt = _sent_prompt(llm)
    assert "A" * 200 in prompt
    assert "A" * 201 not in prompt


# ---------------------------------------------------------------------------
# Claude response parsing
# ---------------------------------------------------------------------------


def test_happy_path_returns_ranked_pages_with_reasons(vault, mock_llm_response):
    _mk_page(vault / "KB" / "Wiki" / "Sources", "s1", "Page 1", "body1")
    _mk_page(vault / "KB" / "Wiki" / "Sources", "s2", "Page 2", "body2")
    mock_llm_response(
        '[{"index": 2, "relevance_reason": "最契合"}, {"index": 1, "relevance_reason": "次相關"}]'
    )

    results = search_kb("主題", vault, engine="haiku")
    assert len(results) == 2
    # Order matches LLM ranking
    assert results[0]["title"] == "Page 2"
    assert results[0]["relevance_reason"] == "最契合"
    assert results[1]["title"] == "Page 1"
    assert results[1]["relevance_reason"] == "次相關"


def test_response_without_json_array_returns_empty(vault, mock_llm_response):
    _mk_page(vault / "KB" / "Wiki" / "Sources", "s1", "Page 1", "body")
    mock_llm_response("Sorry, I could not find anything relevant.")

    assert search_kb("主題", vault, engine="haiku") == []


def test_response_with_invalid_json_returns_empty(vault, mock_llm_response):
    """regex 抓到 [...] 但 json.loads 炸 → 回 []。"""
    _mk_page(vault / "KB" / "Wiki" / "Sources", "s1", "Page 1", "body")
    mock_llm_response('[{"index": 1, invalid-json: true}]')

    assert search_kb("主題", vault, engine="haiku") == []


def test_out_of_range_index_is_skipped(vault, mock_llm_response):
    """LLM 回傳的 index 超出 pages 陣列 → 跳過（不 crash）。"""
    _mk_page(vault / "KB" / "Wiki" / "Sources", "only", "Only", "body")
    mock_llm_response(
        '[{"index": 1, "relevance_reason": "ok"}, {"index": 99, "relevance_reason": "ghost"}]'
    )

    results = search_kb("q", vault, engine="haiku")
    assert len(results) == 1
    assert results[0]["title"] == "Only"


def test_response_with_prose_prefix_then_json_parses_array(vault, mock_llm_response):
    """LLM 常把 JSON 包在說明文字裡 — regex 抓第一個 [...] block 即可。"""
    _mk_page(vault / "KB" / "Wiki" / "Sources", "s1", "Page 1", "body")
    mock_llm_response('以下是相關頁面：\n[{"index": 1, "relevance_reason": "相關"}]\n以上。')

    results = search_kb("q", vault, engine="haiku")
    assert len(results) == 1
    assert results[0]["relevance_reason"] == "相關"


# ---------------------------------------------------------------------------
# File read robustness
# ---------------------------------------------------------------------------


def test_unreadable_file_is_skipped_gracefully(vault, monkeypatch, mock_llm_response):
    """讀檔噴錯 → skip 該檔、其他檔照走。"""
    _mk_page(vault / "KB" / "Wiki" / "Sources", "good", "Good", "body")

    original_read = Path.read_text

    def _selective_read(self, *args, **kwargs):
        if self.name == "good.md":
            raise OSError("permission denied simulated")
        return original_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _selective_read)

    # 只有一個檔、且它讀不到 → pages 為空 → 直接回 [] 不打 LLM
    llm = mock_llm_response("[]")

    assert search_kb("q", vault, engine="haiku") == []
    llm.ask.assert_not_called()


# ---------------------------------------------------------------------------
# Result schema
# ---------------------------------------------------------------------------


def test_result_has_expected_keys(vault, mock_llm_response):
    _mk_page(vault / "KB" / "Wiki" / "Sources", "s1", "P1", "body")
    mock_llm_response('[{"index": 1, "relevance_reason": "r"}]')

    results = search_kb("q", vault, engine="haiku")
    assert set(results[0].keys()) == {
        "type",
        "title",
        "path",
        "preview",
        "relevance_reason",
    }
    assert results[0]["path"] == "KB/Wiki/Sources/s1"


# ---------------------------------------------------------------------------
# Purpose-dispatched prompts (Slice D.2)
# ---------------------------------------------------------------------------


def test_default_purpose_uses_general_intro(vault, mock_llm_response):
    """Default purpose is "general" — neutral KB-query framing, no YouTube wording."""
    _mk_page(vault / "KB" / "Wiki" / "Sources", "s1", "P1", "body")
    llm = mock_llm_response("[]")

    search_kb("Zone 2 訓練", vault, engine="haiku")

    prompt = _sent_prompt(llm)
    assert "想查詢知識庫" in prompt
    assert "YouTube" not in prompt


def test_purpose_youtube_preserves_video_framing(vault, mock_llm_response):
    """purpose="youtube" 保留原本影片製作 lens — Zoro / Robin 影片 pipeline 用。"""
    _mk_page(vault / "KB" / "Wiki" / "Sources", "s1", "P1", "body")
    llm = mock_llm_response("[]")

    search_kb("睡眠科學", vault, purpose="youtube", engine="haiku")

    assert "YouTube 影片" in _sent_prompt(llm)


def test_purpose_seo_audit_frames_internal_link_intent(vault, mock_llm_response):
    """purpose="seo_audit" 給 Haiku 部落格 internal link 補強的上下文。"""
    _mk_page(vault / "KB" / "Wiki" / "Sources", "s1", "P1", "body")
    llm = mock_llm_response("[]")

    search_kb("zone 2 訓練", vault, purpose="seo_audit", engine="haiku")

    prompt = _sent_prompt(llm)
    assert "SEO 體檢" in prompt
    assert "internal link" in prompt
    assert "YouTube" in prompt  # 但只在「請排後」這條尾部提示


def test_purpose_blog_compose_frames_article_writing(vault, mock_llm_response):
    """purpose="blog_compose" 給 Brook 撰文場景的 lens。"""
    _mk_page(vault / "KB" / "Wiki" / "Sources", "s1", "P1", "body")
    llm = mock_llm_response("[]")

    search_kb("肌力訓練飲食", vault, purpose="blog_compose", engine="haiku")

    assert "撰寫一篇部落格" in _sent_prompt(llm)


def test_all_purposes_produce_same_output_shape(vault, mock_llm_response):
    """所有 purpose 走同一套 JSON parsing；輸出 keys 不變。"""
    _mk_page(vault / "KB" / "Wiki" / "Sources", "s1", "Page 1", "body1")
    mock_llm_response('[{"index": 1, "relevance_reason": "r"}]')

    for purpose in ("general", "youtube", "seo_audit", "blog_compose"):
        results = search_kb("q", vault, purpose=purpose, engine="haiku")  # type: ignore[arg-type]
        assert results, f"purpose={purpose} returned empty"
        assert set(results[0].keys()) == {
            "type",
            "title",
            "path",
            "preview",
            "relevance_reason",
        }


# ---------------------------------------------------------------------------
# A5 follow-up — invalid purpose raises ValueError instead of silent fallback
# ---------------------------------------------------------------------------


def test_invalid_purpose_raises_value_error(vault):
    """A5 follow-up: 無效 purpose 字串應 raise ValueError，
    不再 silently fall through 到 general。"""
    _mk_page(vault / "KB" / "Wiki" / "Concepts", "x", "X", "body")
    with pytest.raises(ValueError, match="Unknown purpose"):
        search_kb("q", vault, purpose="not_a_real_purpose", engine="haiku")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# engine="hybrid" path (issue #431)
# ---------------------------------------------------------------------------


def test_engine_hybrid_is_default(vault, monkeypatch, mock_llm_response):
    """ADR-021 §2: engine defaults to 'hybrid' — Haiku must be opt-in."""
    from shared.kb_hybrid_search import SearchHit

    fake_hit = SearchHit(
        chunk_id=7,
        path="KB/Wiki/Concepts/test",
        heading="定義",
        page_title="Test",
        chunk_text="x",
        rrf_score=0.05,
        lane_ranks={"bm25": 1},
    )
    monkeypatch.setattr("shared.kb_hybrid_search.search", lambda q, tk: [fake_hit])
    llm = mock_llm_response("[]")

    results = search_kb("query", vault)

    # Haiku must NOT fire — hybrid is the default lens.
    llm.ask.assert_not_called()
    assert len(results) == 1
    assert results[0]["chunk_id"] == 7  # chunk-level metadata present


def test_engine_hybrid_delegates_to_kb_hybrid_search(vault, monkeypatch, mock_llm_response):
    """engine='hybrid' calls kb_hybrid_search.search() — NOT the Haiku LLM.

    Patches shared.kb_hybrid_search.search at the module level (correct
    approach: _hybrid_results does `from shared import kb_hybrid_search`
    then `kb_hybrid_search.search(...)` — attribute lookup on the module
    object, so patching the module attribute is effective).
    """
    from shared.kb_hybrid_search import SearchHit

    fake_hit = SearchHit(
        chunk_id=1,
        path="KB/Wiki/Concepts/test",
        heading="定義",
        page_title="Test Concept",
        chunk_text="chunk body text here",
        rrf_score=0.05,
        lane_ranks={"bm25": 1, "vec": 2},
    )
    monkeypatch.setattr("shared.kb_hybrid_search.search", lambda q, tk: [fake_hit])

    llm = mock_llm_response("[]")

    results = search_kb("query", vault, engine="hybrid")

    # Haiku should NOT have been called
    llm.ask.assert_not_called()
    assert len(results) == 1
    # ADR-021 §2 + Codex #5: hybrid wrapper must expose chunk-level metadata.
    assert set(results[0].keys()) == {
        "type",
        "title",
        "path",
        "preview",
        "relevance_reason",
        "chunk_text",
        "heading",
        "chunk_id",
        "rrf_score",
    }
    assert results[0]["path"] == "KB/Wiki/Concepts/test"
    assert results[0]["title"] == "Test Concept"
    assert results[0]["type"] == "concept"
    assert results[0]["chunk_id"] == 1
    assert results[0]["heading"] == "定義"
    assert results[0]["chunk_text"] == "chunk body text here"
    assert results[0]["rrf_score"] == pytest.approx(0.05)


def test_engine_hybrid_keeps_multiple_chunks_per_page(vault, monkeypatch, mock_llm_response):
    """Multiple chunks from the same page → all kept (one per chunk).

    Brook synthesize builds evidence cards per-chunk; deduplicating by page
    would collapse two distinct annotations on the same source.
    """
    from shared.kb_hybrid_search import SearchHit

    hits = [
        SearchHit(
            chunk_id=i,
            path="KB/Wiki/Concepts/same-page",
            heading=f"Section {i}",
            page_title="Same Page",
            chunk_text=f"chunk {i}",
            rrf_score=0.05 - i * 0.001,
            lane_ranks={"bm25": i + 1},
        )
        for i in range(3)
    ]
    monkeypatch.setattr("shared.kb_hybrid_search.search", lambda q, tk: hits)
    mock_llm_response("[]")

    results = search_kb("query", vault, engine="hybrid")
    assert len(results) == 3
    assert [r["chunk_id"] for r in results] == [0, 1, 2]


def test_engine_hybrid_annotation_chunk_has_annotation_type(vault, monkeypatch, mock_llm_response):
    """Annotation chunks (KB/Annotations/{slug}) → type='annotation'.

    ADR-021 §2: annotation chunks are indexed by `_index_annotations` under
    `KB/Annotations/{slug}` (not under `KB/Wiki/...`). The wrapper must stamp
    `type='annotation'` so Brook synthesize can route the evidence correctly.
    """
    from shared.kb_hybrid_search import SearchHit

    hit = SearchHit(
        chunk_id=42,
        path="KB/Annotations/why-we-sleep",
        heading="",
        page_title="why-we-sleep annotations",
        chunk_text="The brain consolidates memory during REM.",
        rrf_score=0.04,
        lane_ranks={"bm25": 1, "vec": 2},
    )
    monkeypatch.setattr("shared.kb_hybrid_search.search", lambda q, tk: [hit])
    mock_llm_response("[]")

    results = search_kb("睡眠", vault, engine="hybrid")
    assert len(results) == 1
    assert results[0]["type"] == "annotation"
    assert results[0]["path"] == "KB/Annotations/why-we-sleep"


def test_engine_hybrid_empty_index_returns_empty(vault, monkeypatch, mock_llm_response):
    """engine='hybrid' with no indexed data returns []."""
    monkeypatch.setattr("shared.kb_hybrid_search.search", lambda q, tk: [])
    mock_llm_response("[]")

    results = search_kb("query", vault, engine="hybrid")
    assert results == []
