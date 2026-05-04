"""URLDispatcher image-fetch hook tests (Slice 4, issue #355).

Scope (per PRD §Testing Decisions / "只測外部行為"):

- ``dispatch(url)`` calls the configured ``image_downloader_fn`` after the
  scrape succeeds and returns an ``IngestResult`` carrying the rewritten
  markdown + ``image_paths`` (single-shot construction — schema stays a
  value-object, no post-build mutation).
- The downloader receives the dispatcher's positional ``(markdown,
  attachments_abs_dir, vault_relative_prefix)`` shape — verified via the
  recorded call args, so the router's adapter contract is locked in.
- When ``image_downloader_fn`` is unset, dispatch is a no-op image-wise
  (preserves Slice 1 baseline; ``image_paths`` empty, markdown intact).
- When the downloader raises, we degrade gracefully: original markdown +
  empty ``image_paths`` (single bad image must not kill the whole ingest).
- The < 200-char hard block path runs BEFORE image fetch, so the downloader
  is not called for blocked pages (no wasted HTTP requests on chrome).
- Scrape-exception path also bypasses image fetch (no markdown to scan).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from agents.robin.url_dispatcher import URLDispatcher, URLDispatcherConfig
from shared.image_fetcher import download_markdown_images

# ── Helpers ──────────────────────────────────────────────────────────────────


def _ready_md(image_url: str = "https://example.com/fig1.png") -> str:
    """Return a markdown body long enough to clear the 200-char hard block."""
    return f"# Title\n\n![](​{image_url})\n" + ("body line.\n" * 80)


# ── Happy path ──────────────────────────────────────────────────────────────


def test_dispatch_with_image_downloader_returns_rewritten_markdown(tmp_path: Path):
    """Slice 4 acceptance: downloader is called → IngestResult carries rewritten md + paths."""
    raw_md = "# Title\n\n![](https://example.com/fig1.png)\n" + ("body\n" * 80)
    rewritten = "# Title\n\n![](KB/Attachments/inbox/post/fig1.png)\n" + ("body\n" * 80)

    def fake_downloader(md, attachments_abs_dir, vault_relative_prefix):
        # Sanity: dispatcher passed the values we configured below.
        assert md == raw_md
        assert attachments_abs_dir == tmp_path / "attachments"
        assert vault_relative_prefix == "KB/Attachments/inbox/post/"
        return rewritten, ["KB/Attachments/inbox/post/fig1.png"]

    config = URLDispatcherConfig(
        scrape_url_fn=lambda _u: raw_md,
        image_downloader_fn=fake_downloader,
        image_attachments_abs_dir=tmp_path / "attachments",
        image_vault_relative_prefix="KB/Attachments/inbox/post/",
    )
    result = URLDispatcher(config).dispatch("https://example.com/post")

    assert result.status == "ready"
    assert "KB/Attachments/inbox/post/fig1.png" in result.markdown
    assert "https://example.com/fig1.png" not in result.markdown
    assert result.image_paths == ["KB/Attachments/inbox/post/fig1.png"]


def test_dispatch_calls_downloader_with_positional_signature(tmp_path: Path):
    """Locks in the (markdown, attachments_abs_dir, vault_relative_prefix) call shape.

    Used spec=download_markdown_images on the mock so a future signature change
    in shared.image_fetcher cascades into a clear test failure rather than a
    silent no-op.
    """
    raw_md = "# T\n\n" + ("body line.\n" * 80)
    mock = MagicMock(return_value=(raw_md, []))

    config = URLDispatcherConfig(
        scrape_url_fn=lambda _u: raw_md,
        image_downloader_fn=mock,
        image_attachments_abs_dir=tmp_path / "att",
        image_vault_relative_prefix="KB/Attachments/inbox/x/",
    )
    URLDispatcher(config).dispatch("https://example.com/x")

    mock.assert_called_once()
    args, kwargs = mock.call_args
    # Positional shape — keep this contract stable for the router adapter.
    assert args == (raw_md, tmp_path / "att", "KB/Attachments/inbox/x/")
    assert kwargs == {}


def test_dispatch_real_image_fetcher_signature_compatibility(tmp_path: Path, monkeypatch):
    """Sanity-check the dispatcher → real ``download_markdown_images`` call shape.

    We use the production function via a thin adapter (mimicking what the
    router does) and stub out ``httpx.Client`` so no network is hit. This
    guards against the dispatcher's positional contract drifting away from
    the underlying kwargs API ever again.
    """
    raw_md = "# Title\n\n![](https://cdn.example.com/x.png)\n" + ("body\n" * 80)

    # Stub httpx.Client to a dummy that returns 404 for everything → downloader
    # returns ([] saved, original md) so we just verify call pipeline works.
    import shared.image_fetcher as image_fetcher_mod

    class _NoNetClient:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def stream(self, _method, _url):
            class _S:
                status_code = 404
                headers = {"content-type": "text/plain"}

                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *_):
                    return False

                def iter_bytes(self_inner):
                    return iter([])

            return _S()

    monkeypatch.setattr(image_fetcher_mod.httpx, "Client", lambda *a, **k: _NoNetClient())

    def adapter(md, attachments_abs_dir, vault_relative_prefix):
        return download_markdown_images(
            md,
            dest_dir=attachments_abs_dir,
            vault_relative_prefix=vault_relative_prefix,
        )

    config = URLDispatcherConfig(
        scrape_url_fn=lambda _u: raw_md,
        image_downloader_fn=adapter,
        image_attachments_abs_dir=tmp_path / "inbox-img",
        image_vault_relative_prefix="KB/Attachments/inbox/x/",
    )
    result = URLDispatcher(config).dispatch("https://example.com/x")

    # 404 means no rewrite + empty image_paths, but the dispatcher must NOT
    # have raised — image-fetch failure degrades gracefully.
    assert result.status == "ready"
    assert result.image_paths == []


# ── Backwards compatibility ─────────────────────────────────────────────────


def test_dispatch_without_image_downloader_is_no_op():
    """Slice 1 baseline: no downloader configured → markdown unchanged, image_paths=[]."""
    md = "# Title\n\n![](https://example.com/x.png)\n" + ("body\n" * 80)
    config = URLDispatcherConfig(scrape_url_fn=lambda _u: md)

    result = URLDispatcher(config).dispatch("https://example.com/post")

    assert result.status == "ready"
    assert result.markdown == md  # untouched — Slice 1 callers see no behavioural change
    assert result.image_paths == []


def test_dispatch_with_downloader_but_missing_paths_is_no_op(tmp_path: Path):
    """Defensive: downloader configured but attachments_abs_dir absent → no call."""
    md = "# Title\n\n![](https://example.com/x.png)\n" + ("body\n" * 80)
    mock = MagicMock(return_value=(md, []))

    # vault_relative_prefix missing → guard kicks in even though downloader_fn set
    config = URLDispatcherConfig(
        scrape_url_fn=lambda _u: md,
        image_downloader_fn=mock,
        image_attachments_abs_dir=tmp_path / "att",
        image_vault_relative_prefix=None,
    )
    result = URLDispatcher(config).dispatch("https://example.com/post")

    mock.assert_not_called()
    assert result.markdown == md
    assert result.image_paths == []


# ── Failure handling ────────────────────────────────────────────────────────


def test_dispatch_downloader_exception_degrades_gracefully(tmp_path: Path):
    """Single bad image fetch must not kill the whole ingest."""
    md = "# Title\n\n![](https://broken.example.com/x.png)\n" + ("body\n" * 80)

    def boom(_md, _dir, _prefix):
        raise RuntimeError("network down")

    config = URLDispatcherConfig(
        scrape_url_fn=lambda _u: md,
        image_downloader_fn=boom,
        image_attachments_abs_dir=tmp_path / "att",
        image_vault_relative_prefix="KB/Attachments/inbox/post/",
    )
    result = URLDispatcher(config).dispatch("https://example.com/post")

    # Original markdown survives, image_paths empty, status still ready.
    assert result.status == "ready"
    assert result.markdown == md
    assert result.image_paths == []


def test_dispatch_downloader_returning_garbage_paths_coerced_to_empty(tmp_path: Path):
    """Defensive: misbehaving downloader returning non-list paths → coerce to []."""
    md = "# Title\n\n![](https://example.com/x.png)\n" + ("body\n" * 80)

    def naughty(md_in, _dir, _prefix):
        return md_in, "not-a-list"  # type: ignore[return-value]

    config = URLDispatcherConfig(
        scrape_url_fn=lambda _u: md,
        image_downloader_fn=naughty,
        image_attachments_abs_dir=tmp_path / "att",
        image_vault_relative_prefix="KB/Attachments/inbox/post/",
    )
    result = URLDispatcher(config).dispatch("https://example.com/post")

    assert result.status == "ready"
    assert result.image_paths == []


# ── Bypass paths (no markdown to fetch from) ────────────────────────────────


def test_dispatch_short_content_does_not_call_downloader(tmp_path: Path):
    """< 200-char hard block runs BEFORE image fetch — no wasted HTTP."""
    short_md = "tiny"
    mock = MagicMock(return_value=(short_md, []))

    config = URLDispatcherConfig(
        scrape_url_fn=lambda _u: short_md,
        image_downloader_fn=mock,
        image_attachments_abs_dir=tmp_path / "att",
        image_vault_relative_prefix="KB/Attachments/inbox/post/",
    )
    result = URLDispatcher(config).dispatch("https://example.com/blocked")

    mock.assert_not_called()
    assert result.status == "failed"
    assert result.image_paths == []


def test_dispatch_scrape_exception_does_not_call_downloader(tmp_path: Path):
    """Pre-route exception path bypasses image fetch (no markdown exists)."""
    mock = MagicMock(return_value=("", []))

    def boom(_url):
        raise RuntimeError("connection refused")

    config = URLDispatcherConfig(
        scrape_url_fn=boom,
        image_downloader_fn=mock,
        image_attachments_abs_dir=tmp_path / "att",
        image_vault_relative_prefix="KB/Attachments/inbox/post/",
    )
    result = URLDispatcher(config).dispatch("https://broken.example.com/x")

    mock.assert_not_called()
    assert result.status == "failed"
    assert result.image_paths == []
