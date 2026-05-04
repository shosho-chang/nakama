"""Integration tests for ``GET /discard-info`` + ``POST /discard`` (Slice 5, #356).

Validates the full discard pipeline at the FastAPI layer:

- Auth gate: unauth POST → 302 ``/login``; unauth GET ``/discard-info`` → 403.
- ``GET /discard-info?file=&base=`` returns the annotation count the
  frontend needs to fill the confirm prompt template (no count → 0).
- ``POST /discard?file=&base=`` recycles the source file:
  - Linux: ``Path.unlink`` removed the file.
  - Windows: ``subprocess.run`` invoked with the prefix-match-safe command
    (``feedback_powershell_allow_exact_prefix.md``).
- ``KB/Annotations/{slug}.md`` is also recycled when present (annotation
  連動刪 — issue #356 acceptance #6).
- Path traversal rejected by ``safe_resolve``.
- Redirects 303 → ``/`` after successful discard (so 修修 lands on the
  refreshed inbox view, matching PRD §Pipeline / API).

Mocking notes (matches Slice 1 ``test_scrape_translate_endpoint`` style):
- ``thousand_sunny.routers.robin.DiscardService`` is the caller binding —
  patching there ensures the endpoint uses the mock without touching
  ``shared.discard_service`` (matches the ``URLDispatcher`` pattern from
  Slice 1).
- For the real-disk subprocess assertion we patch ``subprocess.run`` on
  ``shared.discard_service`` (the production binding) so the platform
  branch executes end-to-end.
"""

from __future__ import annotations

import importlib
import platform
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    (tmp_path / "KB" / "Annotations").mkdir(parents=True)
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.auth as auth_router_module
    import thousand_sunny.routers.robin as robin_module

    importlib.reload(auth_module)
    importlib.reload(auth_router_module)
    importlib.reload(robin_module)
    importlib.reload(app_module)
    return TestClient(app_module.app, follow_redirects=False), tmp_path


def _auth_cookie(client: TestClient) -> str:
    resp = client.post("/login", data={"password": "testpass"}, follow_redirects=False)
    return resp.cookies.get("nakama_auth", "")


def _write_source(path: Path, *, title: str = "Test Article") -> None:
    path.write_text(
        "---\n"
        f'title: "{title}"\n'
        'source: "https://example.com/article"\n'
        "source_type: article\n"
        "content_nature: popular_science\n"
        "---\n\n"
        "# Body\n\nLorem ipsum.\n",
        encoding="utf-8",
    )


# ── Auth gates ───────────────────────────────────────────────────────────────


def test_discard_info_requires_auth(client):
    tc, _ = client
    resp = tc.get("/discard-info?file=foo.md")
    assert resp.status_code == 403


def test_discard_post_requires_auth(client):
    tc, _ = client
    resp = tc.post("/discard?file=foo.md")
    # Endpoint redirects to /login (matches /scrape-translate auth gate behaviour)
    assert resp.status_code == 302
    assert "/login" in resp.headers["location"]


# ── GET /discard-info — annotation count ────────────────────────────────────


def test_discard_info_no_annotation_returns_zero(client):
    """Fresh inbox file with no annotation companion → count == 0."""
    tc, vault = client
    auth = _auth_cookie(tc)
    src = vault / "Inbox" / "kb" / "fresh.md"
    _write_source(src, title="Fresh Article")

    resp = tc.get("/discard-info?file=fresh.md", cookies={"nakama_auth": auth})
    assert resp.status_code == 200
    data = resp.json()
    assert data["annotation_count"] == 0
    assert data["slug"] == "fresh-article"


def test_discard_info_counts_existing_annotations(client):
    """N highlights/annotations → count == N (drives confirm prompt N)."""
    tc, vault = client
    auth = _auth_cookie(tc)

    src = vault / "Inbox" / "kb" / "studied.md"
    _write_source(src, title="Studied Paper")

    # Pre-populate annotation file (the AnnotationStore reads VAULT_PATH from
    # the env var the fixture set).
    from shared.annotation_store import AnnotationSet, AnnotationStore, Highlight

    store = AnnotationStore()
    store.save(
        AnnotationSet(
            slug="studied-paper",
            source_filename="studied.md",
            base="inbox",
            items=[
                Highlight(text="first"),
                Highlight(text="second"),
            ],
        )
    )

    resp = tc.get("/discard-info?file=studied.md", cookies={"nakama_auth": auth})
    assert resp.status_code == 200
    assert resp.json()["annotation_count"] == 2


def test_discard_info_404_on_missing_file(client):
    tc, _ = client
    auth = _auth_cookie(tc)
    resp = tc.get("/discard-info?file=nope.md", cookies={"nakama_auth": auth})
    assert resp.status_code == 404


# ── POST /discard — happy path ───────────────────────────────────────────────


def test_discard_redirects_to_inbox(client, monkeypatch):
    """Successful discard returns 303 → ``/`` (so 修修 lands on refreshed inbox)."""
    tc, vault = client
    auth = _auth_cookie(tc)
    src = vault / "Inbox" / "kb" / "to-trash.md"
    _write_source(src, title="To Trash")

    # Run on Linux branch (real unlink) so we don't shell out.
    monkeypatch.setattr(platform, "system", lambda: "Linux")

    resp = tc.post("/discard?file=to-trash.md", cookies={"nakama_auth": auth})

    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    # File actually gone.
    assert not src.exists()


def test_discard_recycles_source_on_linux(client, monkeypatch):
    """Linux branch: unlink the file directly (no subprocess shellout)."""
    tc, vault = client
    auth = _auth_cookie(tc)
    src = vault / "Inbox" / "kb" / "linux-discard.md"
    _write_source(src)

    monkeypatch.setattr(platform, "system", lambda: "Linux")

    resp = tc.post("/discard?file=linux-discard.md", cookies={"nakama_auth": auth})

    assert resp.status_code == 303
    assert not src.exists()


def test_discard_recycles_source_on_windows_via_powershell(client, monkeypatch):
    """Windows branch: shell out to PowerShell with the prefix-match-safe command.

    Patches ``subprocess.run`` on ``shared.discard_service`` (the production
    binding inside ``_send_to_recycle_bin``) — caller-binding rule
    (mock教訓 in spec).
    """
    import shared.discard_service as disc_mod

    tc, vault = client
    auth = _auth_cookie(tc)
    src = vault / "Inbox" / "kb" / "win-discard.md"
    _write_source(src)

    monkeypatch.setattr(platform, "system", lambda: "Windows")

    captured = {}

    def fake_run(args, check):
        captured.setdefault("calls", []).append({"args": args, "check": check})
        return MagicMock(returncode=0)

    monkeypatch.setattr(disc_mod.subprocess, "run", fake_run)

    resp = tc.post("/discard?file=win-discard.md", cookies={"nakama_auth": auth})

    assert resp.status_code == 303
    assert "calls" in captured
    first = captured["calls"][0]
    assert first["args"][0] == "powershell"
    assert first["args"][1] == "-Command"
    # Pin the prefix exactly — see feedback_powershell_allow_exact_prefix.md
    assert first["args"][2].startswith(
        "Add-Type -AssemblyName Microsoft.VisualBasic; "
        "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("
    )
    assert "SendToRecycleBin" in first["args"][2]
    assert first["check"] is False


# ── POST /discard — annotation 連動刪 ───────────────────────────────────────


def test_discard_recycles_annotation_companion(client, monkeypatch):
    """When ``KB/Annotations/{slug}.md`` exists → it goes to recycle bin too."""
    tc, vault = client
    auth = _auth_cookie(tc)

    src = vault / "Inbox" / "kb" / "with-notes.md"
    _write_source(src, title="With Notes")

    from shared.annotation_store import AnnotationSet, AnnotationStore, Highlight

    store = AnnotationStore()
    store.save(
        AnnotationSet(
            slug="with-notes",
            source_filename="with-notes.md",
            base="inbox",
            items=[Highlight(text="critical insight")],
        )
    )
    ann_path = vault / "KB" / "Annotations" / "with-notes.md"
    assert ann_path.exists()

    monkeypatch.setattr(platform, "system", lambda: "Linux")

    resp = tc.post("/discard?file=with-notes.md", cookies={"nakama_auth": auth})

    assert resp.status_code == 303
    assert not src.exists()
    assert not ann_path.exists()


def test_discard_skips_annotation_when_not_present(client, monkeypatch):
    """No annotation file → only the source gets recycled (no spurious calls)."""
    tc, vault = client
    auth = _auth_cookie(tc)

    src = vault / "Inbox" / "kb" / "alone.md"
    _write_source(src, title="Alone")

    monkeypatch.setattr(platform, "system", lambda: "Linux")

    # Patch DiscardService at the caller binding so we can assert the report.
    with patch(
        "thousand_sunny.routers.robin.DiscardService",
        wraps=__import__("shared.discard_service", fromlist=["DiscardService"]).DiscardService,
    ) as MockService:
        resp = tc.post("/discard?file=alone.md", cookies={"nakama_auth": auth})

    assert resp.status_code == 303
    assert not src.exists()
    # Exactly one DiscardService instantiated per request.
    assert MockService.call_count == 1


# ── POST /discard — idempotency / error edges ──────────────────────────────


def test_discard_missing_file_still_redirects(client, monkeypatch):
    """Double-clicking the discard button must not 404 — endpoint is idempotent."""
    tc, vault = client
    auth = _auth_cookie(tc)

    monkeypatch.setattr(platform, "system", lambda: "Linux")

    # File never written.
    resp = tc.post("/discard?file=ghost.md", cookies={"nakama_auth": auth})

    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_discard_path_traversal_rejected(client):
    """``..`` in the file param must be rejected by ``safe_resolve`` (403)."""
    tc, _ = client
    auth = _auth_cookie(tc)

    resp = tc.post("/discard?file=../../../etc/passwd", cookies={"nakama_auth": auth})
    assert resp.status_code == 403


def test_discard_unknown_base_rejected(client):
    """``base=etc`` not in the allowlist must 400."""
    tc, _ = client
    auth = _auth_cookie(tc)

    resp = tc.post("/discard?file=foo.md&base=etc", cookies={"nakama_auth": auth})
    assert resp.status_code == 400


# ── DiscardService caller binding (mock 教訓) ───────────────────────────────


def test_discard_uses_discard_service_caller_binding(client, monkeypatch):
    """Patching ``thousand_sunny.routers.robin.DiscardService`` MUST intercept.

    Spec mock 教訓: ``from X import Y`` → tests must patch the caller binding,
    not the source module. This test is the contract ensuring the endpoint
    imports DiscardService at the module level (not lazy inside the handler).
    """
    tc, vault = client
    auth = _auth_cookie(tc)
    src = vault / "Inbox" / "kb" / "intercepted.md"
    _write_source(src, title="Intercepted")

    monkeypatch.setattr(platform, "system", lambda: "Linux")

    with patch("thousand_sunny.routers.robin.DiscardService") as MockService:
        instance = MockService.return_value
        instance.discard.return_value = MagicMock(
            file_path=src,
            slug="intercepted",
            annotation_count=0,
            deleted_file=True,
            annotation_deleted=False,
        )
        resp = tc.post("/discard?file=intercepted.md", cookies={"nakama_auth": auth})

    assert resp.status_code == 303
    MockService.assert_called_once_with()
    instance.discard.assert_called_once()
    # The patched service was used (real shared.discard_service never ran),
    # so the source file is still there.
    assert src.exists()
