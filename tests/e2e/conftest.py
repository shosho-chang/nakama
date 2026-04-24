"""E2E fixtures — live Docker WP staging.

Tests here are marked `@pytest.mark.live_wp`. They auto-skip unless
`PYTEST_WP_BASE_URL` is set — typically sourced from `.env.test` that
`tests/fixtures/wp_staging/run.sh` generates.

The host-level ping inside `live_wp_client` fails fast so a broken stack
does not cascade into opaque mid-test errors.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

import httpx
import pytest

from shared import gutenberg_builder
from shared.schemas.publishing import (
    BlockNodeV1,
    DraftComplianceV1,
    DraftV1,
)
from shared.wordpress_client import WordPressClient


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("PYTEST_WP_BASE_URL"):
        return
    skip_marker = pytest.mark.skip(
        reason=(
            "PYTEST_WP_BASE_URL not set — boot Docker WP first: "
            "`bash tests/fixtures/wp_staging/run.sh` then `source .env.test`."
        )
    )
    for item in items:
        if "live_wp" in item.keywords:
            item.add_marker(skip_marker)


@pytest.fixture(scope="session")
def live_wp_client() -> WordPressClient:
    """WP client pointed at local Docker staging; pings REST root up front."""
    base = os.environ["PYTEST_WP_BASE_URL"]
    try:
        resp = httpx.get(f"{base}/wp-json/wp/v2/", timeout=5.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        pytest.fail(
            f"WP staging at {base} not reachable: {exc}. "
            "Run `bash tests/fixtures/wp_staging/run.sh` and retry."
        )
    return WordPressClient.from_env("wp_shosho")


@pytest.fixture
def test_draft_factory() -> Callable[..., DraftV1]:
    """Build a valid DraftV1 with unique draft_id + slug per invocation.

    primary_category defaults to `nutrition-science` — the seed.sh creates
    this category, so the publisher's `_get_category_map()` resolves it.
    """

    def _make(
        *,
        primary_category: str = "nutrition-science",
        title: str | None = None,
    ) -> DraftV1:
        token = uuid.uuid4().hex[:6]
        op_id = f"op_{uuid.uuid4().hex[:8]}"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        ast = [
            BlockNodeV1(
                block_type="paragraph",
                content="E2E smoke test post — safe to delete. Regular bedtime supports recovery.",
            )
        ]
        return DraftV1(
            draft_id=f"draft_{timestamp}_{token}",
            created_at=datetime.now(timezone.utc),
            agent="brook",
            operation_id=op_id,
            title=title or f"E2E smoke post {token}",
            slug_candidates=[f"e2e-smoke-{token}"],
            content=gutenberg_builder.build(ast),
            excerpt=("E2E smoke excerpt long enough to satisfy the 20-character schema validator."),
            primary_category=primary_category,  # type: ignore[arg-type]
            focus_keyword=f"e2e-{token}",
            meta_description=(
                "A meta description over fifty characters for the schema validator, safe to delete."
            ),
            compliance=DraftComplianceV1(
                schema_version=1,
                claims_no_therapeutic_effect=True,
                has_disclaimer=False,
            ),
            style_profile_id="blog@0.1.0",
        )

    return _make
