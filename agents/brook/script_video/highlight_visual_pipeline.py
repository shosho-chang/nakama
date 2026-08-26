"""Hash-bound Director -> DP contract for Podcast long-highlight visuals.

Creative workers write proposals.  Only the accept functions in this module may
publish the canonical episode-local artifacts.  Every read revalidates the
complete upstream DAG; no verifier performs creative or LLM judgement.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from agents.brook.script_video.editorial_master import (
    EditorialMasterContractError,
    EditorialMasterRequest,
    EditorialMasterSelection,
)
from agents.brook.script_video.highlight_broll import (
    BrollContractError,
    probe_stock_video,
)
from agents.brook.script_video.highlight_candidate_renderer import (
    HYPERFRAMES_LICENSE,
    HYPERFRAMES_PROVIDER,
    TrustedRenderError,
    dp_hydration_receipt_identity,
    verify_dp_hydration_receipt,
    verify_hyperframes_render_receipt,
)
from shared.highlight_materialization import (
    HighlightSource,
    materialization_path,
    verify_materialization_receipt,
)

WORK_PACKET_CONTRACT = "podcast-highlight-visual-work-packet-v1"
DIRECTOR_PLAN_CONTRACT = "podcast-highlight-director-plan-v1"
DP_FULFILLMENT_CONTRACT = "podcast-highlight-dp-fulfillment-v1"
SEMANTIC_AUDIT_CONTRACT = "podcast-highlight-visual-semantic-audit-v1"
SEMANTIC_REFINEMENT_CONTRACT = "podcast-highlight-visual-semantic-refinement-v1"
REFINEMENT_DECISION_CONTRACT = "podcast-highlight-visual-refinement-decision-v1"
DP_ATTEMPT_CONTRACT = "podcast-highlight-dp-attempt-v1"
DIRECTOR_REPLAN_CONTRACT = "podcast-highlight-director-replan-v1"
ASSET_AUTHORITY_CONTRACT = "podcast-highlight-visual-asset-authority-v1"
ACQUISITION_RECEIPT_CONTRACT = "podcast-highlight-asset-acquisition-receipt-v1"
EXECUTION_RECEIPT_CONTRACT = "podcast-highlight-visual-worker-execution-v1"
EXECUTION_PREPARE_CONTRACT = "podcast-highlight-visual-worker-prepare-v1"
ASSET_ACQUISITION_PROPOSAL_CONTRACT = "podcast-highlight-asset-acquisition-proposal-v1"
STATUS_CONTRACT = "podcast-highlight-visual-status-v1"
PREFLIGHT_CONTRACT = "podcast-highlight-visual-preflight-v1"
LINEAGE_CONTRACT = "podcast-highlight-visual-lineage-v1"
POINTER_CONTRACT = "podcast-highlight-visual-pointer-v1"
REQUESTED_VISUAL_FEEDBACK_CONTRACT = "podcast-highlight-requested-visual-feedback-v1"
FEEDBACK_IDENTITY_MIGRATION_CONTRACT = "finished-review-feedback-component-identity-migration-v1"

VISUAL_PIPELINE_ROOT = Path("highlights") / "visual-pipeline"
WORK_PACKET_NAME = "DIRECTOR-WORK.json"
DIRECTOR_PLAN_NAME = "DIRECTOR-PLAN.json"
DP_FULFILLMENT_NAME = "DP-FULFILLMENT.json"
SEMANTIC_AUDIT_NAME = "SEMANTIC-AUDIT.json"
CURRENT_POINTER_NAME = "CURRENT.json"
PENDING_POINTER_NAME = "PENDING.json"
REVISION_DIR_NAME = "revisions"
ATTEMPTS_DIR_NAME = "attempts"
SEMANTIC_REFINEMENT_NAME = "SEMANTIC-REFINEMENT.json"
REFINEMENT_DECISION_NAME = "REFINEMENT-DECISION.json"
DP_ATTEMPT_NAME = "DP-ATTEMPT.json"
DIRECTOR_REPLAN_NAME = "DIRECTOR-REPLAN.json"
ASSET_AUTHORITY_NAME = "ASSET-AUTHORITY.json"

_EXECUTION_RECEIPT_KEYS = {
    "contract",
    "episode_id",
    "cut_id",
    "revision_id",
    "phase",
    "role",
    "worker_identity",
    "prepare",
    "prompt_sha256",
    "phase_input",
    "proposal",
    "stdout",
    "stderr",
    "content_hash",
}
_EXECUTION_PREPARE_KEYS = {
    "contract",
    "episode_id",
    "cut_id",
    "revision_id",
    "phase",
    "role",
    "attempt",
    "orchestrator_pid",
    "prompt_sha256",
    "phase_input",
    "proposal_path",
    "content_hash",
}
MAX_DP_ATTEMPTS = 3

_CUT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SRT_TIMING_RE = re.compile(
    r"^(?P<sh>\d{2}):(?P<sm>\d{2}):(?P<ss>\d{2}),(?P<sms>\d{3})\s+-->\s+"
    r"(?P<eh>\d{2}):(?P<em>\d{2}):(?P<es>\d{2}),(?P<ems>\d{3})$"
)
_MASTER_KEYS = {
    "contract",
    "episode_id",
    "content_hash",
    "master_media_sha256",
    "master_srt_sha256",
    "editorial_master_receipt",
}
_WORKER_EXECUTION_KEYS = {"worker_id", "execution_id", "role", "session_id"}
_DIRECTOR_CATEGORIES = {
    "stock_scene",
    "keyword",
    "person_inset",
    "book_cover",
    "quote",
    "chapter",
    "worked_example",
    "evidence_doc",
    "self_archive",
    "self_promo",
    "kol_quote",
    "screen_demo",
    "meme",
    "bigstat",
    "none",
}
_DIRECTOR_FORMS = {"cutaway", "overlay", "canvas_pip", "aside_marker", "aroll"}
_DIRECTOR_DECISIONS = {"add_visual", "intentional_aroll"}
_CONTENT_FEEDBACK_ACTIONS = {
    "b_roll": {"remove", "replace_asset", "change_type", "move"},
    "hero_title": {"remove", "edit_text", "move"},
    "title_card": {"remove", "edit_text", "move"},
    "fullscreen_transition": {"remove", "edit_text", "move"},
    "visual_effect": {"remove", "replace_asset", "change_type", "move"},
}
_NON_MUTATING_FEEDBACK_ACTIONS = {"approve", "comment"}
_FEEDBACK_IDENTITY_REGISTRY_NAME = "finished_review_component_identity.v1.json"
_FEEDBACK_CURRENT_MANIFEST_NAME = "finished_review_manifest_current.json"
_FEEDBACK_DIRECTIVE_KEYS = {
    "component_id",
    "lane",
    "action",
    "comment",
    "replacement",
    "timeline_seconds",
    "move_to_seconds",
    "source_component",
}
_SOURCE_COMPONENT_PROJECTION_KEYS = {
    "component_id",
    "lane",
    "t0",
    "t1",
    "type",
    "slug",
    "asset",
    "source_url",
    "content_hash",
}
_REQUESTED_FEEDBACK_KEYS = {
    "contract",
    "cut_id",
    "source_manifest",
    "directives",
    "creative_context",
    "content_hash",
}
_DIRECTOR_EVENT_KEYS = {
    "event_id",
    "cue_ids",
    "t0",
    "t1",
    "quote",
    "category",
    "form",
    "description",
    "on_screen_text",
    "shots_hint",
    "negative_constraints",
    "search_angles",
    "decision",
    "rationale",
}
_COVERAGE_KEYS = {
    "timeline_start_sec",
    "timeline_end_sec",
    "add_visual_count",
    "planned_visual_count",
    "planned_stock_video_count",
    "intentional_aroll_count",
    "max_uncovered_sec",
    "max_uncovered_start_sec",
    "max_uncovered_end_sec",
    "visual_events_per_minute",
    "cutaway_events_per_minute",
}


class HighlightVisualContractError(ValueError):
    """One visual artifact is missing, stale, unsafe, or malformed."""


class HighlightVisualArtifactConflictError(HighlightVisualContractError):
    """An immutable canonical artifact already contains different bytes."""


@dataclass(frozen=True, slots=True)
class ArtifactSelection:
    path: Path
    document: dict[str, object]
    episode_root: Path

    def identity(self) -> dict[str, object]:
        return {
            "contract": self.document["contract"],
            "path": self.path.relative_to(self.episode_root).as_posix(),
            "bytes": self.path.stat().st_size,
            "sha256": _sha256_file(self.path),
            "content_hash": self.document["content_hash"],
        }


@dataclass(frozen=True, slots=True)
class VisualPipelineSelection:
    work_packet: ArtifactSelection
    director_plan: ArtifactSelection
    dp_fulfillment: ArtifactSelection
    semantic_audit: ArtifactSelection
    materializations: tuple[dict[str, object], ...]

    def lineage(self) -> dict[str, object]:
        work = self.work_packet.document
        core: dict[str, object] = {
            "contract": LINEAGE_CONTRACT,
            "episode_id": work["episode_id"],
            "cut_id": work["cut_id"],
            "revision_id": work["revision_id"],
            "format": work["format"],
            "source_range": work["source_range"],
            "editorial_master": work["editorial_master"],
            "current_pointer": _pointer_identity(
                self.work_packet.episode_root,
                str(work["cut_id"]),
                CURRENT_POINTER_NAME,
            ),
            "work_packet": self.work_packet.identity(),
            "director_plan": self.director_plan.identity(),
            "dp_fulfillment": self.dp_fulfillment.identity(),
            "semantic_audit": self.semantic_audit.identity(),
            "materializations": list(self.materializations),
        }
        core["content_hash"] = _content_hash(core)
        return core


def _canonical_json(value: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise HighlightVisualContractError("artifact must contain strict JSON values") from error


def _pretty_json(value: Mapping[str, object]) -> bytes:
    try:
        return (
            json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise HighlightVisualContractError("artifact must contain strict JSON values") from error


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _content_hash(value: Mapping[str, object]) -> str:
    return _sha256_bytes(_canonical_json(value))


def _require_exact_keys(value: object, expected: set[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise HighlightVisualContractError(f"{label} must be an object")
    if set(value) != expected:
        raise HighlightVisualContractError(
            f"{label} fields mismatch: expected {sorted(expected)}, got {sorted(value)}"
        )
    return value


def _safe_cut_id(cut_id: str) -> str:
    if not isinstance(cut_id, str) or not _CUT_ID_RE.fullmatch(cut_id):
        raise HighlightVisualContractError(f"unsafe cut_id: {cut_id!r}")
    return cut_id


def _root(episode_root: str | Path) -> Path:
    root = Path(episode_root).resolve()
    if not root.is_dir():
        raise HighlightVisualContractError(f"episode root does not exist: {root}")
    return root


def _cut_root(root: Path, cut_id: str) -> Path:
    path = (root / VISUAL_PIPELINE_ROOT / _safe_cut_id(cut_id)).resolve()
    if not path.is_relative_to(root):
        raise HighlightVisualContractError("visual pipeline path escapes episode root")
    return path


def _safe_revision_id(revision_id: str) -> str:
    if not isinstance(revision_id, str) or not re.fullmatch(r"r-[0-9a-f]{24}", revision_id):
        raise HighlightVisualContractError(f"unsafe visual revision_id: {revision_id!r}")
    return revision_id


def _revision_dir(root: Path, cut_id: str, revision_id: str) -> Path:
    path = (_cut_root(root, cut_id) / REVISION_DIR_NAME / _safe_revision_id(revision_id)).resolve()
    if not path.is_relative_to(root):
        raise HighlightVisualContractError("visual revision path escapes episode root")
    return path


def _attempt_dir(root: Path, cut_id: str, revision_id: str, attempt: int) -> Path:
    if (
        isinstance(attempt, bool)
        or not isinstance(attempt, int)
        or not 1 <= attempt <= MAX_DP_ATTEMPTS
    ):
        raise HighlightVisualContractError(f"invalid DP attempt: {attempt!r}")
    path = (
        _revision_dir(root, cut_id, revision_id)
        / ATTEMPTS_DIR_NAME
        / f"attempt-{attempt:03d}"
    ).resolve()
    if not path.is_relative_to(root):
        raise HighlightVisualContractError("visual attempt path escapes episode root")
    return path


def _dp_path(root: Path, cut_id: str, revision_id: str, attempt: int) -> Path:
    if attempt == 1:
        return _revision_dir(root, cut_id, revision_id) / DP_FULFILLMENT_NAME
    return _attempt_dir(root, cut_id, revision_id, attempt) / DP_FULFILLMENT_NAME


def _director_path(root: Path, cut_id: str, revision_id: str, attempt: int) -> Path:
    if attempt == 1:
        return _revision_dir(root, cut_id, revision_id) / DIRECTOR_PLAN_NAME
    return _attempt_dir(root, cut_id, revision_id, attempt) / DIRECTOR_PLAN_NAME


def _file_identity(root: Path, path: Path, **extra: object) -> dict[str, object]:
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise HighlightVisualContractError("artifact path escapes episode root")
    result: dict[str, object] = {
        "path": resolved.relative_to(root).as_posix(),
        "bytes": resolved.stat().st_size,
        "sha256": _sha256_file(resolved),
    }
    result.update(extra)
    return result


def _validate_master(value: object, expected: Mapping[str, object]) -> None:
    identity = _require_exact_keys(value, _MASTER_KEYS, "editorial_master")
    if identity != dict(expected):
        raise HighlightVisualContractError("Editorial Master identity is stale or mismatched")


def _open_master(
    root: Path,
    editorial_master: EditorialMasterSelection | object | None,
) -> EditorialMasterSelection | object:
    if editorial_master is not None:
        selected = editorial_master
    else:
        try:
            selected = EditorialMasterRequest(
                episode_root=root,
                expected_episode_id=root.name,
            ).open()
        except EditorialMasterContractError as error:
            raise HighlightVisualContractError(f"Editorial Master is not valid: {error}") from error
    identity_fn = getattr(selected, "identity", None)
    if not callable(identity_fn):
        raise HighlightVisualContractError("editorial_master must expose identity()")
    identity = identity_fn()
    _validate_master(identity, identity)
    if identity["episode_id"] != root.name:
        raise HighlightVisualContractError("Editorial Master belongs to another episode")
    return selected


def _load_json_object(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HighlightVisualContractError(f"{label} is missing or invalid JSON") from error
    if not isinstance(value, dict):
        raise HighlightVisualContractError(f"{label} must be an object")
    return value


def _latest_tight_srt(root: Path, cut_id: str) -> Path:
    directory = (root / "highlights" / "srt").resolve()
    revisions: list[tuple[int, Path]] = []
    if directory.is_dir():
        for path in directory.iterdir():
            match = re.fullmatch(rf"{re.escape(cut_id)}_tight_r(\d+)\.srt", path.name)
            if match and path.is_file():
                revisions.append((int(match.group(1)), path.resolve()))
    if not revisions:
        raise HighlightVisualContractError(f"no canonical tight SRT exists for winner {cut_id}")
    return max(revisions, key=lambda item: (item[0], item[1].name))[1]


def _cue_identities(srt_path: Path) -> list[dict[str, object]]:
    try:
        text = srt_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as error:
        raise HighlightVisualContractError("cut SRT cannot be read") from error
    normalized = text.replace("\r\n", "\n").strip()
    blocks = re.split(r"\n{2,}", normalized) if normalized else []
    if not blocks:
        raise HighlightVisualContractError("cut SRT has no cues")
    result: list[dict[str, object]] = []
    previous_number = 0
    previous_start = -1.0
    for block_number, block in enumerate(blocks, 1):
        lines = block.splitlines()
        if len(lines) < 3:
            raise HighlightVisualContractError(f"malformed SRT block {block_number}")
        try:
            number = int(lines[0].strip())
        except ValueError as error:
            raise HighlightVisualContractError(
                f"invalid cue number in SRT block {block_number}"
            ) from error
        timing = _SRT_TIMING_RE.fullmatch(lines[1].strip())
        if timing is None:
            raise HighlightVisualContractError(f"invalid timing in SRT cue {number}")

        def seconds(prefix: str) -> float:
            return (
                int(timing[f"{prefix}h"]) * 3600
                + int(timing[f"{prefix}m"]) * 60
                + int(timing[f"{prefix}s"])
                + int(timing[f"{prefix}ms"]) / 1000
            )

        start_sec = seconds("s")
        end_sec = seconds("e")
        cue_text = "\n".join(lines[2:]).strip()
        if (
            number <= previous_number
            or start_sec < previous_start
            or end_sec <= start_sec
            or not cue_text
        ):
            raise HighlightVisualContractError("cut SRT cues are not canonical and increasing")
        result.append(
            {
                "number": number,
                "start_sec": start_sec,
                "end_sec": end_sec,
                "text": cue_text,
                "text_sha256": _sha256_bytes(cue_text.encode("utf-8")),
            }
        )
        previous_number = number
        previous_start = start_sec
    return result


_REVISION_REQUEST_KEYS = {"kind", "path", "bytes", "sha256"}


def _revision_request_identity(
    root: Path,
    value: str | Path | None,
) -> dict[str, object]:
    if value is None:
        return {
            "kind": "base",
            "path": None,
            "bytes": 0,
            "sha256": _sha256_bytes(b"podcast-highlight-visual-base"),
        }
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise HighlightVisualContractError(
            "revision request must be an episode-local existing file"
        )
    return {"kind": "feedback", **_file_identity(root, resolved)}


def _validate_revision_request_identity(root: Path, value: object) -> dict[str, object]:
    identity = _require_exact_keys(value, _REVISION_REQUEST_KEYS, "revision_request")
    if identity["kind"] == "base":
        expected = _revision_request_identity(root, None)
        if identity != expected:
            raise HighlightVisualContractError("base revision request identity drift")
        return expected
    if identity["kind"] != "feedback":
        raise HighlightVisualContractError("revision_request.kind is invalid")
    file_value = {key: identity[key] for key in ("path", "bytes", "sha256")}
    _, verified = _validate_file_identity(root, file_value, "revision_request")
    return {"kind": "feedback", **verified}


def _feedback_span(value: object, label: str) -> dict[str, float]:
    span = _require_exact_keys(value, {"t0", "t1"}, label)
    t0 = _number(span["t0"], f"{label}.t0")
    t1 = _number(span["t1"], f"{label}.t1")
    if t0 < 0 or t1 <= t0:
        raise HighlightVisualContractError(f"{label} is invalid")
    return {"t0": t0, "t1": t1}


def _source_feedback_components(
    root: Path,
    *,
    request: Mapping[str, object],
    cut_id: str,
) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    filename = request.get("manifest_filename")
    claimed_sha256 = request.get("source_manifest_sha256")
    if (
        not isinstance(filename, str)
        or not filename
        or Path(filename).name != filename
        or not isinstance(claimed_sha256, str)
        or not _SHA256_RE.fullmatch(claimed_sha256)
    ):
        raise HighlightVisualContractError(
            "visual component feedback requires a safe source manifest identity"
        )
    path = (root / "highlights" / "review" / filename).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HighlightVisualContractError("visual feedback source manifest is missing or unsafe")
    if _sha256_file(path) != claimed_sha256:
        raise HighlightVisualContractError("visual feedback source manifest hash drift")
    manifest = _load_json_object(path, "visual feedback source manifest")
    if manifest.get("episode_id") != root.name or not isinstance(manifest.get("cuts"), list):
        raise HighlightVisualContractError(
            "visual feedback source manifest belongs to another episode"
        )
    cuts = [
        row for row in manifest["cuts"] if isinstance(row, dict) and row.get("cut_id") == cut_id
    ]
    if len(cuts) != 1 or not isinstance(cuts[0].get("components"), list):
        raise HighlightVisualContractError(
            "visual feedback source manifest does not contain the requested cut"
        )
    components: dict[str, dict[str, object]] = {}
    for raw in cuts[0]["components"]:
        if not isinstance(raw, dict):
            raise HighlightVisualContractError("source manifest component is invalid")
        component_id = raw.get("component_id")
        if not isinstance(component_id, str) or not _CUT_ID_RE.fullmatch(component_id):
            raise HighlightVisualContractError("source manifest component_id is unsafe")
        if component_id in components:
            raise HighlightVisualContractError("source manifest component_id is duplicated")
        components[component_id] = raw
    return _file_identity(root, path), components


def _feedback_component_snapshot(component: Mapping[str, object]) -> dict[str, object]:
    variables = component.get("vars")
    title = variables.get("title") if isinstance(variables, dict) else None
    text = component.get("text") or title or component.get("slug") or ""
    return {
        "lane": component.get("lane"),
        "t0": round(_number(component.get("t0"), "feedback component.t0"), 3),
        "t1": round(_number(component.get("t1"), "feedback component.t1"), 3),
        "text": str(text).replace("/", "\n"),
    }


def _feedback_source_component_projection(
    root: Path,
    component: Mapping[str, object],
    *,
    label: str,
) -> dict[str, object]:
    component_id = _safe_cut_id(
        _nonempty_text(component.get("component_id"), f"{label}.component_id")
    )
    lane = _nonempty_text(component.get("lane"), f"{label}.lane")
    component_type = component.get("type")
    if component_type is not None and not isinstance(component_type, str):
        raise HighlightVisualContractError(f"{label}.type must be text or null")
    slug = component.get("slug")
    if slug is not None and not isinstance(slug, str):
        raise HighlightVisualContractError(f"{label}.slug must be text or null")
    asset: dict[str, object] | None = None
    if component.get("asset") is not None:
        _, asset = _validate_feedback_source_asset_identity(
            root,
            component.get("asset"),
            f"{label}.asset",
            media=(lane == "b_roll" and component_type == "video"),
        )
    provenance = component.get("provenance")
    visual_materialization = component.get("visual_materialization")
    if isinstance(visual_materialization, dict):
        provenance = visual_materialization.get("provenance", provenance)
    source_url: str | None = None
    if isinstance(provenance, dict):
        raw_source_url = provenance.get("source_url")
        if raw_source_url is not None and not isinstance(raw_source_url, str):
            raise HighlightVisualContractError(f"{label}.source_url must be text or null")
        source_url = raw_source_url
    projection: dict[str, object] = {
        "component_id": component_id,
        "lane": lane,
        "t0": _number(component.get("t0"), f"{label}.t0"),
        "t1": _number(component.get("t1"), f"{label}.t1"),
        "type": component_type,
        "slug": slug,
        "asset": asset,
        "source_url": source_url,
    }
    projection["content_hash"] = _content_hash(projection)
    return projection


def _validate_feedback_source_asset_identity(
    root: Path,
    value: object,
    label: str,
    *,
    media: bool,
) -> tuple[Path, dict[str, object]]:
    """Normalize hash-bound legacy review paths without relaxing canonical inputs."""

    identity = _require_exact_keys(value, _MEDIA_KEYS, label)
    raw_path = identity["path"]
    if not isinstance(raw_path, str):
        raise HighlightVisualContractError(f"{label}.path must be text")
    source_path = Path(raw_path)
    if source_path.is_absolute():
        resolved = source_path.resolve()
        if not resolved.is_relative_to(root):
            raise HighlightVisualContractError(f"{label} path escapes episode root")
        identity = {**identity, "path": resolved.relative_to(root).as_posix()}
    return _validate_file_identity(root, identity, label, media=media)


def _feedback_event_lane(event_type: object) -> str | None:
    normalized = str(event_type or "").strip().lower().replace("_", "-")
    if normalized in {"video", "photo"}:
        return "b_roll"
    if normalized.startswith("card-tier") or normalized in {"hero-title", "title"}:
        return "hero_title"
    if normalized in {"fullscreen-transition", "transition"}:
        return "fullscreen_transition"
    if normalized in {"icon-motion", "sticker", "concept", "visual-effect"}:
        return "visual_effect"
    return None


def _apply_feedback_registry_lane(
    *,
    cut_id: str,
    lane: str,
    source_rows: Sequence[Mapping[str, object]],
    current_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    assigned_current: set[int] = set()
    assigned_source: set[int] = set()
    for source_index, source in enumerate(source_rows):
        matches = [
            current_index
            for current_index, component in enumerate(current_rows)
            if current_index not in assigned_current
            and _feedback_component_snapshot(component) == source.get("snapshot")
        ]
        if len(matches) == 1:
            current_rows[matches[0]]["component_id"] = source["component_id"]
            assigned_current.add(matches[0])
            assigned_source.add(source_index)
    for key in ("time", "text"):
        for source_index, source in enumerate(source_rows):
            if source_index in assigned_source:
                continue
            source_snapshot = source.get("snapshot")
            if not isinstance(source_snapshot, dict):
                raise HighlightVisualContractError(
                    "feedback component identity registry snapshot is invalid"
                )
            matches: list[int] = []
            for current_index, component in enumerate(current_rows):
                if current_index in assigned_current:
                    continue
                snapshot = _feedback_component_snapshot(component)
                if key == "time":
                    matched = (snapshot["t0"], snapshot["t1"]) == (
                        source_snapshot.get("t0"),
                        source_snapshot.get("t1"),
                    )
                else:
                    matched = bool(snapshot["text"]) and snapshot["text"] == source_snapshot.get(
                        "text"
                    )
                if matched:
                    matches.append(current_index)
            if len(matches) == 1:
                current_rows[matches[0]]["component_id"] = source["component_id"]
                assigned_current.add(matches[0])
                assigned_source.add(source_index)
    source_remaining = [index for index in range(len(source_rows)) if index not in assigned_source]
    if source_remaining:
        raise HighlightVisualContractError(
            "legacy feedback identity cannot be proven without a pre-existing exact "
            "component mapping; regenerate the review request from the trusted current manifest"
        )
    for index, component in enumerate(current_rows, 1):
        component.setdefault("component_id", f"{cut_id}-{lane.replace('_', '-')}-{index:03d}")
    return current_rows


def _feedback_evidence_paths(root: Path, *, cut_id: str, request_sha256: str) -> tuple[Path, Path]:
    if not _SHA256_RE.fullmatch(request_sha256):
        raise HighlightVisualContractError("feedback evidence request hash is invalid")
    directory = (_cut_root(root, cut_id) / "feedback-evidence" / request_sha256).resolve()
    if not directory.is_relative_to(root):
        raise HighlightVisualContractError("feedback evidence path escapes episode root")
    return directory / "target-manifest.json", directory / "events.json"


def _project_feedback_evidence_identity(
    root: Path,
    *,
    selected_path: Path,
    origin_path: Path,
    evidence_path: Path,
) -> dict[str, object]:
    identity = _file_identity(root, selected_path)
    return {
        "path": evidence_path.relative_to(root).as_posix(),
        "bytes": identity["bytes"],
        "sha256": identity["sha256"],
        "origin_path": origin_path.relative_to(root).as_posix(),
    }


def _manifest_events_identity(
    root: Path,
    *,
    cut_id: str,
    artifact: object,
    evidence_path: Path,
) -> tuple[Path, dict[str, object]]:
    value = _require_exact_keys(artifact, {"path", "bytes", "sha256"}, "events artifact")
    raw_path = value["path"]
    if not isinstance(raw_path, str) or not raw_path:
        raise HighlightVisualContractError("events artifact path is invalid")
    path = Path(raw_path)
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    allowed_root = (root / "highlights" / "review" / cut_id).resolve()
    if not resolved.is_relative_to(allowed_root) or (
        not evidence_path.is_file() and not resolved.is_file()
    ):
        raise HighlightVisualContractError("events artifact is missing or outside the review cut")
    selected = evidence_path if evidence_path.is_file() else resolved
    expected = _file_identity(root, selected)
    if value["bytes"] != expected["bytes"] or value["sha256"] != expected["sha256"]:
        raise HighlightVisualContractError("events artifact byte/hash identity drift")
    return selected, {
        "path": evidence_path.relative_to(root).as_posix(),
        "bytes": expected["bytes"],
        "sha256": expected["sha256"],
        "origin_path": resolved.relative_to(root).as_posix(),
    }


def _feedback_identity_migration(
    root: Path,
    *,
    cut_id: str,
    source_manifest: Mapping[str, object],
    source_components: Mapping[str, Mapping[str, object]],
    directives: Sequence[Mapping[str, object]],
    editorial_master: Mapping[str, object],
    request_sha256: str,
) -> dict[str, object]:
    review_dir = root / "highlights" / "review"
    registry_path = review_dir / _FEEDBACK_IDENTITY_REGISTRY_NAME
    current_origin_path = review_dir / _FEEDBACK_CURRENT_MANIFEST_NAME
    target_evidence_path, events_evidence_path = _feedback_evidence_paths(
        root, cut_id=cut_id, request_sha256=request_sha256
    )
    if target_evidence_path.is_file() != events_evidence_path.is_file():
        raise HighlightVisualContractError(
            "feedback identity migration evidence is incomplete; resubmit the immutable review"
        )
    current_path = target_evidence_path if target_evidence_path.is_file() else current_origin_path
    if not registry_path.is_file() or not current_path.is_file():
        raise HighlightVisualContractError(
            "legacy feedback identity cannot be proven; regenerate the trusted current "
            "finished-review manifest once and resubmit the review"
        )
    registry = _require_exact_keys(
        _load_json_object(registry_path, "feedback component identity registry"),
        {"contract", "episode_id", "source_manifest", "cuts", "content_hash"},
        "feedback component identity registry",
    )
    registry_core = {key: value for key, value in registry.items() if key != "content_hash"}
    if (
        registry["contract"] != "finished-review-component-identity-v1"
        or registry["episode_id"] != root.name
        or registry["content_hash"] != _content_hash(registry_core)
    ):
        raise HighlightVisualContractError(
            "feedback component identity registry content hash or episode drift"
        )
    registry_source = _require_exact_keys(
        registry["source_manifest"],
        {"filename", "sha256"},
        "feedback component identity registry source",
    )
    if (
        registry_source["filename"] != Path(str(source_manifest["path"])).name
        or registry_source["sha256"] != source_manifest["sha256"]
    ):
        raise HighlightVisualContractError(
            "feedback component identity registry does not bind the request source manifest"
        )
    registry_cuts = registry["cuts"]
    if not isinstance(registry_cuts, dict) or not isinstance(registry_cuts.get(cut_id), list):
        raise HighlightVisualContractError(
            "feedback component identity registry omits the requested cut"
        )
    source_rows: list[dict[str, object]] = []
    seen_registry_ids: set[str] = set()
    for index, raw in enumerate(registry_cuts[cut_id], 1):
        row = _require_exact_keys(
            raw,
            {"component_id", "lane", "snapshot"},
            f"feedback identity registry row[{index}]",
        )
        component_id = row["component_id"]
        if not isinstance(component_id, str) or component_id in seen_registry_ids:
            raise HighlightVisualContractError(
                "feedback component identity registry has unsafe or duplicate component_id"
            )
        seen_registry_ids.add(component_id)
        source_component = source_components.get(component_id)
        if (
            source_component is None
            or row["lane"] != source_component.get("lane")
            or row["snapshot"] != _feedback_component_snapshot(source_component)
        ):
            raise HighlightVisualContractError(
                f"feedback identity registry source snapshot drift: {component_id}"
            )
        source_rows.append(row)
    if seen_registry_ids != set(source_components):
        raise HighlightVisualContractError(
            "feedback component identity registry/source component coverage drift"
        )

    current_manifest = _load_json_object(current_path, "current finished-review manifest")
    if (
        current_manifest.get("episode_id") != root.name
        or current_manifest.get("schema")
        not in {
            "nakama.finished_cut_review_manifest.v1",
            "nakama.finished_cut_review_manifest.v2",
        }
        or current_manifest.get("editorial_master_lineage") != editorial_master
        or not isinstance(current_manifest.get("cuts"), list)
    ):
        raise HighlightVisualContractError(
            "current finished-review manifest identity/schema/Master lineage is invalid"
        )
    current_cuts = [
        row
        for row in current_manifest["cuts"]
        if isinstance(row, dict) and row.get("cut_id") == cut_id
    ]
    if len(current_cuts) != 1 or not isinstance(current_cuts[0].get("components"), list):
        raise HighlightVisualContractError(
            "current finished-review manifest omits the requested cut/components"
        )
    artifacts = current_cuts[0].get("artifacts")
    if not isinstance(artifacts, dict):
        raise HighlightVisualContractError("current finished-review artifacts are invalid")
    events_path, events_identity = _manifest_events_identity(
        root,
        cut_id=cut_id,
        artifact=artifacts.get("events"),
        evidence_path=events_evidence_path,
    )
    events_document = _load_json_object(events_path, "current finished-review events")
    if events_document.get("editorial_master_lineage") != editorial_master or not isinstance(
        events_document.get("events"), list
    ):
        raise HighlightVisualContractError(
            "current finished-review events have stale Master lineage or invalid events"
        )

    requested_lanes = {str(row["lane"]) for row in directives}
    projected_by_id: dict[str, dict[str, object]] = {}
    target_by_id: dict[str, Mapping[str, object]] = {}
    for lane in sorted(requested_lanes):
        registry_lane = [row for row in source_rows if row["lane"] == lane]
        current_lane: list[dict[str, object]] = []
        for event in events_document["events"]:
            if not isinstance(event, dict) or _feedback_event_lane(event.get("type")) != lane:
                continue
            current_lane.append({**event, "lane": lane})
        projected_lane = _apply_feedback_registry_lane(
            cut_id=cut_id,
            lane=lane,
            source_rows=registry_lane,
            current_rows=current_lane,
        )
        target_lane = [
            row
            for row in current_cuts[0]["components"]
            if isinstance(row, dict) and row.get("lane") == lane
        ]
        projected_identity = [
            (row["component_id"], _feedback_component_snapshot(row)) for row in projected_lane
        ]
        target_identity = [
            (row.get("component_id"), _feedback_component_snapshot(row)) for row in target_lane
        ]
        if projected_identity != target_identity:
            raise HighlightVisualContractError(
                f"current finished-review component identity projection drift for lane={lane}"
            )
        projected_by_id.update({str(row["component_id"]): row for row in projected_lane})
        target_by_id.update({str(row["component_id"]): row for row in target_lane})

    migrated_components: list[dict[str, object]] = []
    for directive in sorted(directives, key=lambda row: str(row["component_id"])):
        component_id = str(directive["component_id"])
        source_component = source_components[component_id]
        target_component = target_by_id.get(component_id)
        if target_component is None or component_id not in projected_by_id:
            raise HighlightVisualContractError(
                f"migrated feedback component identity is missing: {component_id}"
            )
        target_span = {
            "t0": _number(target_component.get("t0"), f"{component_id}.target.t0"),
            "t1": _number(target_component.get("t1"), f"{component_id}.target.t1"),
        }
        if directive["timeline_seconds"] != target_span:
            raise HighlightVisualContractError(
                f"visual feedback span matches neither source nor migrated current component: "
                f"{component_id}"
            )
        source_span = {
            "t0": _number(source_component.get("t0"), f"{component_id}.source.t0"),
            "t1": _number(source_component.get("t1"), f"{component_id}.source.t1"),
        }
        migrated_components.append(
            {
                "component_id": component_id,
                "lane": directive["lane"],
                "source_span": source_span,
                "source_snapshot_sha256": _content_hash(
                    _feedback_component_snapshot(source_component)
                ),
                "target_span": target_span,
                "target_snapshot_sha256": _content_hash(
                    _feedback_component_snapshot(target_component)
                ),
            }
        )
    return {
        "contract": FEEDBACK_IDENTITY_MIGRATION_CONTRACT,
        "source_manifest": dict(source_manifest),
        "identity_registry": {
            **_file_identity(root, registry_path),
            "content_hash": registry["content_hash"],
        },
        "target_manifest": _project_feedback_evidence_identity(
            root,
            selected_path=current_path,
            origin_path=current_origin_path,
            evidence_path=target_evidence_path,
        ),
        "events_file": events_identity,
        "components": migrated_components,
    }


def _requested_visual_feedback(
    root: Path,
    *,
    revision_request: Mapping[str, object],
    cut_id: str,
    editorial_master: Mapping[str, object],
) -> dict[str, object]:
    core: dict[str, object] = {
        "contract": REQUESTED_VISUAL_FEEDBACK_CONTRACT,
        "cut_id": cut_id,
        "source_manifest": None,
        "directives": [],
        "creative_context": {
            "policy": "informational_not_acceptance",
            "overall_feedback": "",
        },
    }
    if revision_request["kind"] == "base":
        core["content_hash"] = _content_hash(core)
        return core
    request_path = root / str(revision_request["path"])
    request = _load_json_object(request_path, "revision request")
    if request.get("episode_id") not in {None, root.name}:
        raise HighlightVisualContractError("revision request belongs to another episode")
    overall = request.get("overall_feedback", {})
    if overall is None:
        overall = {}
    if isinstance(overall, str):
        cut_context = overall
    elif isinstance(overall, dict):
        cut_context = overall.get(cut_id, "")
    else:
        raise HighlightVisualContractError(
            "revision request overall_feedback must be an object or string"
        )
    if not isinstance(cut_context, str) or len(cut_context) > 20_000:
        raise HighlightVisualContractError("cut-scoped overall_feedback is invalid or too large")
    core["creative_context"] = {
        "policy": "informational_not_acceptance",
        "overall_feedback": cut_context,
    }
    raw_rows = request.get("component_feedback", [])
    if not isinstance(raw_rows, list):
        raise HighlightVisualContractError("revision request component_feedback must be an array")
    selected_rows: list[dict[str, object]] = []
    for index, raw in enumerate(raw_rows, 1):
        if not isinstance(raw, dict):
            raise HighlightVisualContractError(f"component_feedback[{index}] must be an object")
        if raw.get("cut_id") != cut_id:
            continue
        lane = raw.get("lane")
        action = raw.get("action")
        if lane not in _CONTENT_FEEDBACK_ACTIONS:
            continue
        if action in _NON_MUTATING_FEEDBACK_ACTIONS:
            continue
        if action not in _CONTENT_FEEDBACK_ACTIONS[str(lane)]:
            raise HighlightVisualContractError(
                f"component_feedback[{index}] action is not allowed for lane={lane}"
            )
        component_id = raw.get("component_id")
        if not isinstance(component_id, str) or not _CUT_ID_RE.fullmatch(component_id):
            raise HighlightVisualContractError(
                f"component_feedback[{index}].component_id is unsafe"
            )
        replacement = raw.get("replacement", "")
        comment = raw.get("comment", "")
        if not isinstance(replacement, str) or not isinstance(comment, str):
            raise HighlightVisualContractError(
                f"component_feedback[{index}] text fields are invalid"
            )
        if action in {"edit_text", "replace_asset", "change_type"} and not replacement:
            raise HighlightVisualContractError(
                f"component_feedback[{index}] replacement is required"
            )
        if action == "change_type" and replacement not in _CHANGE_TYPE_KIND:
            raise HighlightVisualContractError(
                f"component_feedback[{index}] change_type is not a closed visual kind"
            )
        if (
            action == "change_type"
            and _CHANGE_TYPE_KIND[replacement] not in _KINDS_BY_FEEDBACK_LANE[str(lane)]
        ):
            raise HighlightVisualContractError(
                f"component_feedback[{index}] change_type is not compatible with lane={lane}"
            )
        timeline_seconds = _feedback_span(
            raw.get("timeline_seconds"),
            f"component_feedback[{index}].timeline_seconds",
        )
        move_to_seconds: float | None = None
        if action == "move":
            move_to_seconds = _number(
                raw.get("move_to_seconds"),
                f"component_feedback[{index}].move_to_seconds",
            )
            if move_to_seconds < 0:
                raise HighlightVisualContractError(
                    f"component_feedback[{index}].move_to_seconds is invalid"
                )
        selected_rows.append(
            {
                "component_id": component_id,
                "lane": lane,
                "action": action,
                "comment": comment,
                "replacement": replacement,
                "timeline_seconds": timeline_seconds,
                "move_to_seconds": move_to_seconds,
            }
        )
    if not selected_rows:
        core["content_hash"] = _content_hash(core)
        return core
    requested_cut_ids = request.get("requested_cut_ids")
    if not isinstance(requested_cut_ids, list) or cut_id not in requested_cut_ids:
        raise HighlightVisualContractError(
            "revision request requested_cut_ids omits visual feedback cut"
        )
    source_manifest, components = _source_feedback_components(root, request=request, cut_id=cut_id)
    seen_components: set[str] = set()
    source_span_mismatch = False
    for directive in selected_rows:
        component_id = str(directive["component_id"])
        if component_id in seen_components:
            raise HighlightVisualContractError(
                "revision request contains duplicate visual component edits"
            )
        seen_components.add(component_id)
        component = components.get(component_id)
        if component is None or component.get("lane") != directive["lane"]:
            raise HighlightVisualContractError(
                f"visual feedback component identity/lane mismatch: {component_id}"
            )
        manifest_span = {
            "t0": _number(component.get("t0"), f"{component_id}.t0"),
            "t1": _number(component.get("t1"), f"{component_id}.t1"),
        }
        if directive["timeline_seconds"] != manifest_span:
            source_span_mismatch = True
        source_component = _feedback_source_component_projection(
            root,
            component,
            label=f"source component {component_id}",
        )
        if (
            directive["action"] == "remove"
            and directive["lane"] == "b_roll"
            and source_component["asset"] is None
        ):
            raise HighlightVisualContractError(
                f"requires_operator_rebind: removed Stock {component_id} has no "
                "request-bound media identity"
            )
        directive["source_component"] = source_component
    if source_span_mismatch:
        raise HighlightVisualContractError(
            "legacy feedback identity cannot be proven from the immutable source manifest; "
            "regenerate the review request by saving the draft against the trusted current "
            "manifest"
        )
    core["source_manifest"] = source_manifest
    core["directives"] = sorted(selected_rows, key=lambda row: str(row["component_id"]))
    core["content_hash"] = _content_hash(core)
    return core


def _current_work_core(
    root: Path,
    *,
    cut_id: str,
    revision_id: str,
    revision_request: Mapping[str, object],
    parent_current: Mapping[str, object] | None,
    editorial_master: EditorialMasterSelection | object | None,
) -> dict[str, object]:
    master = _open_master(root, editorial_master)
    master_identity = dict(master.identity())  # type: ignore[attr-defined]
    highlights = root / "highlights"
    candidates_path = highlights / "candidates.json"
    winners_path = highlights / "winners.json"
    candidates_doc = _load_json_object(candidates_path, "candidates.json")
    winners_doc = _load_json_object(winners_path, "winners.json")
    for label, document in (
        ("candidates.json", candidates_doc),
        ("winners.json", winners_doc),
    ):
        if document.get("editorial_master_lineage") != master_identity:
            raise HighlightVisualContractError(f"{label} Editorial Master lineage is stale")
    candidates = candidates_doc.get("candidates")
    winners = winners_doc.get("winners")
    if not isinstance(candidates, list) or not isinstance(winners, list):
        raise HighlightVisualContractError("candidates/winners arrays are required")
    selected_candidates = [
        item for item in candidates if isinstance(item, dict) and item.get("id") == cut_id
    ]
    selected_winners = [
        item for item in winners if isinstance(item, dict) and item.get("id") == cut_id
    ]
    if len(selected_candidates) != 1 or len(selected_winners) != 1:
        raise HighlightVisualContractError("cut_id must identify one exact winner candidate")
    candidate = selected_candidates[0]
    cut_format = candidate.get("format")
    start = candidate.get("t_start")
    end = candidate.get("t_end")
    if (
        cut_format not in {"long", "short"}
        or isinstance(start, bool)
        or not isinstance(start, (int, float))
        or isinstance(end, bool)
        or not isinstance(end, (int, float))
        or float(end) <= float(start)
    ):
        raise HighlightVisualContractError("winner candidate format/source range is invalid")
    srt_path = _latest_tight_srt(root, cut_id)
    cues = _cue_identities(srt_path)
    media_path = getattr(master, "media_path", None)
    master_srt_path = getattr(master, "srt_path", None)
    if not isinstance(media_path, Path) or not isinstance(master_srt_path, Path):
        raise HighlightVisualContractError(
            "Editorial Master selection must expose media_path and srt_path"
        )
    try:
        materialization = verify_materialization_receipt(
            root,
            cut_id,
            source=HighlightSource(
                srt_path=master_srt_path,
                media_path=media_path,
                lineage=master_identity,
            ),
            expected_format=str(cut_format),
        )
    except EditorialMasterContractError as error:
        raise HighlightVisualContractError(
            f"winner materialization is not valid: {error}"
        ) from error
    materialized_range = materialization.get("source_range")
    if not isinstance(materialized_range, dict) or (
        materialized_range.get("start_sec") != start or materialized_range.get("end_sec") != end
    ):
        raise HighlightVisualContractError(
            "winner materialization source range differs from candidate"
        )
    materialization_file = materialization_path(root, cut_id)
    core: dict[str, object] = {
        "contract": WORK_PACKET_CONTRACT,
        "episode_id": root.name,
        "cut_id": cut_id,
        "revision_id": _safe_revision_id(revision_id),
        "revision_request": dict(revision_request),
        "requested_visual_feedback": _requested_visual_feedback(
            root,
            revision_request=revision_request,
            cut_id=cut_id,
            editorial_master=master_identity,
        ),
        "parent_current": dict(parent_current) if parent_current is not None else None,
        "format": cut_format,
        "source_range": {"start_sec": float(start), "end_sec": float(end)},
        "editorial_master": master_identity,
        "candidates_file": _file_identity(root, candidates_path),
        "winners_file": _file_identity(root, winners_path),
        "candidate": dict(candidate),
        "winner": dict(selected_winners[0]),
        "materialization": {
            **_file_identity(root, materialization_file),
            "content_hash": materialization["content_hash"],
            "format": materialization["format"],
            "source_range": materialization["source_range"],
            "timeline": materialization["timeline"],
        },
        "cut_srt": _file_identity(root, srt_path, cue_count=len(cues)),
        "cues": cues,
    }
    core["content_hash"] = _content_hash(core)
    return core


def _atomic_publish(path: Path, document: Mapping[str, object]) -> None:
    payload = _pretty_json(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise HighlightVisualArtifactConflictError(
                f"immutable canonical artifact already differs: {path.name}"
            )
        return
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _atomic_publish_feedback_evidence(
    root: Path,
    *,
    cut_id: str,
    value: Mapping[str, object],
) -> None:
    evidence_root = (_cut_root(root, cut_id) / "feedback-evidence").resolve()
    path = (root / str(value["path"])).resolve()
    origin = (root / str(value["origin_path"])).resolve()
    if not path.is_relative_to(evidence_root) or not origin.is_relative_to(root):
        raise HighlightVisualContractError("feedback migration evidence path is unsafe")
    expected_bytes = value["bytes"]
    expected_sha256 = value["sha256"]
    if (
        not isinstance(expected_bytes, int)
        or isinstance(expected_bytes, bool)
        or expected_bytes < 0
        or not isinstance(expected_sha256, str)
        or not _SHA256_RE.fullmatch(expected_sha256)
    ):
        raise HighlightVisualContractError("feedback migration evidence identity is invalid")
    if path.is_file():
        if path.stat().st_size != expected_bytes or _sha256_file(path) != expected_sha256:
            raise HighlightVisualArtifactConflictError(
                "immutable feedback migration evidence already differs"
            )
        return
    if (
        not origin.is_file()
        or origin.stat().st_size != expected_bytes
        or _sha256_file(origin) != expected_sha256
    ):
        raise HighlightVisualContractError(
            "feedback migration evidence origin changed before trusted initialization"
        )
    payload = origin.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _publish_requested_feedback_evidence(
    root: Path,
    *,
    cut_id: str,
    requested_visual_feedback: object,
) -> None:
    feedback = _require_exact_keys(
        requested_visual_feedback,
        _REQUESTED_FEEDBACK_KEYS,
        "requested_visual_feedback",
    )
    migration = feedback["source_manifest"]
    if not isinstance(migration, dict) or migration.get("contract") != (
        FEEDBACK_IDENTITY_MIGRATION_CONTRACT
    ):
        return
    for key in ("events_file", "target_manifest"):
        evidence = _require_exact_keys(
            migration.get(key),
            {"path", "bytes", "sha256", "origin_path"},
            f"feedback migration {key}",
        )
        _atomic_publish_feedback_evidence(
            root,
            cut_id=cut_id,
            value=evidence,
        )


_POINTER_KEYS = {
    "contract",
    "episode_id",
    "cut_id",
    "revision_id",
    "state",
    "work_packet",
    "semantic_audit",
    "content_hash",
}


def _load_pointer(root: Path, cut_id: str, name: str) -> dict[str, object]:
    path = _cut_root(root, cut_id) / name
    document = _require_exact_keys(_load_json_object(path, name), _POINTER_KEYS, name)
    if (
        document["contract"] != POINTER_CONTRACT
        or document["episode_id"] != root.name
        or document["cut_id"] != cut_id
    ):
        raise HighlightVisualContractError(f"{name} belongs to another contract/episode/cut")
    _safe_revision_id(str(document["revision_id"]))
    if document["state"] not in {"pending", "ready"}:
        raise HighlightVisualContractError(f"{name} state is invalid")
    if name == CURRENT_POINTER_NAME and (
        document["state"] != "ready" or not isinstance(document["semantic_audit"], dict)
    ):
        raise HighlightVisualContractError("CURRENT pointer must identify one ready audit")
    if name == PENDING_POINTER_NAME and document["state"] != "pending":
        raise HighlightVisualContractError("PENDING pointer state mismatch")
    claimed = document["content_hash"]
    if not isinstance(claimed, str) or not _SHA256_RE.fullmatch(claimed):
        raise HighlightVisualContractError(f"{name} content_hash is invalid")
    unsigned = {key: value for key, value in document.items() if key != "content_hash"}
    if _content_hash(unsigned) != claimed:
        raise HighlightVisualContractError(f"{name} content hash mismatch")
    revision_id = str(document["revision_id"])
    _validate_pointer_artifact(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        value=document["work_packet"],
        expected_name=WORK_PACKET_NAME,
        expected_contract=WORK_PACKET_CONTRACT,
        label=f"{name}.work_packet",
    )
    if document["state"] == "ready":
        _validate_pointer_artifact(
            root,
            cut_id=cut_id,
            revision_id=revision_id,
            value=document["semantic_audit"],
            expected_name=SEMANTIC_AUDIT_NAME,
            expected_contract=SEMANTIC_AUDIT_CONTRACT,
            label=f"{name}.semantic_audit",
        )
    elif document["semantic_audit"] is not None:
        raise HighlightVisualContractError("PENDING pointer cannot bind semantic_audit")
    return document


_ARTIFACT_IDENTITY_KEYS = {"contract", "path", "bytes", "sha256", "content_hash"}


def _validate_pointer_artifact(
    root: Path,
    *,
    cut_id: str,
    revision_id: str,
    value: object,
    expected_name: str,
    expected_contract: str,
    label: str,
) -> None:
    identity = _require_exact_keys(value, _ARTIFACT_IDENTITY_KEYS, label)
    raw_file = {key: identity[key] for key in ("path", "bytes", "sha256")}
    path, _ = _validate_file_identity(root, raw_file, label)
    expected_path = _revision_dir(root, cut_id, revision_id) / expected_name
    if path != expected_path.resolve() or identity["contract"] != expected_contract:
        raise HighlightVisualContractError(f"{label} canonical path/contract mismatch")
    document = _load_json_object(path, label)
    if (
        document.get("contract") != expected_contract
        or document.get("content_hash") != identity["content_hash"]
    ):
        raise HighlightVisualContractError(f"{label} document identity drift")


def _write_pointer(
    root: Path,
    *,
    cut_id: str,
    name: str,
    revision_id: str,
    state: str,
    work_packet: Mapping[str, object],
    semantic_audit: Mapping[str, object] | None,
    expected_existing_content_hash: str | None,
) -> None:
    if name not in {CURRENT_POINTER_NAME, PENDING_POINTER_NAME}:
        raise HighlightVisualContractError("unknown visual pointer name")
    core: dict[str, object] = {
        "contract": POINTER_CONTRACT,
        "episode_id": root.name,
        "cut_id": cut_id,
        "revision_id": _safe_revision_id(revision_id),
        "state": state,
        "work_packet": dict(work_packet),
        "semantic_audit": dict(semantic_audit) if semantic_audit is not None else None,
    }
    core["content_hash"] = _content_hash(core)
    path = _cut_root(root, cut_id) / name
    payload = _pretty_json(core)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == payload:
        return
    if path.exists():
        existing = _load_pointer(root, cut_id, name)
        if existing["content_hash"] != expected_existing_content_hash:
            raise HighlightVisualArtifactConflictError(f"{name} compare-and-swap parent changed")
    elif expected_existing_content_hash is not None:
        raise HighlightVisualArtifactConflictError(
            f"{name} compare-and-swap expected an existing parent"
        )
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _pointer_identity(root: Path, cut_id: str, name: str) -> dict[str, object]:
    pointer = _load_pointer(root, cut_id, name)
    path = _cut_root(root, cut_id) / name
    return {
        **_file_identity(root, path),
        "content_hash": pointer["content_hash"],
        "revision_id": pointer["revision_id"],
    }


def _active_revision_id(root: Path, cut_id: str, *, current: bool = False) -> str:
    pointer_name = CURRENT_POINTER_NAME if current else PENDING_POINTER_NAME
    pointer = _load_pointer(root, cut_id, pointer_name)
    return _safe_revision_id(str(pointer["revision_id"]))


def _cut_dir(
    root: Path,
    cut_id: str,
    *,
    revision_id: str | None = None,
    current: bool = False,
) -> Path:
    selected_revision = revision_id or _active_revision_id(root, cut_id, current=current)
    return _revision_dir(root, cut_id, selected_revision)


def _require_pending_revision(root: Path, cut_id: str, revision_id: str) -> str:
    revision_id = _safe_revision_id(revision_id)
    pending = _load_pointer(root, cut_id, PENDING_POINTER_NAME)
    if pending["revision_id"] != revision_id:
        raise HighlightVisualArtifactConflictError(
            "proposal revision_id is no longer the active PENDING generation"
        )
    return revision_id


def _proposal_object(
    proposal: Mapping[str, object] | str | Path,
    *,
    canonical_path: Path,
) -> dict[str, object]:
    if isinstance(proposal, Mapping):
        return dict(proposal)
    path = Path(proposal).resolve()
    if path == canonical_path.resolve():
        raise HighlightVisualContractError("canonical artifact cannot be used as its own proposal")
    return _load_json_object(path, "proposal")


def _proposal_file_snapshot(
    root: Path,
    proposal: Mapping[str, object] | str | Path,
    *,
    canonical_path: Path,
    label: str,
) -> tuple[dict[str, object], dict[str, object] | None]:
    """Read a proposal once so validation and publication consume the same bytes."""

    if isinstance(proposal, Mapping):
        return dict(proposal), None
    path = Path(proposal).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HighlightVisualContractError(f"{label} is missing or escapes episode root")
    if path == canonical_path.resolve():
        raise HighlightVisualContractError("canonical artifact cannot be used as its own proposal")
    try:
        payload = path.read_bytes()
        value = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HighlightVisualContractError(f"{label} is missing or invalid JSON") from error
    if not isinstance(value, dict):
        raise HighlightVisualContractError(f"{label} must be an object")
    return value, {
        "path": path.relative_to(root).as_posix(),
        "bytes": len(payload),
        "sha256": _sha256_bytes(payload),
    }


def _json_file_identity_snapshot(
    root: Path,
    value: object,
    label: str,
) -> tuple[Path, dict[str, object], dict[str, object]]:
    """Read one JSON artifact once and derive both its identity and document."""

    identity = _require_exact_keys(value, _MEDIA_KEYS, label)
    raw_path = identity["path"]
    if not isinstance(raw_path, str) or Path(raw_path).is_absolute():
        raise HighlightVisualContractError(f"{label}.path must be episode-relative")
    path = (root / raw_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HighlightVisualContractError(f"{label} path escapes episode root or is missing")
    try:
        payload = path.read_bytes()
        document = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HighlightVisualContractError(f"{label} is missing or invalid JSON") from error
    actual_identity = {
        "path": path.relative_to(root).as_posix(),
        "bytes": len(payload),
        "sha256": _sha256_bytes(payload),
    }
    if dict(identity) != actual_identity:
        raise HighlightVisualContractError(f"{label} identity drift")
    if not isinstance(document, dict):
        raise HighlightVisualContractError(f"{label} must be an object")
    return path, actual_identity, document


def _nonempty_text(value: object, label: str, *, minimum: int = 1) -> str:
    if not isinstance(value, str) or len(value.strip()) < minimum:
        raise HighlightVisualContractError(f"{label} must be concrete non-empty text")
    return value.strip()


def _trusted_worker_execution(value: Mapping[str, str], *, expected_role: str) -> dict[str, object]:
    identity = _require_exact_keys(dict(value), _WORKER_EXECUTION_KEYS, "trusted worker execution")
    normalized = {
        key: _nonempty_text(identity[key], f"worker_execution.{key}")
        for key in _WORKER_EXECUTION_KEYS
    }
    if normalized["role"] != expected_role:
        raise HighlightVisualContractError(f"trusted worker role must be {expected_role}")
    return normalized


def _execution_receipt_path(
    root: Path, *, cut_id: str, revision_id: str, phase: str
) -> Path:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,127}", phase):
        raise HighlightVisualContractError("trusted execution phase is unsafe")
    return (
        _cut_root(root, cut_id)
        / "jobs"
        / _safe_revision_id(revision_id)
        / "receipts"
        / f"{phase}.json"
    )


def _validate_execution_stream_identity(
    root: Path, value: object, label: str
) -> dict[str, object]:
    identity = _require_exact_keys(value, _MEDIA_KEYS, label)
    raw_path = identity["path"]
    if not isinstance(raw_path, str) or Path(raw_path).is_absolute():
        raise HighlightVisualContractError(f"{label}.path must be episode-relative")
    path = (root / raw_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HighlightVisualContractError(f"{label} path escapes episode or is missing")
    byte_count = identity["bytes"]
    digest = identity["sha256"]
    if (
        not isinstance(byte_count, int)
        or isinstance(byte_count, bool)
        or byte_count < 0
        or byte_count != path.stat().st_size
        or not isinstance(digest, str)
        or not _SHA256_RE.fullmatch(digest)
        or digest != _sha256_file(path)
    ):
        raise HighlightVisualContractError(f"{label} identity drift")
    return dict(identity)


def _require_trusted_execution_receipt(
    root: Path,
    *,
    cut_id: str,
    revision_id: str,
    phase: str,
    role: str,
    worker_identity: Mapping[str, str],
    worker_proposal: Mapping[str, object] | str | Path,
    execution_receipt: Mapping[str, object] | None,
    expected_worker_proposal_identity: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Bind acceptance to one canonical orchestration execution receipt.

    Runtime callers cannot replace the required episode-local, hash-bound receipt
    with a bare Mapping proposal or a self-reported worker string.
    """

    if execution_receipt is None:
        raise HighlightVisualContractError(
            "acceptance requires a canonical trusted execution receipt"
        )
    if isinstance(worker_proposal, Mapping):
        raise HighlightVisualContractError(
            "trusted execution acceptance requires an episode-local proposal file"
        )
    receipt_path, receipt_identity, receipt_document = _json_file_identity_snapshot(
        root, execution_receipt, "trusted execution receipt"
    )
    expected_path = _execution_receipt_path(
        root, cut_id=cut_id, revision_id=revision_id, phase=phase
    ).resolve()
    if receipt_path != expected_path:
        raise HighlightVisualContractError("trusted execution receipt path is not canonical")
    receipt = _require_exact_keys(
        receipt_document,
        _EXECUTION_RECEIPT_KEYS,
        "trusted execution receipt",
    )
    claimed_hash = receipt["content_hash"]
    unsigned = {key: value for key, value in receipt.items() if key != "content_hash"}
    if (
        not isinstance(claimed_hash, str)
        or not _SHA256_RE.fullmatch(claimed_hash)
        or claimed_hash != _content_hash(unsigned)
    ):
        raise HighlightVisualContractError("trusted execution receipt content hash drift")
    trusted_worker = _trusted_worker_execution(worker_identity, expected_role=role)
    receipt_worker = _trusted_worker_execution(receipt["worker_identity"], expected_role=role)
    if (
        receipt["contract"] != EXECUTION_RECEIPT_CONTRACT
        or receipt["episode_id"] != root.name
        or receipt["cut_id"] != cut_id
        or receipt["revision_id"] != revision_id
        or receipt["phase"] != phase
        or receipt["role"] != role
        or receipt_worker != trusted_worker
    ):
        raise HighlightVisualContractError("trusted execution receipt lineage/worker drift")
    prompt_sha = receipt["prompt_sha256"]
    if not isinstance(prompt_sha, str) or not _SHA256_RE.fullmatch(prompt_sha):
        raise HighlightVisualContractError("trusted execution prompt hash is invalid")
    proposal_path = Path(worker_proposal).resolve()
    if not proposal_path.is_relative_to(root) or not proposal_path.is_file():
        raise HighlightVisualContractError("trusted worker proposal is missing or escapes episode")
    current_proposal_identity = _file_identity(root, proposal_path)
    if expected_worker_proposal_identity is not None:
        expected_proposal = _require_exact_keys(
            expected_worker_proposal_identity,
            _MEDIA_KEYS,
            "trusted worker proposal snapshot",
        )
        if current_proposal_identity != expected_proposal:
            raise HighlightVisualContractError(
                "trusted worker proposal changed after its acceptance snapshot"
            )
    else:
        expected_proposal = current_proposal_identity
    if receipt["proposal"] != expected_proposal:
        raise HighlightVisualContractError("trusted execution proposal identity drift")
    proposal_path, proposal_identity, proposal_document = _json_file_identity_snapshot(
        root, expected_proposal, "trusted worker proposal"
    )
    if proposal_identity != expected_proposal:
        raise HighlightVisualContractError("trusted execution proposal snapshot drift")
    for name in ("phase_input", "stdout", "stderr"):
        _validate_execution_stream_identity(root, receipt[name], f"trusted execution {name}")
    prepare_path, prepare_identity, prepare_document = _json_file_identity_snapshot(
        root, receipt["prepare"], "trusted execution prepare receipt"
    )
    prepare = _require_exact_keys(
        prepare_document,
        _EXECUTION_PREPARE_KEYS,
        "trusted execution prepare receipt",
    )
    prepare_hash = prepare["content_hash"]
    prepare_unsigned = {key: value for key, value in prepare.items() if key != "content_hash"}
    prepare_attempt = prepare["attempt"]
    prepare_pid = prepare["orchestrator_pid"]
    if (
        not isinstance(prepare_hash, str)
        or prepare_hash != _content_hash(prepare_unsigned)
        or not isinstance(prepare_attempt, int)
        or isinstance(prepare_attempt, bool)
        or prepare_attempt < 1
        or not isinstance(prepare_pid, int)
        or isinstance(prepare_pid, bool)
        or prepare_pid < 1
    ):
        raise HighlightVisualContractError("trusted execution prepare receipt is invalid")
    expected_prepare_path = (
        _cut_root(root, cut_id)
        / "jobs"
        / revision_id
        / "receipts"
        / f"{phase}.attempts"
        / f"attempt-{prepare_attempt:03d}"
        / "PREPARE.json"
    ).resolve()
    if (
        prepare_path != expected_prepare_path
        or receipt["prepare"] != prepare_identity
        or prepare["contract"] != EXECUTION_PREPARE_CONTRACT
        or prepare["episode_id"] != root.name
        or prepare["cut_id"] != cut_id
        or prepare["revision_id"] != revision_id
        or prepare["phase"] != phase
        or prepare["role"] != role
        or prepare["prompt_sha256"] != prompt_sha
        or prepare["phase_input"] != receipt["phase_input"]
        or prepare["proposal_path"] != proposal_path.relative_to(root).as_posix()
    ):
        raise HighlightVisualContractError("trusted execution prepare lineage drift")
    return {
        "receipt": receipt_identity,
        "proposal": proposal_identity,
        "proposal_document": proposal_document,
    }


def _accepted_worker_proposal_snapshot(
    strict_execution: object,
    local_snapshot: Mapping[str, object],
) -> dict[str, object]:
    """Use the exact JSON bytes verified by the trusted execution boundary."""

    if not isinstance(strict_execution, dict):
        return dict(local_snapshot)
    verified = strict_execution.get("proposal_document")
    if not isinstance(verified, dict) or verified != dict(local_snapshot):
        raise HighlightVisualContractError(
            "trusted worker proposal differs from its acceptance snapshot"
        )
    return verified


def _require_persisted_execution_receipt(
    root: Path,
    *,
    cut_id: str,
    revision_id: str,
    phase: str,
    role: str,
    worker_identity: Mapping[str, str],
) -> Path:
    receipt_path = _execution_receipt_path(
        root, cut_id=cut_id, revision_id=revision_id, phase=phase
    ).resolve()
    receipt_identity = _file_identity(root, receipt_path)
    _, _, receipt = _json_file_identity_snapshot(
        root, receipt_identity, "trusted execution receipt"
    )
    proposal_identity = receipt.get("proposal")
    if not isinstance(proposal_identity, dict) or not isinstance(
        proposal_identity.get("path"), str
    ):
        raise HighlightVisualContractError("trusted execution proposal identity is invalid")
    proposal_path = (root / str(proposal_identity["path"])).resolve()
    _require_trusted_execution_receipt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        phase=phase,
        role=role,
        worker_identity=worker_identity,
        worker_proposal=proposal_path,
        execution_receipt=receipt_identity,
        expected_worker_proposal_identity=proposal_identity,
    )
    return proposal_path


def _string_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise HighlightVisualContractError(f"{label} must be a list of non-empty strings")
    stripped = [item.strip() for item in value]
    if len(set(stripped)) != len(stripped):
        raise HighlightVisualContractError(f"{label} must not contain duplicates")
    return stripped


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HighlightVisualContractError(f"{label} must be a number")
    return float(value)


def _visual_feedback_directives(
    work: Mapping[str, object],
) -> list[dict[str, object]]:
    feedback = _require_exact_keys(
        work.get("requested_visual_feedback"),
        _REQUESTED_FEEDBACK_KEYS,
        "requested_visual_feedback",
    )
    if (
        feedback["contract"] != REQUESTED_VISUAL_FEEDBACK_CONTRACT
        or feedback["cut_id"] != work["cut_id"]
    ):
        raise HighlightVisualContractError(
            "requested visual feedback belongs to another contract/cut"
        )
    claimed = feedback["content_hash"]
    unsigned = {key: value for key, value in feedback.items() if key != "content_hash"}
    if (
        not isinstance(claimed, str)
        or not _SHA256_RE.fullmatch(claimed)
        or _content_hash(unsigned) != claimed
    ):
        raise HighlightVisualContractError("requested visual feedback hash mismatch")
    raw_directives = feedback["directives"]
    if not isinstance(raw_directives, list):
        raise HighlightVisualContractError("requested visual feedback directives are invalid")
    directives: list[dict[str, object]] = []
    for index, raw in enumerate(raw_directives, 1):
        if not isinstance(raw, dict):
            raise HighlightVisualContractError(
                f"requested visual directive[{index}] fields mismatch"
            )
        raw_keys = frozenset(raw)
        if raw_keys not in {
            frozenset(_FEEDBACK_DIRECTIVE_KEYS),
            frozenset(_FEEDBACK_DIRECTIVE_KEYS - {"source_component"}),
        }:
            raise HighlightVisualContractError(
                f"requested visual directive[{index}] fields mismatch"
            )
        directive = raw
        if "source_component" not in directive:
            directives.append(directive)
            continue
        source_component = _require_exact_keys(
            directive["source_component"],
            _SOURCE_COMPONENT_PROJECTION_KEYS,
            f"requested visual directive[{index}].source_component",
        )
        source_hash = source_component["content_hash"]
        source_core = {
            key: item for key, item in source_component.items() if key != "content_hash"
        }
        if (
            not isinstance(source_hash, str)
            or not _SHA256_RE.fullmatch(source_hash)
            or source_hash != _content_hash(source_core)
            or source_component["component_id"] != directive["component_id"]
            or source_component["lane"] != directive["lane"]
        ):
            raise HighlightVisualContractError(
                "requested visual directive source component identity drift"
            )
        directives.append(directive)
    return directives


def _reject_human_removed_assets(
    root: Path,
    work: Mapping[str, object],
    assets: Sequence[Mapping[str, object]],
) -> None:
    denied_sha256: set[str] = set()
    denied_urls: set[str] = set()
    denied_slugs: set[str] = set()
    directives = _visual_feedback_directives(work)
    legacy_sources: dict[str, Mapping[str, object]] = {}
    if any("source_component" not in directive for directive in directives):
        feedback = work.get("requested_visual_feedback")
        if not isinstance(feedback, dict) or not isinstance(feedback.get("source_manifest"), dict):
            raise HighlightVisualContractError(
                "legacy removed Stock evidence has no bound source manifest"
            )
        request_identity = _validate_revision_request_identity(root, work.get("revision_request"))
        if request_identity["kind"] != "feedback":
            raise HighlightVisualContractError(
                "legacy removed Stock evidence has no feedback request"
            )
        request_path = (root / str(request_identity["path"])).resolve()
        request = _load_json_object(request_path, "legacy removed Stock revision request")
        source_manifest, source_components = _source_feedback_components(
            root,
            request=request,
            cut_id=str(work["cut_id"]),
        )
        if source_manifest != feedback["source_manifest"]:
            raise HighlightVisualContractError(
                "legacy removed Stock source manifest identity drift"
            )
        legacy_sources = source_components
    for directive in directives:
        if directive["action"] != "remove":
            continue
        if "source_component" in directive:
            source = _require_exact_keys(
                directive["source_component"],
                _SOURCE_COMPONENT_PROJECTION_KEYS,
                "removed visual source component",
            )
        else:
            component_id = str(directive["component_id"])
            component = legacy_sources.get(component_id)
            if component is None or component.get("lane") != directive["lane"]:
                raise HighlightVisualContractError(
                    f"legacy removed Stock component identity drift: {component_id}"
                )
            source_span = {
                "t0": _number(component.get("t0"), f"{component_id}.t0"),
                "t1": _number(component.get("t1"), f"{component_id}.t1"),
            }
            if source_span != directive["timeline_seconds"]:
                raise HighlightVisualContractError(
                    f"legacy removed Stock component span drift: {component_id}"
                )
            source = _feedback_source_component_projection(
                root,
                component,
                label=f"legacy removed source component {component_id}",
            )
        asset = source["asset"]
        if isinstance(asset, dict) and isinstance(asset.get("sha256"), str):
            denied_sha256.add(str(asset["sha256"]))
        source_url = source["source_url"]
        if isinstance(source_url, str) and source_url:
            denied_urls.add(source_url.casefold())
        slug = source["slug"]
        if isinstance(slug, str) and slug:
            denied_slugs.add(slug.casefold())
    for asset in assets:
        media = asset.get("original_media")
        original_sha256 = media.get("sha256") if isinstance(media, dict) else None
        if (
            original_sha256 in denied_sha256
            or (
                isinstance(asset.get("source_url"), str)
                and str(asset["source_url"]).casefold() in denied_urls
            )
            or (
                isinstance(asset.get("asset_id"), str)
                and str(asset["asset_id"]).casefold() in denied_slugs
            )
        ):
            raise HighlightVisualContractError(
                "trusted authority reintroduces a human-removed visual source"
            )


def _requested_move_range(work: Mapping[str, object], component_id: str) -> dict[str, float] | None:
    for directive in _visual_feedback_directives(work):
        if directive["component_id"] != component_id or directive["action"] != "move":
            continue
        source = directive["timeline_seconds"]
        if not isinstance(source, dict):
            raise HighlightVisualContractError("move source span is invalid")
        start = _number(directive["move_to_seconds"], f"{component_id}.move_to_seconds")
        duration = _number(source["t1"], f"{component_id}.source.t1") - _number(
            source["t0"], f"{component_id}.source.t0"
        )
        return {"t0": start, "t1": start + duration}
    return None


def _director_events(
    value: object,
    *,
    work: Mapping[str, object],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    if not isinstance(value, list) or not value:
        raise HighlightVisualContractError("Director plan requires at least one event")
    cue_values = work["cues"]
    if not isinstance(cue_values, list) or not cue_values:
        raise HighlightVisualContractError("work packet cues are invalid")
    cue_by_id = {
        cue["number"]: cue
        for cue in cue_values
        if isinstance(cue, dict) and isinstance(cue.get("number"), int)
    }
    cue_order = list(cue_by_id)
    events: list[dict[str, object]] = []
    event_ids: set[str] = set()
    previous_end = -1.0
    for index, raw in enumerate(value, 1):
        event = _require_exact_keys(raw, _DIRECTOR_EVENT_KEYS, f"events[{index}]")
        event_id = event["event_id"]
        if (
            not isinstance(event_id, str)
            or not _CUT_ID_RE.fullmatch(event_id)
            or event_id in event_ids
        ):
            raise HighlightVisualContractError("Director event_id is unsafe or duplicated")
        event_ids.add(event_id)
        cue_ids = event["cue_ids"]
        if (
            not isinstance(cue_ids, list)
            or not cue_ids
            or any(not isinstance(item, int) or isinstance(item, bool) for item in cue_ids)
            or len(set(cue_ids)) != len(cue_ids)
        ):
            raise HighlightVisualContractError(f"{event_id}.cue_ids are invalid")
        try:
            positions = [cue_order.index(item) for item in cue_ids]
            selected_cues = [cue_by_id[item] for item in cue_ids]
        except (KeyError, ValueError) as error:
            raise HighlightVisualContractError(
                f"{event_id} references a missing SRT cue"
            ) from error
        if positions != list(range(positions[0], positions[0] + len(positions))):
            raise HighlightVisualContractError(f"{event_id}.cue_ids must be contiguous")
        t0 = _number(event["t0"], f"{event_id}.t0")
        t1 = _number(event["t1"], f"{event_id}.t1")
        expected_t0 = float(selected_cues[0]["start_sec"])
        expected_t1 = float(selected_cues[-1]["end_sec"])
        expected_quote = "\n".join(str(cue["text"]) for cue in selected_cues)
        move_range = _requested_move_range(work, event_id)
        if move_range is not None:
            overlapping_ids = [
                int(cue["number"])
                for cue in cue_values
                if isinstance(cue, dict)
                and float(cue["start_sec"]) < t1
                and float(cue["end_sec"]) > t0
            ]
            if t0 != move_range["t0"] or t1 != move_range["t1"] or overlapping_ids != cue_ids:
                raise HighlightVisualContractError(
                    f"{event_id} move must use exact requested range and overlapping cues"
                )
        elif t0 != expected_t0 or t1 != expected_t1:
            raise HighlightVisualContractError(
                f"{event_id} range must snap to exact cue boundaries"
            )
        if event["quote"] != expected_quote:
            raise HighlightVisualContractError(
                f"{event_id} quote must equal newline-joined exact cue text"
            )
        if t0 < previous_end:
            raise HighlightVisualContractError(
                "Director events must be ordered and non-overlapping"
            )
        previous_end = t1
        category = event["category"]
        form = event["form"]
        decision = event["decision"]
        if category not in _DIRECTOR_CATEGORIES:
            raise HighlightVisualContractError(f"{event_id}.category is not allowed")
        if form not in _DIRECTOR_FORMS:
            raise HighlightVisualContractError(f"{event_id}.form is not allowed")
        if decision not in _DIRECTOR_DECISIONS:
            raise HighlightVisualContractError(f"{event_id}.decision is not allowed")
        if decision == "intentional_aroll":
            if category != "none" or form != "aroll":
                raise HighlightVisualContractError(
                    "intentional_aroll requires category=none and form=aroll"
                )
            _nonempty_text(event["rationale"], f"{event_id}.rationale", minimum=12)
        elif category == "none" or form == "aroll":
            raise HighlightVisualContractError("add_visual cannot use category=none or form=aroll")
        description = _nonempty_text(event["description"], f"{event_id}.description")
        rationale = _nonempty_text(event["rationale"], f"{event_id}.rationale")
        negative = _string_list(event["negative_constraints"], f"{event_id}.negative_constraints")
        angles = _string_list(event["search_angles"], f"{event_id}.search_angles")
        on_screen_text = event["on_screen_text"]
        shots_hint = event["shots_hint"]
        if (
            not isinstance(shots_hint, int)
            or isinstance(shots_hint, bool)
            or not 1 <= shots_hint <= 5
        ):
            raise HighlightVisualContractError(f"{event_id}.shots_hint must be in [1, 5]")
        if on_screen_text is not None and (
            not isinstance(on_screen_text, str) or not on_screen_text.strip()
        ):
            raise HighlightVisualContractError(
                f"{event_id}.on_screen_text must be null or non-empty text"
            )
        if category in {"keyword", "quote", "chapter", "bigstat"} and not isinstance(
            on_screen_text, str
        ):
            raise HighlightVisualContractError(
                f"{event_id} title/card intent requires exact on_screen_text"
            )
        if category in {"stock_scene", "none"} and on_screen_text is not None:
            raise HighlightVisualContractError(
                f"{event_id} category={category} cannot carry on_screen_text"
            )
        if category in {"stock_scene", "kol_quote"} and not 3 <= len(angles) <= 5:
            raise HighlightVisualContractError(
                f"{event_id} asset search requires 3-5 distinct search angles"
            )
        duration = t1 - t0
        if category == "stock_scene" and duration > 3.0 * shots_hint:
            raise HighlightVisualContractError(
                f"{event_id} stock_scene exceeds 3s per planned shot"
            )
        if category in {"keyword", "person_inset", "self_promo", "meme"} and not (
            2.0 <= duration <= 4.0
        ):
            raise HighlightVisualContractError(f"{event_id} overlay duration must be within 2-4s")
        if category in {"quote", "evidence_doc"} and not 6.0 <= duration <= 10.0:
            raise HighlightVisualContractError(
                f"{event_id} evidence/quote duration must be within 6-10s"
            )
        if category in {"chapter", "bigstat", "book_cover"} and not 1.5 <= duration <= 6.0:
            raise HighlightVisualContractError(f"{event_id} card duration must be within 1.5-6s")
        normalized = {
            **event,
            "t0": t0,
            "t1": t1,
            "description": description,
            "on_screen_text": on_screen_text,
            "shots_hint": shots_hint,
            "negative_constraints": negative,
            "search_angles": angles,
            "rationale": rationale,
        }
        events.append(normalized)

    start = float(cue_values[0]["start_sec"])  # type: ignore[index]
    end = float(cue_values[-1]["end_sec"])  # type: ignore[index]
    cursor = start
    max_gap = (0.0, start, start)
    for event in events:
        t0 = float(event["t0"])
        t1 = float(event["t1"])
        if t0 > cursor and t0 - cursor > max_gap[0]:
            max_gap = (t0 - cursor, cursor, t0)
        cursor = max(cursor, t1)
    if end > cursor and end - cursor > max_gap[0]:
        max_gap = (end - cursor, cursor, end)
    duration_minutes = (end - start) / 60.0
    add_visual_count = sum(event["decision"] == "add_visual" for event in events)
    planned_visual_count = sum(
        int(event["shots_hint"]) for event in events if event["decision"] == "add_visual"
    )
    planned_stock_video_count = sum(
        int(event["shots_hint"])
        for event in events
        if event["decision"] == "add_visual" and event["category"] == "stock_scene"
    )
    cutaway_count = sum(
        event["decision"] == "add_visual" and event["form"] == "cutaway" for event in events
    )
    visual_rate = planned_visual_count / duration_minutes
    cutaway_rate = cutaway_count / duration_minutes
    coverage: dict[str, object] = {
        "timeline_start_sec": start,
        "timeline_end_sec": end,
        "add_visual_count": add_visual_count,
        "planned_visual_count": planned_visual_count,
        "planned_stock_video_count": planned_stock_video_count,
        "intentional_aroll_count": sum(
            event["decision"] == "intentional_aroll" for event in events
        ),
        "max_uncovered_sec": round(max_gap[0], 3),
        "max_uncovered_start_sec": round(max_gap[1], 3),
        "max_uncovered_end_sec": round(max_gap[2], 3),
        "visual_events_per_minute": round(visual_rate, 3),
        "cutaway_events_per_minute": round(cutaway_rate, 3),
    }
    if work["format"] == "long":
        if max_gap[0] > 20.0:
            raise HighlightVisualContractError(
                "long Highlight has a >20s window without a visual or intentional_aroll event"
            )
        if visual_rate < 4.5:
            raise HighlightVisualContractError(
                "long Highlight requires at least 4.5 content visual events per minute"
            )
        if planned_stock_video_count < 3:
            raise HighlightVisualContractError(
                "long Highlight requires at least 3 planned stock video shots"
            )
        if cutaway_rate > 3.5:
            raise HighlightVisualContractError(
                "long Highlight exceeds 3.5 cutaway events per minute"
            )
    return events, coverage


def _enforce_director_feedback(
    *,
    work: Mapping[str, object],
    events: Sequence[Mapping[str, object]],
) -> None:
    event_by_id = {str(event["event_id"]): event for event in events}
    for directive in _visual_feedback_directives(work):
        component_id = str(directive["component_id"])
        action = str(directive["action"])
        event = event_by_id.get(component_id)
        if action == "remove":
            if event is not None:
                raise HighlightVisualContractError(
                    f"removed visual component must not be planned: {component_id}"
                )
            continue
        if event is None or event["decision"] != "add_visual":
            raise HighlightVisualContractError(
                f"missing requested visual component event: {component_id}"
            )
        if action == "edit_text" and event["on_screen_text"] != directive["replacement"]:
            raise HighlightVisualContractError(
                f"{component_id} must preserve exact requested replacement text"
            )
        if action == "move":
            expected = _requested_move_range(work, component_id)
            if expected is None or event["t0"] != expected["t0"] or event["t1"] != expected["t1"]:
                raise HighlightVisualContractError(
                    f"{component_id} must use exact requested move range"
                )


def _load_hashed_artifact(
    root: Path,
    path: Path,
    *,
    contract: str,
    exact_keys: set[str],
) -> ArtifactSelection:
    document = _require_exact_keys(_load_json_object(path, path.name), exact_keys, path.name)
    if document["contract"] != contract:
        raise HighlightVisualContractError(f"{path.name} contract mismatch")
    claimed = document["content_hash"]
    if not isinstance(claimed, str) or not _SHA256_RE.fullmatch(claimed):
        raise HighlightVisualContractError(f"{path.name} content_hash is invalid")
    unhashed = {key: value for key, value in document.items() if key != "content_hash"}
    if _content_hash(unhashed) != claimed:
        raise HighlightVisualContractError(f"{path.name} content hash mismatch")
    return ArtifactSelection(path=path, document=document, episode_root=root)


_WORK_KEYS = {
    "contract",
    "episode_id",
    "cut_id",
    "revision_id",
    "revision_request",
    "requested_visual_feedback",
    "parent_current",
    "format",
    "source_range",
    "editorial_master",
    "candidates_file",
    "winners_file",
    "candidate",
    "winner",
    "materialization",
    "cut_srt",
    "cues",
    "content_hash",
}


def _prospective_work_document(
    episode_root: str | Path,
    *,
    cut_id: str,
    revision_request: str | Path | None = None,
    editorial_master: EditorialMasterSelection | object | None = None,
) -> tuple[Path, str, dict[str, object]]:
    root = _root(episode_root)
    cut_id = _safe_cut_id(cut_id)
    current_path = _cut_root(root, cut_id) / CURRENT_POINTER_NAME
    if revision_request is None and current_path.is_file():
        raise HighlightVisualContractError(
            "a new episode-local revision_request is required after CURRENT exists"
        )
    request_identity = _revision_request_identity(root, revision_request)
    parent_current = (
        _pointer_identity(root, cut_id, CURRENT_POINTER_NAME) if current_path.is_file() else None
    )
    placeholder = "r-" + "0" * 24
    provisional = _current_work_core(
        root,
        cut_id=cut_id,
        revision_id=placeholder,
        revision_request=request_identity,
        parent_current=parent_current,
        editorial_master=editorial_master,
    )
    revision_seed = {
        key: value
        for key, value in provisional.items()
        if key not in {"revision_id", "content_hash"}
    }
    revision_id = f"r-{_content_hash(revision_seed)[:24]}"
    document = _current_work_core(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        revision_request=request_identity,
        parent_current=parent_current,
        editorial_master=editorial_master,
    )
    legacy = _legacy_feedback_work_projection(document)
    pending_path = _cut_root(root, cut_id) / PENDING_POINTER_NAME
    if legacy is not None and pending_path.is_file():
        pending = _load_pointer(root, cut_id, PENDING_POINTER_NAME)
        if pending["revision_id"] == legacy["revision_id"]:
            legacy_path = (
                _revision_dir(root, cut_id, str(legacy["revision_id"])) / WORK_PACKET_NAME
            )
            legacy_selection = _load_hashed_artifact(
                root,
                legacy_path,
                contract=WORK_PACKET_CONTRACT,
                exact_keys=_WORK_KEYS,
            )
            if legacy_selection.document != legacy:
                raise HighlightVisualContractError(
                    "legacy pending Director work packet upstream lineage is stale"
                )
            if pending["work_packet"] != legacy_selection.identity():
                raise HighlightVisualContractError(
                    "legacy pending Director work packet pointer identity drift"
                )
            document = legacy
    return root, cut_id, document


def _legacy_feedback_work_projection(
    document: Mapping[str, object],
) -> dict[str, object] | None:
    """Reproduce the exact pre-source-evidence work identity for safe resume only."""

    feedback = document.get("requested_visual_feedback")
    if not isinstance(feedback, dict) or not isinstance(feedback.get("directives"), list):
        return None
    directives = feedback["directives"]
    if not directives or any(
        not isinstance(row, dict) or set(row) != _FEEDBACK_DIRECTIVE_KEYS for row in directives
    ):
        return None
    legacy_feedback = dict(feedback)
    legacy_feedback["directives"] = [
        {key: value for key, value in row.items() if key != "source_component"}
        for row in directives
    ]
    legacy_feedback["content_hash"] = _content_hash(
        {key: value for key, value in legacy_feedback.items() if key != "content_hash"}
    )
    legacy = {**document, "requested_visual_feedback": legacy_feedback}
    revision_seed = {
        key: value for key, value in legacy.items() if key not in {"revision_id", "content_hash"}
    }
    legacy["revision_id"] = f"r-{_content_hash(revision_seed)[:24]}"
    legacy["content_hash"] = _content_hash(
        {key: value for key, value in legacy.items() if key != "content_hash"}
    )
    return legacy


def preflight_visual_work_packet(
    episode_root: str | Path,
    *,
    cut_id: str,
    revision_request: str | Path | None = None,
    editorial_master: EditorialMasterSelection | object | None = None,
) -> dict[str, object]:
    """Read-only prospective generation identity; writes no artifact or pointer."""

    root, cut_id, document = _prospective_work_document(
        episode_root,
        cut_id=cut_id,
        revision_request=revision_request,
        editorial_master=editorial_master,
    )
    core: dict[str, object] = {
        "contract": PREFLIGHT_CONTRACT,
        "episode_id": root.name,
        "cut_id": cut_id,
        "revision_id": document["revision_id"],
        "status": "would_initialize",
        "revision_request": document["revision_request"],
        "requested_visual_feedback": document["requested_visual_feedback"],
        "parent_current": document["parent_current"],
        "editorial_master": document["editorial_master"],
        "format": document["format"],
        "source_range": document["source_range"],
        "materialization": document["materialization"],
        "candidates_file": document["candidates_file"],
        "winners_file": document["winners_file"],
        "cut_srt": document["cut_srt"],
        "prospective_work_content_hash": document["content_hash"],
    }
    core["content_hash"] = _content_hash(core)
    return core


def init_visual_work_packet(
    episode_root: str | Path,
    *,
    cut_id: str,
    revision_request: str | Path | None = None,
    editorial_master: EditorialMasterSelection | object | None = None,
) -> ArtifactSelection:
    """Create one immutable work packet from the fresh production inputs."""

    root, cut_id, document = _prospective_work_document(
        episode_root,
        cut_id=cut_id,
        revision_request=revision_request,
        editorial_master=editorial_master,
    )
    revision_id = str(document["revision_id"])
    current_path = _cut_root(root, cut_id) / CURRENT_POINTER_NAME
    path = _revision_dir(root, cut_id, revision_id) / WORK_PACKET_NAME
    _publish_requested_feedback_evidence(
        root,
        cut_id=cut_id,
        requested_visual_feedback=document["requested_visual_feedback"],
    )
    _atomic_publish(path, document)
    selection = ArtifactSelection(path=path, document=document, episode_root=root)
    pending_path = _cut_root(root, cut_id) / PENDING_POINTER_NAME
    expected_pending_hash: str | None = None
    if pending_path.is_file():
        pending = _load_pointer(root, cut_id, PENDING_POINTER_NAME)
        if pending["revision_id"] != revision_id:
            current = (
                _load_pointer(root, cut_id, CURRENT_POINTER_NAME)
                if current_path.is_file()
                else None
            )
            if current is None or pending["revision_id"] != current["revision_id"]:
                raise HighlightVisualArtifactConflictError(
                    "another visual revision is still pending"
                )
        expected_pending_hash = str(pending["content_hash"])
    _write_pointer(
        root,
        cut_id=cut_id,
        name=PENDING_POINTER_NAME,
        revision_id=revision_id,
        state="pending",
        work_packet=selection.identity(),
        semantic_audit=None,
        expected_existing_content_hash=expected_pending_hash,
    )
    return load_visual_work_packet(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        editorial_master=editorial_master,
    )


def load_visual_work_packet(
    episode_root: str | Path,
    *,
    cut_id: str,
    revision_id: str | None = None,
    editorial_master: EditorialMasterSelection | object | None = None,
) -> ArtifactSelection:
    """Freshly verify the work packet and every production input it binds."""

    root = _root(episode_root)
    cut_id = _safe_cut_id(cut_id)
    selected_revision = revision_id or _active_revision_id(root, cut_id)
    selected_revision = _safe_revision_id(selected_revision)
    path = _revision_dir(root, cut_id, selected_revision) / WORK_PACKET_NAME
    selection = _load_hashed_artifact(
        root, path, contract=WORK_PACKET_CONTRACT, exact_keys=_WORK_KEYS
    )
    if selection.document["revision_id"] != selected_revision:
        raise HighlightVisualContractError("Director work packet revision_id mismatch")
    request_identity = _validate_revision_request_identity(
        root, selection.document["revision_request"]
    )
    expected = _current_work_core(
        root,
        cut_id=cut_id,
        revision_id=selected_revision,
        revision_request=request_identity,
        parent_current=(
            dict(selection.document["parent_current"])
            if isinstance(selection.document["parent_current"], dict)
            else None
        ),
        editorial_master=editorial_master,
    )
    legacy_expected = _legacy_feedback_work_projection(expected)
    if selection.document != expected and selection.document != legacy_expected:
        raise HighlightVisualContractError("Director work packet upstream lineage is stale")
    pending_path = _cut_root(root, cut_id) / PENDING_POINTER_NAME
    pending = _load_pointer(root, cut_id, PENDING_POINTER_NAME) if pending_path.is_file() else None
    if pending is not None and pending["revision_id"] == selected_revision:
        current_path = _cut_root(root, cut_id) / CURRENT_POINTER_NAME
        parent_current = selection.document["parent_current"]
        if current_path.is_file():
            current = _load_pointer(root, cut_id, CURRENT_POINTER_NAME)
            if current["revision_id"] != selected_revision:
                if not isinstance(parent_current, dict) or parent_current != _pointer_identity(
                    root, cut_id, CURRENT_POINTER_NAME
                ):
                    raise HighlightVisualContractError(
                        "visual revision CURRENT compare-and-swap parent is stale"
                    )
        elif parent_current is not None:
            raise HighlightVisualContractError("visual revision expected a missing CURRENT parent")
    if revision_id is None:
        if pending is None or pending["work_packet"] != selection.identity():
            raise HighlightVisualContractError("PENDING pointer work packet identity drift")
    return selection


_DIRECTOR_KEYS = {
    "contract",
    "episode_id",
    "cut_id",
    "worker_execution",
    "work_packet",
    "events",
    "coverage",
    "content_hash",
}
_DIRECTOR_PROPOSAL_KEYS = _DIRECTOR_KEYS - {"content_hash", "worker_execution"}


def accept_director_plan(
    episode_root: str | Path,
    *,
    cut_id: str,
    revision_id: str,
    proposal: Mapping[str, object] | str | Path,
    worker_identity: Mapping[str, str],
    execution_receipt: Mapping[str, object] | None = None,
    editorial_master: EditorialMasterSelection | object | None = None,
) -> ArtifactSelection:
    """Validate a Director proposal and atomically publish its canonical marker."""

    root = _root(episode_root)
    cut_id = _safe_cut_id(cut_id)
    revision_id = _require_pending_revision(root, cut_id, revision_id)
    work = load_visual_work_packet(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        editorial_master=editorial_master,
    )
    path = _revision_dir(root, cut_id, revision_id) / DIRECTOR_PLAN_NAME
    proposal_snapshot, proposal_identity = _proposal_file_snapshot(
        root, proposal, canonical_path=path, label="Director proposal"
    )
    strict_execution = _require_trusted_execution_receipt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        phase="director",
        role="director",
        worker_identity=worker_identity,
        worker_proposal=proposal,
        execution_receipt=execution_receipt,
        expected_worker_proposal_identity=proposal_identity,
    )
    raw = _require_exact_keys(
        _accepted_worker_proposal_snapshot(strict_execution, proposal_snapshot),
        _DIRECTOR_PROPOSAL_KEYS,
        "Director proposal",
    )
    if (
        raw["contract"] != DIRECTOR_PLAN_CONTRACT
        or raw["episode_id"] != root.name
        or raw["cut_id"] != cut_id
    ):
        raise HighlightVisualContractError(
            "Director proposal belongs to another contract/episode/cut"
        )
    worker_execution = _trusted_worker_execution(worker_identity, expected_role="director")
    if raw["work_packet"] != work.identity():
        raise HighlightVisualContractError("Director proposal work packet lineage is stale")
    events, coverage = _director_events(raw["events"], work=work.document)
    _enforce_director_feedback(work=work.document, events=events)
    claimed_coverage = _require_exact_keys(raw["coverage"], _COVERAGE_KEYS, "coverage")
    if claimed_coverage != coverage:
        raise HighlightVisualContractError("Director coverage summary is not deterministic")
    document: dict[str, object] = {
        "contract": DIRECTOR_PLAN_CONTRACT,
        "episode_id": root.name,
        "cut_id": cut_id,
        "worker_execution": worker_execution,
        "work_packet": work.identity(),
        "events": events,
        "coverage": coverage,
    }
    document["content_hash"] = _content_hash(document)
    _atomic_publish(path, document)
    return load_director_plan(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        editorial_master=editorial_master,
    )


def _load_director_for_attempt(
    root: Path,
    *,
    cut_id: str,
    revision_id: str,
    attempt: int,
    editorial_master: EditorialMasterSelection | object | None,
) -> ArtifactSelection:
    work = load_visual_work_packet(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        editorial_master=editorial_master,
    )
    path = _director_path(root, cut_id, revision_id, attempt)
    if attempt > 1 and not path.is_file():
        return _load_director_for_attempt(
            root,
            cut_id=cut_id,
            revision_id=revision_id,
            attempt=attempt - 1,
            editorial_master=editorial_master,
        )
    selection = _load_hashed_artifact(
        root, path, contract=DIRECTOR_PLAN_CONTRACT, exact_keys=_DIRECTOR_KEYS
    )
    document = selection.document
    if document["episode_id"] != root.name or document["cut_id"] != cut_id:
        raise HighlightVisualContractError("Director plan belongs to another episode/cut")
    director_worker = _trusted_worker_execution(
        document["worker_execution"], expected_role="director"
    )
    _require_persisted_execution_receipt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        phase="director" if attempt == 1 else f"director_replan-{attempt:03d}",
        role="director",
        worker_identity=director_worker,  # type: ignore[arg-type]
    )
    if document["work_packet"] != work.identity():
        raise HighlightVisualContractError("Director plan work packet lineage is stale")
    events, coverage = _director_events(document["events"], work=work.document)
    _enforce_director_feedback(work=work.document, events=events)
    if document["events"] != events or document["coverage"] != coverage:
        raise HighlightVisualContractError("Director plan event/coverage validation drift")
    if attempt > 1:
        marker_path = _attempt_dir(root, cut_id, revision_id, attempt) / DIRECTOR_REPLAN_NAME
        marker = _load_hashed_artifact(
            root,
            marker_path,
            contract=DIRECTOR_REPLAN_CONTRACT,
            exact_keys=_DIRECTOR_REPLAN_KEYS,
        )
        prior = _load_director_for_attempt(
            root,
            cut_id=cut_id,
            revision_id=revision_id,
            attempt=attempt - 1,
            editorial_master=editorial_master,
        )
        decision = load_refinement_decision(
            root,
            cut_id=cut_id,
            revision_id=revision_id,
            attempt=attempt - 1,
            editorial_master=editorial_master,
        )
        if (
            marker.document["episode_id"] != root.name
            or marker.document["cut_id"] != cut_id
            or marker.document["attempt"] != attempt
            or marker.document["prior_director_plan"] != prior.identity()
            or marker.document["refinement_decision"] != decision.identity()
            or marker.document["director_plan"] != selection.identity()
        ):
            raise HighlightVisualContractError("Director replan lineage is stale")
    return selection


def load_director_plan(
    episode_root: str | Path,
    *,
    cut_id: str,
    revision_id: str | None = None,
    attempt: int = 1,
    editorial_master: EditorialMasterSelection | object | None = None,
) -> ArtifactSelection:
    """Freshly verify the accepted Director plan and its work-packet parent."""

    root = _root(episode_root)
    cut_id = _safe_cut_id(cut_id)
    selected_revision = revision_id or _active_revision_id(root, cut_id)
    return _load_director_for_attempt(
        root,
        cut_id=cut_id,
        revision_id=selected_revision,
        attempt=attempt,
        editorial_master=editorial_master,
    )


_DP_KEYS = {
    "contract",
    "episode_id",
    "cut_id",
    "worker_execution",
    "execution_receipt",
    "worker_proposal",
    "hydrated_proposal",
    "hydration_receipt",
    "director_plan",
    "implementations",
    "content_hash",
}
_DP_PROPOSAL_KEYS = _DP_KEYS - {
    "content_hash",
    "worker_execution",
    "execution_receipt",
    "worker_proposal",
    "hydrated_proposal",
    "hydration_receipt",
}
_DP_ATTEMPT_KEYS = {
    "contract",
    "episode_id",
    "cut_id",
    "attempt",
    "director_plan",
    "prior_dp_fulfillment",
    "refinement_decision",
    "dp_fulfillment",
    "content_hash",
}
_DIRECTOR_REPLAN_KEYS = {
    "contract",
    "episode_id",
    "cut_id",
    "attempt",
    "prior_director_plan",
    "refinement_decision",
    "director_plan",
    "content_hash",
}
_DP_ITEM_KEYS = {
    "event_id",
    "director_intent_sha256",
    "mode",
    "target_lane",
    "implementation_kind",
    "on_screen_text",
    "candidates",
    "selections",
    "semantic_justification",
}
_SELECTION_KEYS = {"candidate_id", "cue_ids", "t0", "t1", "quote", "source_range"}
_ASSET_CANDIDATE_KEYS = {
    "candidate_id",
    "visual_summary",
    "authority_asset_id",
    "media",
    "provenance",
}
_HF_CANDIDATE_KEYS = {
    "candidate_id",
    "visual_summary",
    "component",
    "render_params",
    "render_spec_sha256",
    "preview_media",
    "provenance",
}
_PROVENANCE_KEYS = {"kind", "provider", "source_url", "license", "receipt"}
_TARGET_LANES = {"broll_track2", "content_card_track4", "title_track3"}
_KINDS_BY_TARGET = {
    "broll_track2": {"stock_video", "photo"},
    "content_card_track4": {
        "sticker_pair",
        "concept_card",
        "chapter_label",
        "transition_title",
        "book_cover",
        "quote_card",
        "bigstat",
    },
    "title_track3": {"hero_title", "supporting_title"},
}
_KINDS_BY_FEEDBACK_LANE = {
    "b_roll": {"stock_video", "photo"},
    "hero_title": {"hero_title"},
    "title_card": {"supporting_title"},
    "fullscreen_transition": {"transition_title"},
    "visual_effect": {
        "sticker_pair",
        "concept_card",
        "chapter_label",
        "transition_title",
        "book_cover",
        "quote_card",
        "bigstat",
    },
}
_TARGET_BY_FEEDBACK_LANE = {
    "b_roll": "broll_track2",
    "hero_title": "title_track3",
    "title_card": "title_track3",
    "fullscreen_transition": "content_card_track4",
    "visual_effect": "content_card_track4",
}
_CHANGE_TYPE_KIND = {
    "video": "stock_video",
    "stock_video": "stock_video",
    "photo": "photo",
    "sticker": "sticker_pair",
    "sticker_pair": "sticker_pair",
    "concept": "concept_card",
    "concept_card": "concept_card",
    "chapter_label": "chapter_label",
    "transition": "transition_title",
    "transition_title": "transition_title",
    "book_cover": "book_cover",
    "quote_card": "quote_card",
    "bigstat": "bigstat",
}
_IMPLEMENTATION_KINDS = {
    "stock_video",
    "photo",
    "sticker_pair",
    "concept_card",
    "chapter_label",
    "transition_title",
    "book_cover",
    "quote_card",
    "bigstat",
    "hero_title",
    "supporting_title",
}
_MODES_BY_DIRECTOR_CATEGORY = {
    "stock_scene": {"stock", "hyperframes"},
    "self_archive": {"provided_asset", "hyperframes"},
    "screen_demo": {"provided_asset", "hyperframes"},
    "evidence_doc": {"provided_asset", "hyperframes"},
    "person_inset": {"provided_asset"},
    "keyword": {"hyperframes"},
    "quote": {"hyperframes"},
    "chapter": {"hyperframes"},
    "worked_example": {"hyperframes"},
    "book_cover": {"provided_asset", "hyperframes"},
    "self_promo": {"provided_asset", "hyperframes"},
    "kol_quote": {"provided_asset", "hyperframes"},
    "meme": {"provided_asset", "hyperframes"},
    "bigstat": {"hyperframes"},
}
_HF_COMPONENTS = {
    "sticker_pair",
    "concept_card",
    "chapter_label",
    "transition_title",
    "book_cover",
    "quote_card",
    "bigstat",
    "punch_card",
    "punch_card_wide",
}
_MEDIA_KEYS = {"path", "bytes", "sha256"}
_ASSET_AUTHORITY_KEYS = {
    "contract",
    "episode_id",
    "cut_id",
    "revision_id",
    "attempt",
    "worker_execution",
    "work_packet",
    "parent_refinement",
    "assets",
    "content_hash",
}
_ASSET_AUTHORITY_PROPOSAL_KEYS = _ASSET_AUTHORITY_KEYS - {
    "worker_execution",
    "content_hash",
}
_AUTHORITY_ASSET_KEYS = {
    "asset_id",
    "source_class",
    "provider",
    "provider_item_id",
    "source_url",
    "license",
    "acquired_at",
    "semantic_summary",
    "original_media",
    "acquisition_receipt",
}
_AUTHORITY_SOURCE_KEYS = _AUTHORITY_ASSET_KEYS - {"acquisition_receipt"}
_ASSET_ACQUISITION_PROPOSAL_KEYS = {
    "contract",
    "episode_id",
    "cut_id",
    "revision_id",
    "attempt",
    "assets",
}
_ACQUISITION_RECEIPT_KEYS = {
    "contract",
    "episode_id",
    "cut_id",
    "revision_id",
    "attempt",
    "asset_id",
    "source_class",
    "provider",
    "provider_item_id",
    "source_url",
    "license",
    "acquired_at",
    "original_media",
    "content_hash",
}
_SOURCE_CLASSES = {
    "licensed_stock",
    "official_archive",
    "public_domain",
    "provided_self_archive",
    "provided_screen_demo",
    "provided_evidence_doc",
    "provided_general",
}
_PEXELS_LICENSE = "Pexels license: https://www.pexels.com/license/"
_ENVATO_LICENSE = "Envato Elements license: https://elements.envato.com/license-terms"


def _validate_acquisition_provider_profile(
    *,
    asset_id: str,
    source_class: object,
    provider: str,
    provider_item_id: str,
    source_url: object,
    license_text: str,
) -> str:
    if source_class != "licensed_stock":
        if not isinstance(source_url, str) or not source_url.startswith(("https://", "http://")):
            raise HighlightVisualContractError(f"{asset_id}.source_url must be HTTP(S)")
        return source_url
    if provider == "pexels":
        if not re.fullmatch(r"[0-9]+", provider_item_id):
            raise HighlightVisualContractError(f"{asset_id} Pexels provider_item_id is invalid")
        expected_url = f"https://www.pexels.com/video/{provider_item_id}/"
        if source_url != expected_url or license_text != _PEXELS_LICENSE:
            raise HighlightVisualContractError(
                f"{asset_id} Pexels provider/item/URL/license profile mismatch"
            )
        return expected_url
    if provider == "envato-elements":
        if not re.fullmatch(r"[a-z0-9]+", provider_item_id):
            raise HighlightVisualContractError(f"{asset_id} Envato provider_item_id is invalid")
        if (
            not isinstance(source_url, str)
            or source_url != source_url.casefold()
            or re.fullmatch(
                rf"https://elements\.envato\.com/[a-z0-9-]+-{re.escape(provider_item_id)}",
                source_url,
            )
            is None
            or license_text != _ENVATO_LICENSE
        ):
            raise HighlightVisualContractError(
                f"{asset_id} Envato provider/item/URL/license profile mismatch"
            )
        return source_url
    raise HighlightVisualContractError(
        f"{asset_id} licensed_stock provider is not in the closed acquisition profile"
    )


def _validate_file_identity(
    root: Path,
    value: object,
    label: str,
    *,
    media: bool = False,
) -> tuple[Path, dict[str, object]]:
    identity = _require_exact_keys(value, _MEDIA_KEYS, label)
    raw_path = identity["path"]
    if not isinstance(raw_path, str) or Path(raw_path).is_absolute():
        raise HighlightVisualContractError(f"{label}.path must be episode-relative")
    path = (root / raw_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HighlightVisualContractError(f"{label} path escapes episode root or is missing")
    byte_count = identity["bytes"]
    digest = identity["sha256"]
    if (
        not isinstance(byte_count, int)
        or isinstance(byte_count, bool)
        or byte_count <= 0
        or byte_count != path.stat().st_size
    ):
        raise HighlightVisualContractError(f"{label} byte size drift")
    if (
        not isinstance(digest, str)
        or not _SHA256_RE.fullmatch(digest)
        or digest != _sha256_file(path)
    ):
        raise HighlightVisualContractError(f"{label} hash drift")
    if media:
        try:
            probe_stock_video(path)
        except BrollContractError as error:
            raise HighlightVisualContractError(
                f"{label} is not inspectable playable video: {error}"
            ) from error
    return path, dict(identity)


def _accepted_dp_hydration_lineage(
    root: Path,
    *,
    cut_id: str,
    revision_id: str,
    attempt: int,
    raw_proposal: Mapping[str, object] | str | Path,
    hydrated_proposal: Mapping[str, object] | str | Path,
    strict_execution: object,
    editorial_master: EditorialMasterSelection | object | None,
    raw_proposal_identity: Mapping[str, object] | None = None,
    hydrated_proposal_identity: Mapping[str, object] | None = None,
) -> tuple[
    dict[str, object],
    dict[str, object] | None,
    dict[str, object] | None,
]:
    """Freshly bind trusted hydration to the raw subscription-worker output."""

    if not strict_execution:
        return (
            {
                "worker_proposal": None,
                "hydrated_proposal": None,
                "hydration_receipt": None,
            },
            None,
            None,
        )
    if isinstance(raw_proposal, Mapping) or isinstance(hydrated_proposal, Mapping):
        raise HighlightVisualContractError(
            "trusted DP hydration requires raw and hydrated episode-local proposal files"
        )
    raw_path = Path(raw_proposal).resolve()
    hydrated_path = Path(hydrated_proposal).resolve()
    if raw_path == hydrated_path:
        raise HighlightVisualContractError(
            "trusted DP hydration must preserve raw and hydrated proposals separately"
        )
    try:
        raw_identity = (
            _require_exact_keys(
                raw_proposal_identity, _MEDIA_KEYS, "raw DP proposal snapshot"
            )
            if raw_proposal_identity is not None
            else _file_identity(root, raw_path)
        )
        hydrated_identity = (
            _require_exact_keys(
                hydrated_proposal_identity,
                _MEDIA_KEYS,
                "hydrated DP proposal snapshot",
            )
            if hydrated_proposal_identity is not None
            else _file_identity(root, hydrated_path)
        )
        receipt_identity = dp_hydration_receipt_identity(root, hydrated_path)
        verified = verify_dp_hydration_receipt(
            root,
            receipt_identity=receipt_identity,
            expected_cut_id=cut_id,
            expected_revision_id=revision_id,
            expected_attempt=attempt,
            expected_raw_proposal=raw_identity,
            expected_hydrated_proposal=hydrated_identity,
            editorial_master=editorial_master,
        )
    except (OSError, TrustedRenderError) as error:
        raise HighlightVisualContractError(
            f"trusted DP hydration receipt is invalid: {error}"
        ) from error
    verified_raw = verified.get("raw_proposal_document")
    verified_hydrated = verified.get("hydrated_proposal_document")
    if not isinstance(verified_raw, dict) or not isinstance(verified_hydrated, dict):
        raise HighlightVisualContractError(
            "trusted DP hydration verifier omitted exact proposal snapshots"
        )
    return (
        {
            "worker_proposal": raw_identity,
            "hydrated_proposal": hydrated_identity,
            "hydration_receipt": receipt_identity,
        },
        verified_raw,
        verified_hydrated,
    )


def _verify_canonical_dp_hydration(
    root: Path,
    *,
    cut_id: str,
    revision_id: str,
    attempt: int,
    document: Mapping[str, object],
    editorial_master: EditorialMasterSelection | object | None,
) -> None:
    execution_identity = document["execution_receipt"]
    worker_proposal_identity = document["worker_proposal"]
    if not isinstance(execution_identity, dict) or not isinstance(
        worker_proposal_identity, dict
    ):
        raise HighlightVisualContractError(
            "canonical DP fulfillment lacks trusted execution lineage"
        )
    worker_path_value = worker_proposal_identity.get("path")
    if not isinstance(worker_path_value, str):
        raise HighlightVisualContractError("canonical DP worker proposal identity is invalid")
    execution_lineage = _require_trusted_execution_receipt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        phase="dp" if attempt == 1 else f"dp-{attempt:03d}",
        role="dp",
        worker_identity=document["worker_execution"],  # type: ignore[arg-type]
        worker_proposal=root / worker_path_value,
        execution_receipt=execution_identity,
        expected_worker_proposal_identity=worker_proposal_identity,
    )
    if (
        execution_lineage["receipt"] != execution_identity
        or execution_lineage["proposal"] != worker_proposal_identity
    ):
        raise HighlightVisualContractError("canonical DP execution lineage drift")
    values = {
        key: document[key]
        for key in ("worker_proposal", "hydrated_proposal", "hydration_receipt")
    }
    if all(value is None for value in values.values()):
        raise HighlightVisualContractError("canonical DP fulfillment lacks hydration lineage")
    if any(not isinstance(value, dict) for value in values.values()):
        raise HighlightVisualContractError("canonical DP hydration lineage is incomplete")
    assert isinstance(values["worker_proposal"], dict)
    assert isinstance(values["hydrated_proposal"], dict)
    assert isinstance(values["hydration_receipt"], dict)
    try:
        verified = verify_dp_hydration_receipt(
            root,
            receipt_identity=values["hydration_receipt"],
            expected_cut_id=cut_id,
            expected_revision_id=revision_id,
            expected_attempt=attempt,
            expected_raw_proposal=values["worker_proposal"],
            expected_hydrated_proposal=values["hydrated_proposal"],
            editorial_master=editorial_master,
        )
    except (OSError, TrustedRenderError) as error:
        raise HighlightVisualContractError(
            f"canonical DP hydration lineage is stale: {error}"
        ) from error
    if verified.get("raw_proposal_document") != execution_lineage["proposal_document"]:
        raise HighlightVisualContractError(
            "canonical DP hydration raw proposal differs from trusted worker execution"
        )
    verified_hydrated = verified.get("hydrated_proposal_document")
    if not isinstance(verified_hydrated, dict):
        raise HighlightVisualContractError(
            "canonical DP hydration verifier omitted its exact proposal snapshot"
        )
    hydrated = _require_exact_keys(
        verified_hydrated,
        _DP_PROPOSAL_KEYS,
        "canonical hydrated DP proposal",
    )
    expected_hydrated = {
        "contract": document["contract"],
        "episode_id": document["episode_id"],
        "cut_id": document["cut_id"],
        "director_plan": document["director_plan"],
        "implementations": document["implementations"],
    }
    if hydrated != expected_hydrated:
        raise HighlightVisualContractError(
            "canonical DP fulfillment differs from its verified hydrated proposal"
        )


def _validate_provenance(
    root: Path,
    value: object,
    *,
    label: str,
    expected_kind: str,
) -> dict[str, object]:
    provenance = _require_exact_keys(value, _PROVENANCE_KEYS, label)
    if provenance["kind"] != expected_kind:
        raise HighlightVisualContractError(f"{label}.kind does not match implementation mode")
    provider = _nonempty_text(provenance["provider"], f"{label}.provider")
    license_text = _nonempty_text(provenance["license"], f"{label}.license")
    source_url = provenance["source_url"]
    if expected_kind == "stock_source":
        if not isinstance(source_url, str) or not source_url.startswith(("https://", "http://")):
            raise HighlightVisualContractError(f"{label}.source_url must be an HTTP(S) URL")
    elif source_url is not None and not isinstance(source_url, str):
        raise HighlightVisualContractError(f"{label}.source_url must be text or null")
    _, receipt = _validate_file_identity(root, provenance["receipt"], f"{label}.receipt")
    return {
        "kind": expected_kind,
        "provider": provider,
        "source_url": source_url,
        "license": license_text,
        "receipt": receipt,
    }


def _acquisition_receipt_path(
    root: Path,
    *,
    cut_id: str,
    revision_id: str,
    attempt: int,
    asset_id: str,
) -> Path:
    return (
        _attempt_dir(root, cut_id, revision_id, attempt)
        / "trusted-acquisitions"
        / _safe_cut_id(asset_id)
        / "ACQUISITION.json"
    )


def _normalized_authority_assets(
    root: Path,
    value: object,
    *,
    cut_id: str,
    revision_id: str,
    attempt: int,
) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise HighlightVisualContractError("asset authority assets must be an array")
    normalized: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    seen_original_sha256: set[str] = set()
    seen_source_items: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(value, 1):
        asset = _require_exact_keys(raw, _AUTHORITY_ASSET_KEYS, f"authority assets[{index}]")
        asset_id = _safe_cut_id(
            _nonempty_text(asset["asset_id"], f"authority assets[{index}].asset_id")
        )
        if asset_id in seen_ids:
            raise HighlightVisualContractError("asset authority asset_id is duplicated")
        seen_ids.add(asset_id)
        source_class = asset["source_class"]
        if source_class not in _SOURCE_CLASSES:
            raise HighlightVisualContractError(f"{asset_id} source_class is not allowed")
        provider = _nonempty_text(asset["provider"], f"{asset_id}.provider")
        provider_lower = provider.casefold()
        if any(
            token in provider_lower
            for token in ("imagegen", "midjourney", "hyperframes", "generated", "runway")
        ):
            raise HighlightVisualContractError(
                f"{asset_id} generated provider is not acquisition authority"
            )
        provider_item_id = _nonempty_text(asset["provider_item_id"], f"{asset_id}.provider_item_id")
        license_text = _nonempty_text(asset["license"], f"{asset_id}.license")
        source_url = _validate_acquisition_provider_profile(
            asset_id=asset_id,
            source_class=source_class,
            provider=provider,
            provider_item_id=provider_item_id,
            source_url=asset["source_url"],
            license_text=license_text,
        )
        acquired_at = asset["acquired_at"]
        if not isinstance(acquired_at, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", acquired_at
        ):
            raise HighlightVisualContractError(f"{asset_id}.acquired_at must be UTC seconds")
        semantic_summary = _nonempty_text(
            asset["semantic_summary"], f"{asset_id}.semantic_summary", minimum=12
        )
        _, original_media = _validate_file_identity(
            root, asset["original_media"], f"{asset_id}.original_media", media=True
        )
        original_sha256 = str(original_media["sha256"])
        source_item = (provider.casefold(), provider_item_id.casefold(), source_url.casefold())
        if original_sha256 in seen_original_sha256 or source_item in seen_source_items:
            raise HighlightVisualContractError(
                "asset authority original bytes and provider item must be unique per attempt"
            )
        seen_original_sha256.add(original_sha256)
        seen_source_items.add(source_item)
        receipt_path, receipt_identity = _validate_file_identity(
            root, asset["acquisition_receipt"], f"{asset_id}.acquisition_receipt"
        )
        expected_receipt_path = _acquisition_receipt_path(
            root,
            cut_id=cut_id,
            revision_id=revision_id,
            attempt=attempt,
            asset_id=asset_id,
        ).resolve()
        if receipt_path != expected_receipt_path:
            raise HighlightVisualContractError(
                f"{asset_id} acquisition receipt is outside trusted canonical path"
            )
        receipt = _require_exact_keys(
            _load_json_object(receipt_path, f"{asset_id} acquisition receipt"),
            _ACQUISITION_RECEIPT_KEYS,
            f"{asset_id} acquisition receipt",
        )
        claimed_hash = receipt["content_hash"]
        if not isinstance(claimed_hash, str) or claimed_hash != _content_hash(
            {key: item for key, item in receipt.items() if key != "content_hash"}
        ):
            raise HighlightVisualContractError(f"{asset_id} acquisition receipt hash drift")
        expected_receipt = {
            "contract": ACQUISITION_RECEIPT_CONTRACT,
            "episode_id": root.name,
            "cut_id": cut_id,
            "revision_id": revision_id,
            "attempt": attempt,
            "asset_id": asset_id,
            "source_class": source_class,
            "provider": provider,
            "provider_item_id": provider_item_id,
            "source_url": source_url,
            "license": license_text,
            "acquired_at": acquired_at,
            "original_media": original_media,
        }
        if {key: receipt[key] for key in expected_receipt} != expected_receipt:
            raise HighlightVisualContractError(f"{asset_id} acquisition receipt metadata drift")
        normalized.append(
            {
                "asset_id": asset_id,
                "source_class": source_class,
                "provider": provider,
                "provider_item_id": provider_item_id,
                "source_url": source_url,
                "license": license_text,
                "acquired_at": acquired_at,
                "semantic_summary": semantic_summary,
                "original_media": original_media,
                "acquisition_receipt": receipt_identity,
            }
        )
    return normalized


def _asset_acquisition_manifest_sources(
    value: Mapping[str, object] | str | Path,
    *,
    root: Path,
    cut_id: str,
    revision_id: str,
    attempt: int,
) -> list[dict[str, object]]:
    if isinstance(value, Mapping):
        raise HighlightVisualContractError(
            "asset acquisition requires an episode-local manifest file"
        )
    manifest_path = Path(value).resolve()
    if not manifest_path.is_relative_to(root) or not manifest_path.is_file():
        raise HighlightVisualContractError("asset acquisition manifest escapes episode root")
    manifest_snapshot, _ = _proposal_file_snapshot(
        root,
        manifest_path,
        canonical_path=_attempt_dir(root, cut_id, revision_id, attempt) / ASSET_AUTHORITY_NAME,
        label="asset acquisition manifest",
    )
    return _asset_acquisition_manifest_sources_document(
        manifest_snapshot,
        root=root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt,
    )


def _asset_acquisition_manifest_sources_document(
    value: Mapping[str, object],
    *,
    root: Path,
    cut_id: str,
    revision_id: str,
    attempt: int,
) -> list[dict[str, object]]:
    manifest = _require_exact_keys(
        value,
        _ASSET_ACQUISITION_PROPOSAL_KEYS,
        "asset acquisition manifest",
    )
    if (
        manifest["contract"] != ASSET_ACQUISITION_PROPOSAL_CONTRACT
        or manifest["episode_id"] != root.name
        or manifest["cut_id"] != cut_id
        or manifest["revision_id"] != revision_id
        or manifest["attempt"] != attempt
    ):
        raise HighlightVisualContractError("asset acquisition manifest lineage is stale")
    if not isinstance(manifest["assets"], list):
        raise HighlightVisualContractError("asset acquisition manifest assets must be an array")
    return [
        _require_exact_keys(
            row,
            _AUTHORITY_SOURCE_KEYS,
            f"asset acquisition manifest assets[{index}]",
        )
        for index, row in enumerate(manifest["assets"], 1)
    ]


def _verify_canonical_asset_execution(
    root: Path,
    *,
    cut_id: str,
    revision_id: str,
    attempt: int,
    document: Mapping[str, object],
) -> None:
    """Freshly bind canonical authority rows to the trusted acquisition execution."""

    worker = _trusted_worker_execution(
        document["worker_execution"], expected_role="asset_acquisition"  # type: ignore[arg-type]
    )
    manifest_path = _require_persisted_execution_receipt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        phase=f"asset_acquisition-{attempt:03d}",
        role="asset_acquisition",
        worker_identity=worker,  # type: ignore[arg-type]
    )
    manifest_assets = _asset_acquisition_manifest_sources(
        manifest_path,
        root=root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt,
    )
    authority_assets = document["assets"]
    if not isinstance(authority_assets, list):
        raise HighlightVisualContractError("canonical asset authority assets are invalid")
    projected = [
        {
            key: row[key]
            for key in _AUTHORITY_SOURCE_KEYS
        }
        for row in authority_assets
        if isinstance(row, dict)
    ]
    if len(projected) != len(authority_assets) or projected != manifest_assets:
        raise HighlightVisualContractError(
            "canonical asset authority differs from trusted acquisition execution"
        )


def accept_asset_authority(
    episode_root: str | Path,
    *,
    cut_id: str,
    revision_id: str,
    attempt: int,
    proposal: Mapping[str, object] | str | Path,
    worker_identity: Mapping[str, str],
    acquisition_manifest: Mapping[str, object] | str | Path | None = None,
    execution_receipt: Mapping[str, object] | None = None,
    editorial_master: EditorialMasterSelection | object | None = None,
) -> ArtifactSelection:
    """Accept trusted acquired originals; creative DP proposals cannot self-author this."""

    root = _root(episode_root)
    cut_id = _safe_cut_id(cut_id)
    revision_id = _require_pending_revision(root, cut_id, revision_id)
    work = load_visual_work_packet(
        root, cut_id=cut_id, revision_id=revision_id, editorial_master=editorial_master
    )
    parent_refinement: object = None
    if attempt > 1:
        parent_refinement = load_refinement_decision(
            root,
            cut_id=cut_id,
            revision_id=revision_id,
            attempt=attempt - 1,
            editorial_master=editorial_master,
        ).identity()
    path = _attempt_dir(root, cut_id, revision_id, attempt) / ASSET_AUTHORITY_NAME
    proposal_snapshot, _ = _proposal_file_snapshot(
        root, proposal, canonical_path=path, label="asset authority proposal"
    )
    manifest_snapshot: dict[str, object] | None = None
    manifest_identity: dict[str, object] | None = None
    if acquisition_manifest is not None:
        manifest_snapshot, manifest_identity = _proposal_file_snapshot(
            root,
            acquisition_manifest,
            canonical_path=path,
            label="asset acquisition manifest",
        )
    strict_execution = _require_trusted_execution_receipt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        phase=f"asset_acquisition-{attempt:03d}",
        role="asset_acquisition",
        worker_identity=worker_identity,
        worker_proposal=(
            acquisition_manifest if acquisition_manifest is not None else proposal
        ),
        execution_receipt=execution_receipt,
        expected_worker_proposal_identity=manifest_identity,
    )
    raw = _require_exact_keys(
        proposal_snapshot,
        _ASSET_AUTHORITY_PROPOSAL_KEYS,
        "asset authority proposal",
    )
    if (
        raw["contract"] != ASSET_AUTHORITY_CONTRACT
        or raw["episode_id"] != root.name
        or raw["cut_id"] != cut_id
        or raw["revision_id"] != revision_id
        or raw["attempt"] != attempt
        or raw["work_packet"] != work.identity()
        or raw["parent_refinement"] != parent_refinement
    ):
        raise HighlightVisualContractError("asset authority lineage is stale")
    worker_execution = _trusted_worker_execution(
        worker_identity, expected_role="asset_acquisition"
    )
    assets = _normalized_authority_assets(
        root,
        raw["assets"],
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt,
    )
    if strict_execution:
        if manifest_snapshot is None:
            raise HighlightVisualContractError("trusted acquisition manifest snapshot is missing")
        manifest_document = _accepted_worker_proposal_snapshot(
            strict_execution, manifest_snapshot
        )
        manifest_assets = _asset_acquisition_manifest_sources_document(
            manifest_document,
            root=root,
            cut_id=cut_id,
            revision_id=revision_id,
            attempt=attempt,
        )
        projected_assets = [
            {key: row[key] for key in _AUTHORITY_SOURCE_KEYS} for row in assets
        ]
        if projected_assets != manifest_assets:
            raise HighlightVisualContractError(
                "asset authority differs from trusted acquisition manifest"
            )
    _reject_human_removed_assets(root, work.document, assets)
    prior_assets = _effective_authority_assets(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        through_attempt=attempt - 1,
        editorial_master=editorial_master,
        work=work,
        require_any=False,
    )
    _validate_authority_extension(prior_assets, assets)
    document: dict[str, object] = {
        "contract": ASSET_AUTHORITY_CONTRACT,
        "episode_id": root.name,
        "cut_id": cut_id,
        "revision_id": revision_id,
        "attempt": attempt,
        "worker_execution": worker_execution,
        "work_packet": work.identity(),
        "parent_refinement": parent_refinement,
        "assets": assets,
    }
    document["content_hash"] = _content_hash(document)
    _atomic_publish(path, document)
    return load_asset_authority(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt,
        editorial_master=editorial_master,
    )


def _preflight_authority_sources(
    root: Path,
    value: Sequence[Mapping[str, object]],
    *,
    cut_id: str,
    revision_id: str,
    attempt: int,
) -> list[tuple[Path, dict[str, object], dict[str, object]]]:
    """Validate the complete trusted batch without creating canonical artifacts."""

    planned: list[tuple[Path, dict[str, object], dict[str, object]]] = []
    seen_ids: set[str] = set()
    seen_original_sha256: set[str] = set()
    seen_source_items: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(value, 1):
        source = _require_exact_keys(
            raw, _AUTHORITY_SOURCE_KEYS, f"trusted acquisition assets[{index}]"
        )
        asset_id = _safe_cut_id(
            _nonempty_text(source["asset_id"], f"trusted acquisition assets[{index}].asset_id")
        )
        if asset_id in seen_ids:
            raise HighlightVisualContractError("asset authority asset_id is duplicated")
        seen_ids.add(asset_id)
        source_class = source["source_class"]
        if source_class not in _SOURCE_CLASSES:
            raise HighlightVisualContractError(f"{asset_id} source_class is not allowed")
        provider = _nonempty_text(source["provider"], f"{asset_id}.provider")
        if any(
            token in provider.casefold()
            for token in ("imagegen", "midjourney", "hyperframes", "generated", "runway")
        ):
            raise HighlightVisualContractError(
                f"{asset_id} generated provider is not acquisition authority"
            )
        provider_item_id = _nonempty_text(
            source["provider_item_id"], f"{asset_id}.provider_item_id"
        )
        license_text = _nonempty_text(source["license"], f"{asset_id}.license")
        source_url = _validate_acquisition_provider_profile(
            asset_id=asset_id,
            source_class=source_class,
            provider=provider,
            provider_item_id=provider_item_id,
            source_url=source["source_url"],
            license_text=license_text,
        )
        acquired_at = source["acquired_at"]
        if not isinstance(acquired_at, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", acquired_at
        ):
            raise HighlightVisualContractError(f"{asset_id}.acquired_at must be UTC seconds")
        semantic_summary = _nonempty_text(
            source["semantic_summary"], f"{asset_id}.semantic_summary", minimum=12
        )
        _, original_media = _validate_file_identity(
            root, source["original_media"], f"{asset_id}.original_media", media=True
        )
        original_sha256 = str(original_media["sha256"])
        source_item = (provider.casefold(), provider_item_id.casefold(), source_url.casefold())
        if original_sha256 in seen_original_sha256 or source_item in seen_source_items:
            raise HighlightVisualContractError(
                "asset authority original bytes and provider item must be unique per attempt"
            )
        seen_original_sha256.add(original_sha256)
        seen_source_items.add(source_item)
        receipt_path = _acquisition_receipt_path(
            root,
            cut_id=cut_id,
            revision_id=revision_id,
            attempt=attempt,
            asset_id=asset_id,
        )
        receipt: dict[str, object] = {
            "contract": ACQUISITION_RECEIPT_CONTRACT,
            "episode_id": root.name,
            "cut_id": cut_id,
            "revision_id": revision_id,
            "attempt": attempt,
            "asset_id": asset_id,
            "source_class": source_class,
            "provider": provider,
            "provider_item_id": provider_item_id,
            "source_url": source_url,
            "license": license_text,
            "acquired_at": acquired_at,
            "original_media": original_media,
        }
        receipt["content_hash"] = _content_hash(receipt)
        receipt_payload = _pretty_json(receipt)
        if receipt_path.exists() and receipt_path.read_bytes() != receipt_payload:
            raise HighlightVisualArtifactConflictError(
                f"immutable canonical artifact already differs: {receipt_path.name}"
            )
        receipt_identity = {
            "path": receipt_path.resolve().relative_to(root).as_posix(),
            "bytes": len(receipt_payload),
            "sha256": _sha256_bytes(receipt_payload),
        }
        row = {
            "asset_id": asset_id,
            "source_class": source_class,
            "provider": provider,
            "provider_item_id": provider_item_id,
            "source_url": source_url,
            "license": license_text,
            "acquired_at": acquired_at,
            "semantic_summary": semantic_summary,
            "original_media": original_media,
            "acquisition_receipt": receipt_identity,
        }
        planned.append((receipt_path, receipt, row))
    return planned


def publish_asset_authority(
    episode_root: str | Path,
    *,
    cut_id: str,
    revision_id: str,
    attempt: int,
    assets: Sequence[Mapping[str, object]],
    worker_identity: Mapping[str, str],
    acquisition_manifest: Mapping[str, object] | str | Path | None = None,
    execution_receipt: Mapping[str, object] | None = None,
    editorial_master: EditorialMasterSelection | object | None = None,
) -> ArtifactSelection:
    """Trusted writer: publish canonical receipts first, authority marker last."""

    root = _root(episode_root)
    cut_id = _safe_cut_id(cut_id)
    revision_id = _require_pending_revision(root, cut_id, revision_id)
    path = _attempt_dir(root, cut_id, revision_id, attempt) / ASSET_AUTHORITY_NAME
    manifest_snapshot: dict[str, object] | None = None
    manifest_identity: dict[str, object] | None = None
    if acquisition_manifest is not None:
        manifest_snapshot, manifest_identity = _proposal_file_snapshot(
            root,
            acquisition_manifest,
            canonical_path=path,
            label="asset acquisition manifest",
        )
    strict_execution = _require_trusted_execution_receipt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        phase=f"asset_acquisition-{attempt:03d}",
        role="asset_acquisition",
        worker_identity=worker_identity,
        worker_proposal=(
            acquisition_manifest
            if acquisition_manifest is not None
            else {"assets": list(assets)}
        ),
        execution_receipt=execution_receipt,
        expected_worker_proposal_identity=manifest_identity,
    )
    if strict_execution:
        if manifest_snapshot is None:
            raise HighlightVisualContractError("trusted acquisition manifest snapshot is missing")
        manifest_document = _accepted_worker_proposal_snapshot(
            strict_execution, manifest_snapshot
        )
        manifest_assets = _asset_acquisition_manifest_sources_document(
            manifest_document,
            root=root,
            cut_id=cut_id,
            revision_id=revision_id,
            attempt=attempt,
        )
        if [dict(row) for row in assets] != manifest_assets:
            raise HighlightVisualContractError(
                "trusted acquisition inputs differ from execution-bound manifest"
            )
    work = load_visual_work_packet(
        root, cut_id=cut_id, revision_id=revision_id, editorial_master=editorial_master
    )
    parent_refinement: object = None
    if attempt > 1:
        parent_refinement = load_refinement_decision(
            root,
            cut_id=cut_id,
            revision_id=revision_id,
            attempt=attempt - 1,
            editorial_master=editorial_master,
        ).identity()
    planned = _preflight_authority_sources(
        root,
        assets,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt,
    )
    planned_rows = [row for _, _, row in planned]
    _reject_human_removed_assets(root, work.document, planned_rows)
    prior_assets = _effective_authority_assets(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        through_attempt=attempt - 1,
        editorial_master=editorial_master,
        work=work,
        require_any=False,
    )
    _validate_authority_extension(prior_assets, planned_rows)
    rows: list[dict[str, object]] = []
    for receipt_path, receipt, row in planned:
        _atomic_publish(receipt_path, receipt)
        if row["acquisition_receipt"] != _file_identity(root, receipt_path):
            raise HighlightVisualContractError("acquisition receipt publish identity drift")
        rows.append(row)
    document: dict[str, object] = {
        "contract": ASSET_AUTHORITY_CONTRACT,
        "episode_id": root.name,
        "cut_id": cut_id,
        "revision_id": revision_id,
        "attempt": attempt,
        "worker_execution": _trusted_worker_execution(
            worker_identity, expected_role="asset_acquisition"
        ),
        "work_packet": work.identity(),
        "parent_refinement": parent_refinement,
        "assets": rows,
    }
    document["content_hash"] = _content_hash(document)
    _atomic_publish(path, document)
    return load_asset_authority(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt,
        editorial_master=editorial_master,
    )


def load_asset_authority(
    episode_root: str | Path,
    *,
    cut_id: str,
    revision_id: str,
    attempt: int,
    editorial_master: EditorialMasterSelection | object | None = None,
    _work: ArtifactSelection | None = None,
) -> ArtifactSelection:
    """Freshly verify one attempt's acquisition authority and every original byte."""

    root = _root(episode_root)
    cut_id = _safe_cut_id(cut_id)
    revision_id = _safe_revision_id(revision_id)
    work = _work or load_visual_work_packet(
        root, cut_id=cut_id, revision_id=revision_id, editorial_master=editorial_master
    )
    expected_parent: object = None
    if attempt > 1:
        expected_parent = load_refinement_decision(
            root,
            cut_id=cut_id,
            revision_id=revision_id,
            attempt=attempt - 1,
            editorial_master=editorial_master,
        ).identity()
    path = _attempt_dir(root, cut_id, revision_id, attempt) / ASSET_AUTHORITY_NAME
    selection = _load_hashed_artifact(
        root, path, contract=ASSET_AUTHORITY_CONTRACT, exact_keys=_ASSET_AUTHORITY_KEYS
    )
    document = selection.document
    _trusted_worker_execution(document["worker_execution"], expected_role="asset_acquisition")
    if (
        document["episode_id"] != root.name
        or document["cut_id"] != cut_id
        or document["revision_id"] != revision_id
        or document["attempt"] != attempt
        or document["work_packet"] != work.identity()
        or document["parent_refinement"] != expected_parent
    ):
        raise HighlightVisualContractError("asset authority parent lineage is stale")
    _verify_canonical_asset_execution(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt,
        document=document,
    )
    assets = _normalized_authority_assets(
        root,
        document["assets"],
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt,
    )
    if document["assets"] != assets:
        raise HighlightVisualContractError("asset authority normalized content drift")
    return selection


def _validate_authority_extension(
    prior_assets: Sequence[Mapping[str, object]],
    new_assets: Sequence[Mapping[str, object]],
) -> None:
    prior_ids = {str(row["asset_id"]) for row in prior_assets}
    prior_sha256 = {str(row["original_media"]["sha256"]) for row in prior_assets}  # type: ignore[index]
    prior_items = {
        (str(row["provider"]).casefold(), str(row["provider_item_id"]).casefold())
        for row in prior_assets
    }
    prior_urls = {str(row["source_url"]).casefold() for row in prior_assets}
    for row in new_assets:
        asset_id = str(row["asset_id"])
        original_sha256 = str(row["original_media"]["sha256"])  # type: ignore[index]
        provider_item = (
            str(row["provider"]).casefold(),
            str(row["provider_item_id"]).casefold(),
        )
        source_url = str(row["source_url"]).casefold()
        if (
            asset_id in prior_ids
            or original_sha256 in prior_sha256
            or provider_item in prior_items
            or source_url in prior_urls
        ):
            raise HighlightVisualContractError(
                "asset authority reuses original source across attempts"
            )


def _effective_authority_assets(
    root: Path,
    *,
    cut_id: str,
    revision_id: str,
    through_attempt: int,
    editorial_master: EditorialMasterSelection | object | None,
    work: ArtifactSelection,
    require_any: bool = True,
) -> list[dict[str, object]]:
    assets: list[dict[str, object]] = []
    for authority_attempt in range(1, through_attempt + 1):
        path = _attempt_dir(root, cut_id, revision_id, authority_attempt) / ASSET_AUTHORITY_NAME
        if not path.is_file():
            continue
        authority = load_asset_authority(
            root,
            cut_id=cut_id,
            revision_id=revision_id,
            attempt=authority_attempt,
            editorial_master=editorial_master,
            _work=work,
        )
        current_rows = [
            dict(row) for row in authority.document["assets"] if isinstance(row, dict)
        ]
        _validate_authority_extension(assets, current_rows)
        assets.extend(current_rows)
    if require_any and not assets:
        raise HighlightVisualContractError("DP requires trusted acquisition authority")
    return assets


def load_asset_authority_projection(
    episode_root: str | Path,
    *,
    cut_id: str,
    revision_id: str,
    attempt: int,
    editorial_master: EditorialMasterSelection | object | None = None,
) -> dict[str, object]:
    """Return a read-only, hash-bound DP phase inventory with no acquisition secrets."""

    root = _root(episode_root)
    work = load_visual_work_packet(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        editorial_master=editorial_master,
    )
    assets = _effective_authority_assets(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        through_attempt=attempt,
        editorial_master=editorial_master,
        work=work,
    )
    identities = []
    for authority_attempt in range(1, attempt + 1):
        path = _attempt_dir(root, cut_id, revision_id, authority_attempt) / ASSET_AUTHORITY_NAME
        if path.is_file():
            identities.append(
                load_asset_authority(
                    root,
                    cut_id=cut_id,
                    revision_id=revision_id,
                    attempt=authority_attempt,
                    editorial_master=editorial_master,
                    _work=work,
                ).identity()
            )
    return {
        "identity": identities[-1],
        "authority_chain": identities,
        "attempt": attempt,
        "assets": assets,
    }


def _selected_cue_claim(
    raw: object,
    *,
    event: Mapping[str, object],
    cues: Sequence[Mapping[str, object]],
    allow_cue_subrange: bool,
    label: str,
) -> dict[str, object]:
    selection = _require_exact_keys(raw, _SELECTION_KEYS, label)
    candidate_id = _nonempty_text(selection["candidate_id"], f"{label}.candidate_id")
    cue_ids = selection["cue_ids"]
    if (
        not isinstance(cue_ids, list)
        or not cue_ids
        or any(not isinstance(item, int) or isinstance(item, bool) for item in cue_ids)
    ):
        raise HighlightVisualContractError(f"{label}.cue_ids are invalid")
    event_cues = event["cue_ids"]
    if not isinstance(event_cues, list):
        raise HighlightVisualContractError("Director event cue_ids are invalid")
    positions: list[int] = []
    cue_by_id = {int(cue["number"]): cue for cue in cues}
    try:
        positions = [event_cues.index(item) for item in cue_ids]
        selected_cues = [cue_by_id[item] for item in cue_ids]
    except (KeyError, ValueError) as error:
        raise HighlightVisualContractError(
            f"{label} does not bind a cue subset of its Director event"
        ) from error
    if positions != list(range(positions[0], positions[0] + len(positions))):
        raise HighlightVisualContractError(f"{label}.cue_ids must be contiguous")
    t0 = _number(selection["t0"], f"{label}.t0")
    t1 = _number(selection["t1"], f"{label}.t1")
    if t1 <= t0:
        raise HighlightVisualContractError(f"{label} timeline range is invalid")
    if allow_cue_subrange:
        overlapping = [
            cue
            for cue in selected_cues
            if float(cue["start_sec"]) < t1 and float(cue["end_sec"]) > t0
        ]
        overlapping_ids = [int(cue["number"]) for cue in overlapping]
        if (
            overlapping_ids != cue_ids
            or t0 < float(selected_cues[0]["start_sec"])
            or t1 > float(selected_cues[-1]["end_sec"])
        ):
            raise HighlightVisualContractError(
                f"{label} timeline subrange must cite exactly every overlapping cue"
            )
    elif t0 != float(selected_cues[0]["start_sec"]) or t1 != float(selected_cues[-1]["end_sec"]):
        raise HighlightVisualContractError(f"{label} must bind exact cue boundaries")
    quote = "\n".join(str(cue["text"]) for cue in selected_cues)
    if selection["quote"] != quote:
        raise HighlightVisualContractError(
            f"{label} quote must be newline-joined exact cited cue text"
        )
    if t0 < float(event["t0"]) or t1 > float(event["t1"]):
        raise HighlightVisualContractError(f"{label} escapes its Director event range")
    source_range = _require_exact_keys(
        selection["source_range"], {"start_sec", "end_sec"}, f"{label}.source_range"
    )
    source_start = _number(source_range["start_sec"], f"{label}.source_range.start_sec")
    source_end = _number(source_range["end_sec"], f"{label}.source_range.end_sec")
    if (
        source_start < 0
        or source_end <= source_start
        or abs((source_end - source_start) - (t1 - t0)) > 0.001
    ):
        raise HighlightVisualContractError(
            f"{label}.source_range must match exact timeline display duration"
        )
    return {
        "candidate_id": candidate_id,
        "cue_ids": list(cue_ids),
        "t0": t0,
        "t1": t1,
        "quote": quote,
        "source_range": {"start_sec": source_start, "end_sec": source_end},
    }


def _dp_implementations(
    root: Path,
    value: object,
    *,
    director: ArtifactSelection,
    work: ArtifactSelection,
    attempt: int = 1,
    editorial_master: EditorialMasterSelection | object | None = None,
) -> tuple[list[dict[str, object]], tuple[dict[str, object], ...]]:
    if not isinstance(value, list):
        raise HighlightVisualContractError("DP implementations must be an array")
    director_events = {
        event["event_id"]: event
        for event in director.document["events"]
        if isinstance(event, dict) and event.get("decision") == "add_visual"
    }
    if len(value) != len(director_events):
        raise HighlightVisualContractError(
            "DP must implement every add_visual Director event exactly once"
        )
    cues = work.document["cues"]
    if not isinstance(cues, list):
        raise HighlightVisualContractError("work packet cues are invalid")
    normalized_items: list[dict[str, object]] = []
    materializations: list[dict[str, object]] = []
    seen_events: set[str] = set()
    selected_source_media_sha256: set[str] = set()
    needs_asset_authority = any(
        isinstance(row, dict) and row.get("mode") in {"stock", "provided_asset"}
        for row in value
    )
    authority_by_id: dict[str, dict[str, object]] = {}
    if needs_asset_authority:
        authority_assets = _effective_authority_assets(
            root,
            cut_id=str(work.document["cut_id"]),
            revision_id=str(work.document["revision_id"]),
            through_attempt=attempt,
            editorial_master=editorial_master,
            work=work,
        )
        authority_by_id = {
            str(row["asset_id"]): row
            for row in authority_assets
        }
    feedback_by_event = {
        str(directive["component_id"]): directive
        for directive in _visual_feedback_directives(work.document)
        if directive["action"] != "remove"
    }
    for item_index, raw in enumerate(value, 1):
        item = _require_exact_keys(raw, _DP_ITEM_KEYS, f"implementations[{item_index}]")
        event_id = item["event_id"]
        if (
            not isinstance(event_id, str)
            or event_id not in director_events
            or event_id in seen_events
        ):
            raise HighlightVisualContractError(
                "DP event coverage is missing, duplicated, or unknown"
            )
        seen_events.add(event_id)
        event = director_events[event_id]
        intent_hash = _content_hash(event)
        if item["director_intent_sha256"] != intent_hash:
            raise HighlightVisualContractError(f"{event_id} DP changed Director intent")
        mode = item["mode"]
        target_lane = item["target_lane"]
        implementation_kind = item["implementation_kind"]
        if mode not in {"stock", "provided_asset", "hyperframes"}:
            raise HighlightVisualContractError(f"{event_id}.mode is not allowed")
        allowed_modes = _MODES_BY_DIRECTOR_CATEGORY.get(str(event["category"]), set())
        if mode not in allowed_modes:
            raise HighlightVisualContractError(
                f"{event_id} Director category={event['category']} "
                f"cannot be fulfilled by mode={mode}"
            )
        if target_lane not in _TARGET_LANES or implementation_kind not in _IMPLEMENTATION_KINDS:
            raise HighlightVisualContractError(f"{event_id} target/kind is not allowed")
        if implementation_kind not in _KINDS_BY_TARGET[str(target_lane)]:
            raise HighlightVisualContractError(
                f"{event_id} implementation_kind is not valid for target_lane"
            )
        feedback = feedback_by_event.get(event_id)
        if feedback is not None:
            feedback_lane = str(feedback["lane"])
            if (
                target_lane != _TARGET_BY_FEEDBACK_LANE[feedback_lane]
                or implementation_kind not in _KINDS_BY_FEEDBACK_LANE[feedback_lane]
            ):
                raise HighlightVisualContractError(
                    f"{event_id} DP target/kind violates requested component lane"
                )
            if feedback["action"] == "change_type":
                expected_kind = _CHANGE_TYPE_KIND.get(str(feedback["replacement"]))
                if expected_kind is None or implementation_kind != expected_kind:
                    raise HighlightVisualContractError(
                        f"{event_id} DP type differs from requested change_type"
                    )
        if mode == "stock" and (
            target_lane != "broll_track2" or implementation_kind != "stock_video"
        ):
            raise HighlightVisualContractError("stock must materialize as stock_video on track 2")
        if mode == "provided_asset" and implementation_kind not in {"photo", "stock_video"}:
            raise HighlightVisualContractError("provided_asset kind is not allowed")
        if mode == "hyperframes" and target_lane == "broll_track2":
            raise HighlightVisualContractError("HyperFrames content must use card/title lane")
        director_text = event["on_screen_text"]
        implementation_text = item["on_screen_text"]
        if director_text is not None and implementation_text != director_text:
            raise HighlightVisualContractError(f"{event_id} changed exact on_screen_text")
        if director_text is None:
            if mode == "hyperframes":
                _nonempty_text(
                    implementation_text,
                    f"{event_id}.on_screen_text HyperFrames fallback",
                )
            elif implementation_text is not None:
                raise HighlightVisualContractError(
                    f"{event_id} added on_screen_text outside HyperFrames fallback"
                )
        justification = _nonempty_text(
            item["semantic_justification"],
            f"{event_id}.semantic_justification",
            minimum=12,
        )
        raw_candidates = item["candidates"]
        if not isinstance(raw_candidates, list) or not raw_candidates:
            raise HighlightVisualContractError(f"{event_id} requires concrete candidates")
        if mode == "stock" and len(raw_candidates) < 3:
            raise HighlightVisualContractError(f"{event_id} stock requires A/B/C candidates")
        candidates: list[dict[str, object]] = []
        candidate_by_id: dict[str, dict[str, object]] = {}
        candidate_media_sha256: set[str] = set()
        candidate_source_urls: set[str] = set()
        for candidate_index, candidate_raw in enumerate(raw_candidates, 1):
            label = f"{event_id}.candidates[{candidate_index}]"
            if mode == "hyperframes":
                candidate = _require_exact_keys(candidate_raw, _HF_CANDIDATE_KEYS, label)
            else:
                candidate = _require_exact_keys(candidate_raw, _ASSET_CANDIDATE_KEYS, label)
            candidate_id = _nonempty_text(candidate["candidate_id"], f"{label}.candidate_id")
            if candidate_id in candidate_by_id:
                raise HighlightVisualContractError(f"{event_id} candidate_id is duplicated")
            summary = _nonempty_text(candidate["visual_summary"], f"{label}.visual_summary")
            if mode == "hyperframes":
                component = candidate["component"]
                params = candidate["render_params"]
                if component not in _HF_COMPONENTS or not isinstance(params, dict) or not params:
                    raise HighlightVisualContractError(f"{label} render spec is not allowed")
                if implementation_kind in {"hero_title", "supporting_title"}:
                    if component not in {"punch_card", "punch_card_wide"}:
                        raise HighlightVisualContractError(
                            f"{label} title must use punch_card composition"
                        )
                    title_params = _require_exact_keys(
                        params,
                        {"text", "tier", "style", "show_sec", "pos_y"},
                        f"{label}.render_params",
                    )
                    expected_tier = 1 if implementation_kind == "hero_title" else 2
                    if (
                        title_params["text"] != event["on_screen_text"]
                        or title_params["tier"] != expected_tier
                        or _number(title_params["show_sec"], f"{label}.show_sec")
                        != float(event["t1"]) - float(event["t0"])
                    ):
                        raise HighlightVisualContractError(
                            f"{label} title text/tier/duration differs from Director intent"
                        )
                    _nonempty_text(title_params["style"], f"{label}.style")
                    _number(title_params["pos_y"], f"{label}.pos_y")
                elif (
                    implementation_kind
                    in {
                        "transition_title",
                        "book_cover",
                        "quote_card",
                        "bigstat",
                        "sticker_pair",
                        "concept_card",
                        "chapter_label",
                    }
                    and component != implementation_kind
                ):
                    raise HighlightVisualContractError(
                        f"{label} component differs from implementation_kind"
                    )
                expected_spec_hash = _content_hash(
                    {"component": component, "render_params": params}
                )
                if candidate["render_spec_sha256"] != expected_spec_hash:
                    raise HighlightVisualContractError(f"{label} render spec hash mismatch")
                _, media_identity = _validate_file_identity(
                    root, candidate["preview_media"], f"{label}.preview_media", media=True
                )
                provenance = _validate_provenance(
                    root,
                    candidate["provenance"],
                    label=f"{label}.provenance",
                    expected_kind="hyperframes_render",
                )
                if (
                    provenance["provider"] != HYPERFRAMES_PROVIDER
                    or provenance["license"] != HYPERFRAMES_LICENSE
                    or provenance["source_url"] is not None
                ):
                    raise HighlightVisualContractError(
                        f"{label} is not a trusted HyperFrames render"
                    )
                try:
                    verify_hyperframes_render_receipt(
                        root,
                        receipt_identity=provenance["receipt"],
                        expected_cut_id=str(work.document["cut_id"]),
                        expected_revision_id=str(work.document["revision_id"]),
                        expected_candidate_id=candidate_id,
                        expected_component=str(component),
                        expected_render_params=params,
                        expected_on_screen_text=implementation_text,
                        expected_media=media_identity,
                    )
                except TrustedRenderError as error:
                    raise HighlightVisualContractError(
                        f"{label} trusted HyperFrames receipt failed: {error}"
                    ) from error
                normalized_candidate = {
                    "candidate_id": candidate_id,
                    "visual_summary": summary,
                    "component": component,
                    "render_params": params,
                    "render_spec_sha256": expected_spec_hash,
                    "preview_media": media_identity,
                    "provenance": provenance,
                }
            else:
                authority_asset_id = _nonempty_text(
                    candidate["authority_asset_id"], f"{label}.authority_asset_id"
                )
                authority_asset = authority_by_id.get(authority_asset_id)
                if authority_asset is None:
                    raise HighlightVisualContractError(
                        f"{label} references an absent trusted authority asset"
                    )
                if summary != authority_asset["semantic_summary"]:
                    raise HighlightVisualContractError(
                        f"{label} visual_summary differs from trusted authority"
                    )
                source_class = str(authority_asset["source_class"])
                if mode == "stock" and source_class not in {
                    "licensed_stock",
                    "official_archive",
                    "public_domain",
                }:
                    raise HighlightVisualContractError(
                        f"{label} stock mode requires a trusted stock/archive source class"
                    )
                required_provided_class = {
                    "self_archive": "provided_self_archive",
                    "screen_demo": "provided_screen_demo",
                    "evidence_doc": "provided_evidence_doc",
                }.get(str(event["category"]))
                if (
                    mode == "provided_asset"
                    and required_provided_class is not None
                    and source_class != required_provided_class
                ):
                    raise HighlightVisualContractError(
                        f"{label} provided source class differs from Director category"
                    )
                if candidate["media"] != authority_asset["original_media"]:
                    raise HighlightVisualContractError(
                        f"{label} media differs from trusted original acquisition bytes"
                    )
                expected_provenance = {
                    "kind": "stock_source" if mode == "stock" else "provided_source",
                    "provider": authority_asset["provider"],
                    "source_url": authority_asset["source_url"],
                    "license": authority_asset["license"],
                    "receipt": authority_asset["acquisition_receipt"],
                }
                if candidate["provenance"] != expected_provenance:
                    raise HighlightVisualContractError(
                        f"{label} provenance differs from trusted acquisition authority"
                    )
                _, media_identity = _validate_file_identity(
                    root, candidate["media"], f"{label}.media", media=True
                )
                expected_kind = "stock_source" if mode == "stock" else "provided_source"
                provenance = _validate_provenance(
                    root,
                    candidate["provenance"],
                    label=f"{label}.provenance",
                    expected_kind=expected_kind,
                )
                normalized_candidate = {
                    "candidate_id": candidate_id,
                    "visual_summary": summary,
                    "authority_asset_id": authority_asset_id,
                    "media": media_identity,
                    "provenance": provenance,
                }
            media_sha256 = str(media_identity["sha256"])
            source_url = provenance["source_url"]
            if media_sha256 in candidate_media_sha256 or (
                source_url is not None and str(source_url) in candidate_source_urls
            ):
                raise HighlightVisualContractError(f"{event_id} candidates are not distinct")
            candidate_media_sha256.add(media_sha256)
            if source_url is not None:
                candidate_source_urls.add(str(source_url))
            candidates.append(normalized_candidate)
            candidate_by_id[candidate_id] = normalized_candidate

        raw_selections = item["selections"]
        expected_shots = int(event["shots_hint"])
        if not isinstance(raw_selections, list) or len(raw_selections) != expected_shots:
            raise HighlightVisualContractError(
                f"{event_id} must fulfill exactly shots_hint={expected_shots} selections"
            )
        selections: list[dict[str, object]] = []
        prior_end = float(event["t0"])
        selected_ids: set[str] = set()
        for selection_index, selection_raw in enumerate(raw_selections, 1):
            selection = _selected_cue_claim(
                selection_raw,
                event=event,
                cues=cues,
                allow_cue_subrange=(
                    mode == "stock"
                    or _requested_move_range(work.document, str(event_id)) is not None
                ),
                label=f"{event_id}.selections[{selection_index}]",
            )
            candidate_id = str(selection["candidate_id"])
            if candidate_id not in candidate_by_id or candidate_id in selected_ids:
                raise HighlightVisualContractError(
                    f"{event_id} selected candidate is absent or reused"
                )
            selected_ids.add(candidate_id)
            if float(selection["t0"]) != prior_end:
                raise HighlightVisualContractError(
                    f"{event_id} selections must tile the exact Director event range"
                )
            prior_end = float(selection["t1"])
            if mode == "stock" and prior_end - float(selection["t0"]) > 3.0:
                raise HighlightVisualContractError("stock shot exceeds the 3s visual-phrase cap")
            candidate = candidate_by_id[candidate_id]
            media_identity = (
                candidate["preview_media"] if mode == "hyperframes" else candidate["media"]
            )
            media_path = root / str(media_identity["path"])
            try:
                media_duration = float(probe_stock_video(media_path)["duration_seconds"])
            except BrollContractError as error:
                raise HighlightVisualContractError(
                    f"{event_id} selected media is no longer inspectable: {error}"
                ) from error
            source_range = selection["source_range"]
            if float(source_range["end_sec"]) > media_duration + 0.001:
                raise HighlightVisualContractError(
                    f"{event_id} selected source range exceeds media duration"
                )
            if mode in {"stock", "provided_asset"}:
                media_sha256 = str(media_identity["sha256"])
                if media_sha256 in selected_source_media_sha256:
                    raise HighlightVisualContractError(
                        "selected stock/provided footage bytes must be unique per video"
                    )
                selected_source_media_sha256.add(media_sha256)
            render_spec: dict[str, object] | None = None
            if mode == "hyperframes":
                render_spec = {
                    "component": candidate["component"],
                    "render_params": candidate["render_params"],
                    "render_spec_sha256": candidate["render_spec_sha256"],
                }
            materializations.append(
                {
                    "materialization_id": f"{event_id}-s{selection_index:02d}",
                    "event_id": event_id,
                    "director_intent_sha256": intent_hash,
                    "target_lane": target_lane,
                    "implementation_kind": implementation_kind,
                    "mode": mode,
                    "cue_ids": selection["cue_ids"],
                    "t0": selection["t0"],
                    "t1": selection["t1"],
                    "quote": selection["quote"],
                    "source_range": source_range,
                    "on_screen_text": implementation_text,
                    "media": media_identity,
                    "provenance": candidate["provenance"],
                    "render_spec": render_spec,
                }
            )
            selections.append(selection)
        if prior_end != float(event["t1"]):
            raise HighlightVisualContractError(
                f"{event_id} selections must tile the exact Director event range"
            )
        if feedback is not None and feedback["action"] == "replace_asset":
            selected_candidate_ids = [row["candidate_id"] for row in selections]
            if selected_candidate_ids != [feedback["replacement"]]:
                raise HighlightVisualContractError(
                    f"{event_id} selected asset differs from requested replacement"
                )
        normalized_items.append(
            {
                "event_id": event_id,
                "director_intent_sha256": intent_hash,
                "mode": mode,
                "target_lane": target_lane,
                "implementation_kind": implementation_kind,
                "on_screen_text": implementation_text,
                "candidates": candidates,
                "selections": selections,
                "semantic_justification": justification,
            }
        )
    if seen_events != set(director_events):
        raise HighlightVisualContractError("DP implementation event coverage is incomplete")
    return normalized_items, tuple(materializations)


def accept_dp_fulfillment(
    episode_root: str | Path,
    *,
    cut_id: str,
    revision_id: str,
    proposal: Mapping[str, object] | str | Path,
    worker_identity: Mapping[str, str],
    execution_receipt: Mapping[str, object] | None = None,
    worker_proposal: Mapping[str, object] | str | Path | None = None,
    editorial_master: EditorialMasterSelection | object | None = None,
) -> ArtifactSelection:
    """Validate DP candidates/selections and publish the immutable fulfillment."""

    root = _root(episode_root)
    cut_id = _safe_cut_id(cut_id)
    revision_id = _require_pending_revision(root, cut_id, revision_id)
    work = load_visual_work_packet(
        root, cut_id=cut_id, revision_id=revision_id, editorial_master=editorial_master
    )
    director = load_director_plan(
        root, cut_id=cut_id, revision_id=revision_id, editorial_master=editorial_master
    )
    path = _revision_dir(root, cut_id, revision_id) / DP_FULFILLMENT_NAME
    hydrated_local_snapshot, hydrated_snapshot_identity = _proposal_file_snapshot(
        root, proposal, canonical_path=path, label="DP proposal"
    )
    worker_source = worker_proposal if worker_proposal is not None else proposal
    worker_local_snapshot, worker_snapshot_identity = _proposal_file_snapshot(
        root, worker_source, canonical_path=path, label="raw DP worker proposal"
    )
    strict_execution = _require_trusted_execution_receipt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        phase="dp",
        role="dp",
        worker_identity=worker_identity,
        worker_proposal=worker_source,
        execution_receipt=execution_receipt,
        expected_worker_proposal_identity=worker_snapshot_identity,
    )
    hydration_lineage, verified_worker_snapshot, verified_hydrated_snapshot = (
        _accepted_dp_hydration_lineage(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=1,
        raw_proposal=worker_proposal if worker_proposal is not None else proposal,
        hydrated_proposal=proposal,
        strict_execution=strict_execution,
        editorial_master=editorial_master,
        raw_proposal_identity=worker_snapshot_identity,
        hydrated_proposal_identity=hydrated_snapshot_identity,
        )
    )
    if verified_worker_snapshot is not None and (
        verified_worker_snapshot != strict_execution.get("proposal_document")
        or verified_worker_snapshot != worker_local_snapshot
    ):
        raise HighlightVisualContractError(
            "trusted DP hydration raw proposal differs from its execution snapshot"
        )
    raw = _require_exact_keys(
        verified_hydrated_snapshot or hydrated_local_snapshot,
        _DP_PROPOSAL_KEYS,
        "DP proposal",
    )
    if (
        raw["contract"] != DP_FULFILLMENT_CONTRACT
        or raw["episode_id"] != root.name
        or raw["cut_id"] != cut_id
    ):
        raise HighlightVisualContractError("DP proposal belongs to another contract/episode/cut")
    worker_execution = _trusted_worker_execution(worker_identity, expected_role="dp")
    director_execution = director.document["worker_execution"]
    if any(
        worker_execution[key] == director_execution[key]
        for key in ("worker_id", "execution_id", "session_id")
    ):
        raise HighlightVisualContractError(
            "DP worker identity, execution, and session must be distinct from Director"
        )
    if raw["director_plan"] != director.identity():
        raise HighlightVisualContractError("DP proposal Director lineage is stale")
    implementations, _ = _dp_implementations(
        root,
        raw["implementations"],
        director=director,
        work=work,
        attempt=1,
        editorial_master=editorial_master,
    )
    document: dict[str, object] = {
        "contract": DP_FULFILLMENT_CONTRACT,
        "episode_id": root.name,
        "cut_id": cut_id,
        "worker_execution": worker_execution,
        "execution_receipt": (
            strict_execution["receipt"] if isinstance(strict_execution, dict) else None
        ),
        **hydration_lineage,
        "director_plan": director.identity(),
        "implementations": implementations,
    }
    document["content_hash"] = _content_hash(document)
    _atomic_publish(path, document)
    return load_dp_fulfillment(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        editorial_master=editorial_master,
    )


def _load_dp_for_attempt(
    root: Path,
    *,
    cut_id: str,
    revision_id: str,
    attempt: int,
    editorial_master: EditorialMasterSelection | object | None,
) -> ArtifactSelection:
    work = load_visual_work_packet(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        editorial_master=editorial_master,
    )
    director = _load_director_for_attempt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt,
        editorial_master=editorial_master,
    )
    path = _dp_path(root, cut_id, revision_id, attempt)
    selection = _load_hashed_artifact(
        root, path, contract=DP_FULFILLMENT_CONTRACT, exact_keys=_DP_KEYS
    )
    document = selection.document
    if document["episode_id"] != root.name or document["cut_id"] != cut_id:
        raise HighlightVisualContractError("DP fulfillment belongs to another episode/cut")
    dp_execution = _trusted_worker_execution(document["worker_execution"], expected_role="dp")
    director_execution = director.document["worker_execution"]
    if any(
        dp_execution[key] == director_execution[key]
        for key in ("worker_id", "execution_id", "session_id")
    ):
        raise HighlightVisualContractError("DP worker role integrity failed")
    if document["director_plan"] != director.identity():
        raise HighlightVisualContractError("DP fulfillment Director lineage is stale")
    _verify_canonical_dp_hydration(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt,
        document=document,
        editorial_master=editorial_master,
    )
    implementations, _ = _dp_implementations(
        root,
        document["implementations"],
        director=director,
        work=work,
        attempt=attempt,
        editorial_master=editorial_master,
    )
    if document["implementations"] != implementations:
        raise HighlightVisualContractError("DP fulfillment normalized content drift")
    return selection


def _load_dp_attempt_marker(
    root: Path,
    *,
    cut_id: str,
    revision_id: str,
    attempt: int,
    editorial_master: EditorialMasterSelection | object | None,
) -> ArtifactSelection:
    if attempt < 2:
        raise HighlightVisualContractError("DP attempt marker starts at attempt 2")
    prior_dp = _load_dp_for_attempt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt - 1,
        editorial_master=editorial_master,
    )
    decision = load_refinement_decision(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt - 1,
        editorial_master=editorial_master,
    )
    dp = _load_dp_for_attempt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt,
        editorial_master=editorial_master,
    )
    path = _attempt_dir(root, cut_id, revision_id, attempt) / DP_ATTEMPT_NAME
    marker = _load_hashed_artifact(
        root, path, contract=DP_ATTEMPT_CONTRACT, exact_keys=_DP_ATTEMPT_KEYS
    )
    document = marker.document
    if (
        document["episode_id"] != root.name
        or document["cut_id"] != cut_id
        or document["attempt"] != attempt
        or document["director_plan"] != dp.document["director_plan"]
        or document["prior_dp_fulfillment"] != prior_dp.identity()
        or document["refinement_decision"] != decision.identity()
        or document["dp_fulfillment"] != dp.identity()
    ):
        raise HighlightVisualContractError("DP attempt lineage is stale")
    return marker


def _active_dp_attempt(
    root: Path,
    *,
    cut_id: str,
    revision_id: str,
    editorial_master: EditorialMasterSelection | object | None,
) -> int:
    _load_dp_for_attempt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=1,
        editorial_master=editorial_master,
    )
    active = 1
    for attempt in range(2, MAX_DP_ATTEMPTS + 1):
        marker_path = _attempt_dir(root, cut_id, revision_id, attempt) / DP_ATTEMPT_NAME
        if not marker_path.is_file():
            for later in range(attempt + 1, MAX_DP_ATTEMPTS + 1):
                later_dir = _attempt_dir(root, cut_id, revision_id, later)
                if (later_dir / DP_ATTEMPT_NAME).exists():
                    raise HighlightVisualContractError("DP attempt chain contains a gap")
            break
        _load_dp_attempt_marker(
            root,
            cut_id=cut_id,
            revision_id=revision_id,
            attempt=attempt,
            editorial_master=editorial_master,
        )
        active = attempt
    return active


def load_dp_fulfillment(
    episode_root: str | Path,
    *,
    cut_id: str,
    revision_id: str | None = None,
    attempt: int | None = None,
    editorial_master: EditorialMasterSelection | object | None = None,
) -> ArtifactSelection:
    """Freshly verify DP fulfillment, selected media, and every parent hash."""

    root = _root(episode_root)
    cut_id = _safe_cut_id(cut_id)
    selected_revision = revision_id or _active_revision_id(root, cut_id)
    selected_attempt = attempt
    if selected_attempt is None:
        selected_attempt = _active_dp_attempt(
            root,
            cut_id=cut_id,
            revision_id=selected_revision,
            editorial_master=editorial_master,
        )
    selection = _load_dp_for_attempt(
        root,
        cut_id=cut_id,
        revision_id=selected_revision,
        attempt=selected_attempt,
        editorial_master=editorial_master,
    )
    if selected_attempt > 1:
        _load_dp_attempt_marker(
            root,
            cut_id=cut_id,
            revision_id=selected_revision,
            attempt=selected_attempt,
            editorial_master=editorial_master,
        )
    return selection


def load_visual_materializations(
    episode_root: str | Path,
    *,
    cut_id: str,
    revision_id: str,
    attempt: int | None = None,
    editorial_master: EditorialMasterSelection | object | None = None,
) -> tuple[dict[str, object], ...]:
    """Freshly derive the exact per-selection semantic-audit work items."""

    root = _root(episode_root)
    cut_id = _safe_cut_id(cut_id)
    revision_id = _safe_revision_id(revision_id)
    work = load_visual_work_packet(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        editorial_master=editorial_master,
    )
    selected_attempt = attempt or _active_dp_attempt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        editorial_master=editorial_master,
    )
    director = _load_director_for_attempt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=selected_attempt,
        editorial_master=editorial_master,
    )
    dp = load_dp_fulfillment(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=selected_attempt,
        editorial_master=editorial_master,
    )
    _, materializations = _dp_implementations(
        root,
        dp.document["implementations"],
        director=director,
        work=work,
        attempt=selected_attempt,
        editorial_master=editorial_master,
    )
    return materializations


_AUDIT_KEYS = {
    "contract",
    "episode_id",
    "cut_id",
    "worker_execution",
    "director_plan",
    "dp_fulfillment",
    "findings",
    "content_hash",
}
_AUDIT_PROPOSAL_KEYS = _AUDIT_KEYS - {"content_hash", "worker_execution"}
_REFINEMENT_KEYS = {
    "contract",
    "episode_id",
    "cut_id",
    "attempt",
    "worker_execution",
    "director_plan",
    "dp_fulfillment",
    "findings",
    "mismatch_count",
    "uncertain_count",
    "content_hash",
}
_REFINEMENT_DECISION_KEYS = {
    "contract",
    "episode_id",
    "cut_id",
    "attempt",
    "worker_execution",
    "semantic_refinement",
    "decisions",
    "requires_director_replan",
    "content_hash",
}
_REFINEMENT_DECISION_PROPOSAL_KEYS = _REFINEMENT_DECISION_KEYS - {
    "worker_execution",
    "requires_director_replan",
    "content_hash",
}
_REFINEMENT_ITEM_KEYS = {"materialization_id", "event_id", "action", "rationale"}
_FINDING_KEYS = {
    "materialization_id",
    "event_id",
    "director_intent_sha256",
    "cue_ids",
    "t0",
    "t1",
    "quote",
    "source_range",
    "evidence_sha256",
    "visual_observation",
    "verdict",
    "rationale",
}
VISUAL_MATERIALIZATION_KEYS = frozenset(
    {
        "materialization_id",
        "event_id",
        "director_intent_sha256",
        "target_lane",
        "implementation_kind",
        "mode",
        "cue_ids",
        "t0",
        "t1",
        "quote",
        "source_range",
        "on_screen_text",
        "media",
        "provenance",
        "render_spec",
    }
)


def _accepted_findings(
    value: object,
    *,
    director: ArtifactSelection,
    materializations: Sequence[Mapping[str, object]],
    require_all_match: bool = True,
) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise HighlightVisualContractError("semantic audit findings must be an array")
    events = {
        event["event_id"]: event
        for event in director.document["events"]
        if isinstance(event, dict) and event.get("decision") == "add_visual"
    }
    if len(value) != len(materializations):
        raise HighlightVisualContractError(
            "semantic audit must cover every selected materialization exactly once"
        )
    normalized: list[dict[str, object]] = []
    for index, (raw, materialization) in enumerate(zip(value, materializations, strict=True), 1):
        finding = _require_exact_keys(raw, _FINDING_KEYS, f"findings[{index}]")
        event_id = str(materialization["event_id"])
        if event_id not in events:
            raise HighlightVisualContractError("audit references an unknown Director event")
        media = materialization["media"]
        required_matches = {
            "materialization_id": materialization["materialization_id"],
            "event_id": event_id,
            "director_intent_sha256": materialization["director_intent_sha256"],
            "cue_ids": materialization["cue_ids"],
            "t0": materialization["t0"],
            "t1": materialization["t1"],
            "quote": materialization["quote"],
            "source_range": materialization["source_range"],
            "evidence_sha256": media["sha256"],
        }
        for key, expected in required_matches.items():
            if finding[key] != expected:
                raise HighlightVisualContractError(
                    f"{event_id} audit {key} differs from selected materialization"
                )
        observation = _nonempty_text(
            finding["visual_observation"],
            f"{event_id}.visual_observation",
            minimum=12,
        )
        verdict = finding["verdict"]
        if verdict not in {"match", "mismatch", "uncertain"}:
            raise HighlightVisualContractError(f"{event_id} audit verdict is not allowed")
        if require_all_match and verdict != "match":
            raise HighlightVisualContractError(
                f"{event_id} semantic audit verdict={verdict}; cannot accept for materialization"
            )
        rationale = _nonempty_text(finding["rationale"], f"{event_id}.audit rationale", minimum=12)
        normalized.append(
            {
                **required_matches,
                "visual_observation": observation,
                "verdict": verdict,
                "rationale": rationale,
            }
        )
    return normalized


def _validate_audit_worker(
    worker_execution: Mapping[str, object],
    *,
    director: ArtifactSelection,
    dp: ArtifactSelection,
) -> None:
    director_worker = director.document["worker_execution"]
    dp_worker = dp.document["worker_execution"]
    if (
        worker_execution["worker_id"] != director_worker["worker_id"]
        or worker_execution["session_id"] != director_worker["session_id"]
        or worker_execution["execution_id"] == director_worker["execution_id"]
        or worker_execution["worker_id"] == dp_worker["worker_id"]
        or worker_execution["execution_id"] == dp_worker["execution_id"]
        or worker_execution["session_id"] == dp_worker["session_id"]
    ):
        raise HighlightVisualContractError(
            "semantic audit must be performed by the Director intent owner, distinct from DP"
        )


def load_semantic_refinement(
    episode_root: str | Path,
    *,
    cut_id: str,
    revision_id: str,
    attempt: int,
    editorial_master: EditorialMasterSelection | object | None = None,
) -> ArtifactSelection:
    """Freshly verify one immutable non-match audit as the next-attempt input."""

    root = _root(episode_root)
    cut_id = _safe_cut_id(cut_id)
    revision_id = _safe_revision_id(revision_id)
    work = load_visual_work_packet(
        root, cut_id=cut_id, revision_id=revision_id, editorial_master=editorial_master
    )
    director = _load_director_for_attempt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt,
        editorial_master=editorial_master,
    )
    dp = _load_dp_for_attempt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt,
        editorial_master=editorial_master,
    )
    if attempt > 1:
        _load_dp_attempt_marker(
            root,
            cut_id=cut_id,
            revision_id=revision_id,
            attempt=attempt,
            editorial_master=editorial_master,
        )
    _, materializations = _dp_implementations(
        root,
        dp.document["implementations"],
        director=director,
        work=work,
        attempt=attempt,
        editorial_master=editorial_master,
    )
    path = _attempt_dir(root, cut_id, revision_id, attempt) / SEMANTIC_REFINEMENT_NAME
    selection = _load_hashed_artifact(
        root,
        path,
        contract=SEMANTIC_REFINEMENT_CONTRACT,
        exact_keys=_REFINEMENT_KEYS,
    )
    document = selection.document
    if (
        document["episode_id"] != root.name
        or document["cut_id"] != cut_id
        or document["attempt"] != attempt
    ):
        raise HighlightVisualContractError("semantic refinement belongs to another attempt")
    worker_execution = _trusted_worker_execution(
        document["worker_execution"], expected_role="director"
    )
    _require_persisted_execution_receipt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        phase="semantic_audit" if attempt == 1 else f"semantic_audit-{attempt:03d}",
        role="director",
        worker_identity=worker_execution,  # type: ignore[arg-type]
    )
    _validate_audit_worker(worker_execution, director=director, dp=dp)
    if (
        document["director_plan"] != director.identity()
        or document["dp_fulfillment"] != dp.identity()
    ):
        raise HighlightVisualContractError("semantic refinement parent lineage is stale")
    findings = _accepted_findings(
        document["findings"],
        director=director,
        materializations=materializations,
        require_all_match=False,
    )
    mismatch_count = sum(row["verdict"] == "mismatch" for row in findings)
    uncertain_count = sum(row["verdict"] == "uncertain" for row in findings)
    if mismatch_count + uncertain_count == 0:
        raise HighlightVisualContractError("semantic refinement contains no refinement findings")
    if (
        document["findings"] != findings
        or document["mismatch_count"] != mismatch_count
        or document["uncertain_count"] != uncertain_count
    ):
        raise HighlightVisualContractError("semantic refinement normalized content drift")
    return selection


def _normalized_refinement_decisions(
    value: object,
    *,
    refinement: ArtifactSelection,
) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise HighlightVisualContractError("refinement decisions must be an array")
    findings = [
        row
        for row in refinement.document["findings"]
        if isinstance(row, dict) and row.get("verdict") != "match"
    ]
    if len(value) != len(findings):
        raise HighlightVisualContractError(
            "refinement decision must cover every non-match finding exactly once"
        )
    normalized: list[dict[str, object]] = []
    for index, (raw, finding) in enumerate(zip(value, findings, strict=True), 1):
        decision = _require_exact_keys(
            raw, _REFINEMENT_ITEM_KEYS, f"refinement decisions[{index}]"
        )
        if (
            decision["materialization_id"] != finding["materialization_id"]
            or decision["event_id"] != finding["event_id"]
        ):
            raise HighlightVisualContractError(
                "refinement decision identity differs from exact audit finding"
            )
        action = decision["action"]
        if action not in {"retry_dp", "director_replan"}:
            raise HighlightVisualContractError("refinement decision action is not allowed")
        normalized.append(
            {
                "materialization_id": finding["materialization_id"],
                "event_id": finding["event_id"],
                "action": action,
                "rationale": _nonempty_text(
                    decision["rationale"],
                    f"{finding['materialization_id']}.refinement rationale",
                    minimum=12,
                ),
            }
        )
    return normalized


def accept_refinement_decision(
    episode_root: str | Path,
    *,
    cut_id: str,
    revision_id: str,
    attempt: int,
    proposal: Mapping[str, object] | str | Path,
    worker_identity: Mapping[str, str],
    execution_receipt: Mapping[str, object] | None = None,
    editorial_master: EditorialMasterSelection | object | None = None,
) -> ArtifactSelection:
    """Accept the original Director's exact triage of every failed materialization."""

    root = _root(episode_root)
    cut_id = _safe_cut_id(cut_id)
    revision_id = _require_pending_revision(root, cut_id, revision_id)
    active_attempt = _active_dp_attempt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        editorial_master=editorial_master,
    )
    if attempt != active_attempt:
        raise HighlightVisualContractError("refinement decision attempt is stale")
    director = _load_director_for_attempt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt,
        editorial_master=editorial_master,
    )
    dp = _load_dp_for_attempt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt,
        editorial_master=editorial_master,
    )
    refinement = load_semantic_refinement(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt,
        editorial_master=editorial_master,
    )
    path = _attempt_dir(root, cut_id, revision_id, attempt) / REFINEMENT_DECISION_NAME
    proposal_snapshot, proposal_identity = _proposal_file_snapshot(
        root, proposal, canonical_path=path, label="refinement decision proposal"
    )
    strict_execution = _require_trusted_execution_receipt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        phase=f"refinement_decision-{attempt:03d}",
        role="director",
        worker_identity=worker_identity,
        worker_proposal=proposal,
        execution_receipt=execution_receipt,
        expected_worker_proposal_identity=proposal_identity,
    )
    raw = _require_exact_keys(
        _accepted_worker_proposal_snapshot(strict_execution, proposal_snapshot),
        _REFINEMENT_DECISION_PROPOSAL_KEYS,
        "refinement decision proposal",
    )
    if (
        raw["contract"] != REFINEMENT_DECISION_CONTRACT
        or raw["episode_id"] != root.name
        or raw["cut_id"] != cut_id
        or raw["attempt"] != attempt
        or raw["semantic_refinement"] != refinement.identity()
    ):
        raise HighlightVisualContractError("refinement decision lineage is stale")
    worker_execution = _trusted_worker_execution(worker_identity, expected_role="director")
    director_worker = director.document["worker_execution"]
    refinement_worker = refinement.document["worker_execution"]
    dp_worker = dp.document["worker_execution"]
    if (
        worker_execution["worker_id"] != director_worker["worker_id"]
        or worker_execution["session_id"] != director_worker["session_id"]
        or worker_execution["execution_id"]
        in {
            director_worker["execution_id"],
            refinement_worker["execution_id"],
            dp_worker["execution_id"],
        }
        or worker_execution["session_id"] == dp_worker["session_id"]
    ):
        raise HighlightVisualContractError(
            "refinement decision must be a fresh execution in the original Director session"
        )
    decisions = _normalized_refinement_decisions(raw["decisions"], refinement=refinement)
    document: dict[str, object] = {
        "contract": REFINEMENT_DECISION_CONTRACT,
        "episode_id": root.name,
        "cut_id": cut_id,
        "attempt": attempt,
        "worker_execution": worker_execution,
        "semantic_refinement": refinement.identity(),
        "decisions": decisions,
        "requires_director_replan": any(
            row["action"] == "director_replan" for row in decisions
        ),
    }
    document["content_hash"] = _content_hash(document)
    _atomic_publish(path, document)
    return load_refinement_decision(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt,
        editorial_master=editorial_master,
    )


def load_refinement_decision(
    episode_root: str | Path,
    *,
    cut_id: str,
    revision_id: str,
    attempt: int,
    editorial_master: EditorialMasterSelection | object | None = None,
) -> ArtifactSelection:
    """Freshly verify a Director refinement decision and its exact failed audit."""

    root = _root(episode_root)
    cut_id = _safe_cut_id(cut_id)
    revision_id = _safe_revision_id(revision_id)
    director = _load_director_for_attempt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt,
        editorial_master=editorial_master,
    )
    dp = _load_dp_for_attempt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt,
        editorial_master=editorial_master,
    )
    refinement = load_semantic_refinement(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt,
        editorial_master=editorial_master,
    )
    path = _attempt_dir(root, cut_id, revision_id, attempt) / REFINEMENT_DECISION_NAME
    selection = _load_hashed_artifact(
        root,
        path,
        contract=REFINEMENT_DECISION_CONTRACT,
        exact_keys=_REFINEMENT_DECISION_KEYS,
    )
    document = selection.document
    if (
        document["episode_id"] != root.name
        or document["cut_id"] != cut_id
        or document["attempt"] != attempt
        or document["semantic_refinement"] != refinement.identity()
    ):
        raise HighlightVisualContractError("refinement decision parent lineage is stale")
    worker_execution = _trusted_worker_execution(
        document["worker_execution"], expected_role="director"
    )
    _require_persisted_execution_receipt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        phase=f"refinement_decision-{attempt:03d}",
        role="director",
        worker_identity=worker_execution,  # type: ignore[arg-type]
    )
    director_worker = director.document["worker_execution"]
    refinement_worker = refinement.document["worker_execution"]
    dp_worker = dp.document["worker_execution"]
    if (
        worker_execution["worker_id"] != director_worker["worker_id"]
        or worker_execution["session_id"] != director_worker["session_id"]
        or worker_execution["execution_id"]
        in {
            director_worker["execution_id"],
            refinement_worker["execution_id"],
            dp_worker["execution_id"],
        }
        or worker_execution["session_id"] == dp_worker["session_id"]
    ):
        raise HighlightVisualContractError("refinement decision worker role integrity failed")
    decisions = _normalized_refinement_decisions(document["decisions"], refinement=refinement)
    requires_replan = any(row["action"] == "director_replan" for row in decisions)
    if (
        document["decisions"] != decisions
        or document["requires_director_replan"] is not requires_replan
    ):
        raise HighlightVisualContractError("refinement decision normalized content drift")
    return selection


def _load_unpublished_director_replan(
    root: Path,
    *,
    cut_id: str,
    revision_id: str,
    attempt: int,
    editorial_master: EditorialMasterSelection | object | None,
) -> ArtifactSelection:
    if attempt < 2:
        raise HighlightVisualContractError("unpublished Director replan attempt is invalid")
    work = load_visual_work_packet(
        root, cut_id=cut_id, revision_id=revision_id, editorial_master=editorial_master
    )
    prior = _load_director_for_attempt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt - 1,
        editorial_master=editorial_master,
    )
    decision = load_refinement_decision(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt - 1,
        editorial_master=editorial_master,
    )
    path = _director_path(root, cut_id, revision_id, attempt)
    selection = _load_hashed_artifact(
        root, path, contract=DIRECTOR_PLAN_CONTRACT, exact_keys=_DIRECTOR_KEYS
    )
    document = selection.document
    worker = _trusted_worker_execution(document["worker_execution"], expected_role="director")
    prior_worker = prior.document["worker_execution"]
    if (
        document["episode_id"] != root.name
        or document["cut_id"] != cut_id
        or document["work_packet"] != work.identity()
        or worker["worker_id"] != prior_worker["worker_id"]
        or worker["session_id"] != prior_worker["session_id"]
    ):
        raise HighlightVisualContractError("unpublished Director replan lineage is stale")
    _require_persisted_execution_receipt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        phase=f"director_replan-{attempt:03d}",
        role="director",
        worker_identity=worker,
    )
    events, coverage = _director_events(document["events"], work=work.document)
    _enforce_director_feedback(work=work.document, events=events)
    if document["events"] != events or document["coverage"] != coverage:
        raise HighlightVisualContractError("unpublished Director replan validation drift")
    prior_events = {str(row["event_id"]): row for row in prior.document["events"]}
    next_events = {str(row["event_id"]): row for row in events}
    if set(prior_events) != set(next_events):
        raise HighlightVisualContractError("unpublished Director replan changed event coverage")
    allowed = {
        str(row["event_id"])
        for row in decision.document["decisions"]
        if row["action"] == "director_replan"
    }
    for event_id, prior_event in prior_events.items():
        next_event = next_events[event_id]
        if event_id not in allowed and next_event != prior_event:
            raise HighlightVisualContractError(
                "unpublished Director replan changed an unapproved event"
            )
        if event_id in allowed:
            if next_event == prior_event:
                raise HighlightVisualContractError(
                    "unpublished Director replan left failed intent unchanged"
                )
            for key in ("event_id", "cue_ids", "t0", "t1", "quote"):
                if next_event[key] != prior_event[key]:
                    raise HighlightVisualContractError(
                        "unpublished Director replan changed transcript identity"
                    )
    return selection


def accept_director_replan(
    episode_root: str | Path,
    *,
    cut_id: str,
    revision_id: str,
    attempt: int,
    proposal: Mapping[str, object] | str | Path,
    worker_identity: Mapping[str, str],
    execution_receipt: Mapping[str, object] | None = None,
    editorial_master: EditorialMasterSelection | object | None = None,
) -> ArtifactSelection:
    """Publish a same-revision immutable Director generation for infeasible findings."""

    root = _root(episode_root)
    cut_id = _safe_cut_id(cut_id)
    revision_id = _require_pending_revision(root, cut_id, revision_id)
    prior_attempt = _active_dp_attempt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        editorial_master=editorial_master,
    )
    if attempt != prior_attempt + 1 or attempt > MAX_DP_ATTEMPTS:
        raise HighlightVisualContractError("Director replan attempt is stale or outside the limit")
    decision = load_refinement_decision(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=prior_attempt,
        editorial_master=editorial_master,
    )
    if not decision.document["requires_director_replan"]:
        raise HighlightVisualContractError("Director replan has no infeasible audit findings")
    work = load_visual_work_packet(
        root, cut_id=cut_id, revision_id=revision_id, editorial_master=editorial_master
    )
    prior_director = _load_director_for_attempt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=prior_attempt,
        editorial_master=editorial_master,
    )
    prior_dp = _load_dp_for_attempt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=prior_attempt,
        editorial_master=editorial_master,
    )
    path = _director_path(root, cut_id, revision_id, attempt)
    proposal_snapshot, proposal_identity = _proposal_file_snapshot(
        root, proposal, canonical_path=path, label="Director replan proposal"
    )
    strict_execution = _require_trusted_execution_receipt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        phase=f"director_replan-{attempt:03d}",
        role="director",
        worker_identity=worker_identity,
        worker_proposal=proposal,
        execution_receipt=execution_receipt,
        expected_worker_proposal_identity=proposal_identity,
    )
    raw = _require_exact_keys(
        _accepted_worker_proposal_snapshot(strict_execution, proposal_snapshot),
        _DIRECTOR_PROPOSAL_KEYS,
        "Director replan proposal",
    )
    if (
        raw["contract"] != DIRECTOR_PLAN_CONTRACT
        or raw["episode_id"] != root.name
        or raw["cut_id"] != cut_id
        or raw["work_packet"] != work.identity()
    ):
        raise HighlightVisualContractError("Director replan belongs to stale work lineage")
    worker_execution = _trusted_worker_execution(worker_identity, expected_role="director")
    prior_worker = prior_director.document["worker_execution"]
    decision_worker = decision.document["worker_execution"]
    dp_worker = prior_dp.document["worker_execution"]
    if (
        worker_execution["worker_id"] != prior_worker["worker_id"]
        or worker_execution["session_id"] != prior_worker["session_id"]
        or worker_execution["execution_id"]
        in {
            prior_worker["execution_id"],
            decision_worker["execution_id"],
            dp_worker["execution_id"],
        }
        or worker_execution["session_id"] == dp_worker["session_id"]
    ):
        raise HighlightVisualContractError(
            "Director replan must be a fresh execution in the original Director session"
        )
    events, coverage = _director_events(raw["events"], work=work.document)
    _enforce_director_feedback(work=work.document, events=events)
    claimed_coverage = _require_exact_keys(raw["coverage"], _COVERAGE_KEYS, "coverage")
    if claimed_coverage != coverage:
        raise HighlightVisualContractError("Director replan coverage summary is not deterministic")
    prior_events = {str(row["event_id"]): row for row in prior_director.document["events"]}
    next_events = {str(row["event_id"]): row for row in events}
    if set(prior_events) != set(next_events):
        raise HighlightVisualContractError("Director replan changed event identity coverage")
    allowed_event_ids = {
        str(row["event_id"])
        for row in decision.document["decisions"]
        if row["action"] == "director_replan"
    }
    for event_id, prior_event in prior_events.items():
        next_event = next_events[event_id]
        if event_id not in allowed_event_ids and next_event != prior_event:
            raise HighlightVisualContractError(
                "Director replan changed an event outside exact failed findings"
            )
        if event_id in allowed_event_ids:
            if next_event == prior_event:
                raise HighlightVisualContractError(
                    "Director replan left an infeasible event unchanged"
                )
            for key in ("event_id", "cue_ids", "t0", "t1", "quote"):
                if next_event[key] != prior_event[key]:
                    raise HighlightVisualContractError(
                        "Director replan changed immutable transcript event identity"
                    )
    document: dict[str, object] = {
        "contract": DIRECTOR_PLAN_CONTRACT,
        "episode_id": root.name,
        "cut_id": cut_id,
        "worker_execution": worker_execution,
        "work_packet": work.identity(),
        "events": events,
        "coverage": coverage,
    }
    document["content_hash"] = _content_hash(document)
    _atomic_publish(path, document)
    accepted = _load_hashed_artifact(
        root, path, contract=DIRECTOR_PLAN_CONTRACT, exact_keys=_DIRECTOR_KEYS
    )
    marker_path = _attempt_dir(root, cut_id, revision_id, attempt) / DIRECTOR_REPLAN_NAME
    marker: dict[str, object] = {
        "contract": DIRECTOR_REPLAN_CONTRACT,
        "episode_id": root.name,
        "cut_id": cut_id,
        "attempt": attempt,
        "prior_director_plan": prior_director.identity(),
        "refinement_decision": decision.identity(),
        "director_plan": accepted.identity(),
    }
    marker["content_hash"] = _content_hash(marker)
    _atomic_publish(marker_path, marker)
    return _load_director_for_attempt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt,
        editorial_master=editorial_master,
    )


def accept_dp_refinement(
    episode_root: str | Path,
    *,
    cut_id: str,
    revision_id: str,
    attempt: int,
    proposal: Mapping[str, object] | str | Path,
    worker_identity: Mapping[str, str],
    execution_receipt: Mapping[str, object] | None = None,
    worker_proposal: Mapping[str, object] | str | Path | None = None,
    editorial_master: EditorialMasterSelection | object | None = None,
) -> ArtifactSelection:
    """Publish the next immutable DP attempt, bound to exact failed findings only."""

    root = _root(episode_root)
    cut_id = _safe_cut_id(cut_id)
    revision_id = _require_pending_revision(root, cut_id, revision_id)
    if attempt < 2 or attempt > MAX_DP_ATTEMPTS:
        raise HighlightVisualContractError("DP refinement attempt is outside the bounded limit")
    prior_attempt = _active_dp_attempt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        editorial_master=editorial_master,
    )
    if attempt != prior_attempt + 1:
        raise HighlightVisualContractError("DP refinement attempt is stale or skips a generation")
    decision = load_refinement_decision(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=prior_attempt,
        editorial_master=editorial_master,
    )
    replan_path = _director_path(root, cut_id, revision_id, attempt)
    if decision.document["requires_director_replan"] and not replan_path.is_file():
        raise HighlightVisualContractError(
            "DP refinement is blocked because the Director must replan infeasible intents"
        )
    work = load_visual_work_packet(
        root, cut_id=cut_id, revision_id=revision_id, editorial_master=editorial_master
    )
    director = _load_director_for_attempt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt,
        editorial_master=editorial_master,
    )
    prior_dp = _load_dp_for_attempt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=prior_attempt,
        editorial_master=editorial_master,
    )
    path = _dp_path(root, cut_id, revision_id, attempt)
    hydrated_local_snapshot, hydrated_snapshot_identity = _proposal_file_snapshot(
        root, proposal, canonical_path=path, label="DP refinement proposal"
    )
    worker_source = worker_proposal if worker_proposal is not None else proposal
    worker_local_snapshot, worker_snapshot_identity = _proposal_file_snapshot(
        root, worker_source, canonical_path=path, label="raw DP refinement worker proposal"
    )
    strict_execution = _require_trusted_execution_receipt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        phase=f"dp-{attempt:03d}",
        role="dp",
        worker_identity=worker_identity,
        worker_proposal=worker_source,
        execution_receipt=execution_receipt,
        expected_worker_proposal_identity=worker_snapshot_identity,
    )
    hydration_lineage, verified_worker_snapshot, verified_hydrated_snapshot = (
        _accepted_dp_hydration_lineage(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt,
        raw_proposal=worker_proposal if worker_proposal is not None else proposal,
        hydrated_proposal=proposal,
        strict_execution=strict_execution,
        editorial_master=editorial_master,
        raw_proposal_identity=worker_snapshot_identity,
        hydrated_proposal_identity=hydrated_snapshot_identity,
        )
    )
    if verified_worker_snapshot is not None and (
        verified_worker_snapshot != strict_execution.get("proposal_document")
        or verified_worker_snapshot != worker_local_snapshot
    ):
        raise HighlightVisualContractError(
            "trusted DP hydration raw proposal differs from its execution snapshot"
        )
    raw = _require_exact_keys(
        verified_hydrated_snapshot or hydrated_local_snapshot,
        _DP_PROPOSAL_KEYS,
        "DP refinement proposal",
    )
    if (
        raw["contract"] != DP_FULFILLMENT_CONTRACT
        or raw["episode_id"] != root.name
        or raw["cut_id"] != cut_id
        or raw["director_plan"] != director.identity()
    ):
        raise HighlightVisualContractError("DP refinement belongs to stale Director lineage")
    worker_execution = _trusted_worker_execution(worker_identity, expected_role="dp")
    director_worker = director.document["worker_execution"]
    if any(
        worker_execution[key] == director_worker[key]
        for key in ("worker_id", "execution_id", "session_id")
    ):
        raise HighlightVisualContractError(
            "DP refinement worker must remain distinct from Director"
        )
    for prior in range(1, prior_attempt + 1):
        prior_worker = _load_dp_for_attempt(
            root,
            cut_id=cut_id,
            revision_id=revision_id,
            attempt=prior,
            editorial_master=editorial_master,
        ).document["worker_execution"]
        if (
            worker_execution["execution_id"] == prior_worker["execution_id"]
            or worker_execution["session_id"] == prior_worker["session_id"]
        ):
            raise HighlightVisualContractError(
                "each DP refinement requires a fresh execution and session"
            )
    implementations, materializations = _dp_implementations(
        root,
        raw["implementations"],
        director=director,
        work=work,
        attempt=attempt,
        editorial_master=editorial_master,
    )
    prior_director = _load_director_for_attempt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=prior_attempt,
        editorial_master=editorial_master,
    )
    _, prior_materializations = _dp_implementations(
        root,
        prior_dp.document["implementations"],
        director=prior_director,
        work=work,
        attempt=prior_attempt,
        editorial_master=editorial_master,
    )
    prior_by_id = {str(row["materialization_id"]): row for row in prior_materializations}
    next_by_id = {str(row["materialization_id"]): row for row in materializations}
    replanned_event_ids = {
        str(row["event_id"])
        for row in decision.document["decisions"]
        if row["action"] == "director_replan"
    }
    stable_ids = {
        materialization_id
        for materialization_id, row in prior_by_id.items()
        if str(row["event_id"]) not in replanned_event_ids
    }
    if not stable_ids.issubset(next_by_id):
        raise HighlightVisualContractError(
            "DP refinement removed materializations outside exact replan findings"
        )
    retry_ids = {
        str(row["materialization_id"])
        for row in decision.document["decisions"]
        if row["action"] == "retry_dp"
    }
    def stable_materialization(value: Mapping[str, object]) -> dict[str, object]:
        normalized = dict(value)
        provenance = normalized.get("provenance")
        if isinstance(provenance, dict):
            normalized["provenance"] = {
                key: item for key, item in provenance.items() if key != "receipt"
            }
        return normalized

    for materialization_id, prior_row in prior_by_id.items():
        if str(prior_row["event_id"]) in replanned_event_ids:
            continue
        next_row = next_by_id[materialization_id]
        if materialization_id not in retry_ids and stable_materialization(
            next_row
        ) != stable_materialization(prior_row):
            raise HighlightVisualContractError(
                "DP refinement changed a materialization that passed semantic audit"
            )
        if materialization_id in retry_ids and (
            next_row == prior_row
            or next_row["media"]["sha256"] == prior_row["media"]["sha256"]
        ):
            raise HighlightVisualContractError(
                "DP refinement did not replace an exact failed materialization"
            )
    document: dict[str, object] = {
        "contract": DP_FULFILLMENT_CONTRACT,
        "episode_id": root.name,
        "cut_id": cut_id,
        "worker_execution": worker_execution,
        "execution_receipt": (
            strict_execution["receipt"] if isinstance(strict_execution, dict) else None
        ),
        **hydration_lineage,
        "director_plan": director.identity(),
        "implementations": implementations,
    }
    document["content_hash"] = _content_hash(document)
    _atomic_publish(path, document)
    accepted_dp = _load_dp_for_attempt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt,
        editorial_master=editorial_master,
    )
    marker_path = _attempt_dir(root, cut_id, revision_id, attempt) / DP_ATTEMPT_NAME
    marker: dict[str, object] = {
        "contract": DP_ATTEMPT_CONTRACT,
        "episode_id": root.name,
        "cut_id": cut_id,
        "attempt": attempt,
        "director_plan": director.identity(),
        "prior_dp_fulfillment": prior_dp.identity(),
        "refinement_decision": decision.identity(),
        "dp_fulfillment": accepted_dp.identity(),
    }
    marker["content_hash"] = _content_hash(marker)
    _atomic_publish(marker_path, marker)
    _load_dp_attempt_marker(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt,
        editorial_master=editorial_master,
    )
    return load_dp_fulfillment(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt,
        editorial_master=editorial_master,
    )


def accept_semantic_audit(
    episode_root: str | Path,
    *,
    cut_id: str,
    revision_id: str,
    proposal: Mapping[str, object] | str | Path,
    worker_identity: Mapping[str, str],
    execution_receipt: Mapping[str, object] | None = None,
    editorial_master: EditorialMasterSelection | object | None = None,
) -> ArtifactSelection:
    """Accept the intent owner's independent visual review after DP fulfillment."""

    root = _root(episode_root)
    cut_id = _safe_cut_id(cut_id)
    revision_id = _require_pending_revision(root, cut_id, revision_id)
    work = load_visual_work_packet(
        root, cut_id=cut_id, revision_id=revision_id, editorial_master=editorial_master
    )
    active_attempt = _active_dp_attempt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        editorial_master=editorial_master,
    )
    director = _load_director_for_attempt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=active_attempt,
        editorial_master=editorial_master,
    )
    dp = load_dp_fulfillment(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=active_attempt,
        editorial_master=editorial_master,
    )
    _, materializations = _dp_implementations(
        root,
        dp.document["implementations"],
        director=director,
        work=work,
        attempt=active_attempt,
        editorial_master=editorial_master,
    )
    path = _revision_dir(root, cut_id, revision_id) / SEMANTIC_AUDIT_NAME
    audit_phase = (
        "semantic_audit" if active_attempt == 1 else f"semantic_audit-{active_attempt:03d}"
    )
    proposal_snapshot, proposal_identity = _proposal_file_snapshot(
        root, proposal, canonical_path=path, label="semantic audit proposal"
    )
    strict_execution = _require_trusted_execution_receipt(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        phase=audit_phase,
        role="director",
        worker_identity=worker_identity,
        worker_proposal=proposal,
        execution_receipt=execution_receipt,
        expected_worker_proposal_identity=proposal_identity,
    )
    raw = _require_exact_keys(
        _accepted_worker_proposal_snapshot(strict_execution, proposal_snapshot),
        _AUDIT_PROPOSAL_KEYS,
        "semantic audit proposal",
    )
    if (
        raw["contract"] != SEMANTIC_AUDIT_CONTRACT
        or raw["episode_id"] != root.name
        or raw["cut_id"] != cut_id
    ):
        raise HighlightVisualContractError("semantic audit belongs to another contract/episode/cut")
    worker_execution = _trusted_worker_execution(worker_identity, expected_role="director")
    _validate_audit_worker(worker_execution, director=director, dp=dp)
    for prior_attempt in range(1, active_attempt):
        prior_refinement = load_semantic_refinement(
            root,
            cut_id=cut_id,
            revision_id=revision_id,
            attempt=prior_attempt,
            editorial_master=editorial_master,
        )
        if (
            worker_execution["execution_id"]
            == prior_refinement.document["worker_execution"]["execution_id"]
        ):
            raise HighlightVisualContractError(
                "each semantic audit requires a fresh Director execution"
            )
    if raw["director_plan"] != director.identity() or raw["dp_fulfillment"] != dp.identity():
        raise HighlightVisualContractError("semantic audit parent lineage is stale")
    findings = _accepted_findings(
        raw["findings"],
        director=director,
        materializations=materializations,
        require_all_match=False,
    )
    mismatch_count = sum(row["verdict"] == "mismatch" for row in findings)
    uncertain_count = sum(row["verdict"] == "uncertain" for row in findings)
    if mismatch_count + uncertain_count:
        attempt = active_attempt
        refinement_path = (
            _attempt_dir(root, cut_id, revision_id, attempt) / SEMANTIC_REFINEMENT_NAME
        )
        refinement: dict[str, object] = {
            "contract": SEMANTIC_REFINEMENT_CONTRACT,
            "episode_id": root.name,
            "cut_id": cut_id,
            "attempt": attempt,
            "worker_execution": worker_execution,
            "director_plan": director.identity(),
            "dp_fulfillment": dp.identity(),
            "findings": findings,
            "mismatch_count": mismatch_count,
            "uncertain_count": uncertain_count,
        }
        refinement["content_hash"] = _content_hash(refinement)
        _atomic_publish(refinement_path, refinement)
        return load_semantic_refinement(
            root,
            cut_id=cut_id,
            revision_id=revision_id,
            attempt=attempt,
            editorial_master=editorial_master,
        )
    document: dict[str, object] = {
        "contract": SEMANTIC_AUDIT_CONTRACT,
        "episode_id": root.name,
        "cut_id": cut_id,
        "worker_execution": worker_execution,
        "director_plan": director.identity(),
        "dp_fulfillment": dp.identity(),
        "findings": findings,
    }
    document["content_hash"] = _content_hash(document)
    _atomic_publish(path, document)
    accepted = load_semantic_audit(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        editorial_master=editorial_master,
    )
    parent_current = work.document["parent_current"]
    expected_current_hash = (
        str(parent_current["content_hash"]) if isinstance(parent_current, dict) else None
    )
    _write_pointer(
        root,
        cut_id=cut_id,
        name=CURRENT_POINTER_NAME,
        revision_id=revision_id,
        state="ready",
        work_packet=work.identity(),
        semantic_audit=accepted.identity(),
        expected_existing_content_hash=expected_current_hash,
    )
    return accepted


def load_semantic_audit(
    episode_root: str | Path,
    *,
    cut_id: str,
    revision_id: str | None = None,
    editorial_master: EditorialMasterSelection | object | None = None,
) -> ArtifactSelection:
    """Freshly verify the Director-owned audit and every selected media byte."""

    root = _root(episode_root)
    cut_id = _safe_cut_id(cut_id)
    selected_revision = revision_id or _active_revision_id(root, cut_id)
    work = load_visual_work_packet(
        root,
        cut_id=cut_id,
        revision_id=selected_revision,
        editorial_master=editorial_master,
    )
    active_attempt = _active_dp_attempt(
        root,
        cut_id=cut_id,
        revision_id=selected_revision,
        editorial_master=editorial_master,
    )
    director = _load_director_for_attempt(
        root,
        cut_id=cut_id,
        revision_id=selected_revision,
        attempt=active_attempt,
        editorial_master=editorial_master,
    )
    dp = load_dp_fulfillment(
        root,
        cut_id=cut_id,
        revision_id=selected_revision,
        editorial_master=editorial_master,
    )
    _, materializations = _dp_implementations(
        root,
        dp.document["implementations"],
        director=director,
        work=work,
        attempt=active_attempt,
        editorial_master=editorial_master,
    )
    path = _revision_dir(root, cut_id, selected_revision) / SEMANTIC_AUDIT_NAME
    selection = _load_hashed_artifact(
        root, path, contract=SEMANTIC_AUDIT_CONTRACT, exact_keys=_AUDIT_KEYS
    )
    document = selection.document
    if document["episode_id"] != root.name or document["cut_id"] != cut_id:
        raise HighlightVisualContractError("semantic audit belongs to another episode/cut")
    audit_worker = _trusted_worker_execution(
        document["worker_execution"], expected_role="director"
    )
    _require_persisted_execution_receipt(
        root,
        cut_id=cut_id,
        revision_id=selected_revision,
        phase=(
            "semantic_audit"
            if active_attempt == 1
            else f"semantic_audit-{active_attempt:03d}"
        ),
        role="director",
        worker_identity=audit_worker,  # type: ignore[arg-type]
    )
    _validate_audit_worker(audit_worker, director=director, dp=dp)
    if (
        document["director_plan"] != director.identity()
        or document["dp_fulfillment"] != dp.identity()
    ):
        raise HighlightVisualContractError("semantic audit parent lineage is stale")
    findings = _accepted_findings(
        document["findings"], director=director, materializations=materializations
    )
    if document["findings"] != findings:
        raise HighlightVisualContractError("semantic audit normalized content drift")
    return selection


def verify_visual_pipeline(
    episode_root: str | Path,
    *,
    cut_id: str,
    revision_id: str | None = None,
    editorial_master: EditorialMasterSelection | object | None = None,
) -> VisualPipelineSelection:
    """Verify the complete work -> Director -> DP -> semantic-audit DAG."""

    root = _root(episode_root)
    cut_id = _safe_cut_id(cut_id)
    selected_revision = revision_id or _active_revision_id(root, cut_id, current=True)
    work = load_visual_work_packet(
        root,
        cut_id=cut_id,
        revision_id=selected_revision,
        editorial_master=editorial_master,
    )
    active_attempt = _active_dp_attempt(
        root,
        cut_id=cut_id,
        revision_id=selected_revision,
        editorial_master=editorial_master,
    )
    director = _load_director_for_attempt(
        root,
        cut_id=cut_id,
        revision_id=selected_revision,
        attempt=active_attempt,
        editorial_master=editorial_master,
    )
    dp = load_dp_fulfillment(
        root,
        cut_id=cut_id,
        revision_id=selected_revision,
        editorial_master=editorial_master,
    )
    audit = load_semantic_audit(
        root,
        cut_id=cut_id,
        revision_id=selected_revision,
        editorial_master=editorial_master,
    )
    _, materializations = _dp_implementations(
        root,
        dp.document["implementations"],
        director=director,
        work=work,
        attempt=active_attempt,
        editorial_master=editorial_master,
    )
    selection = VisualPipelineSelection(
        work_packet=work,
        director_plan=director,
        dp_fulfillment=dp,
        semantic_audit=audit,
        materializations=materializations,
    )
    if revision_id is None:
        pointer = _load_pointer(root, cut_id, CURRENT_POINTER_NAME)
        if (
            pointer["revision_id"] != selected_revision
            or pointer["work_packet"] != work.identity()
            or pointer["semantic_audit"] != audit.identity()
        ):
            raise HighlightVisualContractError("CURRENT pointer ready lineage drift")
    return selection


def verify_visual_lineage(
    episode_root: str | Path,
    cut_id: str,
    *,
    cut_format: str | None = None,
    items: list[dict[str, object]] | None = None,
    editorial_master_lineage: dict[str, object] | None = None,
    editorial_master: EditorialMasterSelection | object | None = None,
) -> dict[str, object]:
    """Authoritative pre-Resolve verifier for normalized content visual items."""

    selected = verify_visual_pipeline(
        episode_root, cut_id=cut_id, editorial_master=editorial_master
    )
    lineage = selected.lineage()
    if cut_format is not None and lineage["format"] != cut_format:
        raise HighlightVisualContractError("visual lineage format differs from caller")
    if (
        editorial_master_lineage is not None
        and lineage["editorial_master"] != editorial_master_lineage
    ):
        raise HighlightVisualContractError("visual lineage Editorial Master differs from caller")
    expected_items = list(selected.materializations)
    if items is not None:
        normalized_items: list[dict[str, object]] = []
        for index, item in enumerate(items, 1):
            normalized_items.append(
                _require_exact_keys(item, set(VISUAL_MATERIALIZATION_KEYS), f"items[{index}]")
            )
        expected_by_id = {str(item["materialization_id"]): item for item in expected_items}
        actual_by_id = {str(item["materialization_id"]): item for item in normalized_items}
        if (
            len(expected_by_id) != len(expected_items)
            or len(actual_by_id) != len(normalized_items)
            or actual_by_id != expected_by_id
        ):
            raise HighlightVisualContractError(
                "materializer content visual items differ from audited DP selections"
            )
    return lineage


def validate_materialization_projection(
    value: object,
    *,
    label: str = "visual materialization",
) -> dict[str, object]:
    """Validate the public normalized projection shape without guessing raw recipes."""

    return _require_exact_keys(value, set(VISUAL_MATERIALIZATION_KEYS), label)


def _revision_paths(root: Path, cut_id: str, revision_id: str) -> dict[str, str]:
    directory = _revision_dir(root, cut_id, revision_id)
    return {
        "work_packet": (directory / WORK_PACKET_NAME).relative_to(root).as_posix(),
        "director_plan": (directory / DIRECTOR_PLAN_NAME).relative_to(root).as_posix(),
        "dp_fulfillment": (directory / DP_FULFILLMENT_NAME).relative_to(root).as_posix(),
        "semantic_audit": (directory / SEMANTIC_AUDIT_NAME).relative_to(root).as_posix(),
    }


def _status_paths(
    root: Path,
    cut_id: str,
    *,
    pending_revision_id: str | None,
    current_revision_id: str | None,
) -> dict[str, object]:
    cut_root = _cut_root(root, cut_id)
    return {
        "pending_pointer": (cut_root / PENDING_POINTER_NAME).relative_to(root).as_posix(),
        "current_pointer": (cut_root / CURRENT_POINTER_NAME).relative_to(root).as_posix(),
        "pending_artifacts": (
            _revision_paths(root, cut_id, pending_revision_id)
            if pending_revision_id is not None
            else None
        ),
        "current_artifacts": (
            _revision_paths(root, cut_id, current_revision_id)
            if current_revision_id is not None
            else None
        ),
    }


def visual_pipeline_status(
    episode_root: str | Path,
    *,
    cut_id: str,
    editorial_master: EditorialMasterSelection | object | None = None,
) -> dict[str, object]:
    """Return one deterministic pending state; malformed/stale artifacts are invalid."""

    root = _root(episode_root)
    cut_id = _safe_cut_id(cut_id)
    pending_revision_id: str | None = None
    current_revision_id: str | None = None
    result: dict[str, object] = {
        "contract": STATUS_CONTRACT,
        "episode_id": root.name,
        "cut_id": cut_id,
        "status": "invalid",
        "pending_revision_id": None,
        "current_revision_id": None,
        "paths": {},
    }
    try:
        pending_path = _cut_root(root, cut_id) / PENDING_POINTER_NAME
        current_path = _cut_root(root, cut_id) / CURRENT_POINTER_NAME
        if not pending_path.is_file() and not current_path.is_file():
            result["status"] = "awaiting_init"
            result["paths"] = _status_paths(
                root,
                cut_id,
                pending_revision_id=None,
                current_revision_id=None,
            )
            return result
        pending = _load_pointer(root, cut_id, PENDING_POINTER_NAME)
        pending_revision_id = str(pending["revision_id"])
        if current_path.is_file():
            current = _load_pointer(root, cut_id, CURRENT_POINTER_NAME)
            current_revision_id = str(current["revision_id"])
        result["pending_revision_id"] = pending_revision_id
        result["current_revision_id"] = current_revision_id
        result["paths"] = _status_paths(
            root,
            cut_id,
            pending_revision_id=pending_revision_id,
            current_revision_id=current_revision_id,
        )
        load_visual_work_packet(
            root,
            cut_id=cut_id,
            revision_id=pending_revision_id,
            editorial_master=editorial_master,
        )
        directory = _revision_dir(root, cut_id, pending_revision_id)
        if not (directory / DIRECTOR_PLAN_NAME).is_file():
            result["status"] = "awaiting_director"
        else:
            load_director_plan(
                root,
                cut_id=cut_id,
                revision_id=pending_revision_id,
                editorial_master=editorial_master,
            )
        if result["status"] == "invalid" and not (directory / DP_FULFILLMENT_NAME).is_file():
            result["status"] = "awaiting_dp"
        elif result["status"] == "invalid":
            load_dp_fulfillment(
                root,
                cut_id=cut_id,
                revision_id=pending_revision_id,
                editorial_master=editorial_master,
            )
            result["active_dp_attempt"] = _active_dp_attempt(
                root,
                cut_id=cut_id,
                revision_id=pending_revision_id,
                editorial_master=editorial_master,
            )
        if result["status"] == "invalid" and not (directory / SEMANTIC_AUDIT_NAME).is_file():
            active_attempt = int(result["active_dp_attempt"])
            refinement_path = (
                _attempt_dir(root, cut_id, pending_revision_id, active_attempt)
                / SEMANTIC_REFINEMENT_NAME
            )
            if refinement_path.is_file():
                refinement = load_semantic_refinement(
                    root,
                    cut_id=cut_id,
                    revision_id=pending_revision_id,
                    attempt=active_attempt,
                    editorial_master=editorial_master,
                )
                result["refinement_input"] = refinement.identity()
                decision_path = (
                    _attempt_dir(root, cut_id, pending_revision_id, active_attempt)
                    / REFINEMENT_DECISION_NAME
                )
                if not decision_path.is_file():
                    result["status"] = "awaiting_refinement_decision"
                else:
                    decision = load_refinement_decision(
                        root,
                        cut_id=cut_id,
                        revision_id=pending_revision_id,
                        attempt=active_attempt,
                        editorial_master=editorial_master,
                    )
                    result["refinement_decision"] = decision.identity()
                    if decision.document["requires_director_replan"]:
                        if active_attempt >= MAX_DP_ATTEMPTS:
                            result["status"] = "refinement_limit_reached"
                        else:
                            next_attempt = active_attempt + 1
                            next_director_path = _director_path(
                                root, cut_id, pending_revision_id, next_attempt
                            )
                            replan_marker = (
                                _attempt_dir(
                                    root, cut_id, pending_revision_id, next_attempt
                                )
                                / DIRECTOR_REPLAN_NAME
                            )
                            if next_director_path.is_file() and not replan_marker.is_file():
                                unpublished = _load_unpublished_director_replan(
                                    root,
                                    cut_id=cut_id,
                                    revision_id=pending_revision_id,
                                    attempt=next_attempt,
                                    editorial_master=editorial_master,
                                )
                                result["status"] = "requires_director_replan"
                                result["next_director_attempt"] = next_attempt
                                result["unpublished_director_replan"] = unpublished.identity()
                            elif next_director_path.is_file():
                                replanned = _load_director_for_attempt(
                                    root,
                                    cut_id=cut_id,
                                    revision_id=pending_revision_id,
                                    attempt=next_attempt,
                                    editorial_master=editorial_master,
                                )
                                result["status"] = "awaiting_dp_refinement"
                                result["next_dp_attempt"] = next_attempt
                                result["director_replan"] = replanned.identity()
                            else:
                                result["status"] = "requires_director_replan"
                                result["next_director_attempt"] = next_attempt
                    elif active_attempt >= MAX_DP_ATTEMPTS:
                        result["status"] = "refinement_limit_reached"
                    else:
                        result["status"] = "awaiting_dp_refinement"
                        result["next_dp_attempt"] = active_attempt + 1
            else:
                result["status"] = "awaiting_semantic_audit"
        elif result["status"] == "invalid":
            verified = verify_visual_pipeline(
                root,
                cut_id=cut_id,
                revision_id=pending_revision_id,
                editorial_master=editorial_master,
            )
            if not current_path.is_file():
                audit_worker = verified.semantic_audit.document["worker_execution"]
                active_attempt = int(result["active_dp_attempt"])
                audit_phase = (
                    "semantic_audit"
                    if active_attempt == 1
                    else f"semantic_audit-{active_attempt:03d}"
                )
                _require_persisted_execution_receipt(
                    root,
                    cut_id=cut_id,
                    revision_id=pending_revision_id,
                    phase=audit_phase,
                    role="director",
                    worker_identity=audit_worker,
                )
                result["status"] = "awaiting_semantic_audit"
                result["unpublished_semantic_audit"] = verified.semantic_audit.identity()
                return result
            current = _load_pointer(root, cut_id, CURRENT_POINTER_NAME)
            if current["revision_id"] != pending_revision_id:
                audit_worker = verified.semantic_audit.document["worker_execution"]
                active_attempt = int(result["active_dp_attempt"])
                audit_phase = (
                    "semantic_audit"
                    if active_attempt == 1
                    else f"semantic_audit-{active_attempt:03d}"
                )
                _require_persisted_execution_receipt(
                    root,
                    cut_id=cut_id,
                    revision_id=pending_revision_id,
                    phase=audit_phase,
                    role="director",
                    worker_identity=audit_worker,
                )
                result["status"] = "awaiting_semantic_audit"
                result["unpublished_semantic_audit"] = verified.semantic_audit.identity()
                return result
            if (
                current["work_packet"] != verified.work_packet.identity()
                or current["semantic_audit"] != verified.semantic_audit.identity()
            ):
                raise HighlightVisualContractError(
                    "accepted generation is not the exact CURRENT pointer"
                )
            result["current_revision_id"] = pending_revision_id
            result["paths"] = _status_paths(
                root,
                cut_id,
                pending_revision_id=pending_revision_id,
                current_revision_id=pending_revision_id,
            )
            result["status"] = "ready_to_materialize"
    except HighlightVisualContractError as error:
        result["error"] = str(error)
        result["paths"] = _status_paths(
            root,
            cut_id,
            pending_revision_id=pending_revision_id,
            current_revision_id=current_revision_id,
        )
    return result


# The remaining public functions are defined below as the contract grows through
# TDD tracer bullets.  Keeping the names exported here prevents callers from
# inventing a second interface while implementation is in progress.


__all__ = [
    "ACQUISITION_RECEIPT_CONTRACT",
    "ASSET_ACQUISITION_PROPOSAL_CONTRACT",
    "ASSET_AUTHORITY_CONTRACT",
    "ASSET_AUTHORITY_NAME",
    "CURRENT_POINTER_NAME",
    "DIRECTOR_PLAN_CONTRACT",
    "DIRECTOR_PLAN_NAME",
    "DIRECTOR_REPLAN_CONTRACT",
    "DIRECTOR_REPLAN_NAME",
    "DP_ATTEMPT_CONTRACT",
    "DP_ATTEMPT_NAME",
    "DP_FULFILLMENT_CONTRACT",
    "DP_FULFILLMENT_NAME",
    "EXECUTION_RECEIPT_CONTRACT",
    "EXECUTION_PREPARE_CONTRACT",
    "FEEDBACK_IDENTITY_MIGRATION_CONTRACT",
    "LINEAGE_CONTRACT",
    "PENDING_POINTER_NAME",
    "POINTER_CONTRACT",
    "PREFLIGHT_CONTRACT",
    "REQUESTED_VISUAL_FEEDBACK_CONTRACT",
    "SEMANTIC_AUDIT_CONTRACT",
    "SEMANTIC_AUDIT_NAME",
    "SEMANTIC_REFINEMENT_CONTRACT",
    "SEMANTIC_REFINEMENT_NAME",
    "REFINEMENT_DECISION_CONTRACT",
    "REFINEMENT_DECISION_NAME",
    "STATUS_CONTRACT",
    "VISUAL_PIPELINE_ROOT",
    "VISUAL_MATERIALIZATION_KEYS",
    "WORK_PACKET_CONTRACT",
    "WORK_PACKET_NAME",
    "ArtifactSelection",
    "HighlightVisualArtifactConflictError",
    "HighlightVisualContractError",
    "VisualPipelineSelection",
    "accept_director_plan",
    "accept_director_replan",
    "accept_dp_fulfillment",
    "accept_dp_refinement",
    "accept_asset_authority",
    "accept_refinement_decision",
    "accept_semantic_audit",
    "init_visual_work_packet",
    "load_director_plan",
    "load_dp_fulfillment",
    "load_asset_authority",
    "load_asset_authority_projection",
    "load_semantic_audit",
    "load_semantic_refinement",
    "load_refinement_decision",
    "load_visual_materializations",
    "load_visual_work_packet",
    "preflight_visual_work_packet",
    "publish_asset_authority",
    "validate_materialization_projection",
    "verify_visual_lineage",
    "verify_visual_pipeline",
    "visual_pipeline_status",
]
