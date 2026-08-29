from __future__ import annotations

import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from agents.brook.script_video.finished_cut_production._composition import (
    ProductionCutoverConfiguration,
    ProductionResolveConfiguration,
    ProductionStatusView,
)
from agents.brook.script_video.finished_cut_production._correction import RunInspection
from scripts import run_finished_cut_production as cli


class _Cp1252Stdout:
    def __init__(self) -> None:
        self.encoding = "cp1252"
        self.buffer = io.BytesIO()

    def write(self, value: str) -> int:
        self.buffer.write(value.encode(self.encoding))
        return len(value)

    def flush(self) -> None:
        return None

    def reconfigure(self, *, encoding: str) -> None:
        self.encoding = encoding


@dataclass
class _FakeApplication:
    registered: object | None = None
    advanced: str | None = None
    inspected_status: str | None = None
    inspected_run: str | None = None
    revision_request: tuple[str, str, str] | None = None
    correction_request: tuple[str, str, str, str] | None = None
    dispatch_recovery: str | None = None

    def register_approved_cut(self, registration):
        self.registered = registration
        return "approved-cut:0123456789abcdef0123456789abcdef"

    def advance(self, command_id: str) -> ProductionStatusView:
        self.advanced = command_id
        return ProductionStatusView(
            command_id=command_id,
            state="pending",
            run_id="run-1",
            current_stage="director",
            scope="full_stage",
        )

    def status(self, command_id: str) -> ProductionStatusView:
        self.inspected_status = command_id
        return ProductionStatusView(command_id=command_id, state="registered")

    def request_revision(
        self,
        current_release_ref: str,
        event_id: str,
        feedback: str,
    ) -> str:
        self.revision_request = (current_release_ref, event_id, feedback)
        return "targeted-revision:0123456789abcdef0123456789abcdef"

    def inspect_run(self, command_id: str) -> RunInspection:
        self.inspected_run = command_id
        return RunInspection(
            run_id="run-1",
            command_id=command_id,
            episode_id="20260805 林之晨",
            cut_id="long-3",
            format="long",
            status="pending",
            outstanding_stage="dp",
            outstanding_scope="full_stage",
            outstanding_event_id=None,
            current_stages=(),
            superseded_acceptance_ids=(),
            build_state="not_started",
        )

    def request_correction(
        self,
        command_id: str,
        stage: str,
        event_id: str,
        feedback: str,
    ) -> str:
        self.correction_request = (command_id, stage, event_id, feedback)
        return "request-0123456789abcdef0123456789abcdef"

    def retry_failed_dispatch(self, command_id: str) -> str:
        self.dispatch_recovery = command_id
        return "request-fedcba9876543210fedcba9876543210"

    def cutover(
        self,
        cutover_id: str,
        command_ids: tuple[str, ...],
    ) -> _CutoverStatus:
        self.cutover_request = (cutover_id, command_ids)
        return _CutoverStatus(
            cutover_id=cutover_id,
            episode_id="20260805 林之晨",
            state="completed",
            release_ids=("release-1", "release-2", "release-3"),
            manifest_id="manifest-1",
            deployment_id="finished-cut-production-v1",
        )


@dataclass(frozen=True)
class _CutoverStatus:
    cutover_id: str
    episode_id: str
    state: str
    release_ids: tuple[str, ...]
    manifest_id: str | None
    deployment_id: str


def _registration_payload() -> dict[str, object]:
    return {
        "episode_id": "episode-1",
        "cut_id": "long-3",
        "format": "long",
        "editorial_master_id": "a" * 64,
        "winner_id": "winner-long-3",
        "tight_cut_id": "tight-long-3",
        "source_ranges": [{"t0": 120.0, "t1": 660.0}],
        "cues": [
            {
                "cue_id": "cue-1",
                "text": "第一句",
                "t0": 0.0,
                "t1": 540.0,
                "section_id": "section-1",
            }
        ],
        "sections": [
            {
                "section_id": "section-1",
                "chapter_title": "第一章",
                "t0": 0.0,
                "transition_before": False,
                "transition_title": None,
            }
        ],
        "human_approved": True,
        "approved_by": "human:shosho",
        "approved_at": "2026-08-28T10:00:00+08:00",
        "editorial_feedback": ["Hero Title 太密。"],
    }


def _resolve_configuration_payload(tmp_path: Path) -> dict[str, object]:
    return {
        "episode_id": "20260805 林之晨",
        "database": {
            "db_type": "Disk",
            "db_name": "Local Database",
            "ip_address": None,
        },
        "folder": "",
        "project_name": "20260805 林之晨",
        "project_uid": "resolve-project:" + "a" * 64,
        "editorial_master_content_hash": "b" * 64,
        "staging_root": str(
            tmp_path / "episodes" / "20260805 林之晨" / "highlights" / "staging" / "finished-cut"
        ),
        "cuts": [
            {
                "cut_id": "long-1",
                "timeline_name": "long-1-clean-base",
                "timeline_uid": "timeline-long-1",
            },
            {
                "cut_id": "long-2",
                "timeline_name": "long-2-clean-base",
                "timeline_uid": "timeline-long-2",
            },
            {
                "cut_id": "long-3",
                "timeline_name": "__lh_backup__punch-L04__6a37a66a18",
                "timeline_uid": "767a4663-2056-4961-a7a6-ba029dc8712f",
            },
        ],
    }


def _cutover_configuration_payload(tmp_path: Path) -> dict[str, object]:
    return {
        "fixed_cut_order": ["long-1", "long-2", "long-3"],
        "target_deployment_id": "finished-cut-production-v1",
        "deployment_state_path": str(tmp_path / "deployment" / "current.json"),
    }


def test_cli_passes_exact_resolve_configuration_to_composition(
    tmp_path: Path,
    capsys,
) -> None:
    config_path = tmp_path / "resolve.json"
    config_path.write_text(
        json.dumps(_resolve_configuration_payload(tmp_path), ensure_ascii=False),
        encoding="utf-8",
    )
    application = _FakeApplication()
    captured: dict[str, object] = {}

    def factory(
        _paths,
        _episode_id,
        *,
        resolve_configuration: ProductionResolveConfiguration,
    ):
        captured["configuration"] = resolve_configuration
        return application

    exit_code = cli.main(
        [
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--episodes-root",
            str(tmp_path / "episodes"),
            "--episode-id",
            "20260805 林之晨",
            "--resolve-config",
            str(config_path),
            "status",
            "approved-cut:0123456789abcdef0123456789abcdef",
        ],
        application_factory=factory,
    )

    assert exit_code == 0
    configuration = captured["configuration"]
    assert isinstance(configuration, ProductionResolveConfiguration)
    assert configuration.locator.folder == ""
    assert configuration.locator.project_name == "20260805 林之晨"
    assert configuration.binding.project_uid == "resolve-project:" + "a" * 64
    assert tuple(
        (cut.cut_id, cut.canonical.name, cut.canonical.uid) for cut in configuration.binding.cuts
    ) == (
        ("long-1", "long-1-clean-base", "timeline-long-1"),
        ("long-2", "long-2-clean-base", "timeline-long-2"),
        (
            "long-3",
            "__lh_backup__punch-L04__6a37a66a18",
            "767a4663-2056-4961-a7a6-ba029dc8712f",
        ),
    )
    assert json.loads(capsys.readouterr().out)["state"] == "registered"


def test_cli_runs_controlled_three_candidate_cutover_with_pinned_configuration(
    tmp_path: Path,
    capsys,
) -> None:
    resolve_path = tmp_path / "resolve.json"
    resolve_path.write_text(
        json.dumps(_resolve_configuration_payload(tmp_path), ensure_ascii=False),
        encoding="utf-8",
    )
    cutover_path = tmp_path / "cutover.json"
    cutover_path.write_text(
        json.dumps(_cutover_configuration_payload(tmp_path)),
        encoding="utf-8",
    )
    application = _FakeApplication()
    captured: dict[str, object] = {}

    def factory(
        _paths,
        _episode_id,
        *,
        resolve_configuration: ProductionResolveConfiguration,
        cutover_configuration: ProductionCutoverConfiguration,
    ):
        captured["resolve"] = resolve_configuration
        captured["cutover"] = cutover_configuration
        return application

    command_ids = tuple(f"approved-cut:{digit * 32}" for digit in ("1", "2", "3"))
    exit_code = cli.main(
        [
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--episodes-root",
            str(tmp_path / "episodes"),
            "--episode-id",
            "20260805 林之晨",
            "--resolve-config",
            str(resolve_path),
            "--cutover-config",
            str(cutover_path),
            "cutover",
            "lin-longs-v3",
            *command_ids,
        ],
        application_factory=factory,
    )

    assert exit_code == 0
    assert application.cutover_request == ("lin-longs-v3", command_ids)
    configuration = captured["cutover"]
    assert isinstance(configuration, ProductionCutoverConfiguration)
    assert configuration.fixed_cut_order == ("long-1", "long-2", "long-3")
    assert configuration.target_deployment_id == "finished-cut-production-v1"
    assert json.loads(capsys.readouterr().out) == {
        "cutover_id": "lin-longs-v3",
        "deployment_id": "finished-cut-production-v1",
        "episode_id": "20260805 林之晨",
        "manifest_id": "manifest-1",
        "release_ids": ["release-1", "release-2", "release-3"],
        "state": "completed",
    }


def test_cli_cutover_fails_before_composition_when_config_is_missing(
    tmp_path: Path,
) -> None:
    called = False

    def factory(*_args, **_kwargs):
        nonlocal called
        called = True
        return _FakeApplication()

    with pytest.raises(ValueError, match="pinned cutover configuration"):
        cli.main(
            [
                "--runtime-root",
                str(tmp_path / "runtime"),
                "--episodes-root",
                str(tmp_path / "episodes"),
                "--episode-id",
                "episode-1",
                "cutover",
                "cutover-1",
                "approved-cut:" + "1" * 32,
                "approved-cut:" + "2" * 32,
                "approved-cut:" + "3" * 32,
            ],
            application_factory=factory,
        )

    assert called is False


def test_cli_registers_approved_cut_through_composition_interface(
    tmp_path: Path,
    capsys,
) -> None:
    request = tmp_path / "approved-cut.json"
    request.write_text(
        json.dumps(_registration_payload(), ensure_ascii=False),
        encoding="utf-8",
    )
    application = _FakeApplication()

    exit_code = cli.main(
        [
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--episodes-root",
            str(tmp_path / "episodes"),
            "--episode-id",
            "episode-1",
            "register-approved-cut",
            "--input",
            str(request),
        ],
        application_factory=lambda _paths, _episode_id: application,
    )

    assert exit_code == 0
    assert application.registered is not None
    assert application.registered.editorial_feedback == ("Hero Title 太密。",)
    assert json.loads(capsys.readouterr().out) == {
        "command_id": "approved-cut:0123456789abcdef0123456789abcdef"
    }


def test_cli_advance_returns_typed_pending_status(tmp_path: Path, capsys) -> None:
    application = _FakeApplication()
    command_id = "approved-cut:0123456789abcdef0123456789abcdef"

    exit_code = cli.main(
        [
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--episodes-root",
            str(tmp_path / "episodes"),
            "--episode-id",
            "episode-1",
            "advance",
            command_id,
        ],
        application_factory=lambda _paths, _episode_id: application,
    )

    assert exit_code == 0
    assert application.advanced == command_id
    assert json.loads(capsys.readouterr().out) == {
        "command_id": command_id,
        "current_stage": "director",
        "event_id": None,
        "reason_code": None,
        "run_id": "run-1",
        "scope": "full_stage",
        "state": "pending",
    }


def test_cli_status_is_read_only_typed_view(tmp_path: Path, capsys) -> None:
    application = _FakeApplication()
    command_id = "approved-cut:0123456789abcdef0123456789abcdef"

    exit_code = cli.main(
        [
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--episodes-root",
            str(tmp_path / "episodes"),
            "--episode-id",
            "episode-1",
            "status",
            command_id,
        ],
        application_factory=lambda _paths, _episode_id: application,
    )

    assert exit_code == 0
    assert application.inspected_status == command_id
    assert json.loads(capsys.readouterr().out)["state"] == "registered"


def test_cli_requests_exact_current_targeted_revision(tmp_path: Path, capsys) -> None:
    application = _FakeApplication()

    exit_code = cli.main(
        [
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--episodes-root",
            str(tmp_path / "episodes"),
            "--episode-id",
            "episode-1",
            "request-revision",
            "release-current",
            "event-1",
            "Hero Title 請縮小。",
        ],
        application_factory=lambda _paths, _episode_id: application,
    )

    assert exit_code == 0
    assert application.revision_request == (
        "release-current",
        "event-1",
        "Hero Title 請縮小。",
    )
    assert json.loads(capsys.readouterr().out) == {
        "command_id": "targeted-revision:0123456789abcdef0123456789abcdef"
    }


def test_cli_inspects_exact_pre_release_run(tmp_path: Path, capsys) -> None:
    application = _FakeApplication()
    command_id = "approved-cut:0123456789abcdef0123456789abcdef"

    exit_code = cli.main(
        [
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--episodes-root",
            str(tmp_path / "episodes"),
            "--episode-id",
            "episode-1",
            "inspect-run",
            command_id,
        ],
        application_factory=lambda _paths, _episode_id: application,
    )

    assert exit_code == 0
    assert application.inspected_run == command_id
    assert json.loads(capsys.readouterr().out)["outstanding_stage"] == "dp"


def test_cli_emits_utf8_json_when_windows_stdout_uses_cp1252(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _FakeApplication()
    command_id = "approved-cut:0123456789abcdef0123456789abcdef"
    stdout = _Cp1252Stdout()
    monkeypatch.setattr(sys, "stdout", stdout)

    exit_code = cli.main(
        [
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--episodes-root",
            str(tmp_path / "episodes"),
            "--episode-id",
            "20260805 林之晨",
            "inspect-run",
            command_id,
        ],
        application_factory=lambda _paths, _episode_id: application,
    )

    assert exit_code == 0
    assert json.loads(stdout.buffer.getvalue().decode("utf-8"))["episode_id"] == ("20260805 林之晨")


def test_cli_requests_one_pre_release_event_correction(tmp_path: Path, capsys) -> None:
    application = _FakeApplication()
    command_id = "approved-cut:0123456789abcdef0123456789abcdef"

    exit_code = cli.main(
        [
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--episodes-root",
            str(tmp_path / "episodes"),
            "--episode-id",
            "episode-1",
            "request-correction",
            command_id,
            "director",
            "event-1",
            "補上完整主詞。",
        ],
        application_factory=lambda _paths, _episode_id: application,
    )

    assert exit_code == 0
    assert application.correction_request == (
        command_id,
        "director",
        "event-1",
        "補上完整主詞。",
    )
    assert json.loads(capsys.readouterr().out) == {
        "request_id": "request-0123456789abcdef0123456789abcdef"
    }


def test_cli_explicitly_recovers_one_failed_semantic_dispatch(tmp_path: Path, capsys) -> None:
    application = _FakeApplication()
    command_id = "approved-cut:0123456789abcdef0123456789abcdef"

    exit_code = cli.main(
        [
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--episodes-root",
            str(tmp_path / "episodes"),
            "--episode-id",
            "episode-1",
            "retry-failed-dispatch",
            command_id,
        ],
        application_factory=lambda _paths, _episode_id: application,
    )

    assert exit_code == 0
    assert application.dispatch_recovery == command_id
    assert json.loads(capsys.readouterr().out) == {
        "request_id": "request-fedcba9876543210fedcba9876543210"
    }


def test_cli_has_no_legacy_or_direct_mutation_imports() -> None:
    source = Path(cli.__file__).read_text(encoding="utf-8")

    for forbidden in (
        "long_highlight_orchestrator",
        "podcast_highlight_visual",
        "run_short",
        "DaVinciResolve",
        "state.json",
        "finished_review_manifest_current",
    ):
        assert forbidden not in source


def test_cli_rejects_stage_rows_in_approved_cut_registration(
    tmp_path: Path,
) -> None:
    request = tmp_path / "approved-cut.json"
    payload = _registration_payload()
    payload["events"] = [{"event_id": "laundered-old-row"}]
    request.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    application = _FakeApplication()

    with pytest.raises(ValueError, match="fields"):
        cli.main(
            [
                "--runtime-root",
                str(tmp_path / "runtime"),
                "--episodes-root",
                str(tmp_path / "episodes"),
                "--episode-id",
                "episode-1",
                "register-approved-cut",
                "--input",
                str(request),
            ],
            application_factory=lambda _paths, _episode_id: application,
        )
    assert application.registered is None
