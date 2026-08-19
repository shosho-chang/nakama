from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from shared.schemas.carousel_publish import (
    CarouselPublishPlatformResult,
    CarouselPublishTarget,
)

NOW = datetime(2026, 8, 19, tzinfo=UTC)


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
