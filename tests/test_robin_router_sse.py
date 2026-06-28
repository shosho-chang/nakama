"""SSE coverage for thousand_sunny.routers.robin `/events/{session_id}`.

自動化 ingest（ADR-043 Stage-3 脊椎）：一條 SSE 連線跑完 summarizing → planning →
executing → 開卡建議，中途無 HITL gate。本檔覆蓋整條自動流程、各階段 resume、phase
進度事件、ingest 完成提案的兩種落點（有候選→/kb/review；無候選→adhoc 開卡），以及
cancelled / unknown / exception path。

依 feedback_pytest_monkeypatch_where_used — patch `robin_module` 內讀名字的
namespace，不是原始定義處。
"""

from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures（與 test_robin_router.py 同模式）
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    import shared.config as cfg

    importlib.reload(cfg)
    return tmp_path


@pytest.fixture
def client(vault, monkeypatch):
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)

    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.robin as robin_module

    importlib.reload(auth_module)
    importlib.reload(robin_module)

    app = FastAPI()
    app.include_router(robin_module.router)
    app.include_router(robin_module.robin_router)
    app.include_router(robin_module.legacy_router)

    @app.get("/login")
    def login(next: str = ""):
        return PlainTextResponse(f"login next={next}")

    return TestClient(app, follow_redirects=False), robin_module


@pytest.fixture
def auth_client(vault, monkeypatch):
    monkeypatch.setenv("WEB_PASSWORD", "testpw")
    monkeypatch.setenv("WEB_SECRET", "testsecret")

    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.robin as robin_module

    importlib.reload(auth_module)
    importlib.reload(robin_module)

    app = FastAPI()
    app.include_router(robin_module.router)
    app.include_router(robin_module.robin_router)
    app.include_router(robin_module.legacy_router)

    @app.get("/login")
    def login(next: str = ""):
        return PlainTextResponse(f"login next={next}")

    tc = TestClient(app, follow_redirects=False)

    from thousand_sunny.auth import make_token

    cookies = {"nakama_auth": make_token("testpw")}
    return tc, robin_module, cookies


def _parse_sse(text: str) -> list[dict]:
    """Split SSE stream into [{event, data}, ...]."""
    events = []
    for chunk in text.strip().split("\n\n"):
        if not chunk.strip():
            continue
        ev = {}
        for line in chunk.split("\n"):
            if line.startswith("event: "):
                ev["event"] = line[7:]
            elif line.startswith("data: "):
                raw = line[6:]
                try:
                    ev["data"] = json.loads(raw)
                except json.JSONDecodeError:
                    ev["data"] = raw
        if ev:
            events.append(ev)
    return events


def _mock_full_pipeline(monkeypatch, mod, *, summary="fake summary", plan=None, proposed=False):
    """Mock 自動流程用到的每一個 seam，讓一條連線可從任一階段跑到底，不碰真 LLM / IO.

    - ``_generate_summary`` / ``write_page``：summarizing 階段
    - ``_get_concept_plan`` / ``_execute_plan`` / ``_update_index``：planning + executing
    - ``mark_file_processed`` / ``_send_to_recycle_bin``：執行後原檔清理
    - ``_propose_source_cards``（async）：ingest 完成提案，回傳是否有候選

    write_page 是 generator 內 ``from shared.obsidian_writer import write_page``，
    要 patch 原始 module。回傳 mock dict 讓個別測試做 assert。
    """
    gen = MagicMock(return_value=summary)
    get_plan = MagicMock(
        return_value=plan if plan is not None else {"concepts": [], "entities": []}
    )
    execute = MagicMock()
    update_index = MagicMock()
    mark_processed = MagicMock()
    recycle = MagicMock()
    monkeypatch.setattr(mod.pipeline, "_generate_summary", gen)
    monkeypatch.setattr(mod.pipeline, "_get_concept_plan", get_plan)
    monkeypatch.setattr(mod.pipeline, "_execute_plan", execute)
    monkeypatch.setattr(mod.pipeline, "_update_index", update_index)
    monkeypatch.setattr(mod, "mark_file_processed", mark_processed)
    monkeypatch.setattr(mod, "_send_to_recycle_bin", recycle)

    import shared.obsidian_writer as ow

    write_page = MagicMock()
    monkeypatch.setattr(ow, "write_page", write_page)

    async def _fake_propose(slug):  # noqa: ANN001
        _fake_propose.calls.append(slug)
        return proposed

    _fake_propose.calls = []
    monkeypatch.setattr(mod, "_propose_source_cards", _fake_propose)

    # summarizing 階段現在會算預估時長（estimate_ingest_seconds → is_server_available
    # 同步網路探測）。stub 成「本地不可用」讓所有流程測試 hermetic + 不打網路。
    import shared.local_llm as _llm

    monkeypatch.setattr(_llm, "is_server_available", lambda *a, **k: False)

    return {
        "generate_summary": gen,
        "get_concept_plan": get_plan,
        "execute_plan": execute,
        "update_index": update_index,
        "mark_processed": mark_processed,
        "recycle": recycle,
        "write_page": write_page,
        "propose": _fake_propose,
    }


def _article_session(mod, vault: Path, *, name="fake.md", body="# Title\n\nbody content"):
    raw = vault / "Inbox" / "web" / name
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(body, encoding="utf-8")
    return mod._new_session(
        step="summarizing",
        file_name=name,
        raw_path=str(raw),
        file_path=str(raw),
        source_type="article",
        content_nature="popular_science",
    )


# ---------------------------------------------------------------------------
# Auth + session existence
# ---------------------------------------------------------------------------


def test_events_unauth_returns_403(auth_client):
    tc, mod, _ = auth_client
    sid = mod._new_session(step="cancelled")
    r = tc.get(f"/robin/events/{sid}")  # 沒帶 cookie
    assert r.status_code == 403


def test_events_unknown_session_returns_404(client):
    tc, _ = client
    r = tc.get("/robin/events/nonexistent-sid")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Step: cancelled
# ---------------------------------------------------------------------------


def test_events_step_cancelled_redirects_home(client):
    tc, mod = client
    sid = mod._new_session(step="cancelled")
    r = tc.get(f"/robin/events/{sid}")
    assert r.status_code == 200
    events = _parse_sse(r.text)
    assert events == [{"event": "done", "data": {"redirect": "/robin"}}]


# ---------------------------------------------------------------------------
# 整條自動流程：summarizing → planning → executing → 開卡建議（一條連線跑完）
# ---------------------------------------------------------------------------


def test_events_full_autonomous_flow_no_candidates_opens_adhoc(client, vault, monkeypatch):
    """文章無劃線（提案回 False）→ 一條連線跑完四階段 → 落點 adhoc 開卡。"""
    tc, mod = client
    sid = _article_session(mod, vault)
    mocks = _mock_full_pipeline(monkeypatch, mod, proposed=False)

    r = tc.get(f"/robin/events/{sid}")
    assert r.status_code == 200
    events = _parse_sse(r.text)

    # 四個 phase 事件依序出現（進度條）
    phases = [e["data"]["step"] for e in events if e["event"] == "phase"]
    assert phases == [1, 2, 3, 4]

    # 無候選 → /kb/review?open=adhoc&slug=<source slug>
    assert events[-1]["event"] == "done"
    assert events[-1]["data"]["redirect"].startswith("/kb/review?open=adhoc&slug=")

    # 每個 pipeline seam 都被走過一次
    mocks["generate_summary"].assert_called_once()
    mocks["get_concept_plan"].assert_called_once()
    mocks["execute_plan"].assert_called_once()
    mocks["update_index"].assert_called_once()
    # 文章來源檔被回收（file_path 非空）
    mocks["mark_processed"].assert_called_once()
    mocks["recycle"].assert_called_once()

    sess = mod.sessions[sid]
    assert sess["step"] == "done"
    assert sess["summary_path"].startswith("KB/Wiki/Sources/")


def test_events_full_autonomous_flow_with_candidates_lands_on_review(client, vault, monkeypatch):
    """有劃線（提案回 True）→ 直接落 /kb/review 看開卡建議，不開 adhoc。"""
    tc, mod = client
    sid = _article_session(mod, vault)
    mocks = _mock_full_pipeline(monkeypatch, mod, proposed=True)

    r = tc.get(f"/robin/events/{sid}")
    assert r.status_code == 200
    events = _parse_sse(r.text)
    assert events[-1] == {"event": "done", "data": {"redirect": "/kb/review"}}
    # 提案被呼叫，且帶 annotation_slug（文章 session 無 → 空字串也算呼叫過）
    assert mocks["propose"].calls == [""]


def test_events_propose_receives_annotation_slug(client, vault, monkeypatch):
    """影片 session 帶 annotation_slug → 提案以該 slug 觸發（書/影片共用此路徑）。"""
    tc, mod = client
    raw = vault / "KB" / "Raw" / "Videos" / "vid123.md"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text("---\ntitle: V\n---\nbody", encoding="utf-8")
    sid = mod._new_session(
        step="summarizing",
        file_name="vid123.vtt",
        raw_path=str(raw),
        file_path="",
        keep_raw=True,
        source_type="video",
        annotation_slug="youtube_vid123",
    )
    mocks = _mock_full_pipeline(monkeypatch, mod, proposed=True)

    r = tc.get(f"/robin/events/{sid}")
    assert r.status_code == 200
    assert mocks["propose"].calls == ["youtube_vid123"]
    # keep_raw / 無 Inbox 來源檔 → 不回收原檔
    mocks["recycle"].assert_not_called()
    mocks["mark_processed"].assert_not_called()


# ---------------------------------------------------------------------------
# Summarizing 階段細節（frontmatter / provenance / 大文件）— 跑全流程但 assert 摘要側
# ---------------------------------------------------------------------------


def test_events_summarizing_md_with_frontmatter(client, vault, monkeypatch):
    """有 frontmatter 的 md 檔，title/author 從 fm 取。"""
    tc, mod = client
    sid = _article_session(
        mod, vault, name="with-fm.md", body="---\ntitle: Custom Title\nauthor: Jane Doe\n---\nbody"
    )
    _mock_full_pipeline(monkeypatch, mod)

    r = tc.get(f"/robin/events/{sid}")
    assert r.status_code == 200
    sess = mod.sessions[sid]
    assert sess["_title"] == "Custom Title"
    assert sess["_author"] == "Jane Doe"


def test_events_summarizing_source_page_author_is_agent_robin(client, vault, monkeypatch):
    """Source digest 是 AI 綜整摘要 → frontmatter author 必須是 agent_robin，
    原文作者另記 original_author（Centaur 規格 §7 紅線 3，provenance 分離）。"""
    tc, mod = client
    sid = _article_session(
        mod, vault, name="prov.md", body="---\ntitle: Provenance Title\nauthor: Jane Doe\n---\nbody"
    )
    mocks = _mock_full_pipeline(monkeypatch, mod)

    r = tc.get(f"/robin/events/{sid}")
    assert r.status_code == 200
    fm = mocks["write_page"].call_args[0][1]  # write_page 第二位置參數是 fm dict
    assert fm["author"] == "agent_robin"
    assert fm["original_author"] == "Jane Doe"


def test_events_summarizing_raw_path_outside_vault_fallback(client, vault, tmp_path, monkeypatch):
    """raw_path 不在 vault 下 → ValueError fallback 用 absolute str。"""
    tc, mod = client
    outside_dir = tmp_path.parent / f"outside-{tmp_path.name}"
    outside_dir.mkdir(exist_ok=True)
    outside = outside_dir / "outside.md"
    outside.write_text("body", encoding="utf-8")
    sid = mod._new_session(
        step="summarizing",
        file_name="outside.md",
        raw_path=str(outside),
        file_path=str(outside),
        source_type="article",
    )
    mocks = _mock_full_pipeline(monkeypatch, mod)

    r = tc.get(f"/robin/events/{sid}")
    assert r.status_code == 200
    fm = mocks["write_page"].call_args[0][1]
    assert fm["source_refs"] == [str(outside)]


def test_events_summarizing_large_doc_announces_chunking(client, vault, monkeypatch):
    """超過 LARGE_DOC_THRESHOLD → 應提示 Map-Reduce 分段。"""
    tc, mod = client
    big_text = "x" * (mod.pipeline.LARGE_DOC_THRESHOLD + 100)
    sid = _article_session(mod, vault, name="big.md", body=big_text)
    _mock_full_pipeline(monkeypatch, mod)

    from agents.robin import chunker

    monkeypatch.setattr(chunker, "chunk_document", MagicMock(return_value=["c1", "c2", "c3"]))

    r = tc.get(f"/robin/events/{sid}")
    assert r.status_code == 200
    events = _parse_sse(r.text)
    status_msgs = [e["data"].get("msg", "") for e in events if e["event"] == "status"]
    assert any("Map-Reduce" in m for m in status_msgs)
    assert any("分 3 段" in m for m in status_msgs)


def test_events_summarizing_emits_heartbeat_when_slow(client, vault, monkeypatch):
    """摘要跑超過 heartbeat 間隔 → 期間送 status heartbeat 保活（防反向代理 idle 切線）。

    回歸守門:13 萬字的書 map-reduce ~5 分鐘,SSE 靜默 >~100s 會被 Cloudflare/nginx
    切線、前端誤判 fatal。摘要丟背景 task + 每 _SSE_HEARTBEAT_SECONDS 送 heartbeat。
    """
    import time

    tc, mod = client
    sid = _article_session(mod, vault)
    _mock_full_pipeline(monkeypatch, mod)
    monkeypatch.setattr(mod, "_SSE_HEARTBEAT_SECONDS", 0.05)

    def _slow_summary(**kw):  # 在 to_thread 裡睡比 heartbeat 間隔久
        time.sleep(0.3)
        return "fake summary"

    monkeypatch.setattr(mod.pipeline, "_generate_summary", _slow_summary)

    r = tc.get(f"/robin/events/{sid}")
    assert r.status_code == 200
    events = _parse_sse(r.text)
    status_msgs = [e["data"].get("msg", "") for e in events if e["event"] == "status"]
    assert any("仍在閱讀" in m for m in status_msgs)  # heartbeat 有送出
    assert events[-1]["event"] == "done"  # 且流程照常跑完


# ---------------------------------------------------------------------------
# Resume：連線中斷後從各階段續跑（step 持久化於 session）
# ---------------------------------------------------------------------------


def test_events_resume_at_planning(client, vault, monkeypatch):
    """reconnect 在 planning → 續跑 planning→executing→done（phase 2,3,4）。"""
    tc, mod = client
    sid = mod._new_session(
        step="planning",
        file_name="x.md",
        raw_path=str(vault / "x.md"),
        file_path="",
        source_type="article",
        summary_body="summary",
        summary_path="KB/Wiki/Sources/x.md",
        _title="X",
    )
    mocks = _mock_full_pipeline(monkeypatch, mod, plan={"concepts": [], "entities": []})

    r = tc.get(f"/robin/events/{sid}")
    assert r.status_code == 200
    events = _parse_sse(r.text)
    phases = [e["data"]["step"] for e in events if e["event"] == "phase"]
    assert phases == [2, 3, 4]
    mocks["generate_summary"].assert_not_called()  # 摘要不重跑
    mocks["get_concept_plan"].assert_called_once()
    assert events[-1]["event"] == "done"


def test_events_resume_at_executing(client, vault, monkeypatch):
    """reconnect 在 executing → 寫入 + 提案 + done（phase 3,4）。"""
    tc, mod = client
    sid = mod._new_session(
        step="executing",
        file_name="y.md",
        raw_path=str(vault / "y.md"),
        file_path="",
        source_type="article",
        summary_path="KB/Wiki/Sources/y.md",
        plan={
            "concepts": [{"slug": "A", "action": "create", "title": "A"}],
            "entities": [{"title": "C", "entity_type": "person"}],
        },
        _title="Y",
    )
    mocks = _mock_full_pipeline(monkeypatch, mod, proposed=False)

    r = tc.get(f"/robin/events/{sid}")
    assert r.status_code == 200
    events = _parse_sse(r.text)
    phases = [e["data"]["step"] for e in events if e["event"] == "phase"]
    assert phases == [3, 4]
    mocks["execute_plan"].assert_called_once()
    mocks["update_index"].assert_called_once()
    # 寫入訊息提示「2 個 Wiki 頁面」(1 concept create + 1 entity)
    status_msgs = [e["data"].get("msg", "") for e in events if e["event"] == "status"]
    assert any("2 個" in m for m in status_msgs)


def test_events_resume_at_done_redirects_to_final(client):
    """reconnect 在 done → 直接吐已算好的 final_redirect。"""
    tc, mod = client
    sid = mod._new_session(step="done", final_redirect="/kb/review?open=adhoc&slug=foo")
    r = tc.get(f"/robin/events/{sid}")
    assert r.status_code == 200
    events = _parse_sse(r.text)
    assert events == [{"event": "done", "data": {"redirect": "/kb/review?open=adhoc&slug=foo"}}]


def test_events_resume_at_done_defaults_to_review(client):
    """done 但無 final_redirect（舊 session）→ 退回 /kb/review。"""
    tc, mod = client
    sid = mod._new_session(step="done")
    r = tc.get(f"/robin/events/{sid}")
    events = _parse_sse(r.text)
    assert events == [{"event": "done", "data": {"redirect": "/kb/review"}}]


def test_events_planning_none_plan_falls_back_to_empty(client, vault, monkeypatch):
    """_get_concept_plan 回 None → fallback 到空 plan，不 crash，仍跑到 done。"""
    tc, mod = client
    sid = mod._new_session(
        step="planning",
        file_name="x.md",
        raw_path=str(vault / "x.md"),
        file_path="",
        source_type="article",
        summary_body="x",
        summary_path="x.md",
        _title="X",
    )
    _mock_full_pipeline(monkeypatch, mod)
    monkeypatch.setattr(mod.pipeline, "_get_concept_plan", MagicMock(return_value=None))

    r = tc.get(f"/robin/events/{sid}")
    assert r.status_code == 200
    assert mod.sessions[sid]["plan"] == {"concepts": [], "entities": []}
    assert mod.sessions[sid]["step"] == "done"


# ---------------------------------------------------------------------------
# Unknown step + exception
# ---------------------------------------------------------------------------


def test_events_unknown_step_yields_error_event(client):
    tc, mod = client
    sid = mod._new_session(step="bogus_step")
    r = tc.get(f"/robin/events/{sid}")
    assert r.status_code == 200
    events = _parse_sse(r.text)
    assert len(events) == 1
    assert events[0]["event"] == "error"
    assert "未知狀態" in events[0]["data"]["msg"]
    assert "bogus_step" in events[0]["data"]["msg"]


def test_events_exception_during_processing_yields_error_and_marks_session(
    client, vault, monkeypatch
):
    """summarizing 時 _generate_summary 拋例外 → SSE yield error event + sess.step="error"。"""
    tc, mod = client
    sid = _article_session(mod, vault, name="boom.md", body="body")

    def raises(*a, **kw):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(mod.pipeline, "_generate_summary", raises)

    r = tc.get(f"/robin/events/{sid}")
    assert r.status_code == 200
    events = _parse_sse(r.text)
    error_events = [e for e in events if e["event"] == "error"]
    assert error_events
    assert "LLM down" in error_events[-1]["data"]["msg"]

    sess = mod.sessions[sid]
    assert sess["step"] == "error"
    assert sess["error"] == "LLM down"


# ---------------------------------------------------------------------------
# _propose_source_cards — ingest 完成「當下」從來源劃線提案永久卡（async helper）
# ---------------------------------------------------------------------------


def test_propose_source_cards_empty_slug_returns_false(client):
    """無 annotation_slug（如文章從 Inbox 丟進來）→ 直接 False，不碰收件匣。"""
    _, mod = client
    assert asyncio.run(mod._propose_source_cards("")) is False


def test_propose_source_cards_no_annotation_file_returns_false(client, vault):
    """來源沒在 Reader 讀過 → 無 KB/Annotations 檔 → collect 回空 → False。"""
    _, mod = client
    assert asyncio.run(mod._propose_source_cards("never-read-source")) is False


def test_propose_source_cards_upserts_candidates_and_returns_true(client, vault, monkeypatch):
    """有劃線 → build_candidates 出候選 → 寫進收件匣 → True。"""
    _, mod = client
    import agents.robin.daily_review as dr
    from shared import candidate_inbox
    from shared.schemas.daily_review import CandidateCard, SourceRef

    monkeypatch.setattr(
        dr,
        "collect_source_items",
        lambda v, *, slug: [
            {
                "slug": slug,
                "anchor": "a1",
                "quote": "睡眠鞏固記憶",
                "note": "重點",
                "type": "annotation",
                "literature_path": f"KB/Literature/{slug}",
            }
        ],
    )
    monkeypatch.setattr(dr, "_read_index_text", lambda v: "idx")
    card = CandidateCard(
        candidate_id="cid-x",
        suggested_title="睡眠如何鞏固記憶",
        why="強訊號",
        source_refs=[
            SourceRef(anchor="a1", literature_path="KB/Literature/vid1", quote="q", note="n")
        ],
        edges=[],
        priority=0,
        strong_signal=True,
    )
    monkeypatch.setattr(dr, "build_candidates", lambda items, idx: ([card], []))

    ok = asyncio.run(mod._propose_source_cards("vid1"))
    assert ok is True
    assert "cid-x" in {c.candidate_id for c in candidate_inbox.list_open()}


def test_propose_source_cards_no_items_skips_build(client, vault, monkeypatch):
    """collect 回空清單 → 不呼叫 P-1（build_candidates）→ False。"""
    _, mod = client
    import agents.robin.daily_review as dr

    called = {"build": False}
    monkeypatch.setattr(dr, "collect_source_items", lambda v, *, slug: [])

    def _build(*a, **k):
        called["build"] = True
        return [], []

    monkeypatch.setattr(dr, "build_candidates", _build)
    ok = asyncio.run(mod._propose_source_cards("vid1"))
    assert ok is False
    assert called["build"] is False


def test_propose_source_cards_swallows_exceptions(client, monkeypatch):
    """提案 best-effort：底層拋例外也回 False，不沉已完成的 ingest。"""
    _, mod = client
    import agents.robin.daily_review as dr

    def _boom(*a, **k):
        raise RuntimeError("collect blew up")

    monkeypatch.setattr(dr, "collect_source_items", _boom)
    assert asyncio.run(mod._propose_source_cards("vid1")) is False


# ---------------------------------------------------------------------------
# Progress UX：estimate 事件（進度頁定速）+ progress 事件（map-reduce 真實段數）
# ---------------------------------------------------------------------------


def test_events_emits_estimate_event(client, vault, monkeypatch):
    """summarizing 開工即送一個 estimate 事件（low/high/label）給進度頁。"""
    tc, mod = client
    sid = _article_session(mod, vault)
    _mock_full_pipeline(monkeypatch, mod)

    r = tc.get(f"/robin/events/{sid}")
    assert r.status_code == 200
    events = _parse_sse(r.text)
    est = [e for e in events if e["event"] == "estimate"]
    assert len(est) == 1
    assert "label" in est[0]["data"]
    assert est[0]["data"]["high"] >= est[0]["data"]["low"]


def test_events_emits_progress_events_during_map_reduce(client, vault, monkeypatch):
    """大文件摘要：progress_cb → 逐段 progress 事件（step/done/total）餵進度條。"""
    tc, mod = client
    big = "x" * (mod.pipeline.LARGE_DOC_THRESHOLD + 100)
    sid = _article_session(mod, vault, name="big.md", body=big)
    _mock_full_pipeline(monkeypatch, mod)

    from agents.robin import chunker

    monkeypatch.setattr(chunker, "chunk_document", MagicMock(return_value=["c1", "c2", "c3"]))

    def _summary_with_progress(**kw):
        cb = kw.get("progress_cb")
        if cb:
            for i in (1, 2, 3):
                cb(i, 3, f"第{i}章")
        return "fake summary"

    monkeypatch.setattr(mod.pipeline, "_generate_summary", _summary_with_progress)

    r = tc.get(f"/robin/events/{sid}")
    assert r.status_code == 200
    events = _parse_sse(r.text)
    prog = [e["data"] for e in events if e["event"] == "progress"]
    assert {(p["done"], p["total"]) for p in prog} == {(1, 3), (2, 3), (3, 3)}
    assert all(p["step"] == 1 for p in prog)
    # 工作記錄：每段帶章名的敘事行
    status_msgs = [e["data"].get("msg", "") for e in events if e["event"] == "status"]
    assert any("讀完第 1/3 段：第1章" in m for m in status_msgs)
    assert events[-1]["event"] == "done"  # 流程照常跑完


def test_events_narrates_concepts_and_per_page_writes(client, vault, monkeypatch):
    """工作記錄（L1）：planning 播報抽到的候選、executing 逐頁播報寫入——讓使用者看到
    Robin「想到什麼、正在寫什麼」，像真的有 agent 在做事。"""
    tc, mod = client
    sid = _article_session(mod, vault)
    plan = {
        "concepts": [{"slug": "lev", "action": "create", "title": "槓桿"}],
        "entities": [{"title": "Nick Maggiulli", "entity_type": "person"}],
    }
    _mock_full_pipeline(monkeypatch, mod, plan=plan, proposed=False)

    r = tc.get(f"/robin/events/{sid}")
    assert r.status_code == 200
    events = _parse_sse(r.text)
    blob = "\n".join(e["data"].get("msg", "") for e in events if e["event"] == "status")
    assert "抽出候選" in blob and "槓桿" in blob  # planning 敘事
    assert "寫入概念：槓桿" in blob  # executing 逐頁
    assert "寫入實體：Nick Maggiulli" in blob
