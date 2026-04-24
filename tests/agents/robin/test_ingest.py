"""Tests for agents/robin/ingest.py.

Goal: 0% → ~100% coverage. LLM / file-IO / interactive input 全 stub。

依 feedback_pytest_monkeypatch_where_used.md，monkeypatch 要 patch 到
``agents.robin.ingest`` 這個 namespace（module 讀名字的地方），不是原始
定義處。lazy imports（pdf_parser / chunker / local_llm）則 patch 到它們
自己的 module。
"""

from __future__ import annotations

import builtins
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agents.robin import ingest as mod
from agents.robin.ingest import (
    IngestPipeline,
    _build_robin_system_prompt,
    _truncate_at_boundary,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_vault(tmp_path: Path, monkeypatch):
    """Redirect both `agents.robin.ingest` 和 `shared.obsidian_writer` 到 tmp_path。

    `shared.obsidian_writer` 在 module load 時 `from shared.config import get_vault_path`
    綁到自己的 namespace，要分別 patch 才能指向 tmp_path。
    """
    monkeypatch.setattr(mod, "get_vault_path", lambda: tmp_path)
    monkeypatch.setattr("shared.obsidian_writer.get_vault_path", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def stub_prompts(monkeypatch):
    """Trivial load_prompt — 回傳一個可辨識的 marker 字串。"""

    def fake(agent, name, **kwargs):
        return f"<{agent}:{name}:{kwargs.get('title', '')}>"

    monkeypatch.setattr(mod, "load_prompt", fake)


@pytest.fixture
def stub_memory(monkeypatch):
    """Shared.memory 靜默化（避免 IO / 記憶系統副作用）。"""
    monkeypatch.setattr(mod, "get_context", lambda *a, **k: "")
    monkeypatch.setattr(mod, "remember", lambda **k: None)


@pytest.fixture
def set_current_agent_spy(monkeypatch):
    """Expose set_current_agent as a MagicMock so tests can assert call args."""
    spy = MagicMock()
    monkeypatch.setattr(mod, "set_current_agent", spy)
    return spy


@pytest.fixture
def pipeline(stub_vault, stub_prompts, stub_memory, set_current_agent_spy, monkeypatch):
    """A pipeline with ask / kb_log / set_current_agent 都 stub 掉。"""
    monkeypatch.setattr(mod, "ask", lambda **k: "stubbed-ask")
    monkeypatch.setattr(mod, "kb_log", lambda *a, **k: None)
    return IngestPipeline()


# ---------------------------------------------------------------------------
# _truncate_at_boundary
# ---------------------------------------------------------------------------


def test_truncate_noop_under_limit():
    assert _truncate_at_boundary("short text", 100) == "short text"


def test_truncate_equal_to_limit():
    text = "x" * 50
    assert _truncate_at_boundary(text, 50) == text


def test_truncate_paragraph_boundary_preferred():
    """文字內有 \\n\\n 在 max_chars*0.5 之後 → 切在段落邊界。"""
    text = "A" * 60 + "\n\n" + "B" * 60
    result = _truncate_at_boundary(text, 80)
    assert result.endswith("[…內容過長，已截斷]")
    # 切點在 \n\n 前，不應包含 B
    assert "B" not in result.split("\n\n[…")[0]


def test_truncate_chinese_full_stop_fallback():
    """沒有 \\n\\n 但有「。」過 max_chars*0.5 → 切在句末。"""
    text = "一" * 30 + "。" + "X" * 200
    result = _truncate_at_boundary(text, 50)
    # rfind("。") in text[:50] == 30，30 > 25 → 採用
    assert "。" in result
    assert result.endswith("[…內容過長，已截斷]")


def test_truncate_english_sentence_boundary():
    """英文句末 ". " 過閾值 → 切在句末。"""
    text = "X" * 30 + ". " + "Y" * 200
    result = _truncate_at_boundary(text, 50)
    assert ". " in result
    assert result.endswith("[…內容過長，已截斷]")


def test_truncate_dot_newline_boundary():
    """'.\\n' sep 過閾值 → 切在此處。"""
    text = "X" * 30 + ".\n" + "Y" * 200
    result = _truncate_at_boundary(text, 50)
    assert ".\n" in result
    assert result.endswith("[…內容過長，已截斷]")


def test_truncate_single_newline_last_resort():
    """只有 '\\n' sep 過閾值 → fallback 到此處。"""
    text = "X" * 30 + "\n" + "Y" * 200
    result = _truncate_at_boundary(text, 50)
    assert result.endswith("[…內容過長，已截斷]")
    # '\n' sep cut position 30 > 25 → 採用
    assert len(result.split("[…")[0].rstrip()) <= 40


def test_truncate_hard_cut_when_no_boundary():
    """全是 X，沒有任何邊界 → 直接硬切。"""
    text = "X" * 500
    result = _truncate_at_boundary(text, 100)
    assert result == "X" * 100 + "\n\n[…內容過長，已截斷]"


def test_truncate_boundary_too_early_falls_through():
    """邊界在 max_chars*0.5 之前 → 不採用該邊界，fallback 到更後面。"""
    text = "AB\n\n" + "C" * 200
    # \n\n 在第 2 個 char（< 50*0.5），應該忽略它走 fallback
    result = _truncate_at_boundary(text, 50)
    # 因為沒有句號也沒合適 \n，最終硬切
    assert result.endswith("[…內容過長，已截斷]")
    assert len(result.split("[…")[0]) >= 40  # 接近 max_chars，不是在 AB\n\n 切


# ---------------------------------------------------------------------------
# _build_robin_system_prompt
# ---------------------------------------------------------------------------


def test_system_prompt_without_memory(monkeypatch):
    monkeypatch.setattr(mod, "get_context", lambda *a, **k: "")
    result = _build_robin_system_prompt()
    assert result == "你是 Robin，Nakama 團隊的考古學家，負責知識庫管理。"


def test_system_prompt_with_memory(monkeypatch):
    monkeypatch.setattr(mod, "get_context", lambda *a, **k: "記憶內容")
    result = _build_robin_system_prompt()
    assert "Robin" in result
    assert "記憶內容" in result


# ---------------------------------------------------------------------------
# set_current_agent("robin") is called at every public entry point
# (regression guard for commit b015775 — cost DB was recording "unknown"
# for Web UI calls until these were wired in)
# ---------------------------------------------------------------------------


def test_set_current_agent_called_on_all_entry_points(
    pipeline, stub_vault, set_current_agent_spy, monkeypatch
):
    """ingest() 跑完後，4 個 entry point（generate_summary / concept_plan /
    create_wiki_page / map_reduce — 後者只在大文件）各自呼叫 set_current_agent('robin')。
    """
    raw = stub_vault / "data.md"
    raw.write_text("---\ntitle: D\n---\nshort body", encoding="utf-8")

    plan_json = (
        '{"create": [{"title": "NewPage", "type": "concept", "content_notes": ""}], "update": []}'
    )
    call_idx = {"n": 0}

    def fake_ask(**k):
        call_idx["n"] += 1
        if call_idx["n"] == 2:
            return plan_json
        return "stub"

    monkeypatch.setattr(mod, "ask", fake_ask)
    monkeypatch.setattr(mod, "list_files", lambda p: [])
    pipeline.ingest(raw, source_type="article")

    # Must have been called at least for _generate_summary + _get_concept_plan
    # + _create_wiki_page — all with "robin"
    called_args = [c.args for c in set_current_agent_spy.call_args_list]
    assert all(a == ("robin",) for a in called_args), (
        f"set_current_agent 被呼叫時 arg 不是 'robin'：{called_args}"
    )
    assert set_current_agent_spy.call_count >= 3


def test_set_current_agent_called_in_map_reduce_path(pipeline, set_current_agent_spy, monkeypatch):
    """_map_reduce_summary 也要呼叫 set_current_agent('robin')。"""
    _install_fake_chunker(
        monkeypatch,
        [{"index": 1, "heading": "A", "text": "t"}],
    )
    fake_llm = types.ModuleType("shared.local_llm")
    fake_llm.ask_local = lambda *a, **k: "x"
    fake_llm.is_server_available = lambda: False
    monkeypatch.setitem(sys.modules, "shared.local_llm", fake_llm)
    monkeypatch.setattr(mod, "ask", lambda **k: "stub")

    set_current_agent_spy.reset_mock()
    pipeline._map_reduce_summary(
        content="big", title="T", author="A", source_type="book", content_nature=""
    )
    set_current_agent_spy.assert_any_call("robin")


# ---------------------------------------------------------------------------
# _get_map_ask_fn
# ---------------------------------------------------------------------------


def test_get_map_ask_fn_local_available(monkeypatch):
    fake = types.ModuleType("shared.local_llm")
    fake.ask_local = lambda *a, **k: "local"
    fake.is_server_available = lambda: True
    monkeypatch.setitem(sys.modules, "shared.local_llm", fake)
    fn = IngestPipeline._get_map_ask_fn()
    assert fn is fake.ask_local


def test_get_map_ask_fn_server_down_falls_back_to_facade(monkeypatch):
    fake = types.ModuleType("shared.local_llm")
    fake.ask_local = lambda *a, **k: "local"
    fake.is_server_available = lambda: False
    monkeypatch.setitem(sys.modules, "shared.local_llm", fake)
    fn = IngestPipeline._get_map_ask_fn()
    assert fn is mod.ask


def test_get_map_ask_fn_import_error_falls_back_to_facade(monkeypatch):
    """ImportError 時 fallback 到 facade。"""
    monkeypatch.delitem(sys.modules, "shared.local_llm", raising=False)
    real_import = builtins.__import__

    def blocked(name, globals_=None, locals_=None, fromlist=(), level=0):
        if name == "shared.local_llm":
            raise ImportError("simulated")
        return real_import(name, globals_, locals_, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", blocked)
    fn = IngestPipeline._get_map_ask_fn()
    assert fn is mod.ask


# ---------------------------------------------------------------------------
# _generate_summary  (small doc vs map-reduce branch)
# ---------------------------------------------------------------------------


def test_generate_summary_small_doc_uses_facade(pipeline, monkeypatch):
    """< LARGE_DOC_THRESHOLD → 單次 facade call，不走 map-reduce。"""
    captured = {}

    def fake_ask(**kwargs):
        captured["prompt"] = kwargs["prompt"]
        captured["system"] = kwargs["system"]
        return "小文件摘要"

    monkeypatch.setattr(mod, "ask", fake_ask)

    result = pipeline._generate_summary(
        content="short content",
        title="T",
        author="A",
        source_type="article",
        content_nature="popular_science",
    )
    assert result == "小文件摘要"
    assert "robin:summarize" in captured["prompt"]


def test_generate_summary_large_doc_triggers_map_reduce(pipeline, monkeypatch):
    """>= LARGE_DOC_THRESHOLD → 走 _map_reduce_summary。"""
    called = {"mr": False}

    def fake_map_reduce(**kwargs):
        called["mr"] = True
        return "map-reduce 結果"

    monkeypatch.setattr(pipeline, "_map_reduce_summary", fake_map_reduce)
    big = "X" * (IngestPipeline.LARGE_DOC_THRESHOLD + 100)
    out = pipeline._generate_summary(
        content=big, title="T", author="", source_type="book", content_nature=""
    )
    assert out == "map-reduce 結果"
    assert called["mr"] is True


# ---------------------------------------------------------------------------
# _map_reduce_summary
# ---------------------------------------------------------------------------


def _install_fake_chunker(monkeypatch, chunks):
    fake = types.ModuleType("agents.robin.chunker")
    fake.chunk_document = lambda content: chunks
    monkeypatch.setitem(sys.modules, "agents.robin.chunker", fake)
    return fake


def test_map_reduce_happy_path(pipeline, monkeypatch):
    """兩個 chunk 全部 map 成功 → reduce 合併回傳。"""
    _install_fake_chunker(
        monkeypatch,
        [
            {"index": 1, "heading": "A 章", "text": "aaa"},
            {"index": 2, "heading": "B 章", "text": "bbb"},
        ],
    )

    calls = []

    def fake_ask(prompt, system=None, **kwargs):
        calls.append(prompt)
        return f"summary-of:{prompt[-3:]}"

    # Local LLM disabled → map 階段走 facade
    fake_llm = types.ModuleType("shared.local_llm")
    fake_llm.ask_local = lambda *a, **k: "nope"
    fake_llm.is_server_available = lambda: False
    monkeypatch.setitem(sys.modules, "shared.local_llm", fake_llm)

    # ask() 雲端 facade：map 階段使用（2 次），reduce 階段使用（1 次）
    monkeypatch.setattr(mod, "ask", fake_ask)

    out = pipeline._map_reduce_summary(
        content="big content", title="T", author="A", source_type="book", content_nature=""
    )
    # 3 calls: 2 map + 1 reduce
    assert len(calls) == 3
    assert out.startswith("summary-of:")


def test_map_reduce_chunk_failure_does_not_abort(pipeline, monkeypatch):
    """其中一個 chunk raise → 該段落填 fallback，fallback 文字要送進 reduce prompt。"""
    _install_fake_chunker(
        monkeypatch,
        [
            {"index": 1, "heading": "好段", "text": "ok"},
            {"index": 2, "heading": "壞段", "text": "bad"},
        ],
    )

    prompts_seen: list[str] = []

    def flaky_ask(prompt, system=None, **kwargs):
        prompts_seen.append(prompt)
        # 第 2 次（壞段 map）raise；第 3 次 reduce → 回合成結果
        if len(prompts_seen) == 2:
            raise RuntimeError("provider down")
        return "ok-sum"

    # Capture load_prompt kwargs so we can inspect what reduce receives
    reduce_kwargs: dict = {}
    orig_load = mod.load_prompt

    def capturing_load(agent, name, **kwargs):
        if name == "reduce_summary":
            reduce_kwargs.update(kwargs)
        return orig_load(agent, name, **kwargs)

    monkeypatch.setattr(mod, "load_prompt", capturing_load)

    fake_llm = types.ModuleType("shared.local_llm")
    fake_llm.ask_local = lambda *a, **k: "x"
    fake_llm.is_server_available = lambda: False
    monkeypatch.setitem(sys.modules, "shared.local_llm", fake_llm)
    monkeypatch.setattr(mod, "ask", flaky_ask)

    out = pipeline._map_reduce_summary(
        content="big", title="T", author="A", source_type="book", content_nature=""
    )
    # reduce call 發生 → 有 output
    assert out == "ok-sum"
    assert len(prompts_seen) == 3  # 2 map (含一次失敗) + 1 reduce
    # fallback 文字要帶上壞段 heading，並送進 reduce prompt（regression guard，
    # 別讓 exception handler 悄悄吞成空字串 — 這是 commit 7884f79 的用意）
    assert "壞段" in reduce_kwargs["chunk_summaries"]
    assert "此段落摘要失敗" in reduce_kwargs["chunk_summaries"]


# ---------------------------------------------------------------------------
# _get_concept_plan
# ---------------------------------------------------------------------------


def test_concept_plan_parses_json(pipeline, monkeypatch):
    monkeypatch.setattr(mod, "list_files", lambda path: [])
    monkeypatch.setattr(
        mod,
        "ask",
        lambda **k: 'some preamble {"create": [{"title": "X"}], "update": []} trailing',
    )
    plan = pipeline._get_concept_plan("body", "KB/Wiki/Sources/x.md")
    assert plan == {"create": [{"title": "X"}], "update": []}


def test_concept_plan_no_json_returns_none(pipeline, monkeypatch):
    monkeypatch.setattr(mod, "list_files", lambda path: [])
    monkeypatch.setattr(mod, "ask", lambda **k: "no json at all")
    assert pipeline._get_concept_plan("body", "src.md") is None


def test_concept_plan_invalid_json_returns_none(pipeline, monkeypatch):
    monkeypatch.setattr(mod, "list_files", lambda path: [])
    monkeypatch.setattr(mod, "ask", lambda **k: "{not json}")
    assert pipeline._get_concept_plan("body", "src.md") is None


def test_concept_plan_existing_pages_listed(pipeline, monkeypatch):
    """既有 concept/entity 頁面應被注入到 prompt context。"""

    # 給 chunker / list_files 的模擬回傳
    def fake_list(path):
        if "Concepts" in path:
            return [Path("sleep.md"), Path("CBT-I.md")]
        return [Path("Carney.md")]

    monkeypatch.setattr(mod, "list_files", fake_list)
    captured = {}

    def fake_load(agent, name, **kwargs):
        captured.update(kwargs)
        return "<prompt>"

    monkeypatch.setattr(mod, "load_prompt", fake_load)
    monkeypatch.setattr(mod, "ask", lambda **k: '{"create":[],"update":[]}')
    pipeline._get_concept_plan("summary body", "src.md", user_guidance="guide")
    assert "sleep" in captured["existing_pages"]
    assert "Carney" in captured["existing_pages"]
    assert captured["user_guidance"] == "guide"


def test_concept_plan_empty_existing_pages_label(pipeline, monkeypatch):
    """無既有頁面 → prompt 用「（無）」。"""
    monkeypatch.setattr(mod, "list_files", lambda path: [])
    captured = {}
    monkeypatch.setattr(mod, "load_prompt", lambda agent, name, **k: captured.update(k) or "<p>")
    monkeypatch.setattr(mod, "ask", lambda **k: '{"create":[],"update":[]}')
    pipeline._get_concept_plan("body", "src.md")
    assert "（無）" in captured["existing_pages"]


def test_concept_plan_default_guidance_placeholder(pipeline, monkeypatch):
    """無 user_guidance → prompt 填預設語。"""
    monkeypatch.setattr(mod, "list_files", lambda path: [])
    captured = {}
    monkeypatch.setattr(mod, "load_prompt", lambda agent, name, **k: captured.update(k) or "<p>")
    monkeypatch.setattr(mod, "ask", lambda **k: '{"create":[],"update":[]}')
    pipeline._get_concept_plan("body", "src.md")
    assert "自行判斷" in captured["user_guidance"]


# ---------------------------------------------------------------------------
# _prompt_user_guidance  (interactive)
# ---------------------------------------------------------------------------


def test_prompt_user_guidance_returns_trimmed_input(pipeline, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *a, **k: "  focus on CBT-I  ")
    result = pipeline._prompt_user_guidance("TheTitle", "SummaryBody")
    assert result == "focus on CBT-I"
    out = capsys.readouterr().out
    assert "TheTitle" in out
    assert "已收到引導" in out


def test_prompt_user_guidance_empty_lets_robin_decide(pipeline, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *a, **k: "")
    assert pipeline._prompt_user_guidance("t", "body") == ""
    assert "自行判斷重點" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _review_plan_interactive
# ---------------------------------------------------------------------------


def test_review_plan_empty_returns_as_is(pipeline, capsys):
    plan = {"create": [], "update": []}
    out = pipeline._review_plan_interactive(plan)
    assert out == plan
    assert "不需要新增或更新" in capsys.readouterr().out


def test_review_plan_all_approves_everything(pipeline, monkeypatch):
    inputs = iter(["all", "all"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))
    plan = {
        "create": [
            {"title": "A", "type": "concept", "reason": "r1", "content_notes": "n1"},
            {"title": "B", "type": "entity", "reason": "r2", "content_notes": "n2"},
        ],
        "update": [{"title": "U", "reason": "ru", "additions": "au"}],
    }
    out = pipeline._review_plan_interactive(plan)
    assert len(out["create"]) == 2
    assert len(out["update"]) == 1


def test_review_plan_none_rejects_all(pipeline, monkeypatch):
    inputs = iter(["none", "none"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))
    plan = {
        "create": [{"title": "A", "reason": "", "content_notes": ""}],
        "update": [{"title": "U", "reason": "", "additions": ""}],
    }
    out = pipeline._review_plan_interactive(plan)
    assert out == {"create": [], "update": []}


def test_review_plan_empty_string_same_as_none(pipeline, monkeypatch):
    """直接 Enter 走「跳過」分支。"""
    inputs = iter(["", ""])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))
    plan = {
        "create": [{"title": "A", "reason": "", "content_notes": ""}],
        "update": [{"title": "U", "reason": "", "additions": ""}],
    }
    out = pipeline._review_plan_interactive(plan)
    assert out == {"create": [], "update": []}


def test_review_plan_indexed_selection(pipeline, monkeypatch):
    inputs = iter(["1,3", "2"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))
    plan = {
        "create": [{"title": f"C{i}", "reason": "", "content_notes": ""} for i in range(3)],
        "update": [{"title": f"U{i}", "reason": "", "additions": ""} for i in range(3)],
    }
    out = pipeline._review_plan_interactive(plan)
    assert [c["title"] for c in out["create"]] == ["C0", "C2"]
    assert [u["title"] for u in out["update"]] == ["U1"]


def test_review_plan_indexed_ignores_out_of_range(pipeline, monkeypatch):
    """超過範圍的 index 靜默忽略，不炸。"""
    inputs = iter(["1,99"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))
    plan = {
        "create": [{"title": "only", "reason": "", "content_notes": ""}],
        "update": [],
    }
    out = pipeline._review_plan_interactive(plan)
    assert len(out["create"]) == 1  # 99 被 drop


def test_review_plan_only_creates_no_updates(pipeline, monkeypatch):
    """plan 只有 create、沒有 update → update 分支不執行。"""
    monkeypatch.setattr("builtins.input", lambda *a, **k: "all")
    plan = {
        "create": [{"title": "A", "reason": "", "content_notes": ""}],
        "update": [],
    }
    out = pipeline._review_plan_interactive(plan)
    assert len(out["create"]) == 1
    assert out["update"] == []


def test_review_plan_only_updates_no_creates(pipeline, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a, **k: "all")
    plan = {
        "create": [],
        "update": [{"title": "U", "reason": "", "additions": ""}],
    }
    out = pipeline._review_plan_interactive(plan)
    assert out["create"] == []
    assert len(out["update"]) == 1


# ---------------------------------------------------------------------------
# _create_wiki_page  (concept vs entity)
# ---------------------------------------------------------------------------


def test_create_concept_page_writes_to_concepts_dir(pipeline, stub_vault):
    pipeline._create_wiki_page(
        {"title": "Sleep Pressure", "type": "concept", "content_notes": "notes"},
        "KB/Wiki/Sources/x.md",
    )
    expected = stub_vault / "KB" / "Wiki" / "Concepts" / "Sleep-Pressure.md"
    assert expected.exists()
    text = expected.read_text(encoding="utf-8")
    assert "type: concept" in text
    assert "Sleep Pressure" in text


def test_create_entity_page_writes_to_entities_dir(pipeline, stub_vault):
    pipeline._create_wiki_page(
        {
            "title": "Colleen Carney",
            "type": "entity",
            "entity_type": "person",
            "content_notes": "notes",
        },
        "KB/Wiki/Sources/x.md",
    )
    expected = stub_vault / "KB" / "Wiki" / "Entities" / "Colleen-Carney.md"
    assert expected.exists()
    text = expected.read_text(encoding="utf-8")
    assert "type: entity" in text


def test_create_page_default_type_is_concept(pipeline, stub_vault):
    """item 沒指定 type → default concept。"""
    pipeline._create_wiki_page({"title": "NoType", "content_notes": ""}, "src.md")
    assert (stub_vault / "KB" / "Wiki" / "Concepts" / "NoType.md").exists()


# ---------------------------------------------------------------------------
# _update_wiki_page
# ---------------------------------------------------------------------------


def _seed_page(vault: Path, relative: str, frontmatter: str, body: str) -> None:
    path = vault / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}\n---\n{body}\n", encoding="utf-8")


def test_update_page_with_explicit_file_path(pipeline, stub_vault):
    _seed_page(stub_vault, "KB/Wiki/Concepts/topic.md", "title: Topic", "old body")
    pipeline._update_wiki_page(
        {
            "title": "Topic",
            "file": "KB/Wiki/Concepts/topic.md",
            "additions": "新發現",
        },
        "KB/Wiki/Sources/new-src.md",
    )
    final = (stub_vault / "KB" / "Wiki" / "Concepts" / "topic.md").read_text("utf-8")
    assert "新發現" in final
    assert "[[new-src]]" in final


def test_update_page_locates_by_slug_in_concepts(pipeline, stub_vault):
    # slugify preserves case，seed path 要對齊 slugify(title) 輸出，不然 Linux CI 大小寫敏感會找不到
    _seed_page(stub_vault, "KB/Wiki/Concepts/Found.md", "title: Found", "body")
    pipeline._update_wiki_page(
        {"title": "Found", "additions": "a"},
        "KB/Wiki/Sources/src.md",
    )
    final = (stub_vault / "KB" / "Wiki" / "Concepts" / "Found.md").read_text("utf-8")
    assert "a" in final


def test_update_page_locates_by_slug_in_entities(pipeline, stub_vault):
    _seed_page(stub_vault, "KB/Wiki/Entities/Carney.md", "title: Carney", "bio")
    pipeline._update_wiki_page(
        {"title": "Carney", "additions": "new paper"},
        "KB/Wiki/Sources/src.md",
    )
    final = (stub_vault / "KB" / "Wiki" / "Entities" / "Carney.md").read_text("utf-8")
    assert "new paper" in final


def test_update_page_title_not_found_logs_warning(pipeline, caplog):
    """title 找不到對應 slug → logger.warning + 靜默 return。"""
    import logging

    caplog.set_level(logging.WARNING, logger="nakama.robin.ingest")
    pipeline._update_wiki_page(
        {"title": "Nonexistent", "additions": "x"},
        "KB/Wiki/Sources/src.md",
    )
    assert any("找不到要更新的頁面" in r.message for r in caplog.records)


def test_update_page_file_field_points_to_missing_file(pipeline, stub_vault):
    """file 指定了但檔不存在 → 警告 + return，不 raise。"""
    pipeline._update_wiki_page(
        {
            "title": "Ghost",
            "file": "KB/Wiki/Concepts/ghost.md",
            "additions": "x",
        },
        "KB/Wiki/Sources/src.md",
    )
    # 不應 raise；ghost.md 不會被建立
    assert not (stub_vault / "KB" / "Wiki" / "Concepts" / "ghost.md").exists()


def test_update_page_deduplicates_source_refs(pipeline, stub_vault):
    """同一 source 再次 ingest 不重複 append refs。"""
    _seed_page(
        stub_vault,
        "KB/Wiki/Concepts/topic.md",
        "title: Topic\nsource_refs:\n  - KB/Wiki/Sources/src.md",
        "body",
    )
    pipeline._update_wiki_page(
        {"title": "Topic", "file": "KB/Wiki/Concepts/topic.md", "additions": "x"},
        "KB/Wiki/Sources/src.md",
    )
    final = (stub_vault / "KB" / "Wiki" / "Concepts" / "topic.md").read_text("utf-8")
    # 應該只出現一次 src.md
    assert final.count("KB/Wiki/Sources/src.md") == 1


# ---------------------------------------------------------------------------
# _update_index
# ---------------------------------------------------------------------------


def test_update_index_creates_section_when_missing(pipeline, stub_vault):
    """index.md 已存在但沒 Sources section → append 新 section。

    （實務上 ingest flow 會在 _update_index 前先 write_page Sources/*.md，
    parent mkdir 會建 KB/ 目錄，所以 KB/index.md 的父目錄必存在。）
    """
    (stub_vault / "KB").mkdir(parents=True, exist_ok=True)
    (stub_vault / "KB" / "index.md").write_text("# Index\n", encoding="utf-8")
    pipeline._update_index("TheTitle", "the-title", "article")
    content = (stub_vault / "KB" / "index.md").read_text("utf-8")
    assert "[[the-title]]" in content
    assert "## Sources" in content


def test_update_index_skips_when_wikilink_already_present(pipeline, stub_vault):
    existing = "## Sources\n- [[the-title]] — existing\n"
    (stub_vault / "KB").mkdir(parents=True, exist_ok=True)
    (stub_vault / "KB" / "index.md").write_text(existing, encoding="utf-8")
    pipeline._update_index("TheTitle", "the-title", "article")
    content = (stub_vault / "KB" / "index.md").read_text("utf-8")
    # 沒新增一行（仍只有 1 個 [[the-title]]）
    assert content.count("[[the-title]]") == 1


def test_update_index_migrates_plain_path_to_wikilink(pipeline, stub_vault):
    """舊 plain path 寫法 → 被自動替換成 wikilink。"""
    existing = "## Sources\n- KB/Wiki/Sources/the-title.md — old format\n- [[other]] — x\n"
    (stub_vault / "KB").mkdir(parents=True, exist_ok=True)
    (stub_vault / "KB" / "index.md").write_text(existing, encoding="utf-8")
    pipeline._update_index("TheTitle", "the-title", "article")
    content = (stub_vault / "KB" / "index.md").read_text("utf-8")
    assert "[[the-title]]" in content
    assert "KB/Wiki/Sources/the-title.md" not in content


def test_update_index_english_sources_heading_inserts_below(pipeline, stub_vault):
    """既有 '## Sources' → re.sub 在 heading 下方插入新條目。"""
    existing = "## Sources\n- [[other]] — x\n"
    (stub_vault / "KB").mkdir(parents=True, exist_ok=True)
    (stub_vault / "KB" / "index.md").write_text(existing, encoding="utf-8")
    pipeline._update_index("TheTitle", "the-title", "article")
    content = (stub_vault / "KB" / "index.md").read_text("utf-8")
    assert "[[the-title]]" in content
    # 新條目應在 heading 後、[[other]] 前
    assert content.index("[[the-title]]") < content.index("[[other]]")


def test_update_index_chinese_heading_falls_through_to_append(pipeline, stub_vault):
    """中文 heading '## 來源（Sources）' 不含字面 '## Sources' → 走 append 分支。"""
    existing = "## 來源（Sources）\n- [[other]] — x\n"
    (stub_vault / "KB").mkdir(parents=True, exist_ok=True)
    (stub_vault / "KB" / "index.md").write_text(existing, encoding="utf-8")
    pipeline._update_index("TheTitle", "the-title", "article")
    content = (stub_vault / "KB" / "index.md").read_text("utf-8")
    assert "[[the-title]]" in content
    # 原中文 heading 保留（append 分支會另外新增 '## Sources' section）
    assert "## 來源（Sources）" in content


# ---------------------------------------------------------------------------
# _execute_plan
# ---------------------------------------------------------------------------


def test_execute_plan_dispatches_to_create_and_update(pipeline, monkeypatch):
    calls = {"create": 0, "update": 0}

    def fake_create(item, src):
        calls["create"] += 1

    def fake_update(item, src):
        calls["update"] += 1

    monkeypatch.setattr(pipeline, "_create_wiki_page", fake_create)
    monkeypatch.setattr(pipeline, "_update_wiki_page", fake_update)

    pipeline._execute_plan(
        {
            "create": [{"title": "A"}, {"title": "B"}],
            "update": [{"title": "X"}],
        },
        "src.md",
    )
    assert calls == {"create": 2, "update": 1}


def test_execute_plan_handles_missing_keys(pipeline, monkeypatch):
    """plan 沒 create / update key → 走預設空 list，不炸。"""
    monkeypatch.setattr(pipeline, "_create_wiki_page", lambda i, s: None)
    monkeypatch.setattr(pipeline, "_update_wiki_page", lambda i, s: None)
    pipeline._execute_plan({}, "src.md")  # no raise


# ---------------------------------------------------------------------------
# ingest (end-to-end orchestration)
# ---------------------------------------------------------------------------


def test_ingest_md_file_full_flow(pipeline, stub_vault, monkeypatch):
    """Markdown source → frontmatter 讀取 → 建 Source Summary → plan 執行 → index 更新。"""
    raw = stub_vault / "raw.md"
    raw.write_text(
        "---\ntitle: Real Title\nauthor: Real Author\n---\nbody content\n",
        encoding="utf-8",
    )

    # concept plan 回一個 create item
    plan_json = (
        '{"create": [{"title": "NewConcept", "type": "concept", '
        '"content_notes": "n"}], "update": []}'
    )
    call_idx = {"n": 0}

    def fake_ask(**kwargs):
        call_idx["n"] += 1
        # 1st: summary; 2nd: concept plan; 3rd: create_wiki_page body
        if call_idx["n"] == 2:
            return plan_json
        return "generic ask response"

    monkeypatch.setattr(mod, "ask", fake_ask)
    monkeypatch.setattr(mod, "list_files", lambda p: [])

    pipeline.ingest(raw, source_type="article")

    summary = stub_vault / "KB" / "Wiki" / "Sources" / "Real-Title.md"
    assert summary.exists()
    assert "title: Real Title" in summary.read_text("utf-8")
    assert "author: Real Author" in summary.read_text("utf-8")

    new_concept = stub_vault / "KB" / "Wiki" / "Concepts" / "NewConcept.md"
    assert new_concept.exists()

    index = (stub_vault / "KB" / "index.md").read_text("utf-8")
    assert "[[Real-Title]]" in index


def test_ingest_raw_path_outside_vault_uses_full_path(pipeline, stub_vault, monkeypatch, tmp_path):
    """raw_path 不在 vault 內 → source_refs 落成絕對路徑。"""
    outside = tmp_path / "outside.md"
    outside.write_text("no frontmatter body", encoding="utf-8")

    monkeypatch.setattr(mod, "ask", lambda **k: '{"create":[],"update":[]}')
    monkeypatch.setattr(mod, "list_files", lambda p: [])

    # 移到 vault 外 — stub_vault 是 tmp_path，outside 就寫在 vault 根（outside.md）
    # 改用另一個 tmp path 製造「不在 vault 內」情境
    real_outside = tmp_path.parent / "elsewhere.md"
    real_outside.write_text("x", encoding="utf-8")

    pipeline.ingest(real_outside, source_type="note")
    summary = stub_vault / "KB" / "Wiki" / "Sources" / "elsewhere.md"
    assert summary.exists()
    content = summary.read_text("utf-8")
    # source_refs 含完整路徑字串（非 relative）
    assert str(real_outside) in content


def test_ingest_plan_is_none_short_circuits(pipeline, stub_vault, monkeypatch):
    """_get_concept_plan 回 None → ingest 提前 return，不執行 plan/index/remember。"""
    raw = stub_vault / "data.md"
    raw.write_text("---\ntitle: Data\n---\nbody", encoding="utf-8")

    responses = iter(
        [
            "summary body",  # _generate_summary
            "no-json-response",  # _get_concept_plan → None
        ]
    )
    monkeypatch.setattr(mod, "ask", lambda **k: next(responses))
    monkeypatch.setattr(mod, "list_files", lambda p: [])

    remember_called = {"yes": False}
    monkeypatch.setattr(mod, "remember", lambda **k: remember_called.update(yes=True))

    pipeline.ingest(raw, source_type="note")

    # Summary 應該寫了
    assert (stub_vault / "KB" / "Wiki" / "Sources" / "Data.md").exists()
    # index 不應更新
    assert not (stub_vault / "KB" / "index.md").exists()
    # remember 不應被呼叫
    assert remember_called["yes"] is False


def test_ingest_pdf_research_nature_enables_table_extraction(pipeline, stub_vault, monkeypatch):
    """PDF + content_nature=research → parse_pdf(with_tables=True)。"""
    raw = stub_vault / "paper.pdf"
    raw.write_bytes(b"%PDF fake")

    captured = {}

    def fake_parse_pdf(path, with_tables=False):
        captured["with_tables"] = with_tables
        return "parsed pdf content"

    fake_mod = types.ModuleType("shared.pdf_parser")
    fake_mod.parse_pdf = fake_parse_pdf
    monkeypatch.setitem(sys.modules, "shared.pdf_parser", fake_mod)

    monkeypatch.setattr(mod, "ask", lambda **k: '{"create":[],"update":[]}')
    monkeypatch.setattr(mod, "list_files", lambda p: [])

    pipeline.ingest(raw, source_type="paper", content_nature="research")
    assert captured["with_tables"] is True


def test_ingest_pdf_popular_science_disables_table_extraction(pipeline, stub_vault, monkeypatch):
    """PDF + content_nature=popular_science → parse_pdf(with_tables=False)。"""
    raw = stub_vault / "pop.pdf"
    raw.write_bytes(b"%PDF fake")

    captured = {}

    def fake_parse_pdf(path, with_tables=False):
        captured["with_tables"] = with_tables
        return "content"

    fake_mod = types.ModuleType("shared.pdf_parser")
    fake_mod.parse_pdf = fake_parse_pdf
    monkeypatch.setitem(sys.modules, "shared.pdf_parser", fake_mod)

    monkeypatch.setattr(mod, "ask", lambda **k: '{"create":[],"update":[]}')
    monkeypatch.setattr(mod, "list_files", lambda p: [])

    pipeline.ingest(raw, source_type="book", content_nature="popular_science")
    assert captured["with_tables"] is False


def test_ingest_interactive_mode_consults_user_twice(pipeline, stub_vault, monkeypatch):
    """interactive=True → _prompt_user_guidance + _review_plan_interactive 都被呼叫。"""
    raw = stub_vault / "i.md"
    raw.write_text("---\ntitle: I\n---\nbody", encoding="utf-8")

    guidance_calls = {"n": 0}
    review_calls = {"n": 0}

    def fake_guide(title, body):
        guidance_calls["n"] += 1
        return "user-guide"

    def fake_review(plan):
        review_calls["n"] += 1
        return plan

    monkeypatch.setattr(pipeline, "_prompt_user_guidance", fake_guide)
    monkeypatch.setattr(pipeline, "_review_plan_interactive", fake_review)

    monkeypatch.setattr(mod, "ask", lambda **k: '{"create":[],"update":[]}')
    monkeypatch.setattr(mod, "list_files", lambda p: [])

    pipeline.ingest(raw, source_type="note", interactive=True)

    assert guidance_calls["n"] == 1
    assert review_calls["n"] == 1


def test_ingest_remember_records_created_and_updated_titles(pipeline, stub_vault, monkeypatch):
    """ingest 尾端 remember() 應收到 plan 內的 create + update titles。"""
    raw = stub_vault / "r.md"
    raw.write_text("---\ntitle: R\n---\nbody", encoding="utf-8")

    plan_json = (
        '{"create": [{"title": "NewPage", "type": "concept", "content_notes": ""}], '
        '"update": [{"title": "OldPage", "file": "KB/Wiki/Concepts/old.md", '
        '"additions": "x"}]}'
    )
    _seed_page(stub_vault, "KB/Wiki/Concepts/old.md", "title: OldPage", "body")

    ask_idx = {"n": 0}

    def fake_ask(**k):
        ask_idx["n"] += 1
        if ask_idx["n"] == 2:
            return plan_json
        return "stub"

    monkeypatch.setattr(mod, "ask", fake_ask)
    monkeypatch.setattr(mod, "list_files", lambda p: [])

    captured = {}
    monkeypatch.setattr(mod, "remember", lambda **k: captured.update(k))

    pipeline.ingest(raw, source_type="note", user_guidance="hint")

    assert "NewPage" in captured["content"]
    assert "OldPage" in captured["content"]
    assert "hint" in captured["content"]
    assert "ingest" in captured["tags"]


def test_ingest_md_without_frontmatter_uses_stem_as_title(pipeline, stub_vault, monkeypatch):
    """MD 檔沒有 frontmatter → title 取 stem，author 空。"""
    raw = stub_vault / "no-fm.md"
    raw.write_text("plain text only\n", encoding="utf-8")

    monkeypatch.setattr(mod, "ask", lambda **k: '{"create":[],"update":[]}')
    monkeypatch.setattr(mod, "list_files", lambda p: [])

    pipeline.ingest(raw, source_type="note")
    summary = stub_vault / "KB" / "Wiki" / "Sources" / "no-fm.md"
    assert summary.exists()
    text = summary.read_text("utf-8")
    assert "title: no-fm" in text
