"""Tests for thousand_sunny.routers.brook — context bridge (ADR-027 §Decision 8).

The conversational `/brook/chat` LLM loop, SQLite persistence, and
`export_draft` endpoint were removed in PR-3. What remains:

- ``GET /brook/chat`` — 301 redirect to ``/brook/bridge`` (link-rot mitigation).
- ``GET /brook/bridge`` — renders the context bridge page; with ``topic``
  query param, packages context via ``agents.brook.context_bridge``.

Auth uses dev-mode (``WEB_PASSWORD`` / ``WEB_SECRET`` unset) →
``check_auth`` returns True. The bridge module is exercised end-to-end;
KB search is stubbed to avoid hitting the real vault and to assert the
NO-LLM invariant deterministically.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    """TestClient with dev-mode auth + Robin disabled + isolated vault."""
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    # Point the vault at a tmp_path so context_bridge can read a Projects
    # dir we control without touching the real Obsidian vault.
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    (tmp_path / "Projects").mkdir()
    (tmp_path / "KB" / "Wiki" / "Sources").mkdir(parents=True)
    (tmp_path / "KB" / "Annotations").mkdir(parents=True)

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.brook as brook_module

    importlib.reload(auth_module)
    importlib.reload(brook_module)
    importlib.reload(app_module)
    return TestClient(app_module.app, follow_redirects=False)


# ---------------------------------------------------------------------------
# GET /brook/chat — legacy redirect
# ---------------------------------------------------------------------------


def test_chat_legacy_url_redirects_301_to_bridge(client):
    """Old `/brook/chat` URL must 301 to `/brook/bridge` (link-rot mitigation)."""
    r = client.get("/brook/chat")
    assert r.status_code == 301
    assert r.headers["location"] == "/brook/bridge"


# ---------------------------------------------------------------------------
# GET /brook/bridge — page shell (no topic)
# ---------------------------------------------------------------------------


def test_bridge_page_dev_mode_returns_html(client):
    """No query params → form-only HTML page, 200."""
    r = client.get("/brook/bridge")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    # Form is always present
    assert 'name="topic"' in r.text
    assert "Context Bridge" in r.text


def test_bridge_redirects_to_login_when_auth_required(monkeypatch, tmp_path):
    """When WEB_SECRET is set and no cookie present → 302 → /login."""
    monkeypatch.setenv("WEB_PASSWORD", "testpass")
    monkeypatch.setenv("WEB_SECRET", "testsecret")
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    (tmp_path / "Projects").mkdir()

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.brook as brook_module

    importlib.reload(auth_module)
    importlib.reload(brook_module)
    importlib.reload(app_module)
    local_client = TestClient(app_module.app, follow_redirects=False)

    r = local_client.get("/brook/bridge")
    assert r.status_code == 302
    assert "/login" in r.headers["location"]


# ---------------------------------------------------------------------------
# GET /brook/bridge?topic=... — packaging path
# ---------------------------------------------------------------------------


def test_bridge_with_topic_renders_packaged_prompt(client, monkeypatch):
    """With ``topic``, the page must render the packaged prompt blob and
    the compliance reminder section."""
    monkeypatch.setattr(
        "agents.brook.context_bridge.search_kb",
        lambda query, vault_path, top_k=5: [],
    )

    r = client.get("/brook/bridge?topic=肌酸對睡眠的影響")
    assert r.status_code == 200
    body = r.text
    # Summary section + prompt box both render
    assert "已打包內容" in body
    assert "Packaged prompt" in body
    assert "肌酸對睡眠的影響" in body
    # Compliance reminder always present
    assert "合規提醒" in body


def test_bridge_includes_kb_chunks_when_search_returns_hits(client, monkeypatch):
    monkeypatch.setattr(
        "agents.brook.context_bridge.search_kb",
        lambda query, vault_path, top_k=5: [
            {
                "title": "肌酸研究綜述",
                "type": "article",
                "relevance_reason": "directly on topic",
            },
            {
                "title": "睡眠節律與荷爾蒙",
                "type": "note",
                "relevance_reason": "related context",
            },
        ],
    )
    r = client.get("/brook/bridge?topic=肌酸睡眠")
    assert r.status_code == 200
    body = r.text
    assert "肌酸研究綜述" in body
    assert "睡眠節律與荷爾蒙" in body
    # Chunk count summary surfaces in the summary panel
    assert ">2<" in body or ">2 " in body or ">2</span>" in body


def test_bridge_includes_project_excerpt_when_project_slug_passed(
    client, monkeypatch, tmp_path
):
    """When a Projects/<slug>.md exists, its frontmatter excerpt is in
    the packaged prompt blob."""
    monkeypatch.setattr(
        "agents.brook.context_bridge.search_kb",
        lambda query, vault_path, top_k=5: [],
    )
    project_md = tmp_path / "Projects" / "test-project.md"
    project_md.write_text(
        "---\ntype: project\ntopic: 測試專題\n---\n\nProject body line.\n",
        encoding="utf-8",
    )

    r = client.get("/brook/bridge?topic=主題&project_slug=test-project")
    assert r.status_code == 200
    body = r.text
    assert "test-project" in body
    assert "Project 上下文" in body
    assert "測試專題" in body


def test_bridge_includes_rcp_when_annotations_exist(
    client, monkeypatch, tmp_path
):
    """When source_slug points at a Robin source with annotations on disk,
    the RCP excerpt section is in the packaged prompt."""
    monkeypatch.setattr(
        "agents.brook.context_bridge.search_kb",
        lambda query, vault_path, top_k=5: [],
    )
    src_slug = "book-x"
    (tmp_path / "KB" / "Wiki" / "Sources" / src_slug).mkdir(parents=True)
    (tmp_path / "KB" / "Wiki" / "Sources" / src_slug / "digest.md").write_text(
        "## 摘要\nThe digest content for book x.\n",
        encoding="utf-8",
    )
    (tmp_path / "KB" / "Annotations" / f"{src_slug}.md").write_text(
        "annotation-1\nannotation-2\n",
        encoding="utf-8",
    )

    r = client.get(f"/brook/bridge?topic=主題&source_slug={src_slug}")
    assert r.status_code == 200
    body = r.text
    assert "Reading-Context-Package" in body
    assert "book-x" in body
    assert "digest content" in body


def test_bridge_includes_style_profile_section(client, monkeypatch):
    """Style profile section renders when a category can be detected or
    when explicitly overridden via the ``category`` query param."""
    monkeypatch.setattr(
        "agents.brook.context_bridge.search_kb",
        lambda query, vault_path, top_k=5: [],
    )
    # Force a known category to avoid relying on detect_category heuristics.
    r = client.get("/brook/bridge?topic=任意主題&category=book-review")
    assert r.status_code == 200
    body = r.text
    # Style profile section present + summary chip filled in
    assert "風格側寫" in body
    # profile_id pattern slug@M.m.p — book-review profile uses this
    assert "book-review" in body


def test_bridge_compliance_vocab_reminder_section_present(client, monkeypatch):
    monkeypatch.setattr(
        "agents.brook.context_bridge.search_kb",
        lambda query, vault_path, top_k=5: [],
    )
    r = client.get("/brook/bridge?topic=任何主題")
    body = r.text
    assert "合規提醒" in body
    assert "藥事法" in body
    # Reminder is *not* enforcement — copy explicitly says reminder
    assert "reminder" in body.lower() or "提醒" in body


def test_bridge_handler_does_not_call_any_llm(client, monkeypatch):
    """ADR-027 invariant: the bridge MUST NOT call any LLM. Stub the LLM
    surfaces and assert zero calls after a full packaging round-trip."""
    calls: list[tuple] = []

    def _fail(*args, **kwargs):  # noqa: ANN001 — any signature
        calls.append((args, kwargs))
        raise AssertionError("LLM must not be called from /brook/bridge")

    # Stub every plausible LLM entry point. ImportError on monkeypatch.setattr
    # means the path doesn't exist — that's fine, we only care nothing under
    # context_bridge.py reaches one of these.
    for path in [
        "shared.llm.ask_multi",
        "shared.llm.ask",
        "agents.brook.context_bridge.search_kb",  # also stubbed → returns []
    ]:
        try:
            if path.endswith("search_kb"):
                monkeypatch.setattr(path, lambda *a, **kw: [])
            else:
                monkeypatch.setattr(path, _fail)
        except AttributeError:
            pass

    r = client.get("/brook/bridge?topic=任何主題&category=book-review")
    assert r.status_code == 200
    assert calls == [], f"LLM was called from bridge: {calls}"


def test_bridge_kb_search_failure_does_not_500(client, monkeypatch):
    """KB unreachable → bridge soft-fails to 0 chunks, page still renders."""

    def _boom(*a, **kw):
        raise RuntimeError("vault unreachable")

    monkeypatch.setattr("agents.brook.context_bridge.search_kb", _boom)

    r = client.get("/brook/bridge?topic=主題")
    assert r.status_code == 200
    assert "已打包內容" in r.text
