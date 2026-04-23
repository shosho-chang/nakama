"""WordPress REST API response anti-corruption schemas (ADR-005b §9).

Bound to WP 6.9.4 REST API responses. If WP upgrades break these shapes,
schema validation will fail fast rather than silently corrupt data.

Plugin versions:
    WordPress: 6.9.4
    SEOPress:  9.4.1 (see seopress.py)

All models use extra="forbid" + frozen=True per docs/principles/schemas.md §8.
AwareDatetime fields are kept as str (WP returns ISO8601 UTC strings) and
parsed on construction.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# WP REST: Post
# ---------------------------------------------------------------------------

WP_POST_STATUS = Literal["publish", "future", "draft", "pending", "private", "trash", "auto-draft"]
WP_POST_TYPE = Literal["post", "page", "attachment"]
WP_COMMENT_STATUS = Literal["open", "closed"]
WP_PING_STATUS = Literal["open", "closed"]


class WpRenderedFieldV1(BaseModel):
    """WP rendered content wrapper (e.g. title.rendered, content.rendered)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    rendered: str
    protected: bool = False


class WpPostV1(BaseModel):
    """WP REST /wp/v2/posts response (WP 6.9.4).

    Only fields consumed by wordpress_client.py are modelled. Any unexpected
    field in the response will raise ValidationError immediately (extra="forbid").
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int
    date: str  # local time ISO8601; prefer date_gmt
    date_gmt: str  # UTC ISO8601
    guid: WpRenderedFieldV1
    modified: str
    modified_gmt: str
    slug: str
    status: WP_POST_STATUS
    type: WP_POST_TYPE = "post"
    link: str
    title: WpRenderedFieldV1
    content: WpRenderedFieldV1
    excerpt: WpRenderedFieldV1
    author: int
    featured_media: int = 0
    comment_status: WP_COMMENT_STATUS = "open"
    ping_status: WP_PING_STATUS = "open"
    sticky: bool = False
    template: str = ""
    format: str = "standard"
    meta: dict[str, Any] = Field(default_factory=dict)
    categories: list[int] = Field(default_factory=list)
    tags: list[int] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# WP REST: Media (attachment)
# ---------------------------------------------------------------------------


class WpMediaDetailsV1(BaseModel):
    """Subset of WP media_details for image uploads."""

    model_config = ConfigDict(extra="ignore", frozen=True)  # allow unknown size keys

    width: int = 0
    height: int = 0
    file: str = ""


class WpMediaV1(BaseModel):
    """WP REST /wp/v2/media upload response (WP 6.9.4)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int
    date: str
    date_gmt: str
    guid: WpRenderedFieldV1
    modified: str
    modified_gmt: str
    slug: str
    status: Literal["inherit", "private", "trash"] = "inherit"
    type: Literal["attachment"] = "attachment"
    link: str
    title: WpRenderedFieldV1
    author: int
    comment_status: WP_COMMENT_STATUS = "open"
    ping_status: WP_PING_STATUS = "closed"
    template: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)
    description: WpRenderedFieldV1 = Field(default_factory=lambda: WpRenderedFieldV1(rendered=""))
    caption: WpRenderedFieldV1 = Field(default_factory=lambda: WpRenderedFieldV1(rendered=""))
    alt_text: str = ""
    media_type: Literal["image", "file"] = "image"
    mime_type: str = ""
    media_details: WpMediaDetailsV1 = Field(default_factory=WpMediaDetailsV1)
    post: int | None = None
    source_url: str = ""


# ---------------------------------------------------------------------------
# WP REST: Category / Tag
# ---------------------------------------------------------------------------


class WpTermV1(BaseModel):
    """WP REST /wp/v2/categories or /wp/v2/tags item."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int
    count: int = 0
    description: str = ""
    link: str = ""
    name: str
    slug: str
    taxonomy: Literal["category", "post_tag"]
    parent: int = 0
    meta: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# WP REST: Error response
# ---------------------------------------------------------------------------


class WpErrorV1(BaseModel):
    """Standard WP REST error envelope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
