"""Tests for agents/franky/jobs/gsc_daily.py — cron entrypoint.

Coverage (per task prompt §Acceptance):
- compute_window: end_date = today_taipei - 4, 7-day inclusive
- load_keywords: missing yaml → None (clean exit)
- load_keywords: empty list → empty model (cron returns 'skipped')
- load_keywords: malformed YAML → propagates ValidationError / YAMLError
- gsc_row_to_pydantic: malformed rows logged + dropped, not raised
- gsc_row_to_pydantic: position < 1.0 clamped to 1.0
- run_once: missing yaml → status='skipped', no API call
- run_once: empty keyword list → status='skipped', no API call
- run_once: dry_run → no API call, no DB write
- run_once: missing GCP_SERVICE_ACCOUNT_JSON env → status='skipped'
- run_once: missing GSC_PROPERTY_* env for one site → skips that site, others process
- run_once: happy path with mocked GSC client → rows written, status='ok'
- run_once: 429 then 200 → retries with backoff, eventual success
- run_once: 5xx (500/502/503) then 200 → retries with backoff, eventual success
- run_once: 429 exhausted → retryable error propagates as keyword failure
- run_once: idempotent re-run on same day → row count unchanged
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httplib2
import pytest
import yaml
from googleapiclient.errors import HttpError

from agents.franky.jobs import gsc_daily
from shared import gsc_rows_store
from shared.gsc_client import GSCClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_keywords_yaml(tmp_path: Path, keywords: list[dict[str, Any]] | None) -> Path:
    """Write a `target-keywords.yaml` file with the supplied entries.

    `keywords=None` writes nothing (file does not exist).
    """
    p = tmp_path / "target-keywords.yaml"
    if keywords is None:
        return p
    payload = {
        "schema_version": 1,
        "updated_at": "2026-04-29T00:00:00+08:00",
        "keywords": keywords,
    }
    p.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return p


def _kw_entry(
    keyword: str = "肌酸 功效",
    site: str = "shosho.tw",
    added_by: str = "shosho",
    added_at: str = "2026-04-25T10:00:00+08:00",
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "keyword": keyword,
        "site": site,
        "added_by": added_by,
        "added_at": added_at,
    }


def _gsc_raw_row(
    day: date = date(2026, 4, 25),
    query: str = "肌酸 功效",
    page: str = "https://shosho.tw/blog/creatine",
    country: str = "twn",
    device: str = "desktop",
    clicks: int = 8,
    impressions: int = 240,
    ctr: float = 0.033,
    position: float = 11.4,
) -> dict[str, Any]:
    return {
        "keys": [day.isoformat(), query, page, country, device],
        "clicks": clicks,
        "impressions": impressions,
        "ctr": ctr,
        "position": position,
    }


def _make_mock_client(rows_per_call: list[Any] | None = None) -> Any:
    """Build a MagicMock GSCClient whose ``query()`` returns successive entries from
    ``rows_per_call``.

    ``rows_per_call`` items can be:
        list[dict]  → ok response (those rows returned directly)
        Exception   → raise that exception (e.g. HttpError 429)
    """
    client = MagicMock(spec=GSCClient)

    if rows_per_call is None:
        client.query.return_value = []
    else:
        side_effects = []
        for entry in rows_per_call:
            if isinstance(entry, Exception):
                side_effects.append(entry)
            else:
                side_effects.append(entry)
        client.query.side_effect = side_effects
    return client


def _http_error(status: int) -> HttpError:
    """Build a googleapiclient HttpError with the given status (for retry tests)."""
    resp = httplib2.Response({"status": str(status)})
    resp.reason = "Too Many Requests" if status == 429 else "Server Error"
    return HttpError(resp=resp, content=b"")


# ---------------------------------------------------------------------------
# compute_window
# ---------------------------------------------------------------------------


def test_compute_window_offsets_4_days_back_inclusive_7():
    today = date(2026, 4, 30)
    start, end = gsc_daily.compute_window(today_taipei=today)
    # end = today - 4 = 2026-04-26
    assert end == date(2026, 4, 26)
    # 7-day inclusive → start = end - 6 = 2026-04-20
    assert start == date(2026, 4, 20)
    assert (end - start).days == 6


def test_compute_window_default_uses_taipei_now():
    """No arg → uses Asia/Taipei now; smoke test it returns a sane window."""
    start, end = gsc_daily.compute_window()
    assert (end - start).days == 6


# ---------------------------------------------------------------------------
# load_keywords
# ---------------------------------------------------------------------------


def test_load_keywords_missing_path_returns_none(tmp_path):
    assert gsc_daily.load_keywords(tmp_path / "no-such.yaml") is None


def test_load_keywords_empty_list_returns_model(tmp_path):
    p = _make_keywords_yaml(tmp_path, keywords=[])
    model = gsc_daily.load_keywords(p)
    assert model is not None
    assert model.keywords == []


def test_load_keywords_validates_entries(tmp_path):
    p = _make_keywords_yaml(
        tmp_path,
        keywords=[
            _kw_entry(keyword="肌酸 功效", site="shosho.tw"),
            _kw_entry(keyword="褪黑激素 副作用", site="fleet.shosho.tw"),
        ],
    )
    model = gsc_daily.load_keywords(p)
    assert model is not None
    assert len(model.keywords) == 2
    assert model.keywords[0].site == "shosho.tw"
    assert model.keywords[1].site == "fleet.shosho.tw"


def test_load_keywords_malformed_yaml_propagates(tmp_path):
    """Truly broken file should not be silently swallowed — config error
    must surface so the operator knows to fix it."""
    p = tmp_path / "bad.yaml"
    p.write_text(": :\n  not - valid", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        gsc_daily.load_keywords(p)


# ---------------------------------------------------------------------------
# gsc_row_to_pydantic
# ---------------------------------------------------------------------------


def test_gsc_row_to_pydantic_happy_path():
    raw = _gsc_raw_row()
    model = gsc_daily.gsc_row_to_pydantic(
        raw,
        gsc_property="sc-domain:shosho.tw",
        fallback_query="肌酸 功效",
    )
    assert model is not None
    assert model.site == "sc-domain:shosho.tw"
    assert model.date == date(2026, 4, 25)
    assert model.device == "desktop"
    assert model.clicks == 8


def test_gsc_row_to_pydantic_clamps_position_below_one():
    raw = _gsc_raw_row(position=0.5)
    model = gsc_daily.gsc_row_to_pydantic(
        raw,
        gsc_property="sc-domain:shosho.tw",
        fallback_query="肌酸 功效",
    )
    assert model is not None
    assert model.position == 1.0


def test_gsc_row_to_pydantic_drops_bad_device():
    raw = _gsc_raw_row(device="smart_fridge")
    model = gsc_daily.gsc_row_to_pydantic(
        raw,
        gsc_property="sc-domain:shosho.tw",
        fallback_query="肌酸 功效",
    )
    assert model is None


def test_gsc_row_to_pydantic_drops_dim_mismatch():
    """GSC returning fewer dimensions than requested → row dropped, no crash."""
    raw = {
        "keys": ["2026-04-25", "肌酸 功效"],
        "clicks": 1,
        "impressions": 1,
        "ctr": 0.0,
        "position": 1.0,
    }
    model = gsc_daily.gsc_row_to_pydantic(
        raw,
        gsc_property="sc-domain:shosho.tw",
        fallback_query="肌酸 功效",
    )
    assert model is None


def test_gsc_row_to_pydantic_blank_query_uses_fallback():
    raw = _gsc_raw_row(query="")
    model = gsc_daily.gsc_row_to_pydantic(
        raw,
        gsc_property="sc-domain:shosho.tw",
        fallback_query="肌酸 功效",
    )
    assert model is not None
    assert model.query == "肌酸 功效"


# ---------------------------------------------------------------------------
# run_once — early-exit paths
# ---------------------------------------------------------------------------


def test_run_once_missing_yaml_returns_skipped(tmp_path):
    result = gsc_daily.run_once(
        keywords_path=tmp_path / "missing.yaml",
        env={},
    )
    assert result.status == "skipped"
    assert "missing" in result.detail


def test_run_once_empty_yaml_returns_skipped(tmp_path):
    p = _make_keywords_yaml(tmp_path, keywords=[])
    result = gsc_daily.run_once(keywords_path=p, env={})
    assert result.status == "skipped"
    assert result.keywords_total == 0


def test_run_once_dry_run_makes_no_api_call(tmp_path):
    """`--dry-run`: no client built, no API call, no DB write."""
    p = _make_keywords_yaml(tmp_path, keywords=[_kw_entry()])
    client = _make_mock_client()  # if used would record calls
    result = gsc_daily.run_once(
        keywords_path=p,
        today_taipei=date(2026, 4, 30),
        dry_run=True,
        client=client,
    )
    assert result.status == "ok"
    assert "dry_run" in result.detail
    client.query.assert_not_called()
    # And no DB writes
    out = gsc_rows_store.query(
        site="sc-domain:shosho.tw",
        since=date(2026, 4, 1),
        until=date(2026, 4, 30),
    )
    assert out == []


def test_run_once_missing_sa_env_returns_skipped(tmp_path, monkeypatch):
    """No `GCP_SERVICE_ACCOUNT_JSON` → cron exits cleanly with 'skipped'."""
    monkeypatch.delenv("GCP_SERVICE_ACCOUNT_JSON", raising=False)
    p = _make_keywords_yaml(tmp_path, keywords=[_kw_entry()])
    # Don't pass a client → defaults to GSCClient.from_env() → raises
    result = gsc_daily.run_once(
        keywords_path=p,
        today_taipei=date(2026, 4, 30),
        env={"GSC_PROPERTY_SHOSHO": "sc-domain:shosho.tw"},
    )
    assert result.status == "skipped"
    assert "credentials" in result.detail.lower()


def test_run_once_missing_property_env_skips_only_that_site(tmp_path):
    """One site has env, the other doesn't → only the configured site processed."""
    p = _make_keywords_yaml(
        tmp_path,
        keywords=[
            _kw_entry(keyword="肌酸 功效", site="shosho.tw"),
            _kw_entry(keyword="夜間血糖", site="fleet.shosho.tw"),
        ],
    )
    client = _make_mock_client(
        rows_per_call=[
            [_gsc_raw_row(query="肌酸 功效", page="https://shosho.tw/blog/creatine")],
        ]
    )
    result = gsc_daily.run_once(
        keywords_path=p,
        today_taipei=date(2026, 4, 30),
        client=client,
        env={"GSC_PROPERTY_SHOSHO": "sc-domain:shosho.tw"},  # fleet missing
        sleep=lambda _: None,
    )
    # 1 keyword processed (shosho), 0 failed (fleet skipped — not failed)
    assert result.keywords_processed == 1
    assert result.keywords_failed == 0
    assert result.rows_written == 1
    assert "sc-domain:shosho.tw" in result.sites


# ---------------------------------------------------------------------------
# run_once — happy path
# ---------------------------------------------------------------------------


def test_run_once_happy_path_writes_rows(tmp_path):
    p = _make_keywords_yaml(
        tmp_path,
        keywords=[_kw_entry(keyword="肌酸 功效", site="shosho.tw")],
    )
    rows = [
        _gsc_raw_row(day=date(2026, 4, 20), query="肌酸 功效"),
        _gsc_raw_row(day=date(2026, 4, 21), query="肌酸 功效"),
    ]
    client = _make_mock_client(rows_per_call=[rows])
    result = gsc_daily.run_once(
        keywords_path=p,
        today_taipei=date(2026, 4, 30),
        client=client,
        env={"GSC_PROPERTY_SHOSHO": "sc-domain:shosho.tw"},
        sleep=lambda _: None,
    )
    assert result.status == "ok"
    assert result.rows_written == 2

    written = gsc_rows_store.query(
        site="sc-domain:shosho.tw",
        since=date(2026, 4, 20),
        until=date(2026, 4, 26),
    )
    assert len(written) == 2

    # Verify GSC API call included date dimension and keyword filter
    call_args = client.query.call_args
    assert "date" in call_args.kwargs["dimensions"]
    assert call_args.kwargs["start_date"] == "2026-04-20"
    assert call_args.kwargs["end_date"] == "2026-04-26"
    assert call_args.kwargs["dimension_filter_groups"] == [
        {
            "filters": [
                {
                    "dimension": "query",
                    "operator": "equals",
                    "expression": "肌酸 功效",
                }
            ]
        }
    ]


def test_run_once_idempotent_same_day(tmp_path):
    """Re-running cron same day with same returned rows → row count stays equal."""
    p = _make_keywords_yaml(
        tmp_path,
        keywords=[_kw_entry(keyword="肌酸 功效", site="shosho.tw")],
    )
    rows = [_gsc_raw_row(day=date(2026, 4, 22), query="肌酸 功效")]

    # First run
    client_a = _make_mock_client(rows_per_call=[rows])
    result_a = gsc_daily.run_once(
        keywords_path=p,
        today_taipei=date(2026, 4, 30),
        client=client_a,
        env={"GSC_PROPERTY_SHOSHO": "sc-domain:shosho.tw"},
        sleep=lambda _: None,
    )
    assert result_a.rows_written == 1
    initial = gsc_rows_store.query(
        site="sc-domain:shosho.tw",
        since=date(2026, 4, 20),
        until=date(2026, 4, 26),
    )
    assert len(initial) == 1

    # Second run — same day, same rows
    client_b = _make_mock_client(rows_per_call=[rows])
    result_b = gsc_daily.run_once(
        keywords_path=p,
        today_taipei=date(2026, 4, 30),
        client=client_b,
        env={"GSC_PROPERTY_SHOSHO": "sc-domain:shosho.tw"},
        sleep=lambda _: None,
    )
    assert result_b.rows_written == 1

    after = gsc_rows_store.query(
        site="sc-domain:shosho.tw",
        since=date(2026, 4, 20),
        until=date(2026, 4, 26),
    )
    assert len(after) == 1, "idempotent re-run must not create duplicates"


# ---------------------------------------------------------------------------
# run_once — 429 retry
# ---------------------------------------------------------------------------


def test_run_once_retries_429_then_succeeds(tmp_path):
    """One 429 then 200 → keyword processed via retry."""
    p = _make_keywords_yaml(
        tmp_path,
        keywords=[_kw_entry(keyword="肌酸 功效", site="shosho.tw")],
    )
    rows = [_gsc_raw_row(day=date(2026, 4, 22), query="肌酸 功效")]
    client = _make_mock_client(
        rows_per_call=[
            _http_error(429),
            rows,  # success on second attempt
        ]
    )
    sleep_calls: list[float] = []
    result = gsc_daily.run_once(
        keywords_path=p,
        today_taipei=date(2026, 4, 30),
        client=client,
        env={"GSC_PROPERTY_SHOSHO": "sc-domain:shosho.tw"},
        sleep=lambda secs: sleep_calls.append(secs),
    )
    assert result.status == "ok"
    assert result.keywords_processed == 1
    assert result.keywords_failed == 0
    assert result.rows_written == 1
    assert len(sleep_calls) == 1, "exactly one backoff between attempts"
    assert sleep_calls[0] >= 1.0  # base 2.0 ** 0 = 1.0 + jitter


@pytest.mark.parametrize("status_code", [500, 502, 503])
def test_run_once_retries_5xx_then_succeeds(tmp_path, status_code):
    """One 5xx then 200 → keyword processed via retry (mirrors 429 test)."""
    p = _make_keywords_yaml(
        tmp_path,
        keywords=[_kw_entry(keyword="肌酸 功效", site="shosho.tw")],
    )
    rows = [_gsc_raw_row(day=date(2026, 4, 22), query="肌酸 功效")]
    client = _make_mock_client(
        rows_per_call=[
            _http_error(status_code),
            rows,  # success on second attempt
        ]
    )
    sleep_calls: list[float] = []
    result = gsc_daily.run_once(
        keywords_path=p,
        today_taipei=date(2026, 4, 30),
        client=client,
        env={"GSC_PROPERTY_SHOSHO": "sc-domain:shosho.tw"},
        sleep=lambda secs: sleep_calls.append(secs),
    )
    assert result.status == "ok"
    assert result.keywords_processed == 1
    assert result.keywords_failed == 0
    assert result.rows_written == 1
    assert len(sleep_calls) == 1, "exactly one backoff between attempts"
    assert sleep_calls[0] >= 1.0  # base 2.0 ** 0 = 1.0 + jitter


def test_run_once_429_exhausted_counts_as_keyword_failure(tmp_path):
    """All 3 attempts return 429 → that keyword fails, cron status reflects partial/fail."""
    p = _make_keywords_yaml(
        tmp_path,
        keywords=[_kw_entry(keyword="肌酸 功效", site="shosho.tw")],
    )
    client = _make_mock_client(rows_per_call=[_http_error(429), _http_error(429), _http_error(429)])
    result = gsc_daily.run_once(
        keywords_path=p,
        today_taipei=date(2026, 4, 30),
        client=client,
        env={"GSC_PROPERTY_SHOSHO": "sc-domain:shosho.tw"},
        sleep=lambda _: None,
    )
    # 1 of 1 keywords failed → status='fail'
    assert result.status == "fail"
    assert result.keywords_failed == 1
    assert result.keywords_processed == 0
    assert result.rows_written == 0


def test_run_once_4xx_non_retryable_fails_fast(tmp_path):
    """403 (auth) is NOT retryable → first failure ends the keyword."""
    p = _make_keywords_yaml(
        tmp_path,
        keywords=[_kw_entry(keyword="肌酸 功效", site="shosho.tw")],
    )
    client = _make_mock_client(rows_per_call=[_http_error(403)])
    sleep_calls: list[float] = []
    result = gsc_daily.run_once(
        keywords_path=p,
        today_taipei=date(2026, 4, 30),
        client=client,
        env={"GSC_PROPERTY_SHOSHO": "sc-domain:shosho.tw"},
        sleep=lambda s: sleep_calls.append(s),
    )
    # 403 not retried — 0 sleeps, keyword failed once
    assert sleep_calls == []
    assert result.keywords_failed == 1


def test_run_once_partial_status_when_some_keywords_fail(tmp_path):
    """One keyword succeeds, one 403s → status='partial'."""
    p = _make_keywords_yaml(
        tmp_path,
        keywords=[
            _kw_entry(keyword="肌酸 功效", site="shosho.tw"),
            _kw_entry(keyword="褪黑激素", site="shosho.tw"),
        ],
    )
    rows = [_gsc_raw_row(day=date(2026, 4, 22), query="肌酸 功效")]
    client = _make_mock_client(rows_per_call=[rows, _http_error(403)])
    result = gsc_daily.run_once(
        keywords_path=p,
        today_taipei=date(2026, 4, 30),
        client=client,
        env={"GSC_PROPERTY_SHOSHO": "sc-domain:shosho.tw"},
        sleep=lambda _: None,
    )
    assert result.status == "partial"
    assert result.keywords_processed == 1
    assert result.keywords_failed == 1
    assert result.rows_written == 1
