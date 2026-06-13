"""N532 — /robin/literature/{slug} 文獻筆記 viewer 測試。

minimal app 掛 robin_router + dev auth bypass + 隔離 vault，驗：有檔渲染、
無檔空態、path-traversal slug 不外逃。
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
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    lit = tmp_path / "KB" / "Literature"
    lit.mkdir(parents=True)
    note_md = (
        "---\ntype: literature\ntitle: 卡片盒筆記\n---\n\n"
        "## 劃線與心得\n\n> 一個好的系統。 ^cfi-1\n"
    )
    (lit / "卡片盒筆記.md").write_text(note_md, encoding="utf-8")
    import thousand_sunny.auth as auth

    importlib.reload(auth)
    import thousand_sunny.routers.robin as robin

    importlib.reload(robin)

    app = FastAPI()
    app.include_router(robin.robin_router)
    return TestClient(app)


def test_view_existing_note_renders_body(client):
    resp = client.get("/robin/literature/卡片盒筆記")
    assert resp.status_code == 200
    assert "劃線與心得" in resp.text
    assert "一個好的系統" in resp.text
    assert "卡片盒筆記" in resp.text


def test_view_missing_note_shows_empty_state(client):
    resp = client.get("/robin/literature/不存在的書")
    assert resp.status_code == 200
    assert "尚無文獻筆記" in resp.text


def test_view_path_traversal_slug_is_contained(client):
    # ../ 不該逃出 KB/Literature；safe_resolve 擋下 → 當作不存在（空態）或 4xx，總之不外讀
    resp = client.get("/robin/literature/..%2f..%2fsecret")
    # 依框架路由 / safe_resolve guard，可能 404（路由不匹配）或 403/400（被擋）——
    # 重點是不得讀到 vault 外內容、不得 500
    assert resp.status_code in (200, 400, 403, 404)
    assert "type: literature" not in resp.text  # 沒讀到 vault 外的東西
