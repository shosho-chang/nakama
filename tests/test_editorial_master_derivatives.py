"""Editorial Master fail-closed seams for tighten/director/review inventory."""

from __future__ import annotations

import hashlib
import inspect
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def _candidate_files(
    episode: Path,
    *,
    cut_id: str = "value-L01",
    lineage: dict | None = None,
) -> None:
    highlights = episode / "highlights"
    highlights.mkdir(parents=True, exist_ok=True)
    (highlights / "candidates.json").write_text(
        json.dumps(
            {
                "editorial_master_lineage": lineage,
                "candidates": [
                    {
                        "id": cut_id,
                        "format": "long",
                        "t_start": 10.0,
                        "t_end": 20.0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (highlights / "winners.json").write_text(
        json.dumps(
            {
                "editorial_master_lineage": lineage,
                "winners": [
                    {
                        "id": cut_id,
                        "rank": 1,
                        "title": "Master cut",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def _selection(episode: Path):
    master_dir = episode / "editorial-master" / "v1"
    master_dir.mkdir(parents=True, exist_ok=True)
    media = master_dir / "master.mp4"
    media.write_bytes(b"approved-program")
    srt = master_dir / "master.srt"
    srt.write_text("1\n00:00:10,000 --> 00:00:20,000\n正式母版\n", encoding="utf-8")
    identity = {
        "contract": "podcast-editorial-master-v1",
        "episode_id": episode.name,
        "content_hash": "a" * 64,
        "master_media_sha256": hashlib.sha256(media.read_bytes()).hexdigest(),
        "master_srt_sha256": hashlib.sha256(srt.read_bytes()).hexdigest(),
    }
    return SimpleNamespace(media_path=media, srt_path=srt, identity=lambda: identity), identity


def _authoritative_broll_receipt_fixture(episode: Path, cut_id: str, items: list[dict]) -> dict:
    rows = []
    for item in items:
        asset = episode / "assets" / "broll" / f"{item['slug']}.mp4"
        rows.append(
            {
                "slug": item["slug"],
                "t0": item["t0"],
                "t1": item["t1"],
                "asset": {
                    "path": asset.relative_to(episode).as_posix(),
                    "bytes": asset.stat().st_size,
                    "sha256": hashlib.sha256(asset.read_bytes()).hexdigest(),
                },
            }
        )
    return {
        "contract": "podcast-long-highlight-stock-video-v2",
        "cut_id": cut_id,
        "content_hash": "f" * 64,
        "stock_video_count": len(rows),
        "stock_videos": rows,
        "visual_pipeline_lineage": {
            "revision_id": "fixture-r1",
            "content_hash": "e" * 64,
            "current_pointer": {
                "path": "highlights/visual-pipeline/value-L01/CURRENT.json",
                "bytes": 101,
                "sha256": "1" * 64,
                "revision_id": "fixture-r1",
            },
            "work_packet": {"path": "WORK.json", "sha256": "2" * 64},
            "director_plan": {"path": "DIRECTOR.json", "sha256": "3" * 64},
            "dp_fulfillment": {"path": "DP.json", "sha256": "4" * 64},
            "semantic_audit": {"path": "AUDIT.json", "sha256": "5" * 64},
        },
    }


def _live_master_timeline(name: str, uid: str, media_path: Path):
    media_pool_item = SimpleNamespace(
        GetClipProperty=lambda key: str(media_path) if key == "File Path" else ""
    )
    timeline_item = SimpleNamespace(GetMediaPoolItem=lambda: media_pool_item)
    return SimpleNamespace(
        GetName=lambda: name,
        GetUniqueId=lambda: uid,
        GetItemListInTrack=lambda track_type, index: (
            [timeline_item] if track_type in {"video", "audio"} and index == 1 else []
        ),
    )


def test_tighten_detect_reads_only_master_clock_even_when_raw_exists(tmp_path, monkeypatch):
    import run_short_tighten as tighten

    master, identity = _selection(tmp_path)
    _candidate_files(tmp_path, lineage=identity)
    (tmp_path / "normalized.wav").write_bytes(b"raw-audio-must-not-be-opened")
    (tmp_path / "Default_program.mp4").write_bytes(b"raw-video-must-not-be-opened")
    seen = []
    monkeypatch.setattr(tighten, "_open_editorial_master", lambda episode: master)
    monkeypatch.setattr(
        tighten,
        "_detect_silences",
        lambda audio, *_args: seen.append(audio) or [(12.0, 13.2)],
    )

    result = tighten.detect(tmp_path, "value-L01")

    assert seen == [master.media_path]
    payload = json.loads(Path(result["file"]).read_text(encoding="utf-8"))
    assert payload["editorial_master_lineage"] == identity
    assert "subtitle_lineage" not in payload


def test_master_retime_cannot_resurrect_removed_cough(tmp_path, monkeypatch):
    import run_short_tighten as tighten

    master, _ = _selection(tmp_path)
    (tmp_path / "transcript.srt").write_text(
        "1\n00:00:10,000 --> 00:00:20,000\n咳嗽 抱歉\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(tighten, "_tight_pause_map", lambda *_args, **_kwargs: None)

    output, _ = tighten._retime_srt(
        tmp_path,
        "value-L01",
        [(10.0, 20.0)],
        [],
        transcript=master.srt_path,
        source_media=master.media_path,
        allow_legacy_words=False,
    )

    rendered = output.read_text(encoding="utf-8")
    assert "正式母版" in rendered
    assert "咳嗽" not in rendered and "抱歉" not in rendered


def test_director_production_function_has_no_raw_camera_or_audio_path():
    import run_short_director as director

    source = inspect.getsource(director.direct)
    assert "_word_speakers" not in source
    assert "normalized.wav" not in source
    assert 'episode_dir / "Video"' not in source
    assert "cam_items" not in source
    assert "master.media_path" in source


@pytest.mark.parametrize(
    ("module_name", "function_name"),
    [
        ("run_short_tighten", "apply"),
        ("run_short_director", "direct"),
    ],
)
def test_short_materializers_use_fail_loud_append_for_audio(module_name, function_name):
    """A truthy Resolve ``[None]`` result must never count as audio materialization."""

    module = __import__(module_name)
    source = inspect.getsource(getattr(module, function_name))
    assert "append_checked(" in source
    assert "Master audio" in source


def test_fail_loud_append_rejects_truthy_none_result():
    from shared.resolve_append import append_checked

    media_pool = SimpleNamespace(AppendToTimeline=lambda _specs: [None])
    with pytest.raises(SystemExit, match="上軌失敗"):
        append_checked(media_pool, [{"mediaType": 2}], "Master audio", retries=1, delay=0)


def test_media_pool_same_name_wrong_path_is_rejected(tmp_path):
    import run_short_tighten as tighten

    wanted = tmp_path / "editorial-master" / "v1" / "master.mp4"
    wanted.parent.mkdir(parents=True)
    wanted.write_bytes(b"master")

    class WrongClip:
        def GetName(self):
            return "master.mp4"

        def GetClipProperty(self, name):
            assert name == "File Path"
            return str(tmp_path / "raw" / "master.mp4")

    root = SimpleNamespace(GetClipList=lambda: [WrongClip()])
    mp = SimpleNamespace(
        ImportMedia=lambda paths: (_ for _ in ()).throw(
            AssertionError("must not import over collision")
        )
    )
    with pytest.raises(SystemExit, match="同名素材冒充"):
        tighten._verified_master_media_pool_item(mp, root, wanted)


def test_candidate_and_winner_lineage_are_both_fresh(tmp_path):
    import run_short_tighten as tighten

    lineage = {"content_hash": "a" * 64}
    _candidate_files(tmp_path, lineage=lineage)
    assert tighten._load_winner(tmp_path, "value-L01", lineage)[0]["id"] == "value-L01"
    winners = tmp_path / "highlights" / "winners.json"
    payload = json.loads(winners.read_text(encoding="utf-8"))
    payload["editorial_master_lineage"] = {"content_hash": "b" * 64}
    winners.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SystemExit, match="已過期"):
        tighten._load_winner(tmp_path, "value-L01", lineage)


def test_finished_manifest_is_deterministic_and_classifies_visual_truth(tmp_path, monkeypatch):
    import build_finished_review_manifest as producer

    monkeypatch.setattr(
        "agents.brook.script_video.highlight_broll.probe_stock_video",
        lambda _path: {
            "duration_seconds": 5.0,
            "video_streams": [
                {"index": 0, "codec_name": "h264", "width": 16, "height": 16}
            ],
        },
    )
    master, identity = _selection(tmp_path)
    _candidate_files(tmp_path, lineage=identity)
    monkeypatch.setattr(producer, "_open_master", lambda episode: master)
    asset_dir = tmp_path / "assets" / "broll"
    asset_dir.mkdir(parents=True)
    for index in range(3):
        (asset_dir / f"factory-{index}.mp4").write_bytes(f"asset-{index}".encode())
    broll_items = [
        {
            "kind": "video",
            "slug": f"factory-{index}",
            "t0": 1.0 + index * 10,
            "t1": 4.0 + index * 10,
            "provenance": {
                "source_url": f"https://stock.example.test/factory-{index}",
                "license_id": f"license-{index}",
                "acquired_at": "2026-08-22T10:00:00+08:00",
            },
        }
        for index in range(3)
    ]
    tighten = tmp_path / "highlights" / "tighten"
    tighten.mkdir(parents=True)
    review_items = [
        *broll_items,
        {
            "kind": "concept",
            "slug": "guest-namecard",
            "comp": "chapter_label",
            "name": "林之晨",
            "title": "《逆分工》共同作者",
            "t0": 5.0,
            "t1": 8.0,
        },
        *[
            {
                "kind": "concept",
                "slug": slug,
                "comp": "transition_title",
                "vars": {"kicker": f"{index:02d}", "title": title},
                "t0": t0,
                "t1": t0 + 3.0,
            }
            for index, (slug, title, t0) in enumerate(
                (
                    ("tr1-intelligence", "兩種智慧", 20.0),
                    ("tr2-create", "先做作品，再補知識", 24.0),
                    ("tr3-play", "玩，本來就是學習", 28.0),
                    ("tr4-adults", "真正落後的是大人", 36.0),
                ),
                start=1,
            )
        ],
    ]
    (tighten / "value-L01_broll.json").write_text(
        json.dumps({"items": review_items}, ensure_ascii=False), encoding="utf-8"
    )
    (tighten / "value-L01_titles.json").write_text(
        json.dumps(
            {
                "titles": [
                    {"text": "重點", "tier": 2, "t0": 9.0, "t1": 12.0},
                    {"text": "沒有任何\n保證了", "tier": 1, "t0": 31.0, "t1": 34.0},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tighten / "value-L01_camera_plan.json").write_text(
        json.dumps(
            {
                "contract": "podcast-highlight-camera-plan-v1",
                "cut_id": "value-L01",
                "format": "long",
                "timebase": "cut-local",
                "shots": [
                    {
                        "t0": 0.0,
                        "t1": 20.933,
                        "camera": "host",
                        "reason": "主持人提出開場問題",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    from agents.brook.script_video.highlight_broll import receipt_identity

    broll_receipt = _authoritative_broll_receipt_fixture(
        tmp_path, "value-L01", broll_items
    )
    monkeypatch.setattr(producer, "verify_broll_receipt", lambda *_args, **_kwargs: broll_receipt)
    cut_dir = tmp_path / "highlights" / "review" / "value-L01"
    cut_dir.mkdir(parents=True)
    (cut_dir / "preview.mp4").write_bytes(b"preview")
    (cut_dir / "subs.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\n字幕\n", encoding="utf-8")
    import run_short_review as review_packet

    packet = {
        "editorial_master_lineage": identity,
        "stock_video_lineage": receipt_identity(broll_receipt),
        "timeline": "長1 - Master cut（緊·導播）",
        "duration_sec": 60.0,
        "preview": "preview.mp4",
        "events": [
            *review_packet._load_events(tmp_path, "value-L01"),
            {"type": "badge", "slug": "brand", "t0": 12.0, "t1": 20.0},
        ],
    }
    (cut_dir / "events.json").write_text(json.dumps(packet), encoding="utf-8")
    legacy_source = cut_dir.parent / "finished_review_manifest_20260822.json"
    legacy_source.write_text(
        json.dumps(
            {
                "schema": "nakama.finished_cut_review_manifest.v1",
                "episode_id": tmp_path.name,
                "stage": 5,
                "gate": {
                    "kind": "finished_cut_review",
                    "status": "ready_for_review",
                },
                "cuts": [
                    {
                        "cut_id": "value-L01",
                        "components": [
                            {
                                "component_id": "value-L01-hero-001",
                                "lane": "hero_title",
                                "t0": 9.0,
                                "t1": 12.0,
                                "text": "重點",
                            },
                            {
                                "component_id": "value-L01-hero-002",
                                "lane": "hero_title",
                                "t0": 31.0,
                                "t1": 34.0,
                                "text": "沒有任何\n保證了",
                            },
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    legacy_source_bytes = legacy_source.read_bytes()

    output = producer.build_manifest(tmp_path)
    first = output.read_bytes()
    producer.build_manifest(tmp_path)
    assert output.read_bytes() == first
    payload = json.loads(first)
    assert payload["schema"] == producer.SCHEMA
    assert legacy_source.read_bytes() == legacy_source_bytes
    cut = payload["cuts"][0]
    assert payload["editorial_master_lineage"] == identity
    assert cut["stock_video_lineage"] == receipt_identity(broll_receipt)
    assert cut["visual_pipeline_lineage"] == broll_receipt["visual_pipeline_lineage"]
    assert cut["visual_pipeline_lineage"]["revision_id"] == "fixture-r1"
    assert cut["visual_pipeline_lineage"]["current_pointer"]["sha256"] == "1" * 64
    assert cut["visual_treatment_counts"]["b_roll"] == 3
    assert cut["stock_video_count"] == 3
    assert cut["visual_treatment_counts"]["identity_card"] == 1
    assert cut["visual_treatment_counts"]["hero_title"] == 2
    assert cut["visual_treatment_counts"]["badge"] == 1
    assert cut["visual_treatment_counts"]["fullscreen_transition"] == 4
    assert cut["visual_treatment_counts"]["visual_effect"] == 0
    assert cut["visual_treatment_counts"]["pacing"] == 1
    transition = next(
        item
        for item in cut["components"]
        if item["lane"] == "fullscreen_transition"
        and item["display"] == "真正落後的是大人"
    )
    assert transition["component"] == "transition_title"
    assert transition["display"] == "真正落後的是大人"
    identity = next(item for item in cut["components"] if item["lane"] == "identity_card")
    assert identity["type"] == "concept"
    assert identity["component"] == "chapter_label"
    assert identity["review_lane"] == "identity_card"
    assert identity["display"] == "林之晨｜《逆分工》共同作者"
    camera = next(item for item in cut["components"] if item["lane"] == "pacing")
    assert camera["display"] == "機位：主持人"
    assert (camera["t0"], camera["t1"]) == (0.0, 20.933)
    broll = next(item for item in cut["components"] if item["lane"] == "b_roll")
    assert broll["asset"]["sha256"] == hashlib.sha256(b"asset-0").hexdigest()
    assert broll["asset_category"] == "stock_video"
    assert cut["artifacts"]["events"]["sha256"] == hashlib.sha256(
        (cut_dir / "events.json").read_bytes()
    ).hexdigest()
    hero_ids = [
        row["component_id"] for row in cut["components"] if row["lane"] == "hero_title"
    ]
    assert hero_ids == ["value-L01-hero-001", "value-L01-hero-002"]
    identity_registry_path = cut_dir.parent / "finished_review_component_identity.v2.json"
    identity_registry = json.loads(
        identity_registry_path.read_text(
            encoding="utf-8"
        )
    )
    assert identity_registry["source_manifest"]["filename"] == (
        "finished_review_manifest_20260822.json"
    )
    assert [
        row["component_id"]
        for row in identity_registry["cuts"]["value-L01"]
        if row["lane"] == "hero_title"
    ] == ["value-L01-hero-001", "value-L01-hero-002"]

    verified = producer.verify_finished_review_cut(
        tmp_path, "value-L01", output, feedback_rows=[]
    )
    assert verified["stock_video_count"] == 3
    assert verified["manifest_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    with pytest.raises(SystemExit, match="replacement 未 exact 套用"):
        producer.verify_finished_review_cut(
            tmp_path,
            "value-L01",
            output,
            feedback_rows=[
                {
                    "cut_id": "value-L01",
                    "component_id": "value-L01-hero-001",
                    "action": "edit_text",
                    "replacement": "新的\n兩行 Hero",
                }
            ],
        )

    edited_hero = next(
        event
        for event in packet["events"]
        if event.get("review_lane") == "hero_title" and event.get("t0") == 9.0
    )
    edited_hero["slug"] = "新的/兩行 Hero"
    edited_hero["text"] = "新的\n兩行 Hero"
    edited_hero["display"] = "新的\n兩行 Hero"
    (cut_dir / "events.json").write_text(json.dumps(packet), encoding="utf-8")
    producer.build_manifest(
        tmp_path,
        identity_transition={
            "request_id": "finished-revision-edit-hero",
            "source_manifest_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "source_registry_sha256": json.loads(
                identity_registry_path.read_text(encoding="utf-8")
            )["content_hash"],
            "feedback_rows": [
                {
                    "cut_id": "value-L01",
                    "component_id": "value-L01-hero-001",
                    "action": "edit_text",
                    "replacement": "新的\n兩行 Hero",
                }
            ],
        },
    )
    edited = producer.verify_finished_review_cut(
        tmp_path,
        "value-L01",
        output,
        feedback_rows=[
            {
                "cut_id": "value-L01",
                "component_id": "value-L01-hero-001",
                "action": "edit_text",
                "replacement": "新的\n兩行 Hero",
            }
        ],
    )
    assert edited["approved"] is False
    with pytest.raises(SystemExit, match="preview 沒有改變"):
        producer.verify_finished_review_cut(
            tmp_path,
            "value-L01",
            output,
            source_preview_sha256=edited["preview_sha256"],
            require_preview_change=True,
        )
    rereview = producer.verify_finished_review_cut(
        tmp_path,
        "value-L01",
        output,
        source_preview_sha256="0" * 64,
        require_preview_change=True,
    )
    assert rereview["status"] == "verified_for_human_rereview"
    assert rereview["approved"] is False

    old_broll_ids = {
        row["component_id"]
        for row in json.loads(output.read_text(encoding="utf-8"))["cuts"][0]["components"]
        if row["lane"] == "b_roll"
    }
    source_manifest_sha256 = hashlib.sha256(output.read_bytes()).hexdigest()
    source_registry_sha256 = json.loads(
        identity_registry_path.read_text(encoding="utf-8")
    )["content_hash"]
    replacement_items = []
    stock_events = [
        event for event in packet["events"] if event.get("review_lane") == "b_roll"
    ]
    for index in range(3):
        slug = f"replacement-{index}"
        (asset_dir / f"{slug}.mp4").write_bytes(f"replacement-{index}".encode())
        replacement_items.append(
            {
                "kind": "video",
                "slug": slug,
                "t0": 1.0 + index * 10,
                "t1": 4.0 + index * 10,
                "provenance": {
                    "source_url": f"https://stock.example.test/{slug}",
                    "license_id": f"license-{slug}",
                    "acquired_at": "2026-08-22T10:00:00+08:00",
                },
            }
        )
        stock_events[index]["slug"] = slug
    (tighten / "value-L01_broll.json").write_text(
        json.dumps({"items": replacement_items}), encoding="utf-8"
    )
    broll_receipt = _authoritative_broll_receipt_fixture(
        tmp_path, "value-L01", replacement_items
    )
    packet["stock_video_lineage"] = receipt_identity(broll_receipt)
    (cut_dir / "events.json").write_text(json.dumps(packet), encoding="utf-8")

    transition = {
        "request_id": "finished-revision-remove-old-stock",
        "source_manifest_sha256": source_manifest_sha256,
        "source_registry_sha256": source_registry_sha256,
        "feedback_rows": [
            {
                "cut_id": "value-L01",
                "component_id": component_id,
                "action": "remove",
            }
            for component_id in sorted(old_broll_ids)
        ],
    }
    producer.build_manifest(tmp_path, identity_transition=transition)
    producer.verify_finished_review_cut(
        tmp_path,
        "value-L01",
        output,
        feedback_rows=transition["feedback_rows"],
        identity_transition=transition,
    )
    replaced = json.loads(output.read_text(encoding="utf-8"))["cuts"][0]
    new_broll_ids = {
        row["component_id"] for row in replaced["components"] if row["lane"] == "b_roll"
    }
    assert old_broll_ids.isdisjoint(new_broll_ids)
    assert new_broll_ids == {
        "value-L01-b-roll-004",
        "value-L01-b-roll-005",
        "value-L01-b-roll-006",
    }
    first_replacement = output.read_bytes()
    producer.build_manifest(tmp_path, identity_transition=transition)
    assert output.read_bytes() == first_replacement
    modified_replay = {
        **transition,
        "feedback_rows": [
            *transition["feedback_rows"],
            {
                "cut_id": "value-L01",
                "component_id": "value-L01-hero-002",
                "action": "comment",
            },
        ],
    }
    with pytest.raises(SystemExit, match="不可重複使用 tombstone"):
        producer.build_manifest(tmp_path, identity_transition=modified_replay)

    second_source_manifest_sha256 = hashlib.sha256(output.read_bytes()).hexdigest()
    second_source_registry_sha256 = json.loads(
        identity_registry_path.read_text(encoding="utf-8")
    )["content_hash"]
    second_items = []
    for index in range(3):
        slug = f"second-replacement-{index}"
        (asset_dir / f"{slug}.mp4").write_bytes(slug.encode())
        second_items.append(
            {
                **replacement_items[index],
                "slug": slug,
                "provenance": {
                    **replacement_items[index]["provenance"],
                    "source_url": f"https://stock.example.test/{slug}",
                },
            }
        )
        stock_events[index]["slug"] = slug
    (tighten / "value-L01_broll.json").write_text(
        json.dumps({"items": second_items}), encoding="utf-8"
    )
    broll_receipt = _authoritative_broll_receipt_fixture(
        tmp_path, "value-L01", second_items
    )
    packet["stock_video_lineage"] = receipt_identity(broll_receipt)
    (cut_dir / "events.json").write_text(json.dumps(packet), encoding="utf-8")
    second_transition = {
        "request_id": "finished-revision-remove-second-stock",
        "source_manifest_sha256": second_source_manifest_sha256,
        "source_registry_sha256": second_source_registry_sha256,
        "feedback_rows": [
            {"cut_id": "value-L01", "component_id": value, "action": "remove"}
            for value in sorted(new_broll_ids)
        ],
    }
    producer.build_manifest(tmp_path, identity_transition=second_transition)
    third_ids = {
        row["component_id"]
        for row in json.loads(output.read_text(encoding="utf-8"))["cuts"][0]["components"]
        if row["lane"] == "b_roll"
    }
    assert third_ids == {
        "value-L01-b-roll-007",
        "value-L01-b-roll-008",
        "value-L01-b-roll-009",
    }


def test_finished_manifest_rejects_arbitrary_legacy_component_id_remap(tmp_path):
    import build_finished_review_manifest as producer

    review = tmp_path / "highlights" / "review"
    review.mkdir(parents=True)
    (review / "finished_review_manifest_20260822.json").write_text(
        json.dumps(
            {
                "schema": "nakama.finished_cut_review_manifest.v1",
                "episode_id": tmp_path.name,
                "stage": 5,
                "gate": {"kind": "finished_cut_review", "status": "ready_for_review"},
                "cuts": [
                    {
                        "cut_id": "value-L01",
                        "components": [
                            {
                                "component_id": "value-L01-agent-invented-999",
                                "lane": "hero_title",
                                "t0": 101.82,
                                "t1": 103.9,
                                "text": "與其去教\n三國的故事",
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="不接受任意 remap"):
        producer.build_manifest(tmp_path)


def test_authoritative_verifier_rejects_forged_stock_labels_without_receipt(
    tmp_path, monkeypatch
):
    import build_finished_review_manifest as producer

    master, identity = _selection(tmp_path)
    _candidate_files(tmp_path, lineage=identity)
    monkeypatch.setattr(producer, "_open_master", lambda episode: master)
    assets = tmp_path / "assets" / "broll"
    assets.mkdir(parents=True)
    events = []
    for index in range(3):
        (assets / f"fake-{index}.mp4").write_bytes(f"fake-{index}".encode())
        events.append(
            {
                "type": "video",
                "asset_category": "stock_video",
                "slug": f"fake-{index}",
                "t0": 1 + index * 2,
                "t1": 2 + index * 2,
            }
        )
    cut_dir = tmp_path / "highlights" / "review" / "value-L01"
    cut_dir.mkdir(parents=True)
    (cut_dir / "preview.mp4").write_bytes(b"preview")
    (cut_dir / "subs.srt").write_text("subs", encoding="utf-8")
    (cut_dir / "events.json").write_text(
        json.dumps(
            {
                "editorial_master_lineage": identity,
                "stock_video_lineage": {
                    "contract": "podcast-long-highlight-stock-video-v1",
                    "cut_id": "value-L01",
                    "content_hash": "f" * 64,
                    "stock_video_count": 3,
                },
                "duration_sec": 10,
                "preview": "preview.mp4",
                "events": events,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="materialization receipt"):
        producer.build_manifest(tmp_path)


def test_long_review_packet_refreshes_contract_manifest():
    import run_short_review

    source = inspect.getsource(run_short_review.build_packet)
    assert "_open_editorial_master(episode_dir)" in source
    assert "_load_winner(episode_dir, cid, master_identity)" in source
    assert "_verify_materialization_receipt" in source
    assert 'c.get("format") == "long"' in source
    assert "finished_manifest_cut_ids" in source
    assert "cut_ids=finished_manifest_cut_ids" in source


def test_partial_manifest_identity_bootstrap_never_verifies_unrequested_stale_cut(
    tmp_path, monkeypatch
):
    import build_finished_review_manifest as producer

    calls = []

    def scoped_payload(
        _episode,
        *,
        review_format="long",
        identity_registry=None,
        cut_ids=None,
        identity_transition=None,
    ):
        calls.append(cut_ids)
        if cut_ids is None:
            raise SystemExit("unrequested punch-L04 events.json Editorial Master lineage stale")
        return {
            "schema": producer.SCHEMA,
            "episode_id": tmp_path.name,
            "stage": 5,
            "gate": {},
            "editorial_master_lineage": {"content_hash": "a" * 64},
            "cuts": [{"cut_id": "value-L01", "components": []}],
            "feedback_contract": {},
            "inventory_scope": {
                "mode": "partial_editorial_master_migration",
                "included_cut_ids": ["value-L01"],
                "pending_cut_ids": ["punch-L04"],
            },
        }

    monkeypatch.setattr(producer, "_manifest_payload", scoped_payload)
    (tmp_path / "highlights" / "review").mkdir(parents=True)

    output = producer.build_manifest(tmp_path, cut_ids={"value-L01"})

    assert output.is_file()
    assert calls == [{"value-L01"}, {"value-L01"}]
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["inventory_scope"]["pending_cut_ids"] == ["punch-L04"]


def test_finished_manifest_refuses_unbacked_broll(tmp_path, monkeypatch):
    import build_finished_review_manifest as producer

    master, _ = _selection(tmp_path)
    _candidate_files(tmp_path, lineage=master.identity())
    monkeypatch.setattr(producer, "_open_master", lambda episode: master)
    cut_dir = tmp_path / "highlights" / "review" / "value-L01"
    cut_dir.mkdir(parents=True)
    (cut_dir / "preview.mp4").write_bytes(b"preview")
    (cut_dir / "subs.srt").write_text("subs", encoding="utf-8")
    (cut_dir / "events.json").write_text(
        json.dumps(
            {
                "editorial_master_lineage": master.identity(),
                "duration_sec": 10,
                "preview": "preview.mp4",
                "events": [{"type": "video", "slug": "missing", "t0": 1, "t1": 2}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="asset-backed B-roll"):
        producer.build_manifest(tmp_path)


def test_finished_manifest_rejects_broll_slug_path_escape(tmp_path, monkeypatch):
    import build_finished_review_manifest as producer

    master, identity = _selection(tmp_path)
    _candidate_files(tmp_path, lineage=identity)
    monkeypatch.setattr(producer, "_open_master", lambda episode: master)
    cut_dir = tmp_path / "highlights" / "review" / "value-L01"
    cut_dir.mkdir(parents=True)
    (cut_dir / "preview.mp4").write_bytes(b"preview")
    (cut_dir / "subs.srt").write_text("subs", encoding="utf-8")
    (cut_dir / "events.json").write_text(
        json.dumps(
            {
                "editorial_master_lineage": master.identity(),
                "duration_sec": 10,
                "preview": "preview.mp4",
                "events": [{"type": "video", "slug": "../secret", "t0": 1, "t1": 2}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="slug"):
        producer.build_manifest(tmp_path)


def test_finished_manifest_ignores_unknown_review_packet(tmp_path, monkeypatch):
    import build_finished_review_manifest as producer

    monkeypatch.setattr(
        "agents.brook.script_video.highlight_broll.probe_stock_video",
        lambda _path: {
            "duration_seconds": 5.0,
            "video_streams": [
                {"index": 0, "codec_name": "h264", "width": 16, "height": 16}
            ],
        },
    )
    master, identity = _selection(tmp_path)
    _candidate_files(tmp_path, lineage=identity)
    monkeypatch.setattr(producer, "_open_master", lambda episode: master)
    review = tmp_path / "highlights" / "review"
    assets = tmp_path / "assets" / "broll"
    assets.mkdir(parents=True)
    for index in range(3):
        (assets / f"stock-{index}.mp4").write_bytes(f"stock-{index}".encode())
    broll_items = [
        {
            "kind": "video",
            "slug": f"stock-{index}",
            "t0": 2 + index * 2,
            "t1": 4 + index * 2,
            "provenance": {
                "source_url": f"https://stock.example.test/stock-{index}",
                "license_id": f"license-{index}",
                "acquired_at": "2026-08-22T10:00:00+08:00",
            },
        }
        for index in range(3)
    ]
    tighten = tmp_path / "highlights" / "tighten"
    tighten.mkdir(parents=True)
    (tighten / "value-L01_broll.json").write_text(
        json.dumps({"items": broll_items}), encoding="utf-8"
    )
    from agents.brook.script_video.highlight_broll import receipt_identity

    broll_receipt = _authoritative_broll_receipt_fixture(
        tmp_path, "value-L01", broll_items
    )
    monkeypatch.setattr(producer, "verify_broll_receipt", lambda *_args, **_kwargs: broll_receipt)
    for cut_id in ("value-L01", "unknown-L99"):
        cut_dir = review / cut_id
        cut_dir.mkdir(parents=True)
        (cut_dir / "preview.mp4").write_bytes(b"preview")
        (cut_dir / "subs.srt").write_text("subs", encoding="utf-8")
        (cut_dir / "events.json").write_text(
            json.dumps(
                {
                    "editorial_master_lineage": master.identity(),
                    "stock_video_lineage": receipt_identity(broll_receipt),
                    "duration_sec": 10,
                    "preview": "preview.mp4",
                    "events": [
                        {"type": "card-tier2", "slug": "title", "t0": 1, "t1": 2},
                        {"type": "video", "slug": "stock-0", "t0": 2, "t1": 4},
                        {"type": "video", "slug": "stock-1", "t0": 4, "t1": 6},
                        {"type": "video", "slug": "stock-2", "t0": 6, "t1": 8},
                    ],
                }
            ),
            encoding="utf-8",
        )
    output = producer.build_manifest(tmp_path)
    assert [cut["cut_id"] for cut in json.loads(output.read_bytes())["cuts"]] == ["value-L01"]


def test_finished_manifest_rejects_preview_path_traversal(tmp_path, monkeypatch):
    import build_finished_review_manifest as producer

    master, identity = _selection(tmp_path)
    _candidate_files(tmp_path, lineage=identity)
    monkeypatch.setattr(producer, "_open_master", lambda episode: master)
    cut_dir = tmp_path / "highlights" / "review" / "value-L01"
    cut_dir.mkdir(parents=True)
    (cut_dir / "subs.srt").write_text("subs", encoding="utf-8")
    (tmp_path / "Default_program.mp4").write_bytes(b"raw")
    (cut_dir / "events.json").write_text(
        json.dumps(
            {
                "duration_sec": 10,
                "preview": "../../../Default_program.mp4",
                "editorial_master_lineage": identity,
                "events": [{"type": "card-tier2", "slug": "title", "t0": 1, "t1": 2}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="preview"):
        producer.build_manifest(tmp_path)


def test_finished_manifest_rejects_packet_lineage_drift(tmp_path, monkeypatch):
    import build_finished_review_manifest as producer

    master, identity = _selection(tmp_path)
    _candidate_files(tmp_path, lineage=identity)
    monkeypatch.setattr(producer, "_open_master", lambda episode: master)
    cut_dir = tmp_path / "highlights" / "review" / "value-L01"
    cut_dir.mkdir(parents=True)
    (cut_dir / "preview.mp4").write_bytes(b"preview")
    (cut_dir / "subs.srt").write_text("subs", encoding="utf-8")
    (cut_dir / "events.json").write_text(
        json.dumps(
            {
                "duration_sec": 10,
                "preview": "preview.mp4",
                "editorial_master_lineage": {"content_hash": "b" * 64},
                "events": [{"type": "card-tier2", "slug": "title", "t0": 1, "t1": 2}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="lineage"):
        producer.build_manifest(tmp_path)


@pytest.mark.parametrize("slug", ["*", "foo?", "[abc]"])
def test_finished_manifest_rejects_broll_glob_metacharacters(tmp_path, monkeypatch, slug):
    import build_finished_review_manifest as producer

    master, identity = _selection(tmp_path)
    _candidate_files(tmp_path, lineage=identity)
    monkeypatch.setattr(producer, "_open_master", lambda episode: master)
    assets = tmp_path / "assets" / "broll"
    assets.mkdir(parents=True)
    (assets / "foo.mp4").write_bytes(b"asset")
    cut_dir = tmp_path / "highlights" / "review" / "value-L01"
    cut_dir.mkdir(parents=True)
    (cut_dir / "preview.mp4").write_bytes(b"preview")
    (cut_dir / "subs.srt").write_text("subs", encoding="utf-8")
    (cut_dir / "events.json").write_text(
        json.dumps(
            {
                "duration_sec": 10,
                "preview": "preview.mp4",
                "editorial_master_lineage": identity,
                "events": [{"type": "video", "slug": slug, "t0": 1, "t1": 2}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="glob"):
        producer.build_manifest(tmp_path)


def test_review_rejects_same_name_timeline_with_stale_uid(tmp_path, monkeypatch):
    import run_short_review

    from shared.highlight_materialization import (
        HighlightSource,
        build_materialization_receipt,
        write_materialization_receipt,
    )

    master, identity = _selection(tmp_path)
    timeline_name = "長1 - Master（緊·導播）"
    old_timeline = _live_master_timeline(timeline_name, "old-uid", master.media_path)
    source = HighlightSource(
        srt_path=master.srt_path,
        media_path=master.media_path,
        lineage=identity,
    )
    receipt = build_materialization_receipt(
        tmp_path,
        cut_id="value-L01",
        cut_format="long",
        timeline=old_timeline,
        source_range={"start_sec": 10, "end_sec": 20, "start_frame": 300, "end_frame": 600},
        source=source,
    )
    write_materialization_receipt(tmp_path, receipt)
    monkeypatch.setattr(run_short_review, "_open_editorial_master", lambda episode: master)
    timeline = _live_master_timeline(timeline_name, "new-uid", master.media_path)

    with pytest.raises(SystemExit, match="timeline"):
        run_short_review._verify_materialization_receipt(
            tmp_path,
            "value-L01",
            timeline,
            identity,
            cut_format="long",
            t_start=10.0,
            t_end=20.0,
            fps=30.0,
        )


def test_review_rejects_receipt_after_candidate_range_changes(tmp_path, monkeypatch):
    import run_short_review

    from shared.highlight_materialization import (
        HighlightSource,
        build_materialization_receipt,
        write_materialization_receipt,
    )

    master, identity = _selection(tmp_path)
    timeline = _live_master_timeline("長1 - Master（緊·導播）", "director-uid", master.media_path)
    receipt = build_materialization_receipt(
        tmp_path,
        cut_id="value-L01",
        cut_format="long",
        timeline=timeline,
        source_range={"start_sec": 10, "end_sec": 20, "start_frame": 300, "end_frame": 600},
        source=HighlightSource(
            srt_path=master.srt_path,
            media_path=master.media_path,
            lineage=identity,
        ),
    )
    write_materialization_receipt(tmp_path, receipt)
    monkeypatch.setattr(run_short_review, "_open_editorial_master", lambda episode: master)

    with pytest.raises(SystemExit, match="source range"):
        run_short_review._verify_materialization_receipt(
            tmp_path,
            "value-L01",
            timeline,
            identity,
            cut_format="long",
            t_start=10.0,
            t_end=21.0,
            fps=30.0,
        )


def test_review_rejects_same_timeline_identity_when_live_aroll_is_raw(tmp_path, monkeypatch):
    import run_short_review

    from shared.highlight_materialization import (
        HighlightSource,
        build_materialization_receipt,
        write_materialization_receipt,
    )

    master, identity = _selection(tmp_path)
    name = "長1 - Master（緊·導播）"
    receipt_timeline = _live_master_timeline(name, "director-uid", master.media_path)
    receipt = build_materialization_receipt(
        tmp_path,
        cut_id="value-L01",
        cut_format="long",
        timeline=receipt_timeline,
        source_range={"start_sec": 10, "end_sec": 20, "start_frame": 300, "end_frame": 600},
        source=HighlightSource(
            srt_path=master.srt_path,
            media_path=master.media_path,
            lineage=identity,
        ),
    )
    write_materialization_receipt(tmp_path, receipt)
    raw = tmp_path / "Default_program.mp4"
    raw.write_bytes(b"raw-program")
    replaced_live_timeline = _live_master_timeline(name, "director-uid", raw)
    monkeypatch.setattr(run_short_review, "_open_editorial_master", lambda episode: master)

    with pytest.raises(SystemExit, match="not exact master media"):
        run_short_review._verify_materialization_receipt(
            tmp_path,
            "value-L01",
            replaced_live_timeline,
            identity,
            cut_format="long",
            t_start=10.0,
            t_end=20.0,
            fps=30.0,
        )


def test_tighten_writer_to_review_verifier_cross_contract(tmp_path, monkeypatch):
    import run_short_review
    import run_short_tighten

    master, identity = _selection(tmp_path)
    timeline = _live_master_timeline(
        "長1 - Master（緊·導播）", "director-uid", master.media_path
    )
    path = run_short_tighten._commit_materialization_receipt(
        tmp_path,
        cid="value-L01",
        cut_format="long",
        timeline=timeline,
        t0=10.0,
        t1=20.0,
        fps=30.0,
        master=master,
    )
    first = path.read_bytes()
    # Exact rerun is byte-idempotent.
    assert (
        run_short_tighten._commit_materialization_receipt(
            tmp_path,
            cid="value-L01",
            cut_format="long",
            timeline=timeline,
            t0=10.0,
            t1=20.0,
            fps=30.0,
            master=master,
        ).read_bytes()
        == first
    )
    monkeypatch.setattr(run_short_review, "_open_editorial_master", lambda episode: master)
    verified = run_short_review._verify_materialization_receipt(
        tmp_path,
        "value-L01",
        timeline,
        identity,
        cut_format="long",
        t_start=10.0,
        t_end=20.0,
        fps=30.0,
    )
    assert verified["timeline"] == {
        "name": "長1 - Master（緊·導播）",
        "uid": "director-uid",
    }
    assert verified["editorial_master_lineage"] == identity
