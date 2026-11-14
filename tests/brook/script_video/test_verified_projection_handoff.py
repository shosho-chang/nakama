from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

from agents.brook.script_video.editorial_master import EditorialMasterContractError
from agents.brook.script_video.subtitle_handoff import (
    Stage5SubtitleContractError,
    Stage5SubtitleRequest,
    Stage5SubtitleSelection,
    select_stage5_subtitle,
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _degraded_release_fixture(
    root: Path,
    *,
    cue_count: int = 2630,
    actual_cue_count: int | None = None,
    major_component_count: int = 32,
    major_audio_reviewed_count: int | None = None,
    nonmajor_retained_original_count: int = 23,
) -> Path:
    actual_cue_count = cue_count if actual_cue_count is None else actual_cue_count
    major_audio_reviewed_count = (
        major_component_count if major_audio_reviewed_count is None else major_audio_reviewed_count
    )
    release_root = root / "subtitle-v2" / "degraded-audio-release-v1"
    release_dir = release_root / "release"
    release_dir.mkdir(parents=True)
    blocks = []
    for number in range(1, actual_cue_count + 1):
        start_ms = number * 2000
        end_ms = start_ms + 1000

        def stamp(value: int) -> str:
            return (
                f"{value // 3_600_000:02d}:{value // 60_000 % 60:02d}:"
                f"{value // 1000 % 60:02d},{value % 1000:03d}"
            )

        blocks.append(f"{number}\n{stamp(start_ms)} --> {stamp(end_ms)}\nrelease cue {number}")
    srt_bytes = ("\n\n".join(blocks) + "\n").encode()
    srt_path = release_dir / "release-v1-corrected.srt"
    srt_path.write_bytes(srt_bytes)
    ledger = {
        "schema_version": 1,
        "contract": "podcast-subtitle-v2-degraded-audio-release-v1",
        "episode_id": "episode-degraded",
        "provenance_status": "degraded_dual_asr_major_complete_not_full_v2_checkpoint",
        "output_srt_sha256": _sha256(srt_bytes),
        "major_component_count": major_component_count,
        "major_audio_reviewed_count": major_audio_reviewed_count,
        "nonmajor_retained_original_count": nonmajor_retained_original_count,
        "cue_count": cue_count,
        "non_positive_duration_count": 0,
        "overlap_count": 0,
    }
    ledger_bytes = _canonical_json(ledger) + b"\n"
    ledger_path = release_dir / "release-v1-ledger.json"
    ledger_path.write_bytes(ledger_bytes)
    manifest = {
        "schema_version": 1,
        "contract": "podcast-subtitle-v2-degraded-audio-release-export-v1",
        "episode_id": "episode-degraded",
        "provenance_status": "degraded_dual_asr_major_complete_not_full_v2_checkpoint",
        "canonical_release_srt": "release/release-v1-corrected.srt",
        "canonical_release_srt_sha256": _sha256(srt_bytes),
        "file_count": 2,
        "files": [
            {
                "path": "release/release-v1-corrected.srt",
                "sha256": _sha256(srt_bytes),
                "size_bytes": len(srt_bytes),
            },
            {
                "path": "release/release-v1-ledger.json",
                "sha256": _sha256(ledger_bytes),
                "size_bytes": len(ledger_bytes),
            },
        ],
    }
    manifest_bytes = _canonical_json(manifest) + b"\n"
    manifest_path = release_root / "EXPORT-MANIFEST.json"
    manifest_path.write_bytes(manifest_bytes)
    relative_root = "subtitle-v2/degraded-audio-release-v1"
    handoff = {
        "schema_version": 1,
        "contract": "podcast-subtitle-v2-stage5-degraded-dual-asr-handoff-v1",
        "episode_id": "episode-degraded",
        "provenance_status": "degraded_dual_asr_major_complete_not_full_v2_checkpoint",
        "release_srt": {
            "path": f"{relative_root}/release/release-v1-corrected.srt",
            "sha256": _sha256(srt_bytes),
            "size_bytes": len(srt_bytes),
        },
        "release_ledger": {
            "path": f"{relative_root}/release/release-v1-ledger.json",
            "sha256": _sha256(ledger_bytes),
            "size_bytes": len(ledger_bytes),
        },
        "export_manifest": {
            "path": f"{relative_root}/EXPORT-MANIFEST.json",
            "sha256": _sha256(manifest_bytes),
            "size_bytes": len(manifest_bytes),
        },
        "gates": {
            "major_component_count": major_component_count,
            "major_audio_reviewed_count": major_audio_reviewed_count,
            "nonmajor_retained_original_count": nonmajor_retained_original_count,
            "cue_count": cue_count,
            "non_positive_duration_count": 0,
            "overlap_count": 0,
            "byte_identical_rerun": True,
        },
    }
    handoff_path = release_root / "STAGE5-HANDOFF.json"
    handoff_path.write_text(json.dumps(handoff, ensure_ascii=False, indent=2), encoding="utf-8")
    return handoff_path.relative_to(root)


def _memo_dual_audit_release_fixture(
    root: Path,
    *,
    cue_count: int = 7,
    actual_cue_count: int | None = None,
    major_component_count: int = 4,
    major_audio_reviewed_count: int | None = None,
    nonmajor_retained_original_count: int = 2,
    episode_id: str | None = None,
    relative_root: str = "subtitle-release/memo-dual-audit-v1",
    srt_text: str | None = None,
) -> Path:
    """寫一份 hash 互綁的 Memo Dual-Audit release；``srt_text`` 給定時用它當 release.srt。"""
    if srt_text is not None:
        cue_count = actual_cue_count = srt_text.count(" --> ")
    episode_id = root.name if episode_id is None else episode_id
    actual_cue_count = cue_count if actual_cue_count is None else actual_cue_count
    major_audio_reviewed_count = (
        major_component_count if major_audio_reviewed_count is None else major_audio_reviewed_count
    )
    release_root = root / relative_root
    release_root.mkdir(parents=True)
    blocks = []
    for number in range(1, actual_cue_count + 1):
        start_ms = number * 2000
        end_ms = start_ms + 1000

        def stamp(value: int) -> str:
            return (
                f"{value // 3_600_000:02d}:{value // 60_000 % 60:02d}:"
                f"{value // 1000 % 60:02d},{value % 1000:03d}"
            )

        blocks.append(f"{number}\n{stamp(start_ms)} --> {stamp(end_ms)}\nofficial cue {number}")
    srt_bytes = ("\n\n".join(blocks) + "\n").encode() if srt_text is None else srt_text.encode()
    srt_path = release_root / "release.srt"
    srt_path.write_bytes(srt_bytes)
    input_roles = {
        "normalized_audio",
        "normalized_handoff",
        "memo_srt",
        "memo_recognition_evidence",
        "memo_recognition_acceptance",
        "memo_cue_acceptance",
        "text_audit_a",
        "text_audit_b",
        "base_corrected_srt",
        "base_consensus_ledger",
        "base_needs_audio",
        "arbitration",
        "text_corrected_srt",
        "text_arbitration_ledger",
        "unresolved_components",
        "audio_decisions",
    }
    for role in input_roles:
        input_path = root / "evidence" / f"{role}.bin"
        input_path.parent.mkdir(parents=True, exist_ok=True)
        input_path.write_bytes(role.encode())
    ledger = {
        "schema_version": 1,
        "contract": "podcast-subtitle-memo-dual-audit-release-v1",
        "policy_version": "memo-dual-audit-release-v1",
        "episode_id": episode_id,
        "status": "complete",
        "inputs": {
            role: {
                "path": f"evidence/{role}.bin",
                "sha256": _sha256(role.encode()),
                "size_bytes": len(role),
            }
            for role in input_roles
        },
        "normalized_audio_sha256": "a" * 64,
        "memo_srt_sha256": "b" * 64,
        "text_audit": {
            "independent_agent_count": 2,
            "agents": ["agent-a", "agent-b"],
            "cue_coverage_count": cue_count,
            "complete_coverage": True,
            "fresh_arbitration_replay": True,
            "text_ledger_sha256": "c" * 64,
            "unresolved_components_sha256": "d" * 64,
        },
        "audio_audit": {
            "major_component_count": major_component_count,
            "major_audio_reviewed_count": major_audio_reviewed_count,
            "accepted_major_component_count": 0,
            "retained_major_component_count": major_audio_reviewed_count,
            "nonmajor_retained_original_count": nonmajor_retained_original_count,
            "changed_cue_count": 0,
            "changed_cue_ids": [],
            "retained_major": [
                {"component_id": f"major-{index}"} for index in range(major_audio_reviewed_count)
            ],
            "evidence": [
                {"component_id": f"major-{index}"} for index in range(major_component_count)
            ],
        },
        "release_policy": {
            "primary_text_authority": "accepted Memo large-v2",
            "text_correction": "two independent audits plus strict arbitration",
            "major_risk_audio": "Faster plus Qwen dual-ASR evidence required",
            "major_conflict": "retain Memo text",
            "nonmajor_unresolved": "retain Memo text",
        },
        "cue_count": cue_count,
        "non_positive_duration_count": 0,
        "overlap_count": 0,
        "byte_identical_rerun": True,
        "release_srt": {
            "path": "release.srt",
            "sha256": _sha256(srt_bytes),
            "size_bytes": len(srt_bytes),
        },
    }
    ledger_bytes = _canonical_json(ledger) + b"\n"
    ledger_path = release_root / "release-ledger.json"
    ledger_path.write_bytes(ledger_bytes)
    manifest = {
        "schema_version": 1,
        "contract": "podcast-subtitle-memo-dual-audit-release-export-v1",
        "episode_id": episode_id,
        "canonical_release_srt": "release.srt",
        "canonical_release_srt_sha256": _sha256(srt_bytes),
        "release_ledger": "release-ledger.json",
        "release_ledger_sha256": _sha256(ledger_bytes),
        "file_count": 2,
        "files": [
            {
                "path": "release.srt",
                "sha256": _sha256(srt_bytes),
                "size_bytes": len(srt_bytes),
            },
            {
                "path": "release-ledger.json",
                "sha256": _sha256(ledger_bytes),
                "size_bytes": len(ledger_bytes),
            },
        ],
    }
    manifest_bytes = _canonical_json(manifest) + b"\n"
    manifest_path = release_root / "export-manifest.json"
    manifest_path.write_bytes(manifest_bytes)
    handoff = {
        "schema_version": 1,
        "contract": "podcast-subtitle-stage5-memo-dual-audit-handoff-v1",
        "episode_id": episode_id,
        "release_srt": {
            "path": "release.srt",
            "sha256": _sha256(srt_bytes),
            "size_bytes": len(srt_bytes),
        },
        "release_ledger": {
            "path": "release-ledger.json",
            "sha256": _sha256(ledger_bytes),
            "size_bytes": len(ledger_bytes),
        },
        "export_manifest": {
            "path": "export-manifest.json",
            "sha256": _sha256(manifest_bytes),
            "size_bytes": len(manifest_bytes),
        },
        "gates": {
            "major_component_count": major_component_count,
            "major_audio_reviewed_count": major_audio_reviewed_count,
            "nonmajor_retained_original_count": nonmajor_retained_original_count,
            "cue_count": cue_count,
            "non_positive_duration_count": 0,
            "overlap_count": 0,
            "byte_identical_rerun": True,
        },
    }
    handoff_path = release_root / "STAGE5-HANDOFF.json"
    handoff_path.write_text(
        json.dumps(handoff, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return handoff_path.relative_to(root)


def test_official_release_is_the_default_selection(
    tmp_path: Path,
) -> None:
    handoff_path = _memo_dual_audit_release_fixture(tmp_path)

    selected = Stage5SubtitleRequest().open(tmp_path)

    assert handoff_path == Path("subtitle-release/memo-dual-audit-v1/STAGE5-HANDOFF.json")
    assert selected.mode == "memo-dual-audit-v1"
    assert selected.srt_path.name == "release.srt"
    assert selected.identity()["subtitle_srt_sha256"] == _sha256(selected.srt_path.read_bytes())


def test_official_release_explicit_episode_local_override(tmp_path: Path) -> None:
    handoff_path = _memo_dual_audit_release_fixture(
        tmp_path,
        relative_root="alternate/release",
    )

    selected = Stage5SubtitleRequest(
        subtitle_release_handoff=handoff_path,
    ).open(tmp_path)

    assert selected.mode == "memo-dual-audit-v1"
    assert selected.srt_path == tmp_path / "alternate/release/release.srt"


# 絕對路徑要照這台機器的語意組：POSIX 上 "C:/outside.json" 只是個相對路徑，
# 會落在 episode 目錄裡，測到的就不是「越界」而是「檔案不存在」。
@pytest.mark.parametrize("value", ["../outside.json", Path(Path(__file__).anchor) / "outside.json"])
def test_official_release_override_rejects_path_escape(
    tmp_path: Path,
    value: str | Path,
) -> None:
    with pytest.raises(Stage5SubtitleContractError, match="relative|escapes"):
        Stage5SubtitleRequest(subtitle_release_handoff=value).open(tmp_path)


def test_official_release_accepts_non_2630_episode(tmp_path: Path) -> None:
    _memo_dual_audit_release_fixture(
        tmp_path,
        cue_count=11,
        major_component_count=0,
        nonmajor_retained_original_count=0,
    )

    selected = Stage5SubtitleRequest().open(tmp_path)

    assert selected.mode == "memo-dual-audit-v1"


def test_official_release_copy_to_another_episode_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "episode-A"
    target = tmp_path / "episode-B"
    source.mkdir()
    target.mkdir()
    _memo_dual_audit_release_fixture(source)
    shutil.copytree(
        source / "subtitle-release",
        target / "subtitle-release",
    )

    with pytest.raises(Stage5SubtitleContractError, match="episode directory"):
        Stage5SubtitleRequest().open(target)


def test_official_release_requires_every_declared_episode_input(tmp_path: Path) -> None:
    _memo_dual_audit_release_fixture(tmp_path)
    evidence = tmp_path / "evidence" / "normalized_audio.bin"
    evidence.rename(evidence.with_suffix(".missing"))

    with pytest.raises(Stage5SubtitleContractError, match="missing or unreadable"):
        Stage5SubtitleRequest().open(tmp_path)


@pytest.mark.parametrize("artifact", ["release_srt", "release_ledger", "export_manifest"])
def test_official_release_rejects_artifact_tamper(
    tmp_path: Path,
    artifact: str,
) -> None:
    handoff_relative = _memo_dual_audit_release_fixture(tmp_path)
    handoff_path = tmp_path / handoff_relative
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    target = handoff_path.parent / handoff[artifact]["path"]
    target.write_bytes(target.read_bytes() + b"tamper")

    with pytest.raises(Stage5SubtitleContractError, match="hash or size mismatch"):
        Stage5SubtitleRequest().open(tmp_path)


def test_official_release_rejects_episode_path_escape(tmp_path: Path) -> None:
    handoff_relative = _memo_dual_audit_release_fixture(tmp_path)
    handoff_path = tmp_path / handoff_relative
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    handoff["release_srt"]["path"] = "../outside.srt"
    handoff_path.write_text(json.dumps(handoff), encoding="utf-8")

    with pytest.raises(Stage5SubtitleContractError, match="escapes"):
        Stage5SubtitleRequest().open(tmp_path)


@pytest.mark.parametrize("artifact", ["release_ledger", "export_manifest"])
def test_official_release_rejects_cross_artifact_episode_mismatch(
    tmp_path: Path,
    artifact: str,
) -> None:
    handoff_relative = _memo_dual_audit_release_fixture(tmp_path)
    handoff_path = tmp_path / handoff_relative
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    target_path = handoff_path.parent / handoff[artifact]["path"]
    payload = json.loads(target_path.read_text(encoding="utf-8"))
    payload["episode_id"] = "wrong-episode"
    payload_bytes = _canonical_json(payload) + b"\n"
    target_path.write_bytes(payload_bytes)
    handoff[artifact]["sha256"] = _sha256(payload_bytes)
    handoff[artifact]["size_bytes"] = len(payload_bytes)
    if artifact == "release_ledger":
        manifest_path = handoff_path.parent / handoff["export_manifest"]["path"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entry = next(item for item in manifest["files"] if item["path"] == "release-ledger.json")
        entry["sha256"] = _sha256(payload_bytes)
        entry["size_bytes"] = len(payload_bytes)
        manifest_bytes = _canonical_json(manifest) + b"\n"
        manifest_path.write_bytes(manifest_bytes)
        handoff["export_manifest"]["sha256"] = _sha256(manifest_bytes)
        handoff["export_manifest"]["size_bytes"] = len(manifest_bytes)
    handoff_path.write_text(json.dumps(handoff), encoding="utf-8")

    with pytest.raises(Stage5SubtitleContractError, match="episode"):
        Stage5SubtitleRequest().open(tmp_path)


def test_official_release_rejects_incomplete_major_coverage(tmp_path: Path) -> None:
    _memo_dual_audit_release_fixture(
        tmp_path,
        major_component_count=4,
        major_audio_reviewed_count=3,
    )

    with pytest.raises(Stage5SubtitleContractError, match="major audio coverage"):
        Stage5SubtitleRequest().open(tmp_path)


def test_official_release_rejects_actual_cue_count_mismatch(tmp_path: Path) -> None:
    _memo_dual_audit_release_fixture(tmp_path, cue_count=7, actual_cue_count=6)

    with pytest.raises(Stage5SubtitleContractError, match="actual SRT metrics drift"):
        Stage5SubtitleRequest().open(tmp_path)


def test_official_release_replays_same_serializable_identity(tmp_path: Path) -> None:
    _memo_dual_audit_release_fixture(tmp_path)
    request = Stage5SubtitleRequest()

    first = request.open(tmp_path)
    second = request.open(tmp_path)

    assert first.identity() == second.identity()
    assert set(first.identity()) == {
        "subtitle_mode",
        "episode_id",
        "subtitle_release_handoff",
        "subtitle_release_handoff_sha256",
        "release_ledger",
        "release_ledger_sha256",
        "export_manifest",
        "export_manifest_sha256",
        "subtitle_srt_sha256",
    }


@pytest.mark.parametrize(
    "request_case",
    [
        Stage5SubtitleRequest(
            subtitle_release_handoff="official.json",
            legacy_v1=True,
        ),
        Stage5SubtitleRequest(
            subtitle_release_handoff="official.json",
            degraded_release_handoff="degraded.json",
        ),
    ],
)
def test_official_release_is_mutually_exclusive_with_forensic_modes(
    tmp_path: Path,
    request_case: Stage5SubtitleRequest,
) -> None:
    with pytest.raises(Stage5SubtitleContractError, match="cannot be combined"):
        request_case.open(tmp_path)


def test_resolve_display_copy_strips_fillers_and_leaves_official_release_intact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _memo_dual_audit_release_fixture(tmp_path)
    selected = Stage5SubtitleRequest().open(tmp_path)
    scripts = Path(__file__).resolve().parents[3] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    sys.modules.pop("build_resolve_project", None)
    import build_resolve_project

    release_before = selected.srt_path.read_bytes()
    versioned = build_resolve_project._versioned_srt(tmp_path, subtitle=selected)

    # Hash-bound 顯示副本 = release 文字套語助詞清理（修修 2026-09-03）。
    # 不再是 byte-exact 複製：標點／空隙仍然不碰，但遲疑語助詞要刪。
    # release.srt 本體永遠不動——證據鏈靠它。
    from shared.subtitle_finalize import filler_only, parse_srt_text, strip_fillers

    assert selected.srt_path.read_bytes() == release_before
    source_cues = parse_srt_text(selected.srt_path.read_text(encoding="utf-8-sig"))
    expected = [
        (start, end, strip_fillers(text))
        for start, end, text in source_cues
        if not filler_only(text) and strip_fillers(text)
    ]
    assert parse_srt_text(versioned.read_text(encoding="utf-8-sig")) == expected


def test_resolve_accepts_stage5_handoff_but_highlight_requires_editorial_master(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handoff_relative = _memo_dual_audit_release_fixture(
        tmp_path,
        relative_root="alternate/release",
    )
    scripts = Path(__file__).resolve().parents[3] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    sys.modules.pop("build_resolve_project", None)
    sys.modules.pop("run_highlight_cut", None)
    import build_resolve_project
    import run_highlight_cut

    parsed = build_resolve_project._parse_args(
        [str(tmp_path), "--subtitle-release-handoff", str(handoff_relative)]
    )
    assert parsed.subtitle_request == Stage5SubtitleRequest(
        subtitle_release_handoff=str(handoff_relative)
    )
    with pytest.raises(SystemExit) as stopped:
        run_highlight_cut.main(
            [
                str(tmp_path),
                "--mining-input",
                "--subtitle-release-handoff",
                str(handoff_relative),
            ]
        )
    assert stopped.value.code == 2


def test_resolve_dry_run_uses_default_official_release_and_exact_srt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _memo_dual_audit_release_fixture(tmp_path)
    scripts = Path(__file__).resolve().parents[3] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    sys.modules.pop("build_resolve_project", None)
    import build_resolve_project

    video = tmp_path / "program.mp4"
    video.write_bytes(b"test")
    monkeypatch.setattr(build_resolve_project, "find_main_video", lambda *_args: video)
    monkeypatch.setattr(
        build_resolve_project,
        "_probe",
        lambda _path: {
            "fps": 30.0,
            "width": 1920,
            "height": 1080,
            "duration": 1.0,
        },
    )
    monkeypatch.setattr(
        build_resolve_project,
        "connect_resolve",
        lambda: (_ for _ in ()).throw(AssertionError("dry-run touched Resolve")),
    )

    plan = build_resolve_project.build_project(tmp_path, dry_run=True)
    selected = Stage5SubtitleRequest().open(tmp_path)

    assert plan["subtitle_mode"] == "memo-dual-audit-v1"
    assert plan["subtitle_srt_sha256"] == _sha256(selected.srt_path.read_bytes())
    assert Path(plan["subtitle"]).read_bytes() == selected.srt_path.read_bytes()


class _ExistingTimeline:
    def __init__(self, name: str) -> None:
        self._name = name

    def GetName(self) -> str:
        return self._name


class _ExistingProject:
    def __init__(self, name: str) -> None:
        self._name = name
        self._timeline = _ExistingTimeline(name)

    def GetName(self) -> str:
        return self._name

    def GetTimelineCount(self) -> int:
        return 1

    def GetTimelineByIndex(self, _index: int) -> _ExistingTimeline:
        return self._timeline


class _ExistingProjectManager:
    def __init__(self, project: _ExistingProject) -> None:
        self._project = project

    def LoadProject(self, _name: str) -> _ExistingProject:
        return self._project

    def CreateProject(self, _name: str) -> None:
        raise AssertionError("existing project should not be created")

    def SaveProject(self) -> bool:
        return True


class _ExistingResolve:
    def __init__(self, project: _ExistingProject) -> None:
        self._manager = _ExistingProjectManager(project)

    def GetProjectManager(self) -> _ExistingProjectManager:
        return self._manager


def _prepare_existing_resolve(
    episode: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    scripts = Path(__file__).resolve().parents[3] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    sys.modules.pop("build_resolve_project", None)
    import build_resolve_project

    video = episode / "program.mp4"
    video.write_bytes(b"test")
    project = _ExistingProject(episode.name)
    monkeypatch.setattr(build_resolve_project, "find_main_video", lambda *_args: video)
    monkeypatch.setattr(
        build_resolve_project,
        "_probe",
        lambda _path: {
            "fps": 30.0,
            "width": 1920,
            "height": 1080,
            "duration": 1.0,
        },
    )
    monkeypatch.setattr(
        build_resolve_project,
        "connect_resolve",
        lambda: _ExistingResolve(project),
    )
    return build_resolve_project


def test_existing_resolve_timeline_without_lineage_receipt_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _memo_dual_audit_release_fixture(tmp_path)
    build_resolve_project = _prepare_existing_resolve(tmp_path, monkeypatch)

    with pytest.raises(Stage5SubtitleContractError, match="lacks a valid"):
        build_resolve_project.build_project(tmp_path)


def test_existing_resolve_timeline_wrong_lineage_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _memo_dual_audit_release_fixture(tmp_path)
    build_resolve_project = _prepare_existing_resolve(tmp_path, monkeypatch)
    legacy_srt = tmp_path / "transcript.srt"
    legacy_srt.write_bytes(b"legacy")
    build_resolve_project._write_resolve_lineage_receipt(
        tmp_path,
        project_name=tmp_path.name,
        timeline_name=tmp_path.name,
        subtitle=Stage5SubtitleSelection(
            mode="legacy-v1",
            srt_path=legacy_srt,
            handoff=None,
        ),
    )

    with pytest.raises(Stage5SubtitleContractError, match="differs"):
        build_resolve_project.build_project(tmp_path)


def test_existing_resolve_timeline_matching_lineage_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _memo_dual_audit_release_fixture(tmp_path)
    build_resolve_project = _prepare_existing_resolve(tmp_path, monkeypatch)
    selected = Stage5SubtitleRequest().open(tmp_path)
    build_resolve_project._write_resolve_lineage_receipt(
        tmp_path,
        project_name=tmp_path.name,
        timeline_name=tmp_path.name,
        subtitle=selected,
    )

    result = build_resolve_project.build_project(tmp_path)

    assert result["status"] == "already-exists"
    assert result["subtitle_srt_sha256"] == selected.identity()["subtitle_srt_sha256"]


def test_core_finalize_to_default_stage5_and_resolve_dry_run_exact_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import podcast_subtitle_release as release
    from tests.scripts.test_podcast_subtitle_release import (
        _audio_inputs,
        _early_inputs,
        _text_inputs,
    )

    episode = tmp_path / "fixture-episode"
    episode.mkdir()
    _early_inputs(episode, cue_count=4)
    request_path = release.init_request(episode, episode_id=episode.name)
    text_srt, unresolved_raw = _text_inputs(
        episode,
        cue_count=4,
        unresolved="major",
    )
    request = release.seal_request(request_path)
    _audio_inputs(
        episode,
        text_srt=text_srt,
        unresolved_raw=unresolved_raw,
        population="major",
    )
    release.finalize(release.seal_request(request.request_path))

    scripts = Path(__file__).resolve().parents[3] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    sys.modules.pop("build_resolve_project", None)
    import build_resolve_project

    video = episode / "program.mp4"
    video.write_bytes(b"test")
    monkeypatch.setattr(build_resolve_project, "find_main_video", lambda *_args: video)
    monkeypatch.setattr(
        build_resolve_project,
        "_probe",
        lambda _path: {
            "fps": 30.0,
            "width": 1920,
            "height": 1080,
            "duration": 1.0,
        },
    )
    monkeypatch.setattr(
        build_resolve_project,
        "connect_resolve",
        lambda: (_ for _ in ()).throw(AssertionError("dry-run touched Resolve")),
    )

    selection = Stage5SubtitleRequest().open(episode)
    plan = build_resolve_project.build_project(episode, dry_run=True)

    assert selection.mode == "memo-dual-audit-v1"
    assert plan["subtitle_mode"] == "memo-dual-audit-v1"
    assert Path(plan["subtitle"]).read_bytes() == selection.srt_path.read_bytes()
    assert plan["subtitle_srt_sha256"] == _sha256(selection.srt_path.read_bytes())


def test_degraded_release_handoff_selects_exact_release_srt_and_identity(
    tmp_path: Path,
) -> None:
    handoff_path = _degraded_release_fixture(tmp_path)

    selected = Stage5SubtitleRequest(degraded_release_handoff=handoff_path).open(tmp_path)

    assert selected.mode == "degraded-dual-asr-v1"
    assert selected.srt_path.name == "release-v1-corrected.srt"
    identity = selected.identity()
    assert identity["subtitle_mode"] == "degraded-dual-asr-v1"
    assert identity["episode_id"] == "episode-degraded"
    assert identity["subtitle_srt_sha256"] == _sha256(selected.srt_path.read_bytes())
    assert "projection_id" not in identity


def test_degraded_release_handoff_accepts_episode_specific_counts(
    tmp_path: Path,
) -> None:
    handoff_path = _degraded_release_fixture(
        tmp_path,
        cue_count=7,
        major_component_count=4,
        nonmajor_retained_original_count=2,
    )

    selected = Stage5SubtitleRequest(degraded_release_handoff=handoff_path).open(tmp_path)

    assert selected.mode == "degraded-dual-asr-v1"


def test_resolve_display_copy_strips_fillers_and_leaves_degraded_release_intact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handoff_path = _degraded_release_fixture(tmp_path)
    selected = Stage5SubtitleRequest(degraded_release_handoff=handoff_path).open(tmp_path)
    scripts = Path(__file__).resolve().parents[3] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    sys.modules.pop("build_resolve_project", None)
    import build_resolve_project

    release_before = selected.srt_path.read_bytes()
    versioned = build_resolve_project._versioned_srt(tmp_path, subtitle=selected)

    # Hash-bound 顯示副本 = release 文字套語助詞清理（修修 2026-09-03）。
    # 不再是 byte-exact 複製：標點／空隙仍然不碰，但遲疑語助詞要刪。
    # release.srt 本體永遠不動——證據鏈靠它。
    from shared.subtitle_finalize import filler_only, parse_srt_text, strip_fillers

    assert selected.srt_path.read_bytes() == release_before
    source_cues = parse_srt_text(selected.srt_path.read_text(encoding="utf-8-sig"))
    expected = [
        (start, end, strip_fillers(text))
        for start, end, text in source_cues
        if not filler_only(text) and strip_fillers(text)
    ]
    assert parse_srt_text(versioned.read_text(encoding="utf-8-sig")) == expected


@pytest.mark.parametrize("artifact", ["release_srt", "release_ledger", "export_manifest"])
def test_degraded_release_handoff_rejects_artifact_tamper(tmp_path: Path, artifact: str) -> None:
    handoff_relative = _degraded_release_fixture(tmp_path)
    handoff = json.loads((tmp_path / handoff_relative).read_text(encoding="utf-8"))
    target = tmp_path / handoff[artifact]["path"]
    target.write_bytes(target.read_bytes() + b"tamper")

    with pytest.raises(Stage5SubtitleContractError, match="hash or size mismatch"):
        Stage5SubtitleRequest(degraded_release_handoff=handoff_relative).open(tmp_path)


def test_degraded_release_handoff_rejects_episode_path_escape(tmp_path: Path) -> None:
    handoff_relative = _degraded_release_fixture(tmp_path)
    handoff_path = tmp_path / handoff_relative
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    handoff["release_srt"]["path"] = "../outside.srt"
    handoff_path.write_text(json.dumps(handoff), encoding="utf-8")

    with pytest.raises(Stage5SubtitleContractError, match="escapes"):
        Stage5SubtitleRequest(degraded_release_handoff=handoff_relative).open(tmp_path)


def test_degraded_release_handoff_rejects_incomplete_major_coverage(
    tmp_path: Path,
) -> None:
    handoff_relative = _degraded_release_fixture(
        tmp_path,
        major_component_count=32,
        major_audio_reviewed_count=31,
    )

    with pytest.raises(Stage5SubtitleContractError, match="major audio coverage"):
        Stage5SubtitleRequest(degraded_release_handoff=handoff_relative).open(tmp_path)


def test_degraded_release_handoff_rejects_ledger_major_coverage_even_if_rehashed(
    tmp_path: Path,
) -> None:
    handoff_relative = _degraded_release_fixture(tmp_path)
    handoff_path = tmp_path / handoff_relative
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    ledger_path = tmp_path / handoff["release_ledger"]["path"]
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["major_audio_reviewed_count"] = 31
    ledger_bytes = _canonical_json(ledger) + b"\n"
    ledger_path.write_bytes(ledger_bytes)
    handoff["release_ledger"]["sha256"] = _sha256(ledger_bytes)
    handoff["release_ledger"]["size_bytes"] = len(ledger_bytes)
    manifest_path = tmp_path / handoff["export_manifest"]["path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ledger_relative = ledger_path.relative_to(manifest_path.parent).as_posix()
    ledger_entry = next(item for item in manifest["files"] if item["path"] == ledger_relative)
    ledger_entry["sha256"] = _sha256(ledger_bytes)
    ledger_entry["size_bytes"] = len(ledger_bytes)
    manifest_bytes = _canonical_json(manifest) + b"\n"
    manifest_path.write_bytes(manifest_bytes)
    handoff["export_manifest"]["sha256"] = _sha256(manifest_bytes)
    handoff["export_manifest"]["size_bytes"] = len(manifest_bytes)
    handoff_path.write_text(json.dumps(handoff), encoding="utf-8")

    with pytest.raises(Stage5SubtitleContractError, match="ledger/gates drift"):
        Stage5SubtitleRequest(degraded_release_handoff=handoff_relative).open(tmp_path)


def test_degraded_release_handoff_rejects_actual_cue_count_mismatch(
    tmp_path: Path,
) -> None:
    handoff_relative = _degraded_release_fixture(
        tmp_path,
        cue_count=7,
        actual_cue_count=6,
        major_component_count=4,
        nonmajor_retained_original_count=2,
    )

    with pytest.raises(Stage5SubtitleContractError, match="actual SRT metrics drift"):
        Stage5SubtitleRequest(degraded_release_handoff=handoff_relative).open(tmp_path)


@pytest.mark.parametrize(
    "request_builder",
    [
        lambda path: Stage5SubtitleRequest(legacy_v1=True, degraded_release_handoff=path),
    ],
)
def test_degraded_release_handoff_is_mutually_exclusive_with_other_modes(
    tmp_path: Path, request_builder
) -> None:
    handoff_relative = _degraded_release_fixture(tmp_path)

    with pytest.raises(Stage5SubtitleContractError, match="cannot be combined"):
        request_builder(handoff_relative).open(tmp_path)


def test_degraded_release_handoff_replays_same_serializable_identity(
    tmp_path: Path,
) -> None:
    handoff_relative = _degraded_release_fixture(tmp_path)
    request = Stage5SubtitleRequest(degraded_release_handoff=handoff_relative)

    first = request.open(tmp_path)
    second = request.open(tmp_path)

    assert first.identity() == second.identity()
    assert set(first.identity()) == {
        "subtitle_mode",
        "episode_id",
        "provenance_status",
        "degraded_release_handoff",
        "degraded_release_handoff_sha256",
        "release_ledger",
        "release_ledger_sha256",
        "export_manifest",
        "export_manifest_sha256",
        "subtitle_srt_sha256",
    }


def test_resolve_accepts_degraded_handoff_but_highlight_requires_editorial_master(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handoff_relative = _degraded_release_fixture(tmp_path)
    scripts = Path(__file__).resolve().parents[3] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    sys.modules.pop("build_resolve_project", None)
    sys.modules.pop("run_highlight_cut", None)
    import build_resolve_project
    import run_highlight_cut

    parsed = build_resolve_project._parse_args(
        [str(tmp_path), "--degraded-release-handoff", str(handoff_relative)]
    )
    assert parsed.subtitle_request == Stage5SubtitleRequest(
        degraded_release_handoff=str(handoff_relative)
    )
    with pytest.raises(SystemExit) as stopped:
        run_highlight_cut.main(
            [
                str(tmp_path),
                "--mining-input",
                "--degraded-release-handoff",
                str(handoff_relative),
            ]
        )
    assert stopped.value.code == 2


def test_bare_episode_transcript_requires_explicit_legacy_mode(tmp_path: Path) -> None:
    bare_srt = tmp_path / "transcript.srt"
    bare_srt.write_bytes(b"bare legacy subtitle\n")

    with pytest.raises(Stage5SubtitleContractError, match="official Memo Dual-Audit"):
        select_stage5_subtitle(episode_root=tmp_path)

    selected = select_stage5_subtitle(episode_root=tmp_path, legacy_v1=True)

    assert selected.mode == "legacy-v1"
    assert selected.srt_path == bare_srt
    assert selected.handoff is None


def test_highlight_rejects_stage5_only_request_as_direct_production_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = Stage5SubtitleRequest()
    (tmp_path / "transcript.srt").write_bytes(b"ROOT V1 MUST NEVER WIN\n")
    highlights = tmp_path / "highlights"
    highlights.mkdir()
    (highlights / "candidates.json").write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "id": "L1",
                        "format": "long",
                        "t_start": 0.0,
                        "t_end": 361.0,
                        "title": "verified",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (highlights / "winners.json").write_text(
        json.dumps({"winners": [{"id": "L1", "rank": 1}]}), encoding="utf-8"
    )
    scripts = Path(__file__).resolve().parents[3] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    sys.modules.pop("run_highlight_cut", None)
    import run_highlight_cut

    original = (highlights / "candidates.json").read_bytes()
    for operation, kwargs in (
        (run_highlight_cut.mining_input, {}),
        (run_highlight_cut.validate, {}),
        (run_highlight_cut.materialize, {"dry_run": True}),
    ):
        with pytest.raises(EditorialMasterContractError, match="Stage5-only"):
            operation(
                tmp_path,
                **kwargs,
                subtitle_request=request,
            )
    assert (highlights / "candidates.json").read_bytes() == original


def test_refresh_rejects_stale_lineage_before_resolve_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = Stage5SubtitleRequest()
    highlights = tmp_path / "highlights"
    highlights.mkdir()
    stale = {"subtitle_mode": "memo-dual-audit-v1", "episode_id": "stale"}
    (highlights / "candidates.json").write_text(
        json.dumps({"subtitle_lineage": stale, "candidates": []}), encoding="utf-8"
    )
    (highlights / "winners.json").write_text(
        json.dumps({"subtitle_lineage": stale, "winners": []}), encoding="utf-8"
    )
    scripts = Path(__file__).resolve().parents[3] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    sys.modules.pop("run_highlight_cut", None)
    sys.modules.pop("build_resolve_project", None)
    import build_resolve_project
    import run_highlight_cut

    monkeypatch.setattr(
        build_resolve_project,
        "connect_resolve",
        lambda: (_ for _ in ()).throw(AssertionError("stale lineage touched Resolve")),
    )
    with pytest.raises(EditorialMasterContractError, match="Stage5-only"):
        run_highlight_cut.refresh_subs(
            tmp_path,
            subtitle_request=request,
        )


def test_resolve_cli_keeps_explicit_legacy_switch_and_drops_projection_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripts = Path(__file__).resolve().parents[3] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    sys.modules.pop("build_resolve_project", None)
    import build_resolve_project

    legacy = build_resolve_project._parse_args([str(tmp_path), "--legacy-v1"])

    assert legacy.subtitle_request == Stage5SubtitleRequest(legacy_v1=True)
    with pytest.raises(SystemExit) as stopped:
        build_resolve_project._parse_args([str(tmp_path), "--projection-id", "projection-123"])
    assert stopped.value.code == 2
