"""SSE coverage for thousand_sunny.routers.robin `/events/{session_id}`.

PR #127 把 robin router 拉到 77%，剩 SSE 區塊（lines 496-647）未測。
本檔補完所有 step state（cancelled / summarizing / planning / executing /
awaiting_* / done / unknown）+ exception path。

依 feedback_pytest_monkeypatch_where_used — patch `robin_module` 內讀名字的
namespace，不是原始定義處。
"""

from __future__ import annotations

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


# ---------------------------------------------------------------------------
# Auth + session existence
# ---------------------------------------------------------------------------


def test_events_unauth_returns_403(auth_client):
    tc, mod, _ = auth_client
    sid = mod._new_session(step="cancelled")
    r = tc.get(f"/events/{sid}")  # 沒帶 cookie
    assert r.status_code == 403


def test_events_unknown_session_returns_404(client):
    tc, _ = client
    r = tc.get("/events/nonexistent-sid")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Step: cancelled
# ---------------------------------------------------------------------------


def test_events_step_cancelled_redirects_home(client):
    tc, mod = client
    sid = mod._new_session(step="cancelled")
    r = tc.get(f"/events/{sid}")
    assert r.status_code == 200
    events = _parse_sse(r.text)
    assert events == [{"event": "done", "data": {"redirect": "/"}}]


# ---------------------------------------------------------------------------
# Step: summarizing — md / pdf / large doc / frontmatter
# ---------------------------------------------------------------------------


def _mock_summarizing_io(monkeypatch, mod, *, summary_text: str = "fake summary"):
    """Mock 所有 summarizing step 用到的 I/O 與 LLM call.

    write_page 是 SSE generator 內 `from shared.obsidian_writer import write_page`，
    所以要 patch 原始 module，不是 robin namespace。
    """
    monkeypatch.setattr(
        mod.pipeline,
        "_generate_summary",
        MagicMock(return_value=summary_text),
    )
    import shared.obsidian_writer as ow

    monkeypatch.setattr(ow, "write_page", MagicMock())


def test_events_step_summarizing_md_happy_path(client, vault, monkeypatch):
    tc, mod = client
    raw = vault / "Inbox" / "kb" / "fake.md"
    raw.parent.mkdir(parents=True)
    raw.write_text("# Title\n\nbody content")
    sid = mod._new_session(
        step="summarizing",
        raw_path=str(raw),
        file_path=str(raw),
        source_type="article",
        content_nature="popular_science",
    )
    _mock_summarizing_io(monkeypatch, mod)

    r = tc.get(f"/events/{sid}")
    assert r.status_code == 200
    events = _parse_sse(r.text)
    # 應有 status events + 最後 done redirect 到 review-summary
    assert events[-1] == {"event": "done", "data": {"redirect": "/review-summary"}}
    assert any(e["event"] == "status" for e in events)
    # session state 推到 awaiting_guidance
    assert mod.sessions[sid]["step"] == "awaiting_guidance"
    assert mod.sessions[sid]["summary_body"] == "fake summary"
    assert mod.sessions[sid]["summary_path"].startswith("KB/Wiki/Sources/")


def test_events_step_summarizing_md_with_frontmatter(client, vault, monkeypatch):
    """有 frontmatter 的 md 檔，title/author 從 fm 取。"""
    tc, mod = client
    raw = vault / "Inbox" / "kb" / "with-fm.md"
    raw.parent.mkdir(parents=True)
    raw.write_text("---\ntitle: Custom Title\nauthor: Jane Doe\n---\nbody")
    sid = mod._new_session(
        step="summarizing",
        raw_path=str(raw),
        file_path=str(raw),
        source_type="article",
    )
    _mock_summarizing_io(monkeypatch, mod)

    r = tc.get(f"/events/{sid}")
    assert r.status_code == 200
    sess = mod.sessions[sid]
    assert sess["_title"] == "Custom Title"
    assert sess["_author"] == "Jane Doe"


def test_events_step_summarizing_raw_path_outside_vault_fallback(
    client, vault, tmp_path, monkeypatch
):
    """raw_path 不在 vault 下 → ValueError fallback 用 absolute str。"""
    tc, mod = client
    # 放到 vault 的同層 sibling，確保 relative_to(vault) 會 raise
    outside_dir = tmp_path.parent / f"outside-{tmp_path.name}"
    outside_dir.mkdir(exist_ok=True)
    outside = outside_dir / "outside.md"
    outside.write_text("body")
    sid = mod._new_session(
        step="summarizing",
        raw_path=str(outside),
        file_path=str(outside),
        source_type="article",
    )
    write_page_mock = MagicMock()
    monkeypatch.setattr(mod.pipeline, "_generate_summary", MagicMock(return_value="summary"))
    import shared.obsidian_writer as ow

    monkeypatch.setattr(ow, "write_page", write_page_mock)

    r = tc.get(f"/events/{sid}")
    assert r.status_code == 200
    # write_page 第三個位置參數是 frontmatter dict，含 source_refs；
    # 應為 absolute str（fallback）而不是 relative
    fm = write_page_mock.call_args[0][1]
    source_refs = fm["source_refs"]
    assert source_refs == [str(outside)]


def test_events_step_summarizing_pdf_path(client, vault, monkeypatch):
    """PDF 走 parse_pdf 路徑，需 mock 該 import。"""
    tc, mod = client
    raw = vault / "Inbox" / "kb" / "fake.pdf"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"%PDF-1.4\nfake")
    sid = mod._new_session(
        step="summarizing",
        raw_path=str(raw),
        file_path=str(raw),
        source_type="paper",
    )

    import shared.pdf_parser as pdf_parser

    monkeypatch.setattr(pdf_parser, "parse_pdf", MagicMock(return_value="parsed pdf body"))
    _mock_summarizing_io(monkeypatch, mod)

    r = tc.get(f"/events/{sid}")
    assert r.status_code == 200
    events = _parse_sse(r.text)
    # 應該有「正在解析 PDF...」這個 status
    status_msgs = [e["data"].get("msg", "") for e in events if e["event"] == "status"]
    assert any("解析 PDF" in m for m in status_msgs)
    assert events[-1] == {"event": "done", "data": {"redirect": "/review-summary"}}


def test_events_step_summarizing_large_doc_announces_chunking(client, vault, monkeypatch):
    """超過 LARGE_DOC_THRESHOLD → 應提示 Map-Reduce 分段。"""
    tc, mod = client
    big_text = "x" * (mod.pipeline.LARGE_DOC_THRESHOLD + 100)
    raw = vault / "Inbox" / "kb" / "big.md"
    raw.parent.mkdir(parents=True)
    raw.write_text(big_text)
    sid = mod._new_session(
        step="summarizing",
        raw_path=str(raw),
        file_path=str(raw),
        source_type="article",
    )

    _mock_summarizing_io(monkeypatch, mod)

    # mock chunker.chunk_document 回 3 個 chunk，避免真跑分段邏輯
    from agents.robin import chunker

    monkeypatch.setattr(
        chunker,
        "chunk_document",
        MagicMock(return_value=["c1", "c2", "c3"]),
    )

    r = tc.get(f"/events/{sid}")
    assert r.status_code == 200
    events = _parse_sse(r.text)
    status_msgs = [e["data"].get("msg", "") for e in events if e["event"] == "status"]
    assert any("Map-Reduce" in m for m in status_msgs)
    assert any("分 3 段" in m for m in status_msgs)


# ---------------------------------------------------------------------------
# Step: planning
# ---------------------------------------------------------------------------


def test_events_step_planning_redirects_to_review_plan(client, monkeypatch):
    tc, mod = client
    sid = mod._new_session(
        step="planning",
        summary_body="summary",
        summary_path="KB/Wiki/Sources/x.md",
        user_guidance="extract X",
        content_nature="popular_science",
    )
    monkeypatch.setattr(
        mod.pipeline,
        "_get_concept_plan",
        MagicMock(
            return_value={
                "concepts": [{"slug": "A", "action": "create", "title": "A"}],
                "entities": [],
            }
        ),
    )

    r = tc.get(f"/events/{sid}")
    assert r.status_code == 200
    events = _parse_sse(r.text)
    assert events[-1] == {"event": "done", "data": {"redirect": "/review-plan"}}
    sess = mod.sessions[sid]
    assert sess["step"] == "awaiting_approval"
    assert sess["plan"] == {
        "concepts": [{"slug": "A", "action": "create", "title": "A"}],
        "entities": [],
    }


def test_events_step_planning_none_plan_falls_back_to_empty(client, monkeypatch):
    """_get_concept_plan 回 None → fallback 到空 plan，不 crash。"""
    tc, mod = client
    sid = mod._new_session(
        step="planning",
        summary_body="x",
        summary_path="x.md",
        user_guidance="",
    )
    monkeypatch.setattr(mod.pipeline, "_get_concept_plan", MagicMock(return_value=None))

    r = tc.get(f"/events/{sid}")
    assert r.status_code == 200
    sess = mod.sessions[sid]
    assert sess["plan"] == {"concepts": [], "entities": []}


# ---------------------------------------------------------------------------
# Step: executing
# ---------------------------------------------------------------------------


def test_events_step_executing_redirects_to_done(client, vault, monkeypatch):
    tc, mod = client
    raw = vault / "Inbox" / "kb" / "exec.md"
    raw.parent.mkdir(parents=True)
    raw.write_text("hi")
    sid = mod._new_session(
        step="executing",
        raw_path=str(raw),
        file_path=str(raw),
        source_type="article",
        summary_path="KB/Wiki/Sources/x.md",
        plan={
            "concepts": [
                {"slug": "A", "action": "create", "title": "A"},
                {"slug": "B", "action": "update_merge", "title": "B"},
            ],
            "entities": [{"title": "C", "entity_type": "person"}],
        },
        _title="My Title",
    )

    execute_plan = MagicMock()
    update_index = MagicMock()
    mark_processed = MagicMock()
    recycle = MagicMock()
    monkeypatch.setattr(mod.pipeline, "_execute_plan", execute_plan)
    monkeypatch.setattr(mod.pipeline, "_update_index", update_index)
    monkeypatch.setattr(mod, "mark_file_processed", mark_processed)
    monkeypatch.setattr(mod, "_send_to_recycle_bin", recycle)

    r = tc.get(f"/events/{sid}")
    assert r.status_code == 200
    events = _parse_sse(r.text)
    assert events[-1] == {"event": "done", "data": {"redirect": "/done"}}
    sess = mod.sessions[sid]
    assert sess["step"] == "done"
    # created = concept create + entity create; updated = concept update_merge/conflict;
    # referenced = concept noop (none in this fixture)
    assert sess["result"] == {
        "created": ["A", "C"],
        "updated": ["B"],
        "referenced": [],
    }

    # status 應提示「寫入 3 個 Wiki 頁面」(2 concept + 1 entity = 3 writes; no noop)
    status_msgs = [e["data"].get("msg", "") for e in events if e["event"] == "status"]
    assert any("3 個" in m for m in status_msgs)

    # 各 side-effect 都要被呼叫過 — 防 regression
    execute_plan.assert_called_once()
    update_index.assert_called_once()
    mark_processed.assert_called_once()
    recycle.assert_called_once()


def test_events_step_executing_falls_back_to_raw_stem_when_title_missing(
    client, vault, monkeypatch
):
    """sess["_title"] 不存在 → 使用 raw_path stem 當 title。"""
    tc, mod = client
    raw = vault / "Inbox" / "kb" / "stem-title.md"
    raw.parent.mkdir(parents=True)
    raw.write_text("hi")
    sid = mod._new_session(
        step="executing",
        raw_path=str(raw),
        file_path=str(raw),
        source_type="article",
        summary_path="KB/Wiki/Sources/y.md",
        plan={"concepts": [], "entities": []},
    )

    monkeypatch.setattr(mod.pipeline, "_execute_plan", MagicMock())
    update_index = MagicMock()
    monkeypatch.setattr(mod.pipeline, "_update_index", update_index)
    monkeypatch.setattr(mod, "mark_file_processed", MagicMock())
    monkeypatch.setattr(mod, "_send_to_recycle_bin", MagicMock())

    r = tc.get(f"/events/{sid}")
    assert r.status_code == 200
    # _update_index 第一個位置參數是 title
    title_arg = update_index.call_args[0][0]
    assert title_arg == "stem-title"


# ---------------------------------------------------------------------------
# Step: awaiting_guidance / awaiting_approval / done — pure redirect
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "step,expected_redirect",
    [
        ("awaiting_guidance", "/review-summary"),
        ("awaiting_approval", "/review-plan"),
        ("done", "/done"),
    ],
)
def test_events_step_redirects_match_map(client, step, expected_redirect):
    tc, mod = client
    sid = mod._new_session(step=step)
    r = tc.get(f"/events/{sid}")
    assert r.status_code == 200
    events = _parse_sse(r.text)
    assert events == [{"event": "done", "data": {"redirect": expected_redirect}}]


# ---------------------------------------------------------------------------
# Unknown step + exception
# ---------------------------------------------------------------------------


def test_events_unknown_step_yields_error_event(client):
    tc, mod = client
    sid = mod._new_session(step="bogus_step")
    r = tc.get(f"/events/{sid}")
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
    raw = vault / "Inbox" / "kb" / "boom.md"
    raw.parent.mkdir(parents=True)
    raw.write_text("body")
    sid = mod._new_session(
        step="summarizing",
        raw_path=str(raw),
        file_path=str(raw),
        source_type="article",
    )

    def raises(*a, **kw):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(mod.pipeline, "_generate_summary", raises)

    r = tc.get(f"/events/{sid}")
    assert r.status_code == 200
    events = _parse_sse(r.text)
    error_events = [e for e in events if e["event"] == "error"]
    assert error_events
    assert "LLM down" in error_events[-1]["data"]["msg"]

    sess = mod.sessions[sid]
    assert sess["step"] == "error"
    assert sess["error"] == "LLM down"
