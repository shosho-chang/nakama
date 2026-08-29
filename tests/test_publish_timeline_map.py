"""成品 render 的 timeline 由 Release 說了算（安靜出錯片的護欄）。"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.usopp.publish_timeline import (  # noqa: E402
    MAP_RELPATH,
    SCHEMA,
    PublishTimelineError,
    canonical_timeline_from_transactions,
    export_matches_current_release,
    load_timeline_map,
    packaging_cut_id,
    resolve_target,
    verify_duration,
)

MAP = {
    "schema": SCHEMA,
    "episode": "20260805 林之晨",
    "cuts": {
        "punch-L04": {
            "timeline": "long3-fresh-20260828-r4-base",
            "release_id": "release-af65a1d7a2ac611eb78be493",
            "release_cut_id": "long3-fresh-20260828-r4",
            "expected_duration_sec": 492.309333,
        }
    },
}


def _write_map(root: Path, payload: dict) -> Path:
    path = root / MAP_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_missing_map_returns_none_so_old_episodes_keep_working(tmp_path):
    assert load_timeline_map(tmp_path) is None


def test_foreign_schema_is_refused(tmp_path):
    _write_map(tmp_path, {**MAP, "schema": "something.else.v9"})
    with pytest.raises(PublishTimelineError):
        load_timeline_map(tmp_path)


def test_resolve_target_carries_the_release_side_id(tmp_path):
    _write_map(tmp_path, MAP)
    target = resolve_target(load_timeline_map(tmp_path), "punch-L04")
    assert target.timeline == "long3-fresh-20260828-r4-base"
    # packaging 叫它 punch-L04，Release 叫它 long3-fresh-…；對應表是唯一把
    # 這兩個 id 綁在一起的地方。
    assert target.release_cut_id == "long3-fresh-20260828-r4"


def test_unregistered_cut_fails_instead_of_falling_back_to_the_guess(tmp_path):
    """回退到 winners.json 的名字猜，正是會 render 出舊剪輯的那條路。"""
    _write_map(tmp_path, MAP)
    with pytest.raises(PublishTimelineError, match="value-L02"):
        resolve_target(load_timeline_map(tmp_path), "value-L02")


def test_entry_missing_a_field_fails_loud(tmp_path):
    broken = {**MAP, "cuts": {"punch-L04": {"timeline": "x", "release_id": "y"}}}
    _write_map(tmp_path, broken)
    with pytest.raises(PublishTimelineError, match="expected_duration_sec"):
        resolve_target(load_timeline_map(tmp_path), "punch-L04")


def test_duration_within_container_rounding_is_accepted(tmp_path):
    """preview mp4 與 timeline frame 數本來就差一兩個 frame（實測 592.900/592.967）。"""
    _write_map(tmp_path, MAP)
    target = resolve_target(load_timeline_map(tmp_path), "punch-L04")
    verify_duration(target, 492.333)


def test_the_actual_20260805_mismatch_is_caught(tmp_path):
    """真實事故：長3 的舊 timeline 是 260s，Release 是 492s，舊碼會照 render。"""
    _write_map(tmp_path, MAP)
    target = resolve_target(load_timeline_map(tmp_path), "punch-L04")
    with pytest.raises(PublishTimelineError, match="260"):
        verify_duration(target, 260.0)


def test_canonical_timeline_read_back_from_a_committed_transaction(tmp_path):
    (tmp_path / "resolve-368cb9c9.json").write_text(
        json.dumps(
            {
                "schema": "nakama.finished-cut-resolve-transaction.v1",
                "payload": {
                    "status": "committed",
                    "transaction_receipt_id": "resolve-receipt-278cda15",
                    "canonical": {"name": "long3-fresh-20260828-r4-base"},
                },
            }
        ),
        encoding="utf-8",
    )
    assert (
        canonical_timeline_from_transactions(tmp_path, "resolve-receipt-278cda15")
        == "long3-fresh-20260828-r4-base"
    )


def test_uncommitted_transaction_is_not_authority(tmp_path):
    (tmp_path / "resolve-abandoned.json").write_text(
        json.dumps(
            {
                "payload": {
                    "status": "rolled_back",
                    "transaction_receipt_id": "resolve-receipt-278cda15",
                    "canonical": {"name": "half-written-base"},
                }
            }
        ),
        encoding="utf-8",
    )
    assert canonical_timeline_from_transactions(tmp_path, "resolve-receipt-278cda15") is None


def test_migrated_release_has_no_transaction_to_read(tmp_path):
    assert canonical_timeline_from_transactions(tmp_path / "nope", "any") is None


def test_release_chapters_without_a_map_is_empty(tmp_path):
    """沒有對應表就沒有 Release 權威——回空，由呼叫端決定要不要回退。"""
    from agents.usopp.publish_timeline import release_chapters

    assert release_chapters(tmp_path, "punch-L04") == []


def test_resolve_chapters_prefers_release_over_stale_broll(tmp_path, monkeypatch):
    """有對應表時，絕不回頭撿 broll——那是 ADR-065 的舊時間軸。"""
    from agents.usopp import video_description as vd

    _write_map(tmp_path, MAP)
    broll = tmp_path / "highlights" / "tighten" / "punch-L04_broll.json"
    broll.parent.mkdir(parents=True, exist_ok=True)
    broll.write_text(
        json.dumps(
            {
                "items": [
                    {"t0": 129.2, "comp": "transition_title", "vars": {"title": "舊時間軸 A"}},
                    {"t0": 176.0, "comp": "transition_title", "vars": {"title": "舊時間軸 B"}},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "agents.usopp.publish_timeline.release_chapters",
        lambda episode_dir, cut_id: [(0.0, "開場"), (47.0, "來自 Release")],
    )
    assert vd.resolve_chapters(tmp_path, "punch-L04") == [(0.0, "開場"), (47.0, "來自 Release")]


def test_resolve_chapters_with_a_map_never_falls_back(tmp_path, monkeypatch):
    """Release 說沒有分章，就是沒有分章——沒有分章好過錯的分章。"""
    from agents.usopp import video_description as vd

    _write_map(tmp_path, MAP)
    broll = tmp_path / "highlights" / "tighten" / "punch-L04_broll.json"
    broll.parent.mkdir(parents=True, exist_ok=True)
    broll.write_text(
        json.dumps(
            {
                "items": [
                    {"t0": 129.2, "comp": "transition_title", "vars": {"title": "舊時間軸 A"}},
                    {"t0": 176.0, "comp": "transition_title", "vars": {"title": "舊時間軸 B"}},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "agents.usopp.publish_timeline.release_chapters", lambda episode_dir, cut_id: []
    )
    assert vd.resolve_chapters(tmp_path, "punch-L04") == []


def test_resolve_chapters_falls_back_when_episode_has_no_map(tmp_path):
    """沒建對應表的舊集數行為不變。"""
    from agents.usopp import video_description as vd

    broll = tmp_path / "highlights" / "tighten" / "punch-L5_broll.json"
    broll.parent.mkdir(parents=True, exist_ok=True)
    broll.write_text(
        json.dumps(
            {
                "items": [
                    {"t0": 10.0, "comp": "transition_title", "vars": {"title": "A"}},
                    {"t0": 20.0, "comp": "transition_title", "vars": {"title": "B"}},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert vd.resolve_chapters(tmp_path, "punch-L5") == [(0.0, "開場"), (10.0, "A"), (20.0, "B")]


def test_release_subtitle_without_a_map_is_none(tmp_path):
    from agents.usopp.publish_timeline import release_subtitle

    assert release_subtitle(tmp_path, "punch-L04") is None


def test_description_prompt_falls_back_to_tight_srt_without_a_map(tmp_path):
    """沒建對應表的舊集數仍讀 tight SRT。"""
    from agents.usopp.video_description import build_description_prompt

    srt = tmp_path / "highlights" / "srt" / "punch-L5_tight_r001.srt"
    srt.parent.mkdir(parents=True, exist_ok=True)
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\n舊線逐字稿\n", encoding="utf-8")
    prompt = build_description_prompt(
        tmp_path, cut_id="punch-L5", title="t", citations=[], chapters=[]
    )
    assert "舊線逐字稿" in prompt


def test_description_prompt_prefers_the_release_subtitle(tmp_path, monkeypatch):
    """有 Release 時要照成品那份寫，不能照被取代的 tight SRT。"""
    from agents.usopp.video_description import build_description_prompt

    stale = tmp_path / "highlights" / "srt" / "punch-L04_tight_r002.srt"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("1\n00:00:00,000 --> 00:00:02,000\n被取代的舊剪輯\n", encoding="utf-8")
    fresh = tmp_path / "release.srt"
    fresh.write_text("1\n00:00:00,000 --> 00:00:02,000\n成品那一份\n", encoding="utf-8")
    monkeypatch.setattr(
        "agents.usopp.publish_timeline.release_subtitle", lambda episode_dir, cut_id: fresh
    )
    prompt = build_description_prompt(
        tmp_path, cut_id="punch-L04", title="t", citations=[], chapters=[]
    )
    assert "成品那一份" in prompt
    assert "被取代的舊剪輯" not in prompt


def _stub_youtube(recorder):
    class _Insert:
        def __init__(self, **kwargs):
            recorder.update(kwargs)

        def execute(self):
            return {"id": "caption-1"}

    class _Captions:
        def insert(self, **kwargs):
            return _Insert(**kwargs)

    class _YT:
        def captions(self):
            return _Captions()

    return _YT()


def test_uploaded_captions_come_from_the_release_not_the_stale_tight_srt(tmp_path, monkeypatch):
    """貼錯字幕會讓整支片的 CC 對不上畫面——來源必須跟成品同源。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import publish_upload

    fresh = tmp_path / "release.srt"
    fresh.write_text("1\n00:00:00,000 --> 00:00:01,000\n成品字幕\n", encoding="utf-8")
    stale = tmp_path / "punch-L04_tight_r002.srt"
    stale.write_text("1\n00:00:00,000 --> 00:00:01,000\n舊剪輯字幕\n", encoding="utf-8")

    monkeypatch.setattr(
        "agents.usopp.publish_timeline.release_subtitle", lambda episode_dir, cid: fresh
    )
    monkeypatch.setattr("shared.tight_srt.latest_tight_srt", lambda episode_dir, cid: stale)
    monkeypatch.setattr(
        publish_upload,
        "logger",
        type("L", (), {"info": lambda *a: None, "warning": lambda *a: None})(),
    )

    seen: dict = {}
    monkeypatch.setattr(
        "googleapiclient.http.MediaFileUpload",
        lambda path, mimetype=None: seen.setdefault("path", path),
        raising=False,
    )
    publish_upload.upload_captions(_stub_youtube(seen), "vid", tmp_path, "punch-L04")
    assert seen["path"] == str(fresh)


def test_uploaded_captions_fall_back_when_the_episode_has_no_release(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import publish_upload

    stale = tmp_path / "punch-L5_tight_r001.srt"
    stale.write_text("1\n00:00:00,000 --> 00:00:01,000\n舊線字幕\n", encoding="utf-8")
    monkeypatch.setattr(
        "agents.usopp.publish_timeline.release_subtitle", lambda episode_dir, cid: None
    )
    monkeypatch.setattr("shared.tight_srt.latest_tight_srt", lambda episode_dir, cid: stale)
    monkeypatch.setattr(
        publish_upload,
        "logger",
        type("L", (), {"info": lambda *a: None, "warning": lambda *a: None})(),
    )
    seen: dict = {}
    monkeypatch.setattr(
        "googleapiclient.http.MediaFileUpload",
        lambda path, mimetype=None: seen.setdefault("path", path),
        raising=False,
    )
    publish_upload.upload_captions(_stub_youtube(seen), "vid", tmp_path, "punch-L5")
    assert seen["path"] == str(stale)


# --- Resolve binding for the revision loop ----------------------------------

BINDING = {
    "schema": "nakama.finished_cut_resolve_binding.v1",
    "episode_id": "20260805 林之晨",
    "database": {"db_type": "Disk", "db_name": "Local Database", "ip_address": None},
    "folder": "",
    "project_name": "20260805 林之晨",
    "editorial_master_content_hash": (
        "8e7c13c2c55bc0df0c05241cfd91a9bf5c6b484b58058dae42d2bfaa7576805b"
    ),
    "staging_root": r"G:\Footages\20260805 林之晨\highlights\staging\finished-cut",
    "cuts": [
        {"cut_id": "long3-fresh-20260828-r4", "timeline_name": "long3-fresh-20260828-r4-base"}
    ],
}


def _identities(monkeypatch, rows):
    from agents.brook.script_video.finished_cut_production import _composition as comp
    from agents.brook.script_video.finished_cut_production._resolve import TimelineIdentity

    monkeypatch.setattr(
        comp,
        "current_timeline_identities",
        lambda locator: tuple(TimelineIdentity(name=n, uid=u) for n, u in rows),
    )


def test_binding_resolves_the_timeline_uid_live(monkeypatch):
    """uid 每次 committed transaction 都會變，所以只能在 job 時查。"""
    from agents.brook.script_video.finished_cut_production import build_resolve_configuration

    _identities(
        monkeypatch,
        [("long3-fresh-20260828-r4-base", "167fe522-c178-47d4-b50c-bad7cec92b9d")],
    )
    cfg = build_resolve_configuration(BINDING, "20260805 林之晨")
    assert cfg.binding.cuts[0].canonical.uid == "167fe522-c178-47d4-b50c-bad7cec92b9d"
    # project uid 是由 locator 推導的，不從檔案讀——檔案裡少一個會過期的欄位。
    assert cfg.binding.project_uid == (
        "resolve-project:da7c1f4698b72f57a400f9a5196d0b4a136ea498236f3296b13c4fe272795231"
    )


def test_binding_for_a_renamed_or_missing_timeline_fails_loud(monkeypatch):
    from agents.brook.script_video.finished_cut_production import build_resolve_configuration

    _identities(monkeypatch, [("some-other-timeline", "0000")])
    with pytest.raises(ValueError, match="long3-fresh-20260828-r4-base"):
        build_resolve_configuration(BINDING, "20260805 林之晨")


def test_binding_from_another_episode_is_refused(monkeypatch):
    from agents.brook.script_video.finished_cut_production import build_resolve_configuration

    _identities(monkeypatch, [("long3-fresh-20260828-r4-base", "abcd")])
    with pytest.raises(ValueError, match="another episode"):
        build_resolve_configuration(BINDING, "20260723 謝伯讓")


def test_binding_with_a_foreign_schema_is_refused(monkeypatch):
    from agents.brook.script_video.finished_cut_production import build_resolve_configuration

    _identities(monkeypatch, [("long3-fresh-20260828-r4-base", "abcd")])
    with pytest.raises(ValueError, match="schema"):
        build_resolve_configuration({**BINDING, "schema": "something.else"}, "20260805 林之晨")


def test_watcher_refuses_to_start_a_revision_without_a_binding(tmp_path):
    """沒有 Resolve 授權就走不完，寧可不要開始——不然會白燒一輪 LLM 再死在 Resolve。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import finished_review_watcher as watcher

    episode_dir = tmp_path / "20260805 林之晨"
    (episode_dir / "highlights" / "finished-cut-production-v1").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="Resolve binding is missing"):
        watcher._resolve_configuration(
            {
                "episode_dir": str(episode_dir),
                "episode_id": "20260805 林之晨",
                "episodes_root": str(tmp_path),
            }
        )


def test_release_cut_id_translates_back_to_the_publish_line_id(tmp_path):
    """成品審核講 Release 的 id，publish_prep 與 packaging 板講 winners 的 id。"""
    _write_map(tmp_path, MAP)
    assert packaging_cut_id(tmp_path, "long3-fresh-20260828-r4") == "punch-L04"


def test_unknown_and_mapless_cut_ids_pass_through_unchanged(tmp_path):
    """多數 cut 兩邊同名，舊集數根本沒有對應表——都必須維持既有行為。"""
    assert packaging_cut_id(tmp_path, "value-L01") == "value-L01"
    _write_map(tmp_path, MAP)
    assert packaging_cut_id(tmp_path, "value-L01") == "value-L01"


def test_export_from_the_current_release_is_reused(tmp_path):
    _write_map(tmp_path, MAP)
    receipt = {
        "status": "rendered",
        "cuts": [{"cut_id": "punch-L04", "release_id": "release-af65a1d7a2ac611eb78be493"}],
    }
    assert export_matches_current_release(tmp_path, "punch-L04", receipt) is True


def test_export_from_a_superseded_release_is_not_reused(tmp_path):
    """amendment 重封 Release 時片長不變，長度護欄看不出差別——只能靠 release_id。"""
    _write_map(tmp_path, MAP)
    receipt = {
        "status": "rendered",
        "cuts": [{"cut_id": "punch-L04", "release_id": "release-37058c0dbeed4b6cab280975"}],
    }
    assert export_matches_current_release(tmp_path, "punch-L04", receipt) is False


def test_receipt_without_release_id_is_treated_as_stale(tmp_path):
    """寧可多 render 一次，也不要把來歷不明的舊成品當成現行 Release。"""
    _write_map(tmp_path, MAP)
    receipt = {"status": "rendered", "cuts": [{"cut_id": "punch-L04"}]}
    assert export_matches_current_release(tmp_path, "punch-L04", receipt) is False


def test_episodes_without_a_map_keep_reusing_their_exports(tmp_path):
    receipt = {"status": "rendered", "cuts": [{"cut_id": "punch-L04"}]}
    assert export_matches_current_release(tmp_path, "punch-L04", receipt) is True
