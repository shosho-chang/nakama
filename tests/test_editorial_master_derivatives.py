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

    master, identity = _selection(tmp_path)
    _candidate_files(tmp_path, lineage=identity)
    monkeypatch.setattr(producer, "_open_master", lambda episode: master)
    asset_dir = tmp_path / "assets" / "broll"
    asset_dir.mkdir(parents=True)
    (asset_dir / "factory.mp4").write_bytes(b"asset")
    cut_dir = tmp_path / "highlights" / "review" / "value-L01"
    cut_dir.mkdir(parents=True)
    (cut_dir / "preview.mp4").write_bytes(b"preview")
    (cut_dir / "subs.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\n字幕\n", encoding="utf-8")
    packet = {
        "editorial_master_lineage": identity,
        "timeline": "長1 - Master cut（緊·導播）",
        "duration_sec": 60.0,
        "preview": "preview.mp4",
        "events": [
            {"type": "video", "slug": "factory", "t0": 1.0, "t1": 4.0},
            {"type": "guest-namecard", "slug": "guest", "t0": 5.0, "t1": 8.0},
            {"type": "card-tier2", "slug": "重點", "t0": 9.0, "t1": 12.0},
            {"type": "badge", "slug": "brand", "t0": 12.0, "t1": 20.0},
            {"type": "transition", "slug": "chapter", "t0": 20.0, "t1": 23.0},
        ],
    }
    (cut_dir / "events.json").write_text(json.dumps(packet), encoding="utf-8")

    output = producer.build_manifest(tmp_path)
    first = output.read_bytes()
    producer.build_manifest(tmp_path)
    assert output.read_bytes() == first
    payload = json.loads(first)
    cut = payload["cuts"][0]
    assert payload["editorial_master_lineage"] == identity
    assert cut["visual_treatment_counts"]["b_roll"] == 1
    assert cut["visual_treatment_counts"]["identity_card"] == 1
    assert cut["visual_treatment_counts"]["hero_title"] == 1
    assert cut["visual_treatment_counts"]["badge"] == 1
    assert cut["visual_treatment_counts"]["fullscreen_transition"] == 1
    broll = next(item for item in cut["components"] if item["lane"] == "b_roll")
    assert broll["asset"]["sha256"] == hashlib.sha256(b"asset").hexdigest()
    assert cut["artifacts"]["events"]["sha256"] == hashlib.sha256(
        (cut_dir / "events.json").read_bytes()
    ).hexdigest()


def test_long_review_packet_refreshes_contract_manifest():
    import run_short_review

    source = inspect.getsource(run_short_review.build_packet)
    assert "_open_editorial_master(episode_dir)" in source
    assert "_load_winner(episode_dir, cid, master_identity)" in source
    assert "_verify_materialization_receipt" in source
    assert 'c.get("format") == "long"' in source
    assert 'build_manifest(episode_dir, review_format="long")' in source


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

    master, identity = _selection(tmp_path)
    _candidate_files(tmp_path, lineage=identity)
    monkeypatch.setattr(producer, "_open_master", lambda episode: master)
    review = tmp_path / "highlights" / "review"
    for cut_id in ("value-L01", "unknown-L99"):
        cut_dir = review / cut_id
        cut_dir.mkdir(parents=True)
        (cut_dir / "preview.mp4").write_bytes(b"preview")
        (cut_dir / "subs.srt").write_text("subs", encoding="utf-8")
        (cut_dir / "events.json").write_text(
            json.dumps(
                {
                    "editorial_master_lineage": master.identity(),
                    "duration_sec": 10,
                    "preview": "preview.mp4",
                    "events": [{"type": "card-tier2", "slug": "title", "t0": 1, "t1": 2}],
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
