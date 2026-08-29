"""DaVinci Resolve Adapter for typed Finished Cut Production transactions."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Protocol

from ._records import MaterializationPlan
from ._resolve import (
    CommitReceipt,
    PreviewRender,
    ResolveTransactionError,
    TimelineIdentity,
    TimelineSnapshot,
    TimelineWorkspace,
)
from ._timeline_apply import (
    PreRenderedAssetCatalog,
    TimelinePlacement,
    project_timeline_application,
)

TimelineTrackType = Literal["video", "audio", "subtitle"]
TimelineProperty = tuple[str, str | int | float | bool | None]


@dataclass(frozen=True, slots=True)
class ResolveProjectIdentity:
    episode_id: str
    name: str
    uid: str


@dataclass(frozen=True, slots=True)
class ResolveCutBinding:
    cut_id: str
    canonical: TimelineIdentity


@dataclass(frozen=True, slots=True)
class ResolveProjectBinding:
    episode_id: str
    project_name: str
    project_uid: str
    cuts: tuple[ResolveCutBinding, ...]

    def __post_init__(self) -> None:
        if not self.episode_id or not self.project_name or not self.project_uid or not self.cuts:
            raise ResolveTransactionError("Resolve project binding is incomplete")
        if len({cut.cut_id for cut in self.cuts}) != len(self.cuts):
            raise ResolveTransactionError("Resolve cut identity is ambiguous")
        if len({cut.canonical.uid for cut in self.cuts}) != len(self.cuts):
            raise ResolveTransactionError("Resolve canonical Timeline UID is ambiguous")


@dataclass(frozen=True, slots=True)
class ResolveTimelineItem:
    item_id: str
    track_type: TimelineTrackType
    track_index: int
    start_frame: int
    end_frame: int
    source_in_frame: int
    source_out_frame: int
    properties: tuple[TimelineProperty, ...]
    media_digest: str


@dataclass(frozen=True, slots=True)
class ResolveTimelineTrack:
    track_type: TimelineTrackType
    track_index: int
    name: str
    enabled: bool
    locked: bool
    item_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResolveTimelineState:
    start_frame: int
    end_frame: int
    items: tuple[ResolveTimelineItem, ...]
    tracks: tuple[ResolveTimelineTrack, ...] = ()
    frame_rate: float | None = None


@dataclass(frozen=True, slots=True)
class RenderRequest:
    output: Path
    container: Literal["mp4"] = "mp4"
    video_codec: Literal["H264"] = "H264"
    export_audio: bool = True
    audio_codec: Literal["aac"] = "aac"


@dataclass(frozen=True, slots=True)
class MediaProbeResult:
    path: Path
    duration_sec: float
    video_codec: str
    audio_codec: str | None
    decode_ok: bool = True
    offline_frame_count: int = 0


class ResolveFacade(Protocol):
    """External Resolve scripting seam; all methods target exact UIDs."""

    def project_identity(self) -> ResolveProjectIdentity: ...

    def timeline_identities(self) -> tuple[TimelineIdentity, ...]: ...

    def timeline_state(self, uid: str) -> ResolveTimelineState: ...

    def duplicate_timeline(self, uid: str, name: str) -> TimelineIdentity: ...

    def rename_timeline(self, uid: str, name: str) -> None: ...

    def delete_timeline(self, uid: str) -> None: ...

    def clear_derived_lanes(self, uid: str) -> None: ...

    def append_pre_rendered(self, uid: str, placement: TimelinePlacement) -> None: ...

    def select_timeline(self, uid: str) -> None: ...

    def render_preview(self, uid: str, request: RenderRequest) -> Path: ...

    def save_project(self) -> None: ...


class MediaProbe(Protocol):
    def inspect(self, path: Path) -> MediaProbeResult: ...


@dataclass(frozen=True, slots=True)
class FFprobeProcess:
    returncode: int
    stdout: str
    stderr: str


class FFprobeRunner(Protocol):
    def run(self, arguments: tuple[str, ...], *, timeout_sec: float) -> FFprobeProcess: ...


class SubprocessFFprobeRunner:
    """Concrete external-process Adapter used by the production media probe."""

    def run(self, arguments: tuple[str, ...], *, timeout_sec: float) -> FFprobeProcess:
        try:
            process = subprocess.run(
                arguments,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ResolveTransactionError(f"ffprobe process failed: {exc}") from exc
        return FFprobeProcess(
            returncode=process.returncode,
            stdout=process.stdout,
            stderr=process.stderr,
        )


class FFprobeMediaProbe:
    """Read the exact rendered file through a narrow ffprobe process seam."""

    def __init__(self, *, runner: FFprobeRunner | None = None) -> None:
        self._runner = runner or SubprocessFFprobeRunner()

    def inspect(self, path: Path) -> MediaProbeResult:
        path = Path(path)
        if not path.is_file():
            raise ResolveTransactionError("preview file is missing before ffprobe")
        result = self._runner.run(
            (
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,codec_name",
                "-of",
                "json",
                str(path),
            ),
            timeout_sec=30.0,
        )
        if result.returncode != 0:
            raise ResolveTransactionError(f"ffprobe rejected preview: {result.stderr.strip()}")
        try:
            payload = json.loads(result.stdout)
            duration = float(payload["format"]["duration"])
            streams = payload["streams"]
            video_codec = next(row["codec_name"] for row in streams if row["codec_type"] == "video")
            audio_codec = next(
                (row["codec_name"] for row in streams if row["codec_type"] == "audio"),
                None,
            )
        except (json.JSONDecodeError, KeyError, StopIteration, TypeError, ValueError) as exc:
            raise ResolveTransactionError("ffprobe returned invalid preview metadata") from exc
        decode = self._runner.run(
            (
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-f",
                "null",
                "-",
            ),
            timeout_sec=120.0,
        )
        return MediaProbeResult(
            path=path,
            duration_sec=duration,
            video_codec=str(video_codec),
            audio_codec=str(audio_codec) if audio_codec is not None else None,
            decode_ok=decode.returncode == 0,
            offline_frame_count=0,
        )


class DaVinciResolveTimelineAdapter:
    """Keep UID-bound duplicate/apply mechanics behind the TimelineAdapter Interface."""

    def __init__(
        self,
        *,
        facade: ResolveFacade,
        probe: MediaProbe,
        binding: ResolveProjectBinding,
        assets: PreRenderedAssetCatalog,
    ) -> None:
        self._facade = facade
        self._probe = probe
        self._binding = binding
        self._assets = assets

    def snapshot(self, timeline: TimelineIdentity) -> TimelineSnapshot:
        self._assert_project()
        exact = self._exact_timeline(timeline)
        state = self._facade.timeline_state(exact.uid)
        protected_items = tuple(
            item
            for item in state.items
            if item.track_type in {"audio", "subtitle"}
            or (item.track_type == "video" and item.track_index == 1)
        )
        protected_tracks = tuple(
            track
            for track in state.tracks
            if track.track_type in {"audio", "subtitle"}
            or (track.track_type == "video" and track.track_index == 1)
        )
        return TimelineSnapshot(
            protected_fingerprint=_fingerprint(
                {
                    "start_frame": state.start_frame,
                    "end_frame": state.end_frame,
                    "tracks": [
                        {
                            **{
                                key: value
                                for key, value in asdict(track).items()
                                if key != "item_ids"
                            },
                            "item_count": len(track.item_ids),
                        }
                        for track in protected_tracks
                    ],
                    "items": [
                        {key: value for key, value in asdict(item).items() if key != "item_id"}
                        for item in protected_items
                    ],
                }
            ),
            full_fingerprint=_fingerprint(asdict(state)),
        )

    def preflight_plan(self, plan: MaterializationPlan) -> None:
        """Resolve every final component and classification before host mutation."""

        self._assert_project()
        self._cut_by_id(plan.cut_id)
        if plan.episode_id != self._binding.episode_id:
            raise ResolveTransactionError("typed plan does not match Resolve episode")
        project_timeline_application(plan, self._assets)

    def duplicate(
        self,
        canonical: TimelineIdentity,
        *,
        transaction_id: str,
    ) -> TimelineWorkspace:
        self._assert_project()
        cut = self._cut_for_canonical(canonical)
        self._exact_timeline(canonical)
        work_name = f"__fcp_work__{cut.cut_id}__{transaction_id}"
        backup_name = f"__fcp_backup__{cut.cut_id}__{transaction_id}"
        occupied = {identity.name for identity in self._facade.timeline_identities()}
        if work_name in occupied or backup_name in occupied:
            raise ResolveTransactionError("Resolve transaction Timeline name already exists")
        duplicate = self._facade.duplicate_timeline(canonical.uid, work_name)
        try:
            if duplicate.name != work_name or duplicate.uid == canonical.uid:
                raise ResolveTransactionError(
                    "Resolve duplicate did not bind the expected name and distinct UID"
                )
            self._exact_timeline(duplicate)
            self._exact_timeline(canonical)
            self._facade.rename_timeline(canonical.uid, backup_name)
            backup = TimelineIdentity(name=backup_name, uid=canonical.uid)
            self._exact_timeline(backup)
            self._facade.rename_timeline(duplicate.uid, canonical.name)
            work = TimelineIdentity(name=canonical.name, uid=duplicate.uid)
            self._exact_timeline(work)
            self._facade.save_project()
        except BaseException:
            identities = {item.uid: item for item in self._facade.timeline_identities()}
            if duplicate.uid != canonical.uid and duplicate.uid in identities:
                self._facade.delete_timeline(duplicate.uid)
            original = identities.get(canonical.uid)
            if original is not None and original.name != canonical.name:
                self._facade.rename_timeline(canonical.uid, canonical.name)
            self._facade.save_project()
            raise
        return TimelineWorkspace(canonical=canonical, work=work, backup=backup)

    def apply_plan(self, work: TimelineIdentity, plan: MaterializationPlan) -> None:
        self._assert_project()
        cut = self._cut_by_id(plan.cut_id)
        if plan.episode_id != self._binding.episode_id or work.name != cut.canonical.name:
            raise ResolveTransactionError("typed plan does not match Resolve episode and cut")
        if work.uid == cut.canonical.uid:
            raise ResolveTransactionError("typed plan cannot mutate canonical Timeline in place")
        application = project_timeline_application(plan, self._assets)
        self._exact_timeline(work)
        backups = [
            identity
            for identity in self._facade.timeline_identities()
            if identity.uid == cut.canonical.uid
            and identity.name.startswith(f"__fcp_backup__{cut.cut_id}__")
        ]
        if len(backups) != 1:
            raise ResolveTransactionError("work Timeline has no exact retained canonical backup")
        self._facade.clear_derived_lanes(work.uid)
        for placement in application.placements:
            self._facade.append_pre_rendered(work.uid, placement)
        self._facade.save_project()

    def render_preview(self, work: TimelineIdentity, output: Path) -> PreviewRender:
        self._assert_project()
        self._exact_work_timeline(work)
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        request = RenderRequest(output=output)
        self._facade.select_timeline(work.uid)
        self._exact_timeline(work)
        rendered = Path(self._facade.render_preview(work.uid, request))
        if rendered != output or not rendered.is_file():
            raise ResolveTransactionError(
                "Resolve preview did not produce the exact requested file"
            )
        result = self._probe.inspect(rendered)
        if result.path != rendered:
            raise ResolveTransactionError("media probe result does not bind the rendered preview")
        if (
            result.video_codec.lower() not in {"h264", "avc1"}
            or result.audio_codec is None
            or result.audio_codec.lower() != "aac"
            or not math.isfinite(result.duration_sec)
            or result.duration_sec <= 0
            or not result.decode_ok
            or result.offline_frame_count != 0
        ):
            raise ResolveTransactionError("Resolve preview failed H.264/AAC/decode probe contract")
        return PreviewRender(
            path=rendered,
            duration_sec=result.duration_sec,
            video_codec=result.video_codec,
            audio_codec=result.audio_codec,
        )

    def rollback(self, workspace: TimelineWorkspace) -> None:
        self._assert_project()
        cut = self._cut_for_canonical(workspace.canonical)
        transaction_id = _transaction_from_backup_name(workspace.backup.name, cut.cut_id)
        identities = self._facade.timeline_identities()
        already_restored = (
            workspace.work.uid not in {identity.uid for identity in identities}
            and workspace.canonical in identities
        )
        if already_restored:
            return
        self._validate_open_workspace(
            workspace,
            transaction_id=transaction_id,
            cut_id=cut.cut_id,
        )
        self._facade.delete_timeline(workspace.work.uid)
        self._exact_timeline(workspace.backup)
        self._facade.rename_timeline(workspace.backup.uid, workspace.canonical.name)
        self._exact_timeline(workspace.canonical)
        self._facade.save_project()

    def commit(
        self,
        workspace: TimelineWorkspace,
        *,
        transaction_id: str,
        cut_id: str,
        retain_backup: bool,
    ) -> CommitReceipt:
        if not retain_backup:
            raise ResolveTransactionError("Finished Cut commit must retain its rollback backup")
        self._validate_open_workspace(workspace, transaction_id=transaction_id, cut_id=cut_id)
        receipt = self._receipt(workspace, transaction_id=transaction_id, cut_id=cut_id)
        self._facade.save_project()
        return receipt

    def compensate(self, workspace: TimelineWorkspace, receipt: CommitReceipt) -> None:
        self._assert_project()
        expected = self._receipt(
            workspace,
            transaction_id=receipt.transaction_id,
            cut_id=receipt.cut_id,
        )
        if receipt != expected:
            raise ResolveTransactionError(
                "Resolve compensation receipt does not bind exact transaction backup"
            )
        identities = self._facade.timeline_identities()
        already_restored = (
            workspace.work.uid not in {identity.uid for identity in identities}
            and workspace.canonical in identities
        )
        if already_restored:
            return
        self._validate_open_workspace(
            workspace,
            transaction_id=receipt.transaction_id,
            cut_id=receipt.cut_id,
        )
        self._facade.delete_timeline(workspace.work.uid)
        self._exact_timeline(workspace.backup)
        self._facade.rename_timeline(workspace.backup.uid, workspace.canonical.name)
        self._exact_timeline(workspace.canonical)
        self._facade.save_project()

    def _assert_project(self) -> None:
        actual = self._facade.project_identity()
        expected = ResolveProjectIdentity(
            episode_id=self._binding.episode_id,
            name=self._binding.project_name,
            uid=self._binding.project_uid,
        )
        if actual != expected:
            raise ResolveTransactionError("Resolve project identity does not match binding")

    def _exact_timeline(self, expected: TimelineIdentity) -> TimelineIdentity:
        identities = self._facade.timeline_identities()
        matches = [
            timeline
            for timeline in identities
            if timeline.uid == expected.uid and timeline.name == expected.name
        ]
        if (
            len(matches) != 1
            or sum(item.uid == expected.uid for item in identities) != 1
            or sum(item.name == expected.name for item in identities) != 1
        ):
            raise ResolveTransactionError("Resolve Timeline name/UID identity is not exact")
        return matches[0]

    def _cut_for_canonical(self, canonical: TimelineIdentity) -> ResolveCutBinding:
        matches = [cut for cut in self._binding.cuts if cut.canonical == canonical]
        if len(matches) != 1:
            raise ResolveTransactionError("canonical Timeline is not bound to one cut")
        return matches[0]

    def _cut_by_id(self, cut_id: str) -> ResolveCutBinding:
        matches = [cut for cut in self._binding.cuts if cut.cut_id == cut_id]
        if len(matches) != 1:
            raise ResolveTransactionError("cut identity is not in Resolve binding")
        return matches[0]

    def _exact_work_timeline(self, work: TimelineIdentity) -> ResolveCutBinding:
        matches = [cut for cut in self._binding.cuts if cut.canonical.name == work.name]
        if len(matches) != 1 or work.uid == matches[0].canonical.uid:
            raise ResolveTransactionError("Resolve work Timeline does not bind one duplicate cut")
        self._exact_timeline(work)
        cut = matches[0]
        backups = [
            identity
            for identity in self._facade.timeline_identities()
            if identity.uid == cut.canonical.uid
            and identity.name.startswith(f"__fcp_backup__{cut.cut_id}__")
        ]
        if len(backups) != 1:
            raise ResolveTransactionError("Resolve work Timeline has no exact retained backup")
        self._exact_timeline(backups[0])
        return cut

    def _validate_open_workspace(
        self,
        workspace: TimelineWorkspace,
        *,
        transaction_id: str,
        cut_id: str,
    ) -> ResolveCutBinding:
        self._assert_project()
        cut = self._cut_by_id(cut_id)
        expected_backup = TimelineIdentity(
            name=f"__fcp_backup__{cut_id}__{transaction_id}",
            uid=cut.canonical.uid,
        )
        if (
            workspace.canonical != cut.canonical
            or workspace.backup != expected_backup
            or workspace.work.name != cut.canonical.name
            or workspace.work.uid == cut.canonical.uid
        ):
            raise ResolveTransactionError("Resolve workspace does not bind exact cut transaction")
        self._exact_timeline(workspace.work)
        self._exact_timeline(workspace.backup)
        return cut

    def _receipt(
        self,
        workspace: TimelineWorkspace,
        *,
        transaction_id: str,
        cut_id: str,
    ) -> CommitReceipt:
        core = {
            "episode_id": self._binding.episode_id,
            "project_uid": self._binding.project_uid,
            "cut_id": cut_id,
            "transaction_id": transaction_id,
            "work_uid": workspace.work.uid,
            "backup_uid": workspace.backup.uid,
        }
        identity = _fingerprint(core)[:24]
        return CommitReceipt(
            transaction_id=transaction_id,
            cut_id=cut_id,
            work_uid=workspace.work.uid,
            transaction_receipt_id=f"resolve-receipt-{identity}",
            rollback_ref=(
                f"resolve-backup:{self._binding.project_uid}:"
                f"{workspace.backup.uid}:{transaction_id}"
            ),
            backup_retained=True,
        )


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _transaction_from_backup_name(name: str, cut_id: str) -> str:
    prefix = f"__fcp_backup__{cut_id}__"
    if not name.startswith(prefix) or len(name) == len(prefix):
        raise ResolveTransactionError("Resolve backup name does not bind a transaction")
    return name[len(prefix) :]
