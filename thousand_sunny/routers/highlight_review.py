"""Authenticated human selection gate for long-form highlight candidates."""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Cookie, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from agents.brook.script_video.editorial_master import (
    EditorialMasterContractError,
    EditorialMasterRequest,
)
from shared.background_job import atomic_job_write, job_expired, load_job, new_job
from shared.config import get_db_path, get_vault_path
from shared.highlight_shortlist import (
    HighlightDataError,
    append_review_feedback,
    collect,
    load_review_feedback,
    write_winners,
)
from shared.schemas.packaging import parse_packages
from shared.tight_srt import srt_to_vtt
from thousand_sunny.auth import check_auth

page_router = APIRouter(prefix="/bridge/highlights", tags=["bridge-highlights"])
_templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates" / "bridge")
)
_MAX_CANDIDATES = 5
_MAX_FEEDBACK = 2000
_MAX_REPLACEMENT = 500
_FINISHED_MANIFEST_SCHEMA = "nakama.finished_cut_review_manifest.v1"
_FINISHED_FEEDBACK_SCHEMA = "nakama.finished_cut_review_feedback.v1"
_FINISHED_FEEDBACK_FILE = "finished_review_feedback.v1.json"
_SHORT_FINISHED_FEEDBACK_FILE = "short_finished_review_feedback.v1.json"
_SHA256_LENGTH = 64
_ACTION_LABELS = {
    "approve": "保留",
    "remove": "刪除",
    "replace_asset": "替換素材",
    "change_type": "更換類型",
    "edit_text": "修改文字",
    "move": "移動時間",
    "comment": "留言",
}
_LANE_LABELS = {
    "b_roll": "B-ROLL",
    "hero_title": "HERO TITLE",
    "fullscreen_transition": "FULLSCREEN TRANSITION",
    "title_card": "字卡與斷句",
    "visual_effect": "ICON／動畫",
    "pacing": "剪輯節奏",
}
_SHORT_REVIEW_LANES = ("title_card", "b_roll", "visual_effect", "pacing")
_SHORT_COMPONENT_ACTIONS = {
    "title_card": ["approve", "edit_text", "move", "comment"],
    "b_roll": ["approve", "remove", "replace_asset", "change_type", "move", "comment"],
    "visual_effect": ["approve", "remove", "replace_asset", "change_type", "move", "comment"],
    "pacing": ["approve", "remove", "move", "comment"],
}
_PUBLISH_PREP_PROCESSES: dict[tuple[str, str], subprocess.Popen] = {}
_PUBLISH_PREP_TIMEOUT_SECONDS = 7200


def _publish_prep_state(episode_dir: Path, cut_id: str) -> dict | None:
    receipt = episode_dir / "highlights" / "exports" / f".publish_prep_{cut_id}.json"
    payload = load_job(receipt)
    if not payload or payload.get("status") != "rendering":
        return payload
    key = (str(episode_dir.resolve()), cut_id)
    process = _PUBLISH_PREP_PROCESSES.get(key)
    exit_code = process.poll() if process is not None else None
    if (process is not None and exit_code is not None) or job_expired(payload):
        reason = (
            f"background child exited ({exit_code})"
            if process is not None and exit_code is not None
            else "background attempt exceeded its deadline"
        )
        payload = {**payload, "status": "failed", "exit_code": exit_code, "error": reason}
        atomic_job_write(receipt, payload)
    return payload


def _shosho_asset_version() -> str:
    static_dir = Path(__file__).resolve().parent.parent / "static" / "shosho"
    digest = hashlib.sha1()
    for name in ("tokens.css", "bridge.css", "bridge-pages.css", "bridge-highlight-review.css"):
        asset = static_dir / name
        if asset.is_file():
            digest.update(asset.read_bytes())
    return digest.hexdigest()[:8]


_SHOSHO_ASSET_VERSION = _shosho_asset_version()


def _episode_dir(episode_slug: str) -> Path:
    """Resolve a single episode directory from the explicit Bridge env only."""
    # Episode folders are named by the editor and may include Chinese, spaces and
    # punctuation (for example ``20260721 鄭國威``).  Reject path syntax instead
    # of restricting the human-facing name to ASCII, then prove containment below.
    if (
        not episode_slug
        or len(episode_slug) > 120
        or episode_slug in {".", ".."}
        or any(character in episode_slug for character in ("/", "\\", ":", "\x00"))
    ):
        raise HTTPException(status_code=404, detail="invalid episode slug")
    root_value = os.environ.get("PODCAST_EPISODES_ROOT", "").strip()
    if not root_value:
        raise HTTPException(status_code=503, detail="PODCAST_EPISODES_ROOT is not configured")
    root = Path(root_value)
    if not root.is_dir():
        raise HTTPException(status_code=503, detail="PODCAST_EPISODES_ROOT is not a directory")
    candidate = root / episode_slug
    # ``resolve`` makes the single-segment rule defensible if a directory is a symlink.
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="invalid episode slug") from exc
    if not candidate.is_dir():
        raise HTTPException(status_code=404, detail=f"episode not found: {episode_slug}")
    return candidate


def _require_auth(nakama_auth: str | None) -> None:
    """Protect video, subtitle and write endpoints with a non-HTML 401."""
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="unauthorized")


def _login_redirect(request: Request) -> RedirectResponse:
    return RedirectResponse(f"/login?next={quote(request.url.path, safe='/')}", status_code=302)


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value.lower())
    )


def _manifest_error(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=f"finished review manifest invalid: {detail}")


def _review_format(value: str) -> str:
    normalized = str(value or "long").strip().lower()
    if normalized not in {"long", "short"}:
        raise HTTPException(status_code=400, detail="format must be long or short")
    return normalized


def _require_final_qa_clear(episode_dir: Path, cut_id: str) -> None:
    """Fail closed unless final QA explicitly covers the selected cut."""
    qa_path = episode_dir / "highlights" / "qa_final.json"
    if not qa_path.is_file():
        raise HTTPException(status_code=409, detail="qa_final.json 不存在；先完成成片 Final QA")
    try:
        payload = json.loads(qa_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=409, detail="qa_final.json 無法讀取或不是有效 JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=409, detail="qa_final.json 根節點必須是 object")
    findings = payload.get("findings")
    clean = payload.get("clean")
    if not isinstance(findings, list) or not isinstance(clean, list):
        raise HTTPException(status_code=409, detail="qa_final.json 缺少 findings/clean 清單")

    covered = False
    critical_ids: list[str] = []
    for finding in findings:
        if not isinstance(finding, dict):
            raise HTTPException(status_code=409, detail="qa_final.json findings 格式錯誤")
        finding_id = finding.get("id")
        severity = finding.get("severity")
        if not isinstance(finding_id, str) or not finding_id.strip():
            raise HTTPException(status_code=409, detail="qa_final.json finding 缺少 id")
        if not isinstance(severity, str) or not severity.strip():
            raise HTTPException(status_code=409, detail="qa_final.json finding 缺少 severity")
        if finding_id == cut_id:
            covered = True
            if severity.strip().lower() == "critical":
                critical_ids.append(str(finding.get("check") or finding_id))

    for entry in clean:
        clean_id = (
            entry
            if isinstance(entry, str)
            else entry.get("id")
            if isinstance(entry, dict)
            else None
        )
        if clean_id == cut_id:
            covered = True

    if not covered:
        raise HTTPException(status_code=409, detail=f"qa_final.json 尚未覆蓋選定成片 {cut_id}")
    if critical_ids:
        raise HTTPException(
            status_code=409,
            detail=f"qa_final.json 的 {cut_id} 尚有 critical：{', '.join(critical_ids)}",
        )


def _short_cut_sort_key(path: Path) -> tuple[int, str]:
    digits = "".join(character for character in path.name if character.isdigit())
    return (int(digits) if digits else 9999, path.name)


def _short_event_lane(event_type: str) -> str | None:
    if event_type.startswith("card-tier"):
        return "title_card"
    if event_type in {"video", "photo"}:
        return "b_roll"
    if event_type in {"icon_motion", "sticker", "concept"}:
        return "visual_effect"
    if event_type in {"punch-cut", "punch-ramp"}:
        return "pacing"
    return None


def _short_event_display(event: dict[str, Any], lane: str) -> str:
    slug = str(event.get("slug") or "").replace("/", "\n").strip()
    note = str(event.get("note") or "").strip()
    if lane == "title_card":
        return slug or "未命名字卡"
    if lane == "pacing":
        return note or str(event.get("type") or "節奏事件")
    return note or slug or str(event.get("type") or "未命名元件")


def _artifact_receipt(path: Path, *, duration_seconds: float | None = None) -> dict[str, Any]:
    if not path.is_file():
        raise _manifest_error(f"artifact is missing: {path}")
    receipt: dict[str, Any] = {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": _file_sha256(path),
    }
    if duration_seconds is not None:
        receipt["duration_seconds"] = duration_seconds
    return receipt


def _load_short_finished_manifest(episode_slug: str) -> dict[str, Any]:
    """Build a content-addressed short review inventory from run_short_review receipts."""
    episode_dir = _episode_dir(episode_slug)
    review_dir = episode_dir / "highlights" / "review"
    cut_dirs = sorted(
        (
            path
            for path in review_dir.glob("KS*")
            if path.is_dir() and (path / "events.json").is_file()
        ),
        key=_short_cut_sort_key,
    )
    if not cut_dirs:
        raise HTTPException(status_code=404, detail="short review packets not found")

    cuts: list[dict[str, Any]] = []
    for cut_dir in cut_dirs:
        events_path = cut_dir / "events.json"
        try:
            packet = json.loads(events_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _manifest_error(f"cannot read {events_path}") from exc
        if not isinstance(packet, dict) or not isinstance(packet.get("events"), list):
            raise _manifest_error(f"{cut_dir.name} events.json has an invalid schema")
        duration = packet.get("duration_sec")
        if (
            not isinstance(duration, (int, float))
            or isinstance(duration, bool)
            or not math.isfinite(float(duration))
            or float(duration) <= 0
        ):
            raise _manifest_error(f"{cut_dir.name} duration_sec is invalid")
        duration = float(duration)
        preview_name = packet.get("preview")
        if not isinstance(preview_name, str) or not preview_name:
            raise _manifest_error(f"{cut_dir.name} preview is required")
        preview_path = cut_dir / preview_name
        subtitles_path = cut_dir / "subs.srt"
        lane_counts = {lane: 0 for lane in _SHORT_REVIEW_LANES}
        components: list[dict[str, Any]] = []
        review_components: list[dict[str, Any]] = []
        for event in packet["events"]:
            if not isinstance(event, dict):
                raise _manifest_error(f"{cut_dir.name} event must be an object")
            lane = _short_event_lane(str(event.get("type") or ""))
            if lane is None:
                continue
            t0, t1 = event.get("t0"), event.get("t1")
            if (
                not isinstance(t0, (int, float))
                or isinstance(t0, bool)
                or not isinstance(t1, (int, float))
                or isinstance(t1, bool)
                or not math.isfinite(float(t0))
                or not math.isfinite(float(t1))
                or float(t0) < 0
                or float(t1) <= float(t0)
                or float(t1) > duration + 0.01
            ):
                raise _manifest_error(f"{cut_dir.name} review event has an invalid timeline range")
            lane_counts[lane] += 1
            lane_slug = lane.replace("_", "-")
            component_id = f"{cut_dir.name}-{lane_slug}-{lane_counts[lane]:03d}"
            component = {
                **event,
                "component_id": component_id,
                "lane": lane,
                "t0": float(t0),
                "t1": float(t1),
                "display": _short_event_display(event, lane),
                "actions": [
                    {"value": action, "label": _ACTION_LABELS[action]}
                    for action in _SHORT_COMPONENT_ACTIONS[lane]
                ],
            }
            components.append(component)
            review_components.append(component)
        timeline = str(packet.get("timeline") or cut_dir.name)
        title = timeline.split(" - ", 1)[-1].replace("（緊·導播）", "").strip()
        cuts.append(
            {
                "cut_id": cut_dir.name,
                "title": title,
                "format": "short",
                "artifacts": {
                    "preview": _artifact_receipt(preview_path, duration_seconds=duration),
                    "subtitles": _artifact_receipt(subtitles_path),
                    "review_events": _artifact_receipt(events_path),
                },
                "components": components,
                "review_components": review_components,
                "component_counts": lane_counts,
            }
        )

    contract = {
        "review_lanes": list(_SHORT_REVIEW_LANES),
        "component_actions": _SHORT_COMPONENT_ACTIONS,
        "gate_actions": ["request_changes", "approve_cut", "approve_all"],
    }
    public_manifest: dict[str, Any] = {
        "schema": _FINISHED_MANIFEST_SCHEMA,
        "episode_id": episode_slug,
        "stage": 5,
        "review_format": "short",
        "gate": {"kind": "finished_cut_review", "status": "ready_for_review"},
        "cuts": cuts,
        "feedback_contract": contract,
        "lane_labels": {lane: _LANE_LABELS[lane] for lane in _SHORT_REVIEW_LANES},
        "skill_promotion": {
            "target_skill": ".claude/skills/brook-director",
            "direct_free_text_self_mutation": False,
        },
    }
    canonical = json.dumps(public_manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
    public_manifest["_path"] = review_dir / "virtual_short_finished_review_manifest.json"
    public_manifest["_review_dir"] = review_dir
    public_manifest["_sha256"] = hashlib.sha256(canonical).hexdigest()
    return public_manifest


def _load_review_manifest(episode_slug: str, review_format: str) -> dict[str, Any]:
    if review_format == "short":
        return _load_short_finished_manifest(episode_slug)
    manifest = _load_finished_manifest(episode_slug)
    manifest["review_format"] = "long"
    return manifest


def _latest_finished_manifest_path(review_dir: Path) -> Path:
    manifests = sorted(review_dir.glob("finished_review_manifest_*.json"))
    if not manifests:
        raise HTTPException(status_code=404, detail="finished review manifest not found")
    return manifests[-1]


def _component_display(component: dict[str, Any]) -> str:
    lane = component["lane"]
    if lane == "hero_title":
        return str(component.get("text") or "未命名 Hero title")
    if lane == "fullscreen_transition":
        variables = component.get("vars")
        if isinstance(variables, dict) and variables.get("title"):
            return str(variables["title"])
    return str(component.get("note") or component.get("slug") or component["component_id"])


def _load_finished_manifest(episode_slug: str) -> dict[str, Any]:
    """Load and validate the latest Stage 5 contract, retaining its byte hash."""
    episode_dir = _episode_dir(episode_slug)
    review_dir = episode_dir / "highlights" / "review"
    path = _latest_finished_manifest_path(review_dir)
    try:
        raw = path.read_bytes()
        manifest = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _manifest_error(f"cannot read {path.name}") from exc
    if not isinstance(manifest, dict):
        raise _manifest_error("root must be an object")
    if manifest.get("schema") != _FINISHED_MANIFEST_SCHEMA:
        raise _manifest_error(f"schema must be {_FINISHED_MANIFEST_SCHEMA}")
    if manifest.get("episode_id") != episode_slug:
        raise _manifest_error("episode_id does not match URL slug")
    if manifest.get("stage") != 5:
        raise _manifest_error("stage must be 5")
    gate = manifest.get("gate")
    if not isinstance(gate, dict) or gate.get("kind") != "finished_cut_review":
        raise _manifest_error("gate.kind must be finished_cut_review")
    contract = manifest.get("feedback_contract")
    if not isinstance(contract, dict):
        raise _manifest_error("feedback_contract is required")
    lanes = contract.get("review_lanes")
    actions = contract.get("component_actions")
    if not isinstance(lanes, list) or not lanes or not all(isinstance(lane, str) for lane in lanes):
        raise _manifest_error("feedback_contract.review_lanes must be a non-empty string list")
    if len(lanes) != len(set(lanes)) or not isinstance(actions, dict):
        raise _manifest_error("review lanes/actions are malformed")
    for lane in lanes:
        lane_actions = actions.get(lane)
        if (
            not isinstance(lane_actions, list)
            or not lane_actions
            or not all(
                isinstance(action, str) and action in _ACTION_LABELS for action in lane_actions
            )
        ):
            raise _manifest_error(f"component actions are invalid for lane {lane}")

    cuts = manifest.get("cuts")
    if not isinstance(cuts, list) or not cuts:
        raise _manifest_error("cuts must be a non-empty list")
    cut_ids: set[str] = set()
    component_ids: set[str] = set()
    for cut in cuts:
        if not isinstance(cut, dict):
            raise _manifest_error("each cut must be an object")
        cut_id = cut.get("cut_id")
        if not isinstance(cut_id, str) or not cut_id or cut_id in cut_ids:
            raise _manifest_error("cut_id must be unique and non-empty")
        cut_ids.add(cut_id)
        artifacts = cut.get("artifacts")
        if not isinstance(artifacts, dict):
            raise _manifest_error(f"{cut_id} artifacts are required")
        for artifact_name in ("preview", "subtitles"):
            artifact = artifacts.get(artifact_name)
            if not isinstance(artifact, dict):
                raise _manifest_error(f"{cut_id} {artifact_name} artifact is required")
            if not isinstance(artifact.get("path"), str) or not artifact["path"]:
                raise _manifest_error(f"{cut_id} {artifact_name}.path is required")
            if (
                not isinstance(artifact.get("bytes"), int)
                or isinstance(artifact["bytes"], bool)
                or artifact["bytes"] < 0
            ):
                raise _manifest_error(f"{cut_id} {artifact_name}.bytes is invalid")
            if not _is_sha256(artifact.get("sha256")):
                raise _manifest_error(f"{cut_id} {artifact_name}.sha256 is invalid")
        duration = artifacts["preview"].get("duration_seconds")
        if (
            not isinstance(duration, (int, float))
            or isinstance(duration, bool)
            or not math.isfinite(float(duration))
            or duration <= 0
        ):
            raise _manifest_error(f"{cut_id} preview duration is invalid")
        components = cut.get("components")
        if not isinstance(components, list):
            raise _manifest_error(f"{cut_id} components must be a list")
        review_components: list[dict[str, Any]] = []
        for component in components:
            if not isinstance(component, dict):
                raise _manifest_error(f"{cut_id} component must be an object")
            lane = component.get("lane")
            if lane not in lanes:
                continue
            component_id = component.get("component_id")
            if (
                not isinstance(component_id, str)
                or not component_id
                or component_id in component_ids
            ):
                raise _manifest_error("review component_id must be unique and non-empty")
            component_ids.add(component_id)
            t0, t1 = component.get("t0"), component.get("t1")
            if (
                not isinstance(t0, (int, float))
                or isinstance(t0, bool)
                or not isinstance(t1, (int, float))
                or isinstance(t1, bool)
                or not math.isfinite(float(t0))
                or not math.isfinite(float(t1))
                or t0 < 0
                or t1 <= t0
                or t1 > duration + 0.001
            ):
                raise _manifest_error(f"{component_id} has an invalid timeline range")
            normalized = dict(component)
            normalized["display"] = _component_display(normalized)
            normalized["actions"] = [
                {"value": action, "label": _ACTION_LABELS[action]} for action in actions[lane]
            ]
            review_components.append(normalized)
        cut["review_components"] = review_components
    manifest["_path"] = path
    manifest["_review_dir"] = review_dir
    manifest["_sha256"] = hashlib.sha256(raw).hexdigest()
    manifest["lane_labels"] = {lane: _LANE_LABELS.get(lane, lane.upper()) for lane in lanes}
    return manifest


def _safe_artifact_path(
    manifest: dict[str, Any], cut_id: str, artifact_name: str
) -> tuple[Path, dict]:
    cut = next((row for row in manifest["cuts"] if row["cut_id"] == cut_id), None)
    if cut is None:
        raise HTTPException(status_code=404, detail="cut is not in finished review manifest")
    artifact = cut["artifacts"][artifact_name]
    review_dir = manifest["_review_dir"].resolve()
    path = Path(artifact["path"]).resolve()
    try:
        path.relative_to(review_dir)
    except ValueError as exc:
        raise HTTPException(
            status_code=403, detail="artifact is outside episode review directory"
        ) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"{artifact_name} artifact is missing")
    if path.stat().st_size != artifact["bytes"]:
        raise HTTPException(
            status_code=409, detail=f"{artifact_name} file size does not match manifest"
        )
    return path, artifact


def _feedback_path(manifest: dict[str, Any]) -> Path:
    """Use the server-owned location; never trust gate.feedback_file."""
    filename = (
        _SHORT_FINISHED_FEEDBACK_FILE
        if manifest.get("review_format") == "short"
        else _FINISHED_FEEDBACK_FILE
    )
    return manifest["_review_dir"] / filename


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_finished_feedback(manifest: dict[str, Any]) -> dict[str, Any]:
    path = _feedback_path(manifest)
    if not path.exists():
        return {
            "schema": _FINISHED_FEEDBACK_SCHEMA,
            "episode_id": manifest["episode_id"],
            "revisions": [],
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=422, detail="finished review feedback is unreadable"
        ) from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != _FINISHED_FEEDBACK_SCHEMA
        or payload.get("episode_id") != manifest["episode_id"]
        or not isinstance(payload.get("revisions"), list)
    ):
        raise HTTPException(
            status_code=422, detail="finished review feedback has an invalid schema"
        )
    return payload


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        json.dump(payload, temporary, ensure_ascii=False, indent=2)
        temporary.write("\n")
        temporary.flush()
        os.fsync(temporary.fileno())
    os.replace(temporary.name, path)


def _verified_editorial_master(episode_dir: Path):
    try:
        master = EditorialMasterRequest(
            episode_dir,
            expected_episode_id=episode_dir.name,
        ).open()
    except EditorialMasterContractError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Editorial Master verification failed: {exc}",
        ) from exc

    candidates_path = episode_dir / "highlights" / "candidates.json"
    try:
        candidates_doc = json.loads(candidates_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"candidates.json is missing or unreadable: {exc}",
        ) from exc
    if not isinstance(candidates_doc, dict):
        raise HTTPException(status_code=422, detail="candidates.json must be an object")
    if candidates_doc.get("editorial_master_lineage") != master.identity():
        raise HTTPException(
            status_code=422,
            detail="candidates.json Editorial Master lineage is stale or mismatched",
        )
    return master


def _context(episode_slug: str) -> dict:
    episode_dir = _episode_dir(episode_slug)
    _verified_editorial_master(episode_dir)
    highlights_dir = episode_dir / "highlights"
    try:
        rows = collect(highlights_dir, "long")
        feedback_audit = load_review_feedback(highlights_dir)
    except HighlightDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    shown = rows[:_MAX_CANDIDATES]
    if not shown:
        raise HTTPException(status_code=422, detail="no long-form candidates available")
    latest = feedback_audit["decisions"][-1] if feedback_audit["decisions"] else {}
    latest_feedback = latest.get("feedback", {}) if isinstance(latest, dict) else {}
    if not isinstance(latest_feedback, dict):
        latest_feedback = {}
    selected_ids = latest.get("selected_ids", []) if isinstance(latest, dict) else []
    if not isinstance(selected_ids, list):
        selected_ids = []
    selected_ids = [value for value in selected_ids if isinstance(value, str)]
    selected_set = set(selected_ids)
    selected_rank = {candidate_id: index + 1 for index, candidate_id in enumerate(selected_ids)}
    for row in shown:
        row["feedback"] = str(latest_feedback.get(row["id"], ""))[:_MAX_FEEDBACK]
        row["selected"] = row["id"] in selected_set
        row["selection_rank"] = selected_rank.get(row["id"])
    try:
        finished_manifest = _load_finished_manifest(episode_slug)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        finished_manifest = None
    return {
        "episode_slug": episode_slug,
        "rows": shown,
        "selected_ids": selected_set,
        "decision_count": len(feedback_audit["decisions"]),
        "finished_review_ready": finished_manifest is not None,
        "asset_version": _SHOSHO_ASSET_VERSION,
    }


def _program_video(episode_slug: str) -> Path:
    episode_dir = _episode_dir(episode_slug)
    return Path(_verified_editorial_master(episode_dir).media_path)


@page_router.get("/{episode_slug}", response_class=HTMLResponse)
async def highlight_review_board(
    request: Request,
    episode_slug: str,
    saved: bool = False,
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/bridge/highlights/{episode_slug}", status_code=302)
    context = _context(episode_slug)
    context["saved"] = saved
    return _templates.TemplateResponse(request, "highlight_review.html", context)


@page_router.get("/{episode_slug}/media")
async def highlight_review_media(
    episode_slug: str,
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        raise HTTPException(status_code=401, detail="authentication required")
    return FileResponse(_program_video(episode_slug), media_type="video/mp4")


@page_router.post("/{episode_slug}/decide")
async def highlight_review_decide(
    request: Request,
    episode_slug: str,
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/highlights", status_code=302)
    context = _context(episode_slug)
    form = await request.form()
    selected_ids = [str(value) for value in form.getlist("candidate_id")]
    selection_order = [value for value in str(form.get("selection_order", "")).split(",") if value]
    if selection_order:
        if len(selection_order) != len(set(selection_order)) or set(selection_order) != set(
            selected_ids
        ):
            raise HTTPException(
                status_code=400, detail="selection order does not match selected candidates"
            )
        selected_ids = selection_order
    if len(selected_ids) != 3 or len(set(selected_ids)) != 3:
        raise HTTPException(
            status_code=400, detail="select exactly three distinct long-form candidates"
        )
    by_id = {row["id"]: row for row in context["rows"]}
    unknown = [candidate_id for candidate_id in selected_ids if candidate_id not in by_id]
    if unknown:
        raise HTTPException(
            status_code=400, detail=f"candidate is not in this review shortlist: {unknown}"
        )

    override_ids = {str(value) for value in form.getlist("override_veto")}
    vetoed = {
        candidate_id
        for candidate_id in selected_ids
        if by_id[candidate_id]["brand_severity"] == "veto"
    }
    if not vetoed.issubset(override_ids):
        raise HTTPException(
            status_code=400, detail="brand-veto candidates require an explicit override_veto"
        )
    feedback: dict[str, str] = {}
    # Keep the editor's rejection rationale too: it is the most useful signal for
    # tuning future shortlists, and the form intentionally exposes it on all cards.
    for candidate_id in by_id:
        value = str(form.get(f"feedback_{candidate_id}", "")).strip()
        if len(value) > _MAX_FEEDBACK:
            raise HTTPException(
                status_code=400,
                detail=f"feedback for {candidate_id} exceeds {_MAX_FEEDBACK} characters",
            )
        if value:
            feedback[candidate_id] = value

    episode_dir = _episode_dir(episode_slug)
    # ``await request.form()`` yields control after the page-context check.  Re-open
    # the trust root immediately before durable writes so a replaced Master or
    # candidates document cannot be copied into winners.json through that gap.
    _verified_editorial_master(episode_dir)
    highlights_dir = episode_dir / "highlights"
    try:
        # Validate and prepare every input before either durable write. Each write
        # itself is atomic; the audit entry preserves earlier decisions.
        write_winners(
            highlights_dir,
            context["rows"],
            selected_ids,
            picked_by="修修 (Bridge highlight review gate)",
        )
        append_review_feedback(
            highlights_dir,
            selected_ids=selected_ids,
            feedback=feedback,
            overridden_veto_ids=sorted(vetoed),
        )
    except HighlightDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedirectResponse(f"/bridge/highlights/{episode_slug}?saved=1", status_code=303)


@page_router.get("/{episode_slug}/finished", response_class=HTMLResponse)
async def finished_review_board(
    request: Request,
    episode_slug: str,
    saved: bool = False,
    approved: bool = False,
    review_format: str = Query("long", alias="format"),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return _login_redirect(request)
    review_format = _review_format(review_format)
    manifest = _load_review_manifest(episode_slug, review_format)
    audit = _read_finished_feedback(manifest)
    revisions = audit["revisions"]
    current_revisions = [
        revision
        for revision in revisions
        if isinstance(revision, dict) and revision.get("manifest_sha256") == manifest["_sha256"]
    ]
    latest = current_revisions[-1] if current_revisions else {}
    cut_statuses = latest.get("cut_statuses", {}) if isinstance(latest, dict) else {}
    if not isinstance(cut_statuses, dict):
        cut_statuses = {}
    component_feedback = latest.get("component_feedback", []) if isinstance(latest, dict) else []
    if not isinstance(component_feedback, list):
        component_feedback = []
    feedback_by_component = {
        row["component_id"]: row
        for row in component_feedback
        if isinstance(row, dict) and isinstance(row.get("component_id"), str)
    }
    for cut in manifest["cuts"]:
        cut["saved_status"] = cut_statuses.get(cut["cut_id"], "pending")
        for component in cut["review_components"]:
            prior = feedback_by_component.get(component["component_id"], {})
            component["saved_action"] = prior.get("action", "")
            component["saved_comment"] = prior.get("comment", "")
            component["saved_replacement"] = prior.get("replacement", "")
            component["saved_move"] = prior.get("move_to_seconds", "")
            component["saved_remember"] = bool(prior.get("remember_preference"))
    return _templates.TemplateResponse(
        request,
        "finished_review.html",
        {
            "episode_slug": episode_slug,
            "manifest": manifest,
            "manifest_sha256": manifest["_sha256"],
            "revision_count": len(revisions),
            "saved": saved,
            "approved": approved,
            "review_format": review_format,
            "review_query": "?format=short" if review_format == "short" else "",
            "asset_version": _SHOSHO_ASSET_VERSION,
        },
    )


@page_router.get("/{episode_slug}/finished/media/{cut_id}")
async def finished_review_media(
    episode_slug: str,
    cut_id: str,
    review_format: str = Query("long", alias="format"),
    nakama_auth: str | None = Cookie(None),
) -> FileResponse:
    _require_auth(nakama_auth)
    review_format = _review_format(review_format)
    manifest = _load_review_manifest(episode_slug, review_format)
    path, _ = _safe_artifact_path(manifest, cut_id, "preview")
    if path.suffix.lower() != ".mp4":
        raise HTTPException(status_code=422, detail="preview artifact must be an mp4")
    return FileResponse(path, media_type="video/mp4")


@page_router.get("/{episode_slug}/finished/subtitles/{cut_id}")
async def finished_review_subtitles(
    episode_slug: str,
    cut_id: str,
    review_format: str = Query("long", alias="format"),
    nakama_auth: str | None = Cookie(None),
) -> Response:
    _require_auth(nakama_auth)
    review_format = _review_format(review_format)
    manifest = _load_review_manifest(episode_slug, review_format)
    path, artifact = _safe_artifact_path(manifest, cut_id, "subtitles")
    if path.suffix.lower() != ".srt":
        raise HTTPException(status_code=422, detail="subtitle artifact must be an srt")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != artifact["sha256"]:
        raise HTTPException(status_code=409, detail="subtitle sha256 does not match manifest")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="subtitle artifact is not UTF-8") from exc
    return Response(srt_to_vtt(text), media_type="text/vtt; charset=utf-8")


def _single_form_value(form: Any, key: str, default: str = "") -> str:
    values = form.getlist(key)
    if len(values) > 1:
        raise HTTPException(status_code=400, detail=f"duplicate form field: {key}")
    return str(values[0]) if values else default


def _find_packaging_episode(episode_id: str, cut_id: str) -> str:
    """Resolve a finished cut to its validated vault packaging directory.

    The human-facing episode folder (for example ``20260721 鄭國威``) and the
    portable vault directory slug are intentionally different.  ``packages.json``
    is the contract joining them; directory-name guessing would silently route a
    decision to the wrong episode.
    """
    root = get_vault_path() / "Attachments" / "packaging"
    if not root.is_dir():
        raise HTTPException(status_code=409, detail="Packaging 尚未產生，先完成標題與封面")
    matches: list[str] = []
    for ep_dir in root.iterdir():
        if not ep_dir.is_dir() or not (ep_dir / "packages.json").is_file():
            continue
        try:
            packages = parse_packages(ep_dir / "packages.json")
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if packages.episode != episode_id:
            continue
        cut = next((row for row in packages.cuts if row.cut_id == cut_id), None)
        if cut is not None and cut.format == "long" and len(cut.packages) == 3:
            matches.append(ep_dir.name)
    if not matches:
        raise HTTPException(
            status_code=409,
            detail=f"{cut_id} 的 Packaging 尚未完成，先產生 3 組標題＋封面",
        )
    if len(matches) > 1:
        raise HTTPException(
            status_code=409,
            detail=f"{episode_id}/{cut_id} 對應到多個 Packaging 目錄：{matches}",
        )
    return matches[0]


def _start_publish_prep(episode_dir: Path, cut_id: str) -> None:
    """Start one full-resolution Resolve export without blocking the review UI."""
    key = (str(episode_dir.resolve()), cut_id)
    running = _PUBLISH_PREP_PROCESSES.get(key)
    root = Path(__file__).resolve().parent.parent.parent
    script = root / "scripts" / "publish_prep.py"
    configured_data = os.environ.get("NAKAMA_DATA_DIR", "").strip()
    data_dir = Path(configured_data) if configured_data else get_db_path().parent
    log_dir = data_dir / "publish_prep"
    log_dir.mkdir(parents=True, exist_ok=True)
    receipt = episode_dir / "highlights" / "exports" / f".publish_prep_{cut_id}.json"
    current = _publish_prep_state(episode_dir, cut_id)
    if current and current.get("status") == "rendered":
        return
    if running is not None and running.poll() is None:
        return
    if current and current.get("status") == "rendering" and not job_expired(current):
        if running is None:
            return
        atomic_job_write(
            receipt,
            {
                **current,
                "status": "failed",
                "exit_code": running.poll(),
                "error": "background child exited",
            },
        )
    job = new_job(
        status="rendering",
        timeout_seconds=_PUBLISH_PREP_TIMEOUT_SECONDS,
        episode=episode_dir.name,
        cut_id=cut_id,
    )
    atomic_job_write(receipt, job)
    safe_episode = episode_dir.name.replace("/", "_").replace("\\", "_")
    log_file = open(  # noqa: SIM115 — Popen duplicates the descriptor
        log_dir / f"{safe_episode}_{cut_id}.log", "a", encoding="utf-8"
    )
    configured = os.environ.get("NAKAMA_RESOLVE_PYTHON", "").strip()
    command = [configured, str(script)] if configured else [sys.executable, str(script)]
    try:
        process = subprocess.Popen(
            [
                *command,
                str(episode_dir),
                "--cut",
                cut_id,
                "--render-only",
                "--receipt",
                str(receipt),
                "--attempt-id",
                str(job["attempt_id"]),
            ],
            cwd=str(root),
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        log_file.close()
        atomic_job_write(
            receipt,
            {**job, "status": "failed", "exit_code": None, "error": f"OSError: {exc}"},
        )
        raise HTTPException(
            status_code=503,
            detail=f"無法啟動最終成品匯出：{exc}",
        ) from exc
    _PUBLISH_PREP_PROCESSES[key] = process
    atomic_job_write(receipt, {**job, "pid": process.pid})
    log_file.close()


@page_router.post("/{episode_slug}/finished/review")
async def finished_review_save(
    request: Request,
    episode_slug: str,
    review_format: str = Query("long", alias="format"),
    nakama_auth: str | None = Cookie(None),
):
    _require_auth(nakama_auth)
    review_format = _review_format(review_format)
    manifest = _load_review_manifest(episode_slug, review_format)
    form = await request.form()
    submitted_manifest_sha = _single_form_value(form, "manifest_sha256")
    if submitted_manifest_sha != manifest["_sha256"]:
        raise HTTPException(status_code=409, detail="manifest changed; reload before saving review")
    submit_action = _single_form_value(form, "submit_action")
    if submit_action not in {"save_draft", "approve_cut", "approve_all"}:
        raise HTTPException(status_code=400, detail="invalid finished review submit action")
    selected_cut_id = _single_form_value(form, "selected_cut_id").strip()

    cuts_by_id = {cut["cut_id"]: cut for cut in manifest["cuts"]}
    components_by_id = {
        component["component_id"]: (cut, component)
        for cut in manifest["cuts"]
        for component in cut["review_components"]
    }
    dynamic_prefixes = (
        "cut_status__",
        "component_action__",
        "component_comment__",
        "component_replacement__",
        "component_move__",
        "component_remember__",
    )
    for key, _ in form.multi_items():
        if key.startswith("cut_status__") and key.removeprefix("cut_status__") not in cuts_by_id:
            raise HTTPException(status_code=400, detail="unknown cut in review form")
        for prefix in dynamic_prefixes[1:]:
            if key.startswith(prefix) and key.removeprefix(prefix) not in components_by_id:
                raise HTTPException(status_code=400, detail="unknown component in review form")

    cut_statuses: dict[str, str] = {}
    for cut_id in cuts_by_id:
        status = _single_form_value(form, f"cut_status__{cut_id}", "pending")
        if status not in {"pending", "approved", "needs_changes"}:
            raise HTTPException(status_code=400, detail=f"invalid review status for {cut_id}")
        cut_statuses[cut_id] = status
    if submit_action == "approve_all" and any(
        status != "approved" for status in cut_statuses.values()
    ):
        raise HTTPException(status_code=400, detail="approve_all requires every cut to be approved")
    packaging_episode: str | None = None
    approved_episode_dir: Path | None = None
    if submit_action == "approve_cut":
        if selected_cut_id not in cuts_by_id:
            raise HTTPException(status_code=400, detail="unknown selected cut")
        if cut_statuses[selected_cut_id] != "approved":
            raise HTTPException(status_code=400, detail="selected cut must be approved")
        approved_episode_dir = _episode_dir(episode_slug)
        _require_final_qa_clear(approved_episode_dir, selected_cut_id)
        packaging_episode = _find_packaging_episode(manifest["episode_id"], selected_cut_id)

    component_feedback: list[dict[str, Any]] = []
    preference_candidates: list[dict[str, Any]] = []
    contract_actions = manifest["feedback_contract"]["component_actions"]
    for component_id, (cut, component) in components_by_id.items():
        action = _single_form_value(form, f"component_action__{component_id}").strip()
        comment = _single_form_value(form, f"component_comment__{component_id}").strip()
        replacement = _single_form_value(form, f"component_replacement__{component_id}").strip()
        move_raw = _single_form_value(form, f"component_move__{component_id}").strip()
        remember = bool(form.getlist(f"component_remember__{component_id}"))
        if not action and not comment and not replacement and not move_raw and not remember:
            continue
        if action not in contract_actions[component["lane"]]:
            raise HTTPException(status_code=400, detail=f"action is not allowed for {component_id}")
        if len(comment) > _MAX_FEEDBACK:
            raise HTTPException(
                status_code=400,
                detail=f"comment for {component_id} exceeds {_MAX_FEEDBACK} characters",
            )
        if len(replacement) > _MAX_REPLACEMENT:
            raise HTTPException(
                status_code=400,
                detail=f"replacement for {component_id} exceeds {_MAX_REPLACEMENT} characters",
            )
        if action in {"replace_asset", "change_type", "edit_text"} and not replacement:
            raise HTTPException(
                status_code=400, detail=f"replacement is required for {component_id}"
            )
        if action == "comment" and not comment:
            raise HTTPException(status_code=400, detail=f"comment is required for {component_id}")
        move_to_seconds: float | None = None
        if action == "move":
            try:
                move_to_seconds = float(move_raw)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400, detail=f"move time is invalid for {component_id}"
                ) from exc
            duration = float(cut["artifacts"]["preview"]["duration_seconds"])
            if not math.isfinite(move_to_seconds) or not 0 <= move_to_seconds <= duration:
                raise HTTPException(
                    status_code=400, detail=f"move time is outside {cut['cut_id']} duration"
                )
        row: dict[str, Any] = {
            "cut_id": cut["cut_id"],
            "component_id": component_id,
            "lane": component["lane"],
            "timeline_seconds": {"t0": component["t0"], "t1": component["t1"]},
            "action": action,
            "comment": comment,
            "replacement": replacement,
            "remember_preference": remember,
        }
        if move_to_seconds is not None:
            row["move_to_seconds"] = move_to_seconds
        component_feedback.append(row)
        if remember:
            preference_candidates.append(
                {
                    "cut_id": cut["cut_id"],
                    "component_id": component_id,
                    "lane": component["lane"],
                    "action": action,
                    "preference": replacement or comment or _ACTION_LABELS[action],
                    "status": "candidate_only",
                    "direct_skill_mutation": False,
                }
            )

    audit = _read_finished_feedback(manifest)
    preview_sha256: dict[str, str] = {}
    for cut in manifest["cuts"]:
        preview_path, preview_artifact = _safe_artifact_path(manifest, cut["cut_id"], "preview")
        actual_sha256 = _file_sha256(preview_path)
        if actual_sha256 != preview_artifact["sha256"]:
            raise HTTPException(
                status_code=409,
                detail=f"preview sha256 does not match manifest for {cut['cut_id']}",
            )
        preview_sha256[cut["cut_id"]] = actual_sha256
    revision = {
        "revision": len(audit["revisions"]) + 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manifest_sha256": manifest["_sha256"],
        "preview_sha256": preview_sha256,
        "decision": (
            "approved_cut"
            if submit_action == "approve_cut"
            else "approved"
            if submit_action == "approve_all"
            else "draft"
        ),
        "cut_statuses": cut_statuses,
        "component_feedback": component_feedback,
        "preference_candidates": preference_candidates,
    }
    if submit_action == "approve_cut":
        revision["selected_cut_id"] = selected_cut_id
    audit["revisions"].append(revision)
    _atomic_json_write(_feedback_path(manifest), audit)
    if submit_action == "approve_cut":
        if approved_episode_dir is None:  # pragma: no cover - guarded by submit_action branch
            raise RuntimeError("approved episode directory was not resolved")
        _start_publish_prep(approved_episode_dir, selected_cut_id)
        return RedirectResponse(
            f"/bridge/packaging/{quote(packaging_episode or '', safe='')}?cut="
            f"{quote(selected_cut_id, safe='')}",
            status_code=303,
        )
    suffix = "approved=1" if submit_action == "approve_all" else "saved=1"
    query = f"format=short&{suffix}" if review_format == "short" else suffix
    return RedirectResponse(f"/bridge/highlights/{episode_slug}/finished?{query}", status_code=303)
