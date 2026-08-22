
"""HTTP contract for the Stage 5 finished-cut review gate."""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scripts.finished_review_watcher import prepare_trusted_asset_handoff


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_manifest(episode: Path, *, feedback_file: str | None = None) -> Path:
    review = episode / "highlights" / "review"
    review.mkdir(parents=True, exist_ok=True)
    cuts = []
    for cut_id, title in (("R11", "將太的壽司"), ("R12", "短影音擴圈"), ("R3", "爸爸做的是對的")):
        cut_dir = review / cut_id
        cut_dir.mkdir(exist_ok=True)
        preview = cut_dir / f"{cut_id}.mp4"
        preview.write_bytes((cut_id + "-preview").encode() * 20)
        subtitles = cut_dir / "subs.srt"
        subtitles.write_text("1\n00:00:01,000 --> 00:00:02,000\n修修的字幕\n", encoding="utf-8")
        components = [
            {
                "component_id": f"{cut_id}-broll",
                "lane": "b_roll",
                "kind": "video",
                "asset_category": "stock_video",
                "slug": "sushi-closeup",
                "note": "對齊將太的壽司",
                "t0": 10.0,
                "t1": 14.0,
            },
            {
                "component_id": f"{cut_id}-broll-2",
                "lane": "b_roll",
                "kind": "video",
                "asset_category": "stock_video",
                "slug": "chef-workflow",
                "t0": 40.0,
                "t1": 44.0,
            },
            {
                "component_id": f"{cut_id}-broll-3",
                "lane": "b_roll",
                "kind": "video",
                "asset_category": "stock_video",
                "slug": "restaurant-service",
                "t0": 50.0,
                "t1": 54.0,
            },
            {
                "component_id": f"{cut_id}-hero",
                "lane": "hero_title",
                "text": "職人精神",
                "t0": 20.0,
                "t1": 23.5,
            },
            {
                "component_id": f"{cut_id}-transition",
                "lane": "fullscreen_transition",
                "vars": {"kicker": "01", "title": "從漫畫學經營"},
                "t0": 30.0,
                "t1": 33.0,
            },
            {
                "component_id": f"{cut_id}-badge",
                "lane": "badge",
                "slug": "brand-badge",
                "t0": 0.0,
                "t1": 8.0,
            },
        ]
        cuts.append(
            {
                "cut_id": cut_id,
                "format": "long",
                "title": title,
                "artifacts": {
                    "preview": {
                        "path": str(preview),
                        "bytes": preview.stat().st_size,
                        "sha256": _sha256(preview),
                        "duration_seconds": 90.0,
                    },
                    "subtitles": {
                        "path": str(subtitles),
                        "bytes": subtitles.stat().st_size,
                        "sha256": _sha256(subtitles),
                    },
                },
                "components": components,
            }
        )
    payload = {
        "schema": "nakama.finished_cut_review_manifest.v1",
        "episode_id": episode.name,
        "stage": 5,
        "gate": {
            "kind": "finished_cut_review",
            "status": "ready_for_review",
            "feedback_file": feedback_file or str(review / "finished_review_feedback.v1.json"),
        },
        "cuts": cuts,
        "feedback_contract": {
            "review_lanes": ["b_roll", "hero_title", "fullscreen_transition"],
            "component_actions": {
                "b_roll": ["approve", "remove", "replace_asset", "change_type", "move", "comment"],
                "hero_title": ["approve", "remove", "edit_text", "move", "comment"],
                "fullscreen_transition": ["approve", "remove", "edit_text", "move", "comment"],
            },
            "gate_actions": ["request_changes", "approve_cut", "approve_all"],
        },
    }
    manifest = review / "finished_review_manifest_20260817.json"
    manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return manifest


def _write_short_packet(episode: Path, cut_id: str = "KS1") -> Path:
    review = episode / "highlights" / "review" / cut_id
    review.mkdir(parents=True, exist_ok=True)
    preview = review / "短1_preview.mp4"
    preview.write_bytes(b"short-preview" * 40)
    subtitles = review / "subs.srt"
    subtitles.write_text("1\n00:00:00,000 --> 00:00:02,000\n但是後來就發現說\n", encoding="utf-8")
    events = {
        "timeline": "短1 - 你做再多內容，觀眾入口的也只有這一個（緊·導播）",
        "duration_sec": 57.3,
        "preview": preview.name,
        "events": [
            {"type": "card-tier2", "slug": "但是後來就發現說", "t0": 0.0, "t1": 6.87, "note": ""},
            {"type": "video", "slug": "ks1-sushi-craft", "t0": 6.5, "t1": 9.9, "note": "握壽司"},
            {
                "type": "icon_motion",
                "slug": "ks1-sushi-icon",
                "t0": 10.0,
                "t1": 13.2,
                "note": "單一主壽司",
            },
            {"type": "punch-cut", "slug": "", "t0": 25.5, "t1": 30.0, "note": "觀眾收到訊息"},
            {"type": "cut", "slug": "", "t0": 30.93, "t1": 30.93, "note": "換鏡"},
        ],
    }
    (review / "events.json").write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
    return review


@pytest.fixture
def finished_episode(tmp_path):
    episode = tmp_path / "20260721 鄭國威"
    manifest = _write_manifest(episode)
    return tmp_path, episode, manifest


@pytest.fixture
def client(monkeypatch, finished_episode):
    root, _, _ = finished_episode
    monkeypatch.setenv("PODCAST_EPISODES_ROOT", str(root))
    monkeypatch.setenv("WEB_PASSWORD", "gate-password")
    monkeypatch.setenv("WEB_SECRET", "gate-secret")
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.highlight_review as review_module

    importlib.reload(auth_module)
    importlib.reload(review_module)
    monkeypatch.setattr(
        review_module,
        "verify_finished_review_manifest",
        lambda _episode, path: json.loads(path.read_text(encoding="utf-8")),
    )
    app = FastAPI()
    app.include_router(review_module.page_router)
    return TestClient(app)


def _auth_cookie():
    from thousand_sunny.auth import make_token

    return {"nakama_auth": make_token("gate-password")}


def _manifest_sha(manifest: Path) -> str:
    return hashlib.sha256(manifest.read_bytes()).hexdigest()


def _write_final_qa(episode: Path, cut_id: str, *, severity: str | None = None) -> Path:
    path = episode / "highlights" / "qa_final.json"
    payload = {
        "findings": (
            []
            if severity is None
            else [
                {
                    "id": cut_id,
                    "check": "final-cut",
                    "severity": severity,
                    "detail": "fixture finding",
                    "fix": "fixture fix",
                }
            ]
        ),
        "clean": [cut_id] if severity is None else [],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _valid_review(manifest: Path) -> dict[str, object]:
    return {
        "manifest_sha256": _manifest_sha(manifest),
        "submit_action": "save_draft",
        "cut_status__R11": "needs_changes",
        "cut_status__R12": "approved",
        "cut_status__R3": "approved",
        "component_action__R11-broll": "replace_asset",
        "component_comment__R11-broll": "素材太像 generic stock",
        "component_replacement__R11-broll": "換成《將太的壽司》書封或漫畫內頁",
        "component_remember__R11-broll": "on",
        "component_action__R11-hero": "move",
        "component_move__R11-hero": "21.25",
        "cut_feedback__R11": "整體節奏太慢。\n請保留開頭，但壓短中段。",
    }


def test_page_redirects_and_subresources_return_401(client):
    slug = "20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81"
    response = client.get(f"/bridge/highlights/{slug}/finished", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].endswith(f"/{slug}/finished")
    assert client.get(f"/bridge/highlights/{slug}/finished/media/R11").status_code == 401
    assert client.get(f"/bridge/highlights/{slug}/finished/subtitles/R11").status_code == 401


def test_missing_and_invalid_manifest_fail_loud(client, finished_episode):
    _, _, manifest = finished_episode
    manifest.unlink()
    response = client.get(
        "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished", cookies=_auth_cookie()
    )
    assert response.status_code == 404
    manifest.write_text('{"schema":"wrong"}', encoding="utf-8")
    response = client.get(
        "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished", cookies=_auth_cookie()
    )
    assert response.status_code == 422
    assert "manifest" in response.text


def test_chinese_page_only_lists_review_lanes_and_seek_metadata(client):
    response = client.get(
        "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished",
        cookies=_auth_cookie(),
    )
    assert response.status_code == 200
    assert "將太的壽司" in response.text
    assert 'data-component-id="R11-broll"' in response.text
    assert 'data-seek-seconds="10.0"' in response.text
    assert 'data-duration="90.0"' in response.text
    assert "00:00 / 01:30" in response.text
    assert "loadedmetadata" in response.text
    assert 'data-component-id="R11-badge"' not in response.text
    assert 'kind="captions"' in response.text
    assert "B-ROLL" in response.text
    assert "HERO TITLE" in response.text


def test_long_review_context_fails_closed_when_authoritative_verifier_rejects(
    client, monkeypatch
):
    import thousand_sunny.routers.highlight_review as review_module

    def reject(_episode, _manifest):
        raise SystemExit("forged Stock Video labels without receipt")

    monkeypatch.setattr(review_module, "verify_finished_review_manifest", reject)
    response = client.get(
        "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished",
        cookies=_auth_cookie(),
    )
    assert response.status_code == 422
    assert "without receipt" in response.text


def test_long_review_shows_true_stock_video_deficit_and_blocks_approval(
    client, finished_episode
):
    _, _, manifest = finished_episode
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    for component in payload["cuts"][0]["components"]:
        component.pop("asset_category", None)
    manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    page = client.get(
        "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished",
        cookies=_auth_cookie(),
    )
    assert page.status_code == 200
    assert "Stock Video 目前 0 個，還缺 3 個" in page.text
    assert "guest-namecard" in page.text

    form = _valid_review(manifest)
    form.update(
        {
            "submit_action": "approve_cut",
            "selected_cut_id": "R11",
            "cut_status__R11": "approved",
            "cut_status__R12": "pending",
            "cut_status__R3": "pending",
        }
    )
    response = client.post(
        "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished/review",
        data=form,
        cookies=_auth_cookie(),
    )
    assert response.status_code == 409
    assert "Stock Video" in response.text


def test_media_and_vtt_are_served_from_manifest_inventory(client, finished_episode):
    _, episode, _ = finished_episode
    base = "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished"
    response = client.get(f"{base}/media/R11", cookies=_auth_cookie())
    assert response.status_code == 200
    assert response.content == (episode / "highlights/review/R11/R11.mp4").read_bytes()
    assert response.headers["content-type"].startswith("video/mp4")
    response = client.get(f"{base}/subtitles/R11", cookies=_auth_cookie())
    assert response.status_code == 200
    assert response.text.startswith("WEBVTT")
    assert "00:00:01.000 --> 00:00:02.000" in response.text
    assert "修修的字幕" in response.text
    assert client.get(f"{base}/media/not-a-cut", cookies=_auth_cookie()).status_code == 404


def test_media_rejects_containment_and_file_size_mismatch(client, finished_episode, tmp_path):
    _, episode, manifest = finished_episode
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"outside")
    payload["cuts"][0]["artifacts"]["preview"].update(
        {"path": str(outside), "bytes": outside.stat().st_size, "sha256": _sha256(outside)}
    )
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    base = "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished"
    response = client.get(f"{base}/media/R11", cookies=_auth_cookie())
    assert response.status_code == 403

    manifest = _write_manifest(episode)
    preview = episode / "highlights/review/R11/R11.mp4"
    preview.write_bytes(preview.read_bytes() + b"changed")
    response = client.get(f"{base}/media/R11", cookies=_auth_cookie())
    assert response.status_code == 409
    assert "size" in response.text.lower()


def test_valid_feedback_round_trip_is_append_only_and_uses_fixed_path(
    client, finished_episode, tmp_path
):
    _, episode, manifest = finished_episode
    injected = tmp_path / "injected.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["gate"]["feedback_file"] = str(injected)
    manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    form = _valid_review(manifest)
    endpoint = "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished/review"
    response = client.post(endpoint, data=form, cookies=_auth_cookie(), follow_redirects=False)
    assert response.status_code == 303
    assert "saved=1" in response.headers["location"]
    assert not injected.exists()
    feedback_path = episode / "highlights/review/finished_review_feedback.v1.json"
    audit = json.loads(feedback_path.read_text(encoding="utf-8"))
    assert audit["schema"] == "nakama.finished_cut_review_feedback.v1"
    assert len(audit["revisions"]) == 1
    revision = audit["revisions"][0]
    assert revision["manifest_sha256"] == _manifest_sha(manifest)
    assert revision["preview_sha256"]["R11"]
    assert revision["component_feedback"][0]["component_id"] == "R11-broll"
    assert revision["component_feedback"][1]["move_to_seconds"] == 21.25
    assert revision["preference_candidates"][0]["component_id"] == "R11-broll"
    response = client.post(endpoint, data=form, cookies=_auth_cookie(), follow_redirects=False)
    assert response.status_code == 303
    audit = json.loads(feedback_path.read_text(encoding="utf-8"))
    assert [row["revision"] for row in audit["revisions"]] == [1, 2]


def test_save_draft_with_requested_changes_queues_durable_revision_job(
    client, finished_episode
):
    _, episode, manifest = finished_episode

    response = client.post(
        "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished/review",
        data=_valid_review(manifest),
        cookies=_auth_cookie(),
        follow_redirects=False,
    )

    assert response.status_code == 303, response.text
    audit = json.loads(
        (episode / "highlights/review/finished_review_feedback.v1.json").read_text(
            encoding="utf-8"
        )
    )
    revision = audit["revisions"][-1]
    assert revision["overall_feedback"] == {
        "R11": "整體節奏太慢。\n請保留開頭，但壓短中段。"
    }
    assert revision["revision_job"]["status"] == "queued"
    assert revision["revision_job"]["contract"] == "finished-cut-revision-job-v1"
    assert revision["revision_job"]["request_id"].startswith("finished-revision-")
    assert revision["revision_job"]["source_manifest_sha256"] == _manifest_sha(manifest)

    page = client.get(
        "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished",
        cookies=_auth_cookie(),
    )
    assert page.status_code == 200
    assert "Agent revision 已排入佇列" in page.text
    assert revision["revision_job"]["request_id"] in page.text


def test_future_save_draft_auto_attaches_episode_local_trusted_handoff(
    monkeypatch, client, finished_episode, tmp_path
):
    _, episode, manifest = finished_episode
    acquisition = tmp_path / "acquisition"
    acquisition.mkdir()
    sources = {}
    for index in range(3):
        slug = f"route-stock-{index}"
        media = acquisition / f"{slug}.mp4"
        media.write_bytes(f"route-video-{index}".encode())
        sources[slug] = {
            "filename": media.name,
            "bytes": media.stat().st_size,
            "sha256": _sha256(media),
            "provenance": {
                "source_url": f"https://example.test/video/{index}",
                "acquired_at": "2026-08-22T12:00:00+08:00",
                "license_id": f"route-license-{index}",
            },
        }
    source_manifest = acquisition / "trusted_asset_sources.json"
    source_manifest.write_text(json.dumps(sources), encoding="utf-8")
    monkeypatch.setattr(
        "agents.brook.script_video.highlight_broll.probe_stock_video",
        lambda _path: {"duration_seconds": 2.0, "video_streams": [{}]},
    )
    prepared = prepare_trusted_asset_handoff(
        episode, source_manifest, apply=True
    )

    response = client.post(
        "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished/review",
        data=_valid_review(manifest),
        cookies=_auth_cookie(),
        follow_redirects=False,
    )

    assert response.status_code == 303, response.text
    audit = json.loads(
        (episode / "highlights/review/finished_review_feedback.v1.json").read_text(
            encoding="utf-8"
        )
    )
    job = audit["revisions"][-1]["revision_job"]
    assert job["status"] == "queued"
    assert job["trusted_asset_sources"] == sources
    assert job["trusted_asset_sources_sha256"] == prepared["sources_sha256"]
    assert prepared["sources_sha256"] in job["trusted_asset_handoff"]["root"]


def test_failed_revision_job_remains_visible_after_reload(client, finished_episode):
    _, episode, manifest = finished_episode
    endpoint = "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished/review"
    assert (
        client.post(
            endpoint,
            data=_valid_review(manifest),
            cookies=_auth_cookie(),
            follow_redirects=False,
        ).status_code
        == 303
    )
    feedback_path = episode / "highlights/review/finished_review_feedback.v1.json"
    audit = json.loads(feedback_path.read_text(encoding="utf-8"))
    audit["revisions"][-1]["revision_job"].update(
        {"status": "failed", "error": "Hero title render failed"}
    )
    feedback_path.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")

    page = client.get(
        "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished",
        cookies=_auth_cookie(),
    )

    assert page.status_code == 200
    assert 'role="alert"' in page.text
    assert "Agent revision 失敗" in page.text
    assert "Hero title render failed" in page.text


def test_awaiting_stock_assets_is_visible_instead_of_generic_failure(
    client, finished_episode
):
    _, episode, manifest = finished_episode
    endpoint = "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished/review"
    assert (
        client.post(
            endpoint,
            data=_valid_review(manifest),
            cookies=_auth_cookie(),
            follow_redirects=False,
        ).status_code
        == 303
    )
    feedback_path = episode / "highlights/review/finished_review_feedback.v1.json"
    audit = json.loads(feedback_path.read_text(encoding="utf-8"))
    audit["revisions"][-1]["revision_job"]["status"] = "awaiting_stock_assets"
    feedback_path.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")

    page = client.get(
        "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished",
        cookies=_auth_cookie(),
    )

    assert page.status_code == 200
    assert "等待已核准的 Stock Video 素材" in page.text
    assert "Agent revision 失敗" not in page.text


def test_succeeded_revision_job_requests_a_new_human_review(client, finished_episode):
    _, episode, manifest = finished_episode
    endpoint = "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished/review"
    assert (
        client.post(
            endpoint,
            data=_valid_review(manifest),
            cookies=_auth_cookie(),
            follow_redirects=False,
        ).status_code
        == 303
    )
    feedback_path = episode / "highlights/review/finished_review_feedback.v1.json"
    audit = json.loads(feedback_path.read_text(encoding="utf-8"))
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_payload["cuts"][0]["title"] = "Agent revised title"
    manifest.write_text(json.dumps(manifest_payload, ensure_ascii=False), encoding="utf-8")
    audit["revisions"][-1]["revision_job"].update(
        {
            "status": "succeeded",
            "output_manifest_sha256": _manifest_sha(manifest),
        }
    )
    feedback_path.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")

    page = client.get(
        "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished",
        cookies=_auth_cookie(),
    )

    assert page.status_code == 200
    assert "Agent revision 已完成，可以開始 review 新版" in page.text
    assert "修正版已完成，請重新 review" in page.text


def test_text_components_accept_and_reload_multiline_replacement(client, finished_episode):
    _, episode, manifest = finished_episode
    form = _valid_review(manifest)
    form.update(
        {
            "component_action__R11-hero": "edit_text",
            "component_replacement__R11-hero": "分工不是混亂\n人要變通才有價值",
        }
    )
    endpoint = "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished/review"

    response = client.post(endpoint, data=form, cookies=_auth_cookie(), follow_redirects=False)

    assert response.status_code == 303
    audit = json.loads(
        (episode / "highlights/review/finished_review_feedback.v1.json").read_text(
            encoding="utf-8"
        )
    )
    hero = next(
        row
        for row in audit["revisions"][-1]["component_feedback"]
        if row["component_id"] == "R11-hero"
    )
    assert hero["replacement"] == "分工不是混亂\n人要變通才有價值"

    page = client.get(
        "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished?saved=1",
        cookies=_auth_cookie(),
    )
    assert page.status_code == 200
    assert (
        '<textarea name="component_replacement__R11-hero"' in page.text
    )
    assert "分工不是混亂\n人要變通才有價值</textarea>" in page.text
    assert '<input type="text" name="component_replacement__R11-broll"' in page.text


def test_cut_overall_feedback_round_trip_stays_with_its_cut(client, finished_episode):
    _, episode, manifest = finished_episode
    form = _valid_review(manifest)
    form.update(
        {
            "cut_feedback__R11": "整體節奏太平。\nHero title 請保留手動換行。",
            "cut_feedback__R12": "開頭更快進主題。",
        }
    )
    endpoint = "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished/review"

    response = client.post(endpoint, data=form, cookies=_auth_cookie(), follow_redirects=False)

    assert response.status_code == 303
    audit = json.loads(
        (episode / "highlights/review/finished_review_feedback.v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert audit["revisions"][-1]["overall_feedback"] == {
        "R11": "整體節奏太平。\nHero title 請保留手動換行。",
        "R12": "開頭更快進主題。",
    }

    page = client.get(
        "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished?saved=1",
        cookies=_auth_cookie(),
    )
    assert page.status_code == 200
    assert 'name="cut_feedback__R11"' in page.text
    assert "整體節奏太平。\nHero title 請保留手動換行。</textarea>" in page.text
    assert 'name="cut_feedback__R12"' in page.text
    assert "開頭更快進主題。</textarea>" in page.text
    assert 'name="cut_feedback__R3"' in page.text


def test_feedback_rejects_same_size_preview_revision_mismatch(client, finished_episode):
    _, episode, manifest = finished_episode
    preview = episode / "highlights/review/R11/R11.mp4"
    original = preview.read_bytes()
    preview.write_bytes(b"X" + original[1:])

    response = client.post(
        "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished/review",
        data=_valid_review(manifest),
        cookies=_auth_cookie(),
    )

    assert response.status_code == 409
    assert "sha256" in response.text
    assert not (episode / "highlights/review/finished_review_feedback.v1.json").exists()


def test_rejects_stale_manifest_unknown_component_and_unknown_action(client, finished_episode):
    _, _, manifest = finished_episode
    endpoint = "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished/review"
    stale = _valid_review(manifest)
    stale["manifest_sha256"] = "0" * 64
    assert client.post(endpoint, data=stale, cookies=_auth_cookie()).status_code == 409

    unknown = _valid_review(manifest)
    unknown["component_action__not-in-manifest"] = "remove"
    assert client.post(endpoint, data=unknown, cookies=_auth_cookie()).status_code == 400

    unknown_cut_feedback = _valid_review(manifest)
    unknown_cut_feedback["cut_feedback__not-in-manifest"] = "不應被接受"
    assert (
        client.post(endpoint, data=unknown_cut_feedback, cookies=_auth_cookie()).status_code
        == 400
    )

    bad_action = _valid_review(manifest)
    bad_action["component_action__R11-broll"] = "edit_text"
    assert client.post(endpoint, data=bad_action, cookies=_auth_cookie()).status_code == 400


def test_move_range_and_text_limits_are_validated(client, finished_episode):
    _, _, manifest = finished_episode
    endpoint = "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished/review"
    form = _valid_review(manifest)
    form["component_move__R11-hero"] = "999"
    assert client.post(endpoint, data=form, cookies=_auth_cookie()).status_code == 400
    form = _valid_review(manifest)
    form["component_comment__R11-broll"] = "x" * 2001
    assert client.post(endpoint, data=form, cookies=_auth_cookie()).status_code == 400
    form = _valid_review(manifest)
    form["cut_feedback__R11"] = "x" * 5001
    assert client.post(endpoint, data=form, cookies=_auth_cookie()).status_code == 400


def test_approve_all_requires_every_cut_approved(client, finished_episode):
    _, _, manifest = finished_episode
    endpoint = "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished/review"
    form = _valid_review(manifest)
    form["submit_action"] = "approve_all"
    assert client.post(endpoint, data=form, cookies=_auth_cookie()).status_code == 400
    form["cut_status__R11"] = "approved"
    response = client.post(endpoint, data=form, cookies=_auth_cookie(), follow_redirects=False)
    assert response.status_code == 303
    assert "approved=1" in response.headers["location"]


def test_approve_cut_is_terminal_and_hands_off_to_packaging(
    client, finished_episode, monkeypatch
):
    _, episode, manifest = finished_episode
    _write_final_qa(episode, "R11")
    import thousand_sunny.routers.highlight_review as review_module

    started: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        review_module,
        "_find_packaging_episode",
        lambda episode_id, cut_id: "20260721-zhengguowei",
    )
    monkeypatch.setattr(
        review_module,
        "_start_publish_prep",
        lambda episode_dir, cut_id: started.append((episode_dir, cut_id)),
    )
    form = _valid_review(manifest)
    form.update(
        {
            "submit_action": "approve_cut",
            "selected_cut_id": "R11",
            "cut_status__R11": "approved",
            "cut_status__R12": "pending",
            "cut_status__R3": "pending",
        }
    )

    response = client.post(
        "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished/review",
        data=form,
        cookies=_auth_cookie(),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/bridge/packaging/20260721-zhengguowei?cut=R11"
    )
    assert started == [(episode, "R11")]
    audit = json.loads(
        (episode / "highlights/review/finished_review_feedback.v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert audit["revisions"][-1]["decision"] == "approved_cut"
    assert audit["revisions"][-1]["selected_cut_id"] == "R11"


@pytest.mark.parametrize("qa_state", ["missing", "critical"])
def test_approve_cut_fails_closed_without_clear_final_qa(
    client, finished_episode, monkeypatch, qa_state
):
    _, episode, manifest = finished_episode
    import thousand_sunny.routers.highlight_review as review_module

    started: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        review_module,
        "_find_packaging_episode",
        lambda episode_id, cut_id: "20260721-zhengguowei",
    )
    monkeypatch.setattr(
        review_module,
        "_start_publish_prep",
        lambda episode_dir, cut_id: started.append((episode_dir, cut_id)),
    )
    if qa_state == "critical":
        _write_final_qa(episode, "R11", severity="critical")
    form = _valid_review(manifest)
    form.update(
        {
            "submit_action": "approve_cut",
            "selected_cut_id": "R11",
            "cut_status__R11": "approved",
            "cut_status__R12": "pending",
            "cut_status__R3": "pending",
        }
    )

    response = client.post(
        "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished/review",
        data=form,
        cookies=_auth_cookie(),
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert "qa_final" in response.text
    assert started == []
    assert not (episode / "highlights/review/finished_review_feedback.v1.json").exists()


def test_approve_cut_requires_selected_cut_to_be_approved(client, finished_episode):
    _, _, manifest = finished_episode
    form = _valid_review(manifest)
    form.update({"submit_action": "approve_cut", "selected_cut_id": "R11"})

    response = client.post(
        "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished/review",
        data=form,
        cookies=_auth_cookie(),
    )

    assert response.status_code == 400
    assert "selected cut must be approved" in response.text


def test_short_review_view_discovers_packets_and_exposes_short_component_lanes(
    client, finished_episode
):
    _, episode, _ = finished_episode
    packet = _write_short_packet(episode)

    response = client.get(
        "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished?format=short",
        cookies=_auth_cookie(),
    )

    assert response.status_code == 200
    assert "短影片 Review" in response.text
    assert "KS1" in response.text
    assert "字卡與斷句" in response.text
    assert "B-ROLL" in response.text
    assert "ICON／動畫" in response.text
    assert "剪輯節奏" in response.text
    assert 'class="player-shell player-shell--short"' in response.text
    assert ".finished-gate { box-sizing: border-box; width: 100%;" in response.text
    assert 'class="cut-tab__meta sho-mono"' in response.text
    assert ".sho .cut-tab" in response.text
    assert 'class="cut-tab__title"' in response.text
    assert "目前 REVIEW" not in response.text
    assert '.sho .cut-tab[aria-selected="true"]::before' not in response.text
    assert "先播放每支直式初剪" not in response.text
    caption_tag = re.search(r'<track id="review-captions"[^>]*>', response.text)
    assert caption_tag
    assert " default" not in caption_tag.group(0)
    assert "KS1-title-card-001" in response.text
    assert "KS1-b-roll-001" in response.text
    assert "KS1-visual-effect-001" in response.text
    assert "KS1-pacing-001" in response.text
    media = client.get(
        "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished/media/KS1?format=short",
        cookies=_auth_cookie(),
    )
    assert media.status_code == 200
    assert media.content == (packet / "短1_preview.mp4").read_bytes()


def test_short_review_feedback_is_append_only_and_separate_from_long_feedback(
    client, finished_episode
):
    _, episode, _ = finished_episode
    _write_short_packet(episode)
    page = client.get(
        "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished?format=short",
        cookies=_auth_cookie(),
    )
    assert page.status_code == 200, page.text
    manifest_match = re.search(
        r'name="manifest_sha256"\s+value="([0-9a-f]{64})"', page.text
    )
    assert manifest_match, page.text
    manifest_sha = manifest_match.group(1)
    response = client.post(
        "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished/review?format=short",
        data={
            "manifest_sha256": manifest_sha,
            "submit_action": "save_draft",
            "cut_status__KS1": "needs_changes",
            "component_action__KS1-title-card-001": "edit_text",
            "component_replacement__KS1-title-card-001": "把所以移到下一張",
            "component_comment__KS1-title-card-001": "語意斷點不對",
            "component_remember__KS1-title-card-001": "on",
        },
        cookies=_auth_cookie(),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "format=short" in response.headers["location"]
    short_feedback = episode / "highlights/review/short_finished_review_feedback.v1.json"
    assert short_feedback.exists()
    audit = json.loads(short_feedback.read_text(encoding="utf-8"))
    assert audit["revisions"][0]["component_feedback"][0]["lane"] == "title_card"
    assert not (episode / "highlights/review/finished_review_feedback.v1.json").exists()


def test_finished_review_rejects_unknown_format(client):
    response = client.get(
        "/bridge/highlights/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/finished?format=sideways",
        cookies=_auth_cookie(),
    )
    assert response.status_code == 400


def test_lightweight_review_app_mounts_gate_without_full_agent_surfaces(monkeypatch):
    monkeypatch.setenv("NAKAMA_DEV_AUTH_BYPASS", "1")
    import thousand_sunny.review_app as review_app

    paths = set(review_app.app.openapi()["paths"])
    assert "/bridge/highlights/{episode_slug}/finished" in paths
    assert "/bridge/highlights/{episode_slug}/finished/media/{cut_id}" in paths
    assert "/login" in paths
    assert TestClient(review_app.app).get("/healthz").json()["surface"] == "finished-review"
