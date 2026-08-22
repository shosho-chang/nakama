#!/usr/bin/env python3
"""Durable desktop worker for Stage 5 finished-cut revision requests.

Bridge appends a content-addressed ``revision_job`` to the episode-local finished
review feedback file.  This worker is deliberately a separate process: no request
depends on an in-memory FastAPI background task, and a failed revision restores the
previous preview/manifest before reporting ``failed``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_FEEDBACK_FILES = (
    "finished_review_feedback.v1.json",
    "short_finished_review_feedback.v1.json",
)
_TRUSTED_HANDOFF_ROOT = Path("highlights") / "revision-inputs"
_TRUSTED_HANDOFF_POINTER = _TRUSTED_HANDOFF_ROOT / "current.json"

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _json_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_trusted_asset_sources(
    sources: object,
    *,
    asset_path: Callable[[str], Path],
) -> tuple[dict, dict[str, Path]]:
    """Freshly validate a canonical acquisition map and every named media file."""
    from agents.brook.script_video.highlight_broll import probe_stock_video

    if not isinstance(sources, dict) or not sources:
        raise RuntimeError("trusted asset sources must be a non-empty object")
    normalized: dict = {}
    paths: dict[str, Path] = {}
    for slug, row in sorted(sources.items()):
        if not isinstance(slug, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", slug):
            raise RuntimeError("trusted asset source slug is unsafe")
        if not isinstance(row, dict):
            raise RuntimeError(f"trusted asset source is not an object: {slug}")
        filename = row.get("filename")
        sha256 = row.get("sha256")
        size = row.get("bytes")
        provenance = row.get("provenance")
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or Path(filename).stem != slug
            or not isinstance(sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", sha256)
            or not isinstance(size, int)
            or size <= 0
            or not isinstance(provenance, dict)
        ):
            raise RuntimeError(f"trusted asset source schema is invalid: {slug}")
        for key in ("source_url",):
            parsed = urlparse(str(provenance.get(key) or ""))
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise RuntimeError(f"trusted asset provenance URL is invalid: {slug}.{key}")
        acquired_at = str(provenance.get("acquired_at") or "")
        try:
            acquired = datetime.fromisoformat(acquired_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuntimeError(f"trusted asset acquired_at is invalid: {slug}") from exc
        if acquired.tzinfo is None:
            raise RuntimeError(f"trusted asset acquired_at lacks timezone: {slug}")
        evidence = [
            provenance.get("license_url"),
            provenance.get("terms_url"),
            provenance.get("license_id"),
        ]
        if not any(evidence):
            raise RuntimeError(f"trusted asset license evidence is missing: {slug}")
        for key in ("license_url", "terms_url"):
            if provenance.get(key):
                parsed = urlparse(str(provenance[key]))
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    raise RuntimeError(
                        f"trusted asset provenance URL is invalid: {slug}.{key}"
                    )
        raw_media = asset_path(filename)
        if raw_media.is_symlink():
            raise RuntimeError(f"trusted asset file is missing or unsafe: {filename}")
        media = raw_media.resolve()
        if not media.is_file():
            raise RuntimeError(f"trusted asset file is missing or unsafe: {filename}")
        if media.stat().st_size != size or _sha256(media) != sha256:
            raise RuntimeError(f"trusted asset file hash/size mismatch: {filename}")
        probe_stock_video(media)
        normalized[slug] = dict(row)
        paths[slug] = media
    return normalized, paths


def prepare_trusted_asset_handoff(
    episode_dir: Path,
    source_manifest: Path,
    *,
    apply: bool,
) -> dict:
    """Validate an acquisition result and optionally stage it inside one episode."""
    source_manifest = source_manifest.resolve()
    try:
        raw = json.loads(source_manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"trusted asset sources JSON is unreadable: {source_manifest}") from exc
    source_root = source_manifest.parent.resolve()

    def external_asset(filename: str) -> Path:
        candidate = (source_root / filename).resolve()
        try:
            candidate.relative_to(source_root)
        except ValueError as exc:
            raise RuntimeError("trusted asset source escaped its manifest directory") from exc
        return candidate

    sources, paths = _validate_trusted_asset_sources(raw, asset_path=external_asset)
    sources_sha256 = _json_sha256(sources)
    relative_root = _TRUSTED_HANDOFF_ROOT / sources_sha256
    target_root = _contained(
        episode_dir / relative_root,
        episode_dir,
        "trusted acquisition handoff",
    )
    if apply:
        assets_root = target_root / "assets"
        assets_root.mkdir(parents=True, exist_ok=True)
        for slug, source in paths.items():
            destination = assets_root / sources[slug]["filename"]
            if destination.exists() and (
                destination.stat().st_size != source.stat().st_size
                or _sha256(destination) != _sha256(source)
            ):
                raise RuntimeError(f"trusted handoff destination drifted: {destination.name}")
            if not destination.exists():
                shutil.copy2(source, destination)
        _atomic_json(target_root / "trusted_asset_sources.json", sources)
        _atomic_json(
            episode_dir / _TRUSTED_HANDOFF_POINTER,
            {
                "contract": "finished-revision-trusted-assets-v1",
                "sources_sha256": sources_sha256,
                "root": relative_root.as_posix(),
            },
        )
    return {
        "sources": sources,
        "sources_sha256": sources_sha256,
        "handoff": {
            "contract": "finished-revision-trusted-assets-v1",
            "sources_sha256": sources_sha256,
            "root": relative_root.as_posix(),
        },
        "asset_count": len(sources),
    }


def load_episode_trusted_asset_handoff(episode_dir: Path) -> dict | None:
    pointer_path = episode_dir / _TRUSTED_HANDOFF_POINTER
    if not pointer_path.is_file():
        return None
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("episode trusted asset handoff pointer is unreadable") from exc
    if pointer.get("contract") != "finished-revision-trusted-assets-v1":
        raise RuntimeError("episode trusted asset handoff contract drift")
    root = _contained(
        episode_dir / str(pointer.get("root") or ""),
        episode_dir / _TRUSTED_HANDOFF_ROOT,
        "episode trusted asset handoff root",
    )
    manifest = root / "trusted_asset_sources.json"
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("episode trusted asset handoff manifest is unreadable") from exc
    sources, _paths = _validate_trusted_asset_sources(
        raw, asset_path=lambda filename: root / "assets" / filename
    )
    sources_sha256 = _json_sha256(sources)
    if sources_sha256 != pointer.get("sources_sha256") or root.name != sources_sha256:
        raise RuntimeError("episode trusted asset handoff hash mismatch")
    return {"sources": sources, "sources_sha256": sources_sha256, "handoff": pointer}


def revision_requires_stock_assets(
    episode_dir: Path, manifest_path: Path, requested_cut_ids: list[str]
) -> bool:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("cannot inspect finished manifest for Stock Video bootstrap") from exc
    cuts = {
        row.get("cut_id"): row
        for row in manifest.get("cuts", [])
        if isinstance(row, dict) and isinstance(row.get("cut_id"), str)
    }
    for cut_id in requested_cut_ids:
        cut = cuts.get(cut_id, {})
        if str(cut.get("format") or "long") != "long":
            continue
        components = cut.get("components") if isinstance(cut, dict) else None
        stock_count = (
            int(cut.get("stock_video_count"))
            if isinstance(cut.get("stock_video_count"), int)
            else sum(
                1
                for component in components or []
                if isinstance(component, dict)
                and component.get("asset_category") == "stock_video"
            )
        )
        if stock_count < 3:
            return True
    return False


def _atomic_json(path: Path, payload: dict) -> None:
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


def _load_feedback(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "nakama.finished_cut_review_feedback.v1"
        or not isinstance(payload.get("revisions"), list)
    ):
        raise RuntimeError(f"invalid finished review feedback: {path}")
    return payload


def pending_revision_jobs(episodes_root: Path) -> list[dict]:
    """Return each queued content-addressed request once, newest revision first."""
    found: list[dict] = []
    seen: set[str] = set()
    if not episodes_root.is_dir():
        return found
    feedback_paths: list[Path] = []
    for filename in _FEEDBACK_FILES:
        feedback_paths.extend(episodes_root.glob(f"*/highlights/review/{filename}"))
    for feedback_path in sorted(feedback_paths):
        try:
            audit = _load_feedback(feedback_path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, RuntimeError):
            continue
        for index in range(len(audit["revisions"]) - 1, -1, -1):
            revision = audit["revisions"][index]
            job = revision.get("revision_job") if isinstance(revision, dict) else None
            if not isinstance(job, dict) or job.get("status") != "queued":
                continue
            request_id = job.get("request_id")
            if not isinstance(request_id, str) or not request_id or request_id in seen:
                continue
            seen.add(request_id)
            found.append(
                {
                    "episode_dir": feedback_path.parents[2],
                    "review_dir": feedback_path.parent,
                    "feedback_path": feedback_path,
                    "revision_index": index,
                    "request_id": request_id,
                    "job": job,
                }
            )
    return found


def reconcile_missing_revision_job(
    episodes_root: Path,
    *,
    episode_id: str,
    apply: bool = False,
    trusted_asset_sources: Path | None = None,
) -> dict:
    """Prepare one latest pre-cutover draft for the durable worker queue.

    This is intentionally episode-explicit and never runs from the normal scan.
    It exists for feedback saved immediately before deployment: an operator can
    first inspect the deterministic dry-run, then apply it without asking the user
    to re-enter or re-save their feedback.
    """
    episode_dir = _contained(episodes_root / episode_id, episodes_root, "reconcile episode")
    if not episode_dir.is_dir() or episode_dir.name != episode_id:
        raise RuntimeError(f"episode not found for reconcile: {episode_id}")
    review_dir = episode_dir / "highlights" / "review"
    available = [review_dir / name for name in _FEEDBACK_FILES if (review_dir / name).is_file()]
    if len(available) != 1:
        raise RuntimeError(
            f"reconcile requires exactly one finished feedback file, found {len(available)}"
        )
    feedback_path = available[0]
    audit = _load_feedback(feedback_path)
    if audit.get("episode_id") != episode_id or not audit["revisions"]:
        raise RuntimeError("reconcile feedback episode/revisions are invalid")
    revision = audit["revisions"][-1]
    if not isinstance(revision, dict) or revision.get("decision") != "draft":
        raise RuntimeError("latest finished feedback is not a draft")
    if trusted_asset_sources is not None:
        trusted_handoff = prepare_trusted_asset_handoff(
            episode_dir, trusted_asset_sources, apply=apply
        )
    else:
        trusted_handoff = load_episode_trusted_asset_handoff(episode_dir)
    existing = revision.get("revision_job")
    if isinstance(existing, dict) and not (
        existing.get("status") == "awaiting_stock_assets" and trusted_handoff is not None
    ):
        return {
            "episode_id": episode_id,
            "status": (
                "awaiting_stock_assets"
                if existing.get("status") == "awaiting_stock_assets"
                else "already_queued"
            ),
            "request_id": existing.get("request_id"),
        }
    component_feedback = revision.get("component_feedback") or []
    overall_feedback = revision.get("overall_feedback") or {}
    cut_statuses = revision.get("cut_statuses") or {}
    if (
        not isinstance(component_feedback, list)
        or not isinstance(overall_feedback, dict)
        or not isinstance(cut_statuses, dict)
    ):
        raise RuntimeError("latest finished feedback fields are invalid")
    all_cut_ids = set(cut_statuses) | set(overall_feedback) | {
        row.get("cut_id")
        for row in component_feedback
        if isinstance(row, dict) and isinstance(row.get("cut_id"), str)
    }
    requested_cut_ids = sorted(
        cut_id
        for cut_id in all_cut_ids
        if cut_statuses.get(cut_id) == "needs_changes"
        or cut_id in overall_feedback
        or any(
            isinstance(row, dict) and row.get("cut_id") == cut_id
            for row in component_feedback
        )
    )
    if not requested_cut_ids:
        raise RuntimeError("latest finished feedback contains no requested changes")
    source_manifest_sha = revision.get("manifest_sha256")
    manifest_matches = [
        path
        for path in sorted(review_dir.glob("finished_review_manifest_*.json"))
        if path.is_file() and _sha256(path) == source_manifest_sha
    ]
    if len(manifest_matches) != 1:
        raise RuntimeError(
            "cannot bind legacy feedback to exactly one unchanged finished manifest"
        )
    preview_sha256 = revision.get("preview_sha256")
    if not isinstance(preview_sha256, dict) or any(
        not isinstance(preview_sha256.get(cut_id), str) for cut_id in requested_cut_ids
    ):
        raise RuntimeError("legacy feedback preview hashes are incomplete")
    request = {
        "episode_id": episode_id,
        "review_format": "short" if feedback_path.name.startswith("short_") else "long",
        "manifest_filename": manifest_matches[0].name,
        "source_manifest_sha256": source_manifest_sha,
        "source_preview_sha256": preview_sha256,
        "requested_cut_ids": requested_cut_ids,
        "cut_statuses": cut_statuses,
        "component_feedback": component_feedback,
        "overall_feedback": overall_feedback,
    }
    if trusted_handoff is not None:
        request["trusted_asset_sources"] = trusted_handoff["sources"]
        request["trusted_asset_sources_sha256"] = trusted_handoff["sources_sha256"]
        request["trusted_asset_handoff"] = trusted_handoff["handoff"]
    needs_stock_assets = revision_requires_stock_assets(
        episode_dir, manifest_matches[0], requested_cut_ids
    )
    job_status = (
        "awaiting_stock_assets"
        if needs_stock_assets and trusted_handoff is None
        else "queued"
    )
    canonical = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    request_id = f"finished-revision-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:20]}"
    result = {
        "episode_id": episode_id,
        "status": job_status if job_status == "awaiting_stock_assets" else "would_queue",
        "request_id": request_id,
        "requested_cut_ids": requested_cut_ids,
        "feedback_path": str(feedback_path),
    }
    if trusted_handoff is not None:
        result["trusted_asset_sources_sha256"] = trusted_handoff["sources_sha256"]
        result["trusted_asset_count"] = trusted_handoff.get(
            "asset_count", len(trusted_handoff["sources"])
        )
    if not apply:
        return result
    revision["overall_feedback"] = overall_feedback
    revision["revision_job"] = {
        "contract": "finished-cut-revision-job-v1",
        "request_id": request_id,
        "status": job_status,
        "attempt": 0,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "started_at": None,
        "finished_at": None,
        "result_receipt": None,
        "error": None,
        **request,
    }
    _atomic_json(feedback_path, audit)
    result["status"] = job_status
    return result


def _retry_target_inventory(episode_dir: Path, review_dir: Path, allowed: dict) -> dict:
    """Rebuild the exact filesystem inventory covered by a revision rollback."""
    inventory: dict[str, dict[str, int | str]] = {}
    tree_keys = (
        "trusted_output_review_cut_dirs",
        "trusted_output_stills_visual_dirs",
    )
    tree_relatives: list[str] = []
    for key in tree_keys:
        values = allowed.get(key)
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise RuntimeError(f"failed revision request has invalid {key}")
        tree_relatives.extend(values)
    assets_relative = allowed.get("new_broll_assets_dir")
    if not isinstance(assets_relative, str):
        raise RuntimeError("failed revision request has invalid B-roll asset directory")
    tree_relatives.append(assets_relative)
    for relative in tree_relatives:
        target = _contained(episode_dir / relative, episode_dir, "retry rollback target")
        inventory.update(_tree_inventory(target, relative_to=episode_dir))

    for key in ("tighten_files", "trusted_output_receipts"):
        values = allowed.get(key)
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise RuntimeError(f"failed revision request has invalid {key}")
        for relative in values:
            target = _contained(episode_dir / relative, episode_dir, "retry rollback file")
            if target.is_file():
                inventory.update(_tree_inventory(target, relative_to=episode_dir))
    for manifest in sorted(review_dir.glob("finished_review_manifest_*.json")):
        inventory.update(_tree_inventory(manifest, relative_to=episode_dir))
    return inventory


def retry_failed_revision_job(
    episodes_root: Path,
    *,
    episode_id: str,
    request_id: str,
    apply: bool = False,
) -> dict:
    """Requeue one failed request only after proving rollback restored its inputs."""
    if Path(episode_id).name != episode_id or not re.fullmatch(r"[A-Za-z0-9._-]+", request_id):
        raise RuntimeError("retry episode or request id is unsafe")
    episodes_root = Path(episodes_root)
    episode_dir = _contained(episodes_root / episode_id, episodes_root, "retry episode")
    review_dir = episode_dir / "highlights" / "review"
    feedback_paths = [review_dir / name for name in _FEEDBACK_FILES]
    feedback_paths = [path for path in feedback_paths if path.is_file()]
    if len(feedback_paths) != 1:
        raise RuntimeError("retry requires exactly one finished review feedback file")
    feedback_path = feedback_paths[0]
    audit = _load_feedback(feedback_path)
    matches = [
        (index, revision, revision.get("revision_job"))
        for index, revision in enumerate(audit["revisions"])
        if isinstance(revision, dict)
        and isinstance(revision.get("revision_job"), dict)
        and revision["revision_job"].get("request_id") == request_id
    ]
    if len(matches) != 1:
        raise RuntimeError("retry request id must identify exactly one revision")
    index, _revision, job = matches[0]
    if job.get("status") != "failed":
        raise RuntimeError("only a failed finished revision job can be retried")
    attempt = job.get("attempt")
    if not isinstance(attempt, int) or attempt < 1:
        raise RuntimeError("failed revision has no completed attempt")
    request_root = _contained(
        review_dir / "revisions" / request_id,
        review_dir / "revisions",
        "retry request root",
    )
    attempt_dir = request_root if attempt == 1 else request_root / "attempts" / str(attempt)
    try:
        request = json.loads((attempt_dir / "request.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("failed revision request receipt is unreadable") from exc
    if request.get("request_id") != request_id:
        raise RuntimeError("failed revision request receipt does not match job")
    pre_snapshot = request.get("pre_snapshot")
    allowed = request.get("allowed_changes")
    if not isinstance(pre_snapshot, dict) or not isinstance(allowed, dict):
        raise RuntimeError("failed revision lacks a rollback snapshot")
    current_inventory = _retry_target_inventory(episode_dir, review_dir, allowed)
    if current_inventory != pre_snapshot:
        raise RuntimeError("failed revision rollback is not clean; refusing retry")

    manifest_name = job.get("manifest_filename")
    if not isinstance(manifest_name, str) or Path(manifest_name).name != manifest_name:
        raise RuntimeError("failed revision manifest filename is invalid")
    manifest_path = _contained(review_dir / manifest_name, review_dir, "retry source manifest")
    if _sha256(manifest_path) != job.get("source_manifest_sha256"):
        raise RuntimeError("failed revision source manifest was not restored")
    requested = job.get("requested_cut_ids")
    if not isinstance(requested, list) or not requested:
        raise RuntimeError("failed revision has no requested cuts")
    source_cuts = _manifest_cuts(manifest_path, review_dir, requested)
    for cut_id in requested:
        preview = Path(source_cuts[cut_id]["artifacts"]["preview"]["path"])
        if _sha256(preview) != job.get("source_preview_sha256", {}).get(cut_id):
            raise RuntimeError(f"failed revision preview was not restored: {cut_id}")

    result_relative = job.get("result_receipt")
    if not isinstance(result_relative, str):
        raise RuntimeError("failed revision has no failure receipt")
    result_path = _contained(review_dir / result_relative, review_dir, "retry failure receipt")
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("failed revision failure receipt is unreadable") from exc
    if result.get("request_id") != request_id or result.get("status") != "failed":
        raise RuntimeError("failed revision failure receipt does not match job")
    response = {
        "status": "would_retry",
        "episode_id": episode_id,
        "request_id": request_id,
        "previous_attempt": attempt,
        "previous_result_receipt": result_relative,
        "rollback_verified": True,
    }
    if not apply:
        return response
    previous_receipts = job.get("previous_result_receipts")
    if previous_receipts is None:
        previous_receipts = []
    if not isinstance(previous_receipts, list) or not all(
        isinstance(value, str) for value in previous_receipts
    ):
        raise RuntimeError("failed revision previous receipt history is invalid")
    if result_relative not in previous_receipts:
        previous_receipts = [*previous_receipts, result_relative]
    work = {
        "episode_dir": episode_dir,
        "review_dir": review_dir,
        "feedback_path": feedback_path,
        "revision_index": index,
        "request_id": request_id,
        "job": job,
    }
    _update_job(
        work,
        {
            "status": "queued",
            "started_at": None,
            "finished_at": None,
            "result_receipt": None,
            "error": None,
            "retry_requested_at": datetime.now(timezone.utc).isoformat(),
            "previous_result_receipts": previous_receipts,
        },
        required_status="failed",
    )
    response["status"] = "queued"
    return response


def _update_job(
    work: dict,
    updates: dict,
    *,
    required_status: str | None = None,
) -> dict:
    feedback_path = Path(work["feedback_path"])
    audit = _load_feedback(feedback_path)
    index = int(work["revision_index"])
    try:
        revision = audit["revisions"][index]
        current = revision["revision_job"]
    except (IndexError, KeyError, TypeError) as exc:
        raise RuntimeError("finished revision disappeared while worker was running") from exc
    if current.get("request_id") != work["request_id"]:
        raise RuntimeError("finished revision request changed while worker was running")
    if required_status is not None and current.get("status") != required_status:
        raise RuntimeError(
            f"finished revision is {current.get('status')!r}, expected {required_status!r}"
        )
    current.update(updates)
    revision["revision_job"] = current
    _atomic_json(feedback_path, audit)
    return current


def _contained(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise RuntimeError(f"{label} escapes finished review directory") from exc
    return resolved


def _manifest_cuts(manifest_path: Path, review_dir: Path, requested: list[str]) -> dict[str, dict]:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("revision output manifest is unreadable") from exc
    if payload.get("schema") != "nakama.finished_cut_review_manifest.v1":
        raise RuntimeError("revision output manifest schema is invalid")
    cuts = {
        row.get("cut_id"): row
        for row in payload.get("cuts", [])
        if isinstance(row, dict) and isinstance(row.get("cut_id"), str)
    }
    missing = sorted(set(requested) - cuts.keys())
    if missing:
        raise RuntimeError(f"revision output manifest is missing cuts: {', '.join(missing)}")
    for cut_id in requested:
        artifacts = cuts[cut_id].get("artifacts")
        if not isinstance(artifacts, dict):
            raise RuntimeError(f"revision output lacks artifacts for {cut_id}")
        for name in ("preview", "subtitles"):
            receipt = artifacts.get(name)
            if not isinstance(receipt, dict) or not isinstance(receipt.get("path"), str):
                raise RuntimeError(f"revision output lacks {name} for {cut_id}")
            artifact = _contained(Path(receipt["path"]), review_dir, f"{cut_id} {name}")
            if not artifact.is_file():
                raise RuntimeError(f"revision output {name} is missing for {cut_id}")
            if receipt.get("bytes") != artifact.stat().st_size or receipt.get("sha256") != _sha256(
                artifact
            ):
                raise RuntimeError(f"revision output {name} receipt mismatch for {cut_id}")
    return cuts


def _codex_command() -> str:
    if os.name == "nt":
        local_bin = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI" / "Codex" / "bin"
        candidates = sorted(
            local_bin.glob("*/codex.exe"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            return str(candidates[0])
        npm = Path(os.environ.get("APPDATA", "")) / "npm" / "codex.cmd"
        if npm.is_file():
            return str(npm)
    found = shutil.which("codex")
    if not found:
        raise RuntimeError("找不到 Codex CLI，無法啟動 finished-cut revision agent")
    return found


def dispatch_revision_agent(context: dict) -> subprocess.CompletedProcess[str]:
    """Dispatch one bounded agent whose only writable root is its job directory."""
    job_dir = Path(context["job_dir"])
    output_root = _contained(Path(context["output_root"]), job_dir, "agent output root")
    output_root.mkdir(parents=True, exist_ok=True)
    prompt = f"""你是 Podcast Finished-cut Revision Agent。

請讀 `{context['request_path']}`，逐項處理 component_feedback 與 overall_feedback。
唯一可寫位置是 `{output_root}`；episode 與 repo 只能讀，禁止直接寫入。
依 request.allowed_changes 只產生 staged inputs：tighten 內的 plan/recipe JSON，以及
request.trusted_asset_sources 已 hash-bound 的新素材。不得自報或更改素材授權與來源。
不得產生 review preview、events、
manifest、materialization receipt 或 stills；這些只能由 worker 的可信任程式產生。

不得連線或操作 Resolve；Timeline 修改只由 worker 之後的 trusted apply 執行。
嚴禁修改 Editorial Master、完整節目 Timeline、其他 cut Timeline、其他 cut 檔案、repo code、
feedback JSON、packaging、字幕 release、上傳或 YouTube 狀態。

使用 repo 既有 schema 作為唯讀參考；你的產出只是計畫與輸入，不是執行成功的證據。
Hero Title replacement 內的
    換行是版面指令，必須原樣保留。不可自行核准；若無法確實重建，非零退出。
    完成摘要直接作為最後回覆。
"""
    command = [
        _codex_command(),
        "exec",
        "--ephemeral",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "--cd",
        str(output_root),
        "--output-last-message",
        str(job_dir / "agent-last-message.txt"),
        "-",
    ]
    child_env = os.environ.copy()
    for name in (
        "CODEX_PERMISSION_PROFILE",
        "CODEX_SANDBOX_NETWORK_DISABLED",
        "CODEX_SESSION_ID",
        "CODEX_THREAD_ID",
    ):
        child_env.pop(name, None)
    result = subprocess.run(
        command,
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=output_root,
        env=child_env,
        shell=(os.name == "nt"),
        timeout=7200,
    )
    return result


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _replace_tree(source: Path, destination: Path) -> None:
    _remove_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)


def _tree_inventory(path: Path, *, relative_to: Path) -> dict[str, dict[str, int | str]]:
    if not path.exists():
        return {}
    files = [path] if path.is_file() else sorted(item for item in path.rglob("*") if item.is_file())
    return {
        item.resolve().relative_to(relative_to.resolve()).as_posix(): {
            "bytes": item.stat().st_size,
            "sha256": _sha256(item),
        }
        for item in files
    }


def _allowed_changes(
    episode_dir: Path,
    review_dir: Path,
    manifest_path: Path,
    requested: list[str],
    output_root: Path,
    trusted_asset_sources: dict,
) -> dict:
    recipes = [
        f"{cut_id}{suffix}"
        for cut_id in requested
        for suffix in (
            "_broll.json",
            "_cuts.json",
            "_titles.json",
        )
    ]
    timelines: dict[str, str] = {}
    for cut_id in requested:
        events_path = review_dir / cut_id / "events.json"
        try:
            events = json.loads(events_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"cannot bind derived Resolve Timeline for {cut_id}") from exc
        timeline = events.get("timeline")
        if not isinstance(timeline, str) or not timeline.strip():
            raise RuntimeError(f"review events lack derived Resolve Timeline for {cut_id}")
        timelines[cut_id] = timeline
    return {
        "requested_cut_ids": requested,
        "agent_output_root": str(output_root),
        "trusted_output_review_cut_dirs": [
            str((review_dir / cut_id).relative_to(episode_dir)) for cut_id in requested
        ],
        "tighten_files": [str(Path("highlights/tighten") / name) for name in recipes],
        "trusted_output_receipts": [
            *[
                str(
                    Path("highlights/tighten")
                    / f"{cut_id}_broll_materialization.json"
                )
                for cut_id in requested
            ],
            *[
                str(Path("highlights/materialization") / f"{cut_id}.json")
                for cut_id in requested
            ],
        ],
        "trusted_output_stills_visual_dirs": [
            str(Path("highlights/stills") / f"{cut_id}-visuals") for cut_id in requested
        ],
        "new_broll_assets_dir": "assets/broll",
        "new_broll_assets_policy": "new-files-only-no-overwrite",
        "trusted_asset_sources": trusted_asset_sources,
        "trusted_asset_sources_sha256": _json_sha256(trusted_asset_sources),
        "trusted_asset_sources_contract": {
            "key": "stock-video slug",
            "required": ["filename", "bytes", "sha256", "provenance"],
            "staged_asset_path": "output/assets/broll/<filename>",
            "staged_plan_path": "output/tighten/<cut_id>_broll.json",
            "plan_rule": "video item slug and provenance must exactly match this map",
        },
        "finished_manifest": str(manifest_path.relative_to(episode_dir)),
        "resolve_timelines": timelines,
        "trusted_operations": [
            "duplicate_and_swap_derived_timeline",
            "run_short_director.direct",
            "run_short_broll.apply",
            "run_short_titles.apply",
            "run_short_review.build_packet",
            "build_finished_review_manifest.verify_finished_review_cut",
            "commit_timeline_swap_after_verification",
        ],
        "forbidden": [
            "editorial-master",
            "full episode Timeline/media",
            "other cuts",
            "packaging",
            "upload/YouTube",
            "repo code",
        ],
    }


def _authoritative_output_verifier(context: dict) -> dict:
    from scripts.build_finished_review_manifest import build_manifest, verify_finished_review_cut

    episode_dir = Path(context["episode_dir"])
    request = context["request"]
    try:
        manifest_path = build_manifest(
            episode_dir, review_format=str(request["review_format"])
        )
        results: list[dict] = []
        for cut_id in request["requested_cut_ids"]:
            rows = [
                row
                for row in request.get("component_feedback", [])
                if isinstance(row, dict) and row.get("cut_id") == cut_id
            ]
            result = verify_finished_review_cut(
                episode_dir,
                cut_id,
                manifest_path,
                feedback_rows=rows,
                source_preview_sha256=request["source_preview_sha256"][cut_id],
                require_preview_change=True,
            )
            results.append(result)
    except SystemExit as exc:
        raise RuntimeError(str(exc)) from exc
    manifest_hashes = {row["manifest_sha256"] for row in results}
    if len(manifest_hashes) != 1:
        raise RuntimeError("authoritative verifier returned inconsistent manifest hashes")
    return {
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_hashes.pop(),
        "preview_sha256": {row["cut_id"]: row["preview_sha256"] for row in results},
        "cut_results": results,
        "approved": False,
    }


def _verify_revision_output_acceptance(context: dict, verification: dict) -> dict:
    """Run every output/hash postcondition while the original Timeline still exists."""
    request = context["request"]
    review_dir = Path(context["review_dir"])
    requested = request["requested_cut_ids"]
    manifest_path = _contained(
        Path(verification.get("manifest_path", context["manifest_path"])),
        review_dir,
        "verified manifest",
    )
    output_cuts = _manifest_cuts(manifest_path, review_dir, requested)
    manifest_sha256 = _sha256(manifest_path)
    if manifest_sha256 == request["source_manifest_sha256"]:
        raise RuntimeError("trusted apply did not rebuild the finished review manifest")
    if verification.get("manifest_sha256") not in {None, manifest_sha256}:
        raise RuntimeError("authoritative verifier manifest hash mismatch")
    preview_sha256: dict[str, str] = {}
    verified_previews = verification.get("preview_sha256")
    for cut_id in requested:
        preview = Path(output_cuts[cut_id]["artifacts"]["preview"]["path"])
        preview_sha256[cut_id] = _sha256(preview)
        if preview_sha256[cut_id] == request["source_preview_sha256"].get(cut_id):
            raise RuntimeError(f"trusted apply did not rebuild preview: {cut_id}")
        if isinstance(verified_previews, dict) and verified_previews.get(cut_id) not in {
            None,
            preview_sha256[cut_id],
        }:
            raise RuntimeError(f"authoritative verifier preview hash mismatch: {cut_id}")
    return {
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_sha256,
        "preview_sha256": preview_sha256,
    }


def _verified_current_asset_sources(
    episode_dir: Path, requested: list[str], supplied: object
) -> dict:
    """Bind current verified Stock Video provenance plus upstream supplied assets."""
    sources = dict(supplied) if isinstance(supplied, dict) else {}
    from agents.brook.script_video.highlight_broll import verify_broll_receipt
    from scripts.run_short_broll import _load_winner, _open_editorial_master

    master = None
    for cut_id in requested:
        plan_path = episode_dir / "highlights" / "tighten" / f"{cut_id}_broll.json"
        receipt_path = (
            episode_dir
            / "highlights"
            / "tighten"
            / f"{cut_id}_broll_materialization.json"
        )
        if not plan_path.is_file():
            continue
        if not receipt_path.is_file():
            # A zero/deficit legacy plan is the bootstrap case this revision is
            # meant to repair.  It grants no authority, but must not deadlock
            # request-bound new assets before the agent can produce a valid plan.
            continue
        if master is None:
            master = _open_editorial_master(episode_dir)
        try:
            items = json.loads(plan_path.read_text(encoding="utf-8"))["items"]
            candidate, _winner = _load_winner(
                episode_dir, cut_id, master.identity()
            )
            receipt = verify_broll_receipt(
                episode_dir,
                cut_id,
                str(candidate["format"]),
                items,
                master.identity(),
            )
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
            SystemExit,
        ):
            # Invalid/legacy receipts confer zero trust.  Reuse is possible only
            # when upstream supplied the exact asset+provenance in this request.
            continue
        for row in receipt["stock_videos"]:
            sources[row["slug"]] = {
                "filename": Path(row["asset"]["path"]).name,
                "bytes": row["asset"]["bytes"],
                "sha256": row["asset"]["sha256"],
                "provenance": row["provenance"],
            }
    return sources


def _stage_request_handoff_assets(context: dict) -> list[str]:
    handoff = context["request"].get("trusted_asset_handoff")
    sources = context["request"].get("trusted_asset_sources")
    if handoff is None:
        return []
    if not isinstance(handoff, dict) or not isinstance(sources, dict):
        raise RuntimeError("trusted asset handoff/request schema is incomplete")
    episode_dir = Path(context["episode_dir"])
    root = _contained(
        episode_dir / str(handoff.get("root") or ""),
        episode_dir / _TRUSTED_HANDOFF_ROOT,
        "request trusted asset handoff",
    )
    try:
        staged_sources = json.loads(
            (root / "trusted_asset_sources.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("request trusted asset handoff manifest is unreadable") from exc
    validated, paths = _validate_trusted_asset_sources(
        staged_sources, asset_path=lambda filename: root / "assets" / filename
    )
    sources_sha256 = _json_sha256(validated)
    if (
        validated != sources
        or sources_sha256 != handoff.get("sources_sha256")
        or sources_sha256 != root.name
    ):
        raise RuntimeError("request trusted asset handoff drifted")
    output_assets = Path(context["output_root"]) / "assets" / "broll"
    output_assets.mkdir(parents=True, exist_ok=True)
    staged: list[str] = []
    for slug, source in paths.items():
        filename = validated[slug]["filename"]
        episode_asset = episode_dir / "assets" / "broll" / filename
        if episode_asset.exists():
            if (
                not episode_asset.is_file()
                or episode_asset.is_symlink()
                or episode_asset.stat().st_size != validated[slug]["bytes"]
                or _sha256(episode_asset) != validated[slug]["sha256"]
            ):
                raise RuntimeError(f"existing episode B-roll asset drifted: {filename}")
            from agents.brook.script_video.highlight_broll import probe_stock_video

            probe_stock_video(episode_asset)
            continue
        destination = output_assets / filename
        shutil.copy2(source, destination)
        staged.append(destination.name)
    return staged


def _backup_revision_targets(
    *,
    episode_dir: Path,
    review_dir: Path,
    requested: list[str],
    allowed: dict,
    before_dir: Path,
) -> dict:
    trees: list[dict] = []
    destinations = [review_dir / cut_id for cut_id in requested]
    destinations.extend(
        episode_dir / "highlights" / "stills" / f"{cut_id}-visuals"
        for cut_id in requested
    )
    destinations.append(episode_dir / "assets" / "broll")
    for destination in destinations:
        relative = destination.resolve().relative_to(episode_dir.resolve())
        backup = before_dir / "trees" / relative
        existed = destination.is_dir()
        if existed:
            shutil.copytree(destination, backup)
        trees.append(
            {
                "destination": str(destination),
                "backup": str(backup),
                "existed": existed,
            }
        )

    files: list[dict] = []
    recipe_paths = [
        episode_dir / relative
        for relative in [
            *allowed["tighten_files"],
            *allowed["trusted_output_receipts"],
        ]
    ]
    manifest_paths = sorted(review_dir.glob("finished_review_manifest_*.json"))
    for source in [*recipe_paths, *manifest_paths]:
        relative = source.resolve().relative_to(episode_dir.resolve())
        backup = before_dir / "files" / relative
        existed = source.is_file()
        if existed:
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, backup)
        files.append(
            {
                "destination": str(source),
                "backup": str(backup),
                "existed": existed,
            }
        )
    inventory: dict[str, dict[str, int | str]] = {}
    for row in trees:
        inventory.update(
            _tree_inventory(Path(row["destination"]), relative_to=episode_dir)
        )
    for row in files:
        path = Path(row["destination"])
        if path.is_file():
            inventory.update(_tree_inventory(path, relative_to=episode_dir))
    return {"trees": trees, "files": files, "pre_inventory": inventory}


def _restore_revision_targets(state: dict, review_dir: Path) -> None:
    for row in state.get("trees", []):
        destination = Path(row["destination"])
        _remove_path(destination)
        if row["existed"]:
            shutil.copytree(Path(row["backup"]), destination)
    known_manifest_paths = {
        Path(row["destination"]).resolve()
        for row in state.get("files", [])
        if Path(row["destination"]).name.startswith("finished_review_manifest_")
    }
    for manifest in review_dir.glob("finished_review_manifest_*.json"):
        if manifest.resolve() not in known_manifest_paths:
            manifest.unlink()
    for row in state.get("files", []):
        destination = Path(row["destination"])
        if row["existed"]:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(row["backup"]), destination)
        else:
            _remove_path(destination)


def _promote_agent_inputs(
    *,
    episode_dir: Path,
    review_dir: Path,
    requested: list[str],
    allowed: dict,
    output_root: Path,
) -> list[str]:
    if _json_sha256(allowed.get("trusted_asset_sources", {})) != allowed.get(
        "trusted_asset_sources_sha256"
    ):
        raise RuntimeError("request-bound trusted asset provenance hash mismatch")
    output_root = _contained(output_root, output_root.parent, "agent output root")
    for staged_path in output_root.rglob("*"):
        if staged_path.is_symlink():
            raise RuntimeError("agent output may not contain symlinks")
        _contained(staged_path, output_root, "agent staged path")
    allowed_top = {"tighten", "assets"}
    unexpected_top = sorted(
        path.name for path in output_root.iterdir() if path.name not in allowed_top
    )
    if unexpected_top:
        raise RuntimeError(f"agent output contains forbidden paths: {', '.join(unexpected_top)}")
    recipe_names = {Path(relative).name for relative in allowed["tighten_files"]}
    output_tighten = output_root / "tighten"
    if output_tighten.exists():
        staged = [path for path in output_tighten.iterdir()]
        if any(not path.is_file() or path.name not in recipe_names for path in staged):
            raise RuntimeError("agent output contains a non-whitelisted tighten recipe")
        authority_sources = allowed.get("trusted_asset_sources", {})
        for source in staged:
            if not source.name.endswith("_broll.json"):
                continue
            try:
                items = json.loads(source.read_text(encoding="utf-8"))["items"]
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
                raise RuntimeError(f"agent B-roll plan is invalid: {source.name}") from exc
            for item in items:
                if not isinstance(item, dict) or str(item.get("kind", "")).lower() != "video":
                    continue
                slug = str(item.get("slug") or "")
                authority = authority_sources.get(slug)
                if (
                    not isinstance(authority, dict)
                    or item.get("provenance") != authority.get("provenance")
                ):
                    raise RuntimeError(
                        f"B-roll plan provenance is not request-bound: {slug or '<missing>'}"
                    )
                existing_assets = sorted(
                    path
                    for path in (episode_dir / "assets" / "broll").glob(f"{slug}.*")
                    if path.is_file()
                )
                staged_assets = sorted(
                    path
                    for path in (output_root / "assets" / "broll").glob(f"{slug}.*")
                    if path.is_file()
                )
                candidates = [*existing_assets, *staged_assets]
                if len(candidates) == 2:
                    existing, staged = candidates
                    if (
                        existing.name == staged.name
                        and existing.stat().st_size == staged.stat().st_size
                        and _sha256(existing) == _sha256(staged)
                    ):
                        candidates = [existing]
                if len(candidates) != 1:
                    raise RuntimeError(
                        f"B-roll plan slug must bind exactly one current/staged asset: {slug}"
                    )
                asset = candidates[0]
                if (
                    authority.get("filename") != asset.name
                    or authority.get("bytes") != asset.stat().st_size
                    or authority.get("sha256") != _sha256(asset)
                ):
                    raise RuntimeError(f"B-roll plan asset hash drifted from request: {slug}")
                from agents.brook.script_video.highlight_broll import probe_stock_video

                probe_stock_video(asset)
        for source in staged:
            destination = episode_dir / "highlights" / "tighten" / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    promoted_assets: list[str] = []
    output_assets = output_root / "assets" / "broll"
    if output_assets.exists():
        destination_root = episode_dir / "assets" / "broll"
        destination_root.mkdir(parents=True, exist_ok=True)
        for source in output_assets.iterdir():
            if (
                not source.is_file()
                or source.name != Path(source.name).name
                or source.suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
            ):
                raise RuntimeError("agent output B-roll asset is not a canonical video file")
            destination = destination_root / source.name
            if destination.exists():
                if (
                    destination.is_file()
                    and not destination.is_symlink()
                    and destination.stat().st_size == source.stat().st_size
                    and _sha256(destination) == _sha256(source)
                ):
                    continue
                raise RuntimeError(f"agent may not overwrite B-roll asset: {source.name}")
            authorities = allowed.get("trusted_asset_sources", {})
            authority = authorities.get(source.stem) or authorities.get(source.name)
            if not isinstance(authority, dict):
                raise RuntimeError(
                    f"new B-roll asset lacks request-bound trusted provenance: {source.name}"
                )
            provenance = authority.get("provenance")
            required = {"source_url", "acquired_at"}
            license_fields = {"license_url", "terms_url", "license_id"}
            if (
                not isinstance(provenance, dict)
                or not required.issubset(provenance)
                or not any(provenance.get(name) for name in license_fields)
                or authority.get("filename") != source.name
                or authority.get("bytes") != source.stat().st_size
                or authority.get("sha256") != _sha256(source)
            ):
                raise RuntimeError(
                    f"new B-roll asset differs from request-bound provenance: {source.name}"
                )
            shutil.copy2(source, destination)
            promoted_assets.append(destination.relative_to(episode_dir).as_posix())
    return promoted_assets


def _timeline_uid(timeline: object) -> str | None:
    for method_name in ("GetUniqueId", "GetUniqueID"):
        method = getattr(timeline, method_name, None)
        value = method() if callable(method) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _project_timelines(project: object) -> list[object]:
    return [
        timeline
        for index in range(1, project.GetTimelineCount() + 1)
        if (timeline := project.GetTimelineByIndex(index)) is not None
    ]


class _ResolveTimelineTransaction:
    """Keep canonical derived timelines untouched until trusted verification passes."""

    def __init__(self, project: object, project_manager: object, rows: list[dict]) -> None:
        self.project = project
        self.project_manager = project_manager
        self.rows = rows
        self.closed = False

    @classmethod
    def begin(cls, episode_dir: Path, timelines: dict[str, str], request_id: str):
        from scripts.build_resolve_project import connect_resolve

        resolve = connect_resolve()
        project_manager = resolve.GetProjectManager()
        project = project_manager.GetCurrentProject()
        if project is None or project.GetName() != episode_dir.name:
            project = project_manager.LoadProject(episode_dir.name)
        if project is None:
            raise RuntimeError(f"Resolve project not found: {episode_dir.name}")
        transaction = cls(project, project_manager, [])
        suffix = request_id[-12:]
        try:
            for cut_id, canonical_name in timelines.items():
                matches = [
                    timeline
                    for timeline in _project_timelines(project)
                    if timeline.GetName() == canonical_name
                ]
                if len(matches) != 1:
                    raise RuntimeError(
                        f"expected one canonical derived Timeline for {cut_id}, got {len(matches)}"
                    )
                original = matches[0]
                backup_name = f"{canonical_name}__revision_backup__{suffix}"
                work_name = f"{canonical_name}__revision_work__{suffix}"
                occupied = {timeline.GetName() for timeline in _project_timelines(project)}
                if backup_name in occupied or work_name in occupied:
                    raise RuntimeError(f"stale Resolve revision Timeline exists for {cut_id}")
                duplicate = original.DuplicateTimeline(work_name)
                if duplicate is None:
                    raise RuntimeError(f"DuplicateTimeline failed for {cut_id}")
                if original.SetName(backup_name) is False or original.GetName() != backup_name:
                    project.GetMediaPool().DeleteTimelines([duplicate])
                    raise RuntimeError(f"cannot reserve original Timeline for {cut_id}")
                renamed = duplicate.SetName(canonical_name)
                if renamed is False or duplicate.GetName() != canonical_name:
                    original.SetName(canonical_name)
                    project.GetMediaPool().DeleteTimelines([duplicate])
                    raise RuntimeError(f"cannot activate revision Timeline for {cut_id}")
                transaction.rows.append(
                    {
                        "cut_id": cut_id,
                        "canonical_name": canonical_name,
                        "backup_name": backup_name,
                        "original": original,
                        "original_uid": _timeline_uid(original),
                    }
                )
            if not project_manager.SaveProject():
                raise RuntimeError("Resolve SaveProject failed while opening revision transaction")
            return transaction
        except Exception:
            transaction.rollback()
            raise

    def prepare(self) -> dict:
        for row in self.rows:
            canonical = [
                timeline
                for timeline in _project_timelines(self.project)
                if timeline.GetName() == row["canonical_name"]
                and timeline is not row["original"]
            ]
            if len(canonical) != 1:
                raise RuntimeError(f"verified Timeline promotion drifted for {row['cut_id']}")
        if not self.project_manager.SaveProject():
            raise RuntimeError("Resolve SaveProject failed before Timeline promotion")
        return {
            "strategy": "duplicate-swap-verify-two-phase",
            "prepared": True,
            "canonical_promoted": True,
            "original_backup_retained": True,
            "timelines": [row["canonical_name"] for row in self.rows],
            "backup_timelines": [row["backup_name"] for row in self.rows],
        }

    def finalize(self) -> dict:
        backups = [row["original"] for row in self.rows]
        cleanup_deleted = bool(
            not backups or self.project.GetMediaPool().DeleteTimelines(backups)
        )
        cleanup_saved = bool(cleanup_deleted and self.project_manager.SaveProject())
        self.closed = True
        return {
            "strategy": "duplicate-swap-verify-two-phase",
            "committed": True,
            "timelines": [row["canonical_name"] for row in self.rows],
            "backup_cleanup_deleted": cleanup_deleted,
            "backup_cleanup_saved": cleanup_saved,
        }

    def rollback(self) -> dict:
        if self.closed:
            return {"strategy": "duplicate-swap-verify-two-phase", "rolled_back": False}
        pool = self.project.GetMediaPool()
        restored: list[dict] = []
        for row in reversed(self.rows):
            canonical = [
                timeline
                for timeline in _project_timelines(self.project)
                if timeline.GetName() == row["canonical_name"] and timeline is not row["original"]
            ]
            if canonical and not pool.DeleteTimelines(canonical):
                raise RuntimeError(f"cannot delete failed Timeline for {row['cut_id']}")
            if row["original"].SetName(row["canonical_name"]) is False:
                raise RuntimeError(f"cannot restore original Timeline for {row['cut_id']}")
            if row["original"].GetName() != row["canonical_name"]:
                raise RuntimeError(f"restored Timeline name mismatch for {row['cut_id']}")
            if _timeline_uid(row["original"]) != row["original_uid"]:
                raise RuntimeError(f"restored Timeline identity mismatch for {row['cut_id']}")
            restored.append(
                {
                    "cut_id": row["cut_id"],
                    "timeline": row["canonical_name"],
                    "uid": row["original_uid"],
                }
            )
        if self.rows and not self.project_manager.SaveProject():
            raise RuntimeError("Resolve SaveProject failed while rolling back revision")
        self.closed = True
        return {
            "strategy": "duplicate-swap-verify-two-phase",
            "rolled_back": True,
            "restored": restored,
        }


class _TrustedApplyError(RuntimeError):
    def __init__(self, message: str, *, operations: list[dict], rollback: dict) -> None:
        super().__init__(message)
        self.operations = operations
        self.rollback = rollback


def _trusted_apply_revision(context: dict) -> dict:
    """Apply staged recipes through deterministic code on disposable Timelines."""
    from scripts.run_short_broll import apply as apply_broll
    from scripts.run_short_director import direct
    from scripts.run_short_review import build_packet
    from scripts.run_short_titles import apply as apply_titles

    episode_dir = Path(context["episode_dir"])
    requested = list(context["request"]["requested_cut_ids"])
    transaction = _ResolveTimelineTransaction.begin(
        episode_dir,
        dict(context["allowed_changes"]["resolve_timelines"]),
        str(context["request_id"]),
    )
    operations: list[dict] = []
    try:
        for cut_id in requested:
            stills = episode_dir / "highlights" / "stills" / f"{cut_id}-visuals"
            operations.append(
                {
                    "operation": "run_short_director.direct",
                    "result": direct(episode_dir, cut_id),
                }
            )
            operations.append(
                {
                    "operation": "run_short_broll.apply",
                    "result": apply_broll(episode_dir, cut_id, stills),
                }
            )
            operations.append(
                {
                    "operation": "run_short_titles.apply",
                    "result": apply_titles(episode_dir, cut_id, stills),
                }
            )
            operations.append(
                {
                    "operation": "run_short_review.build_packet",
                    "result": build_packet(episode_dir, cut_id),
                }
            )
        verification = context["output_verifier"](context)
        if not isinstance(verification, dict) or verification.get("approved") is not False:
            raise RuntimeError("fresh verifier did not bind output for human re-review")
        acceptance = _verify_revision_output_acceptance(context, verification)
        transaction_receipt = transaction.prepare()
        return {
            "status": "trusted_apply_succeeded",
            "operations": operations,
            "timeline_transaction": transaction_receipt,
            "_timeline_transaction_handle": transaction,
            "authoritative_verification": verification,
            "output_acceptance": acceptance,
        }
    except KeyboardInterrupt:
        transaction.rollback()
        raise
    except BaseException as exc:
        rollback = transaction.rollback()
        raise _TrustedApplyError(
            str(exc), operations=operations, rollback=rollback
        ) from exc


def run_revision_job(
    work: dict,
    *,
    agent_runner: Callable[[dict], subprocess.CompletedProcess[str]] = dispatch_revision_agent,
    output_verifier: Callable[[dict], dict] = _authoritative_output_verifier,
    trusted_apply: Callable[[dict], dict] = _trusted_apply_revision,
) -> bool:
    """Run one queued request transactionally; repeated pickup is a no-op."""
    feedback_path = Path(work["feedback_path"])
    review_dir = Path(work["review_dir"])
    episode_dir = Path(work["episode_dir"])
    request = work["job"]
    request_id = work["request_id"]
    current_audit = _load_feedback(feedback_path)
    try:
        current = current_audit["revisions"][int(work["revision_index"])]["revision_job"]
    except (IndexError, KeyError, TypeError):
        return False
    if current.get("request_id") != request_id or current.get("status") != "queued":
        return False

    requested = request.get("requested_cut_ids")
    if not isinstance(requested, list) or not requested or not all(
        isinstance(cut_id, str) and cut_id for cut_id in requested
    ):
        _update_job(
            work,
            {
                "status": "failed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error": "revision request has no valid requested_cut_ids",
            },
            required_status="queued",
        )
        return False
    manifest_name = request.get("manifest_filename")
    if not isinstance(manifest_name, str) or Path(manifest_name).name != manifest_name:
        _update_job(
            work,
            {
                "status": "failed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error": "revision request manifest filename is invalid",
            },
            required_status="queued",
        )
        return False
    manifest_path = _contained(review_dir / manifest_name, review_dir, "source manifest")
    attempt_number = int(request.get("attempt", 0)) + 1
    request_root = review_dir / "revisions" / request_id
    job_dir = (
        request_root
        if attempt_number == 1
        else request_root / "attempts" / str(attempt_number)
    )
    before_dir = job_dir / "before"
    output_root = job_dir / "output"
    started_at = datetime.now(timezone.utc)
    backup_complete = False
    rollback_state: dict = {}
    timeline_handle: _ResolveTimelineTransaction | None = None
    job_succeeded = False
    try:
        if _sha256(manifest_path) != request.get("source_manifest_sha256"):
            raise RuntimeError("finished review manifest drifted after feedback was saved")
        source_cuts = _manifest_cuts(manifest_path, review_dir, requested)
        for cut_id in requested:
            preview = Path(source_cuts[cut_id]["artifacts"]["preview"]["path"])
            if _sha256(preview) != request.get("source_preview_sha256", {}).get(cut_id):
                raise RuntimeError(f"finished preview drifted after feedback was saved: {cut_id}")

        if output_root.exists():
            raise RuntimeError("revision job output already exists before first pickup")
        output_root.mkdir(parents=True)
        trusted_asset_sources = _verified_current_asset_sources(
            episode_dir,
            requested,
            request.get("trusted_asset_sources"),
        )
        allowed = _allowed_changes(
            episode_dir,
            review_dir,
            manifest_path,
            requested,
            output_root,
            trusted_asset_sources,
        )
        rollback_state = _backup_revision_targets(
            episode_dir=episode_dir,
            review_dir=review_dir,
            requested=requested,
            allowed=allowed,
            before_dir=before_dir,
        )
        backup_complete = True
        request_path = job_dir / "request.json"
        _atomic_json(
            request_path,
            {
                "contract": "finished-cut-revision-request-v1",
                "request_id": request_id,
                "feedback_revision": int(work["revision_index"]) + 1,
                **request,
                "allowed_changes": allowed,
                "pre_snapshot": rollback_state["pre_inventory"],
            },
        )
        _update_job(
            work,
            {
                "status": "running",
                "attempt": int(request.get("attempt", 0)) + 1,
                "started_at": started_at.isoformat(),
                "finished_at": None,
                "result_receipt": None,
                "error": None,
            },
            required_status="queued",
        )
        context = {
            "request_id": request_id,
            "request_path": str(request_path),
            "job_dir": str(job_dir),
            "output_root": str(output_root),
            "episode_dir": str(episode_dir),
            "review_dir": str(review_dir),
            "manifest_path": str(manifest_path),
            "allowed_changes": allowed,
            "request": request,
            "output_verifier": output_verifier,
        }
        handoff_assets = _stage_request_handoff_assets(context)
        result = agent_runner(context)
        (job_dir / "agent.stdout.log").write_text(result.stdout or "", encoding="utf-8")
        (job_dir / "agent.stderr.log").write_text(result.stderr or "", encoding="utf-8")
        if result.returncode != 0:
            raise RuntimeError(
                f"Finished revision agent exit {result.returncode}: {(result.stderr or '')[-500:]}"
            )
        promoted_assets = _promote_agent_inputs(
            episode_dir=episode_dir,
            review_dir=review_dir,
            requested=requested,
            allowed=allowed,
            output_root=output_root,
        )
        trusted_result = trusted_apply(context)
        if isinstance(trusted_result, dict):
            candidate_handle = trusted_result.pop("_timeline_transaction_handle", None)
            if isinstance(candidate_handle, _ResolveTimelineTransaction):
                timeline_handle = candidate_handle
        timeline_state = (
            trusted_result.get("timeline_transaction", {})
            if isinstance(trusted_result, dict)
            else {}
        )
        if (
            not isinstance(trusted_result, dict)
            or trusted_result.get("status") != "trusted_apply_succeeded"
            or (
                timeline_state.get("committed") is not True
                and timeline_state.get("prepared") is not True
            )
        ):
            raise RuntimeError("trusted apply did not prepare a verified Timeline transaction")
        if timeline_handle is not None:
            timeline_state.update(
                {
                    "committed": True,
                    "backup_cleanup_pending": True,
                }
            )
        verification = trusted_result.get("authoritative_verification")
        acceptance = trusted_result.get("output_acceptance")
        if not isinstance(verification, dict) or not isinstance(acceptance, dict):
            raise RuntimeError("trusted apply lacks authoritative verification")
        output_manifest_sha = acceptance.get("manifest_sha256")
        output_previews = acceptance.get("preview_sha256")
        if (
            not isinstance(output_manifest_sha, str)
            or output_manifest_sha == request["source_manifest_sha256"]
            or not isinstance(output_previews, dict)
            or any(
                output_previews.get(cut_id) == request["source_preview_sha256"].get(cut_id)
                for cut_id in requested
            )
        ):
            raise RuntimeError("trusted apply returned invalid pre-commit output acceptance")
        finished_at = datetime.now(timezone.utc)
        receipt_path = job_dir / "result.json"
        _atomic_json(
            receipt_path,
            {
                "contract": "finished-cut-revision-result-v1",
                "request_id": request_id,
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "source_manifest_sha256": request["source_manifest_sha256"],
                "output_manifest_sha256": output_manifest_sha,
                "output_preview_sha256": output_previews,
                "requested_cut_ids": requested,
                "promoted_broll_assets": promoted_assets,
                "trusted_handoff_assets": handoff_assets,
                "staged_input_inventory": _tree_inventory(
                    output_root, relative_to=output_root
                ),
                "trusted_apply": trusted_result,
                "authoritative_verification": verification,
                "approved": False,
            },
        )
        _update_job(
            work,
            {
                "status": "succeeded",
                "finished_at": finished_at.isoformat(),
                "result_receipt": receipt_path.relative_to(review_dir).as_posix(),
                "output_manifest_sha256": output_manifest_sha,
                "output_preview_sha256": output_previews,
                "error": None,
            },
            required_status="running",
        )
        job_succeeded = True
        if timeline_handle is not None:
            try:
                cleanup = timeline_handle.finalize()
                timeline_state.update(cleanup)
                timeline_state["backup_cleanup_pending"] = not bool(
                    cleanup.get("backup_cleanup_saved")
                )
                result_payload = json.loads(receipt_path.read_text(encoding="utf-8"))
                result_payload["trusted_apply"]["timeline_transaction"] = timeline_state
                _atomic_json(receipt_path, result_payload)
            except Exception as cleanup_exc:
                # Canonical output and succeeded receipt are already durable;
                # retaining the original backup is safe and recoverable.
                timeline_state["backup_cleanup_error"] = str(cleanup_exc)[-500:]
        return True
    except KeyboardInterrupt:
        if job_succeeded:
            # The durable result/job state already names the verified canonical
            # output.  Never restore filesystem bytes after that commit point.
            raise
        if timeline_handle is not None and not job_succeeded:
            timeline_handle.rollback()
        if backup_complete:
            _restore_revision_targets(rollback_state, review_dir)
        _update_job(
            work,
            {
                "status": "failed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error": "Finished revision worker interrupted; previous artifacts restored",
            },
        )
        raise
    except Exception as exc:
        if timeline_handle is not None and not job_succeeded:
            try:
                timeline_handle.rollback()
            except Exception as rollback_exc:
                exc = RuntimeError(f"{exc}; Timeline rollback failed: {rollback_exc}")
        if backup_complete:
            _restore_revision_targets(rollback_state, review_dir)
        failure_receipt: Path | None = None
        if job_dir.is_dir():
            failure_receipt = job_dir / "result.json"
            _atomic_json(
                failure_receipt,
                {
                    "contract": "finished-cut-revision-result-v1",
                    "request_id": request_id,
                    "status": "failed",
                    "started_at": started_at.isoformat(),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "requested_cut_ids": requested,
                    "staged_input_inventory": (
                        _tree_inventory(output_root, relative_to=output_root)
                        if output_root.is_dir()
                        else {}
                    ),
                    "trusted_operations": getattr(exc, "operations", []),
                    "timeline_rollback": getattr(exc, "rollback", None),
                    "error": str(exc)[-1000:],
                    "approved": False,
                },
            )
        try:
            _update_job(
                work,
                {
                    "status": "failed",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "result_receipt": (
                        failure_receipt.relative_to(review_dir).as_posix()
                        if failure_receipt is not None
                        else None
                    ),
                    "error": str(exc)[-1000:],
                },
            )
        except Exception:
            pass
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--episodes-root",
        type=Path,
        default=Path(os.environ.get("PODCAST_EPISODES_ROOT", "G:/Footages")),
    )
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--reconcile-episode",
        help="episode folder whose latest pre-cutover draft should be reconciled",
    )
    parser.add_argument(
        "--apply-reconcile",
        action="store_true",
        help="write the reconciled queue job (default reconcile is read-only)",
    )
    parser.add_argument(
        "--trusted-asset-sources",
        type=Path,
        help=(
            "validated acquisition map; sibling video files are staged into the "
            "episode handoff when --apply-reconcile is set"
        ),
    )
    parser.add_argument(
        "--retry-episode",
        help="episode folder containing the failed finished revision request",
    )
    parser.add_argument(
        "--retry-failed",
        metavar="REQUEST_ID",
        help="verify clean rollback and prepare one failed request for retry",
    )
    parser.add_argument(
        "--apply-retry",
        action="store_true",
        help="requeue the verified failed request (default retry is read-only)",
    )
    args = parser.parse_args()
    if args.apply_reconcile and not args.reconcile_episode:
        parser.error("--apply-reconcile requires --reconcile-episode")
    if args.trusted_asset_sources and not args.reconcile_episode:
        parser.error("--trusted-asset-sources requires --reconcile-episode")
    if bool(args.retry_episode) != bool(args.retry_failed):
        parser.error("--retry-episode and --retry-failed must be provided together")
    if args.apply_retry and not args.retry_failed:
        parser.error("--apply-retry requires --retry-failed")
    if args.retry_failed and args.reconcile_episode:
        parser.error("retry and reconcile are separate operations")
    if args.retry_failed:
        result = retry_failed_revision_job(
            args.episodes_root,
            episode_id=args.retry_episode,
            request_id=args.retry_failed,
            apply=args.apply_retry,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.reconcile_episode:
        result = reconcile_missing_revision_job(
            args.episodes_root,
            episode_id=args.reconcile_episode,
            apply=args.apply_reconcile,
            trusted_asset_sources=args.trusted_asset_sources,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    while True:
        for work in pending_revision_jobs(args.episodes_root):
            run_revision_job(work)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
