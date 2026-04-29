"""SEO observability contracts (ADR-008 §6 + §2).

Schema for `config/target-keywords.yaml` (Zoro / Usopp / Franky 三方共用) and
the GSC row record consumed by `shared/gsc_rows_store.py`.

Shapes:
    TargetKeywordV1     — single row in the YAML list
    TargetKeywordListV1 — top-level YAML document (with updated_at + keywords)
    GSCRowV1            — one normalized GSC search-analytics row, persisted in
                          state.db `gsc_rows` table

All schemas follow `docs/principles/schemas.md`:
- `extra="forbid"` + `frozen=True`
- `schema_version` int literal on every persisted shape
- `AwareDatetime` (no naive datetime allowed)
- `Literal` for closed enums
"""

from __future__ import annotations

from datetime import date as date_type
from typing import Literal, Optional

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    confloat,
    constr,
)

# Sites tracked by Franky GSC observability. Aligned with the canonical
# `target-keywords.yaml` `site` field (ADR-008 §6). Mapping to GSC property
# strings (`sc-domain:shosho.tw`, `sc-domain:fleet.shosho.tw`) lives in
# the cron / store layer via env vars (`GSC_PROPERTY_SHOSHO` /
# `GSC_PROPERTY_FLEET`), per ADR-008 §8.
TargetSiteName = Literal["shosho.tw", "fleet.shosho.tw"]


# ---------------------------------------------------------------------------
# config/target-keywords.yaml — Zoro / Usopp / Franky 三方共用
# ---------------------------------------------------------------------------


class TargetKeywordV1(BaseModel):
    """One keyword Franky tracks in GSC + Zoro/Usopp may attach metadata to.

    Ownership rules (ADR-008 §6):
        - Zoro: add / remove / update goal_rank
        - Usopp: append-only (auto-add focus_keyword on publish)
        - Franky: read-only
        - 修修: any operation via CLI

    Language: `keyword` is 繁中 to match GSC zh-TW queries; `keyword_en`
    is optional bilingual annotation (won't hit GSC).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    keyword: constr(min_length=1, max_length=100)
    keyword_en: Optional[constr(max_length=100)] = None
    site: TargetSiteName
    added_by: Literal["zoro", "usopp", "shosho"]
    added_at: AwareDatetime
    goal_rank: Optional[PositiveInt] = None
    source_post_id: Optional[int] = None


class TargetKeywordListV1(BaseModel):
    """`config/target-keywords.yaml` top-level document."""

    # Not frozen — Zoro / Usopp need to mutate `.keywords` under filelock.
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    updated_at: AwareDatetime
    keywords: list[TargetKeywordV1] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# GSC row (state.db `gsc_rows` table) — ADR-008 §2
# ---------------------------------------------------------------------------


class GSCRowV1(BaseModel):
    """One GSC search-analytics row, normalized for state.db persistence.

    PRIMARY KEY `(site, date, query, page, country, device)` — UPSERT path
    re-writes existing rows (not append) so the daily 7-day window with
    overlap is idempotent (ADR-008 §2 / `reliability.md` §1).

    `site` is the GSC property string (`sc-domain:shosho.tw`), not the
    short site name (`shosho.tw`). The store / cron layers convert.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    site: constr(pattern=r"^sc-domain:[a-z0-9.\-]+$")
    date: date_type  # GSC reports per-day; aware-date (no TZ on a date is OK)
    query: constr(min_length=1, max_length=200)
    page: constr(min_length=1, max_length=2000)
    country: constr(min_length=2, max_length=3)
    device: Literal["desktop", "mobile", "tablet"]
    clicks: int = Field(ge=0)
    impressions: int = Field(ge=0)
    ctr: confloat(ge=0.0, le=1.0)
    position: confloat(ge=1.0)


__all__ = [
    "TargetSiteName",
    "TargetKeywordV1",
    "TargetKeywordListV1",
    "GSCRowV1",
]
