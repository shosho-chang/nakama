"""Bridge 畫面規劃表的 HTTP 契約：頁面、自動儲存、加畫面、刪除、復原。"""

from __future__ import annotations

import importlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agents.brook.youtube_video import visual_plan as vp

EPISODE = "20261201 測試集"


def _plan() -> dict:
    cues = [
        {"id": i, "start": i * 4.0, "end": i * 4.0 + 3.5, "text": f"第 {i} 句"}
        for i in range(1, 21)
    ]
    title = {
        "id": "title",
        "origin": "director",
        "kind": "overlay",
        "category": "concept_title",
        "start_cue": 4,
        "end_cue": 5,
        "before_cue": None,
        "insert_sec": None,
        "layout": "overlay",
        "description": "大字",
        "dp_instruction": "",
        "onscreen_text": "躍升第 5 階",
        "source": "",
        "status": None,
        "note": "",
        "program_state": None,
    }
    title["original"] = vp.snapshot(title)
    chapter = {
        **title,
        "id": "chapter",
        "origin": "auto",
        "kind": "insert",
        "category": "chapter_card",
        "start_cue": None,
        "end_cue": None,
        "before_cue": 2,
        "insert_sec": 2,
        "layout": "full",
        "onscreen_text": "六個財富階層",
    }
    chapter["original"] = vp.snapshot(chapter)
    return {"schema": vp.SCHEMA, "episode": EPISODE, "cues": cues, "rows": [chapter, title]}


@pytest.fixture
def episode_root(tmp_path):
    episode = tmp_path / EPISODE
    episode.mkdir()
    vp.save_plan(episode / vp.PLAN_FILE, _plan())
    (tmp_path / "空的一集").mkdir()
    return tmp_path


@pytest.fixture
def client(monkeypatch, episode_root):
    monkeypatch.setenv("PODCAST_EPISODES_ROOT", str(episode_root))
    monkeypatch.setenv("WEB_PASSWORD", "gate-password")
    monkeypatch.setenv("WEB_SECRET", "gate-secret")
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.video_plan as router_module

    importlib.reload(auth_module)
    importlib.reload(router_module)
    app = FastAPI()
    app.include_router(router_module.page_router)
    test_client = TestClient(app)
    from thousand_sunny.auth import make_token

    test_client.cookies.set("nakama_auth", make_token("gate-password"))
    return test_client


def _saved(episode_root) -> dict:
    return json.loads((episode_root / EPISODE / vp.PLAN_FILE).read_text(encoding="utf-8"))


def _url(path: str = "") -> str:
    return f"/bridge/video-plan/{EPISODE}{path}"


def test_page_requires_login(client):
    client.cookies.clear()
    response = client.get(_url(), follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith("/login?next=")
    assert client.patch(_url("/rows/title"), json={"description": "x"}).status_code == 401


def test_page_renders_rows_gaps_and_stats(client):
    html = client.get(_url()).text
    assert "畫面規劃表" in html
    assert 'id="row-title"' in html and 'id="row-chapter"' in html
    assert "躍升第 5 階" in html
    assert "秒沒有畫面" in html
    assert "自動" in html
    # 自動列不能刪除、不能改分類
    chapter_html = html.split('id="row-chapter"')[1].split("</li>")[0]
    assert "data-delete" not in chapter_html
    assert "<select" not in chapter_html


def test_empty_episode_shows_empty_state(client):
    html = client.get("/bridge/video-plan/空的一集").text
    assert "這一集還沒有畫面規劃表" in html


def test_index_lists_episodes_with_a_plan(client):
    html = client.get("/bridge/video-plan").text
    assert EPISODE in html
    assert "空的一集" not in html


def test_autosave_approves_on_visible_edit(client, episode_root):
    response = client.patch(_url("/rows/title"), json={"onscreen_text": "第 5 階"})
    assert response.status_code == 200
    body = response.json()
    assert body["row"]["status"] == "approved"
    assert body["stats"]["approved"] == 1
    saved = next(row for row in _saved(episode_root)["rows"] if row["id"] == "title")
    assert saved["onscreen_text"] == "第 5 階" and saved["status"] == "approved"


def test_rearrange_without_note_is_rejected(client, episode_root):
    response = client.patch(_url("/rows/title"), json={"status": "rearrange"})
    assert response.status_code == 422
    assert "修正意見" in response.json()["error"]
    assert next(r for r in _saved(episode_root)["rows"] if r["id"] == "title")["status"] is None


def test_add_row_from_gap(client, episode_root):
    response = client.post(_url("/rows"), json={"start_cue": 10, "end_cue": 12})
    assert response.status_code == 200
    new_id = response.json()["row"]["id"]
    saved = next(r for r in _saved(episode_root)["rows"] if r["id"] == new_id)
    assert saved["origin"] == "manual" and (saved["start_cue"], saved["end_cue"]) == (10, 12)


def test_delete_and_restore(client, episode_root):
    client.patch(_url("/rows/title"), json={"onscreen_text": "改過"})
    assert client.post(_url("/rows/title/restore")).status_code == 200
    title = next(r for r in _saved(episode_root)["rows"] if r["id"] == "title")
    assert title["onscreen_text"] == "躍升第 5 階" and title["status"] is None
    assert client.delete(_url("/rows/chapter")).status_code == 422
    assert client.delete(_url("/rows/title")).status_code == 200
    assert [r["id"] for r in _saved(episode_root)["rows"]] == ["chapter"]


def test_path_traversal_is_refused(client):
    assert client.get("/bridge/video-plan/..%5C..").status_code == 404


def test_missing_root_env_is_a_503(client, monkeypatch):
    monkeypatch.delenv("PODCAST_EPISODES_ROOT")
    assert client.get(_url()).status_code == 503
