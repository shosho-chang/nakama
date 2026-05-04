"""Tests for thousand_sunny.routers.robin — KB ingest UI / reader / session routes.

Scope：
- Auth gates（redirect to /login when cookie 無效）
- Helper functions: _send_to_recycle_bin, session store, _get_inbox_files, _resolve_reader_base
- Non-SSE routes：index / read / files / save-annotations / mark-read /
  scrape-translate / start / cancel / processing / review-summary /
  submit-guidance / review-plan / execute / done / kb/research
- SSE `events` route 留下一輪測（session state × async stream 交互複雜，
  獨立 PR 處理）

依 feedback_pytest_monkeypatch_where_used — monkeypatch 到 robin router 模組
本身讀名字的 namespace，不是原始定義處。
"""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def vault(tmp_path: Path, monkeypatch) -> Path:
    """Redirect vault path to tmp_path 供 _get_inbox / _get_sources 使用。"""
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    # shared.config.get_vault_path 讀 env；reload 以清 cache（如果有）
    import shared.config as cfg

    importlib.reload(cfg)
    return tmp_path


@pytest.fixture
def client(vault, monkeypatch):
    """TestClient with dev-mode auth（WEB_PASSWORD / WEB_SECRET 未設，check_auth 放行）。"""
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)

    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.robin as robin_module

    importlib.reload(auth_module)
    importlib.reload(robin_module)

    app = FastAPI()
    app.include_router(robin_module.router)

    from fastapi.responses import PlainTextResponse

    @app.get("/login")
    def login(next: str = ""):
        return PlainTextResponse(f"login next={next}")

    return TestClient(app, follow_redirects=False), robin_module


@pytest.fixture
def auth_client(client, monkeypatch):
    """WEB_PASSWORD / WEB_SECRET 有設的 client — 需要 cookie 才能通過。"""
    monkeypatch.setenv("WEB_PASSWORD", "testpw")
    monkeypatch.setenv("WEB_SECRET", "testsecret")

    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.robin as robin_module

    importlib.reload(auth_module)
    importlib.reload(robin_module)

    app = FastAPI()
    app.include_router(robin_module.router)

    from fastapi.responses import PlainTextResponse

    @app.get("/login")
    def login(next: str = ""):
        return PlainTextResponse(f"login next={next}")

    tc = TestClient(app, follow_redirects=False)

    from thousand_sunny.auth import make_token

    cookies = {"nakama_auth": make_token("testpw")}
    return tc, robin_module, cookies


# ---------------------------------------------------------------------------
# Helpers — _send_to_recycle_bin / session store / _get_inbox_files
# ---------------------------------------------------------------------------


def test_send_to_recycle_bin_linux(tmp_path, monkeypatch):
    """非 Windows 直接 unlink。"""
    import thousand_sunny.routers.robin as robin_module

    monkeypatch.setattr(robin_module.platform, "system", lambda: "Linux")
    f = tmp_path / "foo.txt"
    f.write_text("x")
    robin_module._send_to_recycle_bin(f)
    assert not f.exists()


def test_send_to_recycle_bin_linux_missing_ok(tmp_path, monkeypatch):
    """Linux 路徑不存在不應 raise。"""
    import thousand_sunny.routers.robin as robin_module

    monkeypatch.setattr(robin_module.platform, "system", lambda: "Linux")
    robin_module._send_to_recycle_bin(tmp_path / "nonexistent.txt")  # no raise


def test_send_to_recycle_bin_windows_invokes_powershell(tmp_path, monkeypatch):
    import thousand_sunny.routers.robin as robin_module

    monkeypatch.setattr(robin_module.platform, "system", lambda: "Windows")
    captured = {}

    def fake_run(args, check):
        captured["args"] = args
        captured["check"] = check
        return MagicMock(returncode=0)

    monkeypatch.setattr(robin_module.subprocess, "run", fake_run)
    f = tmp_path / "foo.txt"
    f.write_text("x")
    robin_module._send_to_recycle_bin(f)
    assert captured["args"][0] == "powershell"
    assert "SendToRecycleBin" in captured["args"][2]
    assert captured["check"] is False


def test_session_store_new_and_get(client):
    _, mod = client
    sid = mod._new_session(step="summarizing", foo="bar")
    s = mod._get_session(sid)
    assert s["step"] == "summarizing"
    assert s["foo"] == "bar"
    assert "created_at" in s


def test_get_session_none_returns_none(client):
    _, mod = client
    assert mod._get_session(None) is None
    assert mod._get_session("nonexistent-sid") is None


def test_session_cleanup_expires_old_entries(client, monkeypatch):
    _, mod = client
    old_sid = mod._new_session(step="old")
    # 將 created_at 改到過期
    mod.sessions[old_sid]["created_at"] = 0  # epoch
    new_sid = mod._new_session(step="new")  # 這個呼叫也會 cleanup
    assert old_sid not in mod.sessions
    assert new_sid in mod.sessions


def test_get_inbox_files_empty_when_dir_missing(client):
    _, mod = client
    assert mod._get_inbox_files() == []


def test_get_inbox_files_lists_supported_extensions(client, vault):
    _, mod = client
    from agents.robin.agent import EXTENSION_TO_RAW_DIR

    inbox = vault / "Inbox" / "kb"
    inbox.mkdir(parents=True)
    # Pick any supported extension
    ext = next(iter(EXTENSION_TO_RAW_DIR.keys()))
    supported = inbox / f"foo{ext}"
    supported.write_text("hello")
    unsupported = inbox / "bar.unsupported"
    unsupported.write_text("x")

    files = mod._get_inbox_files()
    names = [f["name"] for f in files]
    assert f"foo{ext}" in names
    assert "bar.unsupported" not in names


def test_get_inbox_files_small_file_shows_bytes(client, vault):
    """size_kb 為 0 → 顯示 bytes 而非 KB。"""
    _, mod = client
    from agents.robin.agent import EXTENSION_TO_RAW_DIR

    inbox = vault / "Inbox" / "kb"
    inbox.mkdir(parents=True)
    ext = next(iter(EXTENSION_TO_RAW_DIR.keys()))
    small = inbox / f"small{ext}"
    small.write_bytes(b"x")  # 1 byte

    files = mod._get_inbox_files()
    assert any("B" in f["size"] and "KB" not in f["size"] for f in files)


def test_resolve_reader_base_inbox(client):
    _, mod = client
    p = mod._resolve_reader_base("inbox")
    assert p.name == "kb"


def test_resolve_reader_base_sources(client):
    _, mod = client
    p = mod._resolve_reader_base("sources")
    assert p.name == "Sources"


def test_resolve_reader_base_rejects_unknown(client):
    from fastapi import HTTPException

    _, mod = client
    with pytest.raises(HTTPException) as exc:
        mod._resolve_reader_base("etc")
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------


def test_index_dev_mode_returns_html(client):
    tc, _ = client
    r = tc.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


def test_index_redirects_when_auth_required_no_cookie(auth_client):
    tc, _, _ = auth_client
    r = tc.get("/")
    assert r.status_code == 302
    assert "/login" in r.headers["location"]


# ---------------------------------------------------------------------------
# GET /read
# ---------------------------------------------------------------------------


def test_read_source_unauth_redirect(auth_client):
    tc, _, _ = auth_client
    r = tc.get("/read", params={"file": "foo.md"})
    assert r.status_code == 302


def test_read_source_missing_file_404(client, vault):
    tc, _ = client
    (vault / "Inbox" / "kb").mkdir(parents=True)
    r = tc.get("/read", params={"file": "nonexistent.md"})
    assert r.status_code == 404


def test_read_source_unsupported_extension_400(client, vault, monkeypatch):
    tc, mod = client
    # Stub fetch_images to avoid network
    monkeypatch.setattr(mod, "fetch_images", lambda p: 0)
    inbox = vault / "Inbox" / "kb"
    inbox.mkdir(parents=True)
    (inbox / "foo.pdf").write_bytes(b"fake pdf")

    r = tc.get("/read", params={"file": "foo.pdf"})
    assert r.status_code == 400


def test_read_source_happy_path_md(client, vault, monkeypatch):
    tc, mod = client
    monkeypatch.setattr(mod, "fetch_images", lambda p: 2)  # 觸發 logger.info 分支
    inbox = vault / "Inbox" / "kb"
    inbox.mkdir(parents=True)
    (inbox / "foo.md").write_text("---\ntitle: Foo\n---\nbody content", encoding="utf-8")

    r = tc.get("/read", params={"file": "foo.md"})
    assert r.status_code == 200
    # Slug and empty annotations injected into page
    assert "foo" in r.text  # slug derived from filename
    assert "annotationsData" in r.text or "[]" in r.text  # JS array present


def test_read_source_passes_existing_annotations(client, vault, monkeypatch):
    """Existing annotations are loaded from KB/Annotations/ and injected into the page."""
    import importlib

    import shared.annotation_store as ann_mod
    import thousand_sunny.routers.robin as robin_mod

    monkeypatch.setenv("VAULT_PATH", str(vault))
    importlib.reload(ann_mod)
    importlib.reload(robin_mod)

    monkeypatch.setattr(robin_mod, "fetch_images", lambda p: 0)

    inbox = vault / "Inbox" / "kb"
    inbox.mkdir(parents=True)
    (inbox / "bar.md").write_text("# Bar\n\nHello world", encoding="utf-8")

    # Pre-populate annotation store
    store = ann_mod.AnnotationStore()
    store.save(
        ann_mod.AnnotationSet(
            slug="bar",
            source_filename="bar.md",
            base="inbox",
            items=[ann_mod.Highlight(text="Hello world", created_at="2026-01-01T00:00:00Z")],
            updated_at="2026-01-01T00:00:00Z",
        )
    )

    # Reload router so it picks up the patched vault path
    app2 = __import__("fastapi", fromlist=["FastAPI"]).FastAPI()
    app2.include_router(robin_mod.router)
    from fastapi.testclient import TestClient as TC2

    tc2 = TC2(app2, follow_redirects=False)

    r = tc2.get("/read", params={"file": "bar.md"})
    assert r.status_code == 200
    assert "Hello world" in r.text


def test_read_source_without_frontmatter(client, vault, monkeypatch):
    """frontmatter 為空 dict → frontmatter_raw 為空字串分支。"""
    tc, mod = client
    monkeypatch.setattr(mod, "fetch_images", lambda p: 0)
    inbox = vault / "Inbox" / "kb"
    inbox.mkdir(parents=True)
    (inbox / "plain.md").write_text("just plain text, no fm", encoding="utf-8")

    r = tc.get("/read", params={"file": "plain.md"})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# GET /files/{path}
# ---------------------------------------------------------------------------


def test_serve_vault_file_auth_required_returns_403(auth_client):
    tc, _, _ = auth_client
    r = tc.get("/files/foo.png")
    assert r.status_code == 403


def test_serve_vault_file_serves_from_files_dir(client, vault):
    tc, _ = client
    files_dir = vault / "Files"
    files_dir.mkdir()
    (files_dir / "img.png").write_bytes(b"\x89PNG")

    r = tc.get("/files/img.png")
    assert r.status_code == 200


def test_serve_vault_file_fallback_to_vault_root(client, vault):
    """Files/ 沒有 → fallback 到 vault root。"""
    tc, _ = client
    (vault / "root.png").write_bytes(b"\x89PNG")

    r = tc.get("/files/root.png")
    assert r.status_code == 200


def test_serve_vault_file_not_found_404(client, vault):
    tc, _ = client
    (vault / "Files").mkdir()

    r = tc.get("/files/missing.png")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /save-annotations  (JSON endpoint — ADR-017)
# ---------------------------------------------------------------------------

_ANN_PAYLOAD = {
    "slug": "doc",
    "source_filename": "doc.md",
    "base": "inbox",
    "items": [{"type": "highlight", "text": "hello", "created_at": "2026-01-01T00:00:00Z"}],
    "updated_at": "2026-01-01T00:00:00Z",
}


def test_save_annotations_auth_required(auth_client):
    tc, _, _ = auth_client
    r = tc.post("/save-annotations", json=_ANN_PAYLOAD)
    assert r.status_code == 403


def test_save_annotations_unknown_base_400(client, vault):
    tc, _ = client
    payload = {**_ANN_PAYLOAD, "base": "unknown-base"}
    r = tc.post("/save-annotations", json=payload)
    assert r.status_code == 400


def test_save_annotations_writes_to_kb_annotations(client, vault, monkeypatch):
    """Saves AnnotationSet to KB/Annotations/{slug}.md; source file NOT mutated."""
    import importlib

    import shared.annotation_store as ann_mod
    import thousand_sunny.routers.robin as robin_mod

    monkeypatch.setenv("VAULT_PATH", str(vault))
    importlib.reload(ann_mod)
    importlib.reload(robin_mod)

    tc, _ = client

    r = tc.post("/save-annotations", json=_ANN_PAYLOAD)
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}

    ann_file = vault / "KB" / "Annotations" / "doc.md"
    assert ann_file.exists(), "annotation file must be created in KB/Annotations/"
    content = ann_file.read_text("utf-8")
    assert "hello" in content
    assert "highlight" in content


def test_save_annotations_does_not_mutate_source(client, vault, monkeypatch):
    """Original source file must remain unchanged after save."""
    import importlib

    import shared.annotation_store as ann_mod
    import thousand_sunny.routers.robin as robin_mod

    monkeypatch.setenv("VAULT_PATH", str(vault))
    importlib.reload(ann_mod)
    importlib.reload(robin_mod)

    inbox = vault / "Inbox" / "kb"
    inbox.mkdir(parents=True)
    source_text = "# Pure Source\n\nSome content here."
    (inbox / "doc.md").write_text(source_text, encoding="utf-8")

    tc, _ = client
    tc.post("/save-annotations", json=_ANN_PAYLOAD)

    assert (inbox / "doc.md").read_text("utf-8") == source_text


def test_save_annotations_sources_base(client, vault, monkeypatch):
    """base=sources is also accepted and writes to KB/Annotations/."""
    import importlib

    import shared.annotation_store as ann_mod
    import thousand_sunny.routers.robin as robin_mod

    monkeypatch.setenv("VAULT_PATH", str(vault))
    importlib.reload(ann_mod)
    importlib.reload(robin_mod)

    (vault / "KB" / "Wiki" / "Sources").mkdir(parents=True)
    payload = {**_ANN_PAYLOAD, "base": "sources", "slug": "src-doc"}

    tc, _ = client
    r = tc.post("/save-annotations", json=payload)
    assert r.status_code == 200
    assert (vault / "KB" / "Annotations" / "src-doc.md").exists()


# ---------------------------------------------------------------------------
# POST /mark-read
# ---------------------------------------------------------------------------


def test_mark_read_auth_required(auth_client):
    tc, _, _ = auth_client
    r = tc.post("/mark-read", data={"filename": "x.md"})
    assert r.status_code == 403


def test_mark_read_missing_file_404(client, vault):
    tc, _ = client
    (vault / "Inbox" / "kb").mkdir(parents=True)
    r = tc.post("/mark-read", data={"filename": "missing.md"})
    assert r.status_code == 404


def test_mark_read_happy_path(client, vault, monkeypatch):
    tc, mod = client
    captured = {}
    monkeypatch.setattr(mod, "mark_file_read", lambda p: captured.setdefault("path", p))
    inbox = vault / "Inbox" / "kb"
    inbox.mkdir(parents=True)
    (inbox / "foo.md").write_text("x", encoding="utf-8")

    r = tc.post("/mark-read", data={"filename": "foo.md"})
    assert r.status_code == 200
    assert captured["path"].name == "foo.md"


# ---------------------------------------------------------------------------
# POST /scrape-translate
# ---------------------------------------------------------------------------


def test_scrape_translate_unauth_redirect(auth_client):
    tc, _, _ = auth_client
    r = tc.post("/scrape-translate", data={"url": "https://example.com/"})
    assert r.status_code == 302


def test_scrape_translate_invalid_types_fallback_defaults(client, vault, monkeypatch):
    """source_type / content_nature 非 allowlist 值 → 退回預設。"""
    tc, mod = client

    def fake_scrape(url):
        return "raw page content"

    def fake_translate(text):
        return f"bilingual:{text}"

    monkeypatch.setattr("shared.web_scraper.scrape_url", fake_scrape)
    monkeypatch.setattr("shared.translator.translate_document", fake_translate)

    (vault / "Inbox" / "kb").mkdir(parents=True)

    r = tc.post(
        "/scrape-translate",
        data={
            "url": "https://example.com/path",
            "source_type": "INVALID",
            "content_nature": "INVALID",
        },
    )
    assert r.status_code == 303
    # Check written file contains defaults
    files = list((vault / "Inbox" / "kb").glob("*.md"))
    assert len(files) == 1
    content = files[0].read_text("utf-8")
    assert "source_type: article" in content
    assert "content_nature: popular_science" in content


def test_scrape_translate_filename_collision_adds_counter(client, vault, monkeypatch):
    tc, mod = client
    monkeypatch.setattr("shared.web_scraper.scrape_url", lambda u: "raw")
    monkeypatch.setattr("shared.translator.translate_document", lambda t: f"bi:{t}")

    inbox = vault / "Inbox" / "kb"
    inbox.mkdir(parents=True)
    # Seed filename collision — let's predict slug then create it
    from urllib.parse import urlparse

    from shared.utils import slugify

    parsed = urlparse("https://example.com/foo")
    expected_slug = slugify(parsed.netloc + parsed.path)[:60] or "scraped"
    (inbox / f"{expected_slug}.md").write_text("existing", encoding="utf-8")

    r = tc.post("/scrape-translate", data={"url": "https://example.com/foo"})
    assert r.status_code == 303
    # Counter suffix file should exist
    counter_files = list(inbox.glob(f"{expected_slug}-*.md"))
    assert len(counter_files) == 1


def test_scrape_translate_scrape_failure_returns_422(client, vault, monkeypatch):
    tc, mod = client

    def fake_scrape(url):
        raise RuntimeError("boom")

    monkeypatch.setattr("shared.web_scraper.scrape_url", fake_scrape)
    (vault / "Inbox" / "kb").mkdir(parents=True)

    r = tc.post("/scrape-translate", data={"url": "https://example.com/"})
    assert r.status_code == 422


def test_scrape_translate_translate_failure_keeps_original(client, vault, monkeypatch):
    """translate 失敗 → bilingual_md 退回 raw_text（line 267）。"""
    tc, mod = client
    monkeypatch.setattr("shared.web_scraper.scrape_url", lambda u: "raw page")

    def fake_translate(text):
        raise RuntimeError("translate boom")

    monkeypatch.setattr("shared.translator.translate_document", fake_translate)
    (vault / "Inbox" / "kb").mkdir(parents=True)

    r = tc.post("/scrape-translate", data={"url": "https://example.com/"})
    assert r.status_code == 303
    files = list((vault / "Inbox" / "kb").glob("*.md"))
    content = files[0].read_text("utf-8")
    assert "raw page" in content  # fallback 保留 raw


# ---------------------------------------------------------------------------
# GET /pubmed-to-reader — smoke only (深路徑已被 tests/test_pubmed_to_reader_route.py 涵蓋)
# ---------------------------------------------------------------------------


def test_pubmed_to_reader_invalid_pmid_returns_400(client):
    tc, _ = client
    r = tc.get("/pubmed-to-reader", params={"pmid": "not-a-number"})
    assert r.status_code == 400


def test_pubmed_to_reader_unauth_redirect(auth_client):
    tc, _, _ = auth_client
    r = tc.get("/pubmed-to-reader", params={"pmid": "12345"})
    assert r.status_code == 302


def test_pubmed_to_reader_no_source_returns_404(client, vault):
    tc, _ = client
    # Neither PDF nor md exists
    r = tc.get("/pubmed-to-reader", params={"pmid": "99999"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /start
# ---------------------------------------------------------------------------


def test_start_unauth_redirect(auth_client):
    tc, _, _ = auth_client
    r = tc.post("/start", data={"filename": "foo.md"})
    assert r.status_code == 302


def test_start_missing_file_404(client, vault):
    tc, _ = client
    (vault / "Inbox" / "kb").mkdir(parents=True)
    r = tc.post("/start", data={"filename": "missing.md"})
    assert r.status_code == 404


def test_start_happy_path_creates_session(client, vault):
    tc, mod = client
    inbox = vault / "Inbox" / "kb"
    inbox.mkdir(parents=True)
    (inbox / "foo.md").write_text("content", encoding="utf-8")

    r = tc.post("/start", data={"filename": "foo.md", "source_type": "article"})
    assert r.status_code == 302
    assert r.headers["location"] == "/processing"
    assert "robin_session" in r.headers.get("set-cookie", "")
    # Raw copy 落地
    raw_files = list((vault / "KB" / "Raw").rglob("foo.md"))
    assert len(raw_files) == 1
    # Session state
    assert len(mod.sessions) >= 1


# ---------------------------------------------------------------------------
# POST /cancel
# ---------------------------------------------------------------------------


def test_cancel_unauth_redirect(auth_client):
    tc, _, _ = auth_client
    r = tc.post("/cancel")
    assert r.status_code == 302


def test_cancel_no_session_returns_home(client):
    tc, _ = client
    r = tc.post("/cancel")
    assert r.status_code == 302
    assert r.headers["location"] == "/"


def test_cancel_marks_session_cancelled_and_cleans_raw(client, vault, monkeypatch):
    tc, mod = client
    raw = vault / "raw_file.md"
    raw.write_text("x")
    sid = mod._new_session(step="summarizing", raw_path=str(raw), summary_path="")

    monkeypatch.setattr(mod.platform, "system", lambda: "Linux")
    tc.cookies.set("robin_session", sid)
    r = tc.post("/cancel")
    assert r.status_code == 302
    assert mod.sessions[sid]["step"] == "cancelled"
    assert not raw.exists()  # recycle bin 刪掉


def test_cancel_keeps_raw_if_summary_already_written(client, vault):
    """summary_path 已設 → 不清 raw 檔。"""
    tc, mod = client
    raw = vault / "raw_file.md"
    raw.write_text("x")
    sid = mod._new_session(
        step="summarizing", raw_path=str(raw), summary_path="KB/Wiki/Sources/x.md"
    )

    tc.cookies.set("robin_session", sid)
    r = tc.post("/cancel")
    assert r.status_code == 302
    assert raw.exists()  # not cleaned


# ---------------------------------------------------------------------------
# GET /processing
# ---------------------------------------------------------------------------


def test_processing_unauth_redirect(auth_client):
    tc, _, _ = auth_client
    r = tc.get("/processing")
    assert r.status_code == 302


def test_processing_no_session_redirects_home(client):
    tc, _ = client
    r = tc.get("/processing")
    assert r.status_code == 302
    assert r.headers["location"] == "/"


def test_processing_renders_label_for_known_step(client):
    tc, mod = client
    sid = mod._new_session(step="summarizing")
    tc.cookies.set("robin_session", sid)
    r = tc.get("/processing")
    assert r.status_code == 200
    assert "Robin 正在閱讀" in r.text


def test_processing_unknown_step_uses_default_label(client):
    tc, mod = client
    sid = mod._new_session(step="weird_step")
    tc.cookies.set("robin_session", sid)
    r = tc.get("/processing")
    assert r.status_code == 200
    assert "處理中" in r.text


# ---------------------------------------------------------------------------
# GET /review-summary
# ---------------------------------------------------------------------------


def test_review_summary_unauth_redirect(auth_client):
    tc, _, _ = auth_client
    r = tc.get("/review-summary")
    assert r.status_code == 302


def test_review_summary_wrong_step_redirects_home(client):
    tc, mod = client
    sid = mod._new_session(step="summarizing")
    tc.cookies.set("robin_session", sid)
    r = tc.get("/review-summary")
    assert r.status_code == 302
    assert r.headers["location"] == "/"


def test_review_summary_happy_path(client):
    tc, mod = client
    sid = mod._new_session(
        step="awaiting_guidance", file_name="foo.md", summary_body="This is the summary"
    )
    tc.cookies.set("robin_session", sid)
    r = tc.get("/review-summary")
    assert r.status_code == 200
    assert "This is the summary" in r.text


# ---------------------------------------------------------------------------
# POST /submit-guidance
# ---------------------------------------------------------------------------


def test_submit_guidance_unauth_redirect(auth_client):
    tc, _, _ = auth_client
    r = tc.post("/submit-guidance", data={"guidance": "focus on X"})
    assert r.status_code == 302


def test_submit_guidance_no_session_redirects_home(client):
    tc, _ = client
    r = tc.post("/submit-guidance", data={"guidance": "focus"})
    assert r.status_code == 302
    assert r.headers["location"] == "/"


def test_submit_guidance_transitions_to_planning(client):
    tc, mod = client
    sid = mod._new_session(step="awaiting_guidance")
    tc.cookies.set("robin_session", sid)
    r = tc.post("/submit-guidance", data={"guidance": "  my guidance  "})
    assert r.status_code == 302
    assert r.headers["location"] == "/processing"
    assert mod.sessions[sid]["step"] == "planning"
    assert mod.sessions[sid]["user_guidance"] == "my guidance"  # stripped


# ---------------------------------------------------------------------------
# GET /review-plan
# ---------------------------------------------------------------------------


def test_review_plan_unauth_redirect(auth_client):
    tc, _, _ = auth_client
    r = tc.get("/review-plan")
    assert r.status_code == 302


def test_review_plan_wrong_step_redirects_home(client):
    tc, mod = client
    sid = mod._new_session(step="summarizing")
    tc.cookies.set("robin_session", sid)
    r = tc.get("/review-plan")
    assert r.status_code == 302


def test_review_plan_happy_path(client):
    tc, mod = client
    plan = {
        "concepts": [{"slug": "concept-a", "action": "create", "title": "Concept A"}],
        "entities": [{"title": "Existing", "entity_type": "person"}],
    }
    sid = mod._new_session(step="awaiting_approval", file_name="foo.md", plan=plan)
    tc.cookies.set("robin_session", sid)
    r = tc.get("/review-plan")
    assert r.status_code == 200
    assert "Concept A" in r.text


# ---------------------------------------------------------------------------
# POST /execute
# ---------------------------------------------------------------------------


def test_execute_unauth_redirect(auth_client):
    tc, _, _ = auth_client
    r = tc.post("/execute", data={})
    assert r.status_code == 302


def test_execute_no_session_redirects_home(client):
    tc, _ = client
    r = tc.post("/execute", data={})
    assert r.status_code == 302
    assert r.headers["location"] == "/"


def test_execute_filters_selected_items(client):
    tc, mod = client
    plan = {
        "concepts": [
            {"slug": "a", "action": "create", "title": "A"},
            {"slug": "b", "action": "update_merge", "title": "B"},
            {"slug": "c", "action": "noop", "title": "C"},
        ],
        "entities": [
            {"title": "U1", "entity_type": "person"},
            {"title": "U2", "entity_type": "tool"},
        ],
    }
    sid = mod._new_session(step="awaiting_approval", plan=plan)
    tc.cookies.set("robin_session", sid)
    r = tc.post("/execute", data={"concept": ["0", "2"], "entity": ["1"]})
    assert r.status_code == 302
    assert r.headers["location"] == "/processing"
    final_plan = mod.sessions[sid]["plan"]
    assert [c["title"] for c in final_plan["concepts"]] == ["A", "C"]
    assert [e["title"] for e in final_plan["entities"]] == ["U2"]
    assert mod.sessions[sid]["step"] == "executing"


def test_execute_ignores_invalid_indices(client):
    """非數字或超界 index 應被略過。"""
    tc, mod = client
    plan = {"concepts": [{"slug": "a", "action": "create", "title": "A"}], "entities": []}
    sid = mod._new_session(step="awaiting_approval", plan=plan)
    tc.cookies.set("robin_session", sid)
    r = tc.post(
        "/execute",
        data={"concept": ["0", "99", "abc"]},
    )
    assert r.status_code == 302
    assert len(mod.sessions[sid]["plan"]["concepts"]) == 1  # only "A"


# ---------------------------------------------------------------------------
# GET /done
# ---------------------------------------------------------------------------


def test_done_unauth_redirect(auth_client):
    tc, _, _ = auth_client
    r = tc.get("/done")
    assert r.status_code == 302


def test_done_wrong_step_redirects_home(client):
    tc, mod = client
    sid = mod._new_session(step="executing")
    tc.cookies.set("robin_session", sid)
    r = tc.get("/done")
    assert r.status_code == 302


def test_done_happy_path(client):
    tc, mod = client
    sid = mod._new_session(
        step="done",
        file_name="foo.md",
        result={"created": ["A", "B"], "updated": ["U1"]},
    )
    tc.cookies.set("robin_session", sid)
    r = tc.get("/done")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# POST /kb/research
# ---------------------------------------------------------------------------


def test_kb_research_returns_results(client, monkeypatch):
    tc, mod = client
    monkeypatch.setattr(mod, "search_kb", lambda q, vault_path: [{"title": "hit", "score": 0.9}])
    r = tc.post("/kb/research", data={"query": "sleep"})
    assert r.status_code == 200
    body = r.json()
    assert body["results"][0]["title"] == "hit"
