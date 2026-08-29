"""Isolated Codex worker Adapter for current Finished Cut stage requests."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from ._projection import _ACTIVE_COMPONENT_LANES, _ACTIVE_SEMANTIC_KINDS
from ._records import (
    DirectorEventProposal,
    DPEventProposal,
    StageName,
    StageProposal,
    StageRequest,
    VisualEventProposal,
)
from ._semantic import SemanticDispatchOutcome
from ._worker_packet import (
    JsonValue,
    NamedMedia,
    StagePacket,
    WorkerPacketMaterializer,
    expected_format_policy,
    worker_packet_document,
)

_ASSET_REFERENCE_RE = re.compile(r"^asset-sha256:[0-9a-f]{64}$")
_LOGICAL_MEDIA_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_DIAGNOSTICS = 32
_MAX_DIAGNOSTIC_DETAIL_CHARS = 512
_MAX_PROCESS_OUTPUT_CHARS = 4_096
_TRUNCATION_MARKER = "\n...[truncated]...\n"
DiagnosticCode = Literal[
    "packet_rejected",
    "process_failed",
    "process_timeout",
    "output_missing",
    "output_invalid",
    "dispatch_error",
]


@dataclass(frozen=True, slots=True)
class CodexProcessResult:
    returncode: int | None
    timed_out: bool = False
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True, slots=True)
class CodexDispatchDiagnostic:
    request_id: str
    stage: StageName
    code: DiagnosticCode
    detail: str


class CodexProcessRunner(Protocol):
    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        prompt: str,
        timeout_sec: float,
    ) -> CodexProcessResult: ...


class SubprocessCodexProcessRunner:
    """Production subprocess Adapter; it never chooses a workspace or retries."""

    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        prompt: str,
        timeout_sec: float,
    ) -> CodexProcessResult:
        child_env = os.environ.copy()
        for name in (
            "CODEX_PERMISSION_PROFILE",
            "CODEX_SANDBOX_NETWORK_DISABLED",
            "CODEX_SESSION_ID",
            "CODEX_THREAD_ID",
        ):
            child_env.pop(name, None)
        try:
            completed = subprocess.run(
                argv,
                input=prompt,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec,
                check=False,
                env=child_env,
            )
        except subprocess.TimeoutExpired as error:
            return CodexProcessResult(
                returncode=None,
                timed_out=True,
                stdout=_bounded_process_output(error.stdout),
                stderr=_bounded_process_output(error.stderr),
            )
        return CodexProcessResult(
            returncode=completed.returncode,
            stdout=_bounded_process_output(completed.stdout),
            stderr=_bounded_process_output(completed.stderr),
        )


def _resolve_codex_executable(
    explicit: str | None,
    *,
    platform_name: str | None = None,
    which: Callable[[str], str | None] | None = None,
) -> str:
    if explicit is not None:
        normalized = explicit.strip()
        if not normalized:
            raise ValueError("explicit Codex executable cannot be empty")
        return normalized
    platform = platform_name or os.name
    resolver = which or shutil.which
    candidates = ("codex",)
    if platform == "nt":
        resolved_cmd = resolver("codex.cmd")
        if resolved_cmd is not None and "windowsapps" not in resolved_cmd.lower():
            return resolved_cmd
        appdata = os.environ.get("APPDATA")
        npm_cmd = Path(appdata) / "npm" / "codex.cmd" if appdata else None
        if npm_cmd is not None:
            try:
                if npm_cmd.is_file():
                    return str(npm_cmd)
            except OSError:
                # Sandboxed launchers may deny metadata probes while CreateProcess can
                # still execute the explicitly named npm shim.
                return str(npm_cmd)
        candidates = ("codex.exe", "codex")
    for candidate in candidates:
        resolved = resolver(candidate)
        if resolved is None:
            continue
        if platform == "nt" and "windowsapps" in resolved.lower():
            continue
        return resolved
    if platform == "nt":
        raise FileNotFoundError("no safe Codex executable is available on Windows")
    return "codex"


class CodexSemanticAdapter:
    """Dispatch each current request at most once to one isolated Codex process."""

    def __init__(
        self,
        *,
        process_runner: CodexProcessRunner,
        packet_materializer: WorkerPacketMaterializer,
        codex_executable: str | None = None,
        timeout_sec: float = 900.0,
    ) -> None:
        self._process_runner = process_runner
        self._packet_materializer = packet_materializer
        self._codex_executable = _resolve_codex_executable(codex_executable)
        self._timeout_sec = timeout_sec
        self._dispatched_request_ids: set[str] = set()
        self._requests_by_id: dict[str, StageRequest] = {}
        self._outcomes: dict[str, SemanticDispatchOutcome] = {}
        self._diagnostics: deque[CodexDispatchDiagnostic] = deque(maxlen=_MAX_DIAGNOSTICS)

    @property
    def diagnostics(self) -> tuple[CodexDispatchDiagnostic, ...]:
        return tuple(self._diagnostics)

    def proposal_for(self, request: StageRequest) -> StageProposal | None:
        outcome = self.dispatch(request)
        return outcome.proposal if outcome.state == "ready" else None

    def dispatch(self, request: StageRequest) -> SemanticDispatchOutcome:
        existing_request = self._requests_by_id.get(request.request_id)
        if existing_request is not None and existing_request != request:
            raise ValueError("semantic request ID was reused for another request")
        self._requests_by_id[request.request_id] = request
        if request.request_id in self._dispatched_request_ids:
            return self._outcomes[request.request_id]
        self._dispatched_request_ids.add(request.request_id)
        if request.stage == "director" and request.editorial_context is None:
            return self._failure(
                request,
                "packet_rejected",
                "Director stage requires the current editorial context",
            )
        if request.stage == "director" and request.editorial_context is not None:
            context = request.editorial_context
            if (context.episode_id, context.cut_id, context.format) != (
                request.episode_id,
                request.cut_id,
                request.format,
            ):
                return self._failure(
                    request,
                    "packet_rejected",
                    "editorial context does not belong to the current cut",
                )
        if request.stage != "director" and request.editorial_context is not None:
            return self._failure(
                request,
                "packet_rejected",
                "editorial context is only allowed for the Director stage",
            )
        if request.stage == "dp" and not _dp_placement_candidates_are_valid(request):
            return self._failure(
                request,
                "packet_rejected",
                "DP placement candidates do not match current Director events",
            )
        if request.stage != "dp" and request.placement_candidates:
            return self._failure(
                request,
                "packet_rejected",
                "placement candidates are only allowed for the DP stage",
            )
        try:
            packet = self._packet_materializer.materialize(request)
            if not _packet_is_allowed(request, packet):
                return self._failure(
                    request,
                    "packet_rejected",
                    "stage packet violates its allowlist",
                )
            with tempfile.TemporaryDirectory(prefix="nakama-finished-cut-codex-") as temporary:
                workspace = Path(temporary).resolve()
                packet_path = workspace / "packet.json"
                schema_path = workspace / "response-schema.json"
                output_path = workspace / "last-message.json"
                _write_media(workspace, packet.media)
                serialized_packet = worker_packet_document(request, packet)
                if request.stage == "dp":
                    request_document = serialized_packet["request"]
                    if not isinstance(request_document, dict):
                        raise ValueError("worker packet request envelope is invalid")
                    request_document["placement_candidates"] = [
                        {
                            "event_id": candidate.event_id,
                            "cues": [
                                {
                                    "cue_id": cue.cue_id,
                                    "text": cue.text,
                                    "t0": cue.t0,
                                    "t1": cue.t1,
                                    "section_id": cue.section_id,
                                }
                                for cue in candidate.cues
                            ],
                        }
                        for candidate in request.placement_candidates
                    ]
                if _packet_contains_forbidden_reference(serialized_packet):
                    return self._failure(
                        request,
                        "packet_rejected",
                        "stage packet contains a path or forbidden metadata",
                    )
                packet_path.write_text(
                    json.dumps(serialized_packet, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                schema_path.write_text(
                    json.dumps(_response_schema(request.stage), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                argv = (
                    self._codex_executable,
                    "exec",
                    "--ignore-user-config",
                    "--sandbox",
                    "read-only",
                    "--ephemeral",
                    "--skip-git-repo-check",
                    "--output-schema",
                    str(schema_path),
                    "--output-last-message",
                    str(output_path),
                    "-",
                )
                result = self._process_runner.run(
                    argv,
                    cwd=workspace,
                    prompt=_stage_prompt(request.stage),
                    timeout_sec=self._timeout_sec,
                )
                if result.timed_out:
                    return self._failure(
                        request,
                        "process_timeout",
                        "Codex process timed out",
                    )
                if result.returncode != 0:
                    return self._failure(
                        request,
                        "process_failed",
                        f"Codex exit={result.returncode}; stderr={result.stderr}",
                    )
                if not output_path.is_file():
                    return self._failure(
                        request,
                        "output_missing",
                        "Codex last-message output is missing",
                    )
                try:
                    proposal = _parse_stage_proposal(
                        request,
                        output_path.read_text(encoding="utf-8"),
                    )
                    outcome = SemanticDispatchOutcome(
                        request_id=request.request_id,
                        state="ready",
                        proposal=proposal,
                    )
                    self._outcomes[request.request_id] = outcome
                    return outcome
                except (OSError, TypeError, ValueError):
                    return self._failure(
                        request,
                        "output_invalid",
                        "Codex last-message output is invalid",
                    )
        except (OSError, TypeError, ValueError) as error:
            return self._failure(request, "dispatch_error", str(error))

    def outcome_for(self, request_id: str) -> SemanticDispatchOutcome | None:
        return self._outcomes.get(request_id)

    def outcome_for_request(
        self,
        request: StageRequest,
    ) -> SemanticDispatchOutcome | None:
        if self._requests_by_id.get(request.request_id) != request:
            return None
        return self._outcomes.get(request.request_id)

    def dispatch_state(self, request_id: str) -> Literal["unclaimed", "claimed", "completed"]:
        if request_id in self._outcomes:
            return "completed"
        if request_id in self._dispatched_request_ids:
            return "claimed"
        return "unclaimed"

    def _failure(
        self,
        request: StageRequest,
        code: DiagnosticCode,
        detail: str,
    ) -> SemanticDispatchOutcome:
        bounded_detail = _bounded_head_and_tail(detail, _MAX_DIAGNOSTIC_DETAIL_CHARS)
        self._record(request, code, bounded_detail)
        outcome = SemanticDispatchOutcome(
            request_id=request.request_id,
            state="failed",
            reason_code=f"semantic_{code}",
            diagnostic=bounded_detail,
        )
        self._outcomes[request.request_id] = outcome
        return outcome

    def _record(self, request: StageRequest, code: DiagnosticCode, detail: str) -> None:
        self._diagnostics.append(
            CodexDispatchDiagnostic(
                request_id=request.request_id,
                stage=request.stage,
                code=code,
                detail=detail,
            )
        )


def _bounded_process_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    return _bounded_head_and_tail(text, _MAX_PROCESS_OUTPUT_CHARS)


def _bounded_head_and_tail(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    payload_chars = limit - len(_TRUNCATION_MARKER)
    head_chars = payload_chars // 2
    tail_chars = payload_chars - head_chars
    return value[:head_chars] + _TRUNCATION_MARKER + value[-tail_chars:]


def _packet_contains_forbidden_reference(value: JsonValue) -> bool:
    if isinstance(value, dict):
        forbidden_keys = {
            "absolute_path",
            "episode_path",
            "legacy_path",
            "prior_event",
            "prior_recipe",
            "prior_title",
            "recipe_identity",
            "selection_metadata",
        }
        return any(
            key.lower() in forbidden_keys
            or key.lower().endswith("_path")
            or _packet_contains_forbidden_reference(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_packet_contains_forbidden_reference(child) for child in value)
    if not isinstance(value, str):
        return False
    normalized = value.strip().replace("\\", "/")
    lowered = normalized.lower()
    return (
        "visual-pipeline" in lowered
        or lowered.startswith("file://")
        or lowered.startswith("//")
        or lowered.startswith("/")
        or re.match(r"^[a-z]:/", lowered) is not None
    )


def _dp_placement_candidates_are_valid(request: StageRequest) -> bool:
    expected = tuple(event for event in request.events if not event.intentional_aroll)
    if tuple(candidate.event_id for candidate in request.placement_candidates) != tuple(
        event.event_id for event in expected
    ):
        return False
    for event, candidate in zip(expected, request.placement_candidates, strict=True):
        cue_ids = tuple(cue.cue_id for cue in candidate.cues)
        if cue_ids != event.master_cue_ids or not candidate.cues:
            return False
        if any(
            not cue.cue_id.strip()
            or not cue.text.strip()
            or cue.t0 < event.t0
            or cue.t1 > event.t1
            or cue.t0 >= cue.t1
            or cue.section_id != event.section_id
            for cue in candidate.cues
        ):
            return False
    return True


def _packet_is_allowed(request: StageRequest, packet: StagePacket) -> bool:
    if packet.stage != request.stage or packet.format_policy != expected_format_policy(
        request.format, request.stage
    ):
        return False
    if request.stage == "director":
        return not packet.payload and not packet.media
    if request.stage == "visual_review":
        return _visual_packet_is_allowed(request, packet)
    if request.stage != "dp" or packet.media or set(packet.payload) != {"catalog"}:
        return False
    catalog = packet.payload["catalog"]
    if not isinstance(catalog, list):
        return False
    expected_references = request.worker_asset_refs
    expected_items = request.worker_catalog_items
    if (
        len(catalog) != len(expected_items)
        or tuple(item.reference for item in expected_items) != expected_references
    ):
        return False
    observed_references: list[str] = []
    required_keys = {
        "reference",
        "kind",
        "visual_summary",
        "width",
        "height",
        "duration_sec",
    }
    for item, expected_item in zip(catalog, expected_items, strict=True):
        if not isinstance(item, dict) or set(item) != required_keys:
            return False
        reference = item["reference"]
        kind = item["kind"]
        visual_summary = item["visual_summary"]
        width = item["width"]
        height = item["height"]
        duration_sec = item["duration_sec"]
        if (
            not isinstance(reference, str)
            or _ASSET_REFERENCE_RE.fullmatch(reference) is None
            or not isinstance(kind, str)
            or kind not in {"stock", "photo", "non_editorial_clip"}
            or not isinstance(visual_summary, str)
            or not visual_summary.strip()
            or not isinstance(width, int)
            or isinstance(width, bool)
            or width <= 0
            or not isinstance(height, int)
            or isinstance(height, bool)
            or height <= 0
        ):
            return False
        if kind == "photo":
            if duration_sec is not None:
                return False
        elif (
            not isinstance(duration_sec, (int, float))
            or isinstance(duration_sec, bool)
            or duration_sec <= 0
        ):
            return False
        if item != {
            "reference": expected_item.reference,
            "kind": expected_item.kind.value,
            "visual_summary": expected_item.visual_summary,
            "width": expected_item.width,
            "height": expected_item.height,
            "duration_sec": expected_item.duration_sec,
        }:
            return False
        observed_references.append(reference)
    return tuple(observed_references) == expected_references


def _visual_packet_is_allowed(request: StageRequest, packet: StagePacket) -> bool:
    if set(packet.payload) != {"built_components"}:
        return False
    built_components = packet.payload["built_components"]
    if not isinstance(built_components, list):
        return False
    expected_events = {
        event.event_id: event for event in request.events if not event.intentional_aroll
    }
    expected_builds = {built.component_id: built for built in request.built_components}
    if len(built_components) != len(expected_builds):
        return False
    required_keys = {
        "component_id",
        "event_id",
        "final_asset_ref",
        "inspection_ref",
    }
    component_ids: set[str] = set()
    observed_event_ids: list[str] = []
    final_asset_refs: list[str] = []
    inspection_refs: list[str] = []
    for row in built_components:
        if not isinstance(row, dict) or set(row) != required_keys:
            return False
        component_id = row["component_id"]
        event_id = row["event_id"]
        final_asset_ref = row["final_asset_ref"]
        inspection_ref = row["inspection_ref"]
        expected_build = (
            expected_builds.get(component_id) if isinstance(component_id, str) else None
        )
        expected_inspection_ref = (
            expected_build.inspection_ref or final_asset_ref
            if expected_build is not None and isinstance(final_asset_ref, str)
            else None
        )
        if (
            not isinstance(component_id, str)
            or not component_id.strip()
            or component_id in component_ids
            or not isinstance(event_id, str)
            or event_id not in expected_events
            or not isinstance(final_asset_ref, str)
            or _ASSET_REFERENCE_RE.fullmatch(final_asset_ref) is None
            or not isinstance(inspection_ref, str)
            or _ASSET_REFERENCE_RE.fullmatch(inspection_ref) is None
            or expected_build is None
            or expected_build.event_id != event_id
            or expected_build.final_asset_ref != final_asset_ref
            or expected_inspection_ref != inspection_ref
        ):
            return False
        component_ids.add(component_id)
        observed_event_ids.append(event_id)
        final_asset_refs.append(final_asset_ref)
        inspection_refs.append(inspection_ref)
    if tuple(observed_event_ids) != tuple(expected_events):
        return False
    if len(packet.media) != len(built_components):
        return False
    logical_names: set[str] = set()
    media_asset_refs: list[str] = []
    media_inspection_refs: list[str] = []
    for media in packet.media:
        if (
            _LOGICAL_MEDIA_NAME_RE.fullmatch(media.logical_name) is None
            or ".." in media.logical_name
            or media.logical_name in logical_names
            or media.mime_type not in {"image/png", "image/jpeg", "video/mp4"}
            or media.inspection_kind not in {"preview_frame", "contact_sheet", "preview_clip"}
            or not media.bytes
            or _ASSET_REFERENCE_RE.fullmatch(media.inspection_ref) is None
            or _ASSET_REFERENCE_RE.fullmatch(media.for_asset_ref) is None
        ):
            return False
        logical_names.add(media.logical_name)
        media_asset_refs.append(media.for_asset_ref)
        media_inspection_refs.append(media.inspection_ref)
    return tuple(media_asset_refs) == tuple(final_asset_refs) and tuple(
        media_inspection_refs
    ) == tuple(inspection_refs)


def _write_media(workspace: Path, media: tuple[NamedMedia, ...]) -> None:
    if not media:
        return
    media_root = workspace / "media"
    media_root.mkdir()
    for item in media:
        (media_root / item.logical_name).write_bytes(item.bytes)


def _stage_prompt(stage: StageName) -> str:
    if stage == "director":
        return (
            "Read packet.json. Act only as the Finished Cut Director: identify complete semantic "
            "highlight events from the current tight transcript and canonical sections. Apply "
            "the aggregate anchor contract to every event: master_cue_ids must be unique, in "
            "packet cue order, contiguous, and entirely within one canonical section. For every "
            "event_retry, echo current_event.master_cue_ids exactly and change only the requested "
            "event fields; the retry cannot replace its Semantic Evidence Range. For every "
            "section with transition_before=true, return exactly one chapter event whose "
            "master_cue_ids contains only that section's first cue. Never use "
            "semantic_kind=chapter for any other event, and never infer a chapter boundary from "
            "semantics. Apply "
            "Renee "
            "retention judgement inside this Director pass. Obey format_policy.editorial_brief "
            "when present and the other core/static constraints without treating them as "
            "authority to alter current anchors. Do not run a Brand "
            "lens, infer prior "
            "titles, or inspect any path outside this workspace. Return only the JSON object "
            "required "
            "by response-schema.json."
        )
    if stage == "dp":
        return (
            "Read packet.json. Act only as the Finished Cut DP: preserve every current Director "
            "event; choose placement_cue_ids only from that event's exact placement_candidates, "
            "then choose one permitted implementation/lane and, when required, one opaque "
            "reference from the filtered neutral catalog. Obey format_policy.editorial_brief "
            "when present, the other core/static constraints, and the exact projection "
            "combinations. For every non-chapter visual, choose the smallest useful contiguous "
            "cue subset rather than the complete Semantic Evidence Range: hero_title and "
            "identity_card placement must be no longer than 8 seconds; "
            "stock_video, photo, non_editorial_clip, and person_inset placement must be no "
            "longer than 12 seconds. Calculate duration from the selected candidates' t0/t1. "
            "intentional_aroll must use an empty placement_cue_ids array; chapter "
            "must echo all its Director master_cue_ids because core owns canonical chapter timing. "
            "Do not infer or recreate prior titles, "
            "recipes, or paths. Return only the JSON object required by response-schema.json."
        )
    if stage == "visual_review":
        return (
            "Read packet.json and inspect every final rendered component media file named in its "
            "media list. Act only as the Finished Cut Visual reviewer: judge the final rendered "
            "component preview bytes against the current DP event, its core-minted "
            "visual_placement, and the final_asset_ref bound inside that placement, "
            "never a source self-description. Judge every event independently: approve a "
            "readable, correctly framed, semantically matching component even when another "
            "event fails, and never cascade one failure to the rest of the batch. Transparent "
            "overlay previews may be flattened onto black for inspection; do not fail an "
            "otherwise valid overlay merely because transparent pixels appear black. Apply "
            "format_policy.editorial_brief when present "
            "and the other core/static constraints to those final bytes. Do not inspect any path "
            "outside this workspace. "
            "Return only "
            "the JSON object required by response-schema.json."
        )
    raise ValueError("unsupported Finished Cut stage")


def _response_schema(stage: StageName) -> dict[str, JsonValue]:
    if stage == "director":
        event_properties: dict[str, JsonValue] = {
            "event_id": {"type": "string", "minLength": 1},
            "master_cue_ids": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string", "minLength": 1},
            },
            "intent": {"type": "string", "minLength": 1},
            "display": {"type": "string", "minLength": 1},
            "semantic_kind": {"enum": sorted(_ACTIVE_SEMANTIC_KINDS)},
            "intentional_aroll": {"type": "boolean"},
        }
    elif stage == "dp":
        event_properties = {
            "event_id": {"type": "string", "minLength": 1},
            "implementation_kind": {"type": "string", "minLength": 1},
            "lane": {"enum": [*_ACTIVE_COMPONENT_LANES, None]},
            "asset_ref": {"type": ["string", "null"]},
            "placement_cue_ids": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
        }
    elif stage == "visual_review":
        event_properties = {
            "event_id": {"type": "string", "minLength": 1},
            "status": {"enum": ["approved", "failed"]},
        }
    else:
        raise ValueError("unsupported Finished Cut stage")
    event_schema: dict[str, JsonValue] = {
        "type": "object",
        "additionalProperties": False,
        "required": list(event_properties),
        "properties": event_properties,
    }
    properties: dict[str, JsonValue] = {
        "schema": {"type": "string"},
        "run_id": {"type": "string"},
        "request_id": {"type": "string"},
        "episode_id": {"type": "string"},
        "cut_id": {"type": "string"},
        "format": {"enum": ["long", "short"]},
        "stage": {"type": "string", "const": stage},
        "attempt": {"type": "integer", "minimum": 1},
        "scope": {"enum": ["full_stage", "event_retry"]},
        "event_id": {"type": ["string", "null"]},
        "parent_acceptance_id": {"type": ["string", "null"]},
        "events": {"type": "array", "minItems": 1, "items": event_schema},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def _parse_stage_proposal(request: StageRequest, raw: str) -> StageProposal:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Codex proposal must be an object")
    expected_keys = {
        "schema",
        "run_id",
        "request_id",
        "episode_id",
        "cut_id",
        "format",
        "stage",
        "attempt",
        "scope",
        "event_id",
        "parent_acceptance_id",
        "events",
    }
    if set(value) != expected_keys:
        raise ValueError("Codex proposal fields do not match the stage schema")
    if type(value["attempt"]) is not int:
        raise ValueError("Codex proposal attempt must be an integer")
    envelope = (
        value["schema"],
        value["run_id"],
        value["request_id"],
        value["episode_id"],
        value["cut_id"],
        value["format"],
        value["stage"],
        value["attempt"],
        value["scope"],
        value["event_id"],
        value["parent_acceptance_id"],
    )
    expected_envelope = (
        request.schema,
        request.run_id,
        request.request_id,
        request.episode_id,
        request.cut_id,
        request.format,
        request.stage,
        request.attempt,
        request.scope,
        request.event_id,
        request.parent_acceptance_id,
    )
    if envelope != expected_envelope:
        raise ValueError("Codex proposal does not match the current request")
    event_values = value["events"]
    if not isinstance(event_values, list) or not event_values:
        raise ValueError("Codex proposal requires events")
    events: list[DirectorEventProposal | DPEventProposal | VisualEventProposal] = []
    if request.stage == "dp":
        event_keys = {
            "event_id",
            "implementation_kind",
            "lane",
            "asset_ref",
            "placement_cue_ids",
        }
        for event in event_values:
            if not isinstance(event, dict) or set(event) != event_keys:
                raise ValueError("DP event fields do not match the stage schema")
            event_id = event["event_id"]
            implementation_kind = event["implementation_kind"]
            lane = event["lane"]
            asset_ref = event["asset_ref"]
            placement_cue_ids = event["placement_cue_ids"]
            if not isinstance(event_id, str) or not event_id.strip():
                raise ValueError("DP event identity is invalid")
            if not isinstance(implementation_kind, str) or not implementation_kind.strip():
                raise ValueError("DP implementation kind is invalid")
            allowed_lanes = {*_ACTIVE_COMPONENT_LANES, None}
            if (
                lane not in allowed_lanes
                or not (isinstance(asset_ref, str) or asset_ref is None)
                or not isinstance(placement_cue_ids, list)
                or any(
                    not isinstance(cue_id, str) or not cue_id.strip()
                    for cue_id in placement_cue_ids
                )
            ):
                raise ValueError("DP lane or asset reference is invalid")
            events.append(
                DPEventProposal(
                    event_id=event_id,
                    implementation_kind=implementation_kind,
                    lane=lane,
                    asset_ref=asset_ref,
                    placement_cue_ids=tuple(placement_cue_ids),
                )
            )
        return _proposal_from_events(request, tuple(events))
    if request.stage == "visual_review":
        event_keys = {"event_id", "status"}
        for event in event_values:
            if not isinstance(event, dict) or set(event) != event_keys:
                raise ValueError("Visual event fields do not match the stage schema")
            event_id = event["event_id"]
            status = event["status"]
            if not isinstance(event_id, str) or not event_id.strip():
                raise ValueError("Visual event identity is invalid")
            if status not in {"approved", "failed"}:
                raise ValueError("Visual event status is invalid")
            events.append(VisualEventProposal(event_id=event_id, status=status))
        return _proposal_from_events(request, tuple(events))
    if request.stage != "director":
        raise ValueError("unsupported Finished Cut stage")
    event_keys = {
        "event_id",
        "master_cue_ids",
        "intent",
        "display",
        "semantic_kind",
        "intentional_aroll",
    }
    for event in event_values:
        if not isinstance(event, dict) or set(event) != event_keys:
            raise ValueError("Director event fields do not match the stage schema")
        cue_ids = event["master_cue_ids"]
        if (
            not isinstance(cue_ids, list)
            or not cue_ids
            or not all(isinstance(cue_id, str) and cue_id for cue_id in cue_ids)
        ):
            raise ValueError("Director event cue IDs are invalid")
        strings = tuple(event[key] for key in ("event_id", "intent", "display", "semantic_kind"))
        if not all(isinstance(item, str) and item.strip() for item in strings):
            raise ValueError("Director event strings are invalid")
        if strings[3] not in _ACTIVE_SEMANTIC_KINDS:
            raise ValueError("Director semantic kind is invalid")
        intentional_aroll = event["intentional_aroll"]
        if not isinstance(intentional_aroll, bool):
            raise ValueError("Director event A-roll flag is invalid")
        events.append(
            DirectorEventProposal(
                event_id=strings[0],
                master_cue_ids=tuple(cue_ids),
                intent=strings[1],
                display=strings[2],
                semantic_kind=strings[3],
                intentional_aroll=intentional_aroll,
            )
        )
    return _proposal_from_events(request, tuple(events))


def _proposal_from_events(
    request: StageRequest,
    events: tuple[DirectorEventProposal | DPEventProposal | VisualEventProposal, ...],
) -> StageProposal:
    return StageProposal(
        run_id=request.run_id,
        request_id=request.request_id,
        episode_id=request.episode_id,
        cut_id=request.cut_id,
        format=request.format,
        stage=request.stage,
        attempt=request.attempt,
        scope=request.scope,
        event_id=request.event_id,
        parent_acceptance_id=request.parent_acceptance_id,
        events=events,
        schema=request.schema,
    )
