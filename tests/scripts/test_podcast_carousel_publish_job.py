from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.podcast_carousel_publish_job import (
    PublishJobTransitionError,
    claim_publish_job,
    complete_publish_job,
    create_or_get_publish_job,
    fail_publish_job,
    list_publish_jobs,
    load_publish_job,
    progress_publish_job,
    publish_job_path,
)
from shared.schemas.carousel_publish import (
    CarouselPublishAsset,
    CarouselPublishPlatformResult,
    CarouselPublishTarget,
)
from shared.schemas.podcast_carousel import ArtifactReceipt

NOW = datetime(2020, 8, 19, 3, 0, tzinfo=UTC)
SHA = "a" * 64


def _asset() -> CarouselPublishAsset:
    return CarouselPublishAsset(
        page_id="cover",
        page_number=1,
        image=ArtifactReceipt(path="C:/fixture/01.png", bytes=123, sha256="b" * 64),
    )


def _browser_target(platform: str = "instagram") -> CarouselPublishTarget:
    if platform == "youtube_community":
        return CarouselPublishTarget(
            platform=platform,
            strategy="agent_browser_manual",
            configuration_state="manual_only",
            required_executor_capabilities=["browser_session"],
            note="No supported Community publish API.",
        )
    return CarouselPublishTarget(
        platform=platform,
        strategy="agent_browser",
        configuration_state="agent_browser_required",
        required_executor_capabilities=["browser_session"],
        note="Use an authenticated agent browser.",
    )


def _queued(package_root: Path, *, suffix: str = "1", targets=None):
    job, created = create_or_get_publish_job(
        package_root=package_root,
        episode_id="ep120",
        source_revision="r016",
        source_manifest_sha256=SHA,
        approval_revision_number=3,
        approved_at=NOW - timedelta(minutes=1),
        caption=f"Approved caption {suffix}",
        assets=[_asset()],
        targets=targets or [_browser_target()],
        now=NOW,
        job_id="pj-" + suffix * 32,
    )
    assert created is True
    return job


def test_create_is_idempotent_for_revision_caption_and_platform_set(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    first = _queued(
        package,
        targets=[_browser_target("youtube_community"), _browser_target("instagram")],
    )
    duplicate, created = create_or_get_publish_job(
        package_root=package,
        episode_id="ep120",
        source_revision="r016",
        source_manifest_sha256=SHA,
        approval_revision_number=3,
        approved_at=NOW - timedelta(minutes=1),
        caption="Approved caption 1",
        assets=[_asset()],
        targets=[_browser_target("instagram"), _browser_target("youtube_community")],
        now=NOW + timedelta(seconds=1),
        job_id="pj-" + "2" * 32,
    )

    assert created is False
    assert duplicate.job_id == first.job_id
    assert [job.job_id for job in list_publish_jobs(package)] == [first.job_id]


@pytest.mark.parametrize("executor", ["codex", "claude_code"])
def test_codex_and_claude_code_can_claim_with_required_capability(
    tmp_path: Path,
    executor: str,
):
    package = tmp_path / executor / "ig-carousel"
    queued = _queued(package)
    path = publish_job_path(package, queued.job_id)

    claimed = claim_publish_job(
        path,
        executor=executor,
        executor_id=f"{executor}-worker",
        executor_capabilities=["browser_session"],
        claim_token=f"claim-{executor}-0001",
        now=NOW + timedelta(seconds=1),
    )

    assert claimed.status == "claimed"
    assert claimed.claim is not None
    assert claimed.claim.executor == executor


def test_claim_rejects_missing_capability_and_active_lease(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    queued = _queued(package)
    path = publish_job_path(package, queued.job_id)

    with pytest.raises(PublishJobTransitionError, match="lacks required"):
        claim_publish_job(
            path,
            executor="codex",
            executor_id="worker-1",
            executor_capabilities=["meta_api"],
            now=NOW + timedelta(seconds=1),
        )
    claim_publish_job(
        path,
        executor="codex",
        executor_id="worker-1",
        executor_capabilities=["browser_session"],
        claim_token="claim-browser-0001",
        lease_seconds=60,
        now=NOW + timedelta(seconds=1),
    )
    with pytest.raises(PublishJobTransitionError, match="lease is still active"):
        claim_publish_job(
            path,
            executor="claude_code",
            executor_id="worker-2",
            executor_capabilities=["browser_session"],
            now=NOW + timedelta(seconds=30),
        )


def test_expired_lease_can_be_reclaimed_but_old_token_cannot_progress(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    queued = _queued(package)
    path = publish_job_path(package, queued.job_id)
    claim_publish_job(
        path,
        executor="codex",
        executor_id="worker-1",
        executor_capabilities=["browser_session"],
        claim_token="claim-browser-0001",
        lease_seconds=60,
        now=NOW + timedelta(seconds=1),
    )

    reclaimed = claim_publish_job(
        path,
        executor="claude_code",
        executor_id="worker-2",
        executor_capabilities=["browser_session"],
        claim_token="claim-browser-0002",
        now=NOW + timedelta(seconds=61),
    )

    assert reclaimed.claim is not None
    assert reclaimed.claim.executor == "claude_code"
    with pytest.raises(PublishJobTransitionError, match="claim token mismatch"):
        progress_publish_job(
            path,
            claim_token="claim-browser-0001",
            step="stale_worker",
            progress_percent=25,
            now=NOW + timedelta(seconds=62),
        )


def test_progress_and_completion_store_one_result_per_platform(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    targets = [_browser_target("instagram"), _browser_target("youtube_community")]
    queued = _queued(package, targets=targets)
    path = publish_job_path(package, queued.job_id)
    claim_publish_job(
        path,
        executor="codex",
        executor_id="worker-1",
        executor_capabilities=["browser_session"],
        claim_token="claim-browser-0001",
        now=NOW + timedelta(seconds=1),
    )
    active = progress_publish_job(
        path,
        claim_token="claim-browser-0001",
        step="open_platforms",
        progress_percent=50,
        message="Authenticated browser sessions ready",
        now=NOW + timedelta(seconds=2),
    )
    assert active.status == "in_progress"

    completed = complete_publish_job(
        path,
        claim_token="claim-browser-0001",
        results=[
            CarouselPublishPlatformResult(
                platform="youtube_community",
                strategy="agent_browser_manual",
                status="failed",
                error="human confirmation was not granted",
                completed_at=NOW + timedelta(seconds=3),
            ),
            CarouselPublishPlatformResult(
                platform="instagram",
                strategy="agent_browser",
                status="published",
                receipt_id="ig-receipt-120",
                permalink="https://www.instagram.com/p/example/",
                completed_at=NOW + timedelta(seconds=3),
            ),
        ],
        now=NOW + timedelta(seconds=4),
    )

    assert completed.status == "completed"
    assert [result.platform for result in completed.results] == [
        "instagram",
        "youtube_community",
    ]
    assert completed.results[0].permalink == "https://www.instagram.com/p/example/"
    assert completed.results[1].error == "human confirmation was not granted"


def test_invalid_terminal_transitions_fail_closed(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    queued = _queued(package)
    path = publish_job_path(package, queued.job_id)
    with pytest.raises(PublishJobTransitionError, match="cannot complete"):
        complete_publish_job(path, claim_token="claim-browser-0001", results=[], now=NOW)
    claim_publish_job(
        path,
        executor="claude_code",
        executor_id="worker-2",
        executor_capabilities=["browser_session"],
        claim_token="claim-browser-0002",
        now=NOW + timedelta(seconds=1),
    )
    failed = fail_publish_job(
        path,
        claim_token="claim-browser-0002",
        error="browser session unavailable",
        now=NOW + timedelta(seconds=2),
    )
    assert failed.status == "failed"
    assert load_publish_job(path).error == "browser session unavailable"


def test_cli_list_claim_progress_and_complete_only_mutate_local_json(tmp_path: Path):
    package = tmp_path / "ig-carousel"
    queued = _queued(package)
    path = publish_job_path(package, queued.job_id)
    script = Path("scripts/podcast_carousel_publish_job.py").resolve()
    result_path = tmp_path / "results.json"
    result_path.write_text(
        json.dumps(
            [
                {
                    "platform": "instagram",
                    "strategy": "agent_browser",
                    "status": "published",
                    "receipt_id": "browser-receipt-1",
                    "completed_at": (NOW + timedelta(seconds=3)).isoformat(),
                }
            ]
        ),
        encoding="utf-8",
    )
    commands = [
        ["list", str(package)],
        [
            "claim",
            str(path),
            "--executor",
            "codex",
            "--executor-id",
            "cli-worker",
            "--capability",
            "browser_session",
            "--claim-token",
            "claim-cli-0001",
        ],
        [
            "progress",
            str(path),
            "--claim-token",
            "claim-cli-0001",
            "--step",
            "browser_publish",
            "--percent",
            "100",
        ],
        [
            "complete",
            str(path),
            "--claim-token",
            "claim-cli-0001",
            "--results-json",
            str(result_path),
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
    assert load_publish_job(path).status == "completed"
    assert not (package / "published.json").exists()
