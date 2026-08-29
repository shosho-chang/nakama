#!/usr/bin/env python3
"""Zero-logic CLI for the Finished Cut Production composition Interface."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal, cast

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agents.brook.script_video.finished_cut_production import (  # noqa: E402
    ApprovedCutRegistration,
    CanonicalSection,
    CueAnchor,
    CutSourceRange,
    FinishedCutProductionApplication,
    ProductionCutoverConfiguration,
    ProductionPaths,
    ProductionResolveConfiguration,
    ResolveCutBinding,
    ResolveDatabaseIdentity,
    ResolveProjectBinding,
    ResolveProjectLocator,
    StageName,
    TimelineIdentity,
    build_production_application,
)

ApplicationFactory = Callable[..., FinishedCutProductionApplication]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--episodes-root", required=True, type=Path)
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--resolve-config", type=Path)
    parser.add_argument("--cutover-config", type=Path)
    commands = parser.add_subparsers(dest="operation", required=True)
    register = commands.add_parser("register-approved-cut")
    register.add_argument("--input", required=True, type=Path)
    advance = commands.add_parser("advance")
    advance.add_argument("command_id")
    status = commands.add_parser("status")
    status.add_argument("command_id")
    revision = commands.add_parser("request-revision")
    revision.add_argument("current_release_ref")
    revision.add_argument("event_id")
    revision.add_argument("feedback")
    inspect_run = commands.add_parser("inspect-run")
    inspect_run.add_argument("command_id")
    correction = commands.add_parser("request-correction")
    correction.add_argument("command_id")
    correction.add_argument("stage", choices=("director", "dp", "visual_review"))
    correction.add_argument("event_id")
    correction.add_argument("feedback")
    dispatch_recovery = commands.add_parser("retry-failed-dispatch")
    dispatch_recovery.add_argument("command_id")
    cutover = commands.add_parser("cutover")
    cutover.add_argument("cutover_id")
    cutover.add_argument("command_ids", nargs=3)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    application_factory: ApplicationFactory | None = None,
) -> int:
    args = _parser().parse_args(argv)
    paths = ProductionPaths(args.runtime_root, args.episodes_root)
    factory = application_factory or build_production_application
    factory_options: dict[str, object] = {}
    if args.resolve_config is not None:
        payload = json.loads(args.resolve_config.read_text(encoding="utf-8"))
        configuration = _resolve_configuration(payload)
        if configuration.locator.episode_id != args.episode_id:
            raise ValueError("Resolve configuration belongs to another episode")
        factory_options["resolve_configuration"] = configuration
    if args.cutover_config is not None:
        if args.resolve_config is None:
            raise ValueError("cutover configuration requires exact Resolve configuration")
        payload = json.loads(args.cutover_config.read_text(encoding="utf-8"))
        factory_options["cutover_configuration"] = _cutover_configuration(payload)
    if args.operation == "cutover" and args.cutover_config is None:
        raise ValueError("cutover operation requires pinned cutover configuration")
    application = factory(paths, args.episode_id, **factory_options)
    if args.operation == "register-approved-cut":
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        command_id = application.register_approved_cut(_registration(payload))
        _print({"command_id": command_id})
        return 0
    if args.operation == "advance":
        _print(asdict(application.advance(args.command_id)))
        return 0
    if args.operation == "status":
        _print(asdict(application.status(args.command_id)))
        return 0
    if args.operation == "request-revision":
        command_id = application.request_revision(
            args.current_release_ref,
            args.event_id,
            args.feedback,
        )
        _print({"command_id": command_id})
        return 0
    if args.operation == "inspect-run":
        _print(asdict(application.inspect_run(args.command_id)))
        return 0
    if args.operation == "request-correction":
        request_id = application.request_correction(
            args.command_id,
            cast(StageName, args.stage),
            args.event_id,
            args.feedback,
        )
        _print({"request_id": request_id})
        return 0
    if args.operation == "retry-failed-dispatch":
        request_id = application.retry_failed_dispatch(args.command_id)
        _print({"request_id": request_id})
        return 0
    if args.operation == "cutover":
        _print(asdict(application.cutover(args.cutover_id, tuple(args.command_ids))))
        return 0
    raise AssertionError("unreachable Finished Cut operation")


def _registration(value: object) -> ApprovedCutRegistration:
    row = _object(value, "ApprovedCut registration")
    expected = {
        "episode_id",
        "cut_id",
        "format",
        "editorial_master_id",
        "winner_id",
        "tight_cut_id",
        "source_ranges",
        "cues",
        "sections",
        "human_approved",
        "approved_by",
        "approved_at",
        "editorial_feedback",
    }
    if set(row) != expected:
        raise ValueError("ApprovedCut registration fields are invalid")
    source_ranges = _object_rows(row["source_ranges"], "source_ranges")
    cues = _object_rows(row["cues"], "cues")
    sections = _object_rows(row["sections"], "sections")
    return ApprovedCutRegistration(
        episode_id=_string(row, "episode_id"),
        cut_id=_string(row, "cut_id"),
        format=cast(Literal["long", "short"], _string(row, "format")),
        editorial_master_id=_string(row, "editorial_master_id"),
        winner_id=_string(row, "winner_id"),
        tight_cut_id=_string(row, "tight_cut_id"),
        source_ranges=tuple(
            CutSourceRange(_number(item, "t0"), _number(item, "t1"))
            for item in source_ranges
            if _exact_fields(item, {"t0", "t1"}, "source range")
        ),
        cues=tuple(
            CueAnchor(
                _string(item, "cue_id"),
                _string(item, "text"),
                _number(item, "t0"),
                _number(item, "t1"),
                _optional_string(item, "section_id"),
            )
            for item in cues
            if _exact_fields(
                item,
                {"cue_id", "text", "t0", "t1", "section_id"},
                "cue",
            )
        ),
        sections=tuple(
            CanonicalSection(
                _string(item, "section_id"),
                _string(item, "chapter_title"),
                _number(item, "t0"),
                _boolean(item, "transition_before"),
                _optional_string(item, "transition_title"),
            )
            for item in sections
            if _exact_fields(
                item,
                {
                    "section_id",
                    "chapter_title",
                    "t0",
                    "transition_before",
                    "transition_title",
                },
                "canonical section",
            )
        ),
        human_approved=_boolean(row, "human_approved"),
        approved_by=_string(row, "approved_by"),
        approved_at=_string(row, "approved_at"),
        editorial_feedback=tuple(_string_list(row, "editorial_feedback")),
    )


def _resolve_configuration(value: object) -> ProductionResolveConfiguration:
    row = _object(value, "Resolve configuration")
    _exact_fields(
        row,
        {
            "episode_id",
            "database",
            "folder",
            "project_name",
            "project_uid",
            "editorial_master_content_hash",
            "staging_root",
            "cuts",
        },
        "Resolve configuration",
    )
    database = _object(row["database"], "Resolve database")
    _exact_fields(
        database,
        {"db_type", "db_name", "ip_address"},
        "Resolve database",
    )
    cut_rows = _object_rows(row["cuts"], "Resolve cuts")
    cuts = tuple(
        ResolveCutBinding(
            cut_id=_config_string(cut, "cut_id"),
            canonical=TimelineIdentity(
                name=_config_string(cut, "timeline_name"),
                uid=_config_string(cut, "timeline_uid"),
            ),
        )
        for cut in cut_rows
        if _exact_fields(
            cut,
            {"cut_id", "timeline_name", "timeline_uid"},
            "Resolve cut",
        )
    )
    episode_id = _config_string(row, "episode_id")
    project_name = _config_string(row, "project_name")
    return ProductionResolveConfiguration(
        locator=ResolveProjectLocator(
            episode_id=episode_id,
            database=ResolveDatabaseIdentity(
                db_type=_config_string(database, "db_type"),
                db_name=_config_string(database, "db_name"),
                ip_address=_optional_config_string(database, "ip_address"),
            ),
            folder=_config_folder(row, "folder"),
            project_name=project_name,
        ),
        binding=ResolveProjectBinding(
            episode_id=episode_id,
            project_name=project_name,
            project_uid=_config_string(row, "project_uid"),
            cuts=cuts,
        ),
        editorial_master_content_hash=_config_string(
            row,
            "editorial_master_content_hash",
        ),
        staging_root=Path(_config_string(row, "staging_root")),
    )


def _cutover_configuration(value: object) -> ProductionCutoverConfiguration:
    row = _object(value, "cutover configuration")
    _exact_fields(
        row,
        {"fixed_cut_order", "target_deployment_id", "deployment_state_path"},
        "cutover configuration",
    )
    return ProductionCutoverConfiguration(
        fixed_cut_order=_string_list(row, "fixed_cut_order"),
        target_deployment_id=_config_string(row, "target_deployment_id"),
        deployment_state_path=Path(_config_string(row, "deployment_state_path")),
    )


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _object_rows(value: object, label: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{label} must be object rows")
    return tuple(cast(dict[str, Any], item) for item in value)


def _exact_fields(row: Mapping[str, object], expected: set[str], label: str) -> bool:
    if set(row) != expected:
        raise ValueError(f"{label} fields are invalid")
    return True


def _string(row: Mapping[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text")
    return value


def _optional_string(row: Mapping[str, object], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be optional text")
    return value


def _config_string(row: Mapping[str, object], key: str) -> str:
    value = _string(row, key)
    if not value or value != value.strip() or any(character in value for character in "\r\n\t"):
        raise ValueError(f"{key} must be exact non-empty text")
    return value


def _optional_config_string(row: Mapping[str, object], key: str) -> str | None:
    value = _optional_string(row, key)
    if value is not None and (not value or value != value.strip()):
        raise ValueError(f"{key} must be exact optional text")
    return value


def _config_folder(row: Mapping[str, object], key: str) -> str:
    value = _string(row, key)
    if value != value.strip():
        raise ValueError(f"{key} must be an exact Resolve folder identity")
    return value


def _number(row: Mapping[str, object], key: str) -> float:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number")
    return float(value)


def _boolean(row: Mapping[str, object], key: str) -> bool:
    value = row.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be boolean")
    return value


def _string_list(row: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = row.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be text rows")
    return tuple(value)


def _print(value: object) -> None:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
