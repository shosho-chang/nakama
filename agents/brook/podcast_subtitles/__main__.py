"""Command-line entry point for the Podcast Subtitle V2 operator verbs."""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import json
import os
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
from pydantic import BaseModel

from shared.schemas.podcast_subtitles_v2 import CorrectionDecision

from .composition import (
    FactoryContextV1,
    ReferenceManifestError,
    build_factory_context,
)
from .facade import PodcastSubtitleFacade, StatusView
from .module import CreateRequest, Interrupted, PodcastSubtitleV2, ProjectRequest
from .native_resolution import ResolveNativeRequest
from .ports import SpeakerTrackInput
from .profiles import profile_by_id

_DEFAULT_FACTORY = "agents.brook.podcast_subtitles.production:build_production"


def _load_repo_environment() -> None:
    """Load the repository `.env` without overriding the invoking process."""

    repo_root = Path(__file__).resolve().parents[3]
    load_dotenv(dotenv_path=repo_root / ".env", override=False)


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _jsonable(getattr(value, field.name)) for field in dataclasses.fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _load_factory(spec: str) -> Callable[[FactoryContextV1], PodcastSubtitleV2]:
    if ":" not in spec:
        raise ValueError("factory must use 'python.module:callable' syntax")
    module_name, attribute = spec.split(":", 1)
    factory = getattr(importlib.import_module(module_name), attribute)
    if not callable(factory):
        raise TypeError(f"configured factory is not callable: {spec}")
    return factory


def _parse_speaker_track(value: str) -> SpeakerTrackInput:
    """Parse one explicit ``LABEL=PATH`` track without filename heuristics."""

    label, separator, raw_path = value.partition("=")
    if not separator or not label or not raw_path:
        raise argparse.ArgumentTypeError(
            "speaker track must use LABEL=PATH with both values explicit"
        )
    try:
        return SpeakerTrackInput(path=Path(raw_path), speaker_label=label)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _validate_speaker_tracks(
    parser: argparse.ArgumentParser,
    tracks: tuple[SpeakerTrackInput, ...],
) -> None:
    """Fail before factory/provider initialization when track inputs are unsafe."""

    if tracks and len(tracks) != 2:
        parser.error("run requires exactly two --mic-track/--speaker-track values")
    labels = tuple(track.speaker_label for track in tracks)
    if len(set(labels)) != len(labels):
        parser.error("speaker track labels must be unique")
    resolved = tuple(os.path.normcase(str(track.path.resolve())) for track in tracks)
    if len(set(resolved)) != len(resolved):
        parser.error("speaker track paths must be distinct")
    missing = tuple(track.path for track in tracks if not track.path.is_file())
    if missing:
        parser.error(f"speaker track is not a file: {missing[0]}")


def _native_resolve_request(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> ResolveNativeRequest:
    """Load exact operator artifacts before any composition/provider initialization."""

    def read_optional(path: Path | None) -> bytes | None:
        return None if path is None else path.read_bytes()

    try:
        return ResolveNativeRequest(
            generation_id=args.generation_id,
            correction_acceptance_verdict=read_optional(args.correction_acceptance_verdict),
            original_confirmation_authorization=read_optional(
                args.original_confirmation_authorization
            ),
            correction_acceptance_policy=read_optional(args.correction_acceptance_policy),
            original_confirmation_policy=read_optional(args.original_confirmation_policy),
            human_audio_receipts=tuple(path.read_bytes() for path in args.human_audio_receipt),
            human_reference_adjudication=read_optional(args.human_reference_adjudication),
            human_original_confirmation_receipts=tuple(
                path.read_bytes() for path in args.human_original_confirmation_receipt
            ),
            reference_authority_proof=read_optional(args.reference_authority_proof),
        )
    except (OSError, TypeError, ValueError) as exc:
        parser.error(f"invalid native resolution request: {exc}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agents.brook.podcast_subtitles",
        description=(
            "Evidence-backed Podcast Subtitle V2. Execution requires a trusted "
            "composition factory; --help never initializes providers."
        ),
    )
    parser.add_argument(
        "--episode-root",
        type=Path,
        help="episode workspace containing the isolated .subtitle-v2 store",
    )
    parser.add_argument(
        "--factory",
        default=_DEFAULT_FACTORY,
        help=(
            "trusted composition factory in python.module:callable form "
            f"(default: {_DEFAULT_FACTORY})"
        ),
    )
    parser.add_argument(
        "--reference-manifest",
        type=Path,
        help=(
            "strict V2 JSON manifest for the episode's exact book/report/outline "
            "index. Repeat on status/review/decide/decide-native/project "
            "for a reference-backed generation so a fresh process reconstructs "
            "the same trust root"
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser(
        "run",
        help="recognize and audit one already-normalized audio file",
    )
    run.add_argument("--episode-id", required=True)
    run.add_argument(
        "--source-audio",
        type=Path,
        required=True,
        help="already-normalized PCM WAV input; Subtitle V2 does not normalize it",
    )
    run.add_argument("--language")
    run.add_argument("--vocabulary", action="append", default=[])
    run.add_argument(
        "--mic-track",
        "--speaker-track",
        dest="speaker_tracks",
        action="append",
        default=[],
        type=_parse_speaker_track,
        metavar="LABEL=PATH",
        help=(
            "isolated mic track with an explicit stable speaker label; repeat exactly "
            "twice. The Module hashes exact track bytes and never infers labels from names"
        ),
    )

    commands.add_parser("status", help="verify and show the active generation")
    review = commands.add_parser("review", help="show unresolved stable-span issues")
    review.add_argument("--generation-id")

    decide = commands.add_parser("decide", help="append one typed Correction Decision")
    decide.add_argument("--generation-id", required=True)
    decide.add_argument("--decision-json", type=Path, required=True)

    decide_native = commands.add_parser(
        "decide-native",
        help="resolve one stored native discovery from exact authorization artifact bytes",
    )
    decide_native.add_argument(
        "--generation-id",
        required=True,
        help="exact parent Generation ID (64 lowercase hex, optionally prefixed by generation-)",
    )
    native_branch = decide_native.add_mutually_exclusive_group(required=True)
    native_branch.add_argument(
        "--correction-acceptance-verdict",
        type=Path,
        help="exact canonical CorrectionAcceptanceVerdictV2 JSON file",
    )
    native_branch.add_argument(
        "--original-confirmation-authorization",
        type=Path,
        help="exact canonical OriginalConfirmationAuthorizationV2 JSON file",
    )
    decide_native.add_argument(
        "--correction-acceptance-policy",
        type=Path,
        help="required exact policy JSON for the correction-acceptance branch",
    )
    decide_native.add_argument(
        "--original-confirmation-policy",
        type=Path,
        help="required exact policy JSON for the original-confirmation branch",
    )
    decide_native.add_argument(
        "--human-audio-receipt",
        type=Path,
        action="append",
        default=[],
        help="exact HumanAudioReviewReceiptV2 JSON file; repeat for each receipt",
    )
    decide_native.add_argument(
        "--human-reference-adjudication",
        type=Path,
        help="exact HumanReferenceAdjudicationReceiptV2 JSON file when required",
    )
    decide_native.add_argument(
        "--human-original-confirmation-receipt",
        type=Path,
        action="append",
        default=[],
        help=("exact HumanOriginalConfirmationReceiptV2 JSON file; repeat for each receipt"),
    )
    decide_native.add_argument(
        "--reference-authority-proof",
        type=Path,
        help="exact complete ReferenceAuthorityProofV2 JSON file when required",
    )

    project = commands.add_parser("project", help="produce a fail-closed Verified Projection")
    project.add_argument("--generation-id", required=True)
    project.add_argument("--profile", default="nakama-zh-hant-16x9")

    # Accept the trust-root option on either side of the verb without letting a
    # subparser default overwrite a global value supplied before the verb.
    for command_parser in (run, review, decide, decide_native, project):
        command_parser.add_argument(
            "--reference-manifest",
            dest="reference_manifest",
            type=Path,
            default=argparse.SUPPRESS,
            help=argparse.SUPPRESS,
        )
    # ``status`` was intentionally created without a local variable above.
    commands.choices["status"].add_argument(
        "--reference-manifest",
        dest="reference_manifest",
        type=Path,
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "run" and args.reference_manifest is None:
        parser.error(
            "run requires an episode-specific --reference-manifest with at least "
            "one enrolled source"
        )
    if args.command == "run":
        _validate_speaker_tracks(parser, tuple(args.speaker_tracks))
    native_request = (
        _native_resolve_request(parser, args) if args.command == "decide-native" else None
    )
    if args.episode_root is None:
        parser.error("execution requires --episode-root")
    _load_repo_environment()
    try:
        factory_context = build_factory_context(
            episode_root=args.episode_root,
            episode_id=args.episode_id if args.command == "run" else None,
            reference_manifest=args.reference_manifest,
        )
    except ReferenceManifestError as exc:
        parser.error(str(exc))
    module = _load_factory(args.factory)(factory_context)
    if not isinstance(module, PodcastSubtitleV2):
        raise TypeError("composition factory must return PodcastSubtitleV2")
    if factory_context.reference_bundle is not None:
        factory_context.reference_bundle.assert_module_binding(module)
    facade = PodcastSubtitleFacade(module)
    if args.command == "run":
        result = facade.run(
            CreateRequest(
                episode_id=args.episode_id,
                source_audio=args.source_audio,
                language_hint=args.language,
                vocabulary=tuple(args.vocabulary),
                speaker_tracks=tuple(args.speaker_tracks),
                reference_enrollments=factory_context.reference_enrollments,
            )
        )
    elif args.command == "status":
        result = facade.status()
    elif args.command == "review":
        result = facade.review(args.generation_id)
    elif args.command == "decide":
        decision = CorrectionDecision.model_validate_json(
            args.decision_json.read_text(encoding="utf-8")
        )
        result = facade.decide(args.generation_id, decision)
    elif args.command == "decide-native":
        if native_request is None:  # pragma: no cover - command dispatch invariant
            raise AssertionError("native request was not prepared")
        result = facade.decide_native(native_request)
    elif args.command == "project":
        result = facade.project(ProjectRequest(args.generation_id, profile_by_id(args.profile)))
    else:  # pragma: no cover - argparse owns the closed command set
        raise AssertionError(args.command)
    print(json.dumps(_jsonable(result), ensure_ascii=False, sort_keys=True, indent=2))
    if isinstance(result, Interrupted):
        return 2
    if isinstance(result, StatusView) and result.state != "complete":
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess smoke test
    raise SystemExit(main())
