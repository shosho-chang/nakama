"""Immutable, fail-closed Editorial Master contract for Podcast derivatives.

The human-approved Resolve timeline is inspected without mutation.  ``seal``
renders and serializes into a sibling staging directory, verifies every byte,
then publishes the complete ``v1`` directory with the receipt as commit marker.
Downstream callers only receive paths after ``verify_editorial_master`` succeeds.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Callable, Mapping

CONTRACT = "podcast-editorial-master-v1"
SNAPSHOT_CONTRACT = "podcast-editorial-master-timeline-snapshot-v1"
VERSION_RELATIVE = Path("editorial-master") / "v1"
RECEIPT_NAME = "EDITORIAL-MASTER.json"
CANONICAL_ARTIFACTS = {
    "media": "master.mp4",
    "subtitles": "master.srt",
    "timeline_snapshot": "timeline-snapshot.json",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SRT_TIMING_RE = re.compile(
    r"(?m)^(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> "
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})$"
)


class EditorialMasterContractError(ValueError):
    """The Editorial Master is absent, ambiguous, stale, or tampered."""


class EditorialMasterArtifactConflictError(EditorialMasterContractError):
    """An immutable destination already contains different or partial bytes."""


class EditorialMasterTimelineDriftError(EditorialMasterContractError):
    """The live Resolve timeline no longer matches the approved snapshot."""


@dataclass(frozen=True, slots=True)
class TimelineInspection:
    project: Any
    timeline: Any
    snapshot: dict[str, object]
    srt_text: str
    timing_qc: dict[str, int]


@dataclass(frozen=True, slots=True)
class EditorialMasterSelection:
    video_path: Path
    srt_path: Path
    snapshot_path: Path
    receipt_path: Path
    receipt: dict[str, object]

    @property
    def media_path(self) -> Path:
        return self.video_path

    @property
    def content_hash(self) -> str:
        return str(self.receipt["content_hash"])

    def identity(self) -> dict[str, object]:
        artifacts = _require_mapping(self.receipt.get("artifacts"), "artifacts")
        media = _require_mapping(artifacts.get("media"), "artifacts.media")
        subtitles = _require_mapping(artifacts.get("subtitles"), "artifacts.subtitles")
        episode_root = self.receipt_path.parents[2]
        return {
            "contract": CONTRACT,
            "episode_id": self.receipt["episode_id"],
            "content_hash": self.content_hash,
            "master_media_sha256": media["sha256"],
            "master_srt_sha256": subtitles["sha256"],
            "editorial_master_receipt": self.receipt_path.relative_to(episode_root).as_posix(),
        }


@dataclass(frozen=True, slots=True)
class EditorialMasterRequest:
    episode_root: str | Path
    project_name: str | None = None
    timeline_name: str | None = None
    expected_timeline_uid: str | None = None
    expected_episode_id: str | None = None
    expected_content_hash: str | None = None

    def open(
        self,
        *,
        live_snapshot: Mapping[str, object] | None = None,
    ) -> EditorialMasterSelection:
        return verify_editorial_master(
            self.episode_root,
            expected_episode_id=self.expected_episode_id,
            expected_content_hash=self.expected_content_hash,
            expected_timeline_uid=self.expected_timeline_uid,
            live_snapshot=live_snapshot,
        )


Renderer = Callable[[Any, Any, Path], Path]
MediaProbe = Callable[[Path], float]


def _canonical_json(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _pretty_json(payload: Mapping[str, object]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise EditorialMasterContractError(f"{label} must be an object")
    return value


def _safe_call(obj: Any, method: str, *args: object) -> object | None:
    function = getattr(obj, method, None)
    if not callable(function):
        return None
    try:
        return function(*args)
    except Exception:
        return None


def _frame_rate(timeline: Any) -> Decimal:
    for key in ("timelineFrameRate", "timelinePlaybackFrameRate"):
        raw = _safe_call(timeline, "GetSetting", key)
        if raw not in (None, ""):
            try:
                value = Decimal(str(raw))
            except Exception:
                continue
            if value > 0:
                return value
    raise EditorialMasterContractError("Resolve timeline has no valid frame rate")


def _timeline_uid(timeline: Any) -> str:
    uid = _safe_call(timeline, "GetUniqueId") or _safe_call(timeline, "GetUniqueID")
    if not isinstance(uid, str) or not uid.strip():
        raise EditorialMasterContractError("Resolve timeline has no stable unique ID")
    return uid.strip()


def _find_project_and_timeline(request: EditorialMasterRequest, resolve: Any) -> tuple[Any, Any]:
    root = Path(request.episode_root).resolve()
    project_name = request.project_name or root.name
    timeline_name = request.timeline_name or project_name
    pm = _safe_call(resolve, "GetProjectManager")
    if pm is None:
        raise EditorialMasterContractError("Resolve project manager is unavailable")
    project = _safe_call(pm, "GetCurrentProject")
    if project is None or _safe_call(project, "GetName") != project_name:
        project = _safe_call(pm, "LoadProject", project_name)
    if project is None or _safe_call(project, "GetName") != project_name:
        raise EditorialMasterContractError(f"Resolve project not found: {project_name}")
    count = int(_safe_call(project, "GetTimelineCount") or 0)
    timeline = next(
        (
            candidate
            for index in range(1, count + 1)
            if (candidate := _safe_call(project, "GetTimelineByIndex", index)) is not None
            and _safe_call(candidate, "GetName") == timeline_name
        ),
        None,
    )
    if timeline is None:
        raise EditorialMasterContractError(f"Resolve timeline not found: {timeline_name}")
    uid = _timeline_uid(timeline)
    if request.expected_timeline_uid and uid != request.expected_timeline_uid:
        raise EditorialMasterTimelineDriftError(
            f"timeline UID mismatch: expected {request.expected_timeline_uid}, got {uid}"
        )
    return project, timeline


def _item_snapshot(item: Any, index: int) -> dict[str, object]:
    start = int(_safe_call(item, "GetStart") or 0)
    end = int(_safe_call(item, "GetEnd") or 0)
    result: dict[str, object] = {
        "index": index,
        "name": str(_safe_call(item, "GetName") or ""),
        "start_frame": start,
        "end_frame": end,
        "duration_frames": end - start,
    }
    uid = _safe_call(item, "GetUniqueId") or _safe_call(item, "GetUniqueID")
    if isinstance(uid, str) and uid:
        result["uid"] = uid
    media_pool_item = _safe_call(item, "GetMediaPoolItem")
    if media_pool_item is not None:
        media_id = _safe_call(media_pool_item, "GetMediaId")
        if isinstance(media_id, str) and media_id:
            result["media_id"] = media_id
        source_path = _safe_call(media_pool_item, "GetClipProperty", "File Path")
        if isinstance(source_path, str) and source_path:
            result["source_path"] = source_path
    return result


def _build_snapshot(project: Any, timeline: Any, episode_id: str) -> dict[str, object]:
    fps = _frame_rate(timeline)
    start = int(_safe_call(timeline, "GetStartFrame") or 0)
    end = int(_safe_call(timeline, "GetEndFrame") or 0)
    if end <= start:
        raise EditorialMasterContractError("Resolve timeline has non-positive duration")
    tracks: list[dict[str, object]] = []
    subtitle_count = 0
    for track_type in ("video", "audio", "subtitle"):
        count = int(_safe_call(timeline, "GetTrackCount", track_type) or 0)
        for track_index in range(1, count + 1):
            items = list(_safe_call(timeline, "GetItemListInTrack", track_type, track_index) or [])
            serialized = [_item_snapshot(item, index) for index, item in enumerate(items, 1)]
            if track_type == "subtitle":
                subtitle_count += len(serialized)
            tracks.append(
                {
                    "type": track_type,
                    "index": track_index,
                    "enabled": bool(
                        _safe_call(timeline, "GetIsTrackEnabled", track_type, track_index)
                    ),
                    "items": serialized,
                }
            )
    core: dict[str, object] = {
        "contract": SNAPSHOT_CONTRACT,
        "episode_id": episode_id,
        "project": {"name": str(_safe_call(project, "GetName") or "")},
        "timeline": {
            "name": str(_safe_call(timeline, "GetName") or ""),
            "uid": _timeline_uid(timeline),
            "fps": str(fps.normalize()),
            "start_frame": start,
            "end_frame": end,
            "duration_frames": end - start,
            "duration_sec": float(Decimal(end - start) / fps),
        },
        "subtitle_cue_count": subtitle_count,
        "tracks": tracks,
    }
    core["snapshot_sha256"] = _sha256_bytes(_canonical_json(core))
    return core


def _srt_timestamp(relative_frame: int, fps: Decimal) -> str:
    milliseconds = int(
        (Decimal(relative_frame) * Decimal(1000) / fps).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def _serialize_subtitles(
    timeline: Any, snapshot: Mapping[str, object]
) -> tuple[str, dict[str, int]]:
    timeline_meta = _require_mapping(snapshot.get("timeline"), "snapshot.timeline")
    fps = Decimal(str(timeline_meta["fps"]))
    timeline_start = int(timeline_meta["start_frame"])
    cues: list[tuple[int, int, int, int, str]] = []
    track_count = int(_safe_call(timeline, "GetTrackCount", "subtitle") or 0)
    for track_index in range(1, track_count + 1):
        items = list(_safe_call(timeline, "GetItemListInTrack", "subtitle", track_index) or [])
        for item_index, item in enumerate(items, 1):
            start = int(_safe_call(item, "GetStart") or 0) - timeline_start
            end = int(_safe_call(item, "GetEnd") or 0) - timeline_start
            text = str(_safe_call(item, "GetName") or "").strip()
            if end <= start:
                raise EditorialMasterContractError(
                    f"non-positive subtitle duration at track {track_index} item {item_index}"
                )
            duration_frames = int(timeline_meta["duration_frames"])
            if start < 0 or end > duration_frames:
                raise EditorialMasterContractError(
                    f"subtitle outside timeline at track {track_index} item {item_index}"
                )
            if not text:
                raise EditorialMasterContractError(
                    f"empty subtitle at track {track_index} item {item_index}"
                )
            cues.append((start, end, track_index, item_index, text))
    cues.sort(key=lambda row: (row[0], row[1], row[2], row[3], row[4]))
    if not cues:
        raise EditorialMasterContractError("approved timeline has no subtitle cues")
    for previous, current in zip(cues, cues[1:]):
        if current[0] < previous[1]:
            raise EditorialMasterContractError(
                "subtitle overlap between "
                f"track {previous[2]} item {previous[3]} and "
                f"track {current[2]} item {current[3]}"
            )
    blocks = []
    for number, (start, end, _track, _item, text) in enumerate(cues, 1):
        blocks.append(
            f"{number}\n{_srt_timestamp(start, fps)} --> {_srt_timestamp(end, fps)}\n{text}"
        )
    return "\n\n".join(blocks) + "\n", {
        "non_positive_duration_count": 0,
        "out_of_timeline_count": 0,
        "overlap_count": 0,
    }


def inspect_timeline(request: EditorialMasterRequest, resolve: Any) -> TimelineInspection:
    """Read the exact Resolve timeline state without writing or changing it."""

    root = Path(request.episode_root).resolve()
    if not root.is_dir():
        raise EditorialMasterContractError(f"episode root does not exist: {root}")
    project, timeline = _find_project_and_timeline(request, resolve)
    snapshot = _build_snapshot(project, timeline, root.name)
    srt_text, timing_qc = _serialize_subtitles(timeline, snapshot)
    return TimelineInspection(
        project=project,
        timeline=timeline,
        snapshot=snapshot,
        srt_text=srt_text,
        timing_qc=timing_qc,
    )


def _artifact_record(version_dir: Path, path: Path, **extra: object) -> dict[str, object]:
    record: dict[str, object] = {
        "path": (VERSION_RELATIVE / path.name).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
    }
    record.update(extra)
    return record


def _probe_media(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    try:
        payload = json.loads(result.stdout)
        duration = float(payload["format"]["duration"])
        stream_types = {
            stream.get("codec_type")
            for stream in payload.get("streams", [])
            if isinstance(stream, dict)
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise EditorialMasterContractError(
            f"ffprobe could not read master media: {path}"
        ) from error
    if result.returncode != 0 or duration <= 0:
        raise EditorialMasterContractError(f"ffprobe rejected master media: {path}")
    if not {"video", "audio"}.issubset(stream_types):
        raise EditorialMasterContractError("master.mp4 must contain both video and audio streams")
    return duration


def _default_renderer(project: Any, timeline: Any, target: Path) -> Path:
    from scripts.publish_prep import _render_master

    previous = _safe_call(project, "GetCurrentTimeline")
    set_current = getattr(project, "SetCurrentTimeline", None)
    if not callable(set_current) or not set_current(timeline):
        raise EditorialMasterContractError(
            "Resolve could not select the approved Timeline for render"
        )
    try:
        rendered = _render_master(project, timeline, target.parent, target.stem)
        return Path(rendered)
    finally:
        if previous is not None and previous is not timeline:
            set_current(previous)


def _approval_time(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise EditorialMasterContractError("approved_at must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise EditorialMasterContractError("approved_at must include timezone")
    return parsed.isoformat()


def _validate_stage5_identity(
    identity: Mapping[str, object],
    episode_id: str,
    *,
    legacy_episode_alias: str | None = None,
) -> dict[str, object]:
    """Stage 5 的字幕必須屬於這一集——除非操作者明確宣告一個 legacy 別名。

    這道檢查是為了擋「把 A 集的字幕封進 B 集」。但 ADR-063 換軌前產出的
    handoff 有自己的 id 慣例（抹布寫的是 `20260814-moboo`，資料夾是
    `20260814 抹布`），而同一份 ADR 又明文禁止改寫那些產物。

    所以放行的方式是**要求操作者明講**，而不是放寬比對：別名必須逐字給對，
    而且會寫進不可變收據的 `stage5_subtitle_identity.legacy_episode_alias`，
    這個例外從此永遠留在證據鏈上，不是靜默通過。
    """
    if not identity:
        raise EditorialMasterContractError("Stage 5 subtitle identity is required")
    stage5 = dict(identity)
    declared = stage5.get("episode_id")
    if declared != episode_id:
        if not legacy_episode_alias or declared != legacy_episode_alias:
            raise EditorialMasterContractError("Stage 5 identity belongs to another episode")
        stage5["legacy_episode_alias"] = legacy_episode_alias
    return stage5


def seal_editorial_master(
    request: EditorialMasterRequest,
    resolve: Any,
    *,
    renderer: Renderer | None = None,
    media_probe: MediaProbe | None = None,
    stage5_identity: Mapping[str, object],
    human_approved: bool,
    approved_by: str,
    approved_at: str | None = None,
    legacy_episode_alias: str | None = None,
) -> EditorialMasterSelection:
    """Seal one approved Timeline transactionally; never edits the Timeline."""

    if not human_approved:
        raise EditorialMasterContractError("explicit human approval is required before seal")
    if not approved_by.strip():
        raise EditorialMasterContractError("approved_by is required")
    root = Path(request.episode_root).resolve()
    episode_id = root.name
    if request.expected_episode_id and request.expected_episode_id != episode_id:
        raise EditorialMasterContractError("episode root does not match expected episode ID")
    stage5 = _validate_stage5_identity(
        stage5_identity, episode_id, legacy_episode_alias=legacy_episode_alias
    )
    version_dir = root / VERSION_RELATIVE
    if version_dir.exists():
        existing = verify_editorial_master(
            root,
            expected_episode_id=request.expected_episode_id,
            expected_content_hash=request.expected_content_hash,
            expected_timeline_uid=request.expected_timeline_uid,
        )
        if existing.receipt.get("stage5_subtitle_identity") != stage5:
            raise EditorialMasterArtifactConflictError(
                "immutable Editorial Master Stage 5 identity differs from this seal request"
            )
        inspected = inspect_timeline(request, resolve)
        stored_hash = _require_mapping(existing.receipt.get("timeline"), "timeline").get(
            "snapshot_sha256"
        )
        if inspected.snapshot.get("snapshot_sha256") != stored_hash:
            raise EditorialMasterTimelineDriftError(
                "live Resolve timeline differs from sealed Editorial Master"
            )
        return existing

    inspected = inspect_timeline(request, resolve)
    timeline_meta = _require_mapping(inspected.snapshot.get("timeline"), "snapshot.timeline")
    stage_parent = root / "editorial-master"
    stage_parent.mkdir(parents=True, exist_ok=True)
    stage = stage_parent / f".v1.staging-{uuid.uuid4().hex}"
    stage.mkdir()
    try:
        target_media = stage / CANONICAL_ARTIFACTS["media"]
        rendered = (renderer or _default_renderer)(
            inspected.project, inspected.timeline, target_media
        )
        rendered = Path(rendered).resolve()
        if rendered != target_media.resolve():
            raise EditorialMasterContractError("renderer must write exactly the staged master.mp4")
        if not target_media.is_file() or target_media.stat().st_size <= 0:
            raise EditorialMasterContractError("renderer did not produce non-empty master.mp4")
        duration = (media_probe or _probe_media)(target_media)
        expected_duration = float(timeline_meta["duration_sec"])
        tolerance = max(0.1, 2.0 / float(Decimal(str(timeline_meta["fps"]))))
        if duration <= 0 or abs(duration - expected_duration) > tolerance:
            raise EditorialMasterContractError(
                f"render duration mismatch: expected {expected_duration:.3f}s, got {duration:.3f}s"
            )

        srt_path = stage / CANONICAL_ARTIFACTS["subtitles"]
        srt_path.write_text(inspected.srt_text, encoding="utf-8", newline="\n")
        snapshot_path = stage / CANONICAL_ARTIFACTS["timeline_snapshot"]
        snapshot_path.write_bytes(_pretty_json(inspected.snapshot))
        after = _build_snapshot(inspected.project, inspected.timeline, episode_id)
        if after.get("snapshot_sha256") != inspected.snapshot.get("snapshot_sha256"):
            raise EditorialMasterTimelineDriftError(
                "timeline changed while Editorial Master was sealing"
            )

        artifacts = {
            "media": _artifact_record(
                stage,
                target_media,
                duration_sec=duration,
            ),
            "subtitles": _artifact_record(
                stage,
                srt_path,
                cue_count=int(inspected.snapshot["subtitle_cue_count"]),
                timing_qc=inspected.timing_qc,
            ),
            "timeline_snapshot": _artifact_record(stage, snapshot_path),
        }
        receipt: dict[str, object] = {
            "contract": CONTRACT,
            "episode_id": episode_id,
            "project": inspected.snapshot["project"],
            "timeline": {
                **timeline_meta,
                "snapshot_sha256": inspected.snapshot["snapshot_sha256"],
            },
            "stage5_subtitle_identity": stage5,
            "artifacts": artifacts,
            "approval": {
                "human_approved": True,
                "approved_by": approved_by.strip(),
                "approved_at": _approval_time(approved_at),
            },
        }
        receipt["content_hash"] = _sha256_bytes(_canonical_json(receipt))
        (stage / RECEIPT_NAME).write_bytes(_pretty_json(receipt))
        verify_editorial_master(stage, _version_dir_is_root=True)
        try:
            os.replace(stage, version_dir)
        except FileExistsError as error:
            raise EditorialMasterArtifactConflictError(
                f"immutable Editorial Master destination appeared during commit: {version_dir}"
            ) from error
        return verify_editorial_master(
            root,
            expected_episode_id=request.expected_episode_id,
            expected_content_hash=request.expected_content_hash,
            expected_timeline_uid=request.expected_timeline_uid,
        )
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def _resolve_artifact_path(
    episode_root: Path,
    version_dir: Path,
    record: Mapping[str, object],
    expected_name: str,
) -> Path:
    raw = record.get("path")
    if not isinstance(raw, str):
        raise EditorialMasterContractError("artifact path must be a relative string")
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        raise EditorialMasterContractError(f"artifact path escapes episode root: {raw}")
    canonical = VERSION_RELATIVE / expected_name
    if relative != canonical:
        raise EditorialMasterContractError(f"non-canonical Editorial Master artifact path: {raw}")
    resolved = (version_dir / expected_name).resolve()
    if not resolved.is_relative_to(version_dir.resolve()):
        raise EditorialMasterContractError(f"artifact path escapes Editorial Master: {raw}")
    return resolved


def _parse_srt_cues(path: Path) -> list[dict[str, object]]:
    try:
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    except (OSError, UnicodeDecodeError) as error:
        raise EditorialMasterContractError("master.srt is unreadable") from error
    lines = text.splitlines()
    cues: list[dict[str, object]] = []
    cursor = 0
    while cursor < len(lines):
        while cursor < len(lines) and not lines[cursor]:
            cursor += 1
        if cursor >= len(lines):
            break
        try:
            index = int(lines[cursor])
        except ValueError as error:
            raise EditorialMasterContractError("master.srt has an invalid cue index") from error
        cursor += 1
        if cursor >= len(lines):
            raise EditorialMasterContractError("master.srt cue has no timing line")
        timing_line = lines[cursor]
        match = _SRT_TIMING_RE.fullmatch(timing_line)
        if match is None:
            raise EditorialMasterContractError("master.srt has an invalid cue timing line")
        values = [int(value) for value in match.groups()]
        start = (((values[0] * 60 + values[1]) * 60 + values[2]) * 1000) + values[3]
        end = (((values[4] * 60 + values[5]) * 60 + values[6]) * 1000) + values[7]
        cursor += 1
        text_lines: list[str] = []
        while cursor < len(lines) and lines[cursor]:
            text_lines.append(lines[cursor])
            cursor += 1
        if not text_lines:
            raise EditorialMasterContractError("master.srt cue has no text")
        cues.append(
            {
                "index": index,
                "start_ms": start,
                "end_ms": end,
                "text": "\n".join(text_lines),
            }
        )
    if not cues:
        raise EditorialMasterContractError("master.srt has no cues")
    return cues


def _srt_timing_qc(cues: list[dict[str, object]], timeline_duration_sec: float) -> dict[str, int]:
    intervals = [(int(cue["start_ms"]), int(cue["end_ms"])) for cue in cues]
    intervals.sort()
    duration_ms = round(timeline_duration_sec * 1000)
    return {
        "non_positive_duration_count": sum(end <= start for start, end in intervals),
        "out_of_timeline_count": sum(
            start < 0 or end > duration_ms + 1 for start, end in intervals
        ),
        "overlap_count": sum(
            current[0] < previous[1] for previous, current in zip(intervals, intervals[1:])
        ),
    }


def _snapshot_subtitle_cues(snapshot: Mapping[str, object]) -> list[dict[str, object]]:
    timeline = _require_mapping(snapshot.get("timeline"), "snapshot.timeline")
    fps = Decimal(str(timeline["fps"]))
    timeline_start = int(timeline["start_frame"])
    rows: list[tuple[int, int, int, int, str]] = []
    tracks = snapshot.get("tracks")
    if not isinstance(tracks, list):
        raise EditorialMasterTimelineDriftError("timeline snapshot tracks are invalid")
    for track in tracks:
        if not isinstance(track, dict) or track.get("type") != "subtitle":
            continue
        track_index = int(track.get("index", 0))
        items = track.get("items")
        if not isinstance(items, list):
            raise EditorialMasterTimelineDriftError("timeline snapshot subtitle items are invalid")
        for fallback_index, item in enumerate(items, 1):
            if not isinstance(item, dict):
                raise EditorialMasterTimelineDriftError(
                    "timeline snapshot subtitle item is invalid"
                )
            rows.append(
                (
                    int(item["start_frame"]) - timeline_start,
                    int(item["end_frame"]) - timeline_start,
                    track_index,
                    int(item.get("index", fallback_index)),
                    str(item.get("name", "")).strip(),
                )
            )
    rows.sort(key=lambda row: (row[0], row[1], row[2], row[3], row[4]))
    return [
        {
            "index": index,
            "start_ms": int(
                (Decimal(start) * Decimal(1000) / fps).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            ),
            "end_ms": int(
                (Decimal(end) * Decimal(1000) / fps).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            ),
            "text": text,
        }
        for index, (start, end, _track, _item, text) in enumerate(rows, 1)
    ]


def _snapshot_subtitle_count(snapshot: Mapping[str, object]) -> int:
    tracks = snapshot.get("tracks")
    if not isinstance(tracks, list):
        raise EditorialMasterTimelineDriftError("timeline snapshot tracks are invalid")
    count = 0
    for track in tracks:
        if not isinstance(track, dict):
            raise EditorialMasterTimelineDriftError("timeline snapshot track is invalid")
        if track.get("type") != "subtitle":
            continue
        items = track.get("items")
        if not isinstance(items, list):
            raise EditorialMasterTimelineDriftError("timeline snapshot subtitle items are invalid")
        count += len(items)
    return count


def verify_editorial_master(
    episode_root: str | Path,
    *,
    expected_episode_id: str | None = None,
    expected_content_hash: str | None = None,
    expected_timeline_uid: str | None = None,
    live_snapshot: Mapping[str, object] | None = None,
    _version_dir_is_root: bool = False,
) -> EditorialMasterSelection:
    """Open only a complete, hash-bound Editorial Master release."""

    supplied_root = Path(episode_root).resolve()
    if _version_dir_is_root:
        version_dir = supplied_root
        episode = version_dir.parents[1]
    else:
        episode = supplied_root
        version_dir = episode / VERSION_RELATIVE
    if not version_dir.is_dir():
        raise EditorialMasterContractError(f"Editorial Master is missing: {version_dir}")
    receipt_path = version_dir / RECEIPT_NAME
    if not receipt_path.is_file():
        raise EditorialMasterArtifactConflictError(
            f"partial Editorial Master destination has no commit marker: {version_dir}"
        )
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EditorialMasterContractError("Editorial Master receipt is unreadable") from error
    receipt = _require_mapping(receipt, "receipt")
    if receipt.get("contract") != CONTRACT:
        raise EditorialMasterContractError("Editorial Master contract mismatch")
    episode_id = receipt.get("episode_id")
    if episode_id != episode.name:
        raise EditorialMasterContractError("Editorial Master belongs to another episode")
    if expected_episode_id and episode_id != expected_episode_id:
        raise EditorialMasterContractError("Editorial Master episode ID mismatch")
    approval = _require_mapping(receipt.get("approval"), "approval")
    if approval.get("human_approved") is not True or not approval.get("approved_by"):
        raise EditorialMasterContractError("Editorial Master has no explicit human approval")
    timeline = _require_mapping(receipt.get("timeline"), "timeline")
    if expected_timeline_uid and timeline.get("uid") != expected_timeline_uid:
        raise EditorialMasterTimelineDriftError("Editorial Master timeline UID mismatch")
    stored_hash = receipt.get("content_hash")
    if not isinstance(stored_hash, str) or not _SHA256_RE.fullmatch(stored_hash):
        raise EditorialMasterContractError("Editorial Master content hash is invalid")
    unsigned = dict(receipt)
    unsigned.pop("content_hash", None)
    if _sha256_bytes(_canonical_json(unsigned)) != stored_hash:
        raise EditorialMasterContractError("Editorial Master receipt content hash mismatch")
    if expected_content_hash and stored_hash != expected_content_hash:
        raise EditorialMasterContractError("Editorial Master content identity mismatch")

    artifacts = _require_mapping(receipt.get("artifacts"), "artifacts")
    paths: dict[str, Path] = {}
    for role, expected_name in CANONICAL_ARTIFACTS.items():
        record = _require_mapping(artifacts.get(role), f"artifacts.{role}")
        path = _resolve_artifact_path(episode, version_dir, record, expected_name)
        if not path.is_file():
            raise EditorialMasterArtifactConflictError(
                f"Editorial Master artifact is missing: {path}"
            )
        if path.stat().st_size != record.get("bytes"):
            raise EditorialMasterContractError(f"Editorial Master artifact size changed: {path}")
        if _sha256_file(path) != record.get("sha256"):
            raise EditorialMasterContractError(f"Editorial Master artifact hash changed: {path}")
        paths[role] = path
    try:
        snapshot = json.loads(paths["timeline_snapshot"].read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EditorialMasterContractError("timeline snapshot is unreadable") from error
    snapshot = _require_mapping(snapshot, "timeline snapshot")
    if snapshot.get("snapshot_sha256") != timeline.get("snapshot_sha256"):
        raise EditorialMasterTimelineDriftError("receipt and timeline snapshot identities differ")
    unsigned_snapshot = dict(snapshot)
    snapshot_hash = unsigned_snapshot.pop("snapshot_sha256", None)
    if _sha256_bytes(_canonical_json(unsigned_snapshot)) != snapshot_hash:
        raise EditorialMasterTimelineDriftError("timeline snapshot content hash mismatch")
    if snapshot.get("contract") != SNAPSHOT_CONTRACT:
        raise EditorialMasterTimelineDriftError("timeline snapshot contract mismatch")
    if snapshot.get("episode_id") != episode_id:
        raise EditorialMasterTimelineDriftError("timeline snapshot episode mismatch")
    snapshot_project = _require_mapping(snapshot.get("project"), "snapshot.project")
    receipt_project = _require_mapping(receipt.get("project"), "project")
    if snapshot_project != receipt_project:
        raise EditorialMasterTimelineDriftError("receipt project differs from timeline snapshot")
    snapshot_timeline = _require_mapping(snapshot.get("timeline"), "snapshot.timeline")
    receipt_timeline = dict(timeline)
    receipt_timeline.pop("snapshot_sha256", None)
    if snapshot_timeline != receipt_timeline:
        raise EditorialMasterTimelineDriftError("receipt timeline differs from timeline snapshot")
    if live_snapshot is not None and live_snapshot.get("snapshot_sha256") != timeline.get(
        "snapshot_sha256"
    ):
        raise EditorialMasterTimelineDriftError(
            "live Resolve timeline differs from Editorial Master"
        )
    stage5 = _require_mapping(receipt.get("stage5_subtitle_identity"), "stage5_subtitle_identity")
    if stage5.get("episode_id") != episode_id:
        raise EditorialMasterContractError("Stage 5 lineage belongs to another episode")
    subtitle_record = _require_mapping(artifacts.get("subtitles"), "artifacts.subtitles")
    timing_qc = _require_mapping(subtitle_record.get("timing_qc"), "subtitle timing_qc")
    expected_qc = {
        "non_positive_duration_count": 0,
        "out_of_timeline_count": 0,
        "overlap_count": 0,
    }
    snapshot_cue_count = snapshot.get("subtitle_cue_count")
    if not isinstance(snapshot_cue_count, int) or snapshot_cue_count != _snapshot_subtitle_count(
        snapshot
    ):
        raise EditorialMasterTimelineDriftError("timeline snapshot subtitle cue count mismatch")
    if subtitle_record.get("cue_count") != snapshot_cue_count:
        raise EditorialMasterContractError(
            "receipt subtitle cue count differs from timeline snapshot"
        )
    actual_cues = _parse_srt_cues(paths["subtitles"])
    if len(actual_cues) != snapshot_cue_count:
        raise EditorialMasterContractError("master.srt cue count differs from timeline snapshot")
    actual_qc = _srt_timing_qc(actual_cues, float(snapshot_timeline["duration_sec"]))
    if timing_qc != expected_qc or actual_qc != timing_qc:
        raise EditorialMasterContractError("Editorial Master subtitle timing QC is not clean")
    expected_cues = _snapshot_subtitle_cues(snapshot)
    if actual_cues != expected_cues:
        raise EditorialMasterContractError("master.srt cue content differs from timeline snapshot")
    return EditorialMasterSelection(
        video_path=paths["media"],
        srt_path=paths["subtitles"],
        snapshot_path=paths["timeline_snapshot"],
        receipt_path=receipt_path,
        receipt=receipt,
    )


def editorial_master_status(episode_root: str | Path) -> dict[str, object]:
    root = Path(episode_root).resolve()
    if not (root / VERSION_RELATIVE).exists():
        return {"status": "missing", "episode_id": root.name}
    try:
        selected = verify_editorial_master(root)
    except EditorialMasterContractError as error:
        return {"status": "invalid", "episode_id": root.name, "error": str(error)}
    return {"status": "ready", "episode_id": root.name, "identity": selected.identity()}


__all__ = [
    "CONTRACT",
    "EditorialMasterArtifactConflictError",
    "EditorialMasterContractError",
    "EditorialMasterRequest",
    "EditorialMasterSelection",
    "EditorialMasterTimelineDriftError",
    "TimelineInspection",
    "editorial_master_status",
    "inspect_timeline",
    "seal_editorial_master",
    "verify_editorial_master",
]
