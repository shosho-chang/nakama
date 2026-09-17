#!/usr/bin/env python3
"""Zero-logic CLI for the Finished Cut Production composition Interface."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, cast

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agents.brook.script_video.finished_cut_production import (  # noqa: E402
    PLAN_RECORD_FILENAME,
    ApprovedCutRegistration,
    CanonicalSection,
    CueAnchor,
    CutSourceRange,
    FinishedCutProductionApplication,
    ProductionPaths,
    ProductionResolveConfiguration,
    ResolveCutBinding,
    ResolveDatabaseIdentity,
    ResolveProjectBinding,
    ResolveProjectLocator,
    StageName,
    TimelineIdentity,
    build_plan_record_reader,
    build_production_application,
)

ApplicationFactory = Callable[..., FinishedCutProductionApplication]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--episodes-root", required=True, type=Path)
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--resolve-config", type=Path)
    parser.add_argument(
        "--semantic-worker",
        choices=("codex", "handoff"),
        default="codex",
        help=(
            "誰回答 Director/DP/visual_review 的 packet。"
            "codex＝開 Codex 子行程（無人看管的 watcher 用）；"
            "handoff＝停下來交給**當下正在跑的 agent**，packet 攤在 --handoff-root"
        ),
    )
    parser.add_argument(
        "--handoff-root",
        type=Path,
        help="--semantic-worker handoff 的交接目錄；預設 <runtime-root>/semantic-handoff",
    )
    commands = parser.add_subparsers(dest="operation", required=True)
    register = commands.add_parser("register-approved-cut")
    register.add_argument("--input", required=True, type=Path)
    advance = commands.add_parser("advance")
    advance.add_argument("command_id")
    status = commands.add_parser("status")
    status.add_argument("command_id")
    revision = commands.add_parser("request-revision")
    revision.add_argument("current_plan_ref")
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
    inspect_cuts = commands.add_parser(
        "inspect-cuts",
        help="列出這一集每支 cut 的視覺事件（event_id／時間／類型／display）",
    )
    inspect_cuts.add_argument("--cut", help="只看這一支（如 punch-L02）")
    inspect_cuts.add_argument(
        "--all",
        action="store_true",
        help="連同歷史 plan record 一起列；預設每支 cut 只給最新的那一份",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    application_factory: ApplicationFactory | None = None,
    plan_record_reader_factory: Callable[[Path], object] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    paths = ProductionPaths(args.runtime_root, args.episodes_root)
    factory = application_factory or build_production_application
    factory_options: dict[str, object] = {}
    if args.semantic_worker == "handoff":
        # 這個接縫 library 一直都有（build_production_application 的 process_runner），
        # 只是 CLI 沒把旋鈕拉出來，於是不管誰在跑都會去開 Codex。
        from agents.brook.script_video.finished_cut_production._agent_handoff import (
            AgentHandoffProcessRunner,
        )

        handoff_root = args.handoff_root or (args.runtime_root / "semantic-handoff")
        factory_options["process_runner"] = AgentHandoffProcessRunner(handoff_root)
    if args.resolve_config is not None:
        payload = json.loads(args.resolve_config.read_text(encoding="utf-8"))
        configuration = _resolve_configuration(payload)
        if configuration.locator.episode_id != args.episode_id:
            raise ValueError("Resolve configuration belongs to another episode")
        factory_options["resolve_configuration"] = configuration
    # 讀取先走。`build_plan_record_reader` 的文件明說「讀取不需要任何外部依賴」，
    # 而 `factory()` 會去驗 HyperFrames runtime、Resolve 綁定與素材櫃——把列事件
    # 排在它後面，等於要修修為了問一個 event_id 先備妥整套算圖環境。
    if args.operation == "inspect-cuts":
        # 改片子之前要先知道「那一句」是哪一個 event_id。`inspect-run` 只認**進行中**
        # 的 command_id，片子做完就沒有進行中的 run，於是這個問題在 CLI 上無路可問——
        # 2026-09-17 修修說「效忠我們的家庭那句畫面很怪」，我是靠翻 semantic-handoff
        # 目錄裡一個臨時 JSON 才找到 event_id 的。底層 `inspect_current()` 早就會回答，
        # 只是沒接出來。這個子指令不做任何判斷，只是把它印出來。
        reader_factory = plan_record_reader_factory or build_plan_record_reader
        inspection = reader_factory(
            (args.episodes_root / args.episode_id).resolve()
        ).inspect_current(args.episode_id)
        episode_root = (args.episodes_root / args.episode_id).resolve()

        def _recorded_at(cut: object) -> float:
            """這份 plan record 是什麼時候寫下來的。

            **不要拿 `inspect_current()` 的順序當時間**：它是 `sorted(glob)`，照
            staging 目錄的雜湊名排，等於隨機。2026-09-17 我照著「最後一筆＝最新」
            取，拿到的是前一晚那一份、display 還是被換掉的舊值。
            """
            preview = pathlib.Path(cut.preview.reference)
            record = episode_root / preview.parent / PLAN_RECORD_FILENAME
            return record.stat().st_mtime if record.is_file() else 0.0

        cuts = [cut for cut in inspection.cuts if args.cut in (None, cut.cut_id)]
        cuts.sort(key=_recorded_at, reverse=True)
        if not args.all:
            # 每支 cut 只留最後寫下來的那一份。要改的通常是它——但「現役」的真正
            # 判準是引擎的 `verify_artifacts`（成品雜湊還對不對得上），不是時間，
            # 所以這裡只說「最新寫入」，不假裝知道哪一份是現役。
            seen: set[str] = set()
            latest = []
            for cut in cuts:
                if cut.cut_id in seen:
                    continue
                seen.add(cut.cut_id)
                latest.append(cut)
            cuts = latest
        _print(
            {
                "episode_id": inspection.episode_id,
                "state": inspection.state,
                "error_code": inspection.error_code,
                "cuts": [
                    {
                        "cut_id": cut.cut_id,
                        "plan_id": cut.plan_id,
                        "timeline": cut.timeline,
                        "recorded_at": datetime.fromtimestamp(
                            _recorded_at(cut), tz=timezone.utc
                        ).isoformat(),
                        "events": [
                            {
                                "event_id": event.event_id,
                                "t0": event.t0,
                                "t1": event.t1,
                                "semantic_kind": event.semantic_kind,
                                "implementation_kind": event.implementation_kind,
                                "display": event.display,
                            }
                            for event in cut.events
                        ],
                    }
                    for cut in cuts
                ],
            }
        )
        return 0
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
            args.current_plan_ref,
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
        format=cast(Literal["long"], _string(row, "format")),
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
                # 「這一段完成的論點」——轉場卡的冷讀回收測試拿它當對照組。
                _optional_string(item, "summary") or "",
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
                optional={"summary"},
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


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _object_rows(value: object, label: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{label} must be object rows")
    return tuple(cast(dict[str, Any], item) for item in value)


def _exact_fields(
    row: Mapping[str, object],
    expected: set[str],
    label: str,
    optional: set[str] | None = None,
) -> bool:
    """欄位精確比對；`optional` 裡的欄位可有可無。

    新增欄位不能讓既有的註冊輸入一律失效——它們都是人手工維護的 JSON。
    """
    present = set(row)
    if optional:
        present -= optional
    if present != expected:
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
