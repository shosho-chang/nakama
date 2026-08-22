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
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

CONTRACT = "podcast-long-highlight-stock-video-v1"
MIN_LONG_STOCK_VIDEOS = 3
RECEIPT_SUFFIX = "_broll_materialization.json"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


class BrollContractError(ValueError):
    """The Stock Video plan or its materialized receipt is not trustworthy."""


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
    acquired_at = str(raw.get("acquired_at") or "").strip()
    try:
        acquired = datetime.fromisoformat(acquired_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BrollContractError(f"Stock Video {slug} provenance.acquired_at 無效") from exc
    if acquired.tzinfo is None:
        raise BrollContractError(f"Stock Video {slug} provenance.acquired_at 必須含 timezone")
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


def build_broll_receipt(
    episode_dir: Path,
    cut_id: str,
    cut_format: str,
    items: list[dict[str, Any]],
    editorial_master_lineage: dict[str, Any],
) -> dict[str, Any]:
    """Validate the current plan and return its content-addressed receipt."""

    if not isinstance(items, list):
        raise BrollContractError("B-roll plan items 必須是 array")
    stock_videos = _stock_video_rows(episode_dir, items)
    if cut_format == "long" and len(stock_videos) < MIN_LONG_STOCK_VIDEOS:
        raise BrollContractError(
            f"long highlight 需要至少 {MIN_LONG_STOCK_VIDEOS} 個 Stock Video；"
            f"目前 {len(stock_videos)} 個，缺 {MIN_LONG_STOCK_VIDEOS - len(stock_videos)} 個"
        )
    core = {
        "contract": CONTRACT,
        "cut_id": cut_id,
        "format": cut_format,
        "editorial_master_lineage": editorial_master_lineage,
        "plan_sha256": _canonical_hash(items),
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
) -> dict[str, Any]:
    """Commit a receipt only after the caller materialized the verified plan."""

    receipt = build_broll_receipt(
        episode_dir, cut_id, cut_format, items, editorial_master_lineage
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
) -> dict[str, Any]:
    """Freshly re-hash the plan and assets; stale materialization fails closed."""

    path = receipt_path(episode_dir, cut_id)
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BrollContractError(
            f"Stock Video materialization receipt 不存在或無法讀取：{path}"
        ) from exc
    expected = build_broll_receipt(
        episode_dir, cut_id, cut_format, items, editorial_master_lineage
    )
    if stored != expected:
        raise BrollContractError("Stock Video materialization receipt 與目前 plan／素材不一致")
    return stored
