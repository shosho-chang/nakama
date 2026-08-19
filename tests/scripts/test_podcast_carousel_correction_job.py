from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.podcast_carousel_correction_job import (
    CorrectionJobTransitionError,
    claim_job,
    complete_job,
    correction_job_path,
    create_queued_job,
    fail_job,
    load_job,
    progress_job,
)
from shared.schemas.podcast_carousel import CarouselCorrectionItem

SHA = "a" * 64
NOW = datetime(2026, 8, 19, 1, 0, tzinfo=UTC)


def _queued(package_root: Path):
    return create_queued_job(
        package_root=package_root,
        episode_id="ep120",
        source_revision="r001",
        source_manifest_sha256=SHA,
        feedback_items=[
            CarouselCorrectionItem(
                page_id="cover",
                artifact_sha256="b" * 64,
                feedback="放大來賓",
            )
        ],
        now=NOW,
        job_id="cj-" + "1" * 32,
    )


def test_state_machine_prevents_double_claim_and_invalid_transitions(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    queued = _queued(package)
    path = correction_job_path(package, queued.job_id)

    with pytest.raises(CorrectionJobTransitionError, match="cannot complete"):
        complete_job(path, claim_token="claim-00000001", result_revision="r002", now=NOW)

    claimed = claim_job(
        path,
        executor="codex",
        executor_id="worker-1",
        claim_token="claim-00000001",
        now=NOW + timedelta(seconds=1),
    )
    assert claimed.status == "claimed"
    with pytest.raises(CorrectionJobTransitionError, match="cannot claim"):
        claim_job(
            path,
            executor="claude_code",
            executor_id="worker-2",
            claim_token="claim-00000002",
            now=NOW + timedelta(seconds=2),
        )

    active = progress_job(
        path,
        claim_token="claim-00000001",
        step="rewrite_copy",
        progress_percent=40,
        message="修正文案",
        now=NOW + timedelta(seconds=3),
    )
    assert active.status == "in_progress"
    assert active.progress[0].sequence == 1
    with pytest.raises(CorrectionJobTransitionError, match="cannot decrease"):
        progress_job(
            path,
            claim_token="claim-00000001",
            step="render",
            progress_percent=20,
            now=NOW + timedelta(seconds=4),
        )

    completed = complete_job(
        path,
        claim_token="claim-00000001",
        result_revision="r002",
        now=NOW + timedelta(seconds=5),
    )
    assert completed.status == "completed"
    assert completed.result_revision == "r002"
    with pytest.raises(CorrectionJobTransitionError, match="cannot record progress"):
        progress_job(
            path,
            claim_token="claim-00000001",
            step="late_write",
            progress_percent=100,
        )
    assert not list(path.parent.glob("*.lock"))
    assert not list(path.parent.glob("*.tmp"))


def test_expired_claim_can_be_reclaimed_and_invalidates_old_token(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    queued = _queued(package)
    path = correction_job_path(package, queued.job_id)

    first = claim_job(
        path,
        executor="codex",
        executor_id="worker-1",
        claim_token="claim-lease-0001",
        lease_seconds=60,
        now=NOW,
    )
    assert first.claim is not None
    assert first.claim.lease_expires_at == NOW + timedelta(seconds=60)

    with pytest.raises(CorrectionJobTransitionError, match="lease is still active"):
        claim_job(
            path,
            executor="claude_code",
            executor_id="worker-2",
            claim_token="claim-lease-0002",
            now=NOW + timedelta(seconds=59),
        )

    reclaimed = claim_job(
        path,
        executor="claude_code",
        executor_id="worker-2",
        claim_token="claim-lease-0002",
        lease_seconds=120,
        now=NOW + timedelta(seconds=60),
    )
    assert reclaimed.status == "claimed"
    assert reclaimed.claim is not None
    assert reclaimed.claim.executor_id == "worker-2"
    assert reclaimed.claim.lease_expires_at == NOW + timedelta(seconds=180)

    with pytest.raises(CorrectionJobTransitionError, match="claim token mismatch"):
        progress_job(
            path,
            claim_token="claim-lease-0001",
            step="stale_worker",
            progress_percent=10,
            now=NOW + timedelta(seconds=61),
        )


def test_progress_renews_lease_and_expired_worker_cannot_mutate(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    queued = _queued(package)
    path = correction_job_path(package, queued.job_id)
    claim_job(
        path,
        executor="codex",
        executor_id="worker-1",
        claim_token="claim-renew-0001",
        lease_seconds=60,
        now=NOW,
    )

    active = progress_job(
        path,
        claim_token="claim-renew-0001",
        step="rewrite",
        progress_percent=25,
        now=NOW + timedelta(seconds=30),
    )
    assert active.claim is not None
    assert active.claim.lease_expires_at == NOW + timedelta(seconds=90)

    with pytest.raises(CorrectionJobTransitionError, match="lease has expired"):
        progress_job(
            path,
            claim_token="claim-renew-0001",
            step="late_heartbeat",
            progress_percent=50,
            now=NOW + timedelta(seconds=90),
        )

    with pytest.raises(CorrectionJobTransitionError, match="lease has expired"):
        complete_job(
            path,
            claim_token="claim-renew-0001",
            result_revision="r002",
            now=NOW + timedelta(seconds=90),
        )

    reclaimed = claim_job(
        path,
        executor="claude_code",
        executor_id="worker-2",
        claim_token="claim-renew-0002",
        lease_seconds=60,
        now=NOW + timedelta(seconds=90),
    )
    assert reclaimed.status == "in_progress"
    assert len(reclaimed.progress) == 1
    assert reclaimed.claim is not None
    assert reclaimed.claim.claim_token == "claim-renew-0002"
    with pytest.raises(CorrectionJobTransitionError, match="claim token mismatch"):
        fail_job(
            path,
            claim_token="claim-renew-0001",
            error="stale worker",
            now=NOW + timedelta(seconds=91),
        )


def test_executor_metadata_is_platform_neutral_and_fail_closed(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    queued = _queued(package)
    path = correction_job_path(package, queued.job_id)

    with pytest.raises(ValidationError, match="executor"):
        claim_job(
            path,
            executor="anthropic_api",
            executor_id="provider-specific",
            claim_token="claim-00000003",
            now=NOW + timedelta(seconds=1),
        )
    assert load_job(path).status == "queued"

    claim_job(
        path,
        executor="claude_code",
        executor_id="desktop-worker",
        claim_token="claim-00000004",
        now=NOW + timedelta(seconds=2),
    )
    failed = fail_job(
        path,
        claim_token="claim-00000004",
        error="transcript evidence mismatch",
        now=NOW + timedelta(seconds=3),
    )
    assert failed.status == "failed"
    assert failed.error == "transcript evidence mismatch"
    with pytest.raises(CorrectionJobTransitionError, match="cannot fail"):
        fail_job(path, claim_token="claim-00000004", error="again")


def test_cli_claim_progress_complete_mutates_only_job_json(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    queued = _queued(package)
    path = correction_job_path(package, queued.job_id)
    script = Path("scripts/podcast_carousel_correction_job.py").resolve()

    commands = [
        [
            "claim",
            str(path),
            "--executor",
            "codex",
            "--executor-id",
            "afk-worker",
            "--claim-token",
            "claim-cli-0001",
        ],
        [
            "progress",
            str(path),
            "--claim-token",
            "claim-cli-0001",
            "--step",
            "copy_and_render",
            "--percent",
            "100",
        ],
        [
            "complete",
            str(path),
            "--claim-token",
            "claim-cli-0001",
            "--result-revision",
            "r002",
        ],
    ]
    for command in commands:
        completed = subprocess.run(
            [sys.executable, str(script), *command],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        json.loads(completed.stdout)

    stored = load_job(path)
    assert stored.status == "completed"
    assert stored.claim is not None
    assert stored.claim.executor == "codex"
    assert stored.result_revision == "r002"
