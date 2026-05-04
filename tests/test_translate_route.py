"""Tests for ``POST /translate`` (Slice 3, issue #354).

Validates the on-demand translate endpoint that 修修 triggers from the
reader header after eyeballing the original-language fulltext:

- Auth gate redirects to /login.
- Successful POST returns 303 → ``/`` (the inbox view). FastAPI runs
  ``BackgroundTasks`` AFTER the response in TestClient, so by the time
  POST returns the BG body has finished and the bilingual file +
  frontmatter mutation are observable. Redirect target is the inbox so
  the user can watch the row's 🔄 (translating) icon flip to 📖
  (translated) — NOT ``/read?file={slug}-bilingual.md``, which used to
  race the BG write and 404 every long article (see
  ``test_translate_redirects_to_inbox_not_bilingual_reader``).
- Bilingual short-circuit: when ``{slug}-bilingual.md`` already exists,
  the endpoint redirects WITHOUT scheduling a new BG task and WITHOUT
  re-running ``translate_document`` (mirrors ``/pubmed-to-reader`` line
  499 short-circuit pattern). The short-circuit redirect IS direct to
  the bilingual reader because the file is already there — no race.
- Before scheduling the BG task, the original ``Inbox/kb/{slug}.md``
  frontmatter is flipped to ``fulltext_status: translating``. After BG
  completes it's flipped to ``translated`` (acceptance #4).
- Translator failure leaves the source frontmatter on ``translating``
  (intentionally visible — the row stays ``translating`` so the user
  can notice + retry rather than the row silently snapping back to
  ``ready`` and hiding the failure).

Mocking note: ``shared.translator.translate_document`` is the function
``thousand_sunny.routers.robin._translate_in_background`` calls via
``import-X-then-call`` inside the BG body, so tests patch the symbol
on the *robin router* module (caller-binding) per
``feedback_facade_mock_caller_binding``.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


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


def _seed_ready_file(inbox: Path, *, name: str = "example.md") -> Path:
    """Drop a Slice 1-shaped ``status=ready`` file into the inbox."""
    p = inbox / name
    p.write_text(
        "---\n"
        'title: "Test article"\n'
        'source: "https://example.com/article"\n'
        'original_url: "https://example.com/article"\n'
        "source_type: article\n"
        "content_nature: popular_science\n"
        "fulltext_status: ready\n"
        "fulltext_layer: readability\n"
        'fulltext_source: "Readability"\n'
        "---\n\n# Test article\n\nFirst paragraph.\n\nSecond paragraph.\n",
        encoding="utf-8",
    )
    return p


# ── Auth gate ────────────────────────────────────────────────────────────────


def test_translate_requires_auth(client):
    tc, _inbox = client
    resp = tc.post("/translate", params={"file": "example.md"})
    assert resp.status_code == 302
    assert "/login" in resp.headers["location"]


# ── Happy path: translate runs + redirect to bilingual reader ────────────────


def test_translate_writes_bilingual_and_redirects(client):
    """POST → BG runs translate_document → writes bilingual.md → 303 to inbox.

    Redirect target is ``/`` (inbox), NOT ``/read?file={stem}-bilingual.md``
    — see ``test_translate_redirects_to_inbox_not_bilingual_reader`` for
    the dedicated regression covering why.
    """
    tc, inbox = client
    auth = _auth_cookie(tc)
    _seed_ready_file(inbox, name="example.md")

    fake_bilingual = (
        "# Test article\n\n> 測試文章\n\nFirst paragraph.\n\n> 第一段。\n\n"
        "Second paragraph.\n\n> 第二段。\n"
    )

    with patch(
        "thousand_sunny.routers.robin.translate_document",
        return_value=fake_bilingual,
    ) as mock_translate:
        resp = tc.post(
            "/translate",
            params={"file": "example.md"},
            cookies={"nakama_auth": auth},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    mock_translate.assert_called_once()

    bilingual = inbox / "example-bilingual.md"
    assert bilingual.exists()
    text = bilingual.read_text(encoding="utf-8")
    assert "bilingual: true" in text
    assert "第一段" in text


def test_translate_updates_original_frontmatter_to_translated(client):
    """Acceptance #4: after BG completes, original file frontmatter flips."""
    tc, inbox = client
    auth = _auth_cookie(tc)
    original = _seed_ready_file(inbox, name="paper.md")

    with patch(
        "thousand_sunny.routers.robin.translate_document",
        return_value="# Title\n\n> 標題\n\nbody\n\n> 內文。\n",
    ):
        tc.post(
            "/translate",
            params={"file": "paper.md"},
            cookies={"nakama_auth": auth},
            follow_redirects=False,
        )

    text = original.read_text(encoding="utf-8")
    # Frontmatter status flipped from ``ready`` → ``translated`` in place.
    assert "fulltext_status: translated" in text
    assert "fulltext_status: ready" not in text
    # Body untouched (we only mutated the YAML scalar).
    assert "First paragraph." in text


# ── Short-circuit: bilingual file already present ─────────────────────────────


def test_translate_short_circuits_when_bilingual_exists(client):
    """Already-translated → redirect immediately, no BG task, no LLM call."""
    tc, inbox = client
    auth = _auth_cookie(tc)
    _seed_ready_file(inbox, name="example.md")
    bilingual = inbox / "example-bilingual.md"
    bilingual.write_text(
        "---\nbilingual: true\n---\n\nalready translated\n",
        encoding="utf-8",
    )
    bilingual_mtime_before = bilingual.stat().st_mtime_ns

    with patch("thousand_sunny.routers.robin.translate_document") as mock_translate:
        resp = tc.post(
            "/translate",
            params={"file": "example.md"},
            cookies={"nakama_auth": auth},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/read?file=example-bilingual.md"
    # Short-circuit invariant: translator NEVER called.
    mock_translate.assert_not_called()
    # File untouched (mtime unchanged → no rewrite happened).
    assert bilingual.stat().st_mtime_ns == bilingual_mtime_before


def test_translate_short_circuit_does_not_schedule_bg_task(client):
    """Short-circuit must skip ``BackgroundTasks.add_task`` entirely.

    Direct contract complementing the translator-not-called assertion: the
    route must not even queue the BG body, otherwise a slow mock would mask
    a regression where the short-circuit only avoids the LLM but still
    re-runs the frontmatter mutation step.
    """
    tc, inbox = client
    auth = _auth_cookie(tc)
    _seed_ready_file(inbox, name="example.md")
    (inbox / "example-bilingual.md").write_text(
        "---\nbilingual: true\n---\n\nx\n", encoding="utf-8"
    )

    with patch("thousand_sunny.routers.robin.BackgroundTasks.add_task") as mock_add_task:
        resp = tc.post(
            "/translate",
            params={"file": "example.md"},
            cookies={"nakama_auth": auth},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    mock_add_task.assert_not_called()


# ── Error / edge cases ───────────────────────────────────────────────────────


def test_translate_404_when_source_missing(client):
    """No such file in inbox → 404 (don't write a bilingual.md from nothing)."""
    tc, _inbox = client
    auth = _auth_cookie(tc)

    resp = tc.post(
        "/translate",
        params={"file": "ghost.md"},
        cookies={"nakama_auth": auth},
        follow_redirects=False,
    )
    assert resp.status_code == 404


def test_translate_rejects_non_markdown(client):
    """``.txt`` / ``.pdf`` files aren't fulltext articles — reject upfront."""
    tc, inbox = client
    auth = _auth_cookie(tc)
    (inbox / "raw.pdf").write_bytes(b"%PDF-1.4")

    resp = tc.post(
        "/translate",
        params={"file": "raw.pdf"},
        cookies={"nakama_auth": auth},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_translate_rejects_path_traversal(client):
    """User-supplied ``file`` must not escape the inbox dir."""
    tc, _inbox = client
    auth = _auth_cookie(tc)

    resp = tc.post(
        "/translate",
        params={"file": "../../etc/passwd"},
        cookies={"nakama_auth": auth},
        follow_redirects=False,
    )
    # safe_resolve raises 403 on traversal.
    assert resp.status_code in (400, 403, 404)


def test_translate_bg_failure_leaves_original_status_translating(client):
    """Translator crash must NOT flip the source frontmatter to ``translated``.

    With the inbox-redirect contract (race-fix), the route flips the source
    to ``translating`` BEFORE scheduling the BG task — so a translator
    crash leaves the row stuck on ``translating`` rather than snapping
    back to ``ready``. That visible "in flight, never finished" surface
    is the recovery affordance: the user notices the row isn't moving
    and can retry. (Pre-fix behaviour was "stays ``ready`` so user can
    retry"; post-fix the equivalent is "stays ``translating`` so user
    can notice + retry.")
    """
    tc, inbox = client
    auth = _auth_cookie(tc)
    original = _seed_ready_file(inbox, name="example.md")

    with patch(
        "thousand_sunny.routers.robin.translate_document",
        side_effect=RuntimeError("LLM down"),
    ):
        resp = tc.post(
            "/translate",
            params={"file": "example.md"},
            cookies={"nakama_auth": auth},
            follow_redirects=False,
        )

    # Route still redirects (BG crash invisible to caller per the same
    # contract as ``/scrape-translate`` — recovery is observable in inbox).
    assert resp.status_code == 303
    text = original.read_text(encoding="utf-8")
    assert "fulltext_status: translating" in text
    assert "fulltext_status: translated" not in text
    assert "fulltext_status: ready" not in text
    # Bilingual file never created.
    assert not (inbox / "example-bilingual.md").exists()


def test_reader_shows_translate_button_on_non_bilingual_file(client):
    """Acceptance #1: reader header (原文模式) 顯示「翻譯成中文」按鈕。

    Render a Slice-1 ``ready`` source through ``/read`` and confirm the
    template emits the ``triggerTranslate`` button. The bilingual variant
    must NOT render it (covered in ``test_reader_hides_translate_button_on_bilingual``).
    """
    tc, inbox = client
    auth = _auth_cookie(tc)
    _seed_ready_file(inbox, name="example.md")

    resp = tc.get("/read?file=example.md", cookies={"nakama_auth": auth})
    assert resp.status_code == 200
    assert 'id="translateBtn"' in resp.text
    assert "翻譯成中文" in resp.text
    # ``triggerTranslate`` is the JS hook the button binds to — ensure
    # the function definition is present so the click never silently no-ops.
    assert "function triggerTranslate" in resp.text


def test_reader_hides_translate_button_on_bilingual(client):
    """Bilingual reader page must NOT show the translate button.

    Once 修修 is on the ``-bilingual.md`` page they're already reading the
    translated version — exposing the button there would (a) crowd the
    header and (b) risk double-posting to ``/translate?file={bilingual}``
    which the short-circuit defends against but the UI shouldn't invite.
    """
    tc, inbox = client
    auth = _auth_cookie(tc)
    bilingual = inbox / "example-bilingual.md"
    bilingual.write_text(
        '---\ntitle: "x"\nbilingual: true\nfulltext_status: translated\n---\n\n# X\n\n> 翻譯版。\n',
        encoding="utf-8",
    )

    resp = tc.get("/read?file=example-bilingual.md", cookies={"nakama_auth": auth})
    assert resp.status_code == 200
    assert 'id="translateBtn"' not in resp.text


def test_inbox_view_renders_translated_status_icon(client):
    """Acceptance #5: after translation, inbox row reflects ``translated`` status.

    Indirect end-to-end check (not strictly part of /translate scope but the
    user-visible delivery): write a Slice-3-shaped frontmatter with
    ``fulltext_status: translated`` and confirm the ``index`` template
    renders the bilingual emoji rather than the generic ✅.
    """
    tc, inbox = client
    auth = _auth_cookie(tc)
    (inbox / "translated.md").write_text(
        "---\n"
        'title: "x"\n'
        'source: "https://example.com/x"\n'
        'original_url: "https://example.com/x"\n'
        "source_type: article\n"
        "content_nature: popular_science\n"
        "fulltext_status: translated\n"
        "fulltext_layer: readability\n"
        'fulltext_source: "Readability"\n'
        "---\n\nbody\n",
        encoding="utf-8",
    )

    resp = tc.get("/", cookies={"nakama_auth": auth})
    assert resp.status_code == 200
    # The bilingual emoji 📖 is the visual delta from ``ready`` → ``translated``.
    assert "📖" in resp.text
    assert 'data-status="translated"' in resp.text


def test_translate_does_not_double_translate_bilingual_filename(client):
    """Calling /translate on a ``-bilingual.md`` file is idempotent (returns the same name).

    Defends against a UI bug where the reader header on the bilingual page
    accidentally posts the bilingual filename back. We treat this as
    "already translated" and short-circuit instead of writing
    ``example-bilingual-bilingual.md``.
    """
    tc, inbox = client
    auth = _auth_cookie(tc)
    bilingual = inbox / "example-bilingual.md"
    bilingual.write_text("---\nbilingual: true\n---\n\nalready translated\n", encoding="utf-8")

    with patch("thousand_sunny.routers.robin.translate_document") as mock_translate:
        resp = tc.post(
            "/translate",
            params={"file": "example-bilingual.md"},
            cookies={"nakama_auth": auth},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/read?file=example-bilingual.md"
    mock_translate.assert_not_called()


# ── Race-fix regressions: redirect target + ``translating`` lifecycle ─────────


def test_translate_redirects_to_inbox_not_bilingual_reader(client):
    """Race regression: POST /translate must NOT redirect to the bilingual reader.

    The original Slice-3 implementation redirected to
    ``/read?file={stem}-bilingual.md`` immediately after scheduling the
    BG task. On long articles (BMJ Medicine, 326 paragraphs ≈ 3 min)
    the redirect raced the BG write and produced HTTP 404
    ``找不到檔案：{stem}-bilingual.md`` for every user. Reproduced live
    2026-05-04.

    The fix sends the user back to the inbox so they can watch the row's
    🔄 (translating) icon flip to 📖 (translated) before clicking 「閱讀」.
    This test simulates the BG NOT YET RUNNING by skipping the BG
    execution entirely (``add_task`` is patched to a no-op) and asserts
    the redirect goes to ``/`` regardless of the bilingual file's
    existence.
    """
    tc, inbox = client
    auth = _auth_cookie(tc)
    _seed_ready_file(inbox, name="example.md")

    with patch("thousand_sunny.routers.robin.BackgroundTasks.add_task"):
        # BG never runs → bilingual file is never written. The race-buggy
        # version of /translate would still 303 to /read?file=example-bilingual.md
        # under this exact setup, which is what users hit live.
        resp = tc.post(
            "/translate",
            params={"file": "example.md"},
            cookies={"nakama_auth": auth},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/", (
        "Translate route must redirect to inbox '/' so the user doesn't "
        "race the BG-written bilingual file. Redirecting to "
        "'/read?file=...-bilingual.md' caused 404s on long articles."
    )
    # Bilingual file genuinely doesn't exist yet — confirms the race
    # window we'd otherwise hit if redirect went straight to /read.
    assert not (inbox / "example-bilingual.md").exists()


def test_translate_flips_source_to_translating_before_redirect(client):
    """``ready`` → ``translating`` transition observable immediately after redirect.

    Companion to ``test_translate_redirects_to_inbox_not_bilingual_reader``:
    the redirect goes to the inbox, and the inbox row must show the
    in-flight 🔄 icon — which requires the source frontmatter to flip
    to ``fulltext_status: translating`` BEFORE the redirect is issued
    (not lazily inside the BG body, otherwise there's a window where
    the row still shows ✅ ready and invites a second click).

    We simulate "BG hasn't run" by patching ``add_task`` to a no-op so
    the only state change observable is the synchronous flip the route
    handler did.
    """
    tc, inbox = client
    auth = _auth_cookie(tc)
    original = _seed_ready_file(inbox, name="example.md")
    assert "fulltext_status: ready" in original.read_text(encoding="utf-8")

    with patch("thousand_sunny.routers.robin.BackgroundTasks.add_task"):
        resp = tc.post(
            "/translate",
            params={"file": "example.md"},
            cookies={"nakama_auth": auth},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    text = original.read_text(encoding="utf-8")
    assert "fulltext_status: translating" in text
    assert "fulltext_status: ready" not in text
    assert "fulltext_status: translated" not in text  # BG hasn't run yet


def test_translate_full_lifecycle_translating_then_translated(client):
    """End-to-end: ``ready`` → (sync) ``translating`` → (BG) ``translated``.

    Asserts the regex in ``_flip_status_to_translated`` correctly
    rewrites the new ``translating`` intermediate scalar — without
    this, the BG task would silently no-op the second flip and the row
    would stay stuck on 🔄 even after the bilingual file was written.

    TestClient drives BackgroundTasks AFTER the response body is sent,
    so by the time ``post`` returns the BG body has finished and we
    can observe the FINAL ``translated`` state in one read.
    """
    tc, inbox = client
    auth = _auth_cookie(tc)
    original = _seed_ready_file(inbox, name="example.md")

    with patch(
        "thousand_sunny.routers.robin.translate_document",
        return_value="# Test article\n\n> 測試文章\n\nFirst paragraph.\n\n> 第一段。\n",
    ):
        resp = tc.post(
            "/translate",
            params={"file": "example.md"},
            cookies={"nakama_auth": auth},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    text = original.read_text(encoding="utf-8")
    # Final state after BG ran: must be ``translated``, NOT stuck on
    # ``translating`` (regression for the regex matching the new state).
    assert "fulltext_status: translated" in text
    assert "fulltext_status: translating" not in text
    assert "fulltext_status: ready" not in text
    # Bilingual file actually written by the BG body.
    assert (inbox / "example-bilingual.md").exists()


def test_inbox_view_renders_translating_status_icon(client):
    """Inbox row 🔄 + ``data-status="translating"`` for in-flight rows.

    Mirrors ``test_inbox_view_renders_translated_status_icon`` for the
    new intermediate state. Without this template branch the row would
    fall through and render no icon at all while the BG task is in
    flight, hiding the "translating" surface from the user.
    """
    tc, inbox = client
    auth = _auth_cookie(tc)
    (inbox / "in-flight.md").write_text(
        "---\n"
        'title: "x"\n'
        'source: "https://example.com/x"\n'
        'original_url: "https://example.com/x"\n'
        "source_type: article\n"
        "content_nature: popular_science\n"
        "fulltext_status: translating\n"
        "fulltext_layer: readability\n"
        'fulltext_source: "Readability"\n'
        "---\n\nbody\n",
        encoding="utf-8",
    )

    resp = tc.get("/", cookies={"nakama_auth": auth})
    assert resp.status_code == 200
    assert 'data-status="translating"' in resp.text
    assert "翻譯中" in resp.text  # title attribute on the icon span
