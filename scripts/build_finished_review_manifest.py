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
import re
import sys
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.brook.script_video.highlight_broll import (  # noqa: E402
    BrollContractError,
    verify_broll_receipt,
)
from agents.brook.script_video.highlight_broll import (  # noqa: E402
    receipt_identity as broll_receipt_identity,
)

SCHEMA = "nakama.finished_cut_review_manifest.v1"
OUTPUT_NAME = "finished_review_manifest_current.json"
IDENTITY_NAME = "finished_review_component_identity.v1.json"
IDENTITY_CONTRACT = "finished-review-component-identity-v1"
MIN_LONG_STOCK_VIDEOS = 3

_COMPONENT_ID_PREFIXES = {
    "b_roll": ("broll", "b-roll"),
    "identity_card": ("identity", "identity-card", "namecard"),
    "hero_title": ("hero", "hero-title"),
    "badge": ("badge",),
    "fullscreen_transition": ("transition", "fullscreen-transition"),
    "visual_effect": ("visual", "visual-effect"),
    "pacing": ("pacing",),
}

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


def _canonical_hash(payload: Any) -> str:
    rendered = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def _identity_snapshot(component: dict[str, Any]) -> dict[str, Any]:
    variables = component.get("vars")
    title = variables.get("title") if isinstance(variables, dict) else None
    text = component.get("text") or title or component.get("slug") or ""
    return {
        "lane": component.get("lane"),
        "t0": round(float(component.get("t0", 0.0)), 3),
        "t1": round(float(component.get("t1", 0.0)), 3),
        "text": str(text).replace("/", "\n"),
    }


def _validated_source_components(payload: dict[str, Any], episode_id: str) -> dict[str, list[dict]]:
    if payload.get("schema") != SCHEMA or payload.get("episode_id") != episode_id:
        raise SystemExit("component identity source manifest schema／episode 不相符")
    result: dict[str, list[dict]] = {}
    seen_ids: set[str] = set()
    for cut in payload.get("cuts", []):
        if not isinstance(cut, dict) or not isinstance(cut.get("cut_id"), str):
            raise SystemExit("component identity source cut 無效")
        cut_id = cut["cut_id"]
        rows: list[dict] = []
        lane_ordinals: dict[str, int] = {}
        lane_prefixes: dict[str, str] = {}
        for component in cut.get("components", []):
            if not isinstance(component, dict):
                raise SystemExit("component identity source component 無效")
            lane = str(component.get("lane") or "")
            component_id = str(component.get("component_id") or "")
            prefixes = _COMPONENT_ID_PREFIXES.get(lane)
            match = None
            if prefixes:
                for prefix in prefixes:
                    candidate = re.fullmatch(
                        rf"{re.escape(cut_id)}-{re.escape(prefix)}-(\d{{3}})", component_id
                    )
                    if candidate:
                        match = (prefix, int(candidate.group(1)))
                        break
            lane_ordinals[lane] = lane_ordinals.get(lane, 0) + 1
            if (
                match is None
                or match[1] != lane_ordinals[lane]
                or component_id in seen_ids
                or (lane in lane_prefixes and lane_prefixes[lane] != match[0])
            ):
                raise SystemExit(
                    f"component identity source 不接受任意 remap：{component_id or '<empty>'}"
                )
            lane_prefixes[lane] = match[0]
            seen_ids.add(component_id)
            rows.append(
                {
                    "component_id": component_id,
                    "lane": lane,
                    "snapshot": _identity_snapshot(component),
                }
            )
        result[cut_id] = rows
    return result


def _identity_path(review_dir: Path) -> Path:
    return review_dir / IDENTITY_NAME


def _build_identity_registry(
    episode_dir: Path,
    payload: dict[str, Any],
    source_path: Path | None,
) -> dict[str, Any]:
    components = _validated_source_components(payload, episode_dir.name)
    core: dict[str, Any] = {
        "contract": IDENTITY_CONTRACT,
        "episode_id": episode_dir.name,
        "source_manifest": (
            {"filename": source_path.name, "sha256": _sha256(source_path)}
            if source_path is not None
            else None
        ),
        "cuts": components,
    }
    return {**core, "content_hash": _canonical_hash(core)}


def _write_identity_registry(review_dir: Path, registry: dict[str, Any]) -> None:
    path = _identity_path(review_dir)
    staging = path.with_suffix(path.suffix + ".tmp")
    staging.write_text(
        json.dumps(registry, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(staging, path)


def _read_identity_registry(episode_dir: Path, review_dir: Path) -> dict[str, Any] | None:
    path = _identity_path(review_dir)
    if not path.is_file():
        return None
    try:
        registry = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit("component identity registry 無法讀取") from exc
    core = {key: value for key, value in registry.items() if key != "content_hash"}
    if (
        registry.get("contract") != IDENTITY_CONTRACT
        or registry.get("episode_id") != episode_dir.name
        or registry.get("content_hash") != _canonical_hash(core)
        or not isinstance(registry.get("cuts"), dict)
    ):
        raise SystemExit("component identity registry 已漂移或格式無效")
    # Re-run ID/prefix/ordinal validation without trusting the stored mapping.
    _validated_source_components(
        {
            "schema": SCHEMA,
            "episode_id": episode_dir.name,
            "cuts": [
                {
                    "cut_id": cut_id,
                    "components": [
                        {
                            "component_id": row.get("component_id"),
                            "lane": row.get("lane"),
                            **(row.get("snapshot") or {}),
                        }
                        for row in rows
                    ],
                }
                for cut_id, rows in registry["cuts"].items()
            ],
        },
        episode_dir.name,
    )
    return registry


def _apply_component_identity(
    cut_id: str, components: list[dict[str, Any]], registry: dict[str, Any] | None
) -> None:
    if registry is None:
        return
    source_rows = registry.get("cuts", {}).get(cut_id, [])
    for lane in LANE_ACTIONS:
        old = [row for row in source_rows if row.get("lane") == lane]
        new = [row for row in components if row.get("lane") == lane]
        assigned_new: set[int] = set()
        assigned_old: set[int] = set()
        # Preserve exact unchanged identities first. This keeps the surviving ID
        # stable when another component in the same lane is removed.
        for old_index, source in enumerate(old):
            matches = [
                new_index
                for new_index, component in enumerate(new)
                if new_index not in assigned_new
                and _identity_snapshot(component) == source.get("snapshot")
            ]
            if len(matches) == 1:
                new[matches[0]]["component_id"] = source["component_id"]
                assigned_new.add(matches[0])
                assigned_old.add(old_index)
        # A text edit keeps its original range; a move keeps its original text.
        # These two independent keys cover simultaneous changes elsewhere in
        # the lane without falling back to a positional remap.
        for key in ("time", "text"):
            for old_index, source in enumerate(old):
                if old_index in assigned_old:
                    continue
                source_snapshot = source.get("snapshot") or {}
                matches = []
                for new_index, component in enumerate(new):
                    if new_index in assigned_new:
                        continue
                    snapshot = _identity_snapshot(component)
                    if key == "time":
                        matched = (snapshot["t0"], snapshot["t1"]) == (
                            source_snapshot.get("t0"),
                            source_snapshot.get("t1"),
                        )
                    else:
                        matched = bool(snapshot["text"]) and snapshot[
                            "text"
                        ] == source_snapshot.get("text")
                    if matched:
                        matches.append(new_index)
                if len(matches) == 1:
                    new[matches[0]]["component_id"] = source["component_id"]
                    assigned_new.add(matches[0])
                    assigned_old.add(old_index)
        old_remaining = [index for index in range(len(old)) if index not in assigned_old]
        new_remaining = [index for index in range(len(new)) if index not in assigned_new]
        # Same-cardinality changes are edit/move compatible: preserve by lane
        # order. Cardinality drift is not guessed; unmatched new items retain a
        # new canonical ID and removed IDs remain absent.
        if len(old_remaining) == len(new_remaining):
            for old_index, new_index in zip(old_remaining, new_remaining, strict=True):
                new[new_index]["component_id"] = old[old_index]["component_id"]


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
    identity_registry: dict[str, Any] | None,
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
    stock_video_count = 0
    stock_video_hashes: set[str] = set()
    seen_stock_video_slugs: set[str] = set()
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
            if str(event.get("type") or "").strip().lower().replace("_", "-") == "video":
                component["asset_category"] = "stock_video"
                stock_video_count += 1
                if asset["sha256"] in stock_video_hashes:
                    raise SystemExit(f"{events_path} Stock Video 使用重複素材")
                stock_video_hashes.add(asset["sha256"])
        components.append(component)

    broll_plan_path = episode_dir / "highlights" / "tighten" / f"{cut_dir.name}_broll.json"
    try:
        broll_items = json.loads(broll_plan_path.read_text(encoding="utf-8"))["items"]
        stock_video_receipt = verify_broll_receipt(
            episode_dir,
            cut_dir.name,
            "long",
            broll_items,
            master_identity,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SystemExit(
            f"{broll_plan_path} 不是合法 B-roll plan／materialization receipt"
        ) from exc
    except BrollContractError as exc:
        raise SystemExit(f"Stock Video materialization receipt 驗證失敗：{exc}") from exc
    if packet.get("stock_video_lineage") != broll_receipt_identity(stock_video_receipt):
        raise SystemExit(f"{events_path} Stock Video lineage 缺失或已過期")
    receipt_stock = {row["slug"]: row for row in stock_video_receipt["stock_videos"]}
    for component in components:
        if component.get("asset_category") != "stock_video":
            continue
        expected_stock = receipt_stock.get(str(component.get("slug") or ""))
        if expected_stock is None:
            raise SystemExit(f"{events_path} Stock Video 不在 materialized plan")
        actual_asset = component["asset"]
        relative_asset = Path(actual_asset["path"]).resolve().relative_to(episode_dir).as_posix()
        if (
            float(component["t0"]) != float(expected_stock["t0"])
            or float(component["t1"]) != float(expected_stock["t1"])
            or relative_asset != expected_stock["asset"]["path"]
            or actual_asset["bytes"] != expected_stock["asset"]["bytes"]
            or actual_asset["sha256"] != expected_stock["asset"]["sha256"]
        ):
            raise SystemExit(f"{events_path} Stock Video timing／asset receipt 已漂移")
        seen_stock_video_slugs.add(expected_stock["slug"])

    if seen_stock_video_slugs != set(receipt_stock):
        missing = sorted(set(receipt_stock) - seen_stock_video_slugs)
        extra = sorted(seen_stock_video_slugs - set(receipt_stock))
        raise SystemExit(
            f"{events_path} Stock Video events 與 materialized plan 不一致；"
            f"missing={missing}, extra={extra}"
        )

    if stock_video_count < MIN_LONG_STOCK_VIDEOS:
        raise SystemExit(
            f"{cut_dir.name} long highlight 需要至少 {MIN_LONG_STOCK_VIDEOS} 個 Stock Video；"
            f"目前 {stock_video_count} 個，缺 {MIN_LONG_STOCK_VIDEOS - stock_video_count} 個。"
            "guest-namecard／Hero Title／transition／badge／紙紋／generated card 都不計數。"
        )

    _apply_component_identity(cut_dir.name, components, identity_registry)

    timeline = str(packet.get("timeline") or cut_dir.name)
    title = timeline.split(" - ", 1)[-1].replace("（緊·導播）", "").strip()
    return {
        "cut_id": cut_dir.name,
        "format": "long",
        "title": title,
        "artifacts": {
            "preview": _artifact(preview_path, duration_seconds=duration),
            "subtitles": _artifact(subtitles_path),
            "events": _artifact(events_path),
        },
        "editorial_master_lineage": master_identity,
        "visual_treatment_counts": counts,
        "stock_video_count": stock_video_count,
        "components": components,
    }


def _manifest_payload(
    episode_dir: Path,
    *,
    review_format: str = "long",
    identity_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
    cuts = [
        _cut_from_packet(episode_dir, review_dir, path, master_identity, identity_registry)
        for path in cut_dirs
    ]
    lanes = list(LANE_ACTIONS)
    return {
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


def build_manifest(episode_dir: Path, *, review_format: str = "long") -> Path:
    episode_dir = episode_dir.resolve()
    review_dir = episode_dir / "highlights" / "review"
    identity_registry = _read_identity_registry(episode_dir, review_dir)
    if identity_registry is None:
        legacy_candidates = sorted(
            path
            for path in review_dir.glob("finished_review_manifest_*.json")
            if path.name != OUTPUT_NAME and path.is_file()
        )
        current_path = review_dir / OUTPUT_NAME
        source_path = (
            legacy_candidates[-1]
            if legacy_candidates
            else current_path
            if current_path.is_file()
            else None
        )
        if source_path is not None:
            try:
                source_payload = json.loads(source_path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SystemExit("component identity source manifest 無法讀取") from exc
            identity_registry = _build_identity_registry(
                episode_dir, source_payload, source_path
            )
        else:
            initial = _manifest_payload(episode_dir, review_format=review_format)
            identity_registry = _build_identity_registry(episode_dir, initial, None)
        _write_identity_registry(review_dir, identity_registry)
    payload = _manifest_payload(
        episode_dir,
        review_format=review_format,
        identity_registry=identity_registry,
    )
    output = episode_dir / "highlights" / "review" / OUTPUT_NAME
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    staging = output.with_suffix(output.suffix + ".tmp")
    staging.write_text(rendered, encoding="utf-8")
    os.replace(staging, output)
    return output


def verify_finished_review_manifest(
    episode_dir: Path,
    expected_manifest_path: Path,
    *,
    review_format: str = "long",
) -> dict[str, Any]:
    """Freshly rebuild the canonical manifest and reject every self-reported field."""

    expected_manifest_path = expected_manifest_path.resolve()
    try:
        supplied = json.loads(expected_manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"finished manifest 無法讀取：{expected_manifest_path}") from exc
    identity_registry = _read_identity_registry(
        episode_dir.resolve(), episode_dir.resolve() / "highlights" / "review"
    )
    if identity_registry is None:
        raise SystemExit("component identity registry 不存在；先由 trusted builder 建立")
    rebuilt = _manifest_payload(
        episode_dir,
        review_format=review_format,
        identity_registry=identity_registry,
    )
    if supplied != rebuilt:
        raise SystemExit(
            "finished manifest 與 current plan／receipt／assets／events fresh rebuild 不一致"
        )
    return rebuilt


def _replacement_text(component: dict[str, Any]) -> str:
    if component.get("lane") == "hero_title":
        return str(component.get("text") or component.get("slug") or "").replace("/", "\n")
    variables = component.get("vars")
    if isinstance(variables, dict) and variables.get("title") is not None:
        return str(variables["title"])
    return str(component.get("text") or component.get("slug") or "")


def verify_finished_review_cut(
    episode_dir: Path,
    cut_id: str,
    expected_manifest_path: Path,
    *,
    feedback_rows: list[dict[str, Any]] | None = None,
    source_preview_sha256: str | None = None,
    require_preview_change: bool = False,
) -> dict[str, Any]:
    """Authoritative worker/router verification for one revised long Highlight."""

    manifest = verify_finished_review_manifest(episode_dir, expected_manifest_path)
    cut = next((row for row in manifest["cuts"] if row["cut_id"] == cut_id), None)
    if cut is None:
        raise SystemExit(f"finished manifest 找不到 requested cut：{cut_id}")
    components = {row["component_id"]: row for row in cut["components"]}
    for feedback in feedback_rows or []:
        if feedback.get("cut_id") != cut_id:
            continue
        component_id = str(feedback.get("component_id") or "")
        action = str(feedback.get("action") or "")
        component = components.get(component_id)
        if action == "remove":
            if component is not None:
                raise SystemExit(f"revision 未移除 component：{component_id}")
            continue
        if action in {"edit_text", "move", "replace_asset", "change_type"} and component is None:
            raise SystemExit(f"revision 找不到 component：{component_id}")
        if action == "edit_text" and _replacement_text(component) != str(
            feedback.get("replacement") or ""
        ):
            raise SystemExit(f"revision Hero／Title replacement 未 exact 套用：{component_id}")
        if action == "move":
            target = feedback.get("move_to_seconds")
            original = feedback.get("timeline_seconds")
            if not isinstance(target, (int, float)) or not isinstance(original, dict):
                raise SystemExit(f"revision move feedback 無效：{component_id}")
            original_span = float(original["t1"]) - float(original["t0"])
            if (
                abs(float(component["t0"]) - float(target)) > 0.001
                or abs(float(component["t1"]) - (float(target) + original_span)) > 0.001
            ):
                raise SystemExit(f"revision move 未 exact 套用：{component_id}")
        if action == "replace_asset" and str(component.get("slug") or "") != str(
            feedback.get("replacement") or ""
        ):
            raise SystemExit(f"revision replacement asset 未 exact 套用：{component_id}")
        if action == "change_type" and str(component.get("type") or "") != str(
            feedback.get("replacement") or ""
        ):
            raise SystemExit(f"revision component type 未 exact 套用：{component_id}")

    preview = cut["artifacts"]["preview"]
    if require_preview_change:
        if not source_preview_sha256 or preview["sha256"] == source_preview_sha256:
            raise SystemExit(f"revision preview 沒有改變：{cut_id}")
    return {
        "status": "verified_for_human_rereview",
        "approved": False,
        "cut_id": cut_id,
        "manifest_sha256": _sha256(expected_manifest_path),
        "preview_sha256": preview["sha256"],
        "stock_video_count": cut["stock_video_count"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="從實際成片 events/artifacts 產生 finished-review manifest"
    )
    parser.add_argument("episode", help="episode 資料夾")
    parser.add_argument("--format", choices=("long", "short"), default="long")
    parser.add_argument(
        "--verify",
        nargs="?",
        const=OUTPUT_NAME,
        help="fresh rebuild 後 exact 驗證 manifest；省略路徑時驗 canonical current",
    )
    args = parser.parse_args(argv)
    if args.verify:
        manifest = Path(args.verify)
        if not manifest.is_absolute():
            manifest = Path(args.episode) / "highlights" / "review" / manifest
        verified = verify_finished_review_manifest(
            Path(args.episode), manifest, review_format=args.format
        )
        print(
            json.dumps(
                {
                    "status": "verified",
                    "manifest": str(manifest.resolve()),
                    "cuts": len(verified["cuts"]),
                },
                ensure_ascii=False,
            )
        )
        return 0
    output = build_manifest(Path(args.episode), review_format=args.format)
    print(json.dumps({"status": "built", "manifest": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
