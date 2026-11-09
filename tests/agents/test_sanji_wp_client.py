"""WPClient 對 Cloudflare challenge 的辨識。

2026-09-06 起 shosho.tw zone 的 Super Bot Fight Mode 把 Sanji（UA ``nakama-sanji/0.1``，
VPS datacenter IP）當機器人，每一輪都回 403 + 「Just a moment...」HTML。舊版把它當一般
4xx，log 與告警裡只有 300 字 HTML，看不出是 CF 擋的、也不知道該去哪修。
"""

from __future__ import annotations

import httpx
import pytest

from agents.sanji.wp_client import CloudflareChallenge, GamAPIError, WPClient

_CHALLENGE_HTML = (
    '<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title>'
    '<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">'
)


def _client(handler) -> WPClient:
    c = WPClient("https://fleet.example.test", "sanji", "pw")
    c._client = httpx.Client(
        base_url="https://fleet.example.test/wp-json/nakama-gam/v1",
        transport=httpx.MockTransport(handler),
    )
    return c


def test_cf_challenge_header_raises_cloudflare_challenge():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            headers={"cf-mitigated": "challenge", "cf-ray": "a40e3e00cb99758d-SEA"},
            text=_CHALLENGE_HTML,
        )

    with pytest.raises(CloudflareChallenge) as ei:
        _client(handler).events(416)

    msg = str(ei.value)
    assert ei.value.status == 403
    assert "Cloudflare challenge" in msg
    assert "a40e3e00cb99758d-SEA" in msg
    assert "cf-waf-skip-rules.md" in msg
    assert "<!DOCTYPE" not in msg  # 不再把 HTML 倒進 log / 告警


def test_cf_challenge_detected_from_body_without_header():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=_CHALLENGE_HTML)

    with pytest.raises(CloudflareChallenge):
        _client(handler).health()


def test_plain_wp_403_stays_generic_api_error():
    """WP 自己回的 403（例如 app password 權限不足）不能被誤判成 CF。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"code": "rest_forbidden", "message": "Sorry"})

    with pytest.raises(GamAPIError) as ei:
        _client(handler).events(0)

    assert not isinstance(ei.value, CloudflareChallenge)
    assert "rest_forbidden" in str(ei.value)


def test_cf_challenge_is_not_retried():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(403, headers={"cf-mitigated": "challenge"}, text=_CHALLENGE_HTML)

    with pytest.raises(CloudflareChallenge):
        _client(handler).events(0)
    assert calls == 1
