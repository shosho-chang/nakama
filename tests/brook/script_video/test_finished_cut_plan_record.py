"""plan record 是成品唯一的耐久紀錄（ADR-069 階段 4）。

Candidate → Release → 不可變版本 → pointer → cutover journal 那五層退役了。這一組
測試鎖住取代它們的那一份紀錄：寫進去的是量過的事實、讀回來的比寫入時寬鬆、發布線
要的東西（timeline 名、片長、章節、字幕）都拿得到。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agents.brook.script_video.finished_cut_production._plan_record import (
    PLAN_RECORD_FILENAME,
    PLAN_RECORD_SCHEMA,
    PlanRecordError,
    PlanRecordStore,
    PlanTimeline,
    read_plan_record_at,
    write_plan_record,
)
from agents.brook.script_video.finished_cut_production._records import (
    EventRecord,
    _mint_materialization_plan,
    _mint_projected_component,
)
from tests.brook.script_video.finished_cut_plan_records import plan_record

_TIMELINE = PlanTimeline(name="長2 - 退休不會解脫的幻覺（緊·導播）", uid="work-uid-1")
_TRANSACTION = "resolve-" + "a" * 24
_STAGING = Path("highlights/staging/finished-cut/1bdf1f5bf32fa058ea75aac6")


class _PreviewReadyTransactions:
    def __init__(self, *, status: str = "preview_ready") -> None:
        self._status = status

    def inspect_transaction(self, transaction_id: str) -> dict[str, object]:
        return {
            "transaction_id": transaction_id,
            "cut_id": "punch-L03",
            "status": self._status,
            "timeline": {"name": _TIMELINE.name, "uid": _TIMELINE.uid},
        }


def _plan(*, duration_sec: float = 490.304, component_t1: float = 23.0):
    event = EventRecord(
        event_id="event-chapter",
        master_cue_ids=("cue-3",),
        text_hash="e" * 64,
        intent="章節卡",
        visual_status="approved",
        text="第三句",
        t0=20.0,
        t1=26.0,
        section_id="section-2",
        display="轉折",
        semantic_kind="chapter",
        implementation_kind="fullscreen_transition",
        lane="fullscreen_transition",
    )
    components = (
        _mint_projected_component(
            component_id="component-opening",
            event_id="event-chapter",
            semantic_kind="chapter",
            implementation_kind="fullscreen_transition",
            lane="fullscreen_transition",
            display="開場之後",
            t0=10.0,
            t1=13.0,
            asset_ref=None,
        ),
        _mint_projected_component(
            component_id="component-chapter",
            event_id="event-chapter",
            semantic_kind="chapter",
            implementation_kind="fullscreen_transition",
            lane="fullscreen_transition",
            display="轉折",
            t0=20.0,
            t1=component_t1,
            asset_ref=None,
        ),
    )
    return _mint_materialization_plan(
        plan_id="plan-242dadff30be4ac58dd789c17b121e86",
        run_id="run-1",
        command_id="approved-cut:" + "f" * 32,
        episode_id="20260901 蘇予昕",
        cut_id="punch-L03",
        format="long",
        director_acceptance_id="director-1",
        dp_acceptance_id="dp-1",
        visual_acceptance_id="visual-1",
        events=(event,),
        components=components,
        duration_sec=duration_sec,
    )


def _artifacts(episode_root: Path) -> tuple[Path, Path]:
    staging = episode_root / _STAGING
    staging.mkdir(parents=True, exist_ok=True)
    preview = staging / "preview.mp4"
    preview.write_bytes(b"preview bytes")
    subtitle = staging / "review.srt"
    subtitle.write_bytes(b"1\n00:00:00,000 --> 00:00:04,000\n\xe7\xac\xac\xe4\xb8\x80\n")
    return preview, subtitle


def _store(episode_root: Path, **kwargs) -> PlanRecordStore:
    return PlanRecordStore(
        episode_root,
        transactions=kwargs.pop("transactions", _PreviewReadyTransactions()),
        preview_probe=kwargs.pop(
            "preview_probe", lambda _path: {"duration_sec": 490.304, "video_codec": "h264"}
        ),
    )


def _staged(episode_root: Path, **kwargs):
    preview, subtitle = _artifacts(episode_root)
    return _store(episode_root, **kwargs).stage(
        kwargs.pop("plan", None) or _plan(),
        editorial_master_id="c" * 64,
        winner_id="winner-1",
        tight_cut_id="tight-1",
        transaction_id=_TRANSACTION,
        timeline=_TIMELINE,
        preview_path=preview,
        subtitle_path=subtitle,
    )


def test_a_staged_record_measures_both_artifacts_where_they_actually_are(tmp_path) -> None:
    record = _staged(tmp_path)

    assert record.preview.path == (_STAGING / "preview.mp4").as_posix()
    assert record.preview.bytes == len(b"preview bytes")
    assert record.preview.sha256 == hashlib.sha256(b"preview bytes").hexdigest()
    assert record.preview.duration_sec == 490.304
    assert record.subtitle.path == (_STAGING / "review.srt").as_posix()
    assert record.timeline == _TIMELINE
    assert record.transaction_id == _TRANSACTION


def test_a_record_survives_the_envelope_unchanged(tmp_path) -> None:
    record = _staged(tmp_path)
    path = tmp_path / _STAGING / PLAN_RECORD_FILENAME

    write_plan_record(path, record)

    assert read_plan_record_at(path) == record
    document = json.loads(path.read_bytes())
    assert document["schema"] == PLAN_RECORD_SCHEMA


def test_a_tampered_record_is_refused_by_its_own_checksum(tmp_path) -> None:
    record = _staged(tmp_path)
    path = tmp_path / _STAGING / PLAN_RECORD_FILENAME
    write_plan_record(path, record)
    document = json.loads(path.read_bytes())
    document["payload"]["cut_id"] = "punch-L04"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(PlanRecordError, match="checksum differs"):
        read_plan_record_at(path)


def test_a_component_past_the_preview_duration_cannot_be_recorded(tmp_path) -> None:
    # plan 與成品對不上就是會發錯片：component 收在 600s，preview 只有 490s。
    with pytest.raises(PlanRecordError, match="exceeds the preview duration"):
        _staged(tmp_path, plan=_plan(duration_sec=600.0, component_t1=600.0))


def test_a_transaction_that_is_not_preview_ready_cannot_be_recorded(tmp_path) -> None:
    with pytest.raises(PlanRecordError, match="requires a preview_ready transaction"):
        _staged(tmp_path, transactions=_PreviewReadyTransactions(status="prepared"))


def test_a_preview_without_a_positive_duration_cannot_be_recorded(tmp_path) -> None:
    with pytest.raises(PlanRecordError, match="positive finite duration_sec"):
        _staged(tmp_path, preview_probe=lambda _path: {"duration_sec": 0.0})


def test_an_artifact_outside_the_episode_root_cannot_be_recorded(tmp_path) -> None:
    episode_root = tmp_path / "episode"
    episode_root.mkdir()
    outside = tmp_path / "elsewhere.mp4"
    outside.write_bytes(b"preview bytes")
    _, subtitle = _artifacts(episode_root)

    with pytest.raises(PlanRecordError, match="stay inside the episode root"):
        _store(episode_root).stage(
            _plan(),
            editorial_master_id="c" * 64,
            winner_id="winner-1",
            tight_cut_id="tight-1",
            transaction_id=_TRANSACTION,
            timeline=_TIMELINE,
            preview_path=outside,
            subtitle_path=subtitle,
        )


def test_a_changed_artifact_is_caught_by_re_measuring(tmp_path) -> None:
    record = _staged(tmp_path)
    store = _store(tmp_path)
    store.verify_artifacts(record)

    (tmp_path / _STAGING / "preview.mp4").write_bytes(b"a different render")

    with pytest.raises(PlanRecordError, match="changed after the plan record"):
        store.verify_artifacts(record)


def test_inspection_hands_the_publish_gate_the_timeline_and_the_chapter_marks(tmp_path) -> None:
    record = _staged(tmp_path)
    write_plan_record(tmp_path / _STAGING / PLAN_RECORD_FILENAME, record)

    inspection = PlanRecordStore(tmp_path).inspect("20260901 蘇予昕")

    assert inspection.state == "ready"
    assert len(inspection.cuts) == 1
    cut = inspection.cuts[0]
    assert cut.plan_id == record.plan_id
    # 以前這一格是 `release_id`，而封存鏈從未跑過，所以發布線永遠查不到 timeline 名。
    assert cut.timeline == _TIMELINE.name
    assert cut.preview.duration_sec == 490.304
    assert [
        (component.t0, component.display)
        for component in cut.components
        if component.implementation_kind == "fullscreen_transition"
    ] == [(10.0, "開場之後"), (20.0, "轉折")]


def test_an_episode_with_no_records_reads_as_missing_not_as_an_error(tmp_path) -> None:
    inspection = PlanRecordStore(tmp_path).inspect("20260901 蘇予昕")

    assert inspection.state == "missing"
    assert inspection.error_code == "plan_record_missing"


def test_records_are_scoped_to_the_episode_that_asked(tmp_path) -> None:
    record = _staged(tmp_path)
    write_plan_record(tmp_path / _STAGING / PLAN_RECORD_FILENAME, record)
    store = PlanRecordStore(tmp_path)

    assert store.records("20260901 蘇予昕") == (record,)
    assert store.records("20260721 呂冠緯") == ()
    assert store.resolve(record.plan_id) == record
    assert store.resolve("plan-nobody") is None


def test_a_pre_adr_069_journal_still_reads_back_as_a_plan_record(tmp_path) -> None:
    """修修硬碟上那 6 支就是這個版本；詞彙瘦身不可以讓它們變成讀不到。"""

    record = _staged(tmp_path)
    legacy_payload = {
        "command_id": record.command_id,
        "run_id": record.run_id,
        "plan_id": record.plan_id,
        "status": "preview_ready",
        "transaction_id": record.transaction_id,
        "subtitle_sha256": record.subtitle.sha256,
        "candidate": {
            "candidate_id": "candidate-0123456789abcdef01234567",
            "episode_id": record.episode_id,
            "cut_id": record.cut_id,
            "format": record.format,
            "command_id": record.command_id,
            "run_id": record.run_id,
            "editorial_master_id": record.editorial_master_id,
            "winner_id": record.winner_id,
            "tight_cut_id": record.tight_cut_id,
            "director_acceptance_id": record.director_acceptance_id,
            "dp_acceptance_id": record.dp_acceptance_id,
            "visual_acceptance_id": record.visual_acceptance_id,
            "preview_ready_transaction_id": record.transaction_id,
            "preview": {
                "path": record.preview.path,
                "bytes": record.preview.bytes,
                "sha256": record.preview.sha256,
                "duration_sec": record.preview.duration_sec,
                "probe": [list(pair) for pair in record.preview.probe],
            },
            "subtitle": {
                "path": record.subtitle.path,
                "bytes": record.subtitle.bytes,
                "sha256": record.subtitle.sha256,
                "duration_sec": None,
                "probe": [],
            },
            "materialization_plan": {
                "plan_id": record.plan_id,
                "duration_sec": record.duration_sec,
                "events": [
                    {
                        "event_id": event.event_id,
                        "master_cue_ids": list(event.master_cue_ids),
                        "text_hash": event.text_hash,
                        "intent": event.intent,
                        "asset_ref": event.asset_ref,
                        "visual_status": event.visual_status,
                        "text": event.text,
                        "t0": event.t0,
                        "t1": event.t1,
                        "section_id": event.section_id,
                        "display": event.display,
                        "semantic_kind": event.semantic_kind,
                        "intentional_aroll": event.intentional_aroll,
                        "implementation_kind": event.implementation_kind,
                        "lane": event.lane,
                        "visual_placement": None,
                    }
                    for event in record.events
                ],
                "components": [
                    {
                        "component_id": component.component_id,
                        "event_id": component.event_id,
                        "semantic_kind": component.semantic_kind,
                        "implementation_kind": component.implementation_kind,
                        "lane": component.lane,
                        "display": component.display,
                        "t0": component.t0,
                        "t1": component.t1,
                        "asset_ref": component.asset_ref,
                    }
                    for component in record.components
                ],
            },
        },
    }
    path = tmp_path / _STAGING / PLAN_RECORD_FILENAME
    payload_bytes = json.dumps(
        legacy_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    path.write_text(
        json.dumps(
            {
                "schema": "nakama.finished-cut-materialization.v1",
                "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
                "payload": legacy_payload,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    lifted = read_plan_record_at(path)

    assert lifted is not None
    # v1 沒有記 timeline，所以留空——發布線看到空字串就知道要人補，
    # 看不到欄位只會當機。其餘事實一格不少。
    assert lifted.timeline == PlanTimeline(name="", uid="")
    assert lifted == plan_record(
        plan_id=record.plan_id,
        command_id=record.command_id,
        run_id=record.run_id,
        episode_id=record.episode_id,
        cut_id=record.cut_id,
        format=record.format,
        editorial_master_id=record.editorial_master_id,
        winner_id=record.winner_id,
        tight_cut_id=record.tight_cut_id,
        director_acceptance_id=record.director_acceptance_id,
        dp_acceptance_id=record.dp_acceptance_id,
        visual_acceptance_id=record.visual_acceptance_id,
        timeline="",
        timeline_uid="",
        transaction_id=record.transaction_id,
        duration_sec=record.duration_sec,
        preview=record.preview,
        subtitle=record.subtitle,
        events=record.events,
        components=record.components,
    )


def test_a_retired_projection_in_an_existing_record_still_reads_back(tmp_path) -> None:
    """reader 刻意比 writer 寬鬆：詞彙表瘦身不該讓既有紀錄變成讀不到。"""

    record = _staged(tmp_path)
    path = tmp_path / _STAGING / PLAN_RECORD_FILENAME
    write_plan_record(path, record)
    document = json.loads(path.read_bytes())
    for component in document["payload"]["components"]:
        component["semantic_kind"] = "supporting_title"
        component["implementation_kind"] = "supporting_title"
        component["lane"] = "supporting_title"
    payload_bytes = json.dumps(
        document["payload"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    document["payload_sha256"] = hashlib.sha256(payload_bytes).hexdigest()
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    reloaded = read_plan_record_at(path)

    assert reloaded is not None
    assert {component.lane for component in reloaded.components} == {"supporting_title"}


def test_an_incomplete_write_is_refused_rather_than_read_half_way(tmp_path) -> None:
    path = tmp_path / _STAGING / PLAN_RECORD_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_name(f".{path.name}.staging").write_text("{", encoding="utf-8")

    with pytest.raises(PlanRecordError, match="incomplete"):
        read_plan_record_at(path)


def test_a_read_only_store_refuses_to_stage(tmp_path) -> None:
    # Bridge 與發布線拿到的就是這種 store：讀得到紀錄，但不可能自己造一份。
    with pytest.raises(PlanRecordError, match="requires Resolve and probe seams"):
        PlanRecordStore(tmp_path).stage(
            _plan(),
            editorial_master_id="c" * 64,
            winner_id="winner-1",
            tight_cut_id="tight-1",
            transaction_id=_TRANSACTION,
            timeline=_TIMELINE,
            preview_path=tmp_path / "preview.mp4",
            subtitle_path=tmp_path / "review.srt",
        )
