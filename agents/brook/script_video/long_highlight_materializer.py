"""Recipe projection and explicit Resolve transaction for mutable long-highlight state."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, UnidentifiedImageError


class LongHighlightMaterializationError(ValueError):
    """The mutable state cannot safely produce renderer recipes."""


class ResolveScriptingAdapter:
    """Host-exchange adapter for explicit, UID-bound Resolve materialization."""

    def __init__(self, episode_root: Path, *, resolve: Any | None = None) -> None:
        self.episode_root = Path(episode_root)
        if resolve is None:
            from scripts.build_resolve_project import connect_resolve

            resolve = connect_resolve()
        self.resolve = resolve
        self.project_manager = resolve.GetProjectManager()
        project = self.project_manager.GetCurrentProject()
        if project is None or project.GetName() != self.episode_root.name:
            project = self.project_manager.LoadProject(self.episode_root.name)
        if project is None:
            raise LongHighlightMaterializationError(
                f"Resolve project not found: {self.episode_root.name}"
            )
        self.project = project

    def snapshot_baseline(self, timeline_name: str, timeline_uid: str) -> dict[str, Any]:
        timeline = self._exact_timeline(timeline_name, timeline_uid)
        return _timeline_structure(timeline, baseline_only=True)

    def duplicate_swap(
        self,
        canonical_name: str,
        canonical_uid: str,
        work_name: str,
        backup_name: str,
    ) -> dict[str, dict[str, str]]:
        original = self._exact_timeline(canonical_name, canonical_uid)
        occupied = {timeline.GetName() for timeline in self._timelines()}
        if work_name in occupied or backup_name in occupied:
            raise LongHighlightMaterializationError(
                "stale work/backup Timeline name already exists"
            )
        duplicate = original.DuplicateTimeline(work_name)
        if duplicate is None:
            # Resolve has not changed the canonical in this branch.
            raise LongHighlightMaterializationError("Resolve DuplicateTimeline failed")
        try:
            work_uid = _timeline_uid(duplicate)
            if not work_uid or work_uid == canonical_uid:
                raise LongHighlightMaterializationError(
                    "Resolve duplicate did not receive a distinct Timeline UID"
                )
            if original.SetName(backup_name) is False or original.GetName() != backup_name:
                raise LongHighlightMaterializationError(
                    "cannot reserve canonical Timeline as backup"
                )
            if duplicate.SetName(canonical_name) is False or duplicate.GetName() != canonical_name:
                raise LongHighlightMaterializationError("cannot activate duplicate work Timeline")
            if not self.project_manager.SaveProject():
                raise LongHighlightMaterializationError(
                    "Resolve SaveProject failed while opening transaction"
                )
        except BaseException:
            pool = self.project.GetMediaPool()
            if duplicate in self._timelines():
                pool.DeleteTimelines([duplicate])
            if original.GetName() != canonical_name:
                original.SetName(canonical_name)
            self.project_manager.SaveProject()
            raise
        return {
            "canonical": {"name": canonical_name, "uid": canonical_uid},
            "work": {"name": canonical_name, "uid": work_uid},
            "backup": {"name": backup_name, "uid": canonical_uid},
        }

    def apply_recipes(
        self,
        timeline_name: str,
        timeline_uid: str,
        broll_path: Path,
        titles_path: Path,
    ) -> dict[str, int]:
        # These are narrow opt-in seams.  Ordinary renderer CLI calls still use
        # their established validation path because they pass none of these kwargs.
        from scripts import run_short_broll, run_short_titles

        try:
            broll = run_short_broll.apply(
                self.episode_root,
                _recipe_cut_id(broll_path, "_broll.json"),
                orchestrator_timeline_name=timeline_name,
                orchestrator_timeline_uid=timeline_uid,
                recipe_path=Path(broll_path),
            )
            titles = run_short_titles.apply(
                self.episode_root,
                _recipe_cut_id(titles_path, "_titles.json"),
                orchestrator_timeline_name=timeline_name,
                orchestrator_timeline_uid=timeline_uid,
                recipe_path=Path(titles_path),
                broll_recipe_path=Path(broll_path),
            )
        except SystemExit as exc:
            raise LongHighlightMaterializationError(str(exc)) from exc
        if not self.project_manager.SaveProject():
            raise LongHighlightMaterializationError(
                "Resolve SaveProject failed after applying orchestrator recipes"
            )
        return {"broll": len(broll.get("items", [])), "titles": len(titles.get("cards", []))}

    def render_preview(self, timeline_name: str, timeline_uid: str, output: Path) -> Path:
        timeline = self._exact_timeline(timeline_name, timeline_uid)
        if self.project.SetCurrentTimeline(timeline) is False:
            raise LongHighlightMaterializationError(
                "Resolve refused to select work Timeline for preview"
            )
        current_method = getattr(self.project, "GetCurrentTimeline", None)
        current = current_method() if callable(current_method) else timeline
        if _timeline_uid(current) != timeline_uid:
            raise LongHighlightMaterializationError(
                "Resolve current Timeline differs from preview target UID"
            )
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        before_mtimes = {
            path: path.stat().st_mtime for path in output.parent.glob(f"{output.stem}*.mp4")
        }
        if self.project.SetCurrentRenderFormatAndCodec("mp4", "H264") is False:
            raise LongHighlightMaterializationError("Resolve cannot select H.264 MP4 render")
        settings = {
            "MarkIn": timeline.GetStartFrame(),
            "MarkOut": timeline.GetEndFrame(),
            "TargetDir": str(output.parent),
            "CustomName": output.stem,
            "ExportAudio": True,
            "AudioCodec": "aac",
        }
        if self.project.SetRenderSettings(settings) is False:
            raise LongHighlightMaterializationError("Resolve rejected preview render settings")
        job_id = self.project.AddRenderJob()
        if not job_id:
            raise LongHighlightMaterializationError("Resolve AddRenderJob failed")
        status: Any = {}
        try:
            self.project.StartRendering([job_id], isInteractiveMode=False)
            for _ in range(600):
                if not self.project.IsRenderingInProgress():
                    break
                time.sleep(1)
            else:
                stop = getattr(self.project, "StopRendering", None)
                if callable(stop):
                    stop()
                raise LongHighlightMaterializationError("Resolve preview render timed out")
            status_method = getattr(self.project, "GetRenderJobStatus", None)
            status = status_method(job_id) if callable(status_method) else {}
        finally:
            self.project.DeleteRenderJob(job_id)
        if isinstance(status, Mapping) and status.get("JobStatus") not in {None, "Complete"}:
            raise LongHighlightMaterializationError(
                f"Resolve preview render failed: {status.get('Error') or status.get('JobStatus')}"
            )
        candidates = sorted(
            (
                path
                for path in output.parent.glob(f"{output.stem}*.mp4")
                if path.stat().st_mtime > before_mtimes.get(path, 0.0)
            ),
            key=lambda path: path.stat().st_mtime,
        )
        if not candidates:
            raise LongHighlightMaterializationError(
                "Resolve preview file was not created or updated"
            )
        rendered = candidates[-1]
        if rendered != output:
            # Preserve the actual Resolve filename in transaction metadata.
            output = rendered
        return output

    def probe_preview(self, output: Path) -> dict[str, Any]:
        try:
            process = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration:stream=codec_type,codec_name",
                    "-of",
                    "json",
                    str(output),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LongHighlightMaterializationError(
                f"ffprobe could not inspect preview: {exc}"
            ) from exc
        if process.returncode != 0:
            raise LongHighlightMaterializationError(
                f"ffprobe rejected preview: {(process.stderr or '').strip()}"
            )
        try:
            payload = json.loads(process.stdout)
            duration = float(payload["format"]["duration"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise LongHighlightMaterializationError(
                "ffprobe returned invalid preview data"
            ) from exc
        streams = payload.get("streams") if isinstance(payload, Mapping) else None
        streams = streams if isinstance(streams, list) else []
        video = next(
            (row.get("codec_name") for row in streams if row.get("codec_type") == "video"),
            None,
        )
        audio = next(
            (row.get("codec_name") for row in streams if row.get("codec_type") == "audio"),
            None,
        )
        return {"video_codec": video, "audio_codec": audio, "duration_sec": duration}

    def rollback(self, transaction: Mapping[str, Any]) -> None:
        canonical = transaction.get("canonical")
        canonical = canonical if isinstance(canonical, Mapping) else {}
        canonical_name = _text(canonical.get("name"))
        original_uid = _text(canonical.get("uid"))
        work = transaction.get("work")
        work_uid = _text(work.get("uid")) if isinstance(work, Mapping) else ""
        original = self._timeline_by_uid(original_uid) if original_uid else None
        active_work = self._timeline_by_uid(work_uid) if work_uid else None
        if active_work is None:
            requested_work = _text(transaction.get("requested_work_name"))
            active_work = self._timeline_by_name(requested_work) if requested_work else None
        if active_work is None and canonical_name:
            named_canonical = self._timeline_by_name(canonical_name)
            if named_canonical is not original:
                active_work = named_canonical
        if active_work is not None and active_work is not original:
            if not self.project.GetMediaPool().DeleteTimelines([active_work]):
                raise LongHighlightMaterializationError("cannot remove failed work Timeline")
        if original is not None and canonical_name and original.GetName() != canonical_name:
            if original.SetName(canonical_name) is False or original.GetName() != canonical_name:
                raise LongHighlightMaterializationError("cannot restore canonical Timeline name")
        if original is None and transaction.get("status") != "starting":
            raise LongHighlightMaterializationError("retained backup Timeline UID is missing")
        if original is not None:
            if _timeline_uid(original) != original_uid or original.GetName() != canonical_name:
                raise LongHighlightMaterializationError(
                    "restored canonical Timeline name/UID does not match transaction"
                )
            baseline = transaction.get("baseline")
            if isinstance(baseline, Mapping) and _timeline_structure(
                original, baseline_only=True
            ) != dict(baseline):
                raise LongHighlightMaterializationError(
                    "restored canonical Editorial Master/audio baseline changed"
                )
        if not self.project_manager.SaveProject():
            raise LongHighlightMaterializationError("Resolve SaveProject failed during rollback")

    def commit(self, transaction: Mapping[str, Any], *, keep_backup: bool) -> None:
        if not keep_backup:
            backup = transaction.get("backup")
            backup_uid = _text(backup.get("uid")) if isinstance(backup, Mapping) else ""
            timeline = self._timeline_by_uid(backup_uid) if backup_uid else None
            if timeline is not None and not self.project.GetMediaPool().DeleteTimelines([timeline]):
                raise LongHighlightMaterializationError("cannot delete retained backup Timeline")
        if not self.project_manager.SaveProject():
            raise LongHighlightMaterializationError("Resolve SaveProject failed during commit")

    def timelines_equivalent(self, first_uid: str, second_uid: str) -> bool:
        first = self._timeline_by_uid(first_uid)
        second = self._timeline_by_uid(second_uid)
        if first is None or second is None:
            return False
        return _timeline_structure(first, baseline_only=False) == _timeline_structure(
            second, baseline_only=False
        )

    def _timelines(self) -> list[Any]:
        return [
            timeline
            for index in range(1, self.project.GetTimelineCount() + 1)
            if (timeline := self.project.GetTimelineByIndex(index)) is not None
        ]

    def _timeline_by_uid(self, uid: str) -> Any | None:
        return next(
            (timeline for timeline in self._timelines() if _timeline_uid(timeline) == uid), None
        )

    def _timeline_by_name(self, name: str) -> Any | None:
        matches = [timeline for timeline in self._timelines() if timeline.GetName() == name]
        return matches[0] if len(matches) == 1 else None

    def _exact_timeline(self, name: str, uid: str) -> Any:
        timeline = self._timeline_by_uid(uid)
        if timeline is None or timeline.GetName() != name:
            raise LongHighlightMaterializationError(
                f"Resolve Timeline name/UID target is missing: {name} / {uid}"
            )
        return timeline


def apply_preview(
    episode_root: Path,
    cut_id: str,
    state: Mapping[str, Any],
    *,
    canonical_name: str,
    canonical_uid: str,
    preview_path: Path,
    transaction_path: Path | None = None,
    adapter: Any,
) -> dict[str, Any]:
    """Apply approved recipes to a duplicate timeline and render a checked preview.

    ``adapter`` is the Resolve host-exchange boundary.  Keeping it explicit makes
    this state machine testable without starting Resolve or a media process.
    """

    episode_root = Path(episode_root)
    transaction_path = (
        Path(transaction_path)
        if transaction_path
        else _default_transaction_path(episode_root, cut_id)
    )
    _ensure_transaction_slot_available(transaction_path)
    # Finish every read-only gate before transaction metadata or Resolve
    # Timeline mutation.  Invalid semantic state therefore cannot trigger a
    # cleanup write against an otherwise untouched project.
    project_recipes(episode_root, cut_id, state)
    recipe_dir = transaction_path.parent / "recipes"
    emitted = emit_recipes(episode_root, cut_id, state, output_dir=recipe_dir)
    baseline = adapter.snapshot_baseline(canonical_name, canonical_uid)
    token = uuid.uuid4().hex[:10]
    transaction: dict[str, Any] = {
        "schema_version": 1,
        "cut_id": cut_id,
        "status": "starting",
        "canonical": {"name": canonical_name, "uid": canonical_uid},
        "requested_work_name": f"__lh_work__{cut_id}__{token}",
        "requested_backup_name": f"__lh_backup__{cut_id}__{token}",
        "baseline": baseline,
        "history": [],
    }
    _record_transaction(transaction_path, transaction, "starting")

    try:
        swapped = adapter.duplicate_swap(
            canonical_name,
            canonical_uid,
            transaction["requested_work_name"],
            transaction["requested_backup_name"],
        )
        for key in ("canonical", "work", "backup"):
            row = swapped.get(key)
            if (
                not isinstance(row, Mapping)
                or not _text(row.get("name"))
                or not _text(row.get("uid"))
            ):
                raise LongHighlightMaterializationError(
                    f"Resolve duplicate-swap did not return {key} identity"
                )
            transaction[key] = dict(row)
        _record_transaction(transaction_path, transaction, "open")

        counts = adapter.apply_recipes(
            transaction["work"]["name"],
            transaction["work"]["uid"],
            Path(emitted["broll_path"]),
            Path(emitted["titles_path"]),
        )
        after = adapter.snapshot_baseline(transaction["work"]["name"], transaction["work"]["uid"])
        if after != baseline:
            raise LongHighlightMaterializationError(
                "Resolve apply changed Editorial Master track 1 or audio baseline"
            )
        rendered = Path(
            adapter.render_preview(
                transaction["work"]["name"],
                transaction["work"]["uid"],
                Path(preview_path),
            )
        )
        probe = _validate_preview_probe(adapter.probe_preview(rendered))
        transaction["counts"] = dict(counts)
        transaction["preview"] = {"path": str(rendered), **probe}
        _record_transaction(transaction_path, transaction, "preview_ready")
        return dict(transaction)
    except BaseException as exc:
        try:
            adapter.rollback(transaction)
        except Exception as rollback_exc:
            transaction["failure"] = str(exc)
            transaction["rollback_failure"] = str(rollback_exc)
            _record_transaction(transaction_path, transaction, "rollback_failed")
            raise LongHighlightMaterializationError(
                f"materialization failed and rollback failed: {rollback_exc}"
            ) from exc
        transaction["failure"] = str(exc)
        _record_transaction(transaction_path, transaction, "rolled_back")
        if isinstance(exc, KeyboardInterrupt):
            raise
        if isinstance(exc, LongHighlightMaterializationError):
            raise
        raise LongHighlightMaterializationError(f"Resolve materialization failed: {exc}") from exc


def commit_transaction(
    transaction_path: Path,
    *,
    adapter: Any,
    keep_backup: bool = True,
) -> dict[str, Any]:
    """Close a preview-ready transaction while retaining its recovery backup by default."""

    path = Path(transaction_path)
    transaction = _read_transaction(path)
    if transaction.get("status") != "preview_ready":
        raise LongHighlightMaterializationError(
            f"only preview_ready transaction can commit: {transaction.get('status')}"
        )
    adapter.commit(transaction, keep_backup=keep_backup)
    transaction["backup_retained"] = bool(keep_backup)
    _record_transaction(path, transaction, "committed")
    return dict(transaction)


def rollback_transaction(transaction_path: Path, *, adapter: Any) -> dict[str, Any]:
    """Restore the canonical timeline from identities persisted in transaction JSON."""

    path = Path(transaction_path)
    transaction = _read_transaction(path)
    if transaction.get("status") not in {
        "starting",
        "open",
        "preview_ready",
        "rollback_failed",
    }:
        raise LongHighlightMaterializationError(
            f"transaction cannot roll back from status: {transaction.get('status')}"
        )
    try:
        adapter.rollback(transaction)
    except Exception as exc:
        transaction["rollback_failure"] = str(exc)
        _record_transaction(path, transaction, "rollback_failed")
        raise LongHighlightMaterializationError(f"Resolve rollback failed: {exc}") from exc
    transaction.pop("rollback_failure", None)
    _record_transaction(path, transaction, "rolled_back")
    return dict(transaction)


def supersede_stale_transaction(
    transaction_path: Path,
    *,
    adapter: Any,
    active: Mapping[str, Any] | None = None,
    backup: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Close stale metadata only after a read-only item/timing equivalence check."""

    path = Path(transaction_path)
    transaction = _read_transaction(path)
    if transaction.get("status") not in {"starting", "open", "preview_ready"}:
        raise LongHighlightMaterializationError(
            f"transaction is not stale/active: {transaction.get('status')}"
        )
    if active is not None:
        transaction["work"] = dict(active)
    if backup is not None:
        transaction["backup"] = dict(backup)
    work = transaction.get("work")
    retained_backup = transaction.get("backup")
    if not isinstance(work, Mapping) or not isinstance(retained_backup, Mapping):
        raise LongHighlightMaterializationError(
            "stale transaction lacks work/backup timeline identities"
        )
    work_uid = _text(work.get("uid"))
    backup_uid = _text(retained_backup.get("uid"))
    if not work_uid or not backup_uid:
        raise LongHighlightMaterializationError(
            "stale transaction lacks work/backup timeline identities"
        )
    if adapter.timelines_equivalent(work_uid, backup_uid) is not True:
        raise LongHighlightMaterializationError(
            "stale canonical and retained backup are not structurally equivalent"
        )
    transaction["backup_retained"] = True
    _record_transaction(path, transaction, "superseded")
    return dict(transaction)


def emit_recipes(
    episode_root: Path,
    cut_id: str,
    state: Mapping[str, Any],
    *,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Validate state and write the two existing renderer recipe shapes."""

    episode_root = Path(episode_root)
    destination = (
        Path(output_dir) if output_dir is not None else episode_root / "highlights" / "tighten"
    )
    projection = project_recipes(episode_root, cut_id, state)
    projected_broll = _materialize_photo_containers(episode_root, cut_id, projection["broll"])
    structural = _load_structural_rows(episode_root, cut_id, projected_broll)
    destination.mkdir(parents=True, exist_ok=True)
    broll_path = destination / f"{cut_id}_broll.json"
    titles_path = destination / f"{cut_id}_titles.json"
    _write_json(broll_path, {"items": [*structural, *projected_broll]})
    _write_json(titles_path, {"titles": projection["titles"]})
    return {
        "status": "recipes-emitted",
        "cut_id": cut_id,
        "broll_path": str(broll_path),
        "titles_path": str(titles_path),
        "counts": {
            "broll": len(projection["broll"]),
            "titles": len(projection["titles"]),
            "structural": len(structural),
        },
    }


def validate_projection(
    episode_root: Path,
    cut_id: str,
    state: Mapping[str, Any],
    *,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Validate a projection without writing recipes or contacting Resolve."""

    episode_root = Path(episode_root)
    destination = (
        Path(output_dir) if output_dir is not None else episode_root / "highlights" / "tighten"
    )
    projection = project_recipes(episode_root, cut_id, state)
    structural = _load_structural_rows(episode_root, cut_id, projection["broll"])
    return {
        "status": "projection-valid",
        "cut_id": cut_id,
        "broll_path": str(destination / f"{cut_id}_broll.json"),
        "titles_path": str(destination / f"{cut_id}_titles.json"),
        "counts": {
            "broll": len(projection["broll"]),
            "titles": len(projection["titles"]),
            "structural": len(structural),
        },
    }


def project_recipes(
    episode_root: Path,
    cut_id: str,
    state: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Project approved DP selections into broll/title recipe rows without writing."""

    episode_root = Path(episode_root)
    _validate_state_gate(state, cut_id)
    cut_duration = _tight_duration(state)
    dp_events = state["stages"]["dp"]["events"]
    materialization_options = state.get("materialization")
    omitted_event_ids = {
        _text(event_id)
        for event_id in (
            materialization_options.get("omit_event_ids", [])
            if isinstance(materialization_options, Mapping)
            else []
        )
        if _text(event_id)
    }
    broll: list[dict[str, Any]] = []
    titles: list[dict[str, Any]] = []
    lanes: dict[str, list[tuple[float, float, str]]] = {}

    for event_id, event in dp_events.items():
        if event_id in omitted_event_ids:
            continue
        data = event.get("data") if isinstance(event, Mapping) else None
        if not isinstance(data, Mapping):
            raise LongHighlightMaterializationError(f"DP event {event_id} has no usable data")
        candidates = data.get("candidates")
        selections = data.get("selections")
        if not isinstance(candidates, list) or not candidates:
            raise LongHighlightMaterializationError(f"DP event {event_id} has no candidates")
        if not isinstance(selections, list) or not selections:
            raise LongHighlightMaterializationError(f"DP event {event_id} has no selections")
        by_id = {
            str(candidate.get("candidate_id")): candidate
            for candidate in candidates
            if isinstance(candidate, Mapping) and candidate.get("candidate_id")
        }
        lane = _text(data.get("target_lane"))
        implementation = _text(data.get("implementation_kind"))
        if lane not in {"broll_track2", "title_track3", "content_card_track4"}:
            raise LongHighlightMaterializationError(f"DP event {event_id} has invalid target lane")

        for selection in selections:
            if not isinstance(selection, Mapping):
                raise LongHighlightMaterializationError(f"DP event {event_id} selection is invalid")
            candidate_id = _text(selection.get("candidate_id"))
            candidate = by_id.get(candidate_id)
            if candidate is None:
                raise LongHighlightMaterializationError(
                    f"DP event {event_id} selection candidate is missing: {candidate_id}"
                )
            t0, t1 = _selection_times(selection, cut_duration, event_id)
            _append_lane_range(lanes, lane, t0, t1, f"{event_id}/{candidate_id}")
            source_range = _source_range(selection, candidate, event_id)
            media_path = _media_path(candidate, event_id)
            _validate_media(episode_root, media_path, candidate, selection, event_id)
            materialization = {
                "materialization_id": candidate_id,
                "implementation_kind": implementation,
                "target_lane": lane,
                "t0": t0,
                "t1": t1,
                "source_range": source_range,
                "media": {"path": media_path},
                "on_screen_text": _on_screen_text(candidate),
                "render_spec": {
                    "component": candidate.get("component"),
                    "render_params": dict(candidate.get("render_params") or {}),
                },
                "semantic_justification": _text(data.get("semantic_justification")),
            }
            if lane == "title_track3":
                titles.append(_title_row(materialization, candidate, implementation))
            else:
                broll.append(_broll_row(materialization, candidate, implementation, lane))

    broll.sort(key=lambda row: (float(row["t0"]), float(row["t1"]), str(row["slug"])))
    titles.sort(key=lambda row: (float(row["t0"]), float(row["t1"]), str(row["text"])))
    return {"broll": broll, "titles": titles}


def _materialize_photo_containers(
    episode_root: Path,
    cut_id: str,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rendered: list[dict[str, Any]] = []
    destination = (
        episode_root
        / "highlights"
        / "long-orchestrator-v2"
        / cut_id
        / "materialization"
        / "photo-containers"
    )
    for original in rows:
        if original.get("kind") != "photo":
            rendered.append(original)
            continue
        row = dict(original)
        materialization = dict(row["visual_materialization"])
        source_media = dict(materialization["media"])
        source_path = Path(str(source_media["path"]))
        source = source_path if source_path.is_absolute() else episode_root / source_path
        frame_count = max(1, int(round((float(row["t1"]) - float(row["t0"])) * 30)))
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "-", str(row["slug"])).strip("-._") or "photo"
        output = destination / f"{safe_id}-{frame_count}f.mp4"
        _freeze_frame_video(source, output, frame_count=frame_count)
        relative = output.relative_to(episode_root).as_posix()
        duration = frame_count / 30.0
        materialization["photo_source"] = source_media
        materialization["photo_source_range"] = dict(materialization["source_range"])
        materialization["media"] = {"path": relative}
        materialization["source_range"] = {"start_sec": 0.0, "end_sec": duration}
        materialization["renderer_container"] = {
            "kind": "video",
            "codec": "h264",
            "fps": 30,
            "frame_count": frame_count,
        }
        row.update(
            {
                "kind": "video",
                "semantic_kind": "photo",
                "media_path": relative,
                "src_in": 0.0,
                "source_range": {"start_sec": 0.0, "end_sec": duration},
                "visual_materialization": materialization,
            }
        )
        rendered.append(row)
    return rendered


def _freeze_frame_video(source: Path, output: Path, *, frame_count: int) -> None:
    if _valid_freeze_frame(output, frame_count=frame_count, source=source):
        return
    executable = shutil.which("ffmpeg")
    if executable is None:
        raise LongHighlightMaterializationError(
            "ffmpeg is required to wrap photo assets for Resolve"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.stem + ".tmp.mp4")
    process = subprocess.run(
        [
            executable,
            "-y",
            "-v",
            "error",
            "-loop",
            "1",
            "-framerate",
            "30",
            "-i",
            str(source),
            "-vf",
            "pad=ceil(iw/2)*2:ceil(ih/2)*2:0:0,scale=in_range=pc:out_range=tv,format=yuv420p",
            "-frames:v",
            str(frame_count),
            "-r",
            "30",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-color_range",
            "tv",
            "-movflags",
            "+faststart",
            str(temporary),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if process.returncode != 0 or not _valid_freeze_frame(
        temporary, frame_count=frame_count, source=None
    ):
        raise LongHighlightMaterializationError(
            f"ffmpeg could not wrap photo asset: {(process.stderr or '').strip()[-300:]}"
        )
    os.replace(temporary, output)


def _valid_freeze_frame(output: Path, *, frame_count: int, source: Path | None) -> bool:
    if not output.is_file() or (
        source is not None and output.stat().st_mtime < source.stat().st_mtime
    ):
        return False
    executable = shutil.which("ffprobe")
    if executable is None:
        return False
    try:
        process = subprocess.run(
            [
                executable,
                "-v",
                "error",
                "-count_frames",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,width,height,r_frame_rate,pix_fmt,nb_read_frames",
                "-of",
                "json",
                str(output),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        stream = json.loads(process.stdout)["streams"][0]
        return (
            process.returncode == 0
            and stream.get("codec_name") == "h264"
            and stream.get("pix_fmt") == "yuv420p"
            and stream.get("r_frame_rate") == "30/1"
            and int(stream.get("nb_read_frames")) == frame_count
            and int(stream.get("width")) % 2 == 0
            and int(stream.get("height")) % 2 == 0
        )
    except (
        OSError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        return False


def _validate_state_gate(state: Mapping[str, Any], cut_id: str) -> None:
    if state.get("human", {}).get("approved") is not True:
        raise LongHighlightMaterializationError("human winner is not approved")
    winner = state.get("winner")
    if not isinstance(winner, Mapping) or _text(winner.get("id")) != cut_id:
        raise LongHighlightMaterializationError("state winner does not match cut id")
    stages = state.get("stages")
    if not isinstance(stages, Mapping):
        raise LongHighlightMaterializationError("state stages are missing")
    for name in ("tighten", "director", "dp", "visual"):
        if stages.get(name, {}).get("status") != "approved":
            raise LongHighlightMaterializationError(f"stage {name} is not approved")
    dp_events = stages["dp"].get("events")
    visual_events = stages["visual"].get("events")
    if not isinstance(dp_events, Mapping) or not isinstance(visual_events, Mapping):
        raise LongHighlightMaterializationError("DP or visual events are missing")
    for event_id, visual in visual_events.items():
        if not isinstance(visual, Mapping) or visual.get("status") != "approved":
            raise LongHighlightMaterializationError(f"visual event {event_id} is not approved")
    for event_id, event in dp_events.items():
        if not isinstance(event, Mapping) or event.get("status") != "approved":
            raise LongHighlightMaterializationError(f"DP event {event_id} is not approved")
        visual = visual_events.get(event_id)
        if not isinstance(visual, Mapping) or visual.get("status") != "approved":
            raise LongHighlightMaterializationError(f"visual event {event_id} is not approved")


def _tight_duration(state: Mapping[str, Any]) -> float:
    ref = state.get("refs", {}).get("tighten")
    if not isinstance(ref, str) or not ref.strip():
        raise LongHighlightMaterializationError("tighten ref is missing")
    path = Path(ref)
    if not path.is_file():
        raise LongHighlightMaterializationError(f"tighten ref is not readable: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise LongHighlightMaterializationError(f"tighten ref is not readable: {path}") from exc
    stamps = re.findall(r"-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})", text)
    if not stamps:
        raise LongHighlightMaterializationError("tighten SRT has no cue range")
    return max(_timestamp_seconds(stamp) for stamp in stamps)


def _selection_times(
    selection: Mapping[str, Any], cut_duration: float, event_id: str
) -> tuple[float, float]:
    try:
        t0 = float(selection["t0"])
        t1 = float(selection["t1"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LongHighlightMaterializationError(
            f"DP event {event_id} selection time is invalid"
        ) from exc
    if not math.isfinite(t0) or not math.isfinite(t1) or t0 < 0 or t1 <= t0 or t1 > cut_duration:
        raise LongHighlightMaterializationError(f"DP event {event_id} is outside tight cut")
    return t0, t1


def _source_range(
    selection: Mapping[str, Any], candidate: Mapping[str, Any], event_id: str
) -> dict[str, float]:
    raw = selection.get("source_range")
    if not isinstance(raw, Mapping):
        raise LongHighlightMaterializationError(f"DP event {event_id} source range is missing")
    try:
        start = float(raw["start_sec"])
        end = float(raw["end_sec"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LongHighlightMaterializationError(
            f"DP event {event_id} source range is invalid"
        ) from exc
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
        raise LongHighlightMaterializationError(f"DP event {event_id} source range is invalid")
    evidence = candidate.get("playable_evidence")
    if isinstance(evidence, Mapping) and isinstance(evidence.get("duration_sec"), (int, float)):
        if end > float(evidence["duration_sec"]):
            raise LongHighlightMaterializationError(
                f"DP event {event_id} source range exceeds playable media"
            )
    return {"start_sec": start, "end_sec": end}


def _media_path(candidate: Mapping[str, Any], event_id: str) -> str:
    for key in ("media", "preview_media"):
        container = candidate.get(key)
        if isinstance(container, Mapping) and _text(container.get("path")):
            return _text(container["path"])
    if _text(candidate.get("media_path")):
        return _text(candidate["media_path"])
    raise LongHighlightMaterializationError(f"DP event {event_id} media path is missing")


def _validate_media(
    episode_root: Path,
    media_path: str,
    candidate: Mapping[str, Any],
    selection: Mapping[str, Any],
    event_id: str,
) -> None:
    # Older adopted DP rows may omit these booleans after a completed visual
    # review.  Explicit false still blocks; otherwise require the bytes below.
    if candidate.get("playable") is False or selection.get("playable") is False:
        raise LongHighlightMaterializationError(f"DP event {event_id} media is not playable")
    path = Path(media_path)
    resolved = path if path.is_absolute() else episode_root / path
    try:
        if not resolved.is_file():
            raise OSError("not a file")
        with resolved.open("rb") as stream:
            if not stream.read(1):
                raise OSError("empty file")
    except OSError as exc:
        raise LongHighlightMaterializationError(
            f"DP event {event_id} media is not readable: {resolved}"
        ) from exc
    if resolved.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
        try:
            with Image.open(resolved) as image:
                image.verify()
        except (OSError, UnidentifiedImageError) as exc:
            raise LongHighlightMaterializationError(
                f"DP event {event_id} image is not readable: {resolved}"
            ) from exc


def _title_row(
    materialization: dict[str, Any],
    candidate: Mapping[str, Any],
    implementation: str,
) -> dict[str, Any]:
    if implementation not in {"hero_title", "supporting_title"}:
        raise LongHighlightMaterializationError(
            f"title lane implementation is invalid: {implementation}"
        )
    params = candidate.get("render_params")
    if not isinstance(params, Mapping):
        raise LongHighlightMaterializationError("title render params are missing")
    text = _text(params.get("text")) or _text(params.get("title"))
    if not text:
        raise LongHighlightMaterializationError("title text is missing")
    return {
        "text": text,
        "t0": materialization["t0"],
        "t1": materialization["t1"],
        "tier": int(params.get("tier", 1 if implementation == "hero_title" else 2)),
        "style": _text(params.get("style")) or "orange",
        "pos_y": float(params.get("pos_y", 0.58)),
        "source_range": materialization["source_range"],
        "media_path": materialization["media"]["path"],
        "visual_materialization": materialization,
    }


def _broll_row(
    materialization: dict[str, Any],
    candidate: Mapping[str, Any],
    implementation: str,
    lane: str,
) -> dict[str, Any]:
    if lane == "broll_track2":
        if implementation not in {"stock_video", "photo"}:
            raise LongHighlightMaterializationError(
                f"broll lane implementation is invalid: {implementation}"
            )
        kind = "video" if implementation == "stock_video" else "photo"
    else:
        if implementation not in {"transition_title", "sticker_pair", "person_inset"}:
            raise LongHighlightMaterializationError(
                f"content-card implementation is invalid: {implementation}"
            )
        kind = "sticker" if implementation in {"sticker_pair", "person_inset"} else "concept"
    row = {
        "kind": kind,
        "slug": materialization["materialization_id"],
        "t0": materialization["t0"],
        "t1": materialization["t1"],
        "src_in": materialization["source_range"]["start_sec"],
        "source_range": materialization["source_range"],
        "media_path": materialization["media"]["path"],
        "on_screen_text": materialization["on_screen_text"],
        "visual_materialization": materialization,
    }
    if lane == "content_card_track4":
        row["comp"] = implementation
        row["vars"] = dict(candidate.get("render_params") or {})
    return row


def _append_lane_range(
    lanes: dict[str, list[tuple[float, float, str]]],
    lane: str,
    t0: float,
    t1: float,
    label: str,
) -> None:
    ranges = lanes.setdefault(lane, [])
    if any(t0 < old_t1 and t1 > old_t0 for old_t0, old_t1, _old_label in ranges):
        raise LongHighlightMaterializationError(f"visual events overlap on {lane}: {label}")
    ranges.append((t0, t1, label))


def _load_structural_rows(
    episode_root: Path,
    cut_id: str,
    projected_broll: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    path = episode_root / "highlights" / "tighten" / f"{cut_id}_broll.json"
    if not path.is_file():
        return []
    try:
        items = json.loads(path.read_text(encoding="utf-8"))["items"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise LongHighlightMaterializationError(
            f"existing broll recipe is invalid: {path}"
        ) from exc
    if not isinstance(items, list):
        raise LongHighlightMaterializationError("existing broll recipe items must be an array")
    structural_kinds = {"camera-correction", "guest-namecard", "badge"}
    rows = [
        dict(item)
        for item in items
        if isinstance(item, Mapping)
        and (
            _text(item.get("kind")) in structural_kinds
            or (
                _text(item.get("kind")) == "concept" and _text(item.get("slug")) == "guest-namecard"
            )
        )
    ]
    content_ranges = [
        (float(item["t0"]), float(item["t1"]))
        for item in (projected_broll or [])
        if _text(item.get("kind")) in {"video", "photo"}
    ]
    return [
        row
        for row in rows
        if not (
            (
                _text(row.get("kind")) == "guest-namecard"
                or (
                    _text(row.get("kind")) == "concept"
                    and _text(row.get("slug")) == "guest-namecard"
                )
            )
            and any(
                float(row["t0"]) < content_end and float(row["t1"]) > content_start
                for content_start, content_end in content_ranges
            )
        )
    ]


def _on_screen_text(candidate: Mapping[str, Any]) -> str | None:
    params = candidate.get("render_params")
    if not isinstance(params, Mapping):
        return None
    return _text(params.get("text")) or _text(params.get("title")) or None


def _timestamp_seconds(value: str) -> float:
    hours, minutes, remainder = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(remainder)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _timeline_uid(timeline: Any) -> str:
    for method_name in ("GetUniqueId", "GetUniqueID"):
        method = getattr(timeline, method_name, None)
        value = method() if callable(method) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _timeline_structure(timeline: Any, *, baseline_only: bool) -> dict[str, Any]:
    track_rows: list[dict[str, Any]] = []
    track_types = ("video", "audio") if baseline_only else ("video", "audio", "subtitle")
    for track_type in track_types:
        count = int(timeline.GetTrackCount(track_type) or 0)
        indexes = (1,) if baseline_only and track_type == "video" else range(1, count + 1)
        for track_index in indexes:
            if track_index > count:
                continue
            items = timeline.GetItemListInTrack(track_type, track_index) or []
            track_rows.append(
                {
                    "type": track_type,
                    "index": track_index,
                    "items": [_timeline_item_structure(item) for item in items],
                }
            )
    return {
        "start_frame": int(timeline.GetStartFrame()),
        "end_frame": int(timeline.GetEndFrame()),
        "tracks": track_rows,
    }


def _timeline_item_structure(item: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "name": str(item.GetName() or ""),
        "start": int(item.GetStart()),
        "end": int(item.GetEnd()),
    }
    for key, method_name in (("left_offset", "GetLeftOffset"), ("right_offset", "GetRightOffset")):
        method = getattr(item, method_name, None)
        if callable(method):
            try:
                row[key] = int(method())
            except (TypeError, ValueError):
                pass
    try:
        media = item.GetMediaPoolItem()
        row["media_path"] = str(media.GetClipProperty("File Path") or "") if media else ""
    except (AttributeError, TypeError):
        row["media_path"] = ""
    return row


def _recipe_cut_id(path: Path, suffix: str) -> str:
    name = Path(path).name
    if not name.endswith(suffix) or len(name) <= len(suffix):
        raise LongHighlightMaterializationError(f"recipe filename must end in {suffix}: {path}")
    return name[: -len(suffix)]


def _default_transaction_path(episode_root: Path, cut_id: str) -> Path:
    return (
        episode_root
        / "highlights"
        / "long-orchestrator-v2"
        / cut_id
        / "materialization"
        / "transaction.json"
    )


def _ensure_transaction_slot_available(path: Path) -> None:
    if not path.is_file():
        return
    prior = _read_transaction(path)
    if prior.get("status") not in {"committed", "rolled_back", "superseded"}:
        raise LongHighlightMaterializationError(
            f"transaction is still active ({prior.get('status')}): {path}"
        )


def _read_transaction(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LongHighlightMaterializationError(
            f"transaction metadata is not readable: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise LongHighlightMaterializationError(f"transaction metadata is invalid: {path}")
    return value


def _record_transaction(path: Path, transaction: dict[str, Any], status: str) -> None:
    transaction["status"] = status
    history = transaction.setdefault("history", [])
    history.append({"status": status, "at": datetime.now(timezone.utc).isoformat()})
    _write_json(path, transaction)


def _validate_preview_probe(probe: Any) -> dict[str, Any]:
    if not isinstance(probe, Mapping):
        raise LongHighlightMaterializationError("preview probe returned no media information")
    video = _text(probe.get("video_codec")).lower()
    audio_value = probe.get("audio_codec")
    audio = _text(audio_value).lower() if audio_value is not None else None
    try:
        duration = float(probe.get("duration_sec"))
    except (TypeError, ValueError) as exc:
        raise LongHighlightMaterializationError("preview duration is invalid") from exc
    if video not in {"h264", "avc1"}:
        raise LongHighlightMaterializationError("preview video codec is not H.264")
    if audio not in {None, "aac"}:
        raise LongHighlightMaterializationError("preview audio codec is not AAC or none")
    if not math.isfinite(duration) or duration <= 0:
        raise LongHighlightMaterializationError("preview duration is not positive")
    return {"video_codec": "h264", "audio_codec": audio, "duration_sec": duration}


__all__ = [
    "LongHighlightMaterializationError",
    "ResolveScriptingAdapter",
    "apply_preview",
    "commit_transaction",
    "emit_recipes",
    "project_recipes",
    "rollback_transaction",
    "supersede_stale_transaction",
    "validate_projection",
]
