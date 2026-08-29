from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from agents.brook.script_video.finished_cut_production._context import (
    _mint_visual_placement,
)
from agents.brook.script_video.finished_cut_production._records import (
    EventRecord,
    FinishedCutRelease,
    MaterializationPlan,
    ReleaseArtifact,
    _mint_materialization_plan,
    _mint_projected_component,
    _rehydrate_finished_cut_release,
    _rehydrate_release_projected_component,
)
from agents.brook.script_video.finished_cut_production._release import (
    FinishedCutReleaseLifecycle,
    ReleaseLifecycleError,
    _release_from_receipt,
    _release_receipt_bytes,
)


class _FakeTimelineTransactions:
    def __init__(self, transaction_id: str, status: str) -> None:
        self.transaction_id = transaction_id
        self.status = status
        self.transaction_receipt_id: str | None = None
        self.rollback_ref: str | None = None

    def inspect_transaction(self, transaction_id: str) -> dict[str, object]:
        assert transaction_id == self.transaction_id
        return {
            "transaction_id": transaction_id,
            "status": self.status,
            "transaction_receipt_id": self.transaction_receipt_id,
            "rollback_ref": self.rollback_ref,
        }

    def commit(
        self,
        *,
        transaction_receipt_id: str = "receipt-001",
        rollback_ref: str = "backup-001",
    ) -> None:
        self.status = "committed"
        self.transaction_receipt_id = transaction_receipt_id
        self.rollback_ref = rollback_ref


class _FailingCurrentPointer:
    def __init__(self) -> None:
        self.called = False

    def replace(self, staging_path: Path, current_path: Path) -> None:
        self.called = True
        assert staging_path.exists()
        assert current_path.exists()
        raise OSError("injected pointer failure")


def _materialization_plan(
    episode_id: str = "episode-001", *, with_components: bool = False
) -> MaterializationPlan:
    event = EventRecord(
        event_id="event-001",
        master_cue_ids=("cue-10", "cue-11"),
        text_hash=hashlib.sha256(b"current quote").hexdigest(),
        intent="show the concrete example",
        asset_ref="asset:stock-001",
        visual_status="approved",
    )
    return _mint_materialization_plan(
        plan_id="plan-001",
        run_id="run-001",
        command_id="approved-cut-001",
        episode_id=episode_id,
        cut_id="value-L01",
        format="long",
        director_acceptance_id="director-001",
        dp_acceptance_id="dp-001",
        visual_acceptance_id="visual-001",
        events=(event,),
        components=(
            (
                _mint_projected_component(
                    component_id="hero-001",
                    event_id=event.event_id,
                    semantic_kind="hero_title",
                    implementation_kind="hero_title",
                    lane="hero_title",
                    display="先保留選擇權",
                    t0=0.25,
                    t1=1.75,
                    asset_ref=event.asset_ref,
                ),
            )
            if with_components
            else ()
        ),
    )


def _semantic_vs_placement_plan() -> MaterializationPlan:
    event = EventRecord(
        event_id="event-45-second-claim",
        master_cue_ids=("cue-long-1", "cue-long-2", "cue-visual"),
        text_hash=hashlib.sha256(b"45 second semantic claim").hexdigest(),
        intent="preserve the complete semantic claim",
        asset_ref=None,
        visual_status="approved",
        text="45 second semantic claim",
        t0=5.0,
        t1=50.0,
        section_id="section-1",
        display="Four-second visual conclusion",
        semantic_kind="hero_title",
        implementation_kind="hero_title",
        lane="hero_title",
        visual_placement=_mint_visual_placement(
            placement_cue_ids=("cue-visual",),
            t0=46.0,
            t1=50.0,
            section_id="section-1",
        ),
    )
    return _mint_materialization_plan(
        plan_id="plan-semantic-placement",
        run_id="run-semantic-placement",
        command_id="approved-cut-semantic-placement",
        episode_id="episode-001",
        cut_id="value-L01",
        format="long",
        director_acceptance_id="director-semantic-placement",
        dp_acceptance_id="dp-semantic-placement",
        visual_acceptance_id="visual-semantic-placement",
        events=(event,),
        components=(
            _mint_projected_component(
                component_id="component:event-45-second-claim",
                event_id=event.event_id,
                semantic_kind=event.semantic_kind,
                implementation_kind=event.implementation_kind,
                lane="hero_title",
                display=event.display,
                t0=46.0,
                t1=50.0,
                asset_ref="asset:rendered-support-title",
            ),
        ),
    )


def _write_artifacts(episode_root: Path) -> tuple[Path, Path]:
    preview = episode_root / "highlights" / "preview" / "value-L01.mp4"
    subtitle = episode_root / "highlights" / "srt" / "value-L01.srt"
    preview.parent.mkdir(parents=True)
    subtitle.parent.mkdir(parents=True)
    preview.write_bytes(b"preview bytes")
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\ncurrent quote\n",
        encoding="utf-8",
    )
    return preview, subtitle


def _preview_probe(_path: Path) -> dict[str, Any]:
    return {"duration_sec": 2.0, "video_codec": "h264", "audio_codec": "aac"}


def test_candidate_requires_preview_ready_transaction(tmp_path: Path) -> None:
    episode_root = tmp_path / "episode-001"
    preview, subtitle = _write_artifacts(episode_root)
    transactions = _FakeTimelineTransactions("tx-001", "pending")
    lifecycle = FinishedCutReleaseLifecycle(
        episode_root,
        transactions=transactions,
        preview_probe=_preview_probe,
    )

    with pytest.raises(ReleaseLifecycleError, match="preview_ready"):
        lifecycle.stage_candidate(
            _materialization_plan(),
            editorial_master_id="master-001",
            winner_id="winner-001",
            tight_cut_id="tight-001",
            transaction_id="tx-001",
            preview_path=preview,
            subtitle_path=subtitle,
        )

    transactions.status = "preview_ready"
    candidate = lifecycle.stage_candidate(
        _materialization_plan(),
        editorial_master_id="master-001",
        winner_id="winner-001",
        tight_cut_id="tight-001",
        transaction_id="tx-001",
        preview_path=preview,
        subtitle_path=subtitle,
    )

    assert candidate.preview_ready_transaction_id == "tx-001"
    assert candidate.preview.sha256 == hashlib.sha256(b"preview bytes").hexdigest()
    assert candidate.preview.duration_sec == 2.0
    assert candidate.subtitle.path == "highlights/srt/value-L01.srt"


def test_uncommitted_candidate_cannot_be_sealed(tmp_path: Path) -> None:
    episode_root = tmp_path / "episode-001"
    preview, subtitle = _write_artifacts(episode_root)
    transactions = _FakeTimelineTransactions("tx-001", "preview_ready")
    lifecycle = FinishedCutReleaseLifecycle(
        episode_root,
        transactions=transactions,
        preview_probe=_preview_probe,
    )
    candidate = lifecycle.stage_candidate(
        _materialization_plan(),
        editorial_master_id="master-001",
        winner_id="winner-001",
        tight_cut_id="tight-001",
        transaction_id="tx-001",
        preview_path=preview,
        subtitle_path=subtitle,
    )

    with pytest.raises(ReleaseLifecycleError, match="committed"):
        lifecycle.seal_candidate(candidate)


def test_committed_candidate_seals_immutable_release(tmp_path: Path) -> None:
    episode_root = tmp_path / "episode-001"
    preview, subtitle = _write_artifacts(episode_root)
    transactions = _FakeTimelineTransactions("tx-001", "preview_ready")
    lifecycle = FinishedCutReleaseLifecycle(
        episode_root,
        transactions=transactions,
        preview_probe=_preview_probe,
    )
    candidate = lifecycle.stage_candidate(
        _materialization_plan(),
        editorial_master_id="master-001",
        winner_id="winner-001",
        tight_cut_id="tight-001",
        transaction_id="tx-001",
        preview_path=preview,
        subtitle_path=subtitle,
    )

    transactions.commit()
    release = lifecycle.seal_candidate(candidate)

    assert isinstance(release, FinishedCutRelease)
    assert release.transaction_receipt_id == "receipt-001"
    assert release.rollback_ref == "backup-001"
    assert release.materialization_plan_id == "plan-001"
    assert release.events == candidate.materialization_plan.events
    assert release.preview == candidate.preview
    with pytest.raises(FrozenInstanceError):
        release.release_id = "forged-release"


def test_seal_rejects_artifact_changed_after_candidate(tmp_path: Path) -> None:
    episode_root = tmp_path / "episode-001"
    preview, subtitle = _write_artifacts(episode_root)
    transactions = _FakeTimelineTransactions("tx-001", "preview_ready")
    lifecycle = FinishedCutReleaseLifecycle(
        episode_root,
        transactions=transactions,
        preview_probe=_preview_probe,
    )
    candidate = lifecycle.stage_candidate(
        _materialization_plan(),
        editorial_master_id="master-001",
        winner_id="winner-001",
        tight_cut_id="tight-001",
        transaction_id="tx-001",
        preview_path=preview,
        subtitle_path=subtitle,
    )
    preview.write_bytes(b"changed after preview_ready")
    transactions.commit()

    with pytest.raises(ReleaseLifecycleError, match="changed after Candidate"):
        lifecycle.seal_candidate(candidate)


def test_manifest_v3_rejects_unsealed_candidate(tmp_path: Path) -> None:
    episode_root = tmp_path / "episode-001"
    preview, subtitle = _write_artifacts(episode_root)
    transactions = _FakeTimelineTransactions("tx-001", "preview_ready")
    lifecycle = FinishedCutReleaseLifecycle(
        episode_root,
        transactions=transactions,
        preview_probe=_preview_probe,
    )
    candidate = lifecycle.stage_candidate(
        _materialization_plan(),
        editorial_master_id="master-001",
        winner_id="winner-001",
        tight_cut_id="tight-001",
        transaction_id="tx-001",
        preview_path=preview,
        subtitle_path=subtitle,
    )

    with pytest.raises(ReleaseLifecycleError, match="sealed FinishedCutRelease"):
        lifecycle.publish_current((candidate,))  # type: ignore[arg-type]

    assert not (
        episode_root / "highlights" / "review" / "finished_review_manifest_current.json"
    ).exists()


def test_seal_persists_release_v1_receipt(tmp_path: Path) -> None:
    episode_root = tmp_path / "episode-001"
    preview, subtitle = _write_artifacts(episode_root)
    transactions = _FakeTimelineTransactions("tx-001", "preview_ready")
    lifecycle = FinishedCutReleaseLifecycle(
        episode_root,
        transactions=transactions,
        preview_probe=_preview_probe,
    )
    candidate = lifecycle.stage_candidate(
        _materialization_plan(),
        editorial_master_id="master-001",
        winner_id="winner-001",
        tight_cut_id="tight-001",
        transaction_id="tx-001",
        preview_path=preview,
        subtitle_path=subtitle,
    )
    transactions.commit()

    release = lifecycle.seal_candidate(candidate)

    receipt_path = episode_root / "highlights" / "releases" / "v1" / f"{release.release_id}.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["schema"] == "nakama.finished_cut_release.v1"
    assert receipt["release"]["release_id"] == release.release_id
    assert (
        receipt["content_hash"]
        == hashlib.sha256(
            json.dumps(
                {"schema": receipt["schema"], "release": receipt["release"]},
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    )


def test_manifest_v3_indexes_only_persisted_sealed_releases(tmp_path: Path) -> None:
    episode_root = tmp_path / "episode-001"
    preview, subtitle = _write_artifacts(episode_root)
    transactions = _FakeTimelineTransactions("tx-001", "preview_ready")
    lifecycle = FinishedCutReleaseLifecycle(
        episode_root,
        transactions=transactions,
        preview_probe=_preview_probe,
    )
    candidate = lifecycle.stage_candidate(
        _materialization_plan(),
        editorial_master_id="master-001",
        winner_id="winner-001",
        tight_cut_id="tight-001",
        transaction_id="tx-001",
        preview_path=preview,
        subtitle_path=subtitle,
    )
    transactions.commit()
    release = lifecycle.seal_candidate(candidate)

    current_path = lifecycle.publish_current((release,))

    expected_current = (
        episode_root / "highlights" / "review" / "finished_review_manifest_current.json"
    )
    assert current_path == expected_current
    manifest = json.loads(current_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "nakama.finished_cut_review_manifest.v3"
    assert manifest["episode_id"] == "episode-001"
    assert manifest["releases"] == [
        {
            "cut_id": "value-L01",
            "format": "long",
            "release_id": release.release_id,
            "release_ref": f"highlights/releases/v1/{release.release_id}.json",
            "release_sha256": hashlib.sha256(
                (
                    episode_root / "highlights" / "releases" / "v1" / f"{release.release_id}.json"
                ).read_bytes()
            ).hexdigest(),
        }
    ]


def test_pointer_last_failure_preserves_exact_current_bytes(tmp_path: Path) -> None:
    episode_root = tmp_path / "episode-001"
    preview, subtitle = _write_artifacts(episode_root)
    transactions = _FakeTimelineTransactions("tx-001", "preview_ready")
    lifecycle = FinishedCutReleaseLifecycle(
        episode_root,
        transactions=transactions,
        preview_probe=_preview_probe,
    )
    candidate = lifecycle.stage_candidate(
        _materialization_plan(),
        editorial_master_id="master-001",
        winner_id="winner-001",
        tight_cut_id="tight-001",
        transaction_id="tx-001",
        preview_path=preview,
        subtitle_path=subtitle,
    )
    transactions.commit()
    first_release = lifecycle.seal_candidate(candidate)
    current_path = lifecycle.publish_current((first_release,))
    original_current = current_path.read_bytes()

    transactions.commit(
        transaction_receipt_id="receipt-002",
        rollback_ref="backup-002",
    )
    second_release = lifecycle.seal_candidate(candidate)
    pointer = _FailingCurrentPointer()
    failing_lifecycle = FinishedCutReleaseLifecycle(
        episode_root,
        transactions=transactions,
        preview_probe=_preview_probe,
        pointer_writer=pointer,
    )

    with pytest.raises(ReleaseLifecycleError, match="current pointer"):
        failing_lifecycle.publish_current((second_release,))

    assert pointer.called
    assert current_path.read_bytes() == original_current
    assert (
        len(list((episode_root / "highlights" / "releases" / "index" / "v3").glob("*.json"))) == 2
    )


def test_inspect_current_returns_read_only_exact_release_without_writes(
    tmp_path: Path,
) -> None:
    episode_root = tmp_path / "episode-001"
    preview, subtitle = _write_artifacts(episode_root)
    transactions = _FakeTimelineTransactions("tx-001", "preview_ready")
    lifecycle = FinishedCutReleaseLifecycle(
        episode_root,
        transactions=transactions,
        preview_probe=_preview_probe,
    )
    candidate = lifecycle.stage_candidate(
        _materialization_plan(),
        editorial_master_id="master-001",
        winner_id="winner-001",
        tight_cut_id="tight-001",
        transaction_id="tx-001",
        preview_path=preview,
        subtitle_path=subtitle,
    )
    transactions.commit()
    release = lifecycle.seal_candidate(candidate)
    lifecycle.publish_current((release,))
    before = {
        path.relative_to(episode_root).as_posix(): path.read_bytes()
        for path in episode_root.rglob("*")
        if path.is_file()
    }
    fresh_reader = FinishedCutReleaseLifecycle(
        episode_root,
        transactions=transactions,
        preview_probe=_preview_probe,
    )

    inspected = fresh_reader.inspect_current("episode-001")

    after = {
        path.relative_to(episode_root).as_posix(): path.read_bytes()
        for path in episode_root.rglob("*")
        if path.is_file()
    }
    assert inspected == (release,)
    assert isinstance(inspected, tuple)
    assert after == before


def test_release_restart_keeps_semantic_event_and_four_second_component_distinct(
    tmp_path: Path,
) -> None:
    episode_root = tmp_path / "episode-001"
    preview, subtitle = _write_artifacts(episode_root)
    transactions = _FakeTimelineTransactions("tx-001", "preview_ready")
    lifecycle = FinishedCutReleaseLifecycle(
        episode_root,
        transactions=transactions,
        preview_probe=lambda _path: {"duration_sec": 60.0},
    )
    candidate = lifecycle.stage_candidate(
        _semantic_vs_placement_plan(),
        editorial_master_id="master-001",
        winner_id="winner-001",
        tight_cut_id="tight-001",
        transaction_id="tx-001",
        preview_path=preview,
        subtitle_path=subtitle,
    )
    transactions.commit()
    release = lifecycle.seal_candidate(candidate)
    lifecycle.publish_current((release,))

    inspected = FinishedCutReleaseLifecycle(
        episode_root,
        transactions=transactions,
        preview_probe=lambda _path: {"duration_sec": 60.0},
    ).inspect_current("episode-001")
    receipt = json.loads(
        (episode_root / "highlights" / "releases" / "v1" / f"{release.release_id}.json").read_text(
            encoding="utf-8"
        )
    )

    assert (inspected[0].events[0].t0, inspected[0].events[0].t1) == (5.0, 50.0)
    assert (inspected[0].components[0].t0, inspected[0].components[0].t1) == (46.0, 50.0)
    assert inspected[0].events[0].asset_ref is None
    assert inspected[0].components[0].asset_ref == "asset:rendered-support-title"
    assert "visual_placement" not in receipt["release"]["events"][0]
    assert receipt["release"]["components"][0]["t0"] == 46.0
    assert receipt["release"]["components"][0]["t1"] == 50.0


def test_release_restart_rejects_extra_semantic_event_field_even_with_rehashed_envelopes(
    tmp_path: Path,
) -> None:
    episode_root = tmp_path / "episode-001"
    preview, subtitle = _write_artifacts(episode_root)
    transactions = _FakeTimelineTransactions("tx-001", "preview_ready")
    lifecycle = FinishedCutReleaseLifecycle(
        episode_root,
        transactions=transactions,
        preview_probe=_preview_probe,
    )
    candidate = lifecycle.stage_candidate(
        _materialization_plan(),
        editorial_master_id="master-001",
        winner_id="winner-001",
        tight_cut_id="tight-001",
        transaction_id="tx-001",
        preview_path=preview,
        subtitle_path=subtitle,
    )
    transactions.commit()
    release = lifecycle.seal_candidate(candidate)
    current_path = lifecycle.publish_current((release,))
    receipt_path = episode_root / "highlights" / "releases" / "v1" / f"{release.release_id}.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["release"]["events"][0]["forged_placement"] = {"t0": 0.0, "t1": 2.0}
    receipt_body = {"schema": receipt["schema"], "release": receipt["release"]}
    receipt["content_hash"] = hashlib.sha256(
        json.dumps(
            receipt_body,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    receipt_bytes = (
        json.dumps(
            receipt,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    receipt_path.write_bytes(receipt_bytes)
    current = json.loads(current_path.read_text(encoding="utf-8"))
    current["releases"][0]["release_sha256"] = hashlib.sha256(receipt_bytes).hexdigest()
    current_path.write_text(
        json.dumps(
            current,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ReleaseLifecycleError, match="event fields are invalid"):
        lifecycle.inspect_current("episode-001")


def test_inspect_missing_current_never_falls_back_to_historical_or_archive(
    tmp_path: Path,
) -> None:
    episode_root = tmp_path / "episode-001"
    preview, subtitle = _write_artifacts(episode_root)
    transactions = _FakeTimelineTransactions("tx-001", "preview_ready")
    lifecycle = FinishedCutReleaseLifecycle(
        episode_root,
        transactions=transactions,
        preview_probe=_preview_probe,
    )
    candidate = lifecycle.stage_candidate(
        _materialization_plan(),
        editorial_master_id="master-001",
        winner_id="winner-001",
        tight_cut_id="tight-001",
        transaction_id="tx-001",
        preview_path=preview,
        subtitle_path=subtitle,
    )
    transactions.commit()
    release = lifecycle.seal_candidate(candidate)
    current_path = lifecycle.publish_current((release,))
    historical_path = current_path.with_name("finished_review_manifest_20991231.json")
    current_path.replace(historical_path)
    historical_bytes = historical_path.read_bytes()
    archive_sentinel = current_path.with_name("finished_review_manifest_archive.json")
    archive_sentinel.write_bytes(historical_bytes)

    with pytest.raises(ReleaseLifecycleError, match="exact current manifest is missing"):
        lifecycle.inspect_current("episode-001")

    assert historical_path.read_bytes() == historical_bytes
    assert archive_sentinel.read_bytes() == historical_bytes
    assert not current_path.exists()


def test_projected_components_round_trip_candidate_release_and_exact_current(
    tmp_path: Path,
) -> None:
    episode_root = tmp_path / "episode-001"
    preview, subtitle = _write_artifacts(episode_root)
    transactions = _FakeTimelineTransactions("tx-001", "preview_ready")
    lifecycle = FinishedCutReleaseLifecycle(
        episode_root,
        transactions=transactions,
        preview_probe=_preview_probe,
    )
    plan = _materialization_plan(with_components=True)

    candidate = lifecycle.stage_candidate(
        plan,
        editorial_master_id="master-001",
        winner_id="winner-001",
        tight_cut_id="tight-001",
        transaction_id="tx-001",
        preview_path=preview,
        subtitle_path=subtitle,
    )
    transactions.commit()
    release = lifecycle.seal_candidate(candidate)
    lifecycle.publish_current((release,))
    inspected = lifecycle.inspect_current("episode-001")

    assert candidate.components == plan.components
    assert release.components == plan.components
    assert inspected[0].components == plan.components
    assert inspected[0].components[0].semantic_kind == "hero_title"
    assert inspected[0].components[0].implementation_kind == "hero_title"
    assert inspected[0].components[0].lane == "hero_title"


def test_historical_supporting_title_receipt_remains_read_only_compatible() -> None:
    artifact = ReleaseArtifact(path="historical", bytes=1, sha256="a" * 64)
    event = EventRecord(
        event_id="historical-support",
        master_cue_ids=("cue-old",),
        text_hash=hashlib.sha256(b"old support").hexdigest(),
        intent="historical only",
        visual_status="approved",
        semantic_kind="supporting_title",
        implementation_kind="supporting_title",
        lane="supporting_title",  # type: ignore[arg-type]
    )
    component = _rehydrate_release_projected_component(
        component_id="historical-support-component",
        event_id=event.event_id,
        semantic_kind="supporting_title",
        implementation_kind="supporting_title",
        lane="supporting_title",  # type: ignore[arg-type]
        display="歷史補充字卡",
        t0=1.0,
        t1=4.0,
        asset_ref="asset-sha256:" + "b" * 64,
    )
    historical = _rehydrate_finished_cut_release(
        release_id="release-historical-support",
        episode_id="episode-001",
        cut_id="value-L04",
        format="long",
        command_id="approved-cut-historical",
        run_id="run-historical",
        editorial_master_id="master-historical",
        winner_id="winner-historical",
        tight_cut_id="tight-historical",
        director_acceptance_id="director-historical",
        dp_acceptance_id="dp-historical",
        visual_acceptance_id="visual-historical",
        materialization_plan_id="plan-historical",
        events=(event,),
        preview=artifact,
        subtitle=artifact,
        transaction_receipt_id="receipt-historical",
        rollback_ref="rollback-historical",
        components=(component,),
    )

    reopened = _release_from_receipt(_release_receipt_bytes(historical))

    assert reopened.events[0].semantic_kind == "supporting_title"
    assert reopened.components[0].lane == "supporting_title"


def test_candidate_rejects_component_timing_outside_preview_duration(tmp_path: Path) -> None:
    episode_root = tmp_path / "episode-001"
    preview, subtitle = _write_artifacts(episode_root)
    transactions = _FakeTimelineTransactions("tx-001", "preview_ready")
    lifecycle = FinishedCutReleaseLifecycle(
        episode_root,
        transactions=transactions,
        preview_probe=lambda _path: {"duration_sec": 1.0},
    )

    with pytest.raises(ReleaseLifecycleError, match="preview duration"):
        lifecycle.stage_candidate(
            _materialization_plan(with_components=True),
            editorial_master_id="master-001",
            winner_id="winner-001",
            tight_cut_id="tight-001",
            transaction_id="tx-001",
            preview_path=preview,
            subtitle_path=subtitle,
        )
