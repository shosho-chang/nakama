"""SEOPress REST API payload & response schemas (ADR-005b §3 / §9).

Bound to SEOPress plugin v9.4.1. If the plugin upgrades and the REST
contract changes, `extra="forbid"` will raise SEOPressSchemaDriftError
immediately instead of silently writing bad SEO metadata.

Plugin version:  SEOPress 9.4.1
WordPress:       6.9.4

Fallback A meta keys are defined as `SEOPRESS_META_KEYS_V941` — these are
the raw postmeta keys that SEOPress reads from the DB when the REST path is
unavailable. Verified against SEOPress 9.4.1 source code (inc/admin/metaboxes/).

Usage:
    from shared.schemas.external.seopress import (
        SEOpressWritePayloadV1,
        SEOpressReadResponseV1,
        SEOPRESS_META_KEYS_V941,
        SEOPressSchemaDriftError,
    )
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# SEOPress 9.4.1 raw postmeta keys (Fallback A path, ADR-005b §3).
# If SEOPress renames these in a future version, the Fallback A write will
# silently store data the plugin can't read — update this dict and cut a V2.
SEOPRESS_META_KEYS_V941: dict[str, str] = {
    "title": "_seopress_titles_title",
    "description": "_seopress_titles_desc",
    "focus_keyword": "_seopress_analysis_target_kw",
    "canonical": "_seopress_robots_canonical",
}

# REST endpoint path fragment (relative to WP REST base).
SEOPRESS_REST_NAMESPACE_V941 = "seopress/v1"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SEOPressSchemaDriftError(ValueError):
    """SEOPress REST response shape doesn't match SEOpressReadResponseV1.

    Raised when `extra="forbid"` detects unexpected fields — signals that the
    plugin was upgraded with breaking API changes. Triggers Fallback A.
    """


# ---------------------------------------------------------------------------
# Write payload (Nakama → SEOPress)
# ---------------------------------------------------------------------------


class SEOpressWritePayloadV1(BaseModel):
    """Payload sent to POST /wp-json/seopress/v1/posts/{post_id}.

    Only the four SEO fields Nakama manages are included. Additional fields
    (og_title, twitter_card, etc.) are intentionally excluded from Phase 1.

    Source: SEOPress 9.4.1 REST route registration in
    seopress/inc/admin/metaboxes/api.php
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Maps 1:1 to SEOPRESS_META_KEYS_V941 field names
    title: str = Field(
        description="<title> tag override; empty string = SEOPress generates from post title",
        max_length=70,
    )
    description: str = Field(
        description="Meta description",
        max_length=160,
    )
    focus_keyword: str = Field(
        description="Primary focus keyword for on-page analysis",
        max_length=100,
    )
    canonical: str = Field(
        default="",
        description="Canonical URL override; empty string = self-referencing",
        max_length=2048,
    )

    @model_validator(mode="after")
    def _non_empty_required(self) -> "SEOpressWritePayloadV1":
        if not self.title.strip():
            raise ValueError("SEOPress title must not be blank")
        if not self.description.strip():
            raise ValueError("SEOPress description must not be blank")
        if not self.focus_keyword.strip():
            raise ValueError("SEOPress focus_keyword must not be blank")
        return self


# ---------------------------------------------------------------------------
# Read response (SEOPress → Nakama, anti-corruption)
# ---------------------------------------------------------------------------


class SEOpressReadResponseV1(BaseModel):
    """Response from GET /wp-json/seopress/v1/posts/{post_id}.

    extra="forbid" ensures any new field SEOPress adds in a future version
    raises SEOPressSchemaDriftError (caught by wordpress_client) rather than
    being silently ignored.

    Note: SEOPress REST responses wrap metadata in a flat dict. The exact
    shape was recorded from SEOPress 9.4.1 staging integration test.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Core SEO fields (mirrors write payload)
    title: str = ""
    description: str = ""
    focus_keyword: str = ""
    canonical: str = ""

    # Read-only analysis fields returned by SEOPress
    score: int | None = None  # 0-100 on-page SEO score
    noindex: bool = False
    nofollow: bool = False


def parse_seopress_response(raw: dict[str, Any]) -> SEOpressReadResponseV1:
    """Parse raw SEOPress REST response; raise SEOPressSchemaDriftError on drift.

    This is the sole entry point for parsing SEOPress API responses.
    Never call SEOpressReadResponseV1.model_validate() directly — always use
    this function so the exception type is consistent.
    """
    try:
        return SEOpressReadResponseV1.model_validate(raw)
    except Exception as exc:
        raise SEOPressSchemaDriftError(
            f"SEOPress response shape mismatch (plugin upgraded?): {exc}"
        ) from exc
