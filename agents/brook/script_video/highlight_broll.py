"""Fail-closed Stock Video contract for Podcast long Highlights.

The creative Director still chooses the footage and timings.  This module owns
the mechanical release boundary: a long Highlight cannot advance unless its
current plan contains at least three distinct, episode-local Stock Video files.
Generated cards, photos, badges and identity cards are visual treatments, but
they are deliberately not counted as Stock Video.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

LEGACY_CONTRACT = "podcast-long-highlight-stock-video-v1"
CONTRACT = "podcast-long-highlight-stock-video-v2"
MIN_LONG_STOCK_VIDEOS = 3
RECEIPT_SUFFIX = "_broll_materialization.json"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
STRUCTURAL_BROLL_KINDS = {"camera-correction", "guest-namecard", "badge"}


class BrollContractError(ValueError):
    """The Stock Video plan or its materialized receipt is not trustworthy."""


def parse_provenance_acquired_at(value: Any) -> datetime:
    """Parse timezone-aware ISO-8601 consistently on Python 3.10 through 3.12."""
    text = str(value or "").strip()
    normalized = f"{text[:-1]}+00:00" if text.endswith("Z") else text
    fractional = re.fullmatch(
        r"(?P<prefix>.+T\d{2}:\d{2}:\d{2})\.(?P<fraction>\d+)(?P<offset>[+-]\d{2}:\d{2})",
        normalized,
    )
    if fractional and len(fractional.group("fraction")) > 6:
        normalized = (
            f"{fractional.group('prefix')}."
            f"{fractional.group('fraction')[:6]}{fractional.group('offset')}"
        )
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("invalid ISO-8601 provenance timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("provenance timestamp must be timezone-aware")
    return parsed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def probe_stock_video(path: Path) -> dict[str, Any]:
    """Return deterministic ffprobe evidence for a real, playable video stream."""

    executable = shutil.which("ffprobe")
    if executable is None:
        raise BrollContractError("找不到 ffprobe，無法驗證 Stock Video 真實影片 stream")
    process = subprocess.run(
        [
            executable,
            "-v",
            "error",
            "-show_entries",
            "stream=index,codec_name,codec_type,width,height,duration:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if process.returncode != 0:
        detail = (process.stderr or "").strip().splitlines()
        suffix = f"：{detail[-1][:180]}" if detail else ""
        raise BrollContractError(f"Stock Video {path.name} ffprobe 失敗{suffix}")
    try:
        payload = json.loads(process.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise BrollContractError(f"Stock Video {path.name} ffprobe 回傳無效 JSON") from exc
    streams = []
    for raw in payload.get("streams", []):
        if not isinstance(raw, dict) or raw.get("codec_type") != "video":
            continue
        codec = str(raw.get("codec_name") or "").strip()
        try:
            width = int(raw.get("width"))
            height = int(raw.get("height"))
        except (TypeError, ValueError) as exc:
            raise BrollContractError(f"Stock Video {path.name} video stream 尺寸無效") from exc
        if not codec or width <= 0 or height <= 0:
            raise BrollContractError(f"Stock Video {path.name} video stream metadata 無效")
        streams.append(
            {
                "index": int(raw.get("index", 0)),
                "codec_name": codec,
                "width": width,
                "height": height,
            }
        )
    if not streams:
        raise BrollContractError(f"Stock Video {path.name} 找不到真實 video stream")
    duration_values = [payload.get("format", {}).get("duration")]
    duration_values.extend(
        raw.get("duration")
        for raw in payload.get("streams", [])
        if isinstance(raw, dict) and raw.get("codec_type") == "video"
    )
    duration_seconds = 0.0
    for raw in duration_values:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            duration_seconds = max(duration_seconds, value)
    if duration_seconds <= 0:
        raise BrollContractError(f"Stock Video {path.name} duration 必須大於 0")
    return {
        "duration_seconds": round(duration_seconds, 6),
        "video_streams": streams,
    }


def receipt_path(episode_dir: Path, cut_id: str) -> Path:
    return episode_dir / "highlights" / "tighten" / f"{cut_id}{RECEIPT_SUFFIX}"


def receipt_identity(receipt: dict[str, Any]) -> dict[str, Any]:
    """Return the stable lineage embedded in downstream review packets."""

    return {
        "contract": receipt.get("contract"),
        "cut_id": receipt.get("cut_id"),
        "content_hash": receipt.get("content_hash"),
        "stock_video_count": receipt.get("stock_video_count"),
        "visual_pipeline_lineage": receipt.get("visual_pipeline_lineage"),
    }


def _safe_slug(value: Any) -> str:
    slug = str(value or "").strip()
    if (
        not slug
        or slug in {".", ".."}
        or Path(slug).name != slug
        or "/" in slug
        or "\\" in slug
        or any(character in slug for character in "*?[]")
    ):
        raise BrollContractError("Stock Video slug 不得為空、包含路徑或 glob 字元")
    return slug


def _http_url(value: Any, label: str) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise BrollContractError(f"Stock Video provenance.{label} 必須是 http(s) URL")
    return url


def _provenance(item: dict[str, Any], slug: str) -> dict[str, str]:
    raw = item.get("provenance")
    if not isinstance(raw, dict):
        raise BrollContractError(f"Stock Video {slug} 缺少 provenance")
    source_url = _http_url(raw.get("source_url"), "source_url")
    try:
        acquired = parse_provenance_acquired_at(raw.get("acquired_at"))
    except ValueError as exc:
        raise BrollContractError(f"Stock Video {slug} provenance.acquired_at 無效") from exc
    evidence: dict[str, str] = {}
    for key in ("license_url", "terms_url"):
        if raw.get(key):
            evidence[key] = _http_url(raw[key], key)
    if raw.get("license_id"):
        evidence["license_id"] = str(raw["license_id"]).strip()
        if not evidence["license_id"]:
            raise BrollContractError(f"Stock Video {slug} provenance.license_id 不得為空")
    if not evidence:
        raise BrollContractError(
            f"Stock Video {slug} provenance 需要 license_url、terms_url 或 license_id"
        )
    return {
        "source_url": source_url,
        "acquired_at": acquired.isoformat(),
        **evidence,
    }


def _asset(episode_dir: Path, slug: str) -> dict[str, Any]:
    root = (episode_dir / "assets" / "broll").resolve()
    hits = sorted(path for path in root.glob(f"{slug}.*") if path.is_file())
    if len(hits) != 1:
        raise BrollContractError(
            f"Stock Video {slug} 必須唯一對應 assets/broll/<slug>.<video>；目前 {len(hits)} 個"
        )
    resolved = hits[0].resolve()
    try:
        relative = resolved.relative_to(episode_dir.resolve())
        resolved.relative_to(root)
    except ValueError as exc:
        raise BrollContractError(f"Stock Video {slug} path escape") from exc
    if resolved.suffix.lower() not in VIDEO_EXTENSIONS:
        raise BrollContractError(f"Stock Video {slug} 不是支援的影片格式")
    return {
        "path": relative.as_posix(),
        "bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
        "media": probe_stock_video(resolved),
    }


def _stock_video_rows(episode_dir: Path, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    slugs: set[str] = set()
    hashes: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise BrollContractError(f"B-roll item {index} 必須是 object")
        if str(item.get("kind") or "").strip().lower() != "video":
            continue
        slug = _safe_slug(item.get("slug"))
        try:
            t0 = float(item["t0"])
            t1 = float(item["t1"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BrollContractError(f"Stock Video {slug} 時間範圍無效") from exc
        if not math.isfinite(t0) or not math.isfinite(t1) or t0 < 0 or t1 <= t0:
            raise BrollContractError(f"Stock Video {slug} 時間範圍無效")
        asset = _asset(episode_dir, slug)
        provenance = _provenance(item, slug)
        if slug in slugs or asset["sha256"] in hashes:
            raise BrollContractError(f"Stock Video {slug} 使用重複素材")
        slugs.add(slug)
        hashes.add(asset["sha256"])
        rows.append(
            {
                "category": "stock_video",
                "kind": "video",
                "slug": slug,
                "t0": t0,
                "t1": t1,
                "asset": asset,
                "provenance": provenance,
            }
        )
    return rows


def _visual_projection(value: Any, label: str) -> dict[str, Any]:
    """Validate one nested DP materialization without importing core at module load.

    The visual contract imports :func:`probe_stock_video` from this module.  A
    lazy import here keeps that dependency one-way at runtime and avoids a
    circular module initialization.
    """

    try:
        from agents.brook.script_video.highlight_visual_pipeline import (
            HighlightVisualContractError,
            validate_materialization_projection,
        )

        return validate_materialization_projection(value, label=label)
    except HighlightVisualContractError as exc:
        raise BrollContractError(
            f"{label} 缺少或不符合已通過 Director／DP／Semantic Audit 的 materialization：{exc}"
        ) from exc


def _same_number(raw: Any, expected: Any, label: str) -> None:
    if (
        isinstance(raw, bool)
        or not isinstance(raw, (int, float))
        or isinstance(expected, bool)
        or not isinstance(expected, (int, float))
        or not math.isfinite(float(raw))
        or float(raw) != float(expected)
    ):
        raise BrollContractError(f"{label} 與 audited DP materialization 不一致")


def broll_item_projection(item: Any, index: int) -> dict[str, Any] | None:
    """Map one B-roll recipe row to its exact audited DP projection.

    Camera corrections, identity namecards and the brand badge have separate
    structural/identity contracts and therefore MUST omit visual_materialization.
    Every other row is content-bearing and fails closed without the nested
    authoritative projection.
    """

    if not isinstance(item, dict):
        raise BrollContractError(f"B-roll item {index} 必須是 object")
    kind = str(item.get("kind") or "").strip().lower()
    legacy_identity_namecard = (
        kind == "concept"
        and str(item.get("slug") or "").strip().lower() == "guest-namecard"
    )
    if kind in STRUCTURAL_BROLL_KINDS or legacy_identity_namecard:
        if "visual_materialization" in item:
            raise BrollContractError(
                f"B-roll item {index} 是 structural visual，不得冒用 Director／DP materialization"
            )
        return None
    expected = {
        "video": ("broll_track2", "stock_video"),
        "photo": ("broll_track2", "photo"),
        "sticker": ("content_card_track4", "sticker_pair"),
        "concept": (
            "content_card_track4",
            str(item.get("comp") or "concept_card").strip(),
        ),
    }.get(kind)
    if expected is None:
        raise BrollContractError(f"B-roll item {index} kind={kind or '<empty>'} 不合法")
    projection = _visual_projection(
        item.get("visual_materialization"), f"B-roll item {index}.visual_materialization"
    )
    target_lane, implementation_kind = expected
    if (
        projection["target_lane"] != target_lane
        or projection["implementation_kind"] != implementation_kind
    ):
        raise BrollContractError(
            f"B-roll item {index} target／implementation 與 audited DP materialization 不一致"
        )
    _same_number(item.get("t0"), projection["t0"], f"B-roll item {index}.t0")
    _same_number(item.get("t1"), projection["t1"], f"B-roll item {index}.t1")
    if item.get("source_range") != projection["source_range"]:
        raise BrollContractError(
            f"B-roll item {index}.source_range 與 audited DP selected range 不一致"
        )
    source_range = projection["source_range"]
    if not isinstance(source_range, dict):
        raise BrollContractError(f"B-roll item {index}.source_range 不合法")
    _same_number(
        item.get("src_in", source_range.get("start_sec")),
        source_range.get("start_sec"),
        f"B-roll item {index}.src_in",
    )
    media = projection["media"]
    if not isinstance(media, dict) or item.get("media_path") != media.get("path"):
        raise BrollContractError(
            f"B-roll item {index}.media_path 與 audited DP selected media 不一致"
        )
    for field in ("on_screen_text", "provenance", "render_spec"):
        if item.get(field) != projection[field]:
            raise BrollContractError(
                f"B-roll item {index}.{field} 與 audited DP materialization 不一致"
            )
    if kind in {"sticker", "concept"}:
        spec = projection["render_spec"]
        if (
            not isinstance(spec, dict)
            or item.get("vars") != spec.get("render_params")
            or spec.get("component") != implementation_kind
        ):
            raise BrollContractError(
                f"B-roll item {index} render recipe 與 audited DP preview 不一致"
            )
    elif projection["render_spec"] is not None:
        raise BrollContractError(f"B-roll item {index} asset 不得攜帶 render_spec")
    return projection


def title_item_projection(item: Any, index: int) -> dict[str, Any]:
    """Map one title recipe row to its exact audited DP projection."""

    if not isinstance(item, dict):
        raise BrollContractError(f"Title item {index} 必須是 object")
    try:
        tier = int(item.get("tier", 2))
    except (TypeError, ValueError) as exc:
        raise BrollContractError(f"Title item {index}.tier 不合法") from exc
    if tier not in {1, 2}:
        raise BrollContractError(f"Title item {index}.tier 必須是 1 或 2")
    expected_kind = "hero_title" if tier == 1 else "supporting_title"
    projection = _visual_projection(
        item.get("visual_materialization"), f"Title item {index}.visual_materialization"
    )
    if (
        projection["target_lane"] != "title_track3"
        or projection["implementation_kind"] != expected_kind
    ):
        raise BrollContractError(
            f"Title item {index} target／implementation 與 audited DP materialization 不一致"
        )
    _same_number(item.get("t0"), projection["t0"], f"Title item {index}.t0")
    _same_number(item.get("t1"), projection["t1"], f"Title item {index}.t1")
    if item.get("source_range") != projection["source_range"]:
        raise BrollContractError(
            f"Title item {index}.source_range 與 audited DP preview range 不一致"
        )
    if item.get("text") != projection["on_screen_text"]:
        raise BrollContractError(
            f"Title item {index}.text 與 Director 核准的 on_screen_text 不一致"
        )
    media = projection["media"]
    if not isinstance(media, dict) or item.get("media_path") != media.get("path"):
        raise BrollContractError(
            f"Title item {index}.media_path 與 audited DP preview 不一致"
        )
    for field in ("provenance", "render_spec"):
        if item.get(field) != projection[field]:
            raise BrollContractError(
                f"Title item {index}.{field} 與 audited DP materialization 不一致"
            )
    spec = projection["render_spec"]
    params = spec.get("render_params") if isinstance(spec, dict) else None
    if not isinstance(params, dict) or (
        params.get("text") != item.get("text")
        or params.get("tier") != tier
        or params.get("style") != item.get("style")
        or params.get("pos_y") != item.get("pos_y")
        or spec.get("component") not in {"punch_card", "punch_card_wide"}
    ):
        raise BrollContractError(
            f"Title item {index} render recipe 與 audited DP preview 不一致"
        )
    _same_number(
        params.get("show_sec"),
        float(projection["t1"]) - float(projection["t0"]),
        f"Title item {index}.show_sec",
    )
    return projection


def collect_visual_recipe_projections(
    episode_dir: Path,
    cut_id: str,
    *,
    broll_items: list[dict[str, Any]] | None = None,
    title_items: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load both materializer recipes and return (all, broll-only) projections."""

    tighten = episode_dir / "highlights" / "tighten"
    if broll_items is None:
        path = tighten / f"{cut_id}_broll.json"
        try:
            broll_items = json.loads(path.read_text(encoding="utf-8"))["items"]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise BrollContractError(
                f"Director／DP visual pipeline 尚未產生合法 B-roll recipe：{path}"
            ) from exc
    if title_items is None:
        path = tighten / f"{cut_id}_titles.json"
        try:
            title_items = json.loads(path.read_text(encoding="utf-8"))["titles"]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise BrollContractError(
                f"Director／DP visual pipeline 尚未產生合法 title recipe：{path}"
            ) from exc
    if not isinstance(broll_items, list) or not isinstance(title_items, list):
        raise BrollContractError("Director／DP visual recipes 必須是 arrays")
    broll = [
        projection
        for index, item in enumerate(broll_items)
        if (projection := broll_item_projection(item, index)) is not None
    ]
    titles = [title_item_projection(item, index) for index, item in enumerate(title_items)]
    all_items = [*broll, *titles]
    identifiers = [item["materialization_id"] for item in all_items]
    if len(identifiers) != len(set(identifiers)):
        raise BrollContractError("visual recipes 重複使用同一 materialization_id")
    return all_items, broll


def verify_visual_recipe_lineage(
    episode_dir: Path,
    cut_id: str,
    cut_format: str,
    editorial_master_lineage: dict[str, Any],
    *,
    broll_items: list[dict[str, Any]] | None = None,
    title_items: list[dict[str, Any]] | None = None,
    editorial_master: object | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Freshly verify both recipes against the currently selected audit generation."""

    projections, broll = collect_visual_recipe_projections(
        episode_dir,
        cut_id,
        broll_items=broll_items,
        title_items=title_items,
    )
    try:
        from agents.brook.script_video.highlight_visual_pipeline import (
            HighlightVisualContractError,
            verify_visual_lineage,
        )

        lineage = verify_visual_lineage(
            episode_dir,
            cut_id,
            cut_format=cut_format,
            items=projections,
            editorial_master_lineage=editorial_master_lineage,
            editorial_master=editorial_master,
        )
    except HighlightVisualContractError as exc:
        raise BrollContractError(
            "Director／DP／Semantic Audit visual pipeline 驗證失敗：" + str(exc)
        ) from exc
    return lineage, broll


def _visual_lineage_identity(lineage: dict[str, Any]) -> dict[str, Any]:
    """Persist the selected CURRENT generation and its complete hash DAG."""

    result = {
        key: lineage.get(key)
        for key in (
            "contract",
            "episode_id",
            "cut_id",
            "revision_id",
            "format",
            "content_hash",
        )
    }
    result["editorial_master"] = lineage.get("editorial_master")
    pointer = lineage.get("current_pointer")
    if not isinstance(pointer, dict):
        raise BrollContractError("visual lineage 缺少 CURRENT pointer identity")
    result["current_pointer"] = dict(pointer)
    for key in ("work_packet", "director_plan", "dp_fulfillment", "semantic_audit"):
        identity = lineage.get(key)
        if not isinstance(identity, dict):
            raise BrollContractError(f"visual lineage 缺少 {key} identity")
        result[key] = dict(identity)
    return result


def build_broll_receipt(
    episode_dir: Path,
    cut_id: str,
    cut_format: str,
    items: list[dict[str, Any]],
    editorial_master_lineage: dict[str, Any],
) -> dict[str, Any]:
    """Build the legacy mechanical stock/license receipt.

    This remains a useful acquisition validator, but v1 can no longer authorize
    a materialization.  Release callers must use
    :func:`build_authoritative_broll_receipt`.
    """

    if not isinstance(items, list):
        raise BrollContractError("B-roll plan items 必須是 array")
    stock_videos = _stock_video_rows(episode_dir, items)
    if cut_format == "long" and len(stock_videos) < MIN_LONG_STOCK_VIDEOS:
        raise BrollContractError(
            f"long highlight 需要至少 {MIN_LONG_STOCK_VIDEOS} 個 Stock Video；"
            f"目前 {len(stock_videos)} 個，缺 {MIN_LONG_STOCK_VIDEOS - len(stock_videos)} 個"
        )
    core = {
        "contract": LEGACY_CONTRACT,
        "cut_id": cut_id,
        "format": cut_format,
        "editorial_master_lineage": editorial_master_lineage,
        "plan_sha256": _canonical_hash(items),
        "stock_video_count": len(stock_videos),
        "stock_videos": stock_videos,
    }
    return {**core, "content_hash": _canonical_hash(core)}


def build_authoritative_broll_receipt(
    episode_dir: Path,
    cut_id: str,
    cut_format: str,
    items: list[dict[str, Any]],
    editorial_master_lineage: dict[str, Any],
    *,
    title_items: list[dict[str, Any]] | None = None,
    editorial_master: object | None = None,
) -> dict[str, Any]:
    """Build the v2 release receipt from the current audited DP selections."""

    if not isinstance(items, list):
        raise BrollContractError("B-roll plan items 必須是 array")
    lineage, broll_projections = verify_visual_recipe_lineage(
        episode_dir,
        cut_id,
        cut_format,
        editorial_master_lineage,
        broll_items=items,
        title_items=title_items,
        editorial_master=editorial_master,
    )
    stock_videos: list[dict[str, Any]] = []
    stock_hashes: set[str] = set()
    raw_by_materialization = {
        item["visual_materialization"]["materialization_id"]: item
        for item in items
        if isinstance(item, dict) and isinstance(item.get("visual_materialization"), dict)
    }
    for projection in broll_projections:
        if projection["implementation_kind"] != "stock_video":
            continue
        raw = raw_by_materialization.get(projection["materialization_id"])
        if raw is None:
            raise BrollContractError("audited Stock Video 找不到 exact recipe row")
        media = projection["media"]
        path = (episode_dir / str(media["path"])).resolve()
        try:
            relative = path.relative_to(episode_dir.resolve())
        except ValueError as exc:
            raise BrollContractError("audited Stock Video media path escape") from exc
        digest = str(media["sha256"])
        if digest in stock_hashes:
            raise BrollContractError("audited Stock Video 使用重複素材")
        stock_hashes.add(digest)
        stock_videos.append(
            {
                "category": "stock_video",
                "kind": "video",
                "slug": _safe_slug(raw.get("slug")),
                "materialization_id": projection["materialization_id"],
                "event_id": projection["event_id"],
                "director_intent_sha256": projection["director_intent_sha256"],
                "t0": projection["t0"],
                "t1": projection["t1"],
                "asset": {
                    "path": relative.as_posix(),
                    "bytes": media["bytes"],
                    "sha256": digest,
                    "media": probe_stock_video(path),
                },
                "provenance": projection["provenance"],
            }
        )
    if cut_format == "long" and len(stock_videos) < MIN_LONG_STOCK_VIDEOS:
        raise BrollContractError(
            f"long highlight 需要至少 {MIN_LONG_STOCK_VIDEOS} 個 audited Stock Video；"
            f"目前 {len(stock_videos)} 個，缺 {MIN_LONG_STOCK_VIDEOS - len(stock_videos)} 個"
        )
    core = {
        "contract": CONTRACT,
        "cut_id": cut_id,
        "format": cut_format,
        "editorial_master_lineage": editorial_master_lineage,
        "plan_sha256": _canonical_hash(items),
        "visual_pipeline_lineage": _visual_lineage_identity(lineage),
        "stock_video_count": len(stock_videos),
        "stock_videos": stock_videos,
    }
    return {**core, "content_hash": _canonical_hash(core)}


def write_broll_receipt(
    episode_dir: Path,
    cut_id: str,
    cut_format: str,
    items: list[dict[str, Any]],
    editorial_master_lineage: dict[str, Any],
    *,
    title_items: list[dict[str, Any]] | None = None,
    editorial_master: object | None = None,
) -> dict[str, Any]:
    """Commit a receipt only after the caller materialized the verified plan."""

    receipt = build_authoritative_broll_receipt(
        episode_dir,
        cut_id,
        cut_format,
        items,
        editorial_master_lineage,
        title_items=title_items,
        editorial_master=editorial_master,
    )
    path = receipt_path(episode_dir, cut_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(path.suffix + ".tmp")
    staging.write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(staging, path)
    return receipt


def verify_broll_receipt(
    episode_dir: Path,
    cut_id: str,
    cut_format: str,
    items: list[dict[str, Any]],
    editorial_master_lineage: dict[str, Any],
    *,
    title_items: list[dict[str, Any]] | None = None,
    editorial_master: object | None = None,
) -> dict[str, Any]:
    """Freshly re-hash the plan and assets; stale materialization fails closed."""

    path = receipt_path(episode_dir, cut_id)
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BrollContractError(
            f"Stock Video materialization receipt 不存在或無法讀取：{path}"
        ) from exc
    if stored.get("contract") == LEGACY_CONTRACT:
        legacy = build_broll_receipt(
            episode_dir, cut_id, cut_format, items, editorial_master_lineage
        )
        if stored != legacy:
            raise BrollContractError("Stock Video materialization receipt 與目前 plan／素材不一致")
        raise BrollContractError(
            "Stock Video v1 receipt 只有素材／授權證據，缺少 Director／DP／Semantic Audit；"
            "不得授權新的 materialization"
        )
    expected = build_authoritative_broll_receipt(
        episode_dir,
        cut_id,
        cut_format,
        items,
        editorial_master_lineage,
        title_items=title_items,
        editorial_master=editorial_master,
    )
    if stored != expected:
        raise BrollContractError("Stock Video materialization receipt 與目前 plan／素材不一致")
    return stored
