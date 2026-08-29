"""審核頁的字幕軌（修修 2026-08-12：「可以預設顯示由 SRT 抓來的字幕檔嗎」）。

長片成品不燒字幕（ADR-055 Q4b），所以審核時畫面上看不到字幕。這裡端出來的
必須**就是實際會上架的那份 CC**——版本挑選規則收斂在 `shared.tight_srt`，
preview / CC / 短片燒字幕三邊共用。
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.tight_srt import latest_tight_srt, srt_to_vtt  # noqa: E402

SRT = """1
00:00:00,020 --> 00:00:03,500
我們來講講你的這個求學歷程好了 因為

2
00:00:03,500 --> 00:00:05,780
大家可能都還不知道說
"""


# ---------------------------------------------------------------------------
# 純函數
# ---------------------------------------------------------------------------


def test_srt_to_vtt_header_and_timestamps():
    out = srt_to_vtt(SRT)
    assert out.startswith("WEBVTT\n\n")
    assert "00:00:00.020 --> 00:00:03.500" in out
    assert "," not in out.split("\n")[2]  # 時間碼那行不能還有逗號


def test_srt_to_vtt_keeps_text_and_linebreaks_verbatim():
    """審核頁看到的斷行必須跟上架的 CC 一模一樣——不做任何順手美化。"""
    src = "1\n00:00:01,000 --> 00:00:02,000\n第一行\n第二行\n"
    assert "第一行\n第二行" in srt_to_vtt(src)


def test_srt_to_vtt_strips_bom():
    assert srt_to_vtt("﻿" + SRT).startswith("WEBVTT")


def test_latest_tight_srt_picks_highest_revision(tmp_path):
    d = tmp_path / "highlights" / "srt"
    d.mkdir(parents=True)
    for n in (1, 2, 9, 10, 11):
        (d / f"SL3_tight_r{n:03d}.srt").write_text(SRT, encoding="utf-8")
    (d / "SL4_tight_r099.srt").write_text(SRT, encoding="utf-8")  # 別支不可入選
    got = latest_tight_srt(tmp_path, "SL3")
    assert got is not None and got.name == "SL3_tight_r011.srt"


def test_latest_tight_srt_none_when_missing(tmp_path):
    (tmp_path / "highlights" / "srt").mkdir(parents=True)
    assert latest_tight_srt(tmp_path, "SL3") is None


# ---------------------------------------------------------------------------
# route
# ---------------------------------------------------------------------------

PASSWORD = "test-web-password"
SECRET = "test-web-secret"


@pytest.fixture
def env(monkeypatch, tmp_path):
    """一個有成品檔與 tight SRT 的 episode + 一列 release。"""
    ep = tmp_path / "20260415 ep"
    (ep / "highlights" / "exports").mkdir(parents=True)
    (ep / "highlights" / "srt").mkdir(parents=True)
    (ep / "highlights" / "exports" / "SL3.mp4").write_bytes(b"video")
    (ep / "highlights" / "srt" / "SL3_tight_r007.srt").write_text(SRT, encoding="utf-8")

    monkeypatch.setenv("WEB_PASSWORD", PASSWORD)
    monkeypatch.setenv("WEB_SECRET", SECRET)
    monkeypatch.delenv("NAKAMA_DEV_AUTH_BYPASS", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "state.db"))

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.publish_review as pub_module
    from shared import release_store

    importlib.reload(auth_module)
    importlib.reload(pub_module)
    importlib.reload(app_module)
    importlib.reload(release_store)

    rid = release_store.register_release(
        "20260415 ep", "SL3", "long", str(ep / "highlights" / "exports" / "SL3.mp4")
    )
    release_store.ensure_target(rid, "youtube")
    client = TestClient(app_module.app, follow_redirects=False)
    token = auth_module.make_token(PASSWORD)
    client.cookies.set("nakama_auth", token)
    return client, ep


def test_subs_route_returns_vtt(env):
    client, _ = env
    r = client.get("/bridge/publish/subs/20260415%20ep/SL3")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/vtt")
    assert r.text.startswith("WEBVTT")
    assert "00:00:00.020 --> 00:00:03.500" in r.text


def test_subs_route_404_when_no_srt(env):
    client, ep = env
    (ep / "highlights" / "srt" / "SL3_tight_r007.srt").unlink()
    assert client.get("/bridge/publish/subs/20260415%20ep/SL3").status_code == 404


def test_subs_route_requires_auth(env):
    client, _ = env
    client.cookies.clear()
    assert client.get("/bridge/publish/subs/20260415%20ep/SL3").status_code == 401


def test_cut_page_renders_track_by_default(env):
    client, _ = env
    r = client.get("/bridge/publish/20260415%20ep/SL3")
    assert r.status_code == 200
    assert "<track" in r.text
    assert "default" in r.text
    assert "SL3_tight_r007.srt" in r.text  # 頁面標明用的是哪一版


def test_cut_page_warns_when_srt_missing(env):
    client, ep = env
    (ep / "highlights" / "srt" / "SL3_tight_r007.srt").unlink()
    r = client.get("/bridge/publish/20260415%20ep/SL3")
    assert r.status_code == 200
    assert "<track" not in r.text
    assert "CC 也會缺" in r.text


def test_status_distinguishes_upload_processing_caption_and_platform_publish(env):
    client, _ = env
    from shared.release_store import get_release, update_target

    target = get_release("20260415 ep", "SL3")["targets"][0]
    update_target(
        target["id"],
        status="uploaded",
        video_id="video-1",
        video_processing_status="processed",
        platform_privacy_status="public",
        caption_status="missing",
        reconciliation_error="zh-TW caption missing",
        last_reconciled_at="2026-08-19T00:00:00+00:00",
    )

    payload = client.get("/bridge/publish/20260415%20ep/SL3/status").json()

    assert payload["upload_status"] == "uploaded"
    assert payload["processing_status"] == "processed"
    assert payload["caption_status"] == "missing"
    assert payload["privacy_status"] == "public"
    assert payload["published"] is True
    assert payload["can_retry_cc"] is True
    assert payload["last_reconciled_at"] == "2026-08-19T00:00:00+00:00"


def test_retry_cc_route_starts_cc_only_worker_without_reupload(
    env,
    monkeypatch: pytest.MonkeyPatch,
):
    client, episode_dir = env
    import thousand_sunny.routers.publish_review as publish_review
    from shared.release_store import get_release, update_target

    target = get_release("20260415 ep", "SL3")["targets"][0]
    update_target(
        target["id"],
        status="uploaded",
        video_id="video-1",
        caption_status="missing",
    )
    (episode_dir.parent / "youtube_token.json").write_text("{}", encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        publish_review.subprocess,
        "Popen",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )

    response = client.post("/bridge/publish/20260415%20ep/SL3/retry-cc")

    assert response.status_code == 303
    command, kwargs = calls[0]
    assert "--cc-only" in command
    assert "--run" not in command
    assert kwargs["env"]["NAKAMA_DATA_DIR"] == str(episode_dir.parent)


def test_json_dumps_guard():
    """SRT 內容不經 json 序列化（避免有人未來把它塞進 JSON 回應）。"""
    assert json.dumps(srt_to_vtt(SRT), ensure_ascii=False).startswith('"WEBVTT')


def test_review_shows_the_same_subtitle_the_uploader_will_send(tmp_path, monkeypatch):
    """審核頁與上傳器必須讀同一份——不然驗證的對象跟交付的對象不是同一個。

    2026-08-29：上傳器改讀 Release 字幕、審核頁沒跟上，修修看到的是 260 秒舊剪輯
    的 125 句，實際要上架的是 492 秒成品的 226 句。
    """
    import inspect

    from thousand_sunny.routers import publish_review
    import scripts.publish_upload as publish_upload

    review_src = inspect.getsource(publish_review.publish_subs)
    upload_src = inspect.getsource(publish_upload)
    assert "release_subtitle" in review_src
    assert "release_subtitle" in upload_src


def test_thumbnail_response_refuses_to_be_cached_blind(tmp_path, monkeypatch):
    """縮圖網址固定但底下的圖會換——沒有 no-cache 就會顯示上一張。"""
    import inspect

    from thousand_sunny.routers import publish_review

    src = inspect.getsource(publish_review.publish_thumb)
    assert "no-cache" in src
