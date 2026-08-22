"""Build the Stage 5 finished-cut review manifest from materialized packets.

The manifest is a deterministic inventory, not an authoring surface.  It reads
``highlights/review/<cut>/events.json`` plus the preview/subtitle artifacts and
classifies what was actually materialized.  Asset-backed B-roll is deliberately
separate from identity cards, titles, badges, transitions and pacing effects.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

SCHEMA = "nakama.finished_cut_review_manifest.v1"
OUTPUT_NAME = "finished_review_manifest_current.json"

LANE_ACTIONS = {
    "b_roll": ["approve", "remove", "replace_asset", "change_type", "move", "comment"],
    "identity_card": ["approve", "remove", "edit_text", "move", "comment"],
    "hero_title": ["approve", "remove", "edit_text", "move", "comment"],
    "badge": ["approve", "remove", "replace_asset", "move", "comment"],
    "fullscreen_transition": ["approve", "remove", "edit_text", "move", "comment"],
    "visual_effect": ["approve", "remove", "replace_asset", "change_type", "move", "comment"],
    "pacing": ["approve", "remove", "move", "comment"],
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _open_master(episode_dir: Path):
    from agents.brook.script_video.editorial_master import (
        EditorialMasterContractError,
        EditorialMasterRequest,
    )

    try:
        return EditorialMasterRequest(
            episode_dir,
            expected_episode_id=episode_dir.name,
        ).open()
    except EditorialMasterContractError as exc:
        raise SystemExit(f"Editorial Master 驗證失敗：{exc}") from exc


def _artifact(path: Path, *, duration_seconds: float | None = None) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"finished review artifact 不存在：{path}")
    out: dict[str, Any] = {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }
    if duration_seconds is not None:
        out["duration_seconds"] = duration_seconds
    return out


def _event_lane(event_type: str) -> str | None:
    normalized = event_type.strip().lower().replace("_", "-")
    if normalized in {"video", "photo"}:
        return "b_roll"
    if normalized in {"guest-namecard", "host-namecard", "identity-card", "namecard"}:
        return "identity_card"
    if normalized.startswith("card-tier") or normalized in {"hero-title", "title"}:
        return "hero_title"
    if normalized == "badge":
        return "badge"
    if normalized in {"fullscreen-transition", "transition"}:
        return "fullscreen_transition"
    if normalized in {"icon-motion", "sticker", "concept", "visual-effect"}:
        return "visual_effect"
    if normalized in {"punch-cut", "punch-ramp"}:
        return "pacing"
    return None


def _asset_receipt(episode_dir: Path, event: dict[str, Any], lane: str) -> dict[str, Any] | None:
    if lane != "b_roll":
        return None
    slug = str(event.get("slug") or "").strip()
    if (
        not slug
        or slug in {".", ".."}
        or Path(slug).name != slug
        or "/" in slug
        or "\\" in slug
    ):
        raise SystemExit("B-roll slug 不得為空、包含 slash 或 ..")
    if any(character in slug for character in "*?[]"):
        raise SystemExit("B-roll slug 不得包含 glob metacharacters")
    assets_root = (episode_dir / "assets" / "broll").resolve()
    hits = sorted(assets_root.glob(f"{slug}.*"))
    if len(hits) != 1:
        raise SystemExit(f"asset-backed B-roll {slug or '<empty>'} 必須唯一對應 assets/broll 檔案")
    resolved = hits[0].resolve()
    try:
        resolved.relative_to(assets_root)
    except ValueError as exc:
        raise SystemExit("B-roll asset path escape") from exc
    return _artifact(resolved)


def _approved_inventory(episode_dir: Path, master_identity: dict[str, Any]) -> dict[str, str]:
    candidates_path = episode_dir / "highlights" / "candidates.json"
    winners_path = episode_dir / "highlights" / "winners.json"
    if not candidates_path.is_file() or not winners_path.is_file():
        raise SystemExit("finished manifest 需要 candidates.json 與 winners.json inventory")
    payload = json.loads(candidates_path.read_text(encoding="utf-8"))
    if (
        payload.get("editorial_master_lineage") != master_identity
        or json.loads(winners_path.read_text(encoding="utf-8")).get("editorial_master_lineage")
        != master_identity
    ):
        raise SystemExit("candidate/winner Editorial Master lineage 缺失或已過期")
    candidate_rows = {
        str(row["id"]): row
        for row in payload.get("candidates", [])
        if isinstance(row, dict) and row.get("id")
    }
    candidates = {
        str(row["id"]): str(row.get("format") or "short")
        for row in candidate_rows.values()
    }
    winners = json.loads(winners_path.read_text(encoding="utf-8")).get("winners", [])
    winner_rows = {
        str(row["id"]): row for row in winners if isinstance(row, dict) and row.get("id")
    }
    approved: dict[str, str] = {}
    for cut_id, fmt in candidates.items():
        winner = winner_rows.get(cut_id)
        if winner is None:
            continue
        approved[cut_id] = fmt
    return approved


def _cut_from_packet(
    episode_dir: Path,
    review_dir: Path,
    cut_dir: Path,
    master_identity: dict[str, Any],
) -> dict[str, Any]:
    cut_root = cut_dir.resolve()

    def contained(path: Path, label: str) -> Path:
        resolved = path.resolve()
        try:
            resolved.relative_to(cut_root)
        except ValueError as exc:
            raise SystemExit(f"{label} path escape：{path}") from exc
        if not resolved.is_file():
            raise SystemExit(f"{label} 不存在：{resolved}")
        return resolved

    events_path = cut_dir / "events.json"
    events_path = contained(events_path, "events")
    packet = json.loads(events_path.read_text(encoding="utf-8-sig"))
    if packet.get("editorial_master_lineage") != master_identity:
        raise SystemExit(f"{events_path} Editorial Master lineage 缺失或已過期")
    events = packet.get("events")
    duration = packet.get("duration_sec")
    if not isinstance(events, list):
        raise SystemExit(f"{events_path} events 必須是 array")
    if (
        not isinstance(duration, (int, float))
        or isinstance(duration, bool)
        or not math.isfinite(float(duration))
        or float(duration) <= 0
    ):
        raise SystemExit(f"{events_path} duration_sec 無效")
    duration = float(duration)
    preview_name = packet.get("preview")
    if (
        not isinstance(preview_name, str)
        or not preview_name
        or Path(preview_name).name != preview_name
        or "/" in preview_name
        or "\\" in preview_name
        or Path(preview_name).suffix.lower() != ".mp4"
    ):
        raise SystemExit(f"{events_path} preview 必須是 canonical basename .mp4")

    preview_path = contained(cut_dir / preview_name, "preview")
    subtitles_path = contained(cut_dir / "subs.srt", "subtitles")

    counts = {lane: 0 for lane in LANE_ACTIONS}
    components: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            raise SystemExit(f"{events_path} event 必須是 object")
        lane = _event_lane(str(event.get("type") or ""))
        if lane is None:
            continue
        t0, t1 = event.get("t0"), event.get("t1")
        if (
            not isinstance(t0, (int, float))
            or isinstance(t0, bool)
            or not isinstance(t1, (int, float))
            or isinstance(t1, bool)
            or float(t0) < 0
            or float(t1) <= float(t0)
            or float(t1) > duration + 0.01
        ):
            raise SystemExit(f"{events_path} review event 時間範圍無效")
        counts[lane] += 1
        component = {
            **event,
            "component_id": f"{cut_dir.name}-{lane.replace('_', '-')}-{counts[lane]:03d}",
            "lane": lane,
            "t0": float(t0),
            "t1": float(t1),
        }
        asset = _asset_receipt(episode_dir, event, lane)
        if asset is not None:
            component["asset"] = asset
        components.append(component)

    timeline = str(packet.get("timeline") or cut_dir.name)
    title = timeline.split(" - ", 1)[-1].replace("（緊·導播）", "").strip()
    return {
        "cut_id": cut_dir.name,
        "title": title,
        "artifacts": {
            "preview": _artifact(preview_path, duration_seconds=duration),
            "subtitles": _artifact(subtitles_path),
            "events": _artifact(events_path),
        },
        "editorial_master_lineage": master_identity,
        "visual_treatment_counts": counts,
        "components": components,
    }


def build_manifest(episode_dir: Path, *, review_format: str = "long") -> Path:
    episode_dir = episode_dir.resolve()
    master = _open_master(episode_dir)
    master_identity = master.identity()
    review_dir = episode_dir / "highlights" / "review"
    formats = _approved_inventory(episode_dir, master_identity)
    cut_dirs = sorted(
        path
        for path in review_dir.iterdir()
        if path.is_dir()
        and (path / "events.json").is_file()
        and path.name in formats
        and formats[path.name] == review_format
    )
    if not cut_dirs:
        raise SystemExit(f"{review_dir} 找不到 {review_format} review packet")
    cuts = [_cut_from_packet(episode_dir, review_dir, path, master_identity) for path in cut_dirs]
    lanes = list(LANE_ACTIONS)
    payload = {
        "schema": SCHEMA,
        "episode_id": episode_dir.name,
        "stage": 5,
        "gate": {
            "kind": "finished_cut_review",
            "status": "ready_for_review",
            "feedback_file": str((review_dir / "finished_review_feedback.v1.json").resolve()),
        },
        "editorial_master_lineage": master_identity,
        "cuts": cuts,
        "feedback_contract": {
            "review_lanes": lanes,
            "component_actions": LANE_ACTIONS,
            "gate_actions": ["request_changes", "approve_cut", "approve_all"],
        },
    }
    output = review_dir / OUTPUT_NAME
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    staging = output.with_suffix(output.suffix + ".tmp")
    staging.write_text(rendered, encoding="utf-8")
    os.replace(staging, output)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="從實際成片 events/artifacts 產生 finished-review manifest"
    )
    parser.add_argument("episode", help="episode 資料夾")
    parser.add_argument("--format", choices=("long", "short"), default="long")
    args = parser.parse_args(argv)
    output = build_manifest(Path(args.episode), review_format=args.format)
    print(json.dumps({"status": "built", "manifest": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
