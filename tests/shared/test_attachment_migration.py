"""Tests for shared.attachment_migration (ADR-028 §7, PR-A1)."""

from __future__ import annotations

from pathlib import Path

from shared.attachment_migration import migrate_slug_attachments


def _seed_inbox(inbox: Path, slug: str, files: dict[str, bytes]) -> None:
    att = inbox / "attachments" / slug
    att.mkdir(parents=True)
    for name, content in files.items():
        (att / name).write_bytes(content)


def test_happy_path_moves_files_and_rewrites_refs(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    inbox = vault / "Inbox" / "kb"
    inbox.mkdir(parents=True)
    slug = "my-article"
    _seed_inbox(inbox, slug, {"img-1.png": b"PNG", "img-2.webp": b"WEBP"})

    raw = vault / "KB" / "Raw" / "Articles" / f"{slug}.md"
    raw.parent.mkdir(parents=True)
    raw.write_text(
        f"# Title\n\n![](attachments/{slug}/img-1.png)\n\n![alt](attachments/{slug}/img-2.webp)\n",
        encoding="utf-8",
    )

    result = migrate_slug_attachments(slug, inbox, vault, rewrite_in_files=[raw])

    assert result.source_missing is False
    assert sorted(result.moved_files) == [
        f"KB/Attachments/{slug}/img-1.png",
        f"KB/Attachments/{slug}/img-2.webp",
    ]
    assert result.rewritten_markdown == [f"KB/Raw/Articles/{slug}.md"]
    # Files landed at target
    assert (vault / "KB" / "Attachments" / slug / "img-1.png").read_bytes() == b"PNG"
    assert (vault / "KB" / "Attachments" / slug / "img-2.webp").read_bytes() == b"WEBP"
    # Source folder cleaned up
    assert not (inbox / "attachments" / slug).exists()
    # Refs rewritten
    text = raw.read_text(encoding="utf-8")
    assert f"KB/Attachments/{slug}/img-1.png" in text
    assert f"KB/Attachments/{slug}/img-2.webp" in text
    assert f"attachments/{slug}/" not in text


def test_source_missing_is_noop_but_still_rewrites_refs(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    inbox = vault / "Inbox" / "kb"
    inbox.mkdir(parents=True)
    slug = "ghost"
    raw = vault / "KB" / "Raw" / "Articles" / f"{slug}.md"
    raw.parent.mkdir(parents=True)
    raw.write_text(f"![](attachments/{slug}/img-1.png)\n", encoding="utf-8")

    result = migrate_slug_attachments(slug, inbox, vault, rewrite_in_files=[raw])

    assert result.source_missing is True
    assert result.moved_files == []
    # Ref rewrite still happens — caller may have inbox attachments handled
    # elsewhere; we still normalize the markdown.
    assert result.rewritten_markdown == [f"KB/Raw/Articles/{slug}.md"]
    assert "KB/Attachments/ghost/img-1.png" in raw.read_text(encoding="utf-8")


def test_idempotent_rerun_is_safe(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    inbox = vault / "Inbox" / "kb"
    inbox.mkdir(parents=True)
    slug = "twice"
    _seed_inbox(inbox, slug, {"img-1.png": b"AAA"})
    raw = vault / "KB" / "Raw" / "Articles" / f"{slug}.md"
    raw.parent.mkdir(parents=True)
    raw.write_text(f"![](attachments/{slug}/img-1.png)\n", encoding="utf-8")

    r1 = migrate_slug_attachments(slug, inbox, vault, rewrite_in_files=[raw])
    assert r1.moved_files == [f"KB/Attachments/{slug}/img-1.png"]

    # Re-seed inbox with identical content — simulate flaky re-run
    _seed_inbox(inbox, slug, {"img-1.png": b"AAA"})
    r2 = migrate_slug_attachments(slug, inbox, vault, rewrite_in_files=[raw])
    assert r2.moved_files == []
    assert r2.skipped_files == [f"KB/Attachments/{slug}/img-1.png"]
    # Markdown ref already rewritten — second pass finds nothing to change
    assert r2.rewritten_markdown == []


def test_dst_collision_with_different_content_is_skipped(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    inbox = vault / "Inbox" / "kb"
    inbox.mkdir(parents=True)
    slug = "collide"
    _seed_inbox(inbox, slug, {"img-1.png": b"NEW"})
    # Pre-existing different content at destination
    dst = vault / "KB" / "Attachments" / slug
    dst.mkdir(parents=True)
    (dst / "img-1.png").write_bytes(b"OLD")

    result = migrate_slug_attachments(slug, inbox, vault)
    assert result.moved_files == []
    assert result.skipped_files == [f"KB/Attachments/{slug}/img-1.png"]
    # Destination unchanged
    assert (dst / "img-1.png").read_bytes() == b"OLD"


def test_no_rewrite_files_argument(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    inbox = vault / "Inbox" / "kb"
    inbox.mkdir(parents=True)
    slug = "a"
    _seed_inbox(inbox, slug, {"x.png": b"X"})

    result = migrate_slug_attachments(slug, inbox, vault)
    assert result.moved_files == [f"KB/Attachments/{slug}/x.png"]
    assert result.rewritten_markdown == []


def test_rewrite_target_missing_is_skipped(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    inbox = vault / "Inbox" / "kb"
    inbox.mkdir(parents=True)
    slug = "s"
    _seed_inbox(inbox, slug, {"a.png": b"A"})
    missing = vault / "KB" / "Raw" / "Articles" / "missing.md"

    result = migrate_slug_attachments(slug, inbox, vault, rewrite_in_files=[missing])
    assert result.moved_files == [f"KB/Attachments/{slug}/a.png"]
    assert result.rewritten_markdown == []
