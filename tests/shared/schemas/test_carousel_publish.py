from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from shared.schemas.carousel_publish import (
    CarouselPublishJobV1,
    CarouselPublishPlatformResult,
    CarouselPublishTarget,
)

NOW = datetime(2026, 8, 19, tzinfo=UTC)


def _legacy_job_payload() -> dict:
    return {
        "job_id": f"pj-{'a' * 32}",
        "episode_id": "episode-alpha",
        "source_revision": "r026",
        "source_manifest_sha256": "b" * 64,
        "approval_revision_number": 1,
        "approved_at": NOW.isoformat(),
        "request_fingerprint": "c" * 64,
        "caption": "Approved caption",
        "assets": [
            {
                "page_id": "cover",
                "page_number": 1,
                "image": {"path": "C:/fixture/01.png", "bytes": 1, "sha256": "d" * 64},
            }
        ],
        "targets": [
            {
                "platform": "instagram",
                "strategy": "agent_browser",
                "configuration_state": "agent_browser_required",
                "required_executor_capabilities": ["browser_session"],
                "note": "Browser handoff",
            }
        ],
        "status": "queued",
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
    }


def test_youtube_community_rejects_api_publish_strategy() -> None:
    with pytest.raises(ValidationError, match="no supported publish API"):
        CarouselPublishTarget(
            platform="youtube_community",
            strategy="meta_api",
            configuration_state="configured",
            required_executor_capabilities=["meta_api"],
            note="Incorrect API claim",
        )


def test_strategy_capability_contract_fails_closed() -> None:
    with pytest.raises(ValidationError, match="capabilities do not match"):
        CarouselPublishTarget(
            platform="instagram",
            strategy="agent_browser",
            configuration_state="agent_browser_required",
            required_executor_capabilities=["meta_api"],
            note="Mismatched executor capability",
        )


def test_platform_result_requires_receipt_permalink_or_error() -> None:
    with pytest.raises(ValidationError, match="requires a receipt or permalink"):
        CarouselPublishPlatformResult(
            platform="instagram",
            strategy="agent_browser",
            status="published",
            completed_at=NOW,
        )
    with pytest.raises(ValidationError, match="requires only an error"):
        CarouselPublishPlatformResult(
            platform="instagram",
            strategy="agent_browser",
            status="failed",
            receipt_id="ambiguous-receipt",
            error="publish failed",
            completed_at=NOW,
        )


def test_publish_contract_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CarouselPublishTarget.model_validate(
            {
                "platform": "facebook_page",
                "strategy": "agent_browser",
                "configuration_state": "agent_browser_required",
                "required_executor_capabilities": ["browser_session"],
                "note": "Browser path",
                "access_token": "must-never-be-stored",
            }
        )


def test_legacy_carousel_publish_job_without_campaign_anchor_remains_readable() -> None:
    job = CarouselPublishJobV1.model_validate(_legacy_job_payload())

    assert job.campaign_anchor_at is None


def test_carousel_campaign_anchor_must_be_timezone_aware() -> None:
    payload = _legacy_job_payload()
    payload["campaign_anchor_at"] = "2026-08-25T09:00:00"

    with pytest.raises(ValidationError, match="timezone-aware"):
        CarouselPublishJobV1.model_validate(payload)
