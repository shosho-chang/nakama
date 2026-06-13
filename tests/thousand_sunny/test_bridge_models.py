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
