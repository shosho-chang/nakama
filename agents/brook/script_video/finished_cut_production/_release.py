"""Finished Cut Release lifecycle and exact-current persistence."""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Protocol, cast

from ._records import (
    ComponentLane,
    EventRecord,
    FinishedCutRelease,
    MaterializationPlan,
    ProbeValue,
    ProjectedComponent,
    ReleaseArtifact,
    StagedReleaseCandidate,
    _mint_staged_release_candidate,
    _rehydrate_finished_cut_release,
    _rehydrate_release_projected_component,
    _seal_finished_cut_release,
)


class ReleaseLifecycleError(ValueError):
    """A Candidate, Release, or current index violates the production contract."""

    def __init__(self, message: str, *, reason: str = "invalid") -> None:
        super().__init__(message)
        self.reason = reason


class TimelineTransactions(Protocol):
    """Read-only transaction state needed by the release lifecycle."""

    def inspect_transaction(self, transaction_id: str) -> Mapping[str, object]: ...


class CurrentPointerWriter(Protocol):
    """Atomic filesystem Adapter for the sole observable publication step."""

    def replace(self, staging_path: Path, current_path: Path) -> None: ...


class _AtomicCurrentPointerWriter:
    def replace(self, staging_path: Path, current_path: Path) -> None:
        os.replace(staging_path, current_path)


PreviewProbe = Callable[[Path], Mapping[str, object]]


class FinishedCutReleaseLifecycle:
    """Advance immutable Candidate and Release records behind one filesystem seam."""

    def __init__(
        self,
        episode_root: Path,
        *,
        transactions: TimelineTransactions,
        preview_probe: PreviewProbe,
        pointer_writer: CurrentPointerWriter | None = None,
    ) -> None:
        self.episode_root = Path(episode_root).resolve()
        self.transactions = transactions
        self.preview_probe = preview_probe
        self.pointer_writer = pointer_writer or _AtomicCurrentPointerWriter()

    def stage_candidate(
        self,
        plan: MaterializationPlan,
        *,
        editorial_master_id: str,
        winner_id: str,
        tight_cut_id: str,
        transaction_id: str,
        preview_path: Path,
        subtitle_path: Path,
    ) -> StagedReleaseCandidate:
        transaction = self.transactions.inspect_transaction(transaction_id)
        if transaction.get("transaction_id") != transaction_id:
            raise ReleaseLifecycleError(
                "transaction identity does not match the requested transaction"
            )
        if transaction.get("status") != "preview_ready":
            raise ReleaseLifecycleError("a Candidate requires a preview_ready transaction")

        preview_probe = self.preview_probe(Path(preview_path))
        duration = preview_probe.get("duration_sec")
        if (
            not isinstance(duration, (int, float))
            or isinstance(duration, bool)
            or not math.isfinite(float(duration))
            or float(duration) <= 0
        ):
            raise ReleaseLifecycleError("preview probe requires a positive finite duration_sec")
        if any(component.t1 > float(duration) for component in plan.components):
            raise ReleaseLifecycleError("projected component timing exceeds the preview duration")
        preview = self._artifact(
            Path(preview_path),
            duration_sec=float(duration),
            probe=preview_probe,
        )
        subtitle = self._artifact(Path(subtitle_path))
        candidate_core = {
            "episode_id": plan.episode_id,
            "cut_id": plan.cut_id,
            "format": plan.format,
            "command_id": plan.command_id,
            "run_id": plan.run_id,
            "editorial_master_id": editorial_master_id,
            "winner_id": winner_id,
            "tight_cut_id": tight_cut_id,
            "director_acceptance_id": plan.director_acceptance_id,
            "dp_acceptance_id": plan.dp_acceptance_id,
            "visual_acceptance_id": plan.visual_acceptance_id,
            "materialization_plan": asdict(plan),
            "preview": asdict(preview),
            "subtitle": asdict(subtitle),
            "preview_ready_transaction_id": transaction_id,
        }
        candidate_id = f"candidate-{_sha256_json(candidate_core)[:24]}"
        return _mint_staged_release_candidate(
            candidate_id=candidate_id,
            episode_id=plan.episode_id,
            cut_id=plan.cut_id,
            format=plan.format,
            command_id=plan.command_id,
            run_id=plan.run_id,
            editorial_master_id=editorial_master_id,
            winner_id=winner_id,
            tight_cut_id=tight_cut_id,
            director_acceptance_id=plan.director_acceptance_id,
            dp_acceptance_id=plan.dp_acceptance_id,
            visual_acceptance_id=plan.visual_acceptance_id,
            materialization_plan=plan,
            preview=preview,
            subtitle=subtitle,
            preview_ready_transaction_id=transaction_id,
        )

    def seal_candidate(self, candidate: StagedReleaseCandidate) -> FinishedCutRelease:
        transaction = self.transactions.inspect_transaction(candidate.preview_ready_transaction_id)
        if transaction.get("transaction_id") != candidate.preview_ready_transaction_id:
            raise ReleaseLifecycleError(
                "transaction identity does not match the Candidate transaction"
            )
        if transaction.get("status") != "committed":
            raise ReleaseLifecycleError(
                "a Candidate requires a committed transaction before sealing"
            )
        transaction_receipt_id = transaction.get("transaction_receipt_id")
        rollback_ref = transaction.get("rollback_ref")
        if not isinstance(transaction_receipt_id, str) or not transaction_receipt_id:
            raise ReleaseLifecycleError("committed transaction receipt is required")
        if not isinstance(rollback_ref, str) or not rollback_ref:
            raise ReleaseLifecycleError("committed transaction rollback reference is required")
        self._verify_artifact(candidate.preview)
        self._verify_artifact(candidate.subtitle)

        release_core = {
            "candidate": asdict(candidate),
            "transaction_receipt_id": transaction_receipt_id,
            "rollback_ref": rollback_ref,
        }
        release_id = f"release-{_sha256_json(release_core)[:24]}"
        release = _seal_finished_cut_release(
            release_id=release_id,
            episode_id=candidate.episode_id,
            cut_id=candidate.cut_id,
            format=candidate.format,
            command_id=candidate.command_id,
            run_id=candidate.run_id,
            editorial_master_id=candidate.editorial_master_id,
            winner_id=candidate.winner_id,
            tight_cut_id=candidate.tight_cut_id,
            director_acceptance_id=candidate.director_acceptance_id,
            dp_acceptance_id=candidate.dp_acceptance_id,
            visual_acceptance_id=candidate.visual_acceptance_id,
            materialization_plan_id=candidate.materialization_plan.plan_id,
            events=candidate.materialization_plan.events,
            components=candidate.components,
            preview=candidate.preview,
            subtitle=candidate.subtitle,
            transaction_receipt_id=transaction_receipt_id,
            rollback_ref=rollback_ref,
        )
        receipt_path = self._release_receipt_path(release.release_id)
        self._write_once(receipt_path, _release_receipt_bytes(release))
        return release

    def publish_current(self, releases: tuple[FinishedCutRelease, ...]) -> Path:
        manifest_id, manifest_bytes = self._manifest_payload(releases)
        version_path = (
            self.episode_root / "highlights" / "releases" / "index" / "v3" / f"{manifest_id}.json"
        )
        self._write_once(version_path, manifest_bytes)

        current_path = self._current_path()
        staging_path = current_path.with_name(f".{current_path.name}.{manifest_id}.staging")
        self._write_once(staging_path, manifest_bytes)
        try:
            self.pointer_writer.replace(staging_path, current_path)
        except OSError as exc:
            raise ReleaseLifecycleError("current pointer publication failed") from exc
        return current_path

    def inspect_current(self, episode_id: str) -> tuple[FinishedCutRelease, ...]:
        current_path = self._current_path()
        try:
            current_bytes = current_path.read_bytes()
        except FileNotFoundError as exc:
            raise ReleaseLifecycleError(
                "exact current manifest is missing", reason="missing"
            ) from exc
        except OSError as exc:
            raise ReleaseLifecycleError("exact current manifest is unreadable") from exc
        try:
            document = json.loads(current_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReleaseLifecycleError("exact current manifest is invalid JSON") from exc
        if not isinstance(document, dict):
            raise ReleaseLifecycleError("exact current manifest must be an object")
        if document.get("schema") != "nakama.finished_cut_review_manifest.v3":
            raise ReleaseLifecycleError("exact current manifest is not schema v3")
        if document.get("episode_id") != episode_id:
            raise ReleaseLifecycleError("exact current manifest episode does not match")
        rows = document.get("releases")
        if not isinstance(rows, list) or not rows:
            raise ReleaseLifecycleError("exact current manifest has no Releases")

        releases: list[FinishedCutRelease] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ReleaseLifecycleError("exact current manifest Release row is invalid")
            release_id = _required_string(row, "release_id")
            release_ref = _required_string(row, "release_ref")
            expected_ref = f"highlights/releases/v1/{release_id}.json"
            if release_ref != expected_ref:
                raise ReleaseLifecycleError("exact current manifest Release ref is not canonical")
            receipt_path = self._release_receipt_path(release_id)
            try:
                receipt_bytes = receipt_path.read_bytes()
            except OSError as exc:
                raise ReleaseLifecycleError(
                    f"exact current Release receipt is unavailable: {release_id}"
                ) from exc
            if hashlib.sha256(receipt_bytes).hexdigest() != row.get("release_sha256"):
                raise ReleaseLifecycleError(f"exact current Release digest differs: {release_id}")
            release = _release_from_receipt(receipt_bytes)
            if (
                release.release_id != release_id
                or release.episode_id != episode_id
                or release.cut_id != row.get("cut_id")
                or release.format != row.get("format")
            ):
                raise ReleaseLifecycleError(f"exact current Release identity differs: {release_id}")
            releases.append(release)

        inspected = tuple(releases)
        _, expected_bytes = self._manifest_payload(inspected)
        if current_bytes != expected_bytes:
            raise ReleaseLifecycleError("exact current manifest is not canonical")
        return inspected

    def _manifest_payload(self, releases: tuple[FinishedCutRelease, ...]) -> tuple[str, bytes]:
        if not releases:
            raise ReleaseLifecycleError("manifest v3 requires at least one sealed Release")
        if any(type(release) is not FinishedCutRelease for release in releases):
            raise ReleaseLifecycleError("manifest v3 accepts only a sealed FinishedCutRelease")
        episode_id = releases[0].episode_id
        if any(release.episode_id != episode_id for release in releases):
            raise ReleaseLifecycleError("manifest v3 cannot mix episodes")
        release_keys = [(release.cut_id, release.format) for release in releases]
        if len(release_keys) != len(set(release_keys)):
            raise ReleaseLifecycleError("manifest v3 cannot contain duplicate cuts")

        entries: list[dict[str, object]] = []
        for release in sorted(
            releases,
            key=lambda item: (item.cut_id, item.format, item.release_id),
        ):
            receipt_path = self._release_receipt_path(release.release_id)
            expected_receipt = _release_receipt_bytes(release)
            try:
                receipt_bytes = receipt_path.read_bytes()
            except OSError as exc:
                raise ReleaseLifecycleError(
                    f"sealed FinishedCutRelease receipt is missing: {release.release_id}"
                ) from exc
            if receipt_bytes != expected_receipt:
                raise ReleaseLifecycleError(
                    f"sealed FinishedCutRelease receipt differs: {release.release_id}"
                )
            entries.append(
                {
                    "cut_id": release.cut_id,
                    "format": release.format,
                    "release_id": release.release_id,
                    "release_ref": receipt_path.relative_to(self.episode_root).as_posix(),
                    "release_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
                }
            )

        identity = {"episode_id": episode_id, "releases": entries}
        manifest_id = f"manifest-{_sha256_json(identity)[:24]}"
        body = {
            "schema": "nakama.finished_cut_review_manifest.v3",
            "manifest_id": manifest_id,
            **identity,
        }
        manifest_bytes = _json_bytes({**body, "content_hash": _sha256_json(body)})
        return manifest_id, manifest_bytes

    def _release_receipt_path(self, release_id: str) -> Path:
        return self.episode_root / "highlights" / "releases" / "v1" / f"{release_id}.json"

    def _current_path(self) -> Path:
        return self.episode_root / "highlights" / "review" / "finished_review_manifest_current.json"

    @staticmethod
    def _write_once(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as stream:
                stream.write(payload)
        except FileExistsError as exc:
            if path.read_bytes() != payload:
                raise ReleaseLifecycleError(
                    f"immutable Release receipt already differs: {path.name}"
                ) from exc

    def _artifact(
        self,
        path: Path,
        *,
        duration_sec: float | None = None,
        probe: Mapping[str, object] | None = None,
    ) -> ReleaseArtifact:
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(self.episode_root)
        except ValueError as exc:
            raise ReleaseLifecycleError(
                "release artifact must stay inside the episode root"
            ) from exc
        try:
            payload = resolved.read_bytes()
        except OSError as exc:
            raise ReleaseLifecycleError(
                f"release artifact is not readable: {relative.as_posix()}"
            ) from exc
        if not payload:
            raise ReleaseLifecycleError(f"release artifact is empty: {relative.as_posix()}")
        normalized_probe = tuple(
            sorted((str(key), _probe_value(value)) for key, value in (probe or {}).items())
        )
        return ReleaseArtifact(
            path=relative.as_posix(),
            bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            duration_sec=duration_sec,
            probe=normalized_probe,
        )

    def _verify_artifact(self, artifact: ReleaseArtifact) -> None:
        path = (self.episode_root / artifact.path).resolve()
        try:
            path.relative_to(self.episode_root)
            payload = path.read_bytes()
        except (ValueError, OSError) as exc:
            raise ReleaseLifecycleError(
                f"release artifact changed after Candidate: {artifact.path}"
            ) from exc
        if len(payload) != artifact.bytes or hashlib.sha256(payload).hexdigest() != artifact.sha256:
            raise ReleaseLifecycleError(
                f"release artifact changed after Candidate: {artifact.path}"
            )


def _probe_value(value: object) -> ProbeValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ReleaseLifecycleError("preview probe values must be JSON scalars")


def _sha256_json(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_bytes(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _release_receipt_bytes(release: FinishedCutRelease) -> bytes:
    release_values = asdict(release)
    release_values["events"] = [_release_event_to_dict(event) for event in release.events]
    body = {
        "schema": "nakama.finished_cut_release.v1",
        "release": release_values,
    }
    return _json_bytes({**body, "content_hash": _sha256_json(body)})


def _release_event_to_dict(event: EventRecord) -> dict[str, object]:
    """Project semantic event authority without duplicating component placement."""

    return {
        "event_id": event.event_id,
        "master_cue_ids": list(event.master_cue_ids),
        "text_hash": event.text_hash,
        "intent": event.intent,
        "asset_ref": event.asset_ref,
        "visual_status": event.visual_status,
        "text": event.text,
        "t0": event.t0,
        "t1": event.t1,
        "section_id": event.section_id,
        "display": event.display,
        "semantic_kind": event.semantic_kind,
        "intentional_aroll": event.intentional_aroll,
        "implementation_kind": event.implementation_kind,
        "lane": event.lane,
    }


def _release_from_receipt(payload: bytes) -> FinishedCutRelease:
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseLifecycleError("FinishedCutRelease receipt is invalid JSON") from exc
    if not isinstance(document, dict):
        raise ReleaseLifecycleError("FinishedCutRelease receipt must be an object")
    if set(document) != {"schema", "release", "content_hash"}:
        raise ReleaseLifecycleError("FinishedCutRelease receipt fields are invalid")
    if document.get("schema") != "nakama.finished_cut_release.v1":
        raise ReleaseLifecycleError("FinishedCutRelease receipt is not schema v1")
    values = document.get("release")
    if not isinstance(values, dict):
        raise ReleaseLifecycleError("FinishedCutRelease receipt payload is invalid")
    body = {"schema": document["schema"], "release": values}
    if document.get("content_hash") != _sha256_json(body):
        raise ReleaseLifecycleError("FinishedCutRelease receipt content hash differs")

    expected_fields = {
        "release_id",
        "episode_id",
        "cut_id",
        "format",
        "command_id",
        "run_id",
        "editorial_master_id",
        "winner_id",
        "tight_cut_id",
        "director_acceptance_id",
        "dp_acceptance_id",
        "visual_acceptance_id",
        "materialization_plan_id",
        "events",
        "components",
        "preview",
        "subtitle",
        "transaction_receipt_id",
        "rollback_ref",
    }
    if set(values) != expected_fields:
        raise ReleaseLifecycleError("FinishedCutRelease payload fields are invalid")
    format_value = _required_string(values, "format")
    if format_value not in {"long", "short"}:
        raise ReleaseLifecycleError("FinishedCutRelease format is invalid")
    raw_events = values.get("events")
    if not isinstance(raw_events, list):
        raise ReleaseLifecycleError("FinishedCutRelease events are invalid")
    events = tuple(_event_from_receipt(value) for value in raw_events)
    raw_components = values.get("components")
    if not isinstance(raw_components, list):
        raise ReleaseLifecycleError("FinishedCutRelease components are invalid")
    components = tuple(_component_from_receipt(value) for value in raw_components)
    events_by_id = {event.event_id: event for event in events}
    # Event assets remain the DP-selected neutral source; component assets are the
    # exact derived finals produced for the timeline.  Their shared authority is
    # the event identity, not asset-ref equality.
    if len(events_by_id) != len(events) or any(
        component.event_id not in events_by_id for component in components
    ):
        raise ReleaseLifecycleError("FinishedCutRelease component event binding is invalid")
    release = _rehydrate_finished_cut_release(
        release_id=_required_string(values, "release_id"),
        episode_id=_required_string(values, "episode_id"),
        cut_id=_required_string(values, "cut_id"),
        format=format_value,  # type: ignore[arg-type]
        command_id=_required_string(values, "command_id"),
        run_id=_required_string(values, "run_id"),
        editorial_master_id=_required_string(values, "editorial_master_id"),
        winner_id=_required_string(values, "winner_id"),
        tight_cut_id=_required_string(values, "tight_cut_id"),
        director_acceptance_id=_required_string(values, "director_acceptance_id"),
        dp_acceptance_id=_required_string(values, "dp_acceptance_id"),
        visual_acceptance_id=_required_string(values, "visual_acceptance_id"),
        materialization_plan_id=_required_string(values, "materialization_plan_id"),
        events=events,
        components=components,
        preview=_artifact_from_receipt(values.get("preview")),
        subtitle=_artifact_from_receipt(values.get("subtitle")),
        transaction_receipt_id=_required_string(values, "transaction_receipt_id"),
        rollback_ref=_required_string(values, "rollback_ref"),
    )
    if payload != _release_receipt_bytes(release):
        raise ReleaseLifecycleError("FinishedCutRelease receipt is not canonical")
    return release


def _event_from_receipt(value: object) -> EventRecord:
    if not isinstance(value, dict):
        raise ReleaseLifecycleError("FinishedCutRelease event is invalid")
    expected_fields = {
        "event_id",
        "master_cue_ids",
        "text_hash",
        "intent",
        "asset_ref",
        "visual_status",
        "text",
        "t0",
        "t1",
        "section_id",
        "display",
        "semantic_kind",
        "intentional_aroll",
        "implementation_kind",
        "lane",
    }
    if set(value) != expected_fields:
        raise ReleaseLifecycleError("FinishedCutRelease event fields are invalid")
    raw_cues = value.get("master_cue_ids")
    if not isinstance(raw_cues, list) or not raw_cues:
        raise ReleaseLifecycleError("FinishedCutRelease event cues are invalid")
    cue_ids = tuple(_nonempty_string(item, "master cue ID") for item in raw_cues)
    t0 = value.get("t0")
    t1 = value.get("t1")
    if (
        not isinstance(t0, (int, float))
        or isinstance(t0, bool)
        or not isinstance(t1, (int, float))
        or isinstance(t1, bool)
        or not math.isfinite(float(t0))
        or not math.isfinite(float(t1))
        or float(t0) < 0
        or float(t1) < float(t0)
    ):
        raise ReleaseLifecycleError("FinishedCutRelease event timing is invalid")
    intentional_aroll = value.get("intentional_aroll")
    if not isinstance(intentional_aroll, bool):
        raise ReleaseLifecycleError("FinishedCutRelease A-roll intent is invalid")
    lane = _optional_string(value, "lane")
    if lane not in {
        None,
        "b_roll",
        "identity_card",
        "hero_title",
        "supporting_title",
        "fullscreen_transition",
        "visual_effect",
    }:
        raise ReleaseLifecycleError("FinishedCutRelease event lane is invalid")
    return EventRecord(
        event_id=_required_string(value, "event_id"),
        master_cue_ids=cue_ids,
        text_hash=_required_sha256(value, "text_hash"),
        intent=_required_string(value, "intent"),
        asset_ref=_optional_string(value, "asset_ref"),
        visual_status=_optional_string(value, "visual_status"),
        text=str(value["text"]),
        t0=float(t0),
        t1=float(t1),
        section_id=_optional_string(value, "section_id"),
        display=str(value["display"]),
        semantic_kind=str(value["semantic_kind"]),
        intentional_aroll=intentional_aroll,
        implementation_kind=str(value["implementation_kind"]),
        lane=lane,  # type: ignore[arg-type]
    )


def _component_from_receipt(value: object) -> ProjectedComponent:
    if not isinstance(value, dict):
        raise ReleaseLifecycleError("FinishedCutRelease component is invalid")
    expected_fields = {
        "component_id",
        "event_id",
        "semantic_kind",
        "implementation_kind",
        "lane",
        "display",
        "t0",
        "t1",
        "asset_ref",
    }
    if set(value) != expected_fields:
        raise ReleaseLifecycleError("FinishedCutRelease component fields are invalid")
    semantic_kind = _required_string(value, "semantic_kind")
    implementation_kind = _required_string(value, "implementation_kind")
    lane = _required_string(value, "lane")
    allowed_projection = {
        ("chapter", "fullscreen_transition", "fullscreen_transition"),
        ("hero_title", "hero_title", "hero_title"),
        ("supporting_title", "supporting_title", "supporting_title"),
        ("b_roll", "stock_video", "b_roll"),
        ("b_roll", "photo", "b_roll"),
        ("b_roll", "non_editorial_clip", "b_roll"),
        ("b_roll", "person_inset", "b_roll"),
        ("b_roll", "camera_correction", "b_roll"),
        ("identity_card", "identity_card", "identity_card"),
        ("visual_effect", "visual_effect", "visual_effect"),
    }
    if (semantic_kind, implementation_kind, lane) not in allowed_projection:
        raise ReleaseLifecycleError("FinishedCutRelease component projection kinds are invalid")
    t0 = value.get("t0")
    t1 = value.get("t1")
    if (
        not isinstance(t0, (int, float))
        or isinstance(t0, bool)
        or not isinstance(t1, (int, float))
        or isinstance(t1, bool)
        or not math.isfinite(float(t0))
        or not math.isfinite(float(t1))
        or float(t0) < 0
        or float(t0) >= float(t1)
    ):
        raise ReleaseLifecycleError("FinishedCutRelease component timing is invalid")
    return _rehydrate_release_projected_component(
        component_id=_required_string(value, "component_id"),
        event_id=_required_string(value, "event_id"),
        semantic_kind=semantic_kind,
        implementation_kind=implementation_kind,
        lane=cast(ComponentLane, lane),
        display=_required_string(value, "display"),
        t0=float(t0),
        t1=float(t1),
        asset_ref=_optional_string(value, "asset_ref"),
    )


def _artifact_from_receipt(value: object) -> ReleaseArtifact:
    if not isinstance(value, dict):
        raise ReleaseLifecycleError("FinishedCutRelease artifact is invalid")
    if set(value) != {"path", "bytes", "sha256", "duration_sec", "probe"}:
        raise ReleaseLifecycleError("FinishedCutRelease artifact fields are invalid")
    path = _required_string(value, "path")
    parts = PurePosixPath(path)
    if parts.is_absolute() or ".." in parts.parts or ":" in parts.parts[0]:
        raise ReleaseLifecycleError("FinishedCutRelease artifact path is not relative")
    size = value.get("bytes")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ReleaseLifecycleError("FinishedCutRelease artifact byte count is invalid")
    duration_value = value.get("duration_sec")
    duration: float | None
    if duration_value is None:
        duration = None
    elif (
        isinstance(duration_value, (int, float))
        and not isinstance(duration_value, bool)
        and math.isfinite(float(duration_value))
        and float(duration_value) > 0
    ):
        duration = float(duration_value)
    else:
        raise ReleaseLifecycleError("FinishedCutRelease artifact duration is invalid")
    raw_probe = value.get("probe")
    if not isinstance(raw_probe, list):
        raise ReleaseLifecycleError("FinishedCutRelease artifact probe is invalid")
    probe: list[tuple[str, ProbeValue]] = []
    for item in raw_probe:
        if not isinstance(item, list) or len(item) != 2:
            raise ReleaseLifecycleError("FinishedCutRelease artifact probe row is invalid")
        probe.append((_nonempty_string(item[0], "probe key"), _probe_value(item[1])))
    return ReleaseArtifact(
        path=path,
        bytes=size,
        sha256=_required_sha256(value, "sha256"),
        duration_sec=duration,
        probe=tuple(probe),
    )


def _required_string(value: Mapping[str, object], key: str) -> str:
    return _nonempty_string(value.get(key), key)


def _nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReleaseLifecycleError(f"{label} must be a non-empty string")
    return value


def _optional_string(value: Mapping[str, object], key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    return _nonempty_string(item, key)


def _required_sha256(value: Mapping[str, object], key: str) -> str:
    digest = _required_string(value, key)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ReleaseLifecycleError(f"{key} must be a lowercase SHA-256 digest")
    return digest
