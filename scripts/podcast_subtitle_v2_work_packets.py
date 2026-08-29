#!/usr/bin/env python3
"""Offline operator seam for Podcast Subtitle V2 subscription work packets.

This CLI never invokes a provider.  It discovers frozen requests, renders the exact
worker contract, validates an offline candidate with the production adapter, and
accepts the exact candidate bytes at the one deterministic response path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

from pydantic import ValidationError

from agents.brook.podcast_subtitles.adapters.semantic import (
    SemanticAnalyzerAdapter,
    semantic_subscription_response_json_schema,
    semantic_subscription_worker_instruction,
    validate_semantic_subscription_response,
)
from agents.brook.podcast_subtitles.audio_audit_execution import (
    AudioAuditExecutionError,
    audio_audit_subscription_worker_instruction,
    build_audio_audit_adapter_identity,
    validate_audio_audit_subscription_response,
)
from agents.brook.podcast_subtitles.hashing import canonical_json_bytes, hash_object
from agents.brook.podcast_subtitles.ports import AdapterIntegrityError
from agents.brook.podcast_subtitles.text_audit_execution import (
    TextAuditExecutionError,
    build_text_audit_adapter_identity,
    text_audit_subscription_worker_instruction,
    validate_text_audit_subscription_response,
)
from shared.schemas.podcast_subtitles_v2_audio_audit import (
    AudioAuditProviderRequestV2,
    AudioAuditProviderResponseV2,
)
from shared.schemas.podcast_subtitles_v2_text_audit import (
    TextAuditProviderRequestV2,
    TextAuditProviderResponseV2,
)

AdapterKind = Literal["text-audit", "audio-audit", "semantic"]
_MAX_REQUEST_BYTES = 64 * 1024 * 1024
_MAX_CANDIDATE_BYTES = 16 * 1024 * 1024


class WorkPacketOperatorError(ValueError):
    """A subscription work artifact failed closed."""


@dataclass(frozen=True, slots=True)
class WorkItem:
    adapter: AdapterKind
    request_id: str
    request_path: Path
    response_path: Path
    status: Literal["pending", "accepted"]
    clip_path: Path | None = None


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON constant: {value}")


def _strict_json(raw: bytes, *, label: str) -> dict[str, object]:
    if not raw or len(raw) > _MAX_REQUEST_BYTES:
        raise WorkPacketOperatorError(f"{label} is empty or exceeds the bounded size")
    try:
        decoded = raw.decode("utf-8")
        value = json.loads(
            decoded,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise WorkPacketOperatorError(f"{label} is not strict UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkPacketOperatorError(f"{label} must be one JSON object")
    return value


def _absolute_unlinked(path: Path, *, must_exist: bool, label: str) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path)))
    try:
        resolved = absolute.resolve(strict=must_exist)
    except OSError as exc:
        raise WorkPacketOperatorError(f"{label} cannot be resolved: {exc}") from exc
    if resolved != absolute:
        raise WorkPacketOperatorError(f"{label} must not traverse a link or reparse point")
    return absolute


def _workspace_root(episode_root: str | Path) -> Path:
    episode = _absolute_unlinked(Path(episode_root), must_exist=True, label="episode root")
    return episode / ".subtitle-v2" / "subscription-work"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _semantic_contract(packet_id: str) -> dict[str, object]:
    return {
        "schema_version": 5,
        "work_packet_id": packet_id,
        "boundaries": [
            {
                "edge_index": "exact owned edge index",
                "left_token_id": "exact owned left token ID",
                "right_token_id": "exact owned right token ID",
                "cue_relation": "forbidden|discouraged|preferred|neutral",
                "line_relation": "forbidden|discouraged|preferred|neutral",
                "strength": "0..1",
            }
        ],
        "coverage": "every owned boundary exactly once; no unowned boundary",
    }


def _validate_semantic_packet(packet: dict[str, object], raw: bytes) -> str:
    if canonical_json_bytes(packet) != raw:
        raise WorkPacketOperatorError("semantic request is not canonical exact bytes")
    if (
        packet.get("schema_version") != 5
        or packet.get("task") != "podcast_subtitle_v2_semantic_boundary_assessment"
        or packet.get("adapter") != "llm-semantic-analyzer"
        or packet.get("adapter_version") != "6"
    ):
        raise WorkPacketOperatorError("semantic request declares an unknown adapter or contract")
    packet_id = packet.get("work_packet_id")
    if not isinstance(packet_id, str) or not packet_id.startswith("semantic-work-"):
        raise WorkPacketOperatorError("semantic request lacks a valid work packet ID")
    hashed_body = {
        key: value
        for key, value in packet.items()
        if key not in {"work_packet_id", "response_contract"}
    }
    if packet_id != f"semantic-work-{hash_object(hashed_body)}":
        raise WorkPacketOperatorError("semantic request content-addressed identity drift")
    if packet.get("response_contract") != _semantic_contract(packet_id):
        raise WorkPacketOperatorError("semantic request response contract drift")
    try:
        adapter = SemanticAnalyzerAdapter(
            model=str(packet.get("model", "")),
            model_version=str(packet.get("model_version", "")),
            execution_mode="subscription",
        )
        identity = adapter.identity
    except (AdapterIntegrityError, ValueError) as exc:
        raise WorkPacketOperatorError(
            f"semantic request executable identity is invalid: {exc}"
        ) from exc
    identity_fields = {
        "adapter": identity.adapter_name,
        "adapter_version": identity.adapter_version,
        "runtime_hash": identity.runtime_hash,
        "adapter_code_hash": identity.adapter_code_hash,
        "config_hash": identity.config_hash,
        "adapter_identity_hash": identity.content_hash,
    }
    if any(packet.get(key) != value for key, value in identity_fields.items()):
        raise WorkPacketOperatorError("semantic request executable identity drift")
    return packet_id


def _load_item(
    episode_root: str | Path,
    request_path: str | Path,
) -> tuple[WorkItem, object, bytes]:
    workspace = _workspace_root(episode_root)
    request = _absolute_unlinked(Path(request_path), must_exist=True, label="request path")
    try:
        relative = request.relative_to(workspace)
    except ValueError as exc:
        raise WorkPacketOperatorError(
            "request path is outside the episode subscription workspace"
        ) from exc
    parts = relative.parts
    expected_layouts = {
        ("text-audit", "requests"): "text-audit",
        ("audio-audit", "requests"): "audio-audit",
        ("semantic", "packets"): "semantic",
    }
    if len(parts) != 3 or parts[:2] not in expected_layouts:
        raise WorkPacketOperatorError("request path is not a known subscription adapter layout")
    adapter = expected_layouts[parts[:2]]
    raw = request.read_bytes()
    payload = _strict_json(raw, label=f"{adapter} request")
    typed: object
    try:
        if adapter == "text-audit":
            typed = TextAuditProviderRequestV2.model_validate_json(raw)
            assert isinstance(typed, TextAuditProviderRequestV2)
            request_id = typed.id
            expected_identity = build_text_audit_adapter_identity(
                adapter=typed.adapter_identity.adapter,
                adapter_version=typed.adapter_identity.adapter_version,
                model=typed.adapter_identity.model,
                model_version=typed.adapter_identity.model_version,
                execution_mode="subscription",
            )
            if (
                typed.adapter_identity.execution_mode != "subscription"
                or typed.adapter_identity != expected_identity
                or canonical_json_bytes(typed) != raw
            ):
                raise WorkPacketOperatorError("text-audit request executable identity drift")
            response = workspace / "text-audit" / "responses" / f"{request_id}.response.json"
            clip = None
        elif adapter == "audio-audit":
            typed = AudioAuditProviderRequestV2.model_validate_json(raw)
            assert isinstance(typed, AudioAuditProviderRequestV2)
            request_id = typed.id
            expected_identity = build_audio_audit_adapter_identity(
                adapter=typed.adapter_identity.adapter,
                adapter_version=typed.adapter_identity.adapter_version,
                model=typed.adapter_identity.model,
                model_version=typed.adapter_identity.model_version,
                execution_mode="subscription",
            )
            if (
                typed.adapter_identity.execution_mode != "subscription"
                or typed.adapter_identity != expected_identity
                or canonical_json_bytes(typed) != raw
            ):
                raise WorkPacketOperatorError("audio-audit request executable identity drift")
            response = workspace / "audio-audit" / "responses" / f"{request_id}.response.json"
            clip = workspace / "audio-audit" / "clips" / f"{request_id}.wav"
            clip = _absolute_unlinked(clip, must_exist=True, label="audio clip")
        else:
            typed = payload
            request_id = _validate_semantic_packet(payload, raw)
            response = workspace / "semantic" / "responses" / f"{request_id}.response.json"
            clip = None
    except ValidationError as exc:
        raise WorkPacketOperatorError(f"{adapter} request violates its strict schema") from exc
    if request.name != f"{request_id}.json":
        raise WorkPacketOperatorError("request filename does not match its content-addressed ID")
    response = _absolute_unlinked(response, must_exist=response.exists(), label="response path")
    return (
        WorkItem(
            adapter=adapter,
            request_id=request_id,
            request_path=request,
            response_path=response,
            status="accepted" if response.is_file() else "pending",
            clip_path=clip,
        ),
        typed,
        raw,
    )


def discover_work_items(episode_root: str | Path) -> tuple[WorkItem, ...]:
    workspace = _workspace_root(episode_root)
    layouts = (
        workspace / "text-audit" / "requests",
        workspace / "audio-audit" / "requests",
        workspace / "semantic" / "packets",
    )
    items: list[WorkItem] = []
    for directory in layouts:
        if not directory.exists():
            continue
        _absolute_unlinked(directory, must_exist=True, label="work packet directory")
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            if not path.is_file():
                raise WorkPacketOperatorError(f"unexpected non-file work artifact: {path}")
            item, _, _ = _load_item(episode_root, path)
            items.append(item)
    return tuple(sorted(items, key=lambda item: (item.adapter, item.request_id)))


def render_worker_bundle(
    episode_root: str | Path,
    request_path: str | Path,
) -> dict[str, object]:
    item, request, raw = _load_item(episode_root, request_path)
    if item.adapter == "text-audit":
        instruction = text_audit_subscription_worker_instruction()
        schema = TextAuditProviderResponseV2.model_json_schema()
        provider_gate = "text_capable_subscription_worker"
    elif item.adapter == "audio-audit":
        instruction = audio_audit_subscription_worker_instruction()
        schema = AudioAuditProviderResponseV2.model_json_schema()
        provider_gate = "human_or_audio_capable_subscription_worker_required"
    else:
        instruction = (
            semantic_subscription_worker_instruction()
            + "\nOperator coverage rule: return every owned boundary exactly once and no others."
        )
        schema = semantic_subscription_response_json_schema()
        provider_gate = "text_capable_subscription_worker"
    bundle: dict[str, object] = {
        "schema_version": 1,
        "contract": "podcast-subtitle-v2-subscription-worker-v1",
        "adapter": item.adapter,
        "request": {
            "path": str(item.request_path),
            "sha256": _sha256(raw),
            "size_bytes": len(raw),
        },
        "response": {
            "exact_path": str(item.response_path),
            "must_not_exist_before_accept": True,
            "serialization": "strict-json-utf8-preserve-exact-candidate-bytes",
        },
        "provider_gate": provider_gate,
        "worker_instruction": instruction,
        "request_json": json.loads(raw.decode("utf-8")),
        "response_json_schema": schema,
    }
    if item.clip_path is not None:
        clip_bytes = item.clip_path.read_bytes()
        assert isinstance(request, AudioAuditProviderRequestV2)
        if _sha256(clip_bytes) != request.clip.binding.content_hash:
            raise WorkPacketOperatorError("audio clip drifted from the frozen request")
        bundle["audio_clip"] = {
            "path": str(item.clip_path),
            "sha256": _sha256(clip_bytes),
            "size_bytes": len(clip_bytes),
            "clip_start_ms": request.clip.clip_start_ms,
            "clip_end_ms": request.clip.clip_end_ms,
        }
    return bundle


def _candidate_bytes(candidate_path: str | Path) -> tuple[Path, bytes]:
    candidate = _absolute_unlinked(
        Path(candidate_path), must_exist=True, label="candidate response"
    )
    raw = candidate.read_bytes()
    if not raw or len(raw) > _MAX_CANDIDATE_BYTES:
        raise WorkPacketOperatorError("candidate response is empty or exceeds the bounded size")
    return candidate, raw


def _validate(item: WorkItem, request: object, request_bytes: bytes, response_bytes: bytes) -> None:
    try:
        if item.adapter == "text-audit":
            validate_text_audit_subscription_response(request_bytes, response_bytes)
        elif item.adapter == "audio-audit":
            assert item.clip_path is not None
            validate_audio_audit_subscription_response(
                request_bytes,
                item.clip_path.read_bytes(),
                response_bytes,
            )
        else:
            assert isinstance(request, Mapping)
            validate_semantic_subscription_response(request, response_bytes)
    except (TextAuditExecutionError, AudioAuditExecutionError, AdapterIntegrityError) as exc:
        raise WorkPacketOperatorError(
            f"candidate response failed {item.adapter} validation: {exc}"
        ) from exc


def validate_candidate(
    episode_root: str | Path,
    request_path: str | Path,
    candidate_path: str | Path,
) -> WorkItem:
    item, request, request_bytes = _load_item(episode_root, request_path)
    _, response_bytes = _candidate_bytes(candidate_path)
    _validate(item, request, request_bytes, response_bytes)
    return item


def _atomic_accept(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise WorkPacketOperatorError(f"response already exists; overwrite is forbidden: {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise WorkPacketOperatorError(
                f"response already exists; overwrite is forbidden: {path}"
            ) from exc
        except OSError as exc:
            raise WorkPacketOperatorError(f"atomic response acceptance failed: {exc}") from exc
        if path.read_bytes() != payload:
            raise WorkPacketOperatorError("accepted response bytes differ from candidate")
    finally:
        temporary.unlink(missing_ok=True)


def accept_candidate(
    episode_root: str | Path,
    request_path: str | Path,
    candidate_path: str | Path,
) -> Path:
    item, request, request_bytes = _load_item(episode_root, request_path)
    if item.response_path.exists():
        raise WorkPacketOperatorError(
            f"response already exists; overwrite is forbidden: {item.response_path}"
        )
    candidate, response_bytes = _candidate_bytes(candidate_path)
    _validate(item, request, request_bytes, response_bytes)
    # Close the request/candidate TOCTOU window before the atomic no-overwrite link.
    if item.request_path.read_bytes() != request_bytes:
        raise WorkPacketOperatorError("request drifted during candidate acceptance")
    if candidate.read_bytes() != response_bytes:
        raise WorkPacketOperatorError("candidate response drifted during acceptance")
    _atomic_accept(item.response_path, response_bytes)
    return item.response_path


def _item_json(item: WorkItem) -> dict[str, object]:
    return {
        "adapter": item.adapter,
        "request_id": item.request_id,
        "request_path": str(item.request_path),
        "response_path": str(item.response_path),
        "status": item.status,
        "clip_path": str(item.clip_path) if item.clip_path is not None else None,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    listing = subcommands.add_parser("list", help="List and verify subscription work packets")
    listing.add_argument("--episode-root", required=True, type=Path)
    for name in ("render", "validate", "accept"):
        command = subcommands.add_parser(name)
        command.add_argument("--episode-root", required=True, type=Path)
        command.add_argument("--request", required=True, type=Path)
        if name in {"validate", "accept"}:
            command.add_argument("--candidate", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            output: object = [_item_json(item) for item in discover_work_items(args.episode_root)]
        elif args.command == "render":
            output = render_worker_bundle(args.episode_root, args.request)
        elif args.command == "validate":
            output = {
                "valid": True,
                **_item_json(validate_candidate(args.episode_root, args.request, args.candidate)),
            }
        else:
            accepted = accept_candidate(args.episode_root, args.request, args.candidate)
            output = {"accepted": True, "response_path": str(accepted)}
    except WorkPacketOperatorError as exc:
        parser.error(str(exc))
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
