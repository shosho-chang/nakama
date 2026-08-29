from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.brook.script_video.editorial_master import EditorialMasterSelection
from agents.brook.script_video.finished_cut_production._materialization_fusion import (
    CanonicalAuthorityError,
    ResolveCanonicalTimelineAuthority,
    VerifiedEditorialMasterContract,
    VerifiedEditorialMasterContractCache,
)
from agents.brook.script_video.finished_cut_production._resolve import (
    TimelineIdentity,
    TimelineSnapshot,
)
from agents.brook.script_video.finished_cut_production._resolve_davinci import (
    ResolveCutBinding,
    ResolveProjectBinding,
    ResolveProjectIdentity,
    ResolveTimelineState,
)

_CONTENT_HASH = "8e7c13c2c55bc0df0c05241cfd91a9bf5c6b484b58058dae42d2bfaa7576805b"
_MEDIA_SHA256 = "39b8db8b82eb114447b9ea4e877899bc505ff1a64788fd62e78b90ee8902ec80"


class _CountingMasterVerifier:
    def __init__(self, selection: EditorialMasterSelection) -> None:
        self.selection = selection
        self.calls = 0

    def __call__(
        self,
        episode_root: str | Path,
        *,
        expected_episode_id: str | None = None,
        expected_content_hash: str | None = None,
    ) -> EditorialMasterSelection:
        self.calls += 1
        assert Path(episode_root) == self.selection.receipt_path.parents[2]
        assert expected_episode_id == "episode-1"
        assert expected_content_hash == _CONTENT_HASH
        return self.selection


class _MasterSource:
    def __init__(self, contract: VerifiedEditorialMasterContract) -> None:
        self.contract = contract
        self.calls: list[tuple[str, str]] = []

    def load(
        self,
        *,
        episode_id: str,
        editorial_master_content_hash: str,
    ) -> VerifiedEditorialMasterContract:
        self.calls.append((episode_id, editorial_master_content_hash))
        return self.contract


class _ReadOnlyFacade:
    def __init__(
        self,
        *,
        project: ResolveProjectIdentity,
        identities: tuple[TimelineIdentity, ...],
        states: tuple[ResolveTimelineState, ...],
    ) -> None:
        self.project = project
        self.identities = identities
        self.states = list(states)
        self.read_calls: list[tuple[str, str | None]] = []
        self.mutation_calls = 0

    def project_identity(self) -> ResolveProjectIdentity:
        self.read_calls.append(("project_identity", None))
        return self.project

    def timeline_identities(self) -> tuple[TimelineIdentity, ...]:
        self.read_calls.append(("timeline_identities", None))
        return self.identities

    def timeline_state(self, uid: str) -> ResolveTimelineState:
        self.read_calls.append(("timeline_state", uid))
        if len(self.states) > 1:
            return self.states.pop(0)
        return self.states[0]

    def __getattr__(self, name: str) -> object:
        if name in {
            "duplicate_timeline",
            "rename_timeline",
            "delete_timeline",
            "clear_derived_lanes",
            "append_pre_rendered",
            "select_timeline",
            "render_preview",
            "save_project",
        }:
            self.mutation_calls += 1
            raise AssertionError(f"read-only authority called mutation: {name}")
        raise AttributeError(name)


class _SnapshotReader:
    def __init__(self, snapshots: tuple[TimelineSnapshot, ...]) -> None:
        self.snapshots = list(snapshots)
        self.calls: list[TimelineIdentity] = []

    def snapshot(self, timeline: TimelineIdentity) -> TimelineSnapshot:
        self.calls.append(timeline)
        if len(self.snapshots) > 1:
            return self.snapshots.pop(0)
        return self.snapshots[0]


def _selection(episode_root: Path) -> EditorialMasterSelection:
    version = episode_root / "editorial-master" / "v1"
    version.mkdir(parents=True)
    video = version / "master.mp4"
    video.write_bytes(b"verified master bytes")
    receipt = {
        "contract": "podcast-editorial-master-v1",
        "episode_id": "episode-1",
        "content_hash": _CONTENT_HASH,
        "project": {"name": "Episode Project"},
        "timeline": {
            "name": "Editorial Master",
            "uid": "master-timeline-uid",
            "fps": "30",
            "duration_sec": 3418.3,
        },
        "artifacts": {
            "media": {
                "path": "editorial-master/v1/master.mp4",
                "bytes": video.stat().st_size,
                "sha256": _MEDIA_SHA256,
            }
        },
    }
    receipt_path = version / "EDITORIAL-MASTER.json"
    receipt_path.write_text("{}", encoding="utf-8")
    return EditorialMasterSelection(
        video_path=video,
        srt_path=version / "master.srt",
        snapshot_path=version / "timeline-snapshot.json",
        receipt_path=receipt_path,
        receipt=receipt,
    )


def _master_contract(tmp_path: Path) -> VerifiedEditorialMasterContract:
    return VerifiedEditorialMasterContract(
        episode_id="episode-1",
        editorial_master_content_hash=_CONTENT_HASH,
        master_media_path=tmp_path / "master.mp4",
        master_media_sha256=_MEDIA_SHA256,
        master_media_bytes=123,
        resolve_project_name="Episode Project",
        editorial_master_timeline_name="Editorial Master",
        editorial_master_timeline_uid="master-timeline-uid",
        frame_rate=30.0,
        duration_sec=3418.3,
    )


def _binding(canonical: TimelineIdentity) -> ResolveProjectBinding:
    return ResolveProjectBinding(
        episode_id="episode-1",
        project_name="Episode Project",
        project_uid="resolve-project:trusted",
        cuts=(ResolveCutBinding("punch-L04", canonical),),
    )


def _state(*, frame_rate: float = 30.0, end_frame: int = 100_800) -> ResolveTimelineState:
    return ResolveTimelineState(
        start_frame=86_400,
        end_frame=end_frame,
        items=(),
        tracks=(),
        frame_rate=frame_rate,
    )


def test_verified_master_contract_is_cached_without_rehashing_on_restart(tmp_path: Path) -> None:
    episode_root = tmp_path / "episode-1"
    verifier = _CountingMasterVerifier(_selection(episode_root))
    cache_path = tmp_path / "authority" / "master-contract.json"

    first = VerifiedEditorialMasterContractCache(
        episode_root=episode_root,
        cache_path=cache_path,
        verifier=verifier,
    ).load(
        episode_id="episode-1",
        editorial_master_content_hash=_CONTENT_HASH,
    )
    reopened = VerifiedEditorialMasterContractCache(
        episode_root=episode_root,
        cache_path=cache_path,
        verifier=verifier,
    ).load(
        episode_id="episode-1",
        editorial_master_content_hash=_CONTENT_HASH,
    )

    assert first == reopened
    assert first.editorial_master_content_hash == _CONTENT_HASH
    assert first.master_media_sha256 == _MEDIA_SHA256
    assert first.master_media_path == episode_root / "editorial-master/v1/master.mp4"
    assert first.frame_rate == 30.0
    assert first.duration_sec == 3418.3
    assert verifier.calls == 1
    assert str(episode_root) not in cache_path.read_text(encoding="utf-8")


def test_tampered_verified_master_cache_fails_closed_without_reverification(tmp_path: Path) -> None:
    episode_root = tmp_path / "episode-1"
    verifier = _CountingMasterVerifier(_selection(episode_root))
    cache_path = tmp_path / "authority" / "master-contract.json"
    cache = VerifiedEditorialMasterContractCache(
        episode_root=episode_root,
        cache_path=cache_path,
        verifier=verifier,
    )
    cache.load(
        episode_id="episode-1",
        editorial_master_content_hash=_CONTENT_HASH,
    )
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    payload["payload"]["master_media_sha256"] = "f" * 64
    cache_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CanonicalAuthorityError) as raised:
        VerifiedEditorialMasterContractCache(
            episode_root=episode_root,
            cache_path=cache_path,
            verifier=verifier,
        ).load(
            episode_id="episode-1",
            editorial_master_content_hash=_CONTENT_HASH,
        )

    assert raised.value.reason_code == "editorial_master_cache_invalid"
    assert verifier.calls == 1


def test_resolve_authority_returns_exact_uid_bound_read_only_inspection(tmp_path: Path) -> None:
    canonical = TimelineIdentity("Long 3", "timeline-L04")
    binding = _binding(canonical)
    state = _state()
    facade = _ReadOnlyFacade(
        project=ResolveProjectIdentity(
            "episode-1",
            "Episode Project",
            "resolve-project:trusted",
        ),
        identities=(canonical,),
        states=(state, state, state, state),
    )
    baseline = TimelineSnapshot("protected", "full")
    snapshots = _SnapshotReader((baseline, baseline))
    source = _MasterSource(_master_contract(tmp_path))
    authority = ResolveCanonicalTimelineAuthority(
        binding=binding,
        facade=facade,
        timeline_adapter=snapshots,
        editorial_master=source,
    )

    result = authority.inspect(
        episode_id="episode-1",
        cut_id="punch-L04",
        editorial_master_content_hash=_CONTENT_HASH,
    )

    assert len(result) == 1
    assert result[0].canonical == canonical
    assert result[0].editorial_master_content_hash == _CONTENT_HASH
    assert result[0].editorial_master_media_sha256 == _MEDIA_SHA256
    assert result[0].timeline_frame_rate == 30.0
    assert result[0].editorial_master_frame_rate == 30.0
    assert result[0].editorial_master_duration_sec == 3418.3
    assert result[0].state == state
    assert result[0].baseline == baseline
    assert source.calls == [("episode-1", _CONTENT_HASH)]
    assert snapshots.calls == [canonical, canonical]
    assert facade.mutation_calls == 0


@pytest.mark.parametrize(
    ("binding", "project", "identities", "reason_code"),
    [
        (
            _binding(TimelineIdentity("Long 3", "timeline-L04")),
            ResolveProjectIdentity("episode-1", "Wrong Project", "resolve-project:trusted"),
            (TimelineIdentity("Long 3", "timeline-L04"),),
            "resolve_project_identity_mismatch",
        ),
        (
            ResolveProjectBinding(
                "episode-1",
                "Episode Project",
                "resolve-project:trusted",
                (ResolveCutBinding("value-L01", TimelineIdentity("Long 1", "timeline-L01")),),
            ),
            ResolveProjectIdentity("episode-1", "Episode Project", "resolve-project:trusted"),
            (TimelineIdentity("Long 1", "timeline-L01"),),
            "canonical_binding_unknown",
        ),
        (
            _binding(TimelineIdentity("Long 3", "timeline-L04")),
            ResolveProjectIdentity("episode-1", "Episode Project", "resolve-project:trusted"),
            (),
            "canonical_timeline_unknown",
        ),
        (
            _binding(TimelineIdentity("Long 3", "timeline-L04")),
            ResolveProjectIdentity("episode-1", "Episode Project", "resolve-project:trusted"),
            (
                TimelineIdentity("Long 3", "timeline-L04"),
                TimelineIdentity("Long 3", "timeline-L04"),
            ),
            "canonical_timeline_ambiguous",
        ),
        (
            _binding(TimelineIdentity("Long 3", "timeline-L04")),
            ResolveProjectIdentity("episode-1", "Episode Project", "resolve-project:trusted"),
            (TimelineIdentity("Renamed", "timeline-L04"),),
            "canonical_timeline_ambiguous",
        ),
    ],
)
def test_resolve_authority_rejects_wrong_project_or_non_exact_cut_uid(
    tmp_path: Path,
    binding: ResolveProjectBinding,
    project: ResolveProjectIdentity,
    identities: tuple[TimelineIdentity, ...],
    reason_code: str,
) -> None:
    facade = _ReadOnlyFacade(project=project, identities=identities, states=(_state(),))
    source = _MasterSource(_master_contract(tmp_path))
    authority = ResolveCanonicalTimelineAuthority(
        binding=binding,
        facade=facade,
        timeline_adapter=_SnapshotReader((TimelineSnapshot("protected", "full"),)),
        editorial_master=source,
    )

    with pytest.raises(CanonicalAuthorityError) as raised:
        authority.inspect(
            episode_id="episode-1",
            cut_id="punch-L04",
            editorial_master_content_hash=_CONTENT_HASH,
        )

    assert raised.value.reason_code == reason_code
    assert facade.mutation_calls == 0
    assert source.calls == []


def test_resolve_authority_rejects_ambiguous_persisted_cut_binding(tmp_path: Path) -> None:
    canonical = TimelineIdentity("Long 3", "timeline-L04")
    binding = SimpleNamespace(
        episode_id="episode-1",
        project_name="Episode Project",
        project_uid="resolve-project:trusted",
        cuts=(
            ResolveCutBinding("punch-L04", canonical),
            ResolveCutBinding("punch-L04", canonical),
        ),
    )
    facade = _ReadOnlyFacade(
        project=ResolveProjectIdentity("episode-1", "Episode Project", "resolve-project:trusted"),
        identities=(canonical,),
        states=(_state(),),
    )
    authority = ResolveCanonicalTimelineAuthority(
        binding=binding,  # type: ignore[arg-type]
        facade=facade,
        timeline_adapter=_SnapshotReader((TimelineSnapshot("protected", "full"),)),
        editorial_master=_MasterSource(_master_contract(tmp_path)),
    )

    with pytest.raises(CanonicalAuthorityError) as raised:
        authority.inspect(
            episode_id="episode-1",
            cut_id="punch-L04",
            editorial_master_content_hash=_CONTENT_HASH,
        )

    assert raised.value.reason_code == "canonical_binding_ambiguous"
    assert facade.mutation_calls == 0


@pytest.mark.parametrize(
    ("states", "snapshots"),
    [
        (
            (_state(), _state(end_frame=100_801)),
            (TimelineSnapshot("protected", "full"),) * 2,
        ),
        (
            (_state(),) * 2,
            (
                TimelineSnapshot("protected", "full"),
                TimelineSnapshot("protected-changed", "full-changed"),
            ),
        ),
    ],
)
def test_resolve_authority_rejects_live_state_or_baseline_drift(
    tmp_path: Path,
    states: tuple[ResolveTimelineState, ...],
    snapshots: tuple[TimelineSnapshot, ...],
) -> None:
    canonical = TimelineIdentity("Long 3", "timeline-L04")
    facade = _ReadOnlyFacade(
        project=ResolveProjectIdentity("episode-1", "Episode Project", "resolve-project:trusted"),
        identities=(canonical,),
        states=states,
    )
    authority = ResolveCanonicalTimelineAuthority(
        binding=_binding(canonical),
        facade=facade,
        timeline_adapter=_SnapshotReader(snapshots),
        editorial_master=_MasterSource(_master_contract(tmp_path)),
    )

    with pytest.raises(CanonicalAuthorityError) as raised:
        authority.inspect(
            episode_id="episode-1",
            cut_id="punch-L04",
            editorial_master_content_hash=_CONTENT_HASH,
        )

    assert raised.value.reason_code == "canonical_timeline_live_drift"
    assert facade.mutation_calls == 0


@pytest.mark.parametrize(
    ("contract", "state", "reason_code"),
    [
        (
            replace(_master_contract(Path(".")), resolve_project_name="Wrong Project"),
            _state(),
            "editorial_master_project_mismatch",
        ),
        (
            _master_contract(Path(".")),
            replace(_state(), frame_rate=None),
            "timeline_frame_rate_unavailable",
        ),
    ],
)
def test_resolve_authority_rejects_wrong_master_project_or_missing_live_fps(
    contract: VerifiedEditorialMasterContract,
    state: ResolveTimelineState,
    reason_code: str,
) -> None:
    canonical = TimelineIdentity("Long 3", "timeline-L04")
    facade = _ReadOnlyFacade(
        project=ResolveProjectIdentity("episode-1", "Episode Project", "resolve-project:trusted"),
        identities=(canonical,),
        states=(state, state),
    )
    authority = ResolveCanonicalTimelineAuthority(
        binding=_binding(canonical),
        facade=facade,
        timeline_adapter=_SnapshotReader((TimelineSnapshot("protected", "full"),) * 2),
        editorial_master=_MasterSource(contract),
    )

    with pytest.raises(CanonicalAuthorityError) as raised:
        authority.inspect(
            episode_id="episode-1",
            cut_id="punch-L04",
            editorial_master_content_hash=_CONTENT_HASH,
        )

    assert raised.value.reason_code == reason_code
    assert facade.mutation_calls == 0
