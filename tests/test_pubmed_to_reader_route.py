"""千陽 /pubmed-to-reader 路由 + /read base=sources 測試。"""

import importlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.delenv("DISABLE_ROBIN", raising=False)
    monkeypatch.setenv("WEB_PASSWORD", "testpass")
    monkeypatch.setenv("WEB_SECRET", "testsecret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    # vault 結構：Inbox/kb + KB/Wiki/Sources + KB/Attachments/pubmed
    (tmp_path / "Inbox" / "kb").mkdir(parents=True)
    (tmp_path / "KB" / "Wiki" / "Sources").mkdir(parents=True)
    (tmp_path / "KB" / "Attachments" / "pubmed").mkdir(parents=True)
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.auth as auth_router_module
    import thousand_sunny.routers.robin as robin_module

    importlib.reload(auth_module)
    importlib.reload(auth_router_module)
    importlib.reload(robin_module)
    importlib.reload(app_module)
    return TestClient(app_module.app, follow_redirects=False)


def _auth_cookie(client):
    resp = client.post("/login", data={"password": "testpass"}, follow_redirects=False)
    return resp.cookies.get("nakama_auth", "")


# ── /pubmed-to-reader ──────────────────────────────────────────────────────


def test_pubmed_to_reader_requires_auth(client):
    resp = client.get("/pubmed-to-reader?pmid=12345")
    assert resp.status_code == 302
    assert "/login" in resp.headers["location"]


def test_pubmed_to_reader_rejects_non_numeric_pmid(client):
    auth = _auth_cookie(client)
    resp = client.get(
        "/pubmed-to-reader?pmid=abc",
        cookies={"nakama_auth": auth},
    )
    assert resp.status_code == 400


def test_pubmed_to_reader_404_when_pdf_missing(client):
    auth = _auth_cookie(client)
    # PDF 不存在
    resp = client.get(
        "/pubmed-to-reader?pmid=99999999",
        cookies={"nakama_auth": auth},
    )
    assert resp.status_code == 404


def test_pubmed_to_reader_translates_and_redirects(client, tmp_path):
    """PDF 存在但 bilingual 還沒做 → 走 parse_pdf + translate → 寫 bilingual → redirect"""
    auth = _auth_cookie(client)
    pdf_path = tmp_path / "KB" / "Attachments" / "pubmed" / "12345.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake content")

    with (
        patch(
            "shared.pdf_parser.parse_pdf",
            return_value="# Paper Title\n\nIntroduction paragraph.",
        ),
        patch(
            "shared.translator.translate_document",
            return_value=("# Paper Title\n\nIntroduction paragraph.\n\n> 標題。\n\n> 介紹段落。"),
        ),
    ):
        resp = client.get(
            "/pubmed-to-reader?pmid=12345",
            cookies={"nakama_auth": auth},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert resp.headers["location"] == ("/read?file=pubmed-12345-bilingual.md&base=sources")
    # 檢查 bilingual md 真的寫進 sources
    bilingual = tmp_path / "KB" / "Wiki" / "Sources" / "pubmed-12345-bilingual.md"
    assert bilingual.exists()
    text = bilingual.read_text(encoding="utf-8")
    assert "bilingual: true" in text
    assert "pmid: 12345" in text
    assert "標題。" in text


def test_pubmed_to_reader_short_circuit_when_bilingual_exists(client, tmp_path):
    """已翻譯過就不重翻，直接 redirect。"""
    auth = _auth_cookie(client)
    pdf_path = tmp_path / "KB" / "Attachments" / "pubmed" / "12345.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    bilingual = tmp_path / "KB" / "Wiki" / "Sources" / "pubmed-12345-bilingual.md"
    bilingual.write_text("already-exists", encoding="utf-8")

    with (
        patch("shared.pdf_parser.parse_pdf") as mock_parse,
        patch("shared.translator.translate_document") as mock_translate,
    ):
        resp = client.get(
            "/pubmed-to-reader?pmid=12345",
            cookies={"nakama_auth": auth},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert "base=sources" in resp.headers["location"]
    # 關鍵：parse_pdf / translate_document 不該被叫（short-circuit）
    mock_parse.assert_not_called()
    mock_translate.assert_not_called()


def test_pubmed_to_reader_html_path_translates(client, tmp_path):
    """只有 {pmid}.md 沒 {pmid}.pdf（oa_html case）→ 讀 md + translate → bilingual"""
    auth = _auth_cookie(client)
    html_md = tmp_path / "KB" / "Attachments" / "pubmed" / "42020128.md"
    raw_md = "# Lean Mass Preservation\n\nThe publisher HTML was scraped and localized. " * 20
    html_md.write_text(raw_md, encoding="utf-8")

    with (
        patch("shared.pdf_parser.parse_pdf") as mock_parse,
        patch(
            "shared.translator.translate_document",
            return_value=raw_md + "\n\n> 翻譯內容。",
        ) as mock_translate,
    ):
        resp = client.get(
            "/pubmed-to-reader?pmid=42020128",
            cookies={"nakama_auth": auth},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert resp.headers["location"] == ("/read?file=pubmed-42020128-bilingual.md&base=sources")
    mock_parse.assert_not_called()  # PDF 路徑不該被碰
    mock_translate.assert_called_once()
    bilingual = tmp_path / "KB" / "Wiki" / "Sources" / "pubmed-42020128-bilingual.md"
    assert bilingual.exists()
    text = bilingual.read_text(encoding="utf-8")
    assert "source_kind: html" in text
    assert "42020128.md" in text  # derived_from 指向 md 而非 pdf
    assert "翻譯內容。" in text


def test_pubmed_to_reader_prefers_pdf_over_html(client, tmp_path):
    """PDF 跟 HTML md 都存在時優先用 PDF（資料完整度高）。"""
    auth = _auth_cookie(client)
    pubmed_dir = tmp_path / "KB" / "Attachments" / "pubmed"
    (pubmed_dir / "42020128.pdf").write_bytes(b"%PDF-1.4 fake")
    (pubmed_dir / "42020128.md").write_text("# HTML version", encoding="utf-8")

    with (
        patch("shared.pdf_parser.parse_pdf", return_value="# PDF version\n\nBody.") as mock_parse,
        patch(
            "shared.translator.translate_document",
            return_value="# PDF version\n\nBody.\n\n> 翻譯版。",
        ) as mock_translate,
    ):
        resp = client.get(
            "/pubmed-to-reader?pmid=42020128",
            cookies={"nakama_auth": auth},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    mock_parse.assert_called_once()  # PDF flow 被觸發
    mock_translate.assert_called_once()
    bilingual = tmp_path / "KB" / "Wiki" / "Sources" / "pubmed-42020128-bilingual.md"
    text = bilingual.read_text(encoding="utf-8")
    assert "source_kind: pdf" in text
    assert "42020128.pdf" in text


def test_pubmed_to_reader_falls_back_to_raw_when_translate_fails(client, tmp_path):
    """翻譯失敗仍要把 raw markdown 存下來，讓使用者至少能讀 + annotate。"""
    auth = _auth_cookie(client)
    pdf_path = tmp_path / "KB" / "Attachments" / "pubmed" / "12345.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    with (
        patch("shared.pdf_parser.parse_pdf", return_value="# Raw Title\n\nRaw body."),
        patch("shared.translator.translate_document", side_effect=RuntimeError("LLM down")),
    ):
        resp = client.get(
            "/pubmed-to-reader?pmid=12345",
            cookies={"nakama_auth": auth},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    bilingual = tmp_path / "KB" / "Wiki" / "Sources" / "pubmed-12345-bilingual.md"
    assert bilingual.exists()
    assert "Raw Title" in bilingual.read_text(encoding="utf-8")


# ── /read base=sources ─────────────────────────────────────────────────────


def test_read_base_sources(client, tmp_path):
    """reader 現在能讀 KB/Wiki/Sources 下的檔案。"""
    auth = _auth_cookie(client)
    sources = tmp_path / "KB" / "Wiki" / "Sources"
    (sources / "pubmed-12345-bilingual.md").write_text(
        "---\nbilingual: true\n---\n\n# Test\n\nBody.",
        encoding="utf-8",
    )

    resp = client.get(
        "/read?file=pubmed-12345-bilingual.md&base=sources",
        cookies={"nakama_auth": auth},
    )
    assert resp.status_code == 200
    assert "Test" in resp.text


def test_read_base_rejects_unknown(client):
    auth = _auth_cookie(client)
    resp = client.get(
        "/read?file=anything.md&base=evil",
        cookies={"nakama_auth": auth},
    )
    assert resp.status_code == 400


def test_read_base_defaults_to_inbox(client, tmp_path):
    """不帶 base 時 default inbox，維持向後相容。"""
    auth = _auth_cookie(client)
    inbox = tmp_path / "Inbox" / "kb"
    (inbox / "hello.md").write_text("# Hello\n\nWorld.", encoding="utf-8")

    resp = client.get("/read?file=hello.md", cookies={"nakama_auth": auth})
    assert resp.status_code == 200
    assert "Hello" in resp.text


# ── /save-annotations base=sources ──────────────────────────────────────────


def test_save_annotations_to_sources(client, tmp_path):
    auth = _auth_cookie(client)
    sources = tmp_path / "KB" / "Wiki" / "Sources"
    target = sources / "pubmed-12345-bilingual.md"
    target.write_text("original", encoding="utf-8")

    resp = client.post(
        "/save-annotations",
        data={
            "filename": "pubmed-12345-bilingual.md",
            "content": "annotated!",
            "base": "sources",
        },
        cookies={"nakama_auth": auth},
    )
    assert resp.status_code == 200
    assert target.read_text(encoding="utf-8") == "annotated!"
