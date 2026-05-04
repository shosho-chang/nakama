"""Integration tests for ``POST /scrape-translate`` (Slice 1, issue #352).

Validates the new BackgroundTask-based behaviour:

- Auth gate still redirects to /login.
- Successful POST returns 303 → ``/`` (inbox view), NOT ``/read``.
  (Slice 1 design: user always lands on inbox so they see the placeholder
  row pulse-loading before the BackgroundTask finishes.)
- A placeholder ``Inbox/kb/{slug}.md`` is written synchronously with
  ``fulltext_status: processing`` so the inbox view has something to render
  the moment the redirect lands.
- ``BackgroundTasks.add_task`` is invoked with ``_ingest_url_in_background``
  (BG task is scheduled, not run inline). FastAPI runs background tasks
  AFTER the response in TestClient, so we assert behaviour AFTER the POST
  returns: the BG body has run and the placeholder file is overwritten with
  the ``URLDispatcher`` output.
- Same-URL repeat short-circuits to ``/read?file={existing}`` (acceptance #6).

Mocking note: tests inject ``URLDispatcher.dispatch`` via ``monkeypatch`` on
``thousand_sunny.routers.robin.URLDispatcher`` (the caller-binding) so the
BackgroundTask uses the mock rather than the real ``shared.web_scraper``.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agents.robin.url_dispatcher import URLDispatcher
from shared.schemas.ingest_result import IngestResult


@pytest.fixture
def client(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("DISABLE_ROBIN", raising=False)
    monkeypatch.setenv("WEB_PASSWORD", "testpass")
    monkeypatch.setenv("WEB_SECRET", "testsecret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    inbox = tmp_path / "Inbox" / "kb"
    inbox.mkdir(parents=True)
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.auth as auth_router_module
    import thousand_sunny.routers.robin as robin_module

    importlib.reload(auth_module)
    importlib.reload(auth_router_module)
    importlib.reload(robin_module)
    importlib.reload(app_module)
    return TestClient(app_module.app, follow_redirects=False), inbox


def _auth_cookie(client):
    resp = client.post("/login", data={"password": "testpass"}, follow_redirects=False)
    return resp.cookies.get("nakama_auth", "")


# ── Auth gate ────────────────────────────────────────────────────────────────


def test_scrape_translate_requires_auth(client):
    tc, _inbox = client
    resp = tc.post("/scrape-translate", data={"url": "https://example.com"})
    assert resp.status_code == 302
    assert "/login" in resp.headers["location"]


# ── Redirect target switched from /read to / (Slice 1 design) ────────────────


def test_scrape_translate_redirects_to_inbox_not_reader(client):
    """Slice 1 changes redirect target — endpoint no longer waits for fetch."""
    tc, _inbox = client
    auth = _auth_cookie(tc)

    fake_result = IngestResult(
        status="ready",
        fulltext_layer="readability",
        fulltext_source="Readability",
        markdown="# T\n\n" + ("body\n" * 80),
        title="T",
        original_url="https://example.com/article",
    )

    with patch("thousand_sunny.routers.robin.URLDispatcher", spec=URLDispatcher) as MockDispatcher:
        instance = MockDispatcher.return_value
        instance.dispatch.return_value = fake_result

        resp = tc.post(
            "/scrape-translate",
            data={
                "url": "https://example.com/article",
                "source_type": "article",
                "content_nature": "popular_science",
            },
            cookies={"nakama_auth": auth},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


# ── Placeholder + BackgroundTask scheduling ──────────────────────────────────


def test_scrape_translate_writes_placeholder_synchronously(client):
    """The placeholder file appears the moment the POST returns."""
    tc, inbox = client
    auth = _auth_cookie(tc)

    fake_result = IngestResult(
        status="ready",
        fulltext_layer="readability",
        fulltext_source="Readability",
        markdown="# Hello\n\n" + ("text\n" * 80),
        title="Hello",
        original_url="https://example.com/post",
    )

    with patch("thousand_sunny.routers.robin.URLDispatcher", spec=URLDispatcher) as MockDispatcher:
        instance = MockDispatcher.return_value
        instance.dispatch.return_value = fake_result

        tc.post(
            "/scrape-translate",
            data={"url": "https://example.com/post"},
            cookies={"nakama_auth": auth},
            follow_redirects=False,
        )

    md_files = list(inbox.glob("*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text(encoding="utf-8")
    # By the time TestClient returns, BackgroundTask has run → status=ready.
    assert "fulltext_status: ready" in content
    assert "original_url:" in content
    assert "https://example.com/post" in content


def test_scrape_translate_bg_task_scheduled(client):
    """Verify the route schedules ``_ingest_url_in_background`` on BackgroundTasks."""
    tc, _inbox = client
    auth = _auth_cookie(tc)

    fake_add_task = MagicMock()

    # FastAPI's BackgroundTasks instance is constructed per-request — to assert
    # scheduling we patch the module-level helper that the route delegates to.
    with (
        patch("thousand_sunny.routers.robin._ingest_url_in_background") as mock_bg,
        patch(
            "thousand_sunny.routers.robin.BackgroundTasks.add_task",
            side_effect=lambda fn, **kw: fake_add_task(fn, **kw),
        ),
    ):
        resp = tc.post(
            "/scrape-translate",
            data={"url": "https://example.com/scheduled"},
            cookies={"nakama_auth": auth},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    fake_add_task.assert_called_once()
    fn, kw = fake_add_task.call_args[0][0], fake_add_task.call_args[1]
    assert fn is mock_bg
    assert kw["url"] == "https://example.com/scheduled"


# ── Failed dispatch path (< 200 chars) ───────────────────────────────────────


def test_scrape_translate_short_content_writes_failed_status(client):
    """URLDispatcher returning failed → file frontmatter shows fulltext_status: failed."""
    tc, inbox = client
    auth = _auth_cookie(tc)

    fake_result = IngestResult(
        status="failed",
        fulltext_layer="readability",
        fulltext_source="Readability",
        markdown="",
        title="example.com/short",
        original_url="https://example.com/short",
        note="抓取結果太短，疑似 bot 擋頁",
    )

    with patch("thousand_sunny.routers.robin.URLDispatcher", spec=URLDispatcher) as MockDispatcher:
        instance = MockDispatcher.return_value
        instance.dispatch.return_value = fake_result

        tc.post(
            "/scrape-translate",
            data={"url": "https://example.com/short"},
            cookies={"nakama_auth": auth},
            follow_redirects=False,
        )

    md_files = list(inbox.glob("*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text(encoding="utf-8")
    assert "fulltext_status: failed" in content
    assert "疑似 bot 擋頁" in content


# ── BG task crash recovery ──────────────────────────────────────────────────


def test_scrape_translate_bg_crash_flips_placeholder_to_failed(client):
    """BG task crashing on URLDispatcher init must flip the row to ❌, not stay 🔄.

    Slice 1 has no delete UI (Slice 5 #356 adds it), so a placeholder stuck on
    ``processing`` would be permanently invisible to the user — the recovery
    write in ``_flip_placeholder_to_failed`` is the only off-ramp.
    """
    tc, inbox = client
    auth = _auth_cookie(tc)

    with patch(
        "thousand_sunny.routers.robin.URLDispatcher",
        spec=URLDispatcher,
        side_effect=RuntimeError("dispatcher boom"),
    ):
        resp = tc.post(
            "/scrape-translate",
            data={"url": "https://example.com/crashed"},
            cookies={"nakama_auth": auth},
            follow_redirects=False,
        )

    # Route still returned 303 — BG task crash is invisible to caller.
    assert resp.status_code == 303
    md_files = list(inbox.glob("*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text(encoding="utf-8")
    # Recovery write replaced the processing placeholder with a failed row.
    assert "fulltext_status: failed" in content
    assert "fulltext_layer: unknown" in content
    assert "後台任務崩潰" in content


# ── Same-URL short-circuit (acceptance #6) ──────────────────────────────────


def test_scrape_translate_same_url_short_circuits(client):
    """Re-pasting a URL whose ingest already produced a file skips the BG task."""
    tc, inbox = client
    auth = _auth_cookie(tc)

    # Pre-populate inbox with a finished ingest for the same URL.
    existing = inbox / "already-here.md"
    existing.write_text(
        "---\n"
        'title: "x"\n'
        'source: "https://example.com/dup"\n'
        'original_url: "https://example.com/dup"\n'
        "source_type: article\n"
        "content_nature: popular_science\n"
        "fulltext_status: ready\n"
        "fulltext_layer: readability\n"
        'fulltext_source: "Readability"\n'
        "---\n\nbody\n",
        encoding="utf-8",
    )

    with patch("thousand_sunny.routers.robin.URLDispatcher", spec=URLDispatcher) as MockDispatcher:
        instance = MockDispatcher.return_value
        # Should NOT be called when short-circuiting.
        instance.dispatch.side_effect = AssertionError(
            "URLDispatcher.dispatch must not run on URL repeat"
        )

        resp = tc.post(
            "/scrape-translate",
            data={"url": "https://example.com/dup"},
            cookies={"nakama_auth": auth},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/read?file=already-here.md"
    # Direct contract: short-circuit means URLDispatcher class was never even
    # instantiated by the route (BG task wasn't scheduled). The file-count
    # assertion below is a secondary check — both must hold.
    MockDispatcher.assert_not_called()
    instance.dispatch.assert_not_called()
    # Still exactly one file — no extra placeholder written.
    assert sorted(p.name for p in inbox.glob("*.md")) == ["already-here.md"]
