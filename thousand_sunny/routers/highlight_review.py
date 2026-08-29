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
from typing import Any, Mapping
from urllib.parse import quote

from fastapi import APIRouter, Cookie, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from agents.brook.script_video.editorial_master import (
    EditorialMasterContractError,
    EditorialMasterRequest,
)
from agents.brook.script_video.finished_cut_production import (
    FinishedCutInspection,
    build_current_release_reader,
)
from scripts.packaging_manifest import load_manifest, stage_parallel_jobs
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
from thousand_sunny.adapters.finished_cut_review import (
    FinishedCutReviewAdapter,
    ReviewState,
)
from thousand_sunny.auth import check_auth

page_router = APIRouter(prefix="/bridge/highlights", tags=["bridge-highlights"])
_templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates" / "bridge")
)
_MAX_CANDIDATES = 5
_MAX_FEEDBACK = 2000
_MAX_REPLACEMENT = 500
_MAX_OVERALL_FEEDBACK = 5000
_FINISHED_MANIFEST_SCHEMA = "nakama.finished_cut_review_manifest.v3"
_FINISHED_FEEDBACK_SCHEMA = "nakama.finished_cut_review_feedback.v3"
_FINISHED_FEEDBACK_FILE = "finished_review_feedback.v3.json"
_PARALLEL_WORK_PLAN_SCHEMA = "nakama.highlight_parallel_work_plan.v1"
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
    "b_roll": "實拍素材／B-ROLL",
    "identity_card": "人物識別卡／IDENTITY",
    "hero_title": "主標題／HERO",
    "supporting_title": "補充標題／SUPPORTING",
    "badge": "BADGE",
    "fullscreen_transition": "滿版章節卡／CHAPTER",
    "title_card": "字卡與斷句",
    "visual_effect": "ICON／動畫",
    "pacing": "剪輯節奏",
}
_COMPONENT_ACTIONS = {
    "b_roll": ["approve", "remove", "replace_asset", "change_type", "move", "comment"],
    "identity_card": ["approve", "remove", "edit_text", "move", "comment"],
    "hero_title": ["approve", "remove", "edit_text", "move", "comment"],
    "supporting_title": ["approve", "remove", "edit_text", "move", "comment"],
    "fullscreen_transition": ["approve", "remove", "edit_text", "move", "comment"],
    "visual_effect": [
        "approve",
        "remove",
        "replace_asset",
        "change_type",
        "move",
        "comment",
    ],
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


def _visual_time_range(t0: object, t1: object) -> str:
    def stamp(value: object) -> str:
        seconds = float(value)
        minutes, remainder = divmod(seconds, 60)
        return f"{int(minutes):02d}:{remainder:06.3f}"

    return f"{stamp(t0)}–{stamp(t1)}"


def _finished_cut_event_view(cut: dict[str, Any]) -> dict[str, object]:
    """Project only semantic events carried by the sealed current Release."""

    return {
        "status": "sealed_current",
        "status_label": "FINISHED CUT RELEASE · SEALED CURRENT",
        "release_id": cut["release_id"],
        "events": cut["events"],
    }


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


_CURRENT_RELEASE_INSPECTOR_FACTORY = build_current_release_reader


def _release_artifact(artifact: Any) -> dict[str, Any]:
    return {
        "path": artifact.reference,
        "bytes": artifact.bytes,
        "sha256": artifact.sha256,
        "duration_seconds": artifact.duration_sec,
        "probe": dict(artifact.probe),
    }


def _release_component(component: Any) -> dict[str, Any]:
    actions = _COMPONENT_ACTIONS[component.lane]
    return {
        "component_id": component.component_id,
        "event_id": component.event_id,
        "semantic_kind": component.semantic_kind,
        "implementation_kind": component.implementation_kind,
        "lane": component.lane,
        "display": component.display,
        "t0": component.t0,
        "t1": component.t1,
        "asset_ref": component.asset_ref,
        "actions": [{"value": action, "label": _ACTION_LABELS[action]} for action in actions],
    }


def _release_event(event: Any) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "master_cue_ids": list(event.master_cue_ids),
        "text": event.text,
        "text_hash": event.text_hash,
        "t0": event.t0,
        "t1": event.t1,
        "time_range": _visual_time_range(event.t0, event.t1),
        "section_id": event.section_id,
        "intent": event.intent,
        "display": event.display,
        "semantic_kind": event.semantic_kind,
        "implementation_kind": event.implementation_kind,
        "lane": event.lane,
        "asset_ref": event.asset_ref,
        "visual_status": event.visual_status,
        "intentional_aroll": event.intentional_aroll,
    }


def _load_finished_manifest(episode_slug: str) -> dict[str, Any]:
    """Project only the exact current v3 Release index into the Bridge view model."""

    episode_dir = _episode_dir(episode_slug)
    review = FinishedCutReviewAdapter(_CURRENT_RELEASE_INSPECTOR_FACTORY(episode_dir)).load(
        episode_slug
    )
    if review.state is ReviewState.MISSING:
        raise HTTPException(status_code=404, detail="finished cut current Release is missing")
    if review.state is ReviewState.INVALID:
        raise _manifest_error(review.error or "current Release is invalid")

    current_path = episode_dir / "highlights" / "review" / "finished_review_manifest_current.json"
    try:
        current_bytes = current_path.read_bytes()
    except OSError as exc:  # The inspector already proved this path; treat races as drift.
        raise _manifest_error("current Release changed during inspection") from exc

    cuts: list[dict[str, Any]] = []
    for cut in review.cuts:
        if cut.preview.duration_sec is None or cut.preview.duration_sec <= 0:
            raise _manifest_error(f"{cut.cut_id} preview duration is unavailable")
        components = [_release_component(component) for component in cut.components]
        events = [_release_event(event) for event in cut.events]
        stock_video_count = sum(
            component["lane"] == "b_roll" and component["implementation_kind"] == "stock_video"
            for component in components
        )
        cuts.append(
            {
                "release_id": cut.release_id,
                "cut_id": cut.cut_id,
                "format": cut.format,
                "title": cut.cut_id,
                "artifacts": {
                    "preview": _release_artifact(cut.preview),
                    "subtitles": _release_artifact(cut.subtitle),
                },
                "events": events,
                "components": components,
                "review_components": components,
                "component_counts": {
                    lane: sum(component["lane"] == lane for component in components)
                    for lane in _COMPONENT_ACTIONS
                },
                "stock_video_count": stock_video_count,
                "stock_video_missing": max(0, 3 - stock_video_count),
            }
        )
    if not cuts:
        raise _manifest_error("current Release has no cuts")
    lanes = list(_COMPONENT_ACTIONS)
    return {
        "schema": _FINISHED_MANIFEST_SCHEMA,
        "episode_id": episode_slug,
        "stage": 5,
        "gate": {"kind": "finished_cut_review", "status": "ready_for_review"},
        "cuts": cuts,
        "feedback_contract": {
            "review_lanes": lanes,
            "component_actions": _COMPONENT_ACTIONS,
            "gate_actions": ["request_changes", "approve_cut", "approve_all"],
        },
        "lane_labels": {lane: _LANE_LABELS[lane] for lane in lanes},
        "_path": current_path,
        "_review_dir": current_path.parent,
        "_episode_dir": episode_dir,
        "_sha256": hashlib.sha256(current_bytes).hexdigest(),
    }


def _load_review_manifest(episode_slug: str, review_format: str) -> dict[str, Any]:
    manifest = _load_finished_manifest(episode_slug)
    cuts = [cut for cut in manifest["cuts"] if cut["format"] == review_format]
    if not cuts:
        raise HTTPException(
            status_code=404,
            detail=f"finished cut current Release has no {review_format} cuts",
        )
    manifest["cuts"] = cuts
    manifest["review_format"] = review_format
    return manifest


def _safe_artifact_path(
    manifest: dict[str, Any], cut_id: str, artifact_name: str
) -> tuple[Path, dict]:
    cut = next((row for row in manifest["cuts"] if row["cut_id"] == cut_id), None)
    if cut is None:
        raise HTTPException(status_code=404, detail="cut is not in finished review manifest")
    artifact = cut["artifacts"][artifact_name]
    episode_dir = manifest["_episode_dir"].resolve()
    reference = Path(artifact["path"])
    if reference.is_absolute():
        raise HTTPException(status_code=403, detail="artifact reference must be episode-relative")
    path = (episode_dir / reference).resolve()
    try:
        path.relative_to(episode_dir)
    except ValueError as exc:
        raise HTTPException(
            status_code=403, detail="artifact is outside the episode directory"
        ) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"{artifact_name} artifact is missing")
    if path.stat().st_size != artifact["bytes"]:
        raise HTTPException(
            status_code=409, detail=f"{artifact_name} file size does not match manifest"
        )
    if _file_sha256(path) != artifact["sha256"]:
        raise HTTPException(
            status_code=409, detail=f"{artifact_name} sha256 does not match manifest"
        )
    return path, artifact


def _feedback_path(manifest: dict[str, Any]) -> Path:
    """Use the one server-owned v3 location for every current Release format."""
    return manifest["_review_dir"] / _FINISHED_FEEDBACK_FILE


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


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


def _stage_parallel_work_plan(
    episode_dir: Path,
    selected_ids: list[str],
    candidates_by_id: dict[str, dict],
    *,
    dry_run: bool = False,
) -> None:
    """Persist the two approved work branches and mirror them via the D14 writer."""
    path = episode_dir / "highlights" / "packaging-plan.json"
    existing_by_id: dict[str, dict] = {}
    created_at = datetime.now(timezone.utc).isoformat()
    if path.is_file():
        try:
            existing = json.loads(
                path.read_text(encoding="utf-8"), object_pairs_hook=_strict_json_object
            )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(
                status_code=422, detail=f"packaging-plan.json 驗證失敗：{exc}"
            ) from exc
        if (
            not isinstance(existing, dict)
            or existing.get("schema") != _PARALLEL_WORK_PLAN_SCHEMA
            or existing.get("episode_id") != episode_dir.name
            or not isinstance(existing.get("cuts"), list)
        ):
            raise HTTPException(status_code=422, detail="packaging-plan.json schema 不合法")
        for row in existing["cuts"]:
            cut_id = row.get("cut_id") if isinstance(row, dict) else None
            if not isinstance(cut_id, str) or cut_id in existing_by_id:
                raise HTTPException(
                    status_code=422, detail="packaging-plan.json cut_id 重複或不合法"
                )
            existing_by_id[cut_id] = row
        created_at = str(existing.get("created_at") or created_at)

    selected_at = datetime.now(timezone.utc).isoformat()
    jobs: list[dict] = []
    for rank, cut_id in enumerate(selected_ids, start=1):
        previous = existing_by_id.get(cut_id, {})
        video_export_ready = (episode_dir / "highlights" / "exports" / f"{cut_id}.mp4").is_file()
        branches: dict[str, dict] = {}
        for branch in ("video", "packaging"):
            prior_branch = previous.get(branch)
            prior_status = prior_branch.get("status") if isinstance(prior_branch, dict) else None
            if prior_status not in {"queued", "running", "ready", "failed"}:
                prior_status = "ready" if branch == "video" and video_export_ready else "queued"
            branches[branch] = {"status": prior_status}
        jobs.append(
            {
                "cut_id": cut_id,
                "rank": rank,
                "title": candidates_by_id[cut_id].get("title") or cut_id,
                "selected_at": previous.get("selected_at") or selected_at,
                **branches,
            }
        )

    packaging_dirs = _packaging_episode_directories(episode_dir.name)
    if len(packaging_dirs) > 1:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{episode_dir.name} 對應到多個 Packaging 目錄：{[p.name for p in packaging_dirs]}"
            ),
        )
    if packaging_dirs:
        try:
            load_manifest(packaging_dirs[0])
            if not dry_run:
                stage_parallel_jobs(packaging_dirs[0], jobs)
        except (SystemExit, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"manifest.json 驗證失敗：{exc}") from exc

    if dry_run:
        return

    _atomic_json_write(
        path,
        {
            "schema": _PARALLEL_WORK_PLAN_SCHEMA,
            "episode_id": episode_dir.name,
            "created_at": created_at,
            "updated_at": selected_at,
            "cuts": jobs,
        },
    )


def _finished_revision_jobs(
    *,
    manifest: dict[str, Any],
    audit: dict[str, Any],
    component_feedback: list[dict[str, Any]],
    overall_feedback: dict[str, str],
) -> list[dict[str, Any]]:
    """Mint event-scoped v3 queue rows; never authorize a whole-stage rerun."""

    prior_by_id: dict[str, dict[str, Any]] = {}
    for prior in audit.get("revisions", []):
        if not isinstance(prior, dict):
            continue
        for prior_job in prior.get("revision_jobs", []):
            if isinstance(prior_job, dict) and isinstance(prior_job.get("request_id"), str):
                prior_by_id[prior_job["request_id"]] = prior_job

    jobs: list[dict[str, Any]] = []
    for row in component_feedback:
        if row["action"] == "approve":
            continue
        feedback_parts = [f"{_ACTION_LABELS[row['action']]}：{row['display']}"]
        if row["comment"]:
            feedback_parts.append(f"說明：{row['comment']}")
        if row["replacement"]:
            feedback_parts.append(f"替代內容：{row['replacement']}")
        if row.get("move_to_seconds") is not None:
            feedback_parts.append(f"移到：{row['move_to_seconds']:.3f} 秒")
        cut_feedback = overall_feedback.get(row["cut_id"])
        if cut_feedback:
            feedback_parts.append(f"本支整體回饋：{cut_feedback}")
        feedback = "\n".join(feedback_parts)
        authority = {
            "episode_id": manifest["episode_id"],
            "source_manifest_sha256": manifest["_sha256"],
            "release_id": row["release_id"],
            "cut_id": row["cut_id"],
            "event_id": row["event_id"],
            "feedback": feedback,
        }
        canonical = json.dumps(
            authority,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        request_id = f"finished-revision:{hashlib.sha256(canonical).hexdigest()}"
        if request_id in prior_by_id:
            jobs.append(dict(prior_by_id[request_id]))
            continue
        jobs.append(
            {
                "contract": "finished-cut-production-revision.v3",
                "request_id": request_id,
                "status": "queued",
                "command_id": None,
                "production_state": None,
                "reason_code": None,
                "requested_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": None,
                "error": None,
                **authority,
            }
        )
    return jobs


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
    _stage_parallel_work_plan(episode_dir, selected_ids, by_id, dry_run=True)
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
        _stage_parallel_work_plan(episode_dir, selected_ids, by_id)
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
        cut["release_truth"] = _finished_cut_event_view(cut)
        for component in cut["review_components"]:
            prior = feedback_by_component.get(component["component_id"], {})
            component["saved_action"] = prior.get("action", "")
            component["saved_comment"] = prior.get("comment", "")
            component["saved_replacement"] = prior.get("replacement", "")
            component["saved_move"] = prior.get("move_to_seconds", "")
            component["saved_remember"] = bool(prior.get("remember_preference"))
        saved_overall = latest.get("overall_feedback", {}) if isinstance(latest, dict) else {}
        cut["saved_overall_feedback"] = (
            saved_overall.get(cut["cut_id"], "") if isinstance(saved_overall, dict) else ""
        )
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
            "latest_revision": latest,
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


def _packaging_episode_directories(episode_id: str) -> list[Path]:
    """Resolve an episode to validated vault packaging directories."""
    root = get_vault_path() / "Attachments" / "packaging"
    if not root.is_dir():
        return []
    matches: list[Path] = []
    for ep_dir in root.iterdir():
        if not ep_dir.is_dir() or not (ep_dir / "packages.json").is_file():
            continue
        try:
            packages = parse_packages(ep_dir / "packages.json")
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if packages.episode == episode_id:
            matches.append(ep_dir)
    return sorted(matches, key=lambda path: path.name)


def _find_packaging_episode(episode_id: str) -> str:
    """Resolve a finished episode to its validated vault packaging directory.

    The human-facing episode folder (for example ``20260721 鄭國威``) and the
    portable vault directory slug are intentionally different.  ``packages.json``
    is the contract joining them; directory-name guessing would silently route a
    decision to the wrong episode.  The selected Long cut may still be queued:
    finished approval starts its final render and lands on that pending tab.
    """
    matches = _packaging_episode_directories(episode_id)
    if not matches:
        raise HTTPException(
            status_code=409,
            detail=f"{episode_id} 的 Packaging 目錄尚未建立",
        )
    if len(matches) > 1:
        raise HTTPException(
            status_code=409,
            detail=f"{episode_id} 對應到多個 Packaging 目錄：{[p.name for p in matches]}",
        )
    return matches[0].name


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
        "cut_feedback__",
        "component_action__",
        "component_comment__",
        "component_replacement__",
        "component_move__",
        "component_remember__",
    )
    for key, _ in form.multi_items():
        if key.startswith("cut_status__") and key.removeprefix("cut_status__") not in cuts_by_id:
            raise HTTPException(status_code=400, detail="unknown cut in review form")
        if (
            key.startswith("cut_feedback__")
            and key.removeprefix("cut_feedback__") not in cuts_by_id
        ):
            raise HTTPException(status_code=400, detail="unknown cut feedback in review form")
        for prefix in dynamic_prefixes[2:]:
            if key.startswith(prefix) and key.removeprefix(prefix) not in components_by_id:
                raise HTTPException(status_code=400, detail="unknown component in review form")

    cut_statuses: dict[str, str] = {}
    overall_feedback: dict[str, str] = {}
    for cut_id in cuts_by_id:
        status = _single_form_value(form, f"cut_status__{cut_id}", "pending")
        if status not in {"pending", "approved", "needs_changes"}:
            raise HTTPException(status_code=400, detail=f"invalid review status for {cut_id}")
        cut_statuses[cut_id] = status
        raw_feedback = _single_form_value(form, f"cut_feedback__{cut_id}")
        if len(raw_feedback) > _MAX_OVERALL_FEEDBACK:
            raise HTTPException(
                status_code=400,
                detail=f"overall feedback for {cut_id} exceeds {_MAX_OVERALL_FEEDBACK} characters",
            )
        if raw_feedback.strip():
            overall_feedback[cut_id] = raw_feedback
    if submit_action == "approve_all" and any(
        status != "approved" for status in cut_statuses.values()
    ):
        raise HTTPException(status_code=400, detail="approve_all requires every cut to be approved")
    if submit_action == "approve_all":
        blocked = [
            cut_id
            for cut_id, cut in cuts_by_id.items()
            if int(cut.get("stock_video_missing") or 0) > 0
        ]
        if blocked:
            raise HTTPException(
                status_code=409,
                detail=f"long Highlight Stock Video 尚未達 3 個：{', '.join(blocked)}",
            )
    packaging_episode: str | None = None
    approved_episode_dir: Path | None = None
    if submit_action == "approve_cut":
        if selected_cut_id not in cuts_by_id:
            raise HTTPException(status_code=400, detail="unknown selected cut")
        if cut_statuses[selected_cut_id] != "approved":
            raise HTTPException(status_code=400, detail="selected cut must be approved")
        if int(cuts_by_id[selected_cut_id].get("stock_video_missing") or 0) > 0:
            raise HTTPException(
                status_code=409,
                detail=f"{selected_cut_id} Stock Video 尚未達 3 個，不能進 Packaging",
            )
        approved_episode_dir = _episode_dir(episode_slug)
        _require_final_qa_clear(approved_episode_dir, selected_cut_id)
        packaging_episode = _find_packaging_episode(manifest["episode_id"])

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
            "release_id": cut["release_id"],
            "cut_id": cut["cut_id"],
            "component_id": component_id,
            "event_id": component["event_id"],
            "lane": component["lane"],
            "display": component["display"],
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
        "overall_feedback": overall_feedback,
        "preference_candidates": preference_candidates,
    }
    if submit_action == "approve_cut":
        revision["selected_cut_id"] = selected_cut_id
    if submit_action == "save_draft":
        revision_jobs = _finished_revision_jobs(
            manifest=manifest,
            audit=audit,
            component_feedback=component_feedback,
            overall_feedback=overall_feedback,
        )
        if revision_jobs:
            revision["revision_jobs"] = revision_jobs
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
