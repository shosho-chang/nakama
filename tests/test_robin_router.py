"""Tests for thousand_sunny.routers.robin — KB ingest UI / reader / session routes.

Scope：
- Auth gates（redirect to /login when cookie 無效）
- Helper functions: _send_to_recycle_bin, session store, _get_inbox_files, _resolve_reader_base
- Non-SSE routes：index / read / files / save-annotations / mark-read /
  start / cancel / processing / kb/research（review-summary / submit-guidance /
  review-plan / execute / done 等中途 HITL gate 已隨自動化 ingest 移除，ADR-043）
- SSE `events` 自動流程（summarizing→planning→executing→開卡建議）見
  test_robin_router_sse.py

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
    app.include_router(robin_module.robin_router)
    app.include_router(robin_module.legacy_router)

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
    app.include_router(robin_module.robin_router)
    app.include_router(robin_module.legacy_router)

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

    inbox = vault / "Inbox" / "web"
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


def test_get_inbox_files_extracts_title_from_frontmatter(client, vault):
    """frontmatter.title 應該被 surface 到 file dict 給 inbox row 顯示。

    Bug context (2026-05-04 smoke):
    URL ingest 寫入時 frontmatter title 是真實標題（"Physical activity types..."），
    但 index.html 顯示的是 file.name（slug），User 看到 ugly 檔名以為標題沒抓到。
    這個 test 守住 _get_inbox_files 必須暴露 frontmatter.title 給 template 用。
    """
    _, mod = client
    inbox = vault / "Inbox" / "web"
    inbox.mkdir(parents=True)
    pretty = inbox / "uglyslug.md"
    pretty.write_text(
        '---\ntitle: "Physical activity and mortality"\nfulltext_status: ready\n---\n\nbody\n',
        encoding="utf-8",
    )
    bare = inbox / "no-frontmatter.md"
    bare.write_text("just text\n", encoding="utf-8")

    files = {f["name"]: f for f in mod._get_inbox_files()}
    assert files["uglyslug.md"]["title"] == "Physical activity and mortality"
    # No frontmatter → empty title (template falls back to file.name).
    assert files["no-frontmatter.md"]["title"] == ""


def test_get_inbox_files_synthesizes_status_for_web_clipper(client, vault):
    """Obsidian Web Clipper files (no fulltext_status, tags=[clippings]) get
    a synthesised display row with status='ready' + source='Web Clipper' so
    the inbox list shows them with the ✅ icon and a meaningful source label.
    """
    _, mod = client
    inbox = vault / "Inbox" / "web"
    inbox.mkdir(parents=True)
    clipped = inbox / "clipped-paper.md"
    clipped.write_text(
        "---\n"
        'title: "Clipped Paper"\n'
        'source: "https://example.com/paper"\n'
        "tags:\n"
        "  - clippings\n"
        "---\n\nbody content\n",
        encoding="utf-8",
    )

    files = {f["name"]: f for f in mod._get_inbox_files()}
    row = files["clipped-paper.md"]
    assert row["fulltext_status"] == "ready"
    assert row["fulltext_source"] == "Web Clipper"
    assert row["title"] == "Clipped Paper"


def test_looks_like_web_clipper_detects_tags_list(client):
    _, mod = client
    assert mod._looks_like_web_clipper({"tags": ["clippings", "research"]}) is True


def test_looks_like_web_clipper_detects_tags_string(client):
    _, mod = client
    assert mod._looks_like_web_clipper({"tags": "clippings"}) is True


def test_looks_like_web_clipper_falls_back_to_source_without_original_url(client):
    """Web Clipper variants with custom tag templates still get caught by the
    'source without original_url' permissive fallback."""
    _, mod = client
    assert mod._looks_like_web_clipper({"source": "https://example.com/x"}) is True


def test_looks_like_web_clipper_rejects_robin_files(client):
    """Robin-written files have BOTH source + original_url — must NOT trip
    the Web Clipper detector (would synthesise the wrong source label)."""
    _, mod = client
    fm = {
        "source": "https://example.com/x",
        "original_url": "https://example.com/x",
        "fulltext_status": "ready",
    }
    assert mod._looks_like_web_clipper(fm) is False


def test_get_inbox_files_does_not_overwrite_explicit_status(client, vault):
    """File with explicit fulltext_status is not touched by the Web Clipper
    synthesiser (status stays whatever the user / pipeline wrote)."""
    _, mod = client
    inbox = vault / "Inbox" / "web"
    inbox.mkdir(parents=True)
    f = inbox / "explicit.md"
    f.write_text(
        "---\n"
        'title: "Explicit"\n'
        'source: "https://example.com/x"\n'
        "tags:\n  - clippings\n"
        "fulltext_status: failed\n"
        "---\nbody\n",
        encoding="utf-8",
    )

    files = {x["name"]: x for x in mod._get_inbox_files()}
    assert files["explicit.md"]["fulltext_status"] == "failed"


def test_get_inbox_files_small_file_shows_bytes(client, vault):
    """size_kb 為 0 → 顯示 bytes 而非 KB。"""
    _, mod = client
    from agents.robin.agent import EXTENSION_TO_RAW_DIR

    inbox = vault / "Inbox" / "web"
    inbox.mkdir(parents=True)
    ext = next(iter(EXTENSION_TO_RAW_DIR.keys()))
    small = inbox / f"small{ext}"
    small.write_bytes(b"x")  # 1 byte

    files = mod._get_inbox_files()
    assert any("B" in f["size"] and "KB" not in f["size"] for f in files)


def test_resolve_reader_base_inbox(client):
    _, mod = client
    p = mod._resolve_reader_base("inbox")
    assert p.name == "web"


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


def test_index_redirects_to_weekly(client):
    """`/` 一律重導到週看板（首頁＝修修每日落點）。Robin 收件匣改住 /robin，
    所以這裡無條件 302 不會孤兒。與 VPS 模式（app.py else 分支）對齊。"""
    tc, _ = client
    r = tc.get("/")
    assert r.status_code == 302
    assert r.headers["location"] == "/bridge/weekly"


def test_robin_inbox_redirects_when_auth_required_no_cookie(auth_client):
    """登入守門：`/` 已改為無條件重導 /bridge/weekly，所以未登入的 /login 守門
    移到各實際頁面 —— Robin 收件匣 /robin 未登入應帶 ?next=/robin redirect 到 /login。"""
    tc, _, _ = auth_client
    r = tc.get("/robin")
    assert r.status_code == 302
    assert "/login" in r.headers["location"]


# ---------------------------------------------------------------------------
# GET /read
# ---------------------------------------------------------------------------


def test_read_source_unauth_redirect(auth_client):
    tc, _, _ = auth_client
    r = tc.get("/robin/read", params={"file": "foo.md"})
    assert r.status_code == 302


def test_read_source_missing_file_404(client, vault):
    tc, _ = client
    (vault / "Inbox" / "web").mkdir(parents=True)
    r = tc.get("/robin/read", params={"file": "nonexistent.md"})
    assert r.status_code == 404


def test_read_source_unsupported_extension_400(client, vault, monkeypatch):
    tc, mod = client
    # Stub fetch_images to avoid network
    monkeypatch.setattr(mod, "fetch_images", lambda p: 0)
    inbox = vault / "Inbox" / "web"
    inbox.mkdir(parents=True)
    (inbox / "foo.pdf").write_bytes(b"fake pdf")

    r = tc.get("/robin/read", params={"file": "foo.pdf"})
    assert r.status_code == 400


def test_read_source_happy_path_md(client, vault, monkeypatch):
    tc, mod = client
    monkeypatch.setattr(mod, "fetch_images", lambda p: 2)  # 觸發 logger.info 分支
    inbox = vault / "Inbox" / "web"
    inbox.mkdir(parents=True)
    (inbox / "foo.md").write_text("---\ntitle: Foo\n---\nbody content", encoding="utf-8")

    r = tc.get("/robin/read", params={"file": "foo.md"})
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

    inbox = vault / "Inbox" / "web"
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
    app2.include_router(robin_mod.robin_router)
    app2.include_router(robin_mod.legacy_router)
    from fastapi.testclient import TestClient as TC2

    tc2 = TC2(app2, follow_redirects=False)

    r = tc2.get("/robin/read", params={"file": "bar.md"})
    assert r.status_code == 200
    assert "Hello world" in r.text


def test_read_source_author_string_renders_intact(client, vault, monkeypatch):
    """Bug fix: author as a single YAML string was iterated char-by-char and
    joined with ``、``. Template now type-checks and renders strings intact."""
    tc, mod = client
    monkeypatch.setattr(mod, "fetch_images", lambda p: 0)
    inbox = vault / "Inbox" / "web"
    inbox.mkdir(parents=True)
    (inbox / "single-author.md").write_text(
        '---\ntitle: "T"\nauthor: "Maiken Nedergaard"\n---\nbody',
        encoding="utf-8",
    )
    r = tc.get("/robin/read", params={"file": "single-author.md"})
    assert r.status_code == 200
    assert "Maiken Nedergaard" in r.text
    # Char-by-char rendering would emit "M、a、i、k、e、n" — must not occur
    assert "M、a" not in r.text


def test_read_source_author_list_still_joined(client, vault, monkeypatch):
    """Multi-author YAML list keeps the original ``、`` join behaviour."""
    tc, mod = client
    monkeypatch.setattr(mod, "fetch_images", lambda p: 0)
    inbox = vault / "Inbox" / "web"
    inbox.mkdir(parents=True)
    (inbox / "multi-author.md").write_text(
        '---\ntitle: "T"\nauthor:\n  - "Alice"\n  - "Bob"\n---\nbody',
        encoding="utf-8",
    )
    r = tc.get("/robin/read", params={"file": "multi-author.md"})
    assert r.status_code == 200
    assert "Alice、Bob" in r.text


def test_read_source_h1_uses_frontmatter_title(client, vault, monkeypatch):
    """Bug fix: H1 header used raw ``{{ filename }}`` (including ``.md`` and
    slug form). Now falls back to frontmatter.title when present."""
    tc, mod = client
    monkeypatch.setattr(mod, "fetch_images", lambda p: 0)
    inbox = vault / "Inbox" / "web"
    inbox.mkdir(parents=True)
    (inbox / "the-slug.md").write_text(
        '---\ntitle: "Real Article Title"\n---\nbody',
        encoding="utf-8",
    )
    r = tc.get("/robin/read", params={"file": "the-slug.md"})
    assert r.status_code == 200
    # H1 should contain the human title, not the slug filename
    assert "<h1>Real Article Title</h1>" in r.text


def test_read_source_h1_falls_back_to_filename_without_md(client, vault, monkeypatch):
    """No frontmatter title → H1 uses filename with ``.md`` stripped."""
    tc, mod = client
    monkeypatch.setattr(mod, "fetch_images", lambda p: 0)
    inbox = vault / "Inbox" / "web"
    inbox.mkdir(parents=True)
    (inbox / "no-title.md").write_text("plain body, no frontmatter", encoding="utf-8")
    r = tc.get("/robin/read", params={"file": "no-title.md"})
    assert r.status_code == 200
    assert "<h1>no-title</h1>" in r.text
    assert "<h1>no-title.md</h1>" not in r.text


def test_read_source_passes_article_dir_for_image_rewrite(client, vault, monkeypatch):
    """Bug fix: relative image paths in markdown were treated as vault-root
    relative, causing 404 on attachments living in article subdirs. Template
    now receives ``article_dir`` so JS can prepend it to relative img src."""
    tc, mod = client
    monkeypatch.setattr(mod, "fetch_images", lambda p: 0)
    inbox = vault / "Inbox" / "web"
    inbox.mkdir(parents=True)
    (inbox / "with-img.md").write_text(
        "---\ntitle: T\n---\n![](attachments/foo/img-1.jpg)",
        encoding="utf-8",
    )
    r = tc.get("/robin/read", params={"file": "with-img.md"})
    assert r.status_code == 200
    # JS constant must carry the vault-relative dir so the rewrite resolves
    # ``attachments/foo/img-1.jpg`` → ``/robin/files/Inbox/web/attachments/foo/img-1.jpg``
    assert "ARTICLE_DIR = 'Inbox/web'" in r.text


def test_read_source_without_frontmatter(client, vault, monkeypatch):
    """frontmatter 為空 dict → frontmatter_raw 為空字串分支。"""
    tc, mod = client
    monkeypatch.setattr(mod, "fetch_images", lambda p: 0)
    inbox = vault / "Inbox" / "web"
    inbox.mkdir(parents=True)
    (inbox / "plain.md").write_text("just plain text, no fm", encoding="utf-8")

    r = tc.get("/robin/read", params={"file": "plain.md"})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# GET /files/{path}
# ---------------------------------------------------------------------------


def test_serve_vault_file_auth_required_returns_403(auth_client):
    tc, _, _ = auth_client
    r = tc.get("/robin/files/foo.png")
    assert r.status_code == 403


def test_serve_vault_file_serves_from_files_dir(client, vault):
    tc, _ = client
    files_dir = vault / "Files"
    files_dir.mkdir()
    (files_dir / "img.png").write_bytes(b"\x89PNG")

    r = tc.get("/robin/files/img.png")
    assert r.status_code == 200


def test_serve_vault_file_fallback_to_vault_root(client, vault):
    """Files/ 沒有 → fallback 到 vault root。"""
    tc, _ = client
    (vault / "root.png").write_bytes(b"\x89PNG")

    r = tc.get("/robin/files/root.png")
    assert r.status_code == 200


def test_serve_vault_file_not_found_404(client, vault):
    tc, _ = client
    (vault / "Files").mkdir()

    r = tc.get("/robin/files/missing.png")
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
    r = tc.post("/robin/save-annotations", json=_ANN_PAYLOAD)
    assert r.status_code == 403


def test_save_annotations_unknown_base_400(client, vault):
    tc, _ = client
    payload = {**_ANN_PAYLOAD, "base": "unknown-base"}
    r = tc.post("/robin/save-annotations", json=payload)
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

    r = tc.post("/robin/save-annotations", json=_ANN_PAYLOAD)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "unsynced_count" in data
    assert data["unsynced_count"] == 1  # 1 item, never synced

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

    inbox = vault / "Inbox" / "web"
    inbox.mkdir(parents=True)
    source_text = "# Pure Source\n\nSome content here."
    (inbox / "doc.md").write_text(source_text, encoding="utf-8")

    tc, _ = client
    tc.post("/robin/save-annotations", json=_ANN_PAYLOAD)

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
    r = tc.post("/robin/save-annotations", json=payload)
    assert r.status_code == 200
    assert (vault / "KB" / "Annotations" / "src-doc.md").exists()


# ---------------------------------------------------------------------------
# POST /mark-read
# ---------------------------------------------------------------------------


def test_mark_read_auth_required(auth_client):
    tc, _, _ = auth_client
    r = tc.post("/robin/mark-read", data={"filename": "x.md"})
    assert r.status_code == 403


def test_mark_read_missing_file_404(client, vault):
    tc, _ = client
    (vault / "Inbox" / "web").mkdir(parents=True)
    r = tc.post("/robin/mark-read", data={"filename": "missing.md"})
    assert r.status_code == 404


def test_mark_read_happy_path(client, vault, monkeypatch):
    tc, mod = client
    captured = {}
    monkeypatch.setattr(mod, "mark_file_read", lambda p: captured.setdefault("path", p))
    inbox = vault / "Inbox" / "web"
    inbox.mkdir(parents=True)
    (inbox / "foo.md").write_text("x", encoding="utf-8")

    r = tc.post("/robin/mark-read", data={"filename": "foo.md"})
    assert r.status_code == 200
    assert captured["path"].name == "foo.md"


# ---------------------------------------------------------------------------
# POST /start
# ---------------------------------------------------------------------------


def test_start_unauth_redirect(auth_client):
    tc, _, _ = auth_client
    r = tc.post("/start", data={"filename": "foo.md"})
    assert r.status_code == 302


def test_start_missing_file_404(client, vault):
    tc, _ = client
    (vault / "Inbox" / "web").mkdir(parents=True)
    r = tc.post("/start", data={"filename": "missing.md"})
    assert r.status_code == 404


def test_start_happy_path_creates_session(client, vault):
    tc, mod = client
    inbox = vault / "Inbox" / "web"
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
# POST /start-book  (Centaur route B — 同步書本 ingest 入口；EPUB→KB/Raw/Books→
# /processing 自動流程。prepare_book_raw 單元測試見 tests/shared/test_book_raw.py)
# ---------------------------------------------------------------------------


def test_start_book_unauth_redirect(auth_client):
    tc, _, _ = auth_client
    r = tc.post("/start-book", data={"book_id": "bk1"})
    assert r.status_code == 302
    assert "/login" in r.headers["location"]


def test_start_book_missing_book_404(client, monkeypatch):
    """書不在 books 表 → prepare_book_raw 拋 LookupError → 404。"""
    tc, mod = client

    def _raise(book_id):
        raise LookupError("no such book")

    monkeypatch.setattr(mod, "prepare_book_raw", _raise)
    r = tc.post("/start-book", data={"book_id": "ghost"})
    assert r.status_code == 404


def test_start_book_unextractable_422(client, monkeypatch):
    """EPUB 抽不出文字 → EPUBTextError → 422。"""
    tc, mod = client
    from shared.epub_text import EPUBTextError

    def _raise(book_id):
        raise EPUBTextError("no extractable text")

    monkeypatch.setattr(mod, "prepare_book_raw", _raise)
    r = tc.post("/start-book", data={"book_id": "empty-bk"})
    assert r.status_code == 422


def test_start_book_happy_path_creates_session(client, vault, monkeypatch):
    """書本 raw 就緒 → 建 session（source_type=book、annotation_slug=book_id、
    keep_raw、無 Inbox 來源檔）→ /processing。"""
    tc, mod = client
    raw = vault / "KB" / "Raw" / "Books" / "my-book.md"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text("---\ntitle: My Book\nsource_type: book\n---\nbook body", encoding="utf-8")
    monkeypatch.setattr(mod, "prepare_book_raw", lambda book_id: raw)

    r = tc.post("/start-book", data={"book_id": "my-book-id"})
    assert r.status_code == 302
    assert r.headers["location"] == "/processing"
    assert "robin_session" in r.headers.get("set-cookie", "")

    sess = next(s for s in mod.sessions.values() if s.get("source_type") == "book")
    assert sess["raw_path"] == str(raw)  # 直指 KB/Raw/Books，未從 Inbox 複製
    assert sess["annotation_slug"] == "my-book-id"  # 整本劃線提案用
    assert sess["file_path"] == ""  # 無 Inbox 來源檔 → execute 不回收
    assert sess["keep_raw"] is True  # 衍生檔，cancel 不回收
    assert sess["step"] == "summarizing"


# ---------------------------------------------------------------------------
# GET /robin/estimate — 按 Ingest 前的時長預估（餵 ingest-confirm.js 確認框）
# ---------------------------------------------------------------------------


def test_estimate_unauth_returns_403(auth_client):
    tc, _, _ = auth_client
    r = tc.get("/robin/estimate?source_type=article&source_id=x.md")
    assert r.status_code == 403


def test_estimate_article_returns_label_and_detail(client, vault, monkeypatch):
    """文章：讀 Inbox 檔（strip frontmatter）→ 回字數 + 時長範圍。"""
    tc, mod = client
    import shared.local_llm as _llm

    monkeypatch.setattr(_llm, "is_server_available", lambda *a, **k: False)
    inbox = mod._get_inbox()
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "a.md").write_text("---\ntitle: A\n---\n" + ("字" * 2000), encoding="utf-8")

    r = tc.get("/robin/estimate?source_type=article&source_id=a.md")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["char_count"] == 2000  # frontmatter 已剝除
    assert data["is_large"] is False
    assert data["time_label"].startswith("約")
    assert "字" in data["detail"]


def test_estimate_article_missing_file_404(client, vault):
    tc, mod = client
    r = tc.get("/robin/estimate?source_type=article&source_id=ghost.md")
    assert r.status_code == 404


def test_estimate_book_extracts_without_writing(client, vault, monkeypatch):
    """書本：走 extract_book_text（不寫 KB/Raw）→ 大文件回段數 + 分鐘範圍。"""
    tc, mod = client
    import shared.local_llm as _llm
    from agents.robin import chunker

    monkeypatch.setattr(_llm, "is_server_available", lambda *a, **k: False)
    monkeypatch.setattr(mod, "extract_book_text", lambda bid: ("書", "作者", "字" * 50000))
    monkeypatch.setattr(chunker, "chunk_document", lambda content: ["c"] * 4)

    r = tc.get("/robin/estimate?source_type=book&source_id=bk1")
    assert r.status_code == 200
    data = r.json()
    assert data["is_large"] is True
    assert data["n_chunks"] == 4
    assert "分 4 段" in data["detail"]
    assert "分鐘" in data["time_label"]
    assert not (vault / "KB" / "Raw" / "Books").exists()  # 估算不落 raw 檔


def test_estimate_book_missing_returns_404(client, vault, monkeypatch):
    tc, mod = client

    def _raise(bid):
        raise LookupError("no book")

    monkeypatch.setattr(mod, "extract_book_text", _raise)
    r = tc.get("/robin/estimate?source_type=book&source_id=ghost")
    assert r.status_code == 404


def test_estimate_book_unextractable_returns_422(client, vault, monkeypatch):
    tc, mod = client
    from shared.epub_text import EPUBTextError

    def _raise(bid):
        raise EPUBTextError("no text")

    monkeypatch.setattr(mod, "extract_book_text", _raise)
    r = tc.get("/robin/estimate?source_type=book&source_id=empty")
    assert r.status_code == 422


def test_estimate_video_missing_transcript_404(client, vault):
    tc, mod = client
    r = tc.get("/robin/estimate?source_type=video&source_id=novid")
    assert r.status_code == 404


def test_estimate_video_vtt_transcript_returns_estimate(client, vault, monkeypatch):
    """影片逐字稿（.vtt）存在 → _read_ingest_source 走 webvtt_to_prose → 回估算。"""
    tc, mod = client
    import shared.local_llm as _llm

    monkeypatch.setattr(_llm, "is_server_available", lambda *a, **k: False)
    vdir = vault / "KB" / "Raw" / "Videos"
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "vid9.vtt").write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n這是一段逐字稿內容，用來估算時間。\n",
        encoding="utf-8",
    )
    r = tc.get("/robin/estimate?source_type=video&source_id=vid9")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["char_count"] > 0


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
    assert r.headers["location"] == "/robin"


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
    assert r.headers["location"] == "/robin"


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
# POST /kb/research
# ---------------------------------------------------------------------------


def test_kb_research_returns_results(client, monkeypatch):
    tc, mod = client
    monkeypatch.setattr(mod, "search_kb", lambda q, vault_path: [{"title": "hit", "score": 0.9}])
    r = tc.post("/kb/research", data={"query": "sleep"})
    assert r.status_code == 200
    body = r.json()
    assert body["results"][0]["title"] == "hit"


# ---------------------------------------------------------------------------
# Legacy redirects (R6) — root-prefix → /robin/* (GET 301 / POST 308)
# ---------------------------------------------------------------------------


def test_legacy_read_redirects_301_preserving_query_string(client):
    """GET /read?file=foo.md → 301 → /robin/read?file=foo.md.

    Codex §1 caveat: query-string shape preserved — no path-segment migration.
    """
    tc, _ = client
    r = tc.get("/read?file=foo.md&base=inbox")
    assert r.status_code == 301
    assert r.headers["location"] == "/robin/read?file=foo.md&base=inbox"


def test_legacy_files_redirects_301(client):
    tc, _ = client
    r = tc.get("/files/img.png")
    assert r.status_code == 301
    assert r.headers["location"] == "/robin/files/img.png"


def test_legacy_events_redirects_301(client):
    """SSE: EventSource follows 301 on initial connection so the legacy
    URL still resolves to the live stream after the rename."""
    tc, _ = client
    r = tc.get("/events/some-sid")
    assert r.status_code == 301
    assert r.headers["location"] == "/robin/events/some-sid"


def test_legacy_save_annotations_returns_308_preserving_method(client):
    """POST legacy URL must 308 (method+body preserving) so the JSON
    fetch replays at the new URL instead of downgrading to GET."""
    tc, _ = client
    r = tc.post("/save-annotations", json=_ANN_PAYLOAD)
    assert r.status_code == 308
    assert r.headers["location"] == "/robin/save-annotations"


def test_legacy_sync_annotations_returns_308(client):
    tc, _ = client
    r = tc.post("/sync-annotations/my-slug")
    assert r.status_code == 308
    assert r.headers["location"] == "/robin/sync-annotations/my-slug"


def test_legacy_mark_read_returns_308(client):
    tc, _ = client
    r = tc.post("/mark-read", data={"filename": "x.md"})
    assert r.status_code == 308
    assert r.headers["location"] == "/robin/mark-read"


def test_legacy_discard_info_returns_301_with_query_string(client):
    tc, _ = client
    r = tc.get("/discard-info?file=x.md&base=inbox")
    assert r.status_code == 301
    assert r.headers["location"] == "/robin/discard-info?file=x.md&base=inbox"


def test_legacy_discard_returns_308_with_query_string(client):
    tc, _ = client
    r = tc.post("/discard?file=x.md&base=inbox")
    assert r.status_code == 308
    assert r.headers["location"] == "/robin/discard?file=x.md&base=inbox"


def test_legacy_translate_returns_308_with_query_string(client):
    tc, _ = client
    r = tc.post("/translate?file=x.md")
    assert r.status_code == 308
    assert r.headers["location"] == "/robin/translate?file=x.md"


# ---------------------------------------------------------------------------
# Reading hub (/robin/home) — unified 3-source entry
# ---------------------------------------------------------------------------


def test_reading_hub_renders_three_sources(client):
    """/robin/home renders the unified hub with all three source panels and
    their add/ingest actions, even when every source is empty."""
    tc, _ = client
    r = tc.get("/robin/home")
    assert r.status_code == 200
    body = r.text
    assert "Robin · 首頁" in body
    # three source panels
    assert "文章" in body and "影片" in body and "書" in body
    # actions present (video add button is the gap this slice fixes)
    assert "/robin/watchlist/add" in body  # ➕ 加影片
    assert "/robin/books/upload" in body  # 📤 上傳新書


def test_reading_hub_requires_auth(auth_client):
    """When WEB_PASSWORD is set, the hub redirects unauthenticated callers to
    /login with a next param (same gate as the other Robin surfaces)."""
    tc, _, _cookies = auth_client
    r = tc.get("/robin/home")
    assert r.status_code == 302
    assert r.headers["location"] == "/login?next=/robin/home"


# ---------------------------------------------------------------------------
# POST /start-video  (Centaur route E — 影片 ingest 入口；逐字稿已 canonical
# 落在 KB/Raw/Videos，不從 Inbox 複製，故不可回收原檔)
# ---------------------------------------------------------------------------


def _make_video_transcript(vault: Path, video_id: str) -> Path:
    vtt = vault / "KB" / "Raw" / "Videos" / f"{video_id}.vtt"
    vtt.parent.mkdir(parents=True, exist_ok=True)
    vtt.write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nHello from the video.\n",
        encoding="utf-8",
    )
    return vtt


def test_start_video_unauth_redirect(auth_client):
    tc, _, _ = auth_client
    r = tc.post("/start-video", data={"video_id": "Ch4Sl0POBhU"})
    assert r.status_code == 302
    assert "/login" in r.headers["location"]


def test_start_video_invalid_id_400(client, vault):
    """video_id 走 regex 白名單擋路徑穿越 — 含 / 或 .. 一律 400。"""
    tc, _ = client
    r = tc.post("/start-video", data={"video_id": "../../etc/passwd"})
    assert r.status_code == 400


def test_start_video_missing_transcript_404(client, vault):
    """合法 id 但 KB/Raw/Videos 沒有對應逐字稿 → 404。"""
    tc, _ = client
    r = tc.post("/start-video", data={"video_id": "no_such_vid"})
    assert r.status_code == 404


def test_start_video_happy_path_creates_session(client, vault):
    """逐字稿就地存在 → 建 session（直指逐字稿、keep_raw、不複製）→ /processing。"""
    tc, mod = client
    vtt = _make_video_transcript(vault, "Ch4Sl0POBhU")

    r = tc.post("/start-video", data={"video_id": "Ch4Sl0POBhU"})
    assert r.status_code == 302
    assert r.headers["location"] == "/processing"
    assert "robin_session" in r.headers.get("set-cookie", "")

    sess = next(s for s in mod.sessions.values() if s.get("source_type") == "video")
    assert sess["raw_path"] == str(vtt)  # 直指 canonical 逐字稿，未複製
    assert sess["file_path"] == ""  # 無 Inbox 來源檔 → execute 不回收
    assert sess["keep_raw"] is True  # cancel 也不回收
    assert sess["annotation_slug"] == "youtube_Ch4Sl0POBhU"
    assert sess["step"] == "summarizing"


def test_start_video_does_not_copy_or_delete_transcript(client, vault):
    """canonical 逐字稿留在原處（不像文章從 Inbox 複製到 KB/Raw）。"""
    tc, _ = client
    vtt = _make_video_transcript(vault, "v6MWNrVbM4E")
    before = vtt.read_text(encoding="utf-8")

    r = tc.post("/start-video", data={"video_id": "v6MWNrVbM4E"})
    assert r.status_code == 302
    assert vtt.exists()
    assert vtt.read_text(encoding="utf-8") == before


def test_cancel_keeps_raw_for_video_ingest(client, vault, monkeypatch):
    """keep_raw=True（影片 ingest）→ cancel 不回收 canonical 逐字稿。"""
    tc, mod = client
    vtt = _make_video_transcript(vault, "Ch4Sl0POBhU")
    sid = mod._new_session(step="summarizing", raw_path=str(vtt), summary_path="", keep_raw=True)
    monkeypatch.setattr(mod.platform, "system", lambda: "Linux")
    tc.cookies.set("robin_session", sid)

    r = tc.post("/cancel")
    assert r.status_code == 302
    assert mod.sessions[sid]["step"] == "cancelled"
    assert vtt.exists()  # keep_raw 守門 → 逐字稿仍在


# ---------------------------------------------------------------------------
# GET /robin/watchlist/{id}  (video reader — av_reader.html，含 Ingest 按鈕)
# + DELETE /robin/watchlist/{id}/annotation 邊界
# ---------------------------------------------------------------------------


def _setup_video(vault: Path, video_id: str = "Ch4Sl0POBhU") -> Path:
    """Watchlist manifest（讓 resolver 認得影片）+ KB/Raw/Videos 逐字稿
    （ADR-046 canonical 位置，watch_video 從這裡讀 cue）。"""
    import json

    entry = vault / "Watchlist" / "youtube" / video_id
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "video_id": video_id,
                "title": "Sample Talk",
                "channel": "Sample Channel",
                "url": f"https://youtube.com/watch?v={video_id}",
                "duration_s": 600,
                "primary_lang": "en",
                "cast": ["host", "Guest A"],
                "transcript_path": "transcript.vtt",
                "added_at": "2026-06-01T00:00:00Z",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    vtt = vault / "KB" / "Raw" / "Videos" / f"{video_id}.vtt"
    vtt.parent.mkdir(parents=True, exist_ok=True)
    vtt.write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nWelcome to the show.\n\n"
        "00:00:05.000 --> 00:00:07.000\nToday we discuss sleep.\n",
        encoding="utf-8",
    )
    return vtt


def test_watch_video_renders_reader_with_ingest_button(client, vault):
    """av_reader 渲染逐字稿 cue + 新的「⚙️ Ingest 這支」按鈕（POST /start-video）。"""
    tc, _ = client
    _setup_video(vault)

    r = tc.get("/robin/watchlist/Ch4Sl0POBhU")
    assert r.status_code == 200
    assert "Welcome to the show." in r.text  # 逐字稿洗出的 cue
    # 新 ingest 入口存在且接到 /start-video，帶 video_id。
    assert 'action="/start-video"' in r.text
    assert "Ingest 這支" in r.text
    assert 'value="Ch4Sl0POBhU"' in r.text


def test_watch_video_renders_annotations_including_unanchored(client, vault):
    """劃線清單渲染：有 t= 定位的命中 cue marker；無 t= 定位的（start=None）
    走 ``continue`` 分支不算 marker，但仍渲染在清單。"""
    from shared.schemas.annotations import AnnotationSetV3, HighlightV3

    tc, mod = client
    _setup_video(vault)

    store = mod.get_annotation_store()
    store.save(
        AnnotationSetV3(
            slug="youtube_Ch4Sl0POBhU",
            base="youtube",
            items=[
                HighlightV3(
                    cfi="t=1.0-3.0",
                    text_excerpt="Welcome to the show.",
                    text="Welcome to the show.",
                ),
                # 無 t= 定位（start 解析為 None）→ 觸發 marker 迴圈的 continue 分支。
                HighlightV3(
                    cfi="epubcfi(/6/4!/4/2:0)",
                    text_excerpt="orphan mark",
                    text="orphan mark",
                ),
            ],
        )
    )

    r = tc.get("/robin/watchlist/Ch4Sl0POBhU")
    assert r.status_code == 200
    assert "Welcome to the show." in r.text
    assert "orphan mark" in r.text


def test_delete_video_highlight_invalid_id_404(client, vault):
    """Path-traversal 形狀的 video_id 在碰 vault 前就被 regex 擋成 404。"""
    tc, _ = client
    r = tc.delete("/robin/watchlist/bad!id/annotation", params={"cue_start": 1.0})
    assert r.status_code == 404


def test_delete_video_highlight_no_match_keeps_item(client, vault):
    """在沒有 mark 的 cue 刪除 → 既有 item 全保留、removed=0。"""
    from shared.schemas.annotations import AnnotationSetV3, HighlightV3

    tc, mod = client
    _setup_video(vault)
    mod.get_annotation_store().save(
        AnnotationSetV3(
            slug="youtube_Ch4Sl0POBhU",
            base="youtube",
            items=[HighlightV3(cfi="t=1.0-3.0", text_excerpt="hi", text="hi")],
        )
    )

    r = tc.delete("/robin/watchlist/Ch4Sl0POBhU/annotation", params={"cue_start": 99.0})
    assert r.status_code == 200
    assert r.json()["removed"] == 0


def test_create_video_annotation_resolver_valueerror_404(client, vault, monkeypatch):
    """Resolver 丟 ValueError（registry 層偵測 path-traversal/symlink-escape）→
    端點映成 404，不是 500（defence-in-depth）。"""
    tc, mod = client
    _setup_video(vault)

    def boom(self, key):
        raise ValueError("traversal at registry layer")

    monkeypatch.setattr(mod.ReadingSourceRegistry, "resolve", boom)
    r = tc.post(
        "/robin/watchlist/Ch4Sl0POBhU/annotation",
        json={"cue_start": 1.0, "cue_end": 3.0, "excerpt": "x"},
    )
    assert r.status_code == 404


def test_delete_video_highlight_resolver_valueerror_404(client, vault, monkeypatch):
    """同上，DELETE 路徑的 resolver ValueError → 404。"""
    tc, mod = client

    def boom(self, key):
        raise ValueError("traversal at registry layer")

    monkeypatch.setattr(mod.ReadingSourceRegistry, "resolve", boom)
    r = tc.delete("/robin/watchlist/Ch4Sl0POBhU/annotation", params={"cue_start": 1.0})
    assert r.status_code == 404


def test_start_video_prefers_cleaned_md_over_vtt(client, vault):
    """既有清理過的 .md 時,/start-video 拿 .md 當 ingest 輸入(不是 .vtt)。"""
    tc, mod = client
    _make_video_transcript(vault, "Ch4Sl0POBhU")  # writes the .vtt
    md = vault / "KB" / "Raw" / "Videos" / "Ch4Sl0POBhU.md"
    md.write_text("---\ntitle: T\n---\n**[00:01]** clean prose.\n", encoding="utf-8")

    r = tc.post("/start-video", data={"video_id": "Ch4Sl0POBhU"})
    assert r.status_code == 302
    sess = next(s for s in mod.sessions.values() if s.get("source_type") == "video")
    assert sess["raw_path"].endswith("Ch4Sl0POBhU.md")
