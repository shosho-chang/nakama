"""GSC daily cron — ADR-008 Phase 2a-min (alert-free, digest-free).

Runs daily 03:00 Asia/Taipei. Pulls a 7-day window ending ``today - 4`` from
GSC search-analytics for each keyword in `config/target-keywords.yaml`,
UPSERTs into `state.db` `gsc_rows`. Idempotent on re-run within the same day.

This slice is **observation-only**:
  - No alert rules (those land in Phase 2a-full)
  - No weekly digest section (also Phase 2a-full)
  - Read-side consumers: `/bridge/seo` v1.1 rank-change panel via
    `shared.gsc_rows_store.rank_change_28d`

Date math (ADR-008 §2):
  - GSC reports lag 2-4 days behind real time
  - End-date = today (Asia/Taipei) - 4 days   (conservative)
  - Start-date = end_date - 6                 (7-day inclusive window)

Failure modes (defensive — never raise out of `run_once`):
  - Missing `target-keywords.yaml` / empty list → log + return summary, exit 0
  - Missing `GCP_SERVICE_ACCOUNT_JSON` env → log + return summary, exit 0
  - Missing `GSC_PROPERTY_*` env for a site → skip that site, continue others
  - GSC HTTP 429 / 5xx → exponential backoff retry up to `_MAX_RETRIES`
  - Single-keyword failure → log, count, continue with rest

Subcommand:
    python -m agents.franky gsc-daily              # production cron path
    python -m agents.franky gsc-daily --dry-run    # parse + log, no write / no API call
"""

from __future__ import annotations

import os
import random
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import yaml
from googleapiclient.errors import HttpError

from shared.gsc_client import GSCClient, GSCCredentialsError
from shared.gsc_rows_store import upsert_rows
from shared.log import get_logger
from shared.schemas.seo import GSCRowV1, TargetKeywordListV1, TargetSiteName

logger = get_logger("nakama.franky.gsc_daily")

_TAIPEI = ZoneInfo("Asia/Taipei")
_GSC_LAG_DAYS = 4
_WINDOW_DAYS = 7  # inclusive — end_date back through end_date - 6
_GSC_DIMENSIONS = ["date", "query", "page", "country", "device"]
_GSC_ROW_LIMIT = 1000  # GSC API hard cap; per-keyword × 7d should never approach

# Map TargetKeywordV1.site → both env var name and GSC property string
# format used by the rest of the codebase. ADR-008 §2 says GSC property
# format is `sc-domain:<host>`, ADR-008 §8 says path lives in the env file.
_SITE_TO_GSC_PROPERTY_ENV: dict[TargetSiteName, str] = {
    "shosho.tw": "GSC_PROPERTY_SHOSHO",
    "fleet.shosho.tw": "GSC_PROPERTY_FLEET",
}

# Retry tuning for transient GSC errors (429 + 5xx).
_MAX_RETRIES = 3
_BACKOFF_BASE_SECONDS = 2.0
_BACKOFF_JITTER = 0.5  # additive uniform [0, 0.5]


# ---------------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------------


@dataclass
class GscDailyResult:
    """Summary returned by ``run_once`` — also serialised for stdout."""

    operation_id: str
    status: str  # 'ok' | 'skipped' | 'partial' | 'fail'
    detail: str
    keywords_total: int = 0
    keywords_processed: int = 0
    keywords_failed: int = 0
    rows_written: int = 0
    sites: dict[str, int] = field(default_factory=dict)  # site → rows_written

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "status": self.status,
            "detail": self.detail,
            "keywords_total": self.keywords_total,
            "keywords_processed": self.keywords_processed,
            "keywords_failed": self.keywords_failed,
            "rows_written": self.rows_written,
            "sites": dict(self.sites),
        }


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _new_op_id() -> str:
    return f"op_{uuid.uuid4().hex[:8]}"


def compute_window(today_taipei: Optional[date] = None) -> tuple[date, date]:
    """Return (start_date, end_date) inclusive for the daily 7-day pull.

    Today defaults to "now in Asia/Taipei" — VPS TZ is Asia/Taipei
    (`reference_vps_timezone.md`) so calling this from anywhere in the
    process gives the right calendar day. Tests inject a fixed date.

    end_date = today - 4 (conservative — GSC delay is 2-4 days, ADR-008 §2).
    start_date = end_date - 6 (7-day inclusive window).
    """
    if today_taipei is None:
        today_taipei = datetime.now(_TAIPEI).date()
    end_date = today_taipei - timedelta(days=_GSC_LAG_DAYS)
    start_date = end_date - timedelta(days=_WINDOW_DAYS - 1)
    return start_date, end_date


def load_keywords(path: Path) -> Optional[TargetKeywordListV1]:
    """Read + validate `config/target-keywords.yaml`. Returns ``None`` if file
    missing (cron should log + exit 0 cleanly, not crash).

    Raises only on truly malformed YAML / Pydantic ValidationError — those
    are programmer / config errors that should surface, not silently skip.
    """
    if not path.is_file():
        logger.warning("target_keywords_yaml missing path=%s", path)
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return TargetKeywordListV1.model_validate(raw)


def _site_to_gsc_property(site: TargetSiteName, env: dict[str, str]) -> Optional[str]:
    """Return the GSC property string for `site`, or ``None`` if env not set.

    Cron is best-effort: if `GSC_PROPERTY_SHOSHO` isn't set we skip that
    site rather than crashing the whole daily pull.
    """
    env_key = _SITE_TO_GSC_PROPERTY_ENV[site]
    value = (env.get(env_key) or "").strip()
    return value or None


def gsc_row_to_pydantic(
    raw: dict[str, Any],
    *,
    gsc_property: str,
    fallback_query: str,
) -> Optional[GSCRowV1]:
    """Convert one GSC API row into `GSCRowV1`. Returns ``None`` if the row
    is malformed (logs + skips — defensive, GSC sometimes emits oddities).

    ``raw['keys']`` order matches `_GSC_DIMENSIONS`:
        [date, query, page, country, device]
    """
    keys = raw.get("keys") or []
    if len(keys) != len(_GSC_DIMENSIONS):
        logger.warning("gsc_row_dim_mismatch expected=%d got=%d", len(_GSC_DIMENSIONS), len(keys))
        return None

    raw_date, raw_query, raw_page, raw_country, raw_device = keys

    try:
        row_date = date.fromisoformat(raw_date)
    except (TypeError, ValueError):
        logger.warning("gsc_row_bad_date raw=%r", raw_date)
        return None

    # Query may legitimately come back blank (anonymized); we requested with
    # an explicit query filter so blanks shouldn't reach us, but defend
    # anyway by falling back to the keyword we filtered with.
    query = (raw_query or "").strip() or fallback_query
    if not query:
        return None

    page = (raw_page or "").strip()
    if not page:
        return None

    country = (raw_country or "").strip().lower()
    if len(country) < 2 or len(country) > 3:
        logger.warning("gsc_row_bad_country raw=%r", raw_country)
        return None

    device = (raw_device or "").strip().lower()
    if device not in ("desktop", "mobile", "tablet"):
        logger.warning("gsc_row_bad_device raw=%r", raw_device)
        return None

    # Position must be ≥ 1.0 per schema; GSC sometimes returns < 1.0 for
    # rich-result queries. Clamp instead of dropping (consistent with
    # `enrich.py` `_build_keyword_metric`).
    position = max(1.0, float(raw.get("position", 1.0)))
    ctr = max(0.0, min(1.0, float(raw.get("ctr", 0.0))))
    clicks = max(0, int(raw.get("clicks", 0) or 0))
    impressions = max(0, int(raw.get("impressions", 0) or 0))

    return GSCRowV1(
        site=gsc_property,
        date=row_date,
        query=query,
        page=page,
        country=country,
        device=device,  # type: ignore[arg-type]  # Literal narrowed at runtime above
        clicks=clicks,
        impressions=impressions,
        ctr=ctr,
        position=position,
    )


# ---------------------------------------------------------------------------
# GSC fetch with retry
# ---------------------------------------------------------------------------


def _is_retryable_http_error(exc: BaseException) -> bool:
    """True iff `exc` is a retryable transient GSC error (429 + 5xx).

    `googleapiclient.errors.HttpError.resp.status` is the classic place to
    look. The SDK already retries 5xx internally (num_retries=2), but 429
    is **not** in its default retry set — we must catch it explicitly.
    """
    if not isinstance(exc, HttpError):
        return False
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status is None:
        return False
    return status == 429 or 500 <= status <= 599


def _query_with_retry(
    client: GSCClient,
    *,
    site: str,
    start_date: str,
    end_date: str,
    dimensions: list[str],
    dimension_filter_groups: list[dict[str, Any]],
    sleep: Any = time.sleep,
) -> list[dict[str, Any]]:
    """Wrap `GSCClient.query` with explicit 429 + 5xx retry loop.

    Sleep is injected so tests can run with no wall-clock waste.
    """
    last_exc: Optional[BaseException] = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return client.query(
                site=site,
                start_date=start_date,
                end_date=end_date,
                dimensions=dimensions,
                row_limit=_GSC_ROW_LIMIT,
                dimension_filter_groups=dimension_filter_groups,
            )
        except HttpError as exc:
            last_exc = exc
            if not _is_retryable_http_error(exc) or attempt == _MAX_RETRIES:
                raise
            wait = _BACKOFF_BASE_SECONDS ** (attempt - 1) + random.uniform(0, _BACKOFF_JITTER)
            logger.warning(
                "gsc_query_retryable attempt=%d/%d wait=%.1fs status=%s",
                attempt,
                _MAX_RETRIES,
                wait,
                getattr(exc.resp, "status", "?"),
            )
            sleep(wait)
    # Defensive — should not reach here (raise inside loop on terminal attempt).
    if last_exc is not None:
        raise last_exc
    return []


# ---------------------------------------------------------------------------
# Public entry — one tick of the daily cron
# ---------------------------------------------------------------------------


def run_once(
    *,
    keywords_path: Optional[Path] = None,
    today_taipei: Optional[date] = None,
    dry_run: bool = False,
    client: Optional[GSCClient] = None,
    env: Optional[dict[str, str]] = None,
    sleep: Any = time.sleep,
) -> GscDailyResult:
    """Run the daily pull once. Returns a `GscDailyResult` summary.

    Args:
        keywords_path: override `config/target-keywords.yaml` location (tests).
        today_taipei:  override "now" for deterministic date math (tests).
        dry_run:       True → parse keywords + compute window + log; no API
                       call, no DB write. Useful for ops dry-runs / CI smoke.
        client:        injected GSC client (tests). Default `GSCClient.from_env`.
        env:           injected env mapping (tests). Default `os.environ`.
        sleep:         injected sleep callable for retry backoff (tests).
    """
    op_id = _new_op_id()
    env_map = env if env is not None else os.environ
    keywords_path = keywords_path or _default_keywords_path()
    start_date, end_date = compute_window(today_taipei)

    # 1. Load + validate keyword list -----------------------------------------
    try:
        kw_list = load_keywords(keywords_path)
    except Exception as exc:
        # Malformed YAML / schema violation — don't swallow; log + return.
        return GscDailyResult(
            operation_id=op_id,
            status="fail",
            detail=f"keywords_load_error: {type(exc).__name__}: {exc}"[:300],
        )

    if kw_list is None:
        return GscDailyResult(
            operation_id=op_id,
            status="skipped",
            detail=f"keywords_yaml_missing path={keywords_path}",
        )
    if not kw_list.keywords:
        return GscDailyResult(
            operation_id=op_id,
            status="skipped",
            detail="keywords_yaml_empty",
            keywords_total=0,
        )

    keywords_total = len(kw_list.keywords)

    if dry_run:
        return GscDailyResult(
            operation_id=op_id,
            status="ok",
            detail=(
                f"dry_run window=[{start_date.isoformat()},{end_date.isoformat()}] "
                f"keywords_total={keywords_total}"
            ),
            keywords_total=keywords_total,
            keywords_processed=0,
        )

    # 2. Build GSC client (may bail early if SA JSON missing) -----------------
    if client is None:
        try:
            client = GSCClient.from_env()
        except GSCCredentialsError as exc:
            return GscDailyResult(
                operation_id=op_id,
                status="skipped",
                detail=f"gsc_credentials_missing: {exc}"[:300],
                keywords_total=keywords_total,
            )

    # 3. Group keywords by site so we can skip a whole site if env not set ----
    by_site: dict[TargetSiteName, list[str]] = defaultdict(list)
    for kw in kw_list.keywords:
        by_site[kw.site].append(kw.keyword)

    rows_written = 0
    keywords_processed = 0
    keywords_failed = 0
    site_counts: dict[str, int] = {}
    skipped_sites: list[str] = []

    for site, keyword_strings in by_site.items():
        gsc_property = _site_to_gsc_property(site, env_map)
        if gsc_property is None:
            logger.warning(
                "gsc_property_env_missing site=%s env_key=%s — skipping %d keywords",
                site,
                _SITE_TO_GSC_PROPERTY_ENV[site],
                len(keyword_strings),
            )
            skipped_sites.append(site)
            continue

        site_rows: list[GSCRowV1] = []
        for keyword in keyword_strings:
            try:
                site_rows.extend(
                    _fetch_one_keyword(
                        client=client,
                        gsc_property=gsc_property,
                        keyword=keyword,
                        start_date=start_date,
                        end_date=end_date,
                        sleep=sleep,
                    )
                )
                keywords_processed += 1
            except Exception as exc:
                keywords_failed += 1
                logger.error(
                    "gsc_fetch_keyword_failed site=%s keyword=%r err=%s",
                    site,
                    keyword,
                    f"{type(exc).__name__}: {exc}"[:200],
                )

        # UPSERT per site so a later site failure doesn't lose earlier writes.
        if site_rows:
            written = upsert_rows(site_rows)
            rows_written += written
            site_counts[gsc_property] = site_counts.get(gsc_property, 0) + written

    # 4. Verdict --------------------------------------------------------------
    if keywords_processed == 0 and keywords_failed == 0 and skipped_sites:
        status = "skipped"
        detail = f"all_sites_missing_env skipped={skipped_sites}"
    elif keywords_failed > 0 and keywords_processed > 0:
        status = "partial"
        detail = (
            f"window=[{start_date.isoformat()},{end_date.isoformat()}] "
            f"processed={keywords_processed} failed={keywords_failed} rows={rows_written}"
        )
    elif keywords_failed > 0 and keywords_processed == 0:
        status = "fail"
        detail = f"all_keywords_failed count={keywords_failed}"
    else:
        status = "ok"
        detail = (
            f"window=[{start_date.isoformat()},{end_date.isoformat()}] "
            f"processed={keywords_processed} rows={rows_written}"
        )

    return GscDailyResult(
        operation_id=op_id,
        status=status,
        detail=detail,
        keywords_total=keywords_total,
        keywords_processed=keywords_processed,
        keywords_failed=keywords_failed,
        rows_written=rows_written,
        sites=site_counts,
    )


def _fetch_one_keyword(
    *,
    client: GSCClient,
    gsc_property: str,
    keyword: str,
    start_date: date,
    end_date: date,
    sleep: Any,
) -> list[GSCRowV1]:
    """Pull one keyword's 7-day window and return validated `GSCRowV1` list."""
    raw_rows = _query_with_retry(
        client,
        site=gsc_property,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        dimensions=_GSC_DIMENSIONS,
        dimension_filter_groups=[
            {
                "filters": [
                    {
                        "dimension": "query",
                        "operator": "equals",
                        "expression": keyword,
                    }
                ]
            }
        ],
        sleep=sleep,
    )

    out: list[GSCRowV1] = []
    for raw in raw_rows:
        model = gsc_row_to_pydantic(raw, gsc_property=gsc_property, fallback_query=keyword)
        if model is not None:
            out.append(model)
    logger.debug(
        "gsc_fetch_keyword site=%s keyword=%r raw=%d kept=%d",
        gsc_property,
        keyword,
        len(raw_rows),
        len(out),
    )
    return out


def _default_keywords_path() -> Path:
    """Repo-relative `config/target-keywords.yaml`."""
    return Path(__file__).resolve().parent.parent.parent.parent / "config" / "target-keywords.yaml"


__all__ = [
    "GscDailyResult",
    "compute_window",
    "load_keywords",
    "gsc_row_to_pydantic",
    "run_once",
]
