from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import scripts.build_long_highlight_playback_manifest as playback_manifest
from scripts.build_long_highlight_playback_manifest import (
    PlaybackManifestError,
    build_manifest,
    main,
    stage_cut,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_h264_preview(path: Path, *, duration: float = 2.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s=320x180:d={duration}",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
    )


def _write_video(
    path: Path,
    *,
    codec: str,
    with_audio: bool,
    audio_codec: str = "aac",
    duration: float = 2.0,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s=320x180:d={duration}",
    ]
    if with_audio:
        command.extend(["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"])
    command.extend(["-c:v", codec, "-pix_fmt", "yuv420p"])
    if with_audio:
        command.extend(["-shortest", "-c:a", audio_codec])
    command.append(str(path))
    subprocess.run(command, check=True)


def _write_recipes(episode: Path, cut_id: str) -> None:
    tighten = episode / "highlights" / "tighten"
    tighten.mkdir(parents=True, exist_ok=True)
    (tighten / f"{cut_id}_broll.json").write_text(
        json.dumps(
            {
                "items": [
                    {"kind": "badge", "slug": "brand-badge", "t0": 0.0, "t1": 0.5},
                    {
                        "kind": "camera-correction",
                        "slug": "cam-1",
                        "t0": 0.0,
                        "t1": 1.0,
                    },
                    {
                        "kind": "video",
                        "slug": "student-project",
                        "t0": 0.1,
                        "t1": 0.6,
                        "visual_materialization": {"implementation_kind": "stock_video"},
                    },
                    {
                        "kind": "concept",
                        "slug": "guest-namecard",
                        "comp": "chapter_label",
                        "vars": {"label": "林之晨", "sub": "《逆分工》共同作者"},
                        "t0": 0.6,
                        "t1": 1.0,
                    },
                    {
                        "kind": "concept",
                        "slug": "chapter-agency",
                        "comp": "transition_title",
                        "vars": {"title": "拿回 Agency"},
                        "t0": 1.0,
                        "t1": 1.4,
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tighten / f"{cut_id}_titles.json").write_text(
        json.dumps(
            {
                "titles": [
                    {
                        "text": "天堂就是\n地獄",
                        "t0": 1.4,
                        "t1": 1.8,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_stage_cut_projects_only_reviewable_visual_components(tmp_path: Path) -> None:
    episode = tmp_path / "20260805 林之晨"
    cut_dir = episode / "highlights" / "review" / "value-L02"
    preview = cut_dir / "長2_preview.mp4"
    subtitles = cut_dir / "subs.srt"
    _write_h264_preview(preview)
    subtitles.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n測試字幕\n",
        encoding="utf-8",
    )
    _write_recipes(episode, "value-L02")

    staged_path = stage_cut(
        episode,
        cut_id="value-L02",
        title="白領職涯自救",
        preview_path=preview,
        subtitles_path=subtitles,
    )

    staged = json.loads(staged_path.read_text(encoding="utf-8"))
    assert staged_path == cut_dir / "playback_manifest_cut.v1.json"
    assert staged["cut_id"] == "value-L02"
    assert staged["format"] == "long"
    assert staged["title"] == "白領職涯自救"
    assert staged["artifacts"]["preview"] == {
        "path": str(preview.resolve()),
        "bytes": preview.stat().st_size,
        "sha256": _sha256(preview),
        "duration_seconds": 2.0,
    }
    assert staged["artifacts"]["subtitles"] == {
        "path": str(subtitles.resolve()),
        "bytes": subtitles.stat().st_size,
        "sha256": _sha256(subtitles),
    }
    assert [row["lane"] for row in staged["components"]] == [
        "b_roll",
        "identity_card",
        "fullscreen_transition",
        "hero_title",
    ]
    assert [row["display"] for row in staged["components"]] == [
        "student-project",
        "林之晨｜《逆分工》共同作者",
        "拿回 Agency",
        "天堂就是\n地獄",
    ]
    assert staged["components"][0]["asset_category"] == "stock_video"
    assert all(row["lane"] not in {"badge", "pacing"} for row in staged["components"])


def test_stage_cut_prefers_materialized_orchestrator_recipes(tmp_path: Path) -> None:
    episode = tmp_path / "20260805 林之晨"
    cut_id = "punch-L04"
    cut_dir = episode / "highlights" / "review" / cut_id
    preview = cut_dir / "長3_preview.mp4"
    subtitles = cut_dir / "subs.srt"
    _write_h264_preview(preview)
    subtitles.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n測試字幕\n",
        encoding="utf-8",
    )
    _write_recipes(episode, cut_id)
    recipes = (
        episode / "highlights" / "long-orchestrator-v2" / cut_id / "materialization" / "recipes"
    )
    recipes.mkdir(parents=True)
    (recipes / f"{cut_id}_broll.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "kind": "video",
                        "slug": "labrador-owner-care",
                        "t0": 0.2,
                        "t1": 0.8,
                        "visual_materialization": {"implementation_kind": "stock_video"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (recipes / f"{cut_id}_titles.json").write_text(
        json.dumps({"titles": []}),
        encoding="utf-8",
    )

    staged = json.loads(
        stage_cut(
            episode,
            cut_id=cut_id,
            title="Long 3",
            preview_path=preview,
            subtitles_path=subtitles,
        ).read_text(encoding="utf-8")
    )

    assert [(row["lane"], row["display"]) for row in staged["components"]] == [
        ("b_roll", "labrador-owner-care")
    ]


def test_stage_rejects_non_h264_preview_without_staging(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    cut_dir = episode / "highlights" / "review" / "value-L02"
    preview = cut_dir / "preview.mp4"
    subtitles = cut_dir / "subs.srt"
    _write_video(preview, codec="mpeg4", with_audio=True)
    subtitles.write_text("1\n00:00:00,000 --> 00:00:00,500\n字幕\n", encoding="utf-8")
    _write_recipes(episode, "value-L02")

    with pytest.raises(PlaybackManifestError, match="H.264"):
        stage_cut(
            episode,
            cut_id="value-L02",
            title="Long 2",
            preview_path=preview,
            subtitles_path=subtitles,
        )

    assert not (cut_dir / "playback_manifest_cut.v1.json").exists()


def test_stage_accepts_h264_preview_without_audio(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    cut_dir = episode / "highlights" / "review" / "value-L02"
    preview = cut_dir / "preview.mp4"
    subtitles = cut_dir / "subs.srt"
    _write_video(preview, codec="libx264", with_audio=False)
    subtitles.write_text("1\n00:00:00,000 --> 00:00:00,500\n字幕\n", encoding="utf-8")
    _write_recipes(episode, "value-L02")

    output = stage_cut(
        episode,
        cut_id="value-L02",
        title="Long 2",
        preview_path=preview,
        subtitles_path=subtitles,
    )

    assert output.is_file()


def test_stage_rejects_non_aac_audio_without_staging(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    cut_dir = episode / "highlights" / "review" / "value-L02"
    preview = cut_dir / "preview.mp4"
    subtitles = cut_dir / "subs.srt"
    _write_video(preview, codec="libx264", with_audio=True, audio_codec="mp3")
    subtitles.write_text("1\n00:00:00,000 --> 00:00:00,500\n字幕\n", encoding="utf-8")
    _write_recipes(episode, "value-L02")

    with pytest.raises(PlaybackManifestError, match="AAC or absent"):
        stage_cut(
            episode,
            cut_id="value-L02",
            title="Long 2",
            preview_path=preview,
            subtitles_path=subtitles,
        )

    assert not (cut_dir / "playback_manifest_cut.v1.json").exists()


def test_stage_rejects_preview_outside_cut_review_directory(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    cut_dir = episode / "highlights" / "review" / "value-L02"
    preview = episode / "outside.mp4"
    subtitles = cut_dir / "subs.srt"
    _write_h264_preview(preview)
    subtitles.parent.mkdir(parents=True, exist_ok=True)
    subtitles.write_text("1\n00:00:00,000 --> 00:00:00,500\n字幕\n", encoding="utf-8")
    _write_recipes(episode, "value-L02")

    with pytest.raises(PlaybackManifestError, match="preview must be inside"):
        stage_cut(
            episode,
            cut_id="value-L02",
            title="Long 2",
            preview_path=preview,
            subtitles_path=subtitles,
        )


def test_stage_rejects_non_utf8_subtitles(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    cut_dir = episode / "highlights" / "review" / "value-L02"
    preview = cut_dir / "preview.mp4"
    subtitles = cut_dir / "subs.srt"
    _write_h264_preview(preview)
    subtitles.write_bytes(b"\xff\xfe\xfa")
    _write_recipes(episode, "value-L02")

    with pytest.raises(PlaybackManifestError, match="UTF-8"):
        stage_cut(
            episode,
            cut_id="value-L02",
            title="Long 2",
            preview_path=preview,
            subtitles_path=subtitles,
        )


def test_final_preview_and_subtitles_are_each_hashed_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    episode = tmp_path / "episode"
    cut_dir = episode / "highlights" / "review" / "value-L02"
    preview = cut_dir / "preview.mp4"
    subtitles = cut_dir / "subs.srt"
    _write_h264_preview(preview)
    subtitles.write_text("1\n00:00:00,000 --> 00:00:00,500\n字幕\n", encoding="utf-8")
    _write_recipes(episode, "value-L02")
    hashed: list[Path] = []
    real_sha256 = playback_manifest._sha256

    def record_hash(path: Path) -> str:
        hashed.append(path)
        return real_sha256(path)

    monkeypatch.setattr(playback_manifest, "_sha256", record_hash)
    stage_cut(
        episode,
        cut_id="value-L02",
        title="Long 2",
        preview_path=preview,
        subtitles_path=subtitles,
    )
    build_manifest(episode, cut_ids=["value-L02"])

    assert hashed == [preview.resolve(), subtitles.resolve()]


def test_build_preserves_existing_cut_and_merges_staged_cuts_idempotently(
    tmp_path: Path,
) -> None:
    episode = tmp_path / "20260805 林之晨"
    review = episode / "highlights" / "review"
    review.mkdir(parents=True)
    original_l1 = {
        "cut_id": "value-L01",
        "format": "long",
        "title": "原本 Long 1",
        "artifacts": {
            "preview": {
                "path": str(review / "value-L01" / "preview.mp4"),
                "bytes": 111,
                "sha256": "1" * 64,
                "duration_seconds": 9.0,
            },
            "subtitles": {
                "path": str(review / "value-L01" / "subs.srt"),
                "bytes": 22,
                "sha256": "2" * 64,
            },
        },
        "components": [],
    }
    existing = {
        "schema": "nakama.finished_cut_review_manifest.v1",
        "episode_id": episode.name,
        "stage": 5,
        "gate": {
            "kind": "finished_cut_review",
            "status": "ready_for_review",
            "feedback_file": str(review / "finished_review_feedback.v1.json"),
        },
        "cuts": [original_l1],
        "feedback_contract": {
            "review_lanes": [
                "b_roll",
                "identity_card",
                "hero_title",
                "fullscreen_transition",
                "visual_effect",
            ],
            "component_actions": {
                "b_roll": ["approve"],
                "identity_card": ["approve"],
                "hero_title": ["approve"],
                "fullscreen_transition": ["approve"],
                "visual_effect": ["approve"],
            },
            "gate_actions": ["request_changes", "approve_cut", "approve_all"],
        },
        "inventory_scope": {
            "mode": "partial_editorial_master_migration",
            "included_cut_ids": ["value-L01"],
            "pending_cut_ids": ["value-L02", "punch-L04"],
        },
    }
    current = review / "finished_review_manifest_current.json"
    current.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
    for cut_id in ("value-L02", "punch-L04"):
        cut_dir = review / cut_id
        preview = cut_dir / f"{cut_id}_preview.mp4"
        subtitles = cut_dir / "subs.srt"
        _write_h264_preview(preview)
        subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\n測試字幕\n", encoding="utf-8")
        _write_recipes(episode, cut_id)
        stage_cut(
            episode,
            cut_id=cut_id,
            title=f"主題 {cut_id}",
            preview_path=preview,
            subtitles_path=subtitles,
        )

    output = build_manifest(episode, cut_ids=["value-L02", "punch-L04"])
    first_bytes = output.read_bytes()
    merged = json.loads(first_bytes.decode("utf-8"))

    assert output == current
    assert [cut["cut_id"] for cut in merged["cuts"]] == [
        "value-L01",
        "value-L02",
        "punch-L04",
    ]
    assert merged["cuts"][0] == original_l1
    assert merged["inventory_scope"] == {
        "mode": "partial_editorial_master_migration",
        "included_cut_ids": ["punch-L04", "value-L01", "value-L02"],
        "pending_cut_ids": [],
    }

    assert build_manifest(episode, cut_ids=["value-L02", "punch-L04"]) == output
    assert output.read_bytes() == first_bytes


def test_build_rejects_any_invalid_staged_cut_without_writing_manifest(
    tmp_path: Path,
) -> None:
    episode = tmp_path / "20260805 林之晨"
    review = episode / "highlights" / "review"
    current = review / "finished_review_manifest_current.json"
    review.mkdir(parents=True)
    original = b'{"sentinel":"must stay byte-identical"}\n'
    current.write_bytes(original)
    for cut_id in ("value-L02", "punch-L04"):
        cut_dir = review / cut_id
        preview = cut_dir / "preview.mp4"
        subtitles = cut_dir / "subs.srt"
        _write_h264_preview(preview)
        subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\n測試字幕\n", encoding="utf-8")
        _write_recipes(episode, cut_id)
        stage_cut(
            episode,
            cut_id=cut_id,
            title=cut_id,
            preview_path=preview,
            subtitles_path=subtitles,
        )
    invalid_path = review / "punch-L04" / "playback_manifest_cut.v1.json"
    invalid = json.loads(invalid_path.read_text(encoding="utf-8"))
    invalid["components"][0]["t1"] = 99.0
    invalid_path.write_text(json.dumps(invalid), encoding="utf-8")

    with pytest.raises(PlaybackManifestError, match="timeline range"):
        build_manifest(episode, cut_ids=["value-L02", "punch-L04"])

    assert current.read_bytes() == original


def test_cli_stage_then_build_creates_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    episode = tmp_path / "20260805 林之晨"
    cut_id = "value-L02"
    cut_dir = episode / "highlights" / "review" / cut_id
    preview = cut_dir / "preview.mp4"
    subtitles = cut_dir / "subs.srt"
    _write_h264_preview(preview)
    subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\n測試字幕\n", encoding="utf-8")
    _write_recipes(episode, cut_id)

    assert (
        main(
            [
                "stage",
                str(episode),
                "--cut-id",
                cut_id,
                "--title",
                "Long 2",
                "--preview",
                str(preview),
                "--subtitles",
                str(subtitles),
            ]
        )
        == 0
    )
    assert main(["build", str(episode), "--cut-id", cut_id]) == 0
    assert capsys.readouterr().out.isascii()
    manifest = json.loads(
        (episode / "highlights" / "review" / "finished_review_manifest_current.json").read_text(
            encoding="utf-8"
        )
    )
    assert [cut["cut_id"] for cut in manifest["cuts"]] == [cut_id]


def test_built_manifest_serves_media_subtitles_and_timeline_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "episodes"
    episode = root / "20260805 林之晨"
    cut_id = "value-L02"
    cut_dir = episode / "highlights" / "review" / cut_id
    preview = cut_dir / "preview.mp4"
    subtitles = cut_dir / "subs.srt"
    _write_h264_preview(preview)
    subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\n測試字幕\n", encoding="utf-8")
    _write_recipes(episode, cut_id)
    stage_cut(
        episode,
        cut_id=cut_id,
        title="Long 2",
        preview_path=preview,
        subtitles_path=subtitles,
    )
    build_manifest(episode, cut_ids=[cut_id])
    monkeypatch.setenv("PODCAST_EPISODES_ROOT", str(root))
    monkeypatch.setenv("WEB_PASSWORD", "gate-password")
    monkeypatch.setenv("WEB_SECRET", "gate-secret")
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.highlight_review as review_module

    importlib.reload(auth_module)
    importlib.reload(review_module)
    app = FastAPI()
    app.include_router(review_module.page_router)
    client = TestClient(app)
    cookie = {"nakama_auth": auth_module.make_token("gate-password")}
    client.cookies.update(cookie)
    base = f"/bridge/highlights/{episode.name}/finished"

    media = client.get(f"{base}/media/{cut_id}")
    captions = client.get(f"{base}/subtitles/{cut_id}")
    page = client.get(base)

    assert media.status_code == 200
    assert media.content == preview.read_bytes()
    assert captions.status_code == 200
    assert "WEBVTT" in captions.text
    assert "測試字幕" in captions.text
    assert page.status_code == 200
    assert "LEGACY MANIFEST V1" in page.text
    assert "核准功能已鎖定" in page.text
    assert 'data-timeline-lane="b_roll"' in page.text
    assert 'data-timeline-count="1"' in page.text
    assert 'data-timeline-lane="badge"' not in page.text
    assert 'data-timeline-lane="pacing"' not in page.text
    assert "student-project" in page.text
    assert "天堂就是\n地獄" in page.text
