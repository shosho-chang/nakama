"""Regression tests for the dotenv `KEY=` empty-string fallback trap.

`os.environ.get(K, DEFAULT)` returns `""` when .env has `KEY=` (no value),
not DEFAULT — `getenv`'s second arg only fires when KEY is missing entirely.
Downstream `int("")` raises and `httpx.get("")` raises UnsupportedProtocols.
The fix pattern is `os.environ.get(K) or DEFAULT`. See
`memory/claude/feedback_dotenv_empty_string_fallback.md`.

Driving incident: 2026-04-26 — probe_nakama_gateway failed every 5 minutes for
216 consecutive cron ticks because VPS .env had NAKAMA_HEALTHZ_URL= (empty),
and the prior os.getenv default never fired.
"""

from unittest.mock import MagicMock


def test_franky_probe_nakama_gateway_falls_to_default_url_when_env_empty(monkeypatch):
    monkeypatch.setenv("NAKAMA_HEALTHZ_URL", "")
    from agents.franky import health_check

    captured: dict = {}

    class _Resp:
        status_code = 200

    class _Client:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            captured["url"] = url
            return _Resp()

    monkeypatch.setattr(health_check.httpx, "Client", _Client)

    result = health_check.probe_nakama_gateway()

    assert captured["url"] == health_check.DEFAULT_NAKAMA_HEALTHZ_URL
    assert result.status == "ok"


def test_usopp_build_from_env_falls_to_default_ints_when_env_empty(monkeypatch):
    from agents.usopp import __main__ as usopp_main

    monkeypatch.setenv("USOPP_POLL_INTERVAL_S", "")
    monkeypatch.setenv("USOPP_BATCH_SIZE", "")
    monkeypatch.setenv("USOPP_TARGET_SITE", "wp_shosho")
    monkeypatch.setattr(usopp_main, "WordPressClient", MagicMock())
    monkeypatch.setattr(usopp_main, "Publisher", MagicMock())

    fake_daemon_cls = MagicMock()
    monkeypatch.setattr(usopp_main, "UsoppDaemon", fake_daemon_cls)

    usopp_main._build_from_env()

    kwargs = fake_daemon_cls.call_args.kwargs
    assert kwargs["poll_interval_s"] == usopp_main.DEFAULT_POLL_INTERVAL_S
    assert kwargs["batch_size"] == usopp_main.DEFAULT_BATCH_SIZE


def test_notifier_send_email_does_not_raise_when_smtp_port_env_empty(monkeypatch):
    monkeypatch.setenv("SMTP_PORT", "")
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("SMTP_USER", "")
    monkeypatch.setenv("SMTP_PASS", "")
    monkeypatch.setenv("NOTIFY_TO", "")
    from shared import notifier

    # Pre-fix int("") raised BEFORE the incomplete-config early-return guard.
    notifier.send_email("subject", "body")


def test_multimodal_arbiter_uses_default_max_workers_when_env_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_MAX_WORKERS", "")
    audio = tmp_path / "fake.wav"
    audio.write_bytes(b"")

    from shared import multimodal_arbiter as ma

    monkeypatch.setattr(ma, "_parse_srt_index", lambda s: {1: (0.0, 1.0, "x")})

    captured: dict = {}

    class _Executor:
        def __init__(self, *a, **kw):
            captured["max_workers"] = kw.get("max_workers")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def map(self, fn, items):
            return iter([])

    monkeypatch.setattr(ma, "ThreadPoolExecutor", _Executor)

    result = ma.arbitrate_uncertain(
        audio,
        "fake_srt_content",
        [{"line": 1, "original": "x", "suggestion": "y", "reason": "", "risk": "low"}],
    )

    assert captured["max_workers"] == 3
    assert result == []
