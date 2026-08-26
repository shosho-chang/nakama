"""run_short_broll behavior and pure-function tests."""

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from run_short_broll import _data_uri, _fill_zoom, _guest_namecard_job  # noqa: E402


@pytest.fixture(autouse=True)
def _stub_stock_video_probe(monkeypatch):
    monkeypatch.setattr(
        "agents.brook.script_video.highlight_broll.probe_stock_video",
        lambda _path: {
            "duration_seconds": 5.0,
            "video_streams": [
                {"index": 0, "codec_name": "h264", "width": 16, "height": 16}
            ],
        },
    )


def _master_selection(episode: Path):
    master_dir = episode / "editorial-master" / "v1"
    master_dir.mkdir(parents=True)
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


def _write_broll_inputs(episode: Path, lineage: dict, *, winner_lineage: dict | None = None):
    highlights = episode / "highlights"
    tighten = highlights / "tighten"
    tighten.mkdir(parents=True)
    candidate = {
        "id": "value-L01",
        "format": "long",
        "t_start": 10.0,
        "t_end": 20.0,
        "title": "Master cut",
    }
    (highlights / "candidates.json").write_text(
        json.dumps({"editorial_master_lineage": lineage, "candidates": [candidate]}),
        encoding="utf-8",
    )
    (highlights / "winners.json").write_text(
        json.dumps(
            {
                "editorial_master_lineage": winner_lineage or lineage,
                "winners": [{"id": "value-L01", "rank": 1, "title": "Master cut"}],
            }
        ),
        encoding="utf-8",
    )
    assets = episode / "assets" / "broll"
    assets.mkdir(parents=True)
    items = []
    for index in range(3):
        slug = f"stock-{index}"
        (assets / f"{slug}.mp4").write_bytes(f"asset-{index}".encode())
        items.append(
            {
                "kind": "video",
                "slug": slug,
                "t0": 1.0 + index * 2,
                "t1": 2.0 + index * 2,
                "provenance": {
                    "source_url": f"https://stock.example.test/{slug}",
                    "license_id": f"license-{index}",
                    "acquired_at": "2026-08-22T10:00:00+08:00",
                },
            }
        )
    (tighten / "value-L01_broll.json").write_text(
        json.dumps({"items": items}), encoding="utf-8"
    )


def _projection(
    *,
    materialization_id: str = "visual-001-s01",
    target_lane: str = "broll_track2",
    implementation_kind: str = "stock_video",
    on_screen_text=None,
    render_spec=None,
):
    return {
        "materialization_id": materialization_id,
        "event_id": "visual-001",
        "director_intent_sha256": "a" * 64,
        "target_lane": target_lane,
        "implementation_kind": implementation_kind,
        "mode": "stock" if implementation_kind == "stock_video" else "hyperframes",
        "cue_ids": [1],
        "t0": 1.0,
        "t1": 2.0,
        "source_range": {"start_sec": 0.25, "end_sec": 1.25},
        "quote": "正式母版",
        "on_screen_text": on_screen_text,
        "media": {"path": "assets/broll/stock-0.mp4", "bytes": 7, "sha256": "b" * 64},
        "provenance": {"authority": "test"},
        "render_spec": render_spec,
    }


def _audited_broll_item(**overrides):
    projection = _projection()
    row = {
        "kind": "video",
        "slug": "visual-001-s01",
        "t0": 1.0,
        "t1": 2.0,
        "src_in": 0.25,
        "source_range": projection["source_range"],
        "media_path": projection["media"]["path"],
        "on_screen_text": None,
        "provenance": projection["provenance"],
        "render_spec": None,
        "visual_materialization": projection,
    }
    row.update(overrides)
    return row


def _allow_authoritative_visual_gate(monkeypatch, broll):
    monkeypatch.setattr(
        broll,
        "build_authoritative_broll_receipt",
        lambda *_args, **_kwargs: {
            "contract": "test-authoritative-visual",
            "stock_video_count": 3,
            "stock_videos": [],
            "content_hash": "a" * 64,
        },
    )


def _attach_test_visual_rows(episode: Path):
    path = episode / "highlights" / "tighten" / "value-L01_broll.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    number = 0
    for item in payload["items"]:
        if item.get("kind") != "video":
            continue
        projection = _projection(materialization_id=f"visual-{number:03d}-s01")
        projection.update(
            {
                "event_id": f"visual-{number:03d}",
                "t0": item["t0"],
                "t1": item["t1"],
                "source_range": {
                    "start_sec": 0.0,
                    "end_sec": float(item["t1"]) - float(item["t0"]),
                },
                "media": {
                    "path": f"assets/broll/{item['slug']}.mp4",
                    "bytes": (episode / "assets" / "broll" / f"{item['slug']}.mp4").stat().st_size,
                    "sha256": "b" * 64,
                },
            }
        )
        item.update(
            {
                "src_in": 0.0,
                "source_range": projection["source_range"],
                "media_path": projection["media"]["path"],
                "on_screen_text": None,
                "provenance": projection["provenance"],
                "render_spec": None,
                "visual_materialization": projection,
            }
        )
        number += 1
    path.write_text(json.dumps(payload), encoding="utf-8")


def _timeline(name: str, uid: str, source_path: Path):
    media_pool_item = SimpleNamespace(
        GetClipProperty=lambda key: str(source_path) if key == "File Path" else ""
    )
    item = SimpleNamespace(
        GetMediaPoolItem=lambda: media_pool_item,
        GetName=lambda: source_path.name,
    )
    return SimpleNamespace(
        GetName=lambda: name,
        GetUniqueId=lambda: uid,
        GetItemListInTrack=lambda track_type, index: (
            [item] if track_type in {"video", "audio"} and index == 1 else []
        ),
        GetTrackCount=lambda _track_type: 1,
    )


def _resolve_for_timeline(episode: Path, timeline, mutations: list[str]):
    root = SimpleNamespace(GetSubFolderList=lambda: [])
    media_pool = SimpleNamespace(GetRootFolder=lambda: root)
    project = SimpleNamespace(
        GetName=lambda: episode.name,
        GetSetting=lambda key: "30" if key == "timelineFrameRate" else "",
        GetMediaPool=lambda: media_pool,
        GetTimelineCount=lambda: 1,
        GetTimelineByIndex=lambda index: timeline if index == 1 else None,
        SetCurrentTimeline=lambda _timeline: mutations.append("SetCurrentTimeline") or True,
    )
    manager = SimpleNamespace(GetCurrentProject=lambda: project)
    return SimpleNamespace(GetProjectManager=lambda: manager)


def _write_materialization(episode: Path, master, timeline, *, end_sec: float = 20.0):
    from shared.highlight_materialization import (
        HighlightSource,
        build_materialization_receipt,
        write_materialization_receipt,
    )

    receipt = build_materialization_receipt(
        episode,
        cut_id="value-L01",
        cut_format="long",
        timeline=timeline,
        source_range={
            "start_sec": 10.0,
            "end_sec": end_sec,
            "start_frame": 300,
            "end_frame": int(end_sec * 30),
        },
        source=HighlightSource(
            srt_path=master.srt_path,
            media_path=master.media_path,
            lineage=master.identity(),
        ),
    )
    write_materialization_receipt(episode, receipt)


def test_apply_rejects_stale_winner_before_connecting_to_resolve(tmp_path, monkeypatch):
    import build_resolve_project
    import run_short_broll as broll

    master, identity = _master_selection(tmp_path)
    _write_broll_inputs(tmp_path, identity, winner_lineage={"content_hash": "stale"})
    monkeypatch.setattr(broll, "_open_editorial_master", lambda _episode: master, raising=False)
    monkeypatch.setattr(broll, "_probe_meta", lambda _path: (1.0, 30.0))
    monkeypatch.setattr(
        build_resolve_project,
        "connect_resolve",
        lambda: (_ for _ in ()).throw(AssertionError("Resolve must not be opened")),
    )

    with pytest.raises(SystemExit, match="winners.json Editorial Master lineage"):
        broll.apply(tmp_path, "value-L01")


def test_validate_plan_rejects_three_licensed_videos_without_visual_pipeline_receipts(
    tmp_path, monkeypatch
):
    import run_short_broll as broll

    master, identity = _master_selection(tmp_path)
    _write_broll_inputs(tmp_path, identity)
    monkeypatch.setattr(broll, "_open_editorial_master", lambda _episode: master)

    with pytest.raises(SystemExit, match="Director|DP|Semantic Audit|visual pipeline"):
        broll.validate_plan(tmp_path, "value-L01")


def test_broll_recipe_rejects_audited_timeline_range_drift():
    from agents.brook.script_video.highlight_broll import (
        BrollContractError,
        broll_item_projection,
    )

    with pytest.raises(BrollContractError, match="t1.*audited DP"):
        broll_item_projection(_audited_broll_item(t1=2.1), 0)


def test_broll_recipe_rejects_dp_selected_media_drift():
    from agents.brook.script_video.highlight_broll import (
        BrollContractError,
        broll_item_projection,
    )

    with pytest.raises(BrollContractError, match="media_path.*selected media"):
        broll_item_projection(_audited_broll_item(media_path="assets/broll/other.mp4"), 0)


def test_broll_recipe_rejects_source_trim_drift():
    from agents.brook.script_video.highlight_broll import (
        BrollContractError,
        broll_item_projection,
    )

    with pytest.raises(BrollContractError, match="src_in.*audited DP"):
        broll_item_projection(_audited_broll_item(src_in=0.5), 0)


def test_valid_audited_broll_and_title_recipes_pass_read_only_gates(tmp_path, monkeypatch):
    import run_short_broll as broll
    import run_short_titles as titles

    master, identity = _master_selection(tmp_path)
    _write_broll_inputs(tmp_path, identity)
    plan_path = tmp_path / "highlights/tighten/value-L01_broll.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    projections = []
    for index, item in enumerate(plan["items"]):
        span = float(item["t1"]) - float(item["t0"])
        projection = _projection(materialization_id=f"visual-{index:03d}-s01")
        projection.update(
            {
                "event_id": f"visual-{index:03d}",
                "t0": item["t0"],
                "t1": item["t1"],
                "source_range": {"start_sec": 0.0, "end_sec": span},
                "media": {
                    "path": f"assets/broll/{item['slug']}.mp4",
                    "bytes": (tmp_path / "assets/broll" / f"{item['slug']}.mp4").stat().st_size,
                    "sha256": hashlib.sha256(
                        (tmp_path / "assets/broll" / f"{item['slug']}.mp4").read_bytes()
                    ).hexdigest(),
                },
            }
        )
        item.update(
            {
                "src_in": 0.0,
                "source_range": projection["source_range"],
                "media_path": projection["media"]["path"],
                "on_screen_text": None,
                "provenance": projection["provenance"],
                "render_spec": None,
                "visual_materialization": projection,
            }
        )
        projections.append(projection)
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    title_media = tmp_path / "highlights/visual-pipeline/previews/hero.mov"
    title_media.parent.mkdir(parents=True)
    title_media.write_bytes(b"audited-hero")
    title_spec = {
        "component": "punch_card_wide",
        "render_params": {
            "text": "完整\n句子",
            "tier": 1,
            "style": "paper",
            "show_sec": 1.0,
            "pos_y": 0.6,
        },
        "render_spec_sha256": "c" * 64,
    }
    title_projection = _projection(
        materialization_id="visual-title-s01",
        target_lane="title_track3",
        implementation_kind="hero_title",
        on_screen_text="完整\n句子",
        render_spec=title_spec,
    )
    title_projection.update(
        {
            "event_id": "visual-title",
            "t0": 7.0,
            "t1": 8.0,
            "source_range": {"start_sec": 0.0, "end_sec": 1.0},
            "media": {
                "path": title_media.relative_to(tmp_path).as_posix(),
                "bytes": title_media.stat().st_size,
                "sha256": hashlib.sha256(title_media.read_bytes()).hexdigest(),
            },
        }
    )
    projections.append(title_projection)
    (tmp_path / "highlights/tighten/value-L01_titles.json").write_text(
        json.dumps(
            {
                "titles": [
                    {
                        "text": "完整\n句子",
                        "t0": 7.0,
                        "t1": 8.0,
                        "tier": 1,
                        "style": "paper",
                        "pos_y": 0.6,
                        "source_range": title_projection["source_range"],
                        "media_path": title_projection["media"]["path"],
                        "provenance": title_projection["provenance"],
                        "render_spec": title_spec,
                        "visual_materialization": title_projection,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    artifact_identity = {
        "contract": "fixture",
        "path": "highlights/visual-pipeline/revisions/r1/artifact.json",
        "bytes": 1,
        "sha256": "d" * 64,
        "content_hash": "e" * 64,
    }
    lineage = {
        "contract": "podcast-highlight-visual-lineage-v1",
        "episode_id": tmp_path.name,
        "cut_id": "value-L01",
        "revision_id": "r1",
        "format": "long",
        "editorial_master": identity,
        "current_pointer": dict(artifact_identity),
        "work_packet": dict(artifact_identity),
        "director_plan": dict(artifact_identity),
        "dp_fulfillment": dict(artifact_identity),
        "semantic_audit": dict(artifact_identity),
        "materializations": projections,
        "content_hash": "f" * 64,
    }

    def verify(_root, _cut_id, **kwargs):
        assert {row["materialization_id"] for row in kwargs["items"]} == {
            row["materialization_id"] for row in projections
        }
        return lineage

    monkeypatch.setattr(broll, "_open_editorial_master", lambda _episode: master)
    monkeypatch.setattr(titles, "_open_editorial_master", lambda _episode: master)
    monkeypatch.setattr(
        "agents.brook.script_video.highlight_visual_pipeline.verify_visual_lineage", verify
    )

    assert broll.validate_plan(tmp_path, "value-L01")["stock_video_count"] == 3
    assert titles.validate_plan(tmp_path, "value-L01")["title_count"] == 1


def test_title_materializer_supports_rev10_seven_second_hero():
    import run_short_titles as titles

    assert titles.COMP_SEC - 0.2 >= 7.262


def test_apply_rejects_legacy_guest_namecard_without_guest_camera_correction(
    tmp_path, monkeypatch
):
    import build_resolve_project
    import run_short_broll as broll

    master, identity = _master_selection(tmp_path)
    _write_broll_inputs(tmp_path, identity)
    plan_path = tmp_path / "highlights" / "tighten" / "value-L01_broll.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["items"].insert(
        0,
        {
            "kind": "concept",
            "slug": "guest-namecard",
            "comp": "chapter_label",
            "t0": 0.5,
            "t1": 2.5,
            "vars": {"label": "Guest", "sub": "Title", "style": "paper"},
        },
    )
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    _attach_test_visual_rows(tmp_path)
    monkeypatch.setattr(broll, "_open_editorial_master", lambda _episode: master)
    _allow_authoritative_visual_gate(monkeypatch, broll)
    monkeypatch.setattr(
        build_resolve_project,
        "connect_resolve",
        lambda: (_ for _ in ()).throw(AssertionError("Resolve must not be opened")),
    )

    with pytest.raises(SystemExit, match="唯一 guest camera-correction"):
        broll.apply(tmp_path, "value-L01")


def test_apply_rejects_stale_materialization_range_before_resolve_mutation(
    tmp_path, monkeypatch
):
    import build_resolve_project
    import run_short_broll as broll

    master, identity = _master_selection(tmp_path)
    _write_broll_inputs(tmp_path, identity)
    _attach_test_visual_rows(tmp_path)
    timeline = _timeline("長1 - Master cut（緊·導播）", "director-uid", master.media_path)
    _write_materialization(tmp_path, master, timeline, end_sec=19.0)
    mutations: list[str] = []
    monkeypatch.setattr(broll, "_open_editorial_master", lambda _episode: master)
    _allow_authoritative_visual_gate(monkeypatch, broll)
    monkeypatch.setattr(broll, "_probe_meta", lambda _path: (1.0, 30.0))
    monkeypatch.setattr(
        build_resolve_project,
        "connect_resolve",
        lambda: _resolve_for_timeline(tmp_path, timeline, mutations),
    )

    with pytest.raises(SystemExit, match="source range"):
        broll.apply(tmp_path, "value-L01")
    assert mutations == []


def test_apply_rejects_raw_live_aroll_before_resolve_mutation(tmp_path, monkeypatch):
    import build_resolve_project
    import run_short_broll as broll

    master, identity = _master_selection(tmp_path)
    _write_broll_inputs(tmp_path, identity)
    _attach_test_visual_rows(tmp_path)
    name = "長1 - Master cut（緊·導播）"
    receipt_timeline = _timeline(name, "director-uid", master.media_path)
    _write_materialization(tmp_path, master, receipt_timeline)
    raw = tmp_path / "Default_program.mp4"
    raw.write_bytes(b"raw-program")
    live_timeline = _timeline(name, "director-uid", raw)
    mutations: list[str] = []
    monkeypatch.setattr(broll, "_open_editorial_master", lambda _episode: master)
    _allow_authoritative_visual_gate(monkeypatch, broll)
    monkeypatch.setattr(broll, "_probe_meta", lambda _path: (1.0, 30.0))
    monkeypatch.setattr(
        build_resolve_project,
        "connect_resolve",
        lambda: _resolve_for_timeline(tmp_path, live_timeline, mutations),
    )

    with pytest.raises(SystemExit, match="not exact master media"):
        broll.apply(tmp_path, "value-L01")
    assert mutations == []


def test_apply_reopens_master_after_preparation_before_resolve_mutation(
    tmp_path, monkeypatch
):
    import build_resolve_project
    import run_short_broll as broll

    master, identity = _master_selection(tmp_path)
    _write_broll_inputs(tmp_path, identity)
    _attach_test_visual_rows(tmp_path)
    changed_identity = {**identity, "content_hash": "b" * 64}
    changed_master = SimpleNamespace(
        media_path=master.media_path,
        srt_path=master.srt_path,
        identity=lambda: changed_identity,
    )
    calls = 0

    def open_master(_episode):
        nonlocal calls
        calls += 1
        return master if calls == 1 else changed_master

    timeline = _timeline("長1 - Master cut（緊·導播）", "director-uid", master.media_path)
    mutations: list[str] = []
    monkeypatch.setattr(broll, "_open_editorial_master", open_master)
    _allow_authoritative_visual_gate(monkeypatch, broll)
    monkeypatch.setattr(broll, "_probe_meta", lambda _path: (1.0, 30.0))
    monkeypatch.setattr(
        build_resolve_project,
        "connect_resolve",
        lambda: _resolve_for_timeline(tmp_path, timeline, mutations),
    )

    with pytest.raises(SystemExit, match="candidates.json Editorial Master lineage"):
        broll.apply(tmp_path, "value-L01")
    assert calls == 2
    assert mutations == []


class TestFillZoom:
    def test_landscape_4k_fills_vertical(self):
        # 3840x2160 fit 進 1080x1920 是貼寬（1080x607.5）→ 補到 1920 高
        assert _fill_zoom("3840x2160") == pytest.approx(1920 / 607.5, rel=1e-3)

    def test_native_vertical_same_aspect_is_one(self):
        assert _fill_zoom("2160x3840") == pytest.approx(1.0, rel=1e-6)
        assert _fill_zoom("1080x1920") == pytest.approx(1.0, rel=1e-6)

    def test_taller_aspect_needs_width_fill(self):
        # 2160x4096 比 9:16 更瘦長 → fit 貼高後寬不足，補寬
        z = _fill_zoom("2160x4096")
        assert z == pytest.approx(1080 / (2160 * (1920 / 4096)), rel=1e-3)
        assert z > 1.0

    def test_garbage_resolution_falls_back_to_one(self):
        assert _fill_zoom("") == 1.0
        assert _fill_zoom(None) == 1.0
        assert _fill_zoom("weird") == 1.0


class TestDataUri:
    def test_png_mime_and_base64(self, tmp_path):
        p = tmp_path / "x.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n")
        uri = _data_uri(p)
        assert uri.startswith("data:image/png;base64,")

    def test_jpg_mime(self, tmp_path):
        p = tmp_path / "x.jpg"
        p.write_bytes(b"\xff\xd8\xff")
        assert _data_uri(p).startswith("data:image/jpeg;base64,")


class TestGuestNamecardJob:
    class _Placement:
        @staticmethod
        def identity():
            return {"contract": "podcast-identity-placement-v1", "content_hash": "a" * 64}

    def test_maps_sealed_event_to_existing_wide_chapter_label(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "run_short_broll.verify_identity_placement",
            lambda *_args, **_kwargs: self._Placement(),
        )
        lineage = self._Placement.identity()
        job = _guest_namecard_job(
            tmp_path,
            "value-L01",
            "long",
            {
                "t0": 43.0,
                "t1": 48.2,
                "kind": "guest-namecard",
                "name": "林之晨",
                "title": "《逆分工》共同作者",
                "style": "paper",
                "identity_placement": lineage,
            },
            3,
        )
        assert job == {
            "comp": "chapter_label",
            "vars": {
                "show_sec": 5.2,
                "label": "林之晨",
                "sub": "《逆分工》共同作者",
                "align": "left",
                "style": "paper",
            },
            "t0": 43.0,
            "span": 5.2,
            "i": 3,
            "kind": "guest-namecard",
        }

    def test_stale_identity_lineage_fails_closed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "run_short_broll.verify_identity_placement",
            lambda *_args, **_kwargs: self._Placement(),
        )
        with pytest.raises(SystemExit, match="identity lineage 已過期"):
            _guest_namecard_job(
                tmp_path,
                "value-L01",
                "long",
                {
                    "t0": 43.0,
                    "t1": 48.2,
                    "name": "林之晨",
                    "title": "《逆分工》共同作者",
                    "identity_placement": {"content_hash": "stale"},
                },
                0,
            )


# ── 長片格式（修修 2026-08-03 長片線）─────────────────────────────────────


class TestLongFormat:
    def test_canvas_and_comp_suffix(self):
        from run_short_broll import FORMAT_BROLL

        assert tuple(FORMAT_BROLL["short"]["canvas"]) == (1080, 1920)
        assert FORMAT_BROLL["short"]["comp_suffix"] == ""  # 短片走原 composition
        assert tuple(FORMAT_BROLL["long"]["canvas"]) == (1920, 1080)
        assert FORMAT_BROLL["long"]["comp_suffix"] == "_wide"

    def test_landscape_source_needs_no_crop_in_wide_canvas(self):
        # 3840x2160 進 16:9 畫布 = 同長寬比，zoom 1.0（不裁）
        assert _fill_zoom("3840x2160", 1.0, (1920, 1080)) == pytest.approx(1.0, rel=1e-6)

    def test_vertical_source_in_wide_canvas_crops_hard(self):
        """直式素材進 16:9 = 只看得到中央橫帶——不是壞掉，是必須逐支驗樣張。"""
        z = _fill_zoom("2160x3840", 1.0, (1920, 1080))
        # fit 後是 607.5×1080，要填滿 1920 寬 → 1920/607.5 ≈ 3.16
        assert z == pytest.approx(1920 / 607.5, rel=1e-3)
        assert 1 / z == pytest.approx(0.316, abs=0.01)  # 只剩約 32% 的源高度可見
        # 2026-08-04 實測：特寫類（手/手機）裁完仍成立，全身鏡頭會變無頭軀幹

    def test_wide_compositions_declare_16_9_canvas(self):
        """*_wide.html 的 data-width/height 是 hyperframes 的輸出解析度來源，
        JS 改不動——寫錯會渲出直式卡片疊到 16:9 timeline 上。"""
        root = Path(__file__).resolve().parent.parent / "video" / "compositions"
        for comp in (
            "punch_card",
            "sticker_pair",
            "concept_card",
            "chapter_label",
            "transition_title",
        ):
            html = (root / comp / "compositions" / f"{comp}_wide.html").read_text(encoding="utf-8")
            assert 'data-width="1920"' in html, comp
            assert 'data-height="1080"' in html, comp

    def test_transition_title_paper_needs_texture_composite(self):
        """B2 定版：paper 系轉場卡是透明字卡，必須疊紙紋 motion bg 才滿版——
        漏合成就回到「黑字裸壓實拍」的壓臉 bug（修修五輪）。scrim 自帶底。"""
        from run_short_broll import COMP_MAX_SEC, PAPER_TEXTURE

        assert PAPER_TEXTURE == "paper-texture.mp4"
        # data-duration 4s：3.0s 上軌 + 退場收在 show_sec 內
        assert COMP_MAX_SEC["transition_title"] == 4.0
        root = Path(__file__).resolve().parent.parent / "video" / "compositions"
        comp_dir = root / "transition_title" / "compositions"
        html = (comp_dir / "transition_title_wide.html").read_text(encoding="utf-8")
        # 滿版底是「元素」不是 body 背景——body 背景在 alpha 渲染下會被丟掉
        assert "background: transparent" in html
        assert 'id="scrim"' in html
        # 退場動畫存在（原生 transition_title 註解「硬切」已廢）
        assert "yPercent: -112" in html

    def test_wide_compositions_avoid_gsap_transform_double_apply(self):
        """CSS transform + GSAP xPercent/yPercent 會疊加（sticker_pair 二十四輪
        血案；concept_card 直式版至今仍中招）。wide 版一律不寫 CSS transform
        在被 GSAP 動到的元素上。"""
        root = Path(__file__).resolve().parent.parent / "video" / "compositions"
        html = (root / "concept_card" / "compositions" / "concept_card_wide.html").read_text(
            encoding="utf-8"
        )
        card_block = html.split("#card {")[1].split("}")[0]
        assert "transform:" not in card_block
        assert "xPercent: -50" in html  # 水平置中改由 GSAP 負責


class TestContentGaps:
    """素材真空偵測（修修 2026-08-04：「情緒是建構的那段太空，程式應該偵測」）。
    既有 gap 偵測把 cut 算進事件——長片導播每幾秒換鏡，14s 門檻永遠不觸發，
    164 秒無素材照樣回 gaps=[]。content_gaps 只掃強事件。"""

    def _scan(self, events, dur, srt=(), threshold=75.0):
        from run_short_review import _scan_content_gaps

        return _scan_content_gaps(events, dur, list(srt), threshold)

    def test_cuts_do_not_fill_content_gap(self):
        # 0-10s 有素材，之後 170 秒只有換鏡——cut 是弱事件，必須報真空
        events = [{"type": "video", "slug": "stock", "t0": 5.0, "t1": 10.0}] + [
            {"type": "cut", "slug": "", "t0": float(t), "t1": float(t)} for t in range(12, 180, 6)
        ]
        gaps = self._scan(events, 180.0)
        assert len(gaps) == 1
        assert gaps[0]["from"] == 10.0 and gaps[0]["to"] == 180.0

    def test_dense_strong_events_report_clean(self):
        events = [
            {"type": "concept", "slug": "tr", "t0": float(t), "t1": float(t + 3)}
            for t in range(0, 300, 60)
        ]
        assert self._scan(events, 300.0) == []

    def test_transcript_attached_to_gap_window(self):
        events = [{"type": "video", "slug": "s", "t0": 0.0, "t1": 5.0}]
        srt = [
            {"t0": 2.0, "t1": 4.0, "text": "素材期間的話"},
            {"t0": 50.0, "t1": 53.0, "text": "真空裡的話"},
        ]
        gaps = self._scan(events, 120.0, srt)
        assert len(gaps) == 1
        assert "真空裡的話" in gaps[0]["transcript"]
        assert "素材期間的話" not in gaps[0]["transcript"]

    def test_punch_zoom_is_not_strong_event(self):
        events = [
            {"type": "punch-ramp", "slug": "", "t0": float(t), "t1": float(t + 2)}
            for t in range(0, 200, 20)
        ]
        gaps = self._scan(events, 200.0)
        assert len(gaps) == 1  # punch zoom 同機位縮放，撐不起素材真空


def test_sfx_chapter_label_maps_to_swish(tmp_path):
    """長片證據驅動語彙：章節籤=swish（導航）、概念卡=pop（重點）、hero=ding。"""
    import json as _json
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from run_short_sfx import build_cues

    td = tmp_path / "highlights" / "tighten"
    td.mkdir(parents=True)
    (td / "x_titles.json").write_text(
        _json.dumps({"titles": [{"text": "把主權\n拿回來", "t0": 10.0, "t1": 12.8, "tier": 1}]}),
        encoding="utf-8",
    )
    (td / "x_broll.json").write_text(
        _json.dumps(
            {
                "items": [
                    {
                        "t0": 20.0,
                        "t1": 23.0,
                        "kind": "concept",
                        "comp": "chapter_label",
                        "vars": {"label": "睡眠"},
                    },
                    {"t0": 30.0, "t1": 33.0, "kind": "concept", "vars": {"title": "相關 ≠ 因果"}},
                ]
            }
        ),
        encoding="utf-8",
    )
    cues = build_cues(tmp_path, "x")
    by_t = {c["t"]: c["sfx"] for c in cues}
    assert by_t[10.0] == "ding"  # hero
    assert by_t[20.0] == "swish"  # 章節籤：導航記號，輕掃
    assert by_t[30.0] == "pop"  # 概念卡維持原映射
