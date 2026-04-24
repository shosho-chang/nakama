"""Tests for shared/image_fetcher.py — download_markdown_images().

Uses monkeypatch to replace httpx.Client with a fake that serves in-memory
bytes and content-type per URL. No network access.

Test coverage:
- Happy path: 多張圖下載 + rewrite URL
- 404 保留 remote URL
- 非 image content-type 跳過
- Dedupe：同一 URL 出現兩次只下載一次但兩個 match 都 rewrite
- 相對 URL 用 base_url resolve
- 無匹配時 md_text 原樣回傳
- 圖片超過上限中止並刪除部份檔
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pytest

import shared.image_fetcher as image_fetcher

# ---------------------------------------------------------------------------
# Fake httpx.Client
# ---------------------------------------------------------------------------


class _FakeStream:
    def __init__(self, status_code: int, content_type: str, chunks: Iterable[bytes]):
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self._chunks = list(chunks)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def iter_bytes(self):
        yield from self._chunks


class _FakeClient:
    def __init__(self, response_map: dict[str, _FakeStream]):
        self._map = response_map
        self.requested: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def stream(self, method: str, url: str):
        self.requested.append(url)
        if url not in self._map:
            # Simulate connection error via status 599 equivalent — use status 404 stream
            return _FakeStream(404, "text/plain", [b""])
        return self._map[url]


def _install_fake_client(monkeypatch: pytest.MonkeyPatch, response_map: dict[str, _FakeStream]):
    """Make httpx.Client(...) in image_fetcher return our fake. Return the fake for assertions."""
    fake = _FakeClient(response_map)

    def _factory(*_a, **_k):
        return fake

    monkeypatch.setattr(image_fetcher.httpx, "Client", _factory)
    return fake


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_happy_path_two_images(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    md = "Intro text.\n\n![Fig 1](https://cdn.example.com/a.jpg) caption\n\n![Fig 2](https://cdn.example.com/b.png)\n"
    fake = _install_fake_client(
        monkeypatch,
        {
            "https://cdn.example.com/a.jpg": _FakeStream(200, "image/jpeg", [b"jpegbytes"]),
            "https://cdn.example.com/b.png": _FakeStream(200, "image/png", [b"pngbytes"]),
        },
    )

    rewritten, saved = image_fetcher.download_markdown_images(
        md,
        dest_dir=tmp_path / "42020128",
        vault_relative_prefix="KB/Attachments/pubmed/42020128",
    )

    assert saved == [
        "KB/Attachments/pubmed/42020128/img-1.jpg",
        "KB/Attachments/pubmed/42020128/img-2.png",
    ]
    assert "![Fig 1](KB/Attachments/pubmed/42020128/img-1.jpg)" in rewritten
    assert "![Fig 2](KB/Attachments/pubmed/42020128/img-2.png)" in rewritten
    assert (tmp_path / "42020128" / "img-1.jpg").read_bytes() == b"jpegbytes"
    assert (tmp_path / "42020128" / "img-2.png").read_bytes() == b"pngbytes"
    assert fake.requested == [
        "https://cdn.example.com/a.jpg",
        "https://cdn.example.com/b.png",
    ]


def test_404_keeps_remote_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    md = "![broken](https://cdn.example.com/missing.jpg)"
    _install_fake_client(
        monkeypatch,
        {"https://cdn.example.com/missing.jpg": _FakeStream(404, "text/html", [b""])},
    )

    rewritten, saved = image_fetcher.download_markdown_images(
        md,
        dest_dir=tmp_path,
        vault_relative_prefix="whatever",
    )

    assert saved == []
    assert rewritten == md  # unchanged


def test_non_image_content_type_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    md = "![html](https://bad.example.com/x)"
    _install_fake_client(
        monkeypatch,
        {"https://bad.example.com/x": _FakeStream(200, "text/html; charset=utf-8", [b"<html/>"])},
    )

    rewritten, saved = image_fetcher.download_markdown_images(
        md,
        dest_dir=tmp_path,
        vault_relative_prefix="p",
    )

    assert saved == []
    assert rewritten == md


def test_dedupe_same_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    md = "![a](https://cdn.example.com/x.png)\n\nmiddle\n\n![b](https://cdn.example.com/x.png)"
    fake = _install_fake_client(
        monkeypatch,
        {"https://cdn.example.com/x.png": _FakeStream(200, "image/png", [b"data"])},
    )

    rewritten, saved = image_fetcher.download_markdown_images(
        md,
        dest_dir=tmp_path / "pmid",
        vault_relative_prefix="KB/Attachments/pubmed/pmid",
    )

    assert saved == ["KB/Attachments/pubmed/pmid/img-1.png"]  # 只一條
    assert fake.requested.count("https://cdn.example.com/x.png") == 1
    # 兩處 match 都 rewrite 成同一 rel path
    assert rewritten.count("![a](KB/Attachments/pubmed/pmid/img-1.png)") == 1
    assert rewritten.count("![b](KB/Attachments/pubmed/pmid/img-1.png)") == 1


def test_relative_url_with_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    md = "![rel](/static/fig-1.webp)"
    fake = _install_fake_client(
        monkeypatch,
        {
            "https://bmjopen.bmj.com/static/fig-1.webp": _FakeStream(
                200, "image/webp", [b"webpdata"]
            )
        },
    )

    rewritten, saved = image_fetcher.download_markdown_images(
        md,
        dest_dir=tmp_path,
        vault_relative_prefix="v",
        base_url="https://bmjopen.bmj.com/content/16/4/e116911",
    )

    assert saved == ["v/img-1.webp"]
    assert "![rel](v/img-1.webp)" in rewritten
    assert fake.requested == ["https://bmjopen.bmj.com/static/fig-1.webp"]


def test_no_images_returns_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    md = "# Title\n\nJust paragraphs.\n\nNo pictures here."
    _install_fake_client(monkeypatch, {})

    rewritten, saved = image_fetcher.download_markdown_images(
        md,
        dest_dir=tmp_path,
        vault_relative_prefix="v",
    )

    assert saved == []
    assert rewritten == md


def test_oversized_image_aborted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # 模擬超過 20MB 上限：用小 cap 驗證中止邏輯
    monkeypatch.setattr(image_fetcher, "_MAX_IMAGE_BYTES", 8)
    md = "![big](https://cdn.example.com/big.jpg)"
    _install_fake_client(
        monkeypatch,
        {
            "https://cdn.example.com/big.jpg": _FakeStream(
                200, "image/jpeg", [b"abcd", b"efgh", b"ijkl"]
            )
        },
    )

    rewritten, saved = image_fetcher.download_markdown_images(
        md,
        dest_dir=tmp_path / "p",
        vault_relative_prefix="p",
    )

    assert saved == []
    assert rewritten == md
    assert not (tmp_path / "p" / "img-1.jpg").exists()


def test_unknown_image_subtype_uses_subtype(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    md = "![heif](https://cdn.example.com/x)"
    _install_fake_client(
        monkeypatch,
        {"https://cdn.example.com/x": _FakeStream(200, "image/heic", [b"heicbytes"])},
    )

    rewritten, saved = image_fetcher.download_markdown_images(
        md,
        dest_dir=tmp_path,
        vault_relative_prefix="v",
    )

    assert saved == ["v/img-1.heic"]
    assert "![heif](v/img-1.heic)" in rewritten


def test_octet_stream_fallback_to_url_extension(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    md = "![cdn](https://cdn.example.com/fig.png?v=3)"
    _install_fake_client(
        monkeypatch,
        {
            "https://cdn.example.com/fig.png?v=3": _FakeStream(
                200, "application/octet-stream", [b"PNGdata"]
            )
        },
    )

    rewritten, saved = image_fetcher.download_markdown_images(
        md,
        dest_dir=tmp_path,
        vault_relative_prefix="v",
    )

    assert saved == ["v/img-1.png"]
    assert "![cdn](v/img-1.png)" in rewritten
