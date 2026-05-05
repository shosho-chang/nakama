"""InboxWriter tests (Slice 1, issue #352).

Scope (per PRD §Testing Decisions):

- < 200 字 reject → file written with ``fulltext_status: failed`` + note.
- Filename collision counter: ``slug.md`` → ``slug-1.md`` → ``slug-2.md``
  (acceptance #5, mirroring the legacy /scrape-translate line 316-321 pattern).
- Frontmatter contains ``fulltext_status`` / ``fulltext_source`` /
  ``fulltext_layer`` / ``original_url`` (acceptance #3).
- Same-URL repeat → ``find_existing_for_url`` returns the existing path
  (acceptance #6).
- Placeholder write produces a status='processing' file the inbox view can
  render with the 🔄 icon.
"""

from __future__ import annotations

from pathlib import Path

from agents.robin.inbox_writer import InboxWriter
from shared.schemas.ingest_result import IngestResult
from shared.utils import extract_frontmatter, read_text


def _ready_result(url: str = "https://example.com/article") -> IngestResult:
    body = "# Hello\n\n" + ("body line.\n" * 80)
    return IngestResult(
        status="ready",
        fulltext_layer="readability",
        fulltext_source="Readability",
        markdown=body,
        title="Hello",
        original_url=url,
    )


def _failed_result(
    url: str = "https://example.com/blocked",
    note: str = "抓取結果太短，疑似 bot 擋頁",
) -> IngestResult:
    return IngestResult(
        status="failed",
        fulltext_layer="readability",
        fulltext_source="Readability",
        markdown="",
        title="example.com/blocked",
        original_url=url,
        note=note,
    )


# ── Frontmatter contract (acceptance #3) ─────────────────────────────────────


def test_write_to_inbox_frontmatter_contains_required_fields(tmp_path: Path):
    writer = InboxWriter(tmp_path)
    path = writer.write_to_inbox(_ready_result(), slug="hello")

    fm, _ = extract_frontmatter(read_text(path))
    assert fm["title"] == "Hello"
    assert fm["original_url"] == "https://example.com/article"
    assert fm["fulltext_status"] == "ready"
    assert fm["fulltext_layer"] == "readability"
    assert fm["fulltext_source"] == "Readability"
    # ``source`` retained for backward-compatibility with existing reader UI.
    assert fm["source"] == "https://example.com/article"


def test_write_to_inbox_writes_markdown_body(tmp_path: Path):
    writer = InboxWriter(tmp_path)
    result = _ready_result()
    path = writer.write_to_inbox(result, slug="hello")

    _, body = extract_frontmatter(read_text(path))
    assert "# Hello" in body
    assert "body line." in body


# ── < 200-char failed path (acceptance #4) ──────────────────────────────────


def test_write_to_inbox_failed_writes_note_into_frontmatter(tmp_path: Path):
    writer = InboxWriter(tmp_path)
    path = writer.write_to_inbox(_failed_result(), slug="blocked")

    content = read_text(path)
    fm, body = extract_frontmatter(content)
    assert fm["fulltext_status"] == "failed"
    assert fm["note"] == "抓取結果太短，疑似 bot 擋頁"
    # body contains the note so reader render shows something useful.
    assert "疑似 bot 擋頁" in body


def test_write_to_inbox_failed_body_contains_user_facing_note(tmp_path: Path):
    """Failed body must surface the < 200-char hint, not be a vacuous file.

    Without this check the file's mere existence (``st_size > 0``) would be
    trivially true — a frontmatter alone is non-empty. The user-facing hint
    is the actual contract: when the reader opens a failed row, they see
    "疑似 bot 擋頁" not a blank page.
    """
    writer = InboxWriter(tmp_path)
    path = writer.write_to_inbox(_failed_result(), slug="blocked")
    assert path.exists()
    body = read_text(path).split("---\n", 2)[-1]
    assert "疑似 bot 擋頁" in body


# ── Filename collision (acceptance #5) ───────────────────────────────────────


def test_write_to_inbox_collision_counter(tmp_path: Path):
    writer = InboxWriter(tmp_path)
    p1 = writer.write_to_inbox(_ready_result("https://a.com/1"), slug="dup")
    p2 = writer.write_to_inbox(_ready_result("https://a.com/2"), slug="dup")
    p3 = writer.write_to_inbox(_ready_result("https://a.com/3"), slug="dup")

    assert p1.name == "dup.md"
    assert p2.name == "dup-1.md"
    assert p3.name == "dup-2.md"
    assert p1.exists() and p2.exists() and p3.exists()


# ── Same-URL repeat detection (acceptance #6) ────────────────────────────────


def test_find_existing_for_url_returns_path_when_match(tmp_path: Path):
    writer = InboxWriter(tmp_path)
    path = writer.write_to_inbox(_ready_result("https://example.com/dup-url"), slug="article")

    found = writer.find_existing_for_url("https://example.com/dup-url")
    assert found == path


def test_find_existing_for_url_none_when_no_match(tmp_path: Path):
    writer = InboxWriter(tmp_path)
    writer.write_to_inbox(_ready_result("https://example.com/A"), slug="a")

    found = writer.find_existing_for_url("https://example.com/B")
    assert found is None


def test_find_existing_for_url_none_when_inbox_missing(tmp_path: Path):
    writer = InboxWriter(tmp_path / "nope")
    assert writer.find_existing_for_url("https://example.com/x") is None


def test_find_existing_for_url_skips_non_md(tmp_path: Path):
    """``find_existing_for_url`` only scans ``.md`` files."""
    writer = InboxWriter(tmp_path)
    # Place a .pdf file with the URL string in raw bytes — must NOT match.
    (tmp_path / "x.pdf").write_bytes(b"original_url: https://example.com/dup-url")
    found = writer.find_existing_for_url("https://example.com/dup-url")
    assert found is None


# ── Placeholder writer (slice 1 BackgroundTask integration) ──────────────────


def test_write_placeholder_creates_processing_file(tmp_path: Path):
    writer = InboxWriter(tmp_path)
    path = writer.write_placeholder(
        slug="pending",
        original_url="https://example.com/article",
        title="example.com/article",
    )
    assert path.exists()
    fm, body = extract_frontmatter(read_text(path))
    assert fm["fulltext_status"] == "processing"
    assert fm["original_url"] == "https://example.com/article"
    assert "處理中" in body or "正在後台抓取" in body


def test_write_placeholder_then_overwrite_in_place(tmp_path: Path):
    """BackgroundTask path: placeholder Path is reused as ``existing_path``."""
    writer = InboxWriter(tmp_path)
    placeholder = writer.write_placeholder(
        slug="pending",
        original_url="https://example.com/article",
        title="example.com/article",
    )
    assert placeholder.name == "pending.md"

    # BG task finishes, writer overwrites in place.
    result = _ready_result("https://example.com/article")
    final = writer.write_to_inbox(result, slug="pending", existing_path=placeholder)

    assert final == placeholder  # same path
    fm, _ = extract_frontmatter(read_text(final))
    assert fm["fulltext_status"] == "ready"  # overwrote the processing state
    # Also assert no second file got created.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["pending.md"]


def test_write_placeholder_collision_counter(tmp_path: Path):
    writer = InboxWriter(tmp_path)
    p1 = writer.write_placeholder(
        slug="dup",
        original_url="https://a.com/1",
        title="t",
    )
    p2 = writer.write_placeholder(
        slug="dup",
        original_url="https://a.com/2",
        title="t",
    )
    assert p1.name == "dup.md"
    assert p2.name == "dup-1.md"


# ── YAML safety (defensive — no per-acceptance criterion but legacy bug) ─────


def test_write_to_inbox_strips_newlines_in_frontmatter_values(tmp_path: Path):
    """Title with embedded newline must NOT corrupt YAML."""
    bad = IngestResult(
        status="ready",
        fulltext_layer="readability",
        fulltext_source="Readability",
        markdown="body" * 60,
        title="line one\nline two",
        original_url="https://example.com/bad",
    )
    writer = InboxWriter(tmp_path)
    path = writer.write_to_inbox(bad, slug="bad")
    fm, _ = extract_frontmatter(read_text(path))
    # Frontmatter still parseable + title rendered as a single line.
    assert "\n" not in fm["title"]


def test_write_to_inbox_round_trip_title_with_yaml_special_chars(tmp_path: Path):
    """Real-world headline punctuation must survive ``yaml.safe_load`` round-trip.

    Headlines routinely contain ``"``, ``:``, ``!``, and Windows-path-like
    backslashes — the writer must escape them inside double-quoted scalars
    so the same string comes back unchanged. ``feedback_yaml_scalar_safety``:
    test using real ``yaml.safe_load`` not substring assertion, so an
    over-aggressive escape that produces invalid YAML still fails this test.
    """
    import yaml

    nasty_title = 'He said "go" — back\\slash: now! exclaim'
    nasty_url = 'https://example.com/q?x="quoted"&y=back\\slash'
    nasty_note = 'multi "quote" and back\\slash and colon: here'
    result = IngestResult(
        status="failed",
        fulltext_layer="readability",
        fulltext_source='Display "label" with quotes',
        markdown="",
        title=nasty_title,
        original_url=nasty_url,
        note=nasty_note,
    )

    writer = InboxWriter(tmp_path)
    path = writer.write_to_inbox(result, slug="nasty")

    content = read_text(path)
    # Round-trip via real YAML parser — exposes any escape that produced
    # invalid YAML or stripped meaningful chars.
    raw_fm = content.split("---\n", 2)[1]
    parsed = yaml.safe_load(raw_fm)

    assert parsed["title"] == nasty_title
    assert parsed["original_url"] == nasty_url
    assert parsed["source"] == nasty_url
    assert parsed["fulltext_source"] == 'Display "label" with quotes'
    assert parsed["note"] == nasty_note


# ── Slice 1 #389 — Zotero frontmatter + dedup ───────────────────────────────


def _zotero_ready_result(item_key: str = "ZOTERO12") -> IngestResult:
    body = "# Sample Paper\n\n" + ("body line.\n" * 80)
    return IngestResult(
        status="ready",
        fulltext_layer="zotero_html_snapshot",
        fulltext_source="Zotero HTML snapshot",
        markdown=body,
        title="Sample Paper",
        original_url=f"zotero://select/library/items/{item_key}",
        zotero_item_key=item_key,
        zotero_attachment_path=f"/Zotero/storage/HTML0001/snapshot.html",
        attachment_type="text/html",
    )


def test_write_to_inbox_with_zotero_result_emits_zotero_frontmatter(tmp_path: Path):
    """Frontmatter contains zotero_item_key / zotero_attachment_path / attachment_type."""
    writer = InboxWriter(tmp_path)
    writer.write_to_inbox(_zotero_ready_result(), slug="sample-paper")

    fm, _ = extract_frontmatter(read_text(tmp_path / "sample-paper.md"))
    assert fm["zotero_item_key"] == "ZOTERO12"
    assert fm["zotero_attachment_path"] == "/Zotero/storage/HTML0001/snapshot.html"
    assert fm["attachment_type"] == "text/html"


def test_write_to_inbox_with_zotero_result_overrides_source_type(tmp_path: Path):
    """source_type auto-overrides to ``zotero`` when result has zotero_item_key,
    regardless of the kwarg passed by caller."""
    writer = InboxWriter(tmp_path)
    writer.write_to_inbox(_zotero_ready_result(), slug="sample-paper", source_type="article")

    fm, _ = extract_frontmatter(read_text(tmp_path / "sample-paper.md"))
    assert fm["source_type"] == "zotero"


def test_write_to_inbox_non_zotero_result_omits_zotero_fields(tmp_path: Path):
    """Existing URL ingest path stays clean — no zotero_* keys leak into frontmatter."""
    writer = InboxWriter(tmp_path)
    writer.write_to_inbox(_ready_result(), slug="hello")

    fm, _ = extract_frontmatter(read_text(tmp_path / "hello.md"))
    assert "zotero_item_key" not in fm
    assert "zotero_attachment_path" not in fm
    assert "attachment_type" not in fm
    assert fm["source_type"] == "article"  # default unchanged


def test_find_existing_for_zotero_item_returns_match(tmp_path: Path):
    """After writing a Zotero-sourced inbox file, lookup by item_key finds it."""
    writer = InboxWriter(tmp_path)
    expected = writer.write_to_inbox(_zotero_ready_result(item_key="ZOTERO12"), slug="sample")

    found = writer.find_existing_for_zotero_item("ZOTERO12")
    assert found == expected


def test_find_existing_for_zotero_item_returns_none_when_no_match(tmp_path: Path):
    """Mismatched item_key → None (no false positives via partial-string match)."""
    writer = InboxWriter(tmp_path)
    writer.write_to_inbox(_zotero_ready_result(item_key="ZOTERO12"), slug="sample")

    assert writer.find_existing_for_zotero_item("OTHER999") is None


def test_find_existing_for_zotero_item_ignores_url_only_files(tmp_path: Path):
    """Inbox files from URL ingest (no zotero_item_key frontmatter) → not matched."""
    writer = InboxWriter(tmp_path)
    writer.write_to_inbox(_ready_result(url="https://example.com/x"), slug="url-article")

    # Looking up any Zotero key should miss — URL inbox file lacks zotero_item_key.
    assert writer.find_existing_for_zotero_item("ZOTERO12") is None
