# ruff: noqa: E501  — fixture HTML strings contain CJK lines longer than 100 chars.
"""Subprocess smoke test for `audit.py` CLI — sanity for sys.path shim, arg
parsing, and end-to-end run with `--llm-level=none` + `--no-kb` (zero external
deps beyond a local fixture HTTP server).

PageSpeed runs unmocked but its env var is unset → audit catches the
PageSpeedCredentialsError and falls back to empty summary. The smoke test
verifies the script exits 0 and writes a valid markdown frontmatter.
"""

from __future__ import annotations

import http.server
import json
import socket
import socketserver
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import yaml

_FIXTURE_HTML_TEXT = (
    """<!doctype html>
<html lang="zh-Hant">
<head>
<title>Smoke test article — Zone 2 訓練短篇</title>
<meta name="description" content="本文測試 audit pipeline 的最小可跑路徑，內容夠長以通過字數門檻並含足夠 metadata 與簡易 schema markup。">
<link rel="canonical" href="http://127.0.0.1:0/smoke-page">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta property="og:title" content="Smoke test article">
<meta property="og:description" content="audit smoke">
<meta property="og:image" content="http://127.0.0.1:0/og.jpg">
<meta property="og:url" content="http://127.0.0.1:0/smoke-page">
<meta name="twitter:card" content="summary">
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Article","headline":"Smoke","author":{"@type":"Person","name":"修修","url":"http://127.0.0.1:0/about"},"datePublished":"2026-01-01"}
</script>
</head>
<body>
<h1>Smoke test article</h1>
<h2>背景</h2>
<p>本文存在的目的是讓 audit smoke test 能在不打外部 API 的情況下跑通整條 pipeline。</p>
<h2>細節</h2>
<p>內容是隨機填充的繁體中文段落，重點是讓字數計算可以接受，metadata 與 schema 通過基本檢查，避免 fetch_html 之後就被打斷。Zone 2 訓練、有氧能量系統、磷酸肌酸系統、糖解系統 — 這些字串只是用來增加 visible text 長度。</p>
"""
    + ("<p>本段為配重內容，重點解釋 Zone 2 訓練的應用差異與操作細節。" * 80)
    + """</p>
<a href="/aerobic-system">內部連結</a>
<a href="https://pubmed.ncbi.nlm.nih.gov/12345678/">外部連結</a>
<img src="/img/x.jpg" alt="圖片" width="800" height="600">
</body></html>
"""
)

_FIXTURE_HTML = _FIXTURE_HTML_TEXT.encode("utf-8")


class _FixtureHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — http.server API
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_FIXTURE_HTML)))
        self.end_headers()
        self.wfile.write(_FIXTURE_HTML)

    def log_message(self, *args, **kwargs):  # silence
        return


@pytest.fixture
def fixture_server():
    """Spin up a localhost HTTP server returning the fixture HTML for any path."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    server = socketserver.ThreadingTCPServer(("127.0.0.1", port), _FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/smoke-page"
    finally:
        server.shutdown()
        server.server_close()


def test_subprocess_runs_with_llm_none_and_no_kb(tmp_path, fixture_server, monkeypatch):
    """`python audit.py --url <fixture> --llm-level=none --no-kb` exits 0 and
    writes a valid markdown report (frontmatter parses, all 7 sections present)."""
    repo_root = Path(__file__).resolve().parents[3]
    audit_py = repo_root / ".claude" / "skills" / "seo-audit-post" / "scripts" / "audit.py"
    out_dir = tmp_path / "audits"

    env = {
        # Strip external creds so the test never accidentally calls real APIs
        "PAGESPEED_INSIGHTS_API_KEY": "",
        "ANTHROPIC_API_KEY": "",
        "GSC_PROPERTY_SHOSHO": "",
        "GSC_PROPERTY_FLEET": "",
        "GSC_SERVICE_ACCOUNT_JSON_PATH": "",
        # Windows subprocess wants SYSTEMROOT for socket / DNS
        "SYSTEMROOT": "C:\\Windows",
        "PATH": "",
    }
    proc = subprocess.run(
        [
            sys.executable,
            str(audit_py),
            "--url",
            fixture_server,
            "--output-dir",
            str(out_dir),
            "--llm-level",
            "none",
            "--no-kb",
        ],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}\n\nstdout:\n{proc.stdout}"

    # Last line of stdout is `{"output_path": "..."}`
    last_json = proc.stdout.strip().splitlines()[-1]
    payload = json.loads(last_json)
    out_path = Path(payload["output_path"])
    assert out_path.exists()

    md = out_path.read_text(encoding="utf-8")
    assert md.startswith("---\n")
    fm = yaml.safe_load(md.split("---\n", 2)[1])
    assert fm["type"] == "seo-audit-report"
    assert fm["llm_level"] == "none"
    # KB section gets skip mark; GSC section gets non-self-hosted
    assert fm["kb_section"] == "skipped (--no-kb)"
    assert fm["gsc_section"] == "skipped (non-self-hosted)"

    for header in (
        "## 1. Summary",
        "## 2. Critical Fixes",
        "## 3. Warnings",
        "## 4. Info",
        "## 5. PageSpeed Insights Summary",
        "## 6. GSC Ranking",
        "## 7. Internal Link Suggestions",
    ):
        assert header in md, f"missing section: {header}"
