"""HTTP contract for the v3-only Stage 5 Finished Cut review gate."""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agents.brook.script_video.finished_cut_production import (
    ArtifactView,
    ComponentView,
    CutView,
    EventView,
    FinishedCutInspection,
)


class _FakeInspector:
    def __init__(self, inspection: FinishedCutInspection) -> None:
        self.inspection = inspection
        self.calls: list[str] = []

    def inspect_current(self, episode_id: str) -> FinishedCutInspection:
        self.calls.append(episode_id)
        return self.inspection


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ready_inspection(episode: Path) -> tuple[FinishedCutInspection, Path]:
    review = episode / "highlights" / "review"
    cut_dir = review / "value-L03"
    cut_dir.mkdir(parents=True)
    preview = cut_dir / "value-L03.mp4"
    preview.write_bytes(b"v3-preview" * 40)
    subtitle = cut_dir / "subs.srt"
    subtitle.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\n下一個黃金年代是什麼？\n",
        encoding="utf-8",
    )
    current = review / "finished_review_manifest_current.json"
    current.write_text(
        json.dumps(
            {
                "schema": "nakama.finished_cut_review_manifest.v3",
                "episode_id": episode.name,
                "releases": [{"release_id": "release-L03"}],
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    event = EventView(
        event_id="event-hero",
        master_cue_ids=("cue-101", "cue-102"),
        text="下一個黃金年代是什麼？",
        text_hash="a" * 64,
        t0=45.0,
        t1=90.0,
        section_id="section-3",
        intent="把抽象問題收斂成可理解的提問",
        display="下一個黃金年代是什麼？",
        semantic_kind="thesis",
        implementation_kind="hero_title",
        lane="hero_title",
        asset_ref="asset-sha256:" + "b" * 64,
        visual_status="approved",
        intentional_aroll=False,
    )
    hero = ComponentView(
        component_id="component-hero",
        event_id=event.event_id,
        semantic_kind=event.semantic_kind,
        implementation_kind="hero_title",
        lane="hero_title",
        display="下一個黃金年代是什麼？",
        t0=61.0,
        t1=65.0,
        asset_ref=event.asset_ref,
    )
    stock_components = tuple(
        ComponentView(
            component_id=f"component-stock-{index}",
            event_id=f"event-stock-{index}",
            semantic_kind="b_roll",
            implementation_kind="stock_video",
            lane="b_roll",
            display=f"橫式工作場景 {index}",
            t0=10.0 * index,
            t1=10.0 * index + 4.0,
            asset_ref=f"asset-sha256:{str(index) * 64}",
        )
        for index in range(1, 4)
    )
    cut = CutView(
        release_id="release-L03",
        cut_id="value-L03",
        format="long",
        preview=ArtifactView(
            reference=preview.relative_to(episode).as_posix(),
            bytes=preview.stat().st_size,
            sha256=_sha(preview),
            duration_sec=90.0,
            probe=(("video_codec", "h264"),),
        ),
        subtitle=ArtifactView(
            reference=subtitle.relative_to(episode).as_posix(),
            bytes=subtitle.stat().st_size,
            sha256=_sha(subtitle),
        ),
        events=(event,),
        components=(hero, *stock_components),
    )
    return (
        FinishedCutInspection(episode_id=episode.name, state="ready", cuts=(cut,)),
        current,
    )


def _client(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    inspection: FinishedCutInspection,
) -> tuple[TestClient, _FakeInspector, object]:
    monkeypatch.setenv("PODCAST_EPISODES_ROOT", str(root))
    monkeypatch.setenv("WEB_PASSWORD", "gate-password")
    monkeypatch.setenv("WEB_SECRET", "gate-secret")

    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.highlight_review as review_module

    importlib.reload(auth_module)
    importlib.reload(review_module)
    inspector = _FakeInspector(inspection)
    monkeypatch.setattr(
        review_module,
        "_CURRENT_RELEASE_INSPECTOR_FACTORY",
        lambda _episode_dir: inspector,
    )
    app = FastAPI()
    app.include_router(review_module.page_router)
    return TestClient(app), inspector, review_module


def _auth_cookie() -> dict[str, str]:
    from thousand_sunny.auth import make_token

    return {"nakama_auth": make_token("gate-password")}


def test_missing_current_is_404_and_never_falls_back_to_historical_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode = tmp_path / "20260805 林之晨"
    review = episode / "highlights" / "review"
    review.mkdir(parents=True)
    (review / "finished_review_manifest_20260822.json").write_text(
        '{"schema":"nakama.finished_cut_review_manifest.v2"}',
        encoding="utf-8",
    )
    inspection = FinishedCutInspection(
        episode_id=episode.name,
        state="missing",
        error_code="current_release_missing",
    )
    client, inspector, _ = _client(monkeypatch, tmp_path, inspection)

    response = client.get(
        f"/bridge/highlights/{episode.name}/finished",
        cookies=_auth_cookie(),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "finished cut current Release is missing"
    assert inspector.calls == [episode.name]


def test_production_inspector_itself_ignores_historical_manifests(tmp_path: Path) -> None:
    episode = tmp_path / "20260805 林之晨"
    review = episode / "highlights" / "review"
    review.mkdir(parents=True)
    (review / "finished_review_manifest_20260822.json").write_text(
        '{"schema":"nakama.finished_cut_review_manifest.v2"}',
        encoding="utf-8",
    )
    from agents.brook.script_video.finished_cut_production import build_current_release_reader

    inspection = build_current_release_reader(episode).inspect_current(episode.name)

    assert inspection.state == "missing"
    assert inspection.error_code == "current_release_missing"


def test_invalid_current_is_explicit_and_not_rendered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode = tmp_path / "20260805 林之晨"
    episode.mkdir()
    inspection = FinishedCutInspection(
        episode_id=episode.name,
        state="invalid",
        error_code="current_release_invalid",
    )
    client, _, _ = _client(monkeypatch, tmp_path, inspection)

    response = client.get(
        f"/bridge/highlights/{episode.name}/finished",
        cookies=_auth_cookie(),
    )

    assert response.status_code == 422
    assert "current_release_invalid" in response.json()["detail"]


def test_v3_board_projects_release_events_component_ranges_and_does_not_preload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode = tmp_path / "20260805 林之晨"
    inspection, _ = _ready_inspection(episode)
    client, inspector, _ = _client(monkeypatch, tmp_path, inspection)

    response = client.get(
        f"/bridge/highlights/{episode.name}/finished",
        cookies=_auth_cookie(),
    )

    assert response.status_code == 200
    assert "FINISHED CUT RELEASE V3" in response.text
    assert "release-L03" in response.text
    assert "下一個黃金年代是什麼？" in response.text
    assert "61.0" in response.text
    assert 'id="review-player" controls preload="none"' in response.text
    assert inspector.calls == [episode.name]


def test_v3_preview_and_subtitle_routes_use_release_artifact_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode = tmp_path / "20260805 林之晨"
    inspection, _ = _ready_inspection(episode)
    client, _, _ = _client(monkeypatch, tmp_path, inspection)

    media = client.get(
        f"/bridge/highlights/{episode.name}/finished/media/value-L03",
        cookies=_auth_cookie(),
    )
    subtitles = client.get(
        f"/bridge/highlights/{episode.name}/finished/subtitles/value-L03",
        cookies=_auth_cookie(),
    )

    assert media.status_code == 200
    assert media.content == b"v3-preview" * 40
    assert subtitles.status_code == 200
    assert "WEBVTT" in subtitles.text
    assert "下一個黃金年代是什麼？" in subtitles.text


def test_save_writes_event_scoped_v3_revision_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode = tmp_path / "20260805 林之晨"
    inspection, current = _ready_inspection(episode)
    client, _, _ = _client(monkeypatch, tmp_path, inspection)
    manifest_sha256 = hashlib.sha256(current.read_bytes()).hexdigest()

    response = client.post(
        f"/bridge/highlights/{episode.name}/finished/review",
        cookies=_auth_cookie(),
        data={
            "manifest_sha256": manifest_sha256,
            "submit_action": "save_draft",
            "selected_cut_id": "value-L03",
            "cut_status__value-L03": "needs_changes",
            "component_action__component-hero": "edit_text",
            "component_replacement__component-hero": "下一個黃金年代，究竟屬於誰？",
            "component_comment__component-hero": "原句缺少主體",
            "cut_feedback__value-L03": "Hero title 必須精準，不要重跑整支。",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    feedback_path = episode / "highlights" / "review" / "finished_review_feedback.v3.json"
    payload = json.loads(feedback_path.read_text(encoding="utf-8"))
    assert payload["schema"] == "nakama.finished_cut_review_feedback.v3"
    job = payload["revisions"][0]["revision_jobs"][0]
    assert job["contract"] == "finished-cut-production-revision.v3"
    assert job["release_id"] == "release-L03"
    assert job["event_id"] == "event-hero"
    assert job["status"] == "queued"
    assert job["command_id"] is None
    assert "不要重跑整支" in job["feedback"]
    assert "manifest_filename" not in job
    assert "requested_cut_ids" not in job
    assert "trusted_asset_sources" not in job


def test_finished_review_callers_contain_no_retired_production_imports() -> None:
    root = Path(__file__).resolve().parents[1]
    sources = "\n".join(
        (root / relative).read_text(encoding="utf-8")
        for relative in (
            "thousand_sunny/routers/highlight_review.py",
            "scripts/finished_review_watcher.py",
        )
    )
    forbidden = (
        "agents.brook.script_video.highlight_visual_pipeline",
        "visual_pipeline_status",
        "verify_visual_pipeline",
        "scripts.podcast_highlight_visual_orchestrator",
        "run_visual_pipeline",
        "finished_review_manifest_*.json",
    )
    assert all(value not in sources for value in forbidden)


# --- 移動時間用時間碼填寫（修修 2026-08-29） ------------------------------


def test_move_time_accepts_a_clock_reading() -> None:
    """修修原話：「像 1 分 49 秒，我就要自己換算成 109 秒」。"""
    from thousand_sunny.routers.highlight_review import parse_move_seconds

    assert parse_move_seconds("1:49") == 109.0
    assert parse_move_seconds("01:49") == 109.0
    assert parse_move_seconds("1:02:03") == 3723.0
    assert parse_move_seconds("0:05.5") == 5.5


def test_move_time_still_accepts_bare_seconds() -> None:
    """既有的填法不能壞——舊草稿裡存的就是秒數。"""
    from thousand_sunny.routers.highlight_review import parse_move_seconds

    assert parse_move_seconds("109") == 109.0
    assert parse_move_seconds("109.0") == 109.0


def test_move_time_rejects_a_reading_that_is_not_a_clock() -> None:
    from thousand_sunny.routers.highlight_review import parse_move_seconds

    for raw in ("1:75", "1:02:99", "", "  ", "abc", "1:2:3:4"):
        with pytest.raises(ValueError):
            parse_move_seconds(raw)


def test_saved_move_is_shown_back_as_a_clock_reading() -> None:
    """存進去是秒數，回填要是人看得懂的樣子，不然下次還是要心算。"""
    from thousand_sunny.routers.highlight_review import format_move_seconds

    assert format_move_seconds(109.0) == "01:49"
    assert format_move_seconds(3723.0) == "1:02:03"
    assert format_move_seconds(5.5) == "00:05.5"
    assert format_move_seconds(None) == ""


def test_move_time_round_trips() -> None:
    from thousand_sunny.routers.highlight_review import (
        format_move_seconds,
        parse_move_seconds,
    )

    for seconds in (0.0, 5.5, 109.0, 492.31, 3723.0):
        assert parse_move_seconds(format_move_seconds(seconds)) == pytest.approx(seconds)


# --- Short finished review（main #1175 帶進來的第二個審核面）-------------------
# 這一段測的是 run_short_review packet 那條路；長片走上面的 v3 current Release。


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
                "slug": "sushi-closeup",
                "note": "對齊將太的壽司",
                "t0": 10.0,
                "t1": 14.0,
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
                "title": title,
                "artifacts": {
                    "preview": {
                        "path": str(preview),
                        "bytes": preview.stat().st_size,
                        "sha256": _sha(preview),
                        "duration_seconds": 90.0,
                    },
                    "subtitles": {
                        "path": str(subtitles),
                        "bytes": subtitles.stat().st_size,
                        "sha256": _sha(subtitles),
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
    app = FastAPI()
    app.include_router(review_module.page_router)
    return TestClient(app)


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
    manifest_match = re.search(r'name="manifest_sha256"\s+value="([0-9a-f]{64})"', page.text)
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
