from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from agents.brook.script_video.long_highlight_materializer import (
    LongHighlightMaterializationError,
    ResolveScriptingAdapter,
    apply_preview,
    commit_transaction,
    emit_recipes,
    project_recipes,
    rollback_transaction,
    supersede_stale_transaction,
    validate_projection,
)


class _FakeResolveAdapter:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.baseline = {"video_track1": [{"start": 0, "end": 300}], "audio": [{"start": 0}]}

    def snapshot_baseline(self, timeline_name: str, timeline_uid: str) -> dict[str, Any]:
        self.calls.append(f"snapshot:{timeline_uid}")
        return self.baseline

    def duplicate_swap(
        self,
        canonical_name: str,
        canonical_uid: str,
        work_name: str,
        backup_name: str,
    ) -> dict[str, dict[str, str]]:
        self.calls.append("duplicate-swap")
        return {
            "canonical": {"name": canonical_name, "uid": canonical_uid},
            "work": {"name": canonical_name, "uid": "work-uid"},
            "backup": {"name": backup_name, "uid": canonical_uid},
        }

    def apply_recipes(
        self,
        timeline_name: str,
        timeline_uid: str,
        broll_path: Path,
        titles_path: Path,
    ) -> dict[str, int]:
        self.calls.append(f"apply:{timeline_uid}")
        assert broll_path.is_file()
        assert titles_path.is_file()
        return {"broll": 2, "titles": 1}

    def render_preview(self, timeline_name: str, timeline_uid: str, output: Path) -> Path:
        self.calls.append(f"render:{timeline_uid}")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"preview")
        return output

    def probe_preview(self, output: Path) -> dict[str, Any]:
        self.calls.append("probe")
        return {"video_codec": "h264", "audio_codec": "aac", "duration_sec": 10.0}

    def rollback(self, transaction: dict[str, Any]) -> None:
        self.calls.append("rollback")

    def commit(self, transaction: dict[str, Any], *, keep_backup: bool) -> None:
        self.calls.append(f"commit:{keep_backup}")

    def timelines_equivalent(self, first_uid: str, second_uid: str) -> bool:
        self.calls.append(f"equivalent:{first_uid}:{second_uid}")
        return True


def _write_srt(path: Path, duration: int = 10) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"1\n00:00:00,000 --> 00:00:{duration:02d},000\nfixture\n", encoding="utf-8")


def _candidate(
    candidate_id: str,
    media_path: str,
    *,
    rendered: bool = False,
    render_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "candidate_id": candidate_id,
        "visual_summary": f"visual for {candidate_id}",
        "playable": True,
        "playable_evidence": {"duration_sec": 10.0},
    }
    if rendered:
        row["component"] = "punch_card_wide"
        row["render_params"] = render_params or {}
        row["preview_media"] = {"path": media_path}
    else:
        row["media"] = {"path": media_path}
    return row


def _selection(candidate_id: str, t0: float, t1: float) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "t0": t0,
        "t1": t1,
        "source_range": {"start_sec": 0.0, "end_sec": t1 - t0},
        "playable": True,
    }


def _state(episode: Path, cut_id: str = "value-L02") -> dict[str, Any]:
    tight = episode / "highlights" / "srt" / f"{cut_id}_tight.srt"
    _write_srt(tight)
    for relative in ("assets/fixed-stock.mp4", "assets/title.mov", "assets/transition.mov"):
        path = episode / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"readable fixture media")
    dp_events = {
        "stock-001": {
            "status": "approved",
            "data": {
                "id": "stock-001",
                "candidates": [_candidate("fixed-stock", "assets/fixed-stock.mp4")],
                "selections": [_selection("fixed-stock", 1.0, 3.0)],
                "implementation_kind": "stock_video",
                "mode": "fixed_authority",
                "target_lane": "broll_track2",
            },
        },
        "title-001": {
            "status": "approved",
            "data": {
                "id": "title-001",
                "candidates": [
                    _candidate(
                        "title-card",
                        "assets/title.mov",
                        rendered=True,
                        render_params={
                            "text": "兩行\n主標",
                            "tier": 1,
                            "style": "orange",
                            "pos_y": 0.58,
                        },
                    )
                ],
                "selections": [_selection("title-card", 3.2, 5.2)],
                "implementation_kind": "hero_title",
                "mode": "hyperframes",
                "target_lane": "title_track3",
            },
        },
        "transition-001": {
            "status": "approved",
            "data": {
                "id": "transition-001",
                "candidates": [
                    _candidate(
                        "transition-card",
                        "assets/transition.mov",
                        rendered=True,
                        render_params={"title": "下一章", "style": "paper", "show_sec": 2.0},
                    )
                ],
                "selections": [_selection("transition-card", 6.0, 8.0)],
                "implementation_kind": "transition_title",
                "mode": "hyperframes",
                "target_lane": "content_card_track4",
            },
        },
    }
    return {
        "schema_version": 1,
        "episode_id": "fixture",
        "status": "running",
        "source": {"duration_sec": 100.0},
        "stages": {
            "tighten": {"status": "approved"},
            "director": {"status": "approved", "events": {}},
            "dp": {"status": "approved", "events": dp_events},
            "visual": {
                "status": "approved",
                "events": {
                    event_id: {"status": "approved", "reason": "fixture reviewed"}
                    for event_id in dp_events
                },
            },
        },
        "winner": {"id": cut_id, "t_start": 100.0, "t_end": 110.0},
        "human": {"approved": True, "candidate_id": cut_id},
        "refs": {"tighten": str(tight)},
    }


def test_emit_recipes_drops_guest_namecard_that_overlaps_new_stock(
    tmp_path: Path,
) -> None:
    episode = tmp_path / "episode"
    state = _state(episode)
    tighten_dir = episode / "highlights" / "tighten"
    tighten_dir.mkdir(parents=True)
    (tighten_dir / "value-L02_broll.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "kind": "guest-namecard",
                        "slug": "guest-namecard",
                        "t0": 0.2,
                        "t1": 2.2,
                    },
                    {"kind": "video", "slug": "legacy-content", "t0": 8.0, "t1": 9.0},
                ]
            }
        ),
        encoding="utf-8",
    )

    result = emit_recipes(episode, "value-L02", state)

    broll = json.loads(Path(result["broll_path"]).read_text(encoding="utf-8"))["items"]
    titles = json.loads(Path(result["titles_path"]).read_text(encoding="utf-8"))["titles"]
    assert [row["kind"] for row in broll] == ["video", "concept"]
    assert broll[0]["media_path"] == "assets/fixed-stock.mp4"
    assert broll[1]["comp"] == "transition_title"
    assert len(titles) == 1
    assert titles[0]["text"] == "兩行\n主標"
    assert result["counts"] == {"broll": 2, "titles": 1, "structural": 0}


def test_projection_rejects_visual_stage_with_failed_event(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    state = _state(episode)
    state["stages"]["visual"]["status"] = "running"
    state["stages"]["visual"]["events"]["stock-001"] = {
        "status": "failed",
        "reason": "wrong footage",
    }

    with pytest.raises(LongHighlightMaterializationError, match="stage visual is not approved"):
        project_recipes(episode, "value-L02", state)


def test_projection_can_omit_one_final_qa_failed_supporting_title(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    state = _state(episode)
    state["materialization"] = {"omit_event_ids": ["title-001"]}

    projection = project_recipes(episode, "value-L02", state)

    assert projection["titles"] == []
    assert len(projection["broll"]) == 2


def test_projection_rejects_failed_event_even_if_visual_stage_claims_approved(
    tmp_path: Path,
) -> None:
    episode = tmp_path / "episode"
    state = _state(episode)
    state["stages"]["visual"]["events"]["stock-001"] = {
        "status": "failed",
        "reason": "wrong footage",
    }

    with pytest.raises(LongHighlightMaterializationError, match="visual event stock-001"):
        project_recipes(episode, "value-L02", state)


def test_projection_rejects_orphan_failed_visual_event(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    state = _state(episode)
    state["stages"]["visual"]["events"]["orphan-visual"] = {"status": "failed"}

    with pytest.raises(LongHighlightMaterializationError, match="visual event orphan-visual"):
        project_recipes(episode, "value-L02", state)


def test_projection_rejects_corrupt_photo_even_when_marked_playable(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    state = _state(episode)
    corrupt = episode / "assets" / "corrupt.jpg"
    corrupt.write_bytes(b"not an image")
    stock = state["stages"]["dp"]["events"]["stock-001"]["data"]
    stock["implementation_kind"] = "photo"
    stock["candidates"][0]["media"] = {"path": "assets/corrupt.jpg"}

    with pytest.raises(LongHighlightMaterializationError, match="image is not readable"):
        project_recipes(episode, "value-L02", state)


def test_validate_projection_is_read_only_and_returns_counts_and_paths(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    state = _state(episode)

    result = validate_projection(episode, "value-L02", state)

    assert result["status"] == "projection-valid"
    assert result["counts"] == {"broll": 2, "titles": 1, "structural": 0}
    assert result["broll_path"].endswith("value-L02_broll.json")
    assert result["titles_path"].endswith("value-L02_titles.json")
    assert not Path(result["broll_path"]).exists()
    assert not Path(result["titles_path"]).exists()


def test_projection_rejects_same_lane_overlap(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    state = _state(episode)
    stock = state["stages"]["dp"]["events"]["stock-001"]["data"]
    stock["selections"].append(_selection("fixed-stock", 2.5, 4.0))

    with pytest.raises(LongHighlightMaterializationError, match="overlap on broll_track2"):
        project_recipes(episode, "value-L02", state)


def test_projection_rejects_selection_outside_tight_cut(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    state = _state(episode)
    title = state["stages"]["dp"]["events"]["title-001"]["data"]
    title["selections"] = [_selection("title-card", 9.0, 11.0)]

    with pytest.raises(LongHighlightMaterializationError, match="outside tight cut"):
        project_recipes(episode, "value-L02", state)


def test_projection_accepts_real_adopted_multi_selection_shape(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    state = _state(episode)
    stock = state["stages"]["dp"]["events"]["stock-001"]["data"]
    candidates = []
    selections = []
    for index, (t0, t1, src0) in enumerate(((1.0, 2.0, 0.0), (2.0, 3.0, 4.0), (3.0, 4.0, 0.0))):
        candidate_id = f"stock-{index + 1}"
        relative = f"assets/{candidate_id}.mp4"
        (episode / relative).write_bytes(b"readable fixture media")
        candidate = _candidate(candidate_id, relative)
        candidate.pop("playable")
        selection = _selection(candidate_id, t0, t1)
        selection.pop("playable")
        selection["source_range"] = {"start_sec": src0, "end_sec": src0 + (t1 - t0)}
        candidates.append(candidate)
        selections.append(selection)
    stock["candidates"] = candidates
    stock["selections"] = selections
    stock.pop("t_start", None)
    stock.pop("t_end", None)

    projection = project_recipes(episode, "value-L02", state)

    stock_rows = [row for row in projection["broll"] if row["kind"] == "video"]
    assert [row["src_in"] for row in stock_rows] == [0.0, 4.0, 0.0]
    assert [row["t0"] for row in stock_rows] == [1.0, 2.0, 3.0]


def test_projection_rejects_explicit_unplayable_candidate(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    state = _state(episode)
    stock = state["stages"]["dp"]["events"]["stock-001"]["data"]
    stock["candidates"][0]["playable"] = False

    with pytest.raises(LongHighlightMaterializationError, match="media is not playable"):
        project_recipes(episode, "value-L02", state)


def test_cli_validate_runs_from_repo_root_without_writing_recipes(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    episode = tmp_path / "episode"
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(_state(episode)), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_long_highlight_materializer.py",
            "validate",
            str(episode),
            "--cut-id",
            "value-L02",
            "--state",
            str(state_path),
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "projection-valid"
    assert not (episode / "highlights" / "tighten" / "value-L02_titles.json").exists()


def test_cli_emit_recipes_writes_both_recipe_files(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    episode = tmp_path / "episode"
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(_state(episode)), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_long_highlight_materializer.py",
            "emit-recipes",
            str(episode),
            "--cut-id",
            "value-L02",
            "--state",
            str(state_path),
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert Path(payload["broll_path"]).is_file()
    assert Path(payload["titles_path"]).is_file()


def test_apply_preview_uses_duplicate_work_timeline_and_persists_recovery_state(
    tmp_path: Path,
) -> None:
    episode = tmp_path / "episode"
    state = _state(episode)
    adapter = _FakeResolveAdapter()
    transaction_path = tmp_path / "transaction.json"
    preview_path = tmp_path / "preview.mp4"

    result = apply_preview(
        episode,
        "value-L02",
        state,
        canonical_name="長2 - fixture（緊·導播）",
        canonical_uid="canonical-uid",
        preview_path=preview_path,
        transaction_path=transaction_path,
        adapter=adapter,
    )

    persisted = json.loads(transaction_path.read_text(encoding="utf-8"))
    assert result["status"] == "preview_ready"
    assert persisted["status"] == "preview_ready"
    assert persisted["canonical"]["uid"] == "canonical-uid"
    assert persisted["work"]["uid"] == "work-uid"
    assert persisted["backup"]["uid"] == "canonical-uid"
    assert persisted["preview"] == {
        "path": str(preview_path),
        "video_codec": "h264",
        "audio_codec": "aac",
        "duration_sec": 10.0,
    }
    assert adapter.calls == [
        "snapshot:canonical-uid",
        "duplicate-swap",
        "apply:work-uid",
        "snapshot:work-uid",
        "render:work-uid",
        "probe",
    ]


def test_apply_preview_rolls_back_and_records_failure_when_renderer_fails(tmp_path: Path) -> None:
    class FailingAdapter(_FakeResolveAdapter):
        def apply_recipes(
            self,
            timeline_name: str,
            timeline_uid: str,
            broll_path: Path,
            titles_path: Path,
        ) -> dict[str, int]:
            raise RuntimeError("renderer rejected an item")

    episode = tmp_path / "episode"
    state = _state(episode)
    adapter = FailingAdapter()
    transaction_path = tmp_path / "transaction.json"

    with pytest.raises(LongHighlightMaterializationError, match="renderer rejected an item"):
        apply_preview(
            episode,
            "value-L02",
            state,
            canonical_name="長2 - fixture（緊·導播）",
            canonical_uid="canonical-uid",
            preview_path=tmp_path / "preview.mp4",
            transaction_path=transaction_path,
            adapter=adapter,
        )

    persisted = json.loads(transaction_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "rolled_back"
    assert persisted["work"]["uid"] == "work-uid"
    assert adapter.calls[-1] == "rollback"


def test_commit_closes_persisted_transaction_without_deleting_backup(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    transaction_path = tmp_path / "transaction.json"
    adapter = _FakeResolveAdapter()
    apply_preview(
        episode,
        "value-L02",
        _state(episode),
        canonical_name="長2 - fixture（緊·導播）",
        canonical_uid="canonical-uid",
        preview_path=tmp_path / "preview.mp4",
        transaction_path=transaction_path,
        adapter=adapter,
    )

    result = commit_transaction(transaction_path, adapter=adapter)

    persisted = json.loads(transaction_path.read_text(encoding="utf-8"))
    assert result["status"] == "committed"
    assert persisted["status"] == "committed"
    assert persisted["backup"]["uid"] == "canonical-uid"
    assert adapter.calls[-1] == "commit:True"


def test_rollback_uses_persisted_identities_in_a_later_process(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    transaction_path = tmp_path / "transaction.json"
    apply_preview(
        episode,
        "value-L02",
        _state(episode),
        canonical_name="長2 - fixture（緊·導播）",
        canonical_uid="canonical-uid",
        preview_path=tmp_path / "preview.mp4",
        transaction_path=transaction_path,
        adapter=_FakeResolveAdapter(),
    )
    later_process_adapter = _FakeResolveAdapter()

    result = rollback_transaction(transaction_path, adapter=later_process_adapter)

    assert result["status"] == "rolled_back"
    assert result["work"]["uid"] == "work-uid"
    assert result["backup"]["uid"] == "canonical-uid"
    assert later_process_adapter.calls == ["rollback"]


def test_supersede_stale_transaction_requires_read_only_timeline_equivalence(
    tmp_path: Path,
) -> None:
    episode = tmp_path / "episode"
    transaction_path = tmp_path / "transaction.json"
    adapter = _FakeResolveAdapter()
    apply_preview(
        episode,
        "value-L02",
        _state(episode),
        canonical_name="長2 - fixture（緊·導播）",
        canonical_uid="canonical-uid",
        preview_path=tmp_path / "preview.mp4",
        transaction_path=transaction_path,
        adapter=adapter,
    )
    adapter.calls.clear()

    result = supersede_stale_transaction(transaction_path, adapter=adapter)

    assert result["status"] == "superseded"
    assert result["backup"]["uid"] == "canonical-uid"
    assert adapter.calls == ["equivalent:work-uid:canonical-uid"]


def test_supersede_stale_transaction_fails_closed_when_timelines_differ(tmp_path: Path) -> None:
    class DifferentAdapter(_FakeResolveAdapter):
        def timelines_equivalent(self, first_uid: str, second_uid: str) -> bool:
            return False

    episode = tmp_path / "episode"
    transaction_path = tmp_path / "transaction.json"
    apply_preview(
        episode,
        "value-L02",
        _state(episode),
        canonical_name="長2 - fixture（緊·導播）",
        canonical_uid="canonical-uid",
        preview_path=tmp_path / "preview.mp4",
        transaction_path=transaction_path,
        adapter=_FakeResolveAdapter(),
    )

    with pytest.raises(LongHighlightMaterializationError, match="not structurally equivalent"):
        supersede_stale_transaction(transaction_path, adapter=DifferentAdapter())

    assert json.loads(transaction_path.read_text(encoding="utf-8"))["status"] == "preview_ready"


def test_resolve_adapter_duplicate_swap_can_restore_original_by_persisted_uid(
    tmp_path: Path,
) -> None:
    class Timeline:
        def __init__(self, project: Any, name: str, uid: str) -> None:
            self.project = project
            self.name = name
            self.uid = uid

        def GetName(self) -> str:
            return self.name

        def GetUniqueId(self) -> str:
            return self.uid

        def SetName(self, name: str) -> bool:
            self.name = name
            return True

        def DuplicateTimeline(self, name: str) -> Any:
            duplicate = Timeline(self.project, name, "work-uid")
            self.project.timelines.append(duplicate)
            return duplicate

    class Pool:
        def __init__(self, project: Any) -> None:
            self.project = project

        def DeleteTimelines(self, timelines: list[Any]) -> bool:
            self.project.timelines = [row for row in self.project.timelines if row not in timelines]
            return True

    class Project:
        def __init__(self) -> None:
            self.timelines: list[Any] = []
            self.pool = Pool(self)

        def GetName(self) -> str:
            return "episode"

        def GetTimelineCount(self) -> int:
            return len(self.timelines)

        def GetTimelineByIndex(self, index: int) -> Any:
            return self.timelines[index - 1]

        def GetMediaPool(self) -> Any:
            return self.pool

    class Manager:
        def __init__(self, project: Any) -> None:
            self.project = project

        def GetCurrentProject(self) -> Any:
            return self.project

        def SaveProject(self) -> bool:
            return True

    class Resolve:
        def __init__(self, project: Any) -> None:
            self.manager = Manager(project)

        def GetProjectManager(self) -> Any:
            return self.manager

    project = Project()
    original = Timeline(project, "長2 - fixture（緊·導播）", "canonical-uid")
    project.timelines.append(original)
    adapter = ResolveScriptingAdapter(tmp_path / "episode", resolve=Resolve(project))

    identities = adapter.duplicate_swap(
        original.GetName(), "canonical-uid", "work-name", "backup-name"
    )
    adapter.rollback({"status": "open", **identities})

    assert [(row.GetName(), row.GetUniqueId()) for row in project.timelines] == [
        ("長2 - fixture（緊·導播）", "canonical-uid")
    ]


def test_cli_exposes_explicit_transaction_commands_without_connecting_resolve() -> None:
    repo_root = Path(__file__).resolve().parents[3]

    result = subprocess.run(
        [sys.executable, "scripts/run_long_highlight_materializer.py", "--help"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "apply-preview" in result.stdout
    assert "commit" in result.stdout
    assert "rollback" in result.stdout
    assert "supersede-stale" in result.stdout


def test_supersede_can_normalize_legacy_open_metadata_with_explicit_uids(tmp_path: Path) -> None:
    transaction_path = tmp_path / "legacy.transaction.json"
    transaction_path.write_text(
        json.dumps({"status": "open", "request_id": "old-r-c851"}), encoding="utf-8"
    )
    adapter = _FakeResolveAdapter()

    result = supersede_stale_transaction(
        transaction_path,
        adapter=adapter,
        active={"name": "長2 - fixture（緊·導播）", "uid": "active-uid"},
        backup={"name": "長2 - fixture（舊備份）", "uid": "backup-uid"},
    )

    assert result["status"] == "superseded"
    assert result["work"]["uid"] == "active-uid"
    assert result["backup"]["uid"] == "backup-uid"
    assert result["request_id"] == "old-r-c851"
    assert adapter.calls == ["equivalent:active-uid:backup-uid"]


def test_apply_preview_rejects_unapproved_state_before_resolve_mutation(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    state = _state(episode)
    state["stages"]["visual"]["status"] = "running"
    adapter = _FakeResolveAdapter()
    transaction_path = tmp_path / "transaction.json"

    with pytest.raises(LongHighlightMaterializationError, match="stage visual is not approved"):
        apply_preview(
            episode,
            "value-L02",
            state,
            canonical_name="長2 - fixture（緊·導播）",
            canonical_uid="canonical-uid",
            preview_path=tmp_path / "preview.mp4",
            transaction_path=transaction_path,
            adapter=adapter,
        )

    assert adapter.calls == []
    assert not transaction_path.exists()


def test_apply_preview_requests_rollback_when_duplicate_swap_fails(tmp_path: Path) -> None:
    class DuplicateFailureAdapter(_FakeResolveAdapter):
        def duplicate_swap(
            self,
            canonical_name: str,
            canonical_uid: str,
            work_name: str,
            backup_name: str,
        ) -> dict[str, dict[str, str]]:
            self.calls.append("duplicate-failed")
            raise RuntimeError("duplicate failed")

    episode = tmp_path / "episode"
    adapter = DuplicateFailureAdapter()
    transaction_path = tmp_path / "transaction.json"

    with pytest.raises(LongHighlightMaterializationError, match="duplicate failed"):
        apply_preview(
            episode,
            "value-L02",
            _state(episode),
            canonical_name="長2 - fixture（緊·導播）",
            canonical_uid="canonical-uid",
            preview_path=tmp_path / "preview.mp4",
            transaction_path=transaction_path,
            adapter=adapter,
        )

    assert adapter.calls == ["snapshot:canonical-uid", "duplicate-failed", "rollback"]
    assert json.loads(transaction_path.read_text(encoding="utf-8"))["status"] == "rolled_back"


def test_apply_preview_rolls_back_unplayable_or_wrong_codec_preview(tmp_path: Path) -> None:
    class WrongCodecAdapter(_FakeResolveAdapter):
        def probe_preview(self, output: Path) -> dict[str, Any]:
            self.calls.append("probe")
            return {"video_codec": "prores", "audio_codec": "pcm", "duration_sec": 10.0}

    episode = tmp_path / "episode"
    adapter = WrongCodecAdapter()

    with pytest.raises(LongHighlightMaterializationError, match="not H.264"):
        apply_preview(
            episode,
            "value-L02",
            _state(episode),
            canonical_name="長2 - fixture（緊·導播）",
            canonical_uid="canonical-uid",
            preview_path=tmp_path / "preview.mp4",
            transaction_path=tmp_path / "transaction.json",
            adapter=adapter,
        )

    assert adapter.calls[-1] == "rollback"


def test_apply_preview_rolls_back_if_editorial_or_audio_baseline_changes(tmp_path: Path) -> None:
    class BaselineChangingAdapter(_FakeResolveAdapter):
        def snapshot_baseline(self, timeline_name: str, timeline_uid: str) -> dict[str, Any]:
            baseline = super().snapshot_baseline(timeline_name, timeline_uid)
            if timeline_uid == "work-uid":
                return {**baseline, "audio": [{"start": 1}]}
            return baseline

    episode = tmp_path / "episode"
    adapter = BaselineChangingAdapter()

    with pytest.raises(LongHighlightMaterializationError, match="Editorial Master track 1"):
        apply_preview(
            episode,
            "value-L02",
            _state(episode),
            canonical_name="長2 - fixture（緊·導播）",
            canonical_uid="canonical-uid",
            preview_path=tmp_path / "preview.mp4",
            transaction_path=tmp_path / "transaction.json",
            adapter=adapter,
        )

    assert adapter.calls[-1] == "rollback"


def test_apply_preview_wraps_photo_as_exact_h264_video_without_using_photo_renderer(
    tmp_path: Path,
) -> None:
    class InspectingAdapter(_FakeResolveAdapter):
        recipe: dict[str, Any] | None = None

        def apply_recipes(
            self,
            timeline_name: str,
            timeline_uid: str,
            broll_path: Path,
            titles_path: Path,
        ) -> dict[str, int]:
            self.calls.append(f"apply:{timeline_uid}")
            self.recipe = json.loads(broll_path.read_text(encoding="utf-8"))["items"][0]
            return {"broll": 2, "titles": 1}

    episode = tmp_path / "episode"
    state = _state(episode)
    photo_path = episode / "assets" / "odd-photo.jpg"
    Image.new("RGB", (101, 99), (20, 40, 60)).save(photo_path)
    stock = state["stages"]["dp"]["events"]["stock-001"]["data"]
    stock["implementation_kind"] = "photo"
    stock["candidates"][0]["media"] = {"path": "assets/odd-photo.jpg"}
    stock["selections"] = [_selection("fixed-stock", 1.0, 3.2)]
    adapter = InspectingAdapter()

    apply_preview(
        episode,
        "value-L02",
        state,
        canonical_name="長2 - fixture（緊·導播）",
        canonical_uid="canonical-uid",
        preview_path=tmp_path / "preview.mp4",
        transaction_path=tmp_path / "transaction.json",
        adapter=adapter,
    )

    assert adapter.recipe is not None
    assert adapter.recipe["kind"] == "video"
    materialization = adapter.recipe["visual_materialization"]
    assert materialization["implementation_kind"] == "photo"
    assert materialization["photo_source"]["path"] == "assets/odd-photo.jpg"
    derived = episode / materialization["media"]["path"]
    assert derived.is_relative_to(
        episode
        / "highlights"
        / "long-orchestrator-v2"
        / "value-L02"
        / "materialization"
        / "photo-containers"
    )
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,width,height,r_frame_rate,pix_fmt:format=duration",
            "-of",
            "json",
            str(derived),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    media = json.loads(probe.stdout)
    stream = media["streams"][0]
    assert stream["codec_name"] == "h264"
    assert stream["pix_fmt"] == "yuv420p"
    assert stream["r_frame_rate"] == "30/1"
    assert stream["width"] % 2 == 0 and stream["height"] % 2 == 0
    assert float(media["format"]["duration"]) == pytest.approx(66 / 30, abs=0.001)


def test_validate_photo_projection_remains_read_only(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    state = _state(episode)
    photo_path = episode / "assets" / "photo.png"
    Image.new("RGB", (100, 100), (20, 40, 60)).save(photo_path)
    stock = state["stages"]["dp"]["events"]["stock-001"]["data"]
    stock["implementation_kind"] = "photo"
    stock["candidates"][0]["media"] = {"path": "assets/photo.png"}

    result = validate_projection(episode, "value-L02", state)

    assert result["status"] == "projection-valid"
    assert not (
        episode
        / "highlights"
        / "long-orchestrator-v2"
        / "value-L02"
        / "materialization"
        / "photo-containers"
    ).exists()


def test_emit_photo_container_is_idempotent_when_source_is_unchanged(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    state = _state(episode)
    photo_path = episode / "assets" / "photo.png"
    Image.new("RGB", (100, 100), (20, 40, 60)).save(photo_path)
    stock = state["stages"]["dp"]["events"]["stock-001"]["data"]
    stock["implementation_kind"] = "photo"
    stock["candidates"][0]["media"] = {"path": "assets/photo.png"}

    first = emit_recipes(episode, "value-L02", state)
    row = json.loads(Path(first["broll_path"]).read_text(encoding="utf-8"))["items"][0]
    derived = episode / row["visual_materialization"]["media"]["path"]
    first_mtime = derived.stat().st_mtime_ns
    second = emit_recipes(episode, "value-L02", state)

    assert second["counts"] == first["counts"]
    assert derived.stat().st_mtime_ns == first_mtime
