"""Bridge 挑片段頁（第 11 類他人影片引用）的 HTTP 契約。不連網。"""

from __future__ import annotations

import importlib
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agents.brook.youtube_video import clips
from agents.brook.youtube_video import visual_plan as vp

EPISODE = "20261201 測試集"
META = {
    "video_id": "wTBhRVkOSJM",
    "title": "Rational </script> 376",
    "channel": "Rational Reminder",
    "duration": 3846,
}


def _row(row_id: str, category: str, start: int, end: int) -> dict:
    row = {
        "id": row_id,
        "origin": "director",
        "kind": "overlay",
        "category": category,
        "start_cue": start,
        "end_cue": end,
        "before_cue": None,
        "insert_sec": None,
        "layout": "full",
        "description": "Nick 三段訪談",
        "dp_instruction": "Nick Maggiulli 接受訪問的畫面",
        "onscreen_text": "",
        "source": "各節目",
        "status": None,
        "note": "",
        "program_state": None,
    }
    row["original"] = vp.snapshot(row)
    return row


@pytest.fixture
def episode_root(tmp_path):
    cues = [
        {"id": i, "start": i * 4.0, "end": i * 4.0 + 3.5, "text": f"第 {i} 句"}
        for i in range(1, 11)
    ]
    plan = {
        "schema": vp.SCHEMA,
        "episode": EPISODE,
        "cues": cues,
        "rows": [_row("clip", "others_clip", 2, 4), _row("title", "concept_title", 6, 6)],
    }
    (tmp_path / EPISODE).mkdir()
    vp.save_plan(tmp_path / EPISODE / vp.PLAN_FILE, plan)
    picks = clips.new_picks("clip")
    clips.add_candidate(picks, META, 270.25, 273.8, "Nick 在回答")
    clips.save_picks(clips.picks_path(tmp_path / EPISODE, "clip"), picks)
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


def _url(path: str = "") -> str:
    return f"/bridge/video-plan/{quote(EPISODE)}/clips/clip{path}"


def _saved(episode_root) -> dict:
    return clips.load_picks(clips.picks_path(episode_root / EPISODE, "clip"))


def test_page_lists_candidates_and_escapes_script_close(client):
    res = client.get(_url())
    assert res.status_code == 200
    assert "wTBhRVkOSJM" in res.text
    assert "Rational <\\/script> 376" in res.text
    assert "</script> 376" not in res.text


def test_page_only_for_others_clip_rows(client):
    assert client.get(f"/bridge/video-plan/{quote(EPISODE)}/clips/title").status_code == 404
    assert client.get(f"/bridge/video-plan/{quote(EPISODE)}/clips/nope").status_code == 404


def test_page_requires_login(client):
    client.cookies.clear()
    res = client.get(_url(), follow_redirects=False)
    assert res.status_code == 302
    assert client.patch(_url("/c1"), json={"status": "approved"}).status_code == 401


def test_mark_and_approve_saves_to_picks_file(client, episode_root):
    res = client.patch(_url("/c1"), json={"start": 271.5, "status": "approved"})
    assert res.status_code == 200
    body = res.json()
    assert body["approved"] == 1
    assert body["approved_sec"] == pytest.approx(2.3)
    saved = _saved(episode_root)["candidates"][0]
    assert saved["start"] == 271.5 and saved["status"] == "approved"


def test_bad_marks_are_rejected_and_not_saved(client, episode_root):
    res = client.patch(_url("/c1"), json={"end": 100})
    assert res.status_code == 422
    assert "終點" in res.json()["error"]
    assert _saved(episode_root)["candidates"][0]["end"] == 273.8


def test_add_url_reads_info_without_downloading(client, episode_root, monkeypatch):
    import thousand_sunny.routers.video_plan as router_module

    seen = []
    monkeypatch.setattr(
        router_module.clips,
        "info",
        lambda url: (
            seen.append(url)
            or {**META, "video_id": "xkMeya7s_s8", "channel": "The Ani-Mates", "duration": 175}
        ),
    )
    res = client.post(_url(), json={"url": "https://youtu.be/xkMeya7s_s8"})
    assert res.status_code == 200
    cand = res.json()["candidate"]
    assert cand["id"] == "c2" and cand["start"] == 0 and cand["end"] == 4
    assert seen == ["https://youtu.be/xkMeya7s_s8"]
    assert len(_saved(episode_root)["candidates"]) == 2
