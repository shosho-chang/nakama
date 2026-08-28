#!/usr/bin/env python3
"""Register materialized Long Highlight previews for Bridge read-only playback."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
from pathlib import Path
from typing import Any

STAGED_CUT_NAME = "playback_manifest_cut.v1.json"
OUTPUT_NAME = "finished_review_manifest_current.json"
LEGACY_SCHEMA = "nakama.finished_cut_review_manifest.v1"
LANE_ACTIONS = {
    "b_roll": ["approve", "remove", "replace_asset", "change_type", "move", "comment"],
    "identity_card": ["approve", "remove", "edit_text", "move", "comment"],
    "hero_title": ["approve", "remove", "edit_text", "move", "comment"],
    "fullscreen_transition": ["approve", "remove", "edit_text", "move", "comment"],
    "visual_effect": ["approve", "remove", "replace_asset", "change_type", "move", "comment"],
}


class PlaybackManifestError(ValueError):
    """The playback artifact or emitted recipe cannot be safely registered."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(path.suffix + ".tmp")
    staging.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(staging, path)


def _contained_file(path: Path, root: Path, *, suffix: str, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise PlaybackManifestError(f"{label} must be inside {root}") from exc
    if resolved.suffix.lower() != suffix:
        raise PlaybackManifestError(f"{label} must be a {suffix} file")
    if not resolved.is_file():
        raise PlaybackManifestError(f"{label} does not exist: {resolved}")
    return resolved


def _probe_preview(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise PlaybackManifestError(f"preview ffprobe failed: {result.stderr[-300:]}")
    try:
        probe = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PlaybackManifestError("preview ffprobe returned invalid JSON") from exc
    streams = probe.get("streams")
    if not isinstance(streams, list):
        raise PlaybackManifestError("preview has no streams")
    video = [row for row in streams if row.get("codec_type") == "video"]
    if not video or any(row.get("codec_name") != "h264" for row in video):
        raise PlaybackManifestError("preview video must be H.264")
    audio = [row for row in streams if row.get("codec_type") == "audio"]
    if any(row.get("codec_name") != "aac" for row in audio):
        raise PlaybackManifestError("preview audio must be AAC or absent")
    raw_duration = (probe.get("format") or {}).get("duration")
    try:
        duration = float(raw_duration)
    except (TypeError, ValueError) as exc:
        raise PlaybackManifestError("preview duration is missing") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise PlaybackManifestError("preview duration must be positive")
    return round(duration, 3)


def _read_recipe(path: Path, key: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlaybackManifestError(f"invalid emitted recipe: {path}") from exc
    rows = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise PlaybackManifestError(f"{path} must contain {key} array")
    if not all(isinstance(row, dict) for row in rows):
        raise PlaybackManifestError(f"{path} {key} rows must be objects")
    return rows


def _recipe_paths(episode_dir: Path, cut_id: str) -> tuple[Path, Path]:
    materialized = (
        episode_dir / "highlights" / "long-orchestrator-v2" / cut_id / "materialization" / "recipes"
    )
    materialized_pair = (
        materialized / f"{cut_id}_broll.json",
        materialized / f"{cut_id}_titles.json",
    )
    if all(path.is_file() for path in materialized_pair):
        return materialized_pair
    if any(path.exists() for path in materialized_pair):
        raise PlaybackManifestError("materialized recipe pair is incomplete")
    tighten = episode_dir / "highlights" / "tighten"
    return tighten / f"{cut_id}_broll.json", tighten / f"{cut_id}_titles.json"


def _display(item: dict[str, Any], lane: str) -> str:
    variables = item.get("vars") if isinstance(item.get("vars"), dict) else {}
    if lane == "identity_card":
        return "｜".join(
            value
            for value in (
                str(variables.get("label") or "").strip(),
                str(variables.get("sub") or "").strip(),
            )
            if value
        )
    return str(
        item.get("text")
        or item.get("on_screen_text")
        or variables.get("title")
        or item.get("slug")
        or ""
    ).strip()


def _broll_lane(item: dict[str, Any]) -> str | None:
    kind = str(item.get("kind") or "").strip().lower().replace("_", "-")
    slug = str(item.get("slug") or "").strip().lower()
    component = str(item.get("comp") or "").strip().lower()
    if kind in {"badge", "camera", "camera-correction", "camera-cut", "pacing", "cut"}:
        return None
    if component == "chapter_label" or slug == "guest-namecard":
        return "identity_card"
    if component == "transition_title":
        return "fullscreen_transition"
    if kind in {"video", "photo"}:
        return "b_roll"
    return "visual_effect"


def _project_components(
    broll: list[dict[str, Any]], titles: list[dict[str, Any]], duration: float
) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for item in broll:
        lane = _broll_lane(item)
        if lane is None:
            continue
        projected.append({"source": item, "lane": lane})
    projected.extend({"source": item, "lane": "hero_title"} for item in titles)

    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, float, float, str]] = set()
    for row in projected:
        item = row["source"]
        lane = row["lane"]
        t0, t1 = item.get("t0"), item.get("t1")
        if (
            not isinstance(t0, (int, float))
            or isinstance(t0, bool)
            or not isinstance(t1, (int, float))
            or isinstance(t1, bool)
            or not math.isfinite(float(t0))
            or not math.isfinite(float(t1))
            or float(t0) < 0
            or float(t1) <= float(t0)
            or float(t1) > duration + 0.001
        ):
            raise PlaybackManifestError("recipe component has invalid timeline range")
        display = _display(item, lane)
        key = (lane, float(t0), float(t1), display)
        if key in seen:
            continue
        seen.add(key)
        component: dict[str, Any] = {
            "lane": lane,
            "t0": float(t0),
            "t1": float(t1),
            "display": display,
        }
        for name in ("kind", "slug", "text", "vars"):
            if item.get(name) is not None:
                component[name] = item[name]
        materialization = item.get("visual_materialization")
        if (
            lane == "b_roll"
            and isinstance(materialization, dict)
            and (
                materialization.get("implementation_kind") == "stock_video"
                or materialization.get("mode") == "stock"
            )
        ):
            component["asset_category"] = "stock_video"
        normalized.append(component)

    normalized.sort(key=lambda row: (row["t0"], row["t1"], row["lane"], row["display"]))
    counts: dict[str, int] = {}
    for component in normalized:
        lane = component["lane"]
        counts[lane] = counts.get(lane, 0) + 1
        component["component_id"] = f"{{cut_id}}-{lane.replace('_', '-')}-{counts[lane]:03d}"
    return normalized


def stage_cut(
    episode_dir: Path,
    *,
    cut_id: str,
    title: str,
    preview_path: Path,
    subtitles_path: Path,
) -> Path:
    """Validate one materialized cut and atomically stage its Bridge cut record."""

    episode_dir = Path(episode_dir).resolve()
    if not cut_id or Path(cut_id).name != cut_id or cut_id in {".", ".."}:
        raise PlaybackManifestError("cut_id must be a safe basename")
    cut_dir = episode_dir / "highlights" / "review" / cut_id
    preview = _contained_file(Path(preview_path), cut_dir, suffix=".mp4", label="preview")
    subtitles = _contained_file(Path(subtitles_path), cut_dir, suffix=".srt", label="subtitles")
    try:
        subtitles.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise PlaybackManifestError("subtitles must be UTF-8") from exc
    duration = _probe_preview(preview)
    broll_path, titles_path = _recipe_paths(episode_dir, cut_id)
    broll = _read_recipe(broll_path, "items")
    titles = _read_recipe(titles_path, "titles")
    components = _project_components(broll, titles, duration)
    for component in components:
        component["component_id"] = component["component_id"].format(cut_id=cut_id)
    payload = {
        "cut_id": cut_id,
        "format": "long",
        "title": str(title).strip() or cut_id,
        "artifacts": {
            "preview": {
                "path": str(preview),
                "bytes": preview.stat().st_size,
                "sha256": _sha256(preview),
                "duration_seconds": duration,
            },
            "subtitles": {
                "path": str(subtitles),
                "bytes": subtitles.stat().st_size,
                "sha256": _sha256(subtitles),
            },
        },
        "components": components,
    }
    output = cut_dir / STAGED_CUT_NAME
    _atomic_write_json(output, payload)
    return output


def _load_legacy_manifest(review_dir: Path, episode_id: str) -> dict[str, Any]:
    current = review_dir / OUTPUT_NAME
    candidates = [current] if current.is_file() else []
    candidates.extend(
        path
        for path in sorted(review_dir.glob("finished_review_manifest_*.json"), reverse=True)
        if path != current and path.is_file()
    )
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if (
            isinstance(payload, dict)
            and payload.get("schema") == LEGACY_SCHEMA
            and payload.get("episode_id") == episode_id
            and isinstance(payload.get("cuts"), list)
        ):
            return payload
    return {
        "schema": LEGACY_SCHEMA,
        "episode_id": episode_id,
        "stage": 5,
        "gate": {
            "kind": "finished_cut_review",
            "status": "ready_for_review",
            "feedback_file": str((review_dir / "finished_review_feedback.v1.json").resolve()),
        },
        "cuts": [],
        "feedback_contract": {
            "review_lanes": list(LANE_ACTIONS),
            "component_actions": LANE_ACTIONS,
            "gate_actions": ["request_changes", "approve_cut", "approve_all"],
        },
    }


def _read_staged_cut(review_dir: Path, cut_id: str) -> dict[str, Any]:
    path = review_dir / cut_id / STAGED_CUT_NAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlaybackManifestError(f"invalid staged cut: {path}") from exc
    if not isinstance(payload, dict) or payload.get("cut_id") != cut_id:
        raise PlaybackManifestError(f"staged cut identity mismatch: {cut_id}")
    _validate_staged_cut(payload, review_dir / cut_id)
    return payload


def _validate_staged_cut(payload: dict[str, Any], cut_dir: Path) -> None:
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict):
        raise PlaybackManifestError("staged cut artifacts are required")
    duration: float | None = None
    for name, suffix in (("preview", ".mp4"), ("subtitles", ".srt")):
        artifact = artifacts.get(name)
        if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
            raise PlaybackManifestError(f"staged {name} artifact is required")
        path = _contained_file(
            Path(artifact["path"]), cut_dir, suffix=suffix, label=f"staged {name}"
        )
        size = artifact.get("bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size != path.stat().st_size:
            raise PlaybackManifestError(f"staged {name} byte size changed")
        digest = artifact.get("sha256")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest.lower())
        ):
            raise PlaybackManifestError(f"staged {name} sha256 is invalid")
        if name == "subtitles":
            try:
                path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError as exc:
                raise PlaybackManifestError("staged subtitles must be UTF-8") from exc
        else:
            raw_duration = artifact.get("duration_seconds")
            if (
                not isinstance(raw_duration, (int, float))
                or isinstance(raw_duration, bool)
                or not math.isfinite(float(raw_duration))
                or float(raw_duration) <= 0
            ):
                raise PlaybackManifestError("staged preview duration is invalid")
            duration = float(raw_duration)
    components = payload.get("components")
    if not isinstance(components, list):
        raise PlaybackManifestError("staged components must be an array")
    component_ids: set[str] = set()
    for component in components:
        if not isinstance(component, dict):
            raise PlaybackManifestError("staged component must be an object")
        component_id = component.get("component_id")
        if not isinstance(component_id, str) or not component_id or component_id in component_ids:
            raise PlaybackManifestError("staged component ids must be unique")
        component_ids.add(component_id)
        if component.get("lane") not in LANE_ACTIONS:
            raise PlaybackManifestError("staged component lane is invalid")
        t0, t1 = component.get("t0"), component.get("t1")
        if (
            not isinstance(t0, (int, float))
            or isinstance(t0, bool)
            or not isinstance(t1, (int, float))
            or isinstance(t1, bool)
            or not math.isfinite(float(t0))
            or not math.isfinite(float(t1))
            or float(t0) < 0
            or float(t1) <= float(t0)
            or duration is None
            or float(t1) > duration + 0.001
        ):
            raise PlaybackManifestError("staged component has invalid timeline range")


def build_manifest(episode_dir: Path, *, cut_ids: list[str]) -> Path:
    """Merge staged cuts into the Bridge legacy read-only manifest atomically."""

    episode_dir = Path(episode_dir).resolve()
    review_dir = episode_dir / "highlights" / "review"
    if not cut_ids or len(cut_ids) != len(set(cut_ids)):
        raise PlaybackManifestError("cut_ids must be a non-empty unique list")
    staged = [_read_staged_cut(review_dir, cut_id) for cut_id in cut_ids]
    manifest = _load_legacy_manifest(review_dir, episode_dir.name)
    replacement_ids = set(cut_ids)
    preserved = [
        cut
        for cut in manifest["cuts"]
        if isinstance(cut, dict) and cut.get("cut_id") not in replacement_ids
    ]
    manifest["cuts"] = [*preserved, *staged]
    scope = manifest.get("inventory_scope")
    if isinstance(scope, dict):
        included = {
            str(cut.get("cut_id"))
            for cut in manifest["cuts"]
            if isinstance(cut, dict) and cut.get("cut_id")
        }
        pending = {
            value
            for value in scope.get("pending_cut_ids", [])
            if isinstance(value, str) and value not in replacement_ids
        }
        scope["included_cut_ids"] = sorted(included)
        scope["pending_cut_ids"] = sorted(pending)
    output = review_dir / OUTPUT_NAME
    _atomic_write_json(output, manifest)
    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Register materialized Long Highlights for Bridge playback."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage", help="Validate and stage one cut")
    stage.add_argument("episode_dir", type=Path)
    stage.add_argument("--cut-id", required=True)
    stage.add_argument("--title", required=True)
    stage.add_argument("--preview", required=True, type=Path)
    stage.add_argument("--subtitles", required=True, type=Path)
    build = commands.add_parser("build", help="Atomically merge staged cuts")
    build.add_argument("episode_dir", type=Path)
    build.add_argument("--cut-id", action="append", dest="cut_ids", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "stage":
        output = stage_cut(
            args.episode_dir,
            cut_id=args.cut_id,
            title=args.title,
            preview_path=args.preview,
            subtitles_path=args.subtitles,
        )
    else:
        output = build_manifest(args.episode_dir, cut_ids=args.cut_ids)
    print(json.dumps({"status": "ok", "path": str(output)}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
