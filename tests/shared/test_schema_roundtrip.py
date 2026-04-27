"""V1 schema round-trip test (Phase 6 Slice 3).

Locks the **publish-path** Pydantic V1 contracts: every model survives
``model_dump_json()`` → ``model_validate_json()`` byte-for-byte field equality,
and the same for ``model_dump()`` / ``model_validate()`` dict mode. Catches
serializer bugs (custom ``model_serializer`` over-stripping fields), validator
asymmetry (``model_validator(mode="after")`` raising on dump output), and
``Optional[None]`` / nested model / ``AwareDatetime`` corner cases.

10 critical-path schemas covered (per Phase 6 task prompt §Slice 3):

- ``shared/schemas/publishing.py``: DraftV1 / DraftComplianceV1 / GutenbergHTMLV1
  / PublishRequestV1 / PublishResultV1 / PublishComplianceGateV1 / SEOContextV1
- ``shared/schemas/approval.py``: PublishWpPostV1 / UpdateWpPostV1
- ``shared/schemas/external/wordpress.py``: WpPostV1 (anti-corruption layer)

Other schemas (franky / kb / external/seopress / site_mapping) are out of scope
for this slice — re-add when those become publish-path critical.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

import pytest
from pydantic import BaseModel

from shared import gutenberg_builder
from shared.schemas.approval import PublishWpPostV1, UpdateWpPostV1
from shared.schemas.external.wordpress import WpPostV1, WpRenderedFieldV1
from shared.schemas.publishing import (
    BlockNodeV1,
    CannibalizationWarningV1,
    DraftComplianceV1,
    DraftV1,
    GutenbergHTMLV1,
    KeywordMetricV1,
    PublishComplianceGateV1,
    PublishRequestV1,
    PublishResultV1,
    SEOContextV1,
    StrikingDistanceV1,
)

# ---------------------------------------------------------------------------
# Factories — minimal-but-valid instance per schema
# ---------------------------------------------------------------------------


def _gutenberg() -> GutenbergHTMLV1:
    ast = [BlockNodeV1(block_type="paragraph", content="round-trip body")]
    return gutenberg_builder.build(ast)


def _draft_compliance() -> DraftComplianceV1:
    return DraftComplianceV1(
        schema_version=1,
        claims_no_therapeutic_effect=True,
        has_disclaimer=False,
        detected_blacklist_hits=[],
    )


def _draft() -> DraftV1:
    return DraftV1(
        schema_version=1,
        draft_id="draft_20260427T120000_a1b2c3",
        created_at=datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc),
        agent="brook",
        operation_id="op_a1b2c3d4",
        title="Round-trip schema test article",
        slug_candidates=["roundtrip-test"],
        content=_gutenberg(),
        excerpt="An excerpt of at least twenty characters present here.",
        primary_category="blog",
        secondary_categories=[],
        tags=[],
        focus_keyword="roundtrip",
        meta_description=(
            "A meta description that is at least fifty chars long to pass validator."
        ),
        compliance=_draft_compliance(),
        style_profile_id="blog@0.1.0",
    )


def _compliance_gate() -> PublishComplianceGateV1:
    return PublishComplianceGateV1(
        schema_version=1,
        medical_claim=False,
        absolute_assertion=False,
        matched_terms=[],
    )


def _publish_request() -> PublishRequestV1:
    return PublishRequestV1(
        schema_version=1,
        draft=_draft(),
        action="publish",
        scheduled_at=None,
        featured_media_id=42,
        reviewer="U_TEST_SHOSHO",
    )


def _publish_result() -> PublishResultV1:
    return PublishResultV1(
        schema_version=1,
        status="published",
        post_id=101,
        permalink="https://example.com/p/101",
        seo_status="written",
        cache_purged=True,
        failure_reason=None,
        operation_id="op_a1b2c3d4",
        completed_at=datetime(2026, 4, 27, 12, 5, 0, tzinfo=timezone.utc),
    )


def _seo_context() -> SEOContextV1:
    return SEOContextV1(
        schema_version=1,
        target_site="wp_shosho",
        primary_keyword=KeywordMetricV1(
            schema_version=1,
            keyword="sleep optimization",
            clicks=120,
            impressions=4500,
            ctr=0.0267,
            avg_position=8.4,
            source="gsc",
        ),
        related_keywords=[],
        striking_distance=[
            StrikingDistanceV1(
                schema_version=1,
                keyword="sleep cycle stages",
                url="https://example.com/sleep-cycle",
                current_position=14.2,
                impressions_last_28d=380,
                suggested_actions=["expand intro section"],
            ),
        ],
        cannibalization_warnings=[
            CannibalizationWarningV1(
                schema_version=1,
                keyword="sleep aid",
                competing_urls=[
                    "https://example.com/melatonin",
                    "https://example.com/herbal-sleep",
                ],
                severity="medium",
                recommendation="merge or de-target one URL",
            ),
        ],
        competitor_serp_summary=None,
        generated_at=datetime(2026, 4, 27, 11, 0, 0, tzinfo=timezone.utc),
        source_keyword_research_path=None,
    )


def _publish_wp_post() -> PublishWpPostV1:
    return PublishWpPostV1(
        schema_version=1,
        action_type="publish_post",
        target_site="wp_shosho",
        draft=_draft(),
        compliance_flags=_compliance_gate(),
        reviewer_compliance_ack=False,
        scheduled_at=None,
    )


def _update_wp_post() -> UpdateWpPostV1:
    return UpdateWpPostV1(
        schema_version=1,
        action_type="update_post",
        target_site="wp_shosho",
        wp_post_id=2048,
        patch={"title": "Updated title", "excerpt": "Refreshed excerpt"},
        change_summary="Refresh stale 2024 excerpt; clarify hook",
        draft_id="draft_20260427T120000_a1b2c3",
        compliance_flags=_compliance_gate(),
        reviewer_compliance_ack=True,
    )


def _wp_post() -> WpPostV1:
    return WpPostV1(
        id=2048,
        date="2026-04-27T12:00:00",
        date_gmt="2026-04-27T04:00:00",
        guid=WpRenderedFieldV1(rendered="https://example.com/?p=2048"),
        modified="2026-04-27T12:05:00",
        modified_gmt="2026-04-27T04:05:00",
        slug="example-post",
        status="publish",
        type="post",
        link="https://example.com/p/2048",
        title=WpRenderedFieldV1(rendered="Example post"),
        content=WpRenderedFieldV1(rendered="<p>Body</p>", protected=False),
        excerpt=WpRenderedFieldV1(rendered="<p>Excerpt</p>"),
        author=1,
        featured_media=99,
        comment_status="open",
        ping_status="open",
        sticky=False,
        template="",
        format="standard",
        meta={"_seopress_titles_title": "SEO override"},
        categories=[5, 12],
        tags=[33, 88],
    )


# ---------------------------------------------------------------------------
# Parametrized round-trip
# ---------------------------------------------------------------------------


_FACTORIES: list[tuple[str, Callable[[], BaseModel]]] = [
    ("DraftV1", _draft),
    ("DraftComplianceV1", _draft_compliance),
    ("GutenbergHTMLV1", _gutenberg),
    ("PublishRequestV1", _publish_request),
    ("PublishResultV1", _publish_result),
    ("PublishComplianceGateV1", _compliance_gate),
    ("SEOContextV1", _seo_context),
    ("PublishWpPostV1", _publish_wp_post),
    ("UpdateWpPostV1", _update_wp_post),
    ("WpPostV1", _wp_post),
]


@pytest.mark.parametrize("factory", [pytest.param(f, id=name) for name, f in _FACTORIES])
def test_schema_json_roundtrip(factory: Callable[[], BaseModel]) -> None:
    """``model_dump_json()`` → ``model_validate_json()`` produces an equal instance."""
    original = factory()
    json_str = original.model_dump_json()
    reborn = type(original).model_validate_json(json_str)
    assert reborn == original, f"JSON round-trip drift: {original!r} vs {reborn!r}"


@pytest.mark.parametrize("factory", [pytest.param(f, id=name) for name, f in _FACTORIES])
def test_schema_dict_roundtrip(factory: Callable[[], BaseModel]) -> None:
    """``model_dump()`` → ``model_validate()`` produces an equal instance.

    Catches serialization bugs that JSON serialization would mask via implicit
    type coercion (e.g. datetime → ISO string round-trip is forgiving; dict
    round-trip is strict).
    """
    original = factory()
    dump = original.model_dump()
    reborn = type(original).model_validate(dump)
    assert reborn == original, f"dict round-trip drift: {original!r} vs {reborn!r}"


@pytest.mark.parametrize("factory", [pytest.param(f, id=name) for name, f in _FACTORIES])
def test_schema_dump_json_is_string(factory: Callable[[], BaseModel]) -> None:
    """``model_dump_json()`` returns ``str`` (not ``bytes``) — many call sites
    pass the output straight into ``json.loads`` / SQLite TEXT columns."""
    original = factory()
    out = original.model_dump_json()
    assert isinstance(out, str)
    assert len(out) > 0
