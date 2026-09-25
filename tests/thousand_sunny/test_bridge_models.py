"""N531 slice 3 — /bridge/models 面板 router 測試。

Minimal app 掛 bridge_models.router + dev auth bypass + 隔離 override 檔，
驗 GET 渲染矩陣、POST set/reset 寫/清 override、未知 model 被擋。
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("NAKAMA_DEV_AUTH_BYPASS", "1")
    monkeypatch.setenv("NAKAMA_MODEL_OVERRIDES", str(tmp_path / "ov.json"))
    # auth 的 _DEV_AUTH_BYPASS 在 import 時固定 → reload 讓 env 生效
    import thousand_sunny.auth as auth

    importlib.reload(auth)
    import shared.llm_router as r

    importlib.reload(r)
    import thousand_sunny.routers.bridge_models as bm

    importlib.reload(bm)

    app = FastAPI()
    app.include_router(bm.router)
    return TestClient(app), r


def test_get_renders_matrix(client):
    c, _ = client
    resp = c.get("/bridge/models")
    assert resp.status_code == 200
    # 登記的 call site 出現在頁面
    assert "concept_merge" in resp.text
    assert "claude-opus-4-7" in resp.text
    assert "模型路由" in resp.text


def test_post_set_writes_override_and_redirects(client):
    c, r = client
    resp = c.post(
        "/bridge/models/set",
        data={"agent": "robin", "task": "concept_merge", "model": "gemini-2.5-pro"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert r.get_override("robin", "concept_merge") == "gemini-2.5-pro"
    # 改後 get_model 即時反映
    assert r.get_model("robin", "concept_merge") == "gemini-2.5-pro"


def test_post_set_rejects_unknown_model(client):
    c, r = client
    resp = c.post(
        "/bridge/models/set",
        data={"agent": "robin", "task": "concept_merge", "model": "totally-made-up-9"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "err=unknown_model" in resp.headers["location"]
    assert r.get_override("robin", "concept_merge") is None  # 未寫入


def test_post_reset_clears_override(client):
    c, r = client
    r.set_override("robin", "kb_search", "claude-opus-4-7")
    assert r.get_override("robin", "kb_search") == "claude-opus-4-7"
    resp = c.post(
        "/bridge/models/reset",
        data={"agent": "robin", "task": "kb_search"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert r.get_override("robin", "kb_search") is None


# ── Transport indicator（Slice 5）─────────────────────────────────────────────
def test_transport_for_covers_all_branches(monkeypatch):
    from thousand_sunny.routers import bridge_models as bm

    monkeypatch.setenv("LLM_TRANSPORT", "openrouter")
    assert bm._transport_for({"provider": "google", "agent": "robin", "task": "x"}) == "openrouter"
    assert bm._transport_for({"provider": "openai", "agent": "robin", "task": "x"}) == "openrouter"
    assert (
        bm._transport_for({"provider": "anthropic", "agent": "nami", "task": "default"})
        == "openrouter"
    )
    # xAI carve-out：OpenRouter 無 grok tier，恆 native
    assert bm._transport_for({"provider": "xai", "agent": "sanji", "task": "default"}) == "native"
    # Anthropic 訂閱 → native（claude -p Max Plan）
    monkeypatch.setenv("AUTH_NAMI", "subscription_required")
    assert (
        bm._transport_for({"provider": "anthropic", "agent": "nami", "task": "default"}) == "native"
    )
    # kill-switch：未設 → 全 native
    monkeypatch.delenv("LLM_TRANSPORT")
    assert bm._transport_for({"provider": "google", "agent": "robin", "task": "x"}) == "native"


def test_get_shows_native_transport_when_disabled(client, monkeypatch):
    monkeypatch.delenv("LLM_TRANSPORT", raising=False)
    c, _ = client
    resp = c.get("/bridge/models")
    assert resp.status_code == 200
    assert "mdl-trans--native" in resp.text
    assert "mdl-trans--openrouter" not in resp.text
    assert "LLM_TRANSPORT=openrouter" in resp.text  # hint：如何啟用


def test_get_shows_openrouter_transport_when_enabled(client, monkeypatch):
    monkeypatch.setenv("LLM_TRANSPORT", "openrouter")
    c, _ = client
    resp = c.get("/bridge/models")
    assert resp.status_code == 200
    # registry 預設多為 claude-* / gemini → 進 OpenRouter
    assert "mdl-trans--openrouter" in resp.text
    assert "OpenRouter" in resp.text


def test_get_per_agent_transport_override(client, monkeypatch):
    """LLM_TRANSPORT_<AGENT> 讓單一 agent 走 OpenRouter，面板逐列反映（全域仍 off）。"""
    monkeypatch.delenv("LLM_TRANSPORT", raising=False)
    monkeypatch.setenv("LLM_TRANSPORT_NAMI", "openrouter")
    c, _ = client
    resp = c.get("/bridge/models")
    assert resp.status_code == 200
    # Nami（claude api）那列 → openrouter；其餘 agent 全域 off → native，兩者並存
    assert "mdl-trans--openrouter" in resp.text
    assert "mdl-trans--native" in resp.text


# ── ADR-070 D1：L1 / L2 標示 ───────────────────────────────────────────────
def _row(model: str, provider: str, agent: str = "robin") -> dict:
    return {"model": model, "provider": provider, "agent": agent, "task": "x"}


def test_transport_for_l2_slug_is_openrouter_regardless_of_transport_env(monkeypatch):
    from thousand_sunny.routers import bridge_models as bm

    monkeypatch.delenv("LLM_TRANSPORT", raising=False)
    site = _row("openai/gpt-5.6-terra", "unknown")
    assert bm._transport_for(site) == "openrouter"
    assert bm._transport_label(site, "openrouter") == "openrouter (L2)"


def test_transport_for_claude_empty_cutover_keeps_legacy(monkeypatch):
    """S1 出貨狀態（L1_CUTOVER_GROUPS 空）：Claude 列的標示跟以前一模一樣。"""
    from shared import llm
    from thousand_sunny.routers import bridge_models as bm

    assert llm.L1_CUTOVER_GROUPS == frozenset()
    monkeypatch.delenv("LLM_TRANSPORT", raising=False)
    site = _row("sonnet", "anthropic")
    assert bm._transport_for(site) == "native"
    assert bm._transport_label(site, "native") == "native"
    monkeypatch.setenv("LLM_TRANSPORT", "openrouter")
    assert bm._transport_for(site) == "openrouter"
    assert bm._transport_label(site, "openrouter") == "OpenRouter"  # 舊標籤不變


def test_transport_for_claude_in_cutover_is_subscription(monkeypatch):
    from shared import llm
    from thousand_sunny.routers import bridge_models as bm

    monkeypatch.delenv("LLM_TRANSPORT", raising=False)
    monkeypatch.setattr(llm, "L1_CUTOVER_GROUPS", frozenset({"gateway", "cron"}))
    site = _row("claude-sonnet-4-6", "anthropic", agent="nami")
    # 只切了部分 group：這一列仍是舊路徑（面板不知道它在哪種 process 跑），附註已切的 group
    assert bm._transport_for(site) == "native"
    assert bm._transport_label(site, "native") == "native · cron / gateway 程序走 L1"
    # 非 Claude model 不受 L1 影響
    gemini = _row("gemini-2.5-pro", "google")
    monkeypatch.delenv("LLM_TRANSPORT", raising=False)
    assert bm._transport_for(gemini) == "native"

    monkeypatch.setattr(
        llm, "L1_CUTOVER_GROUPS", frozenset({"gateway", "cron", "bridge", "desktop"})
    )
    assert bm._transport_for(site) == "subscription"
    assert bm._transport_label(site, "subscription") == "subscription (L1)"


def test_get_page_shows_l1_chip_when_cut_over(client, monkeypatch):
    from shared import llm

    monkeypatch.delenv("LLM_TRANSPORT", raising=False)
    monkeypatch.setattr(llm, "L1_CUTOVER_GROUPS", frozenset({"gateway"}))
    c, _ = client
    resp = c.get("/bridge/models")
    assert resp.status_code == 200
    # 部分切換：不能整列標成訂閱（Robin 等在 cron / Bridge 跑的列仍走舊路徑）
    assert "mdl-trans--subscription" not in resp.text
    assert "native · gateway 程序走 L1" in resp.text

    monkeypatch.setattr(
        llm, "L1_CUTOVER_GROUPS", frozenset({"gateway", "cron", "bridge", "desktop"})
    )
    resp = c.get("/bridge/models")
    assert "mdl-trans--subscription" in resp.text
    assert "subscription (L1)" in resp.text


def test_get_page_without_cutover_has_no_l1_chip(client, monkeypatch):
    monkeypatch.delenv("LLM_TRANSPORT", raising=False)
    c, _ = client
    resp = c.get("/bridge/models")
    assert resp.status_code == 200
    assert "mdl-trans--subscription" not in resp.text
    assert "(L1" not in resp.text
