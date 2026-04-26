"""PageSpeed Insights client tests — mock httpx 邊界（不打真 API）。

覆蓋：
- 缺 PAGESPEED_INSIGHTS_API_KEY → PageSpeedCredentialsError
- query payload 組成（url / key / strategy / category 多次）
- strategy 非 mobile/desktop → ValueError
- 4xx 不 retry 直接 raise
- 5xx 重試 2 次後仍失敗 raise；中途成功則回 JSON
- 連線錯誤 retry
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from shared.pagespeed_client import PageSpeedClient, PageSpeedCredentialsError


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Retry sleep 不 wait 真時間。"""
    monkeypatch.setattr("shared.pagespeed_client.time.sleep", lambda _s: None)


@pytest.fixture
def fake_resp():
    """工廠：產 mock httpx.Response。"""

    def _make(status_code: int, json_payload: dict | None = None, text: str = "") -> MagicMock:
        r = MagicMock(spec=httpx.Response)
        r.status_code = status_code
        r.text = text
        r.json.return_value = json_payload or {}
        if 400 <= status_code:
            r.raise_for_status.side_effect = httpx.HTTPStatusError(
                f"{status_code}", request=MagicMock(), response=r
            )
        else:
            r.raise_for_status.return_value = None
        return r

    return _make


def test_init_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("PAGESPEED_INSIGHTS_API_KEY", raising=False)
    with pytest.raises(PageSpeedCredentialsError, match="not set"):
        PageSpeedClient()


def test_from_env_picks_up_env(monkeypatch):
    monkeypatch.setenv("PAGESPEED_INSIGHTS_API_KEY", "test-key-123")
    client = PageSpeedClient.from_env()
    assert client._api_key == "test-key-123"


def test_explicit_api_key_overrides_env(monkeypatch):
    monkeypatch.setenv("PAGESPEED_INSIGHTS_API_KEY", "from-env")
    client = PageSpeedClient(api_key="explicit-key")
    assert client._api_key == "explicit-key"


def test_invalid_strategy_raises(monkeypatch):
    monkeypatch.setenv("PAGESPEED_INSIGHTS_API_KEY", "k")
    client = PageSpeedClient()
    with pytest.raises(ValueError, match="strategy"):
        client.run("https://example.com", strategy="tablet")  # type: ignore[arg-type]


def test_run_builds_correct_payload(monkeypatch, fake_resp):
    monkeypatch.setenv("PAGESPEED_INSIGHTS_API_KEY", "k123")
    client = PageSpeedClient()

    with patch("shared.pagespeed_client.httpx.get") as m_get:
        m_get.return_value = fake_resp(200, {"lighthouseResult": {"audits": {}}})
        out = client.run("https://example.com/post", strategy="mobile")

    assert out == {"lighthouseResult": {"audits": {}}}
    call = m_get.call_args
    assert call[0][0] == "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
    params = call[1]["params"]
    # 確保包含 url / key / strategy + 4 次 category
    assert ("url", "https://example.com/post") in params
    assert ("key", "k123") in params
    assert ("strategy", "mobile") in params
    cats = [v for k, v in params if k == "category"]
    assert cats == ["PERFORMANCE", "SEO", "BEST_PRACTICES", "ACCESSIBILITY"]
    assert call[1]["timeout"] == 60.0


def test_run_4xx_no_retry(monkeypatch, fake_resp):
    """API key 失效 / quota = 403/400；不 retry。"""
    monkeypatch.setenv("PAGESPEED_INSIGHTS_API_KEY", "bad")
    client = PageSpeedClient()

    with patch("shared.pagespeed_client.httpx.get") as m_get:
        m_get.return_value = fake_resp(403, text="Forbidden")
        with pytest.raises(httpx.HTTPStatusError):
            client.run("https://example.com")
        assert m_get.call_count == 1  # no retry


def test_run_5xx_retries_then_success(monkeypatch, fake_resp):
    monkeypatch.setenv("PAGESPEED_INSIGHTS_API_KEY", "k")
    client = PageSpeedClient()

    with patch("shared.pagespeed_client.httpx.get") as m_get:
        m_get.side_effect = [
            fake_resp(503, text="Service Unavailable"),
            fake_resp(200, {"ok": True}),
        ]
        out = client.run("https://example.com")
    assert out == {"ok": True}
    assert m_get.call_count == 2


def test_run_5xx_giveup_after_max_attempts(monkeypatch, fake_resp):
    monkeypatch.setenv("PAGESPEED_INSIGHTS_API_KEY", "k")
    client = PageSpeedClient()

    with patch("shared.pagespeed_client.httpx.get") as m_get:
        m_get.return_value = fake_resp(502)
        with pytest.raises(httpx.HTTPStatusError):
            client.run("https://example.com")
    assert m_get.call_count == 3


def test_run_request_error_retries(monkeypatch, fake_resp):
    monkeypatch.setenv("PAGESPEED_INSIGHTS_API_KEY", "k")
    client = PageSpeedClient()

    with patch("shared.pagespeed_client.httpx.get") as m_get:
        m_get.side_effect = [
            httpx.ConnectTimeout("timeout"),
            fake_resp(200, {"recovered": True}),
        ]
        out = client.run("https://example.com")
    assert out == {"recovered": True}
    assert m_get.call_count == 2


def test_run_request_error_giveup(monkeypatch):
    monkeypatch.setenv("PAGESPEED_INSIGHTS_API_KEY", "k")
    client = PageSpeedClient()

    with patch("shared.pagespeed_client.httpx.get") as m_get:
        m_get.side_effect = httpx.ConnectError("refused")
        with pytest.raises(httpx.ConnectError):
            client.run("https://example.com")
    assert m_get.call_count == 3


def test_desktop_strategy_propagates(monkeypatch, fake_resp):
    monkeypatch.setenv("PAGESPEED_INSIGHTS_API_KEY", "k")
    client = PageSpeedClient()

    with patch("shared.pagespeed_client.httpx.get") as m_get:
        m_get.return_value = fake_resp(200, {})
        client.run("https://x.test", strategy="desktop")
    params = m_get.call_args[1]["params"]
    assert ("strategy", "desktop") in params
