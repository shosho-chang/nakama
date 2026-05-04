"""Unit tests for ``shared.discard_service.DiscardService`` (Slice 5, issue #356).

Scope (per PRD §Testing Decisions):

- ``_send_to_recycle_bin`` is invoked with the file path (mocked subprocess
  on Windows; direct unlink on Linux).
- annotation 連動刪：``KB/Annotations/{slug}.md`` 也被送進回收桶 — 兩種 case
  （存在 / 不存在）。
- annotation 計數正確（無 / 1 / N 條）— ``DiscardReport.annotation_count``
  反映 confirm prompt 上要顯示的數字。
- 檔案不存在不噴錯（idempotency for double-click on the discard button) —
  ``DiscardReport.deleted_file`` 標記 False。
- ``annotation_count_for()`` standalone — frontend 在按下按鈕前可以查 count
  生 confirm 文字。

Mocking notes:
- ``recycle_bin_fn`` 是 DI ctor arg → tests pass a ``MagicMock`` directly
  (no need to patch module-level subprocess.run).
- ``annotation_store`` 也是 DI ctor arg → tests pass an
  ``AnnotationStore(spec=AnnotationStore)`` mock or a fresh real store
  pointed at ``tmp_path`` via ``VAULT_PATH`` monkeypatch.
- ``annotations_dir`` ctor arg lets tests skip the ``get_vault_path()``
  late-import dance entirely.
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from shared.annotation_store import (
    AnnotationSet,
    AnnotationStore,
    Highlight,
)
from shared.discard_service import DiscardReport, DiscardService, _send_to_recycle_bin

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def vault(tmp_path: Path, monkeypatch) -> Path:
    """Vault dir + KB/Annotations + Inbox/kb subdirs ready."""
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    (tmp_path / "Inbox" / "kb").mkdir(parents=True)
    (tmp_path / "KB" / "Annotations").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def annotations_dir(vault: Path) -> Path:
    return vault / "KB" / "Annotations"


@pytest.fixture
def inbox(vault: Path) -> Path:
    return vault / "Inbox" / "kb"


def _write_source(path: Path, *, title: str = "Test Article") -> None:
    """Helper — write a minimal source file with a known frontmatter title."""
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


# ── _send_to_recycle_bin (lifted from robin.py) ──────────────────────────────


def test_send_to_recycle_bin_linux_unlinks(tmp_path: Path, monkeypatch):
    """Non-Windows path: file gets unlinked directly."""
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    f = tmp_path / "foo.txt"
    f.write_text("x")
    _send_to_recycle_bin(f)
    assert not f.exists()


def test_send_to_recycle_bin_missing_path_no_raise(tmp_path: Path, monkeypatch):
    """Missing path is a no-op, not an error (matches Path.unlink(missing_ok=True))."""
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    _send_to_recycle_bin(tmp_path / "nonexistent.txt")  # no raise


def test_send_to_recycle_bin_windows_invokes_powershell(tmp_path: Path, monkeypatch):
    """Windows: shell out to PowerShell with the exact prefix-match-safe command.

    The exact command shape matters — see
    ``feedback_powershell_allow_exact_prefix.md``: ``.claude/settings.json``
    allow rule is a prefix match, so the test pins the prefix to catch
    regressions like adding ``-NoProfile``.
    """
    monkeypatch.setattr(platform, "system", lambda: "Windows")

    captured = {}

    def fake_run(args, check):
        captured["args"] = args
        captured["check"] = check
        return MagicMock(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    f = tmp_path / "foo.txt"
    f.write_text("x")
    _send_to_recycle_bin(f)

    assert captured["args"][0] == "powershell"
    assert captured["args"][1] == "-Command"
    assert captured["args"][2].startswith(
        "Add-Type -AssemblyName Microsoft.VisualBasic; "
        "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("
    )
    assert "SendToRecycleBin" in captured["args"][2]
    assert captured["check"] is False


# ── annotation_count_for ─────────────────────────────────────────────────────


def test_annotation_count_for_no_annotation_returns_zero(inbox: Path, annotations_dir: Path):
    """No annotation file → count == 0 (most common case for fresh inbox files)."""
    src = inbox / "fresh.md"
    _write_source(src, title="Fresh Article")

    service = DiscardService(annotations_dir=annotations_dir)
    slug, count = service.annotation_count_for(src)

    assert slug == "fresh-article"  # derived from frontmatter title
    assert count == 0


def test_annotation_count_for_with_two_items(inbox: Path, annotations_dir: Path):
    """Two annotation items → count == 2 (matches confirm-prompt manual smoke)."""
    src = inbox / "studied.md"
    _write_source(src, title="Studied Paper")

    # AnnotationStore writes to ``vault/KB/Annotations/{slug}.md`` — under our
    # ``annotations_dir`` fixture which points there.
    ann_set = AnnotationSet(
        slug="studied-paper",
        source_filename="studied.md",
        base="inbox",
        items=[
            Highlight(text="first"),
            Highlight(text="second"),
        ],
    )
    store = AnnotationStore()
    store.save(ann_set)

    service = DiscardService(annotation_store=store, annotations_dir=annotations_dir)
    slug, count = service.annotation_count_for(src)

    assert slug == "studied-paper"
    assert count == 2


def test_annotation_count_for_falls_back_to_filename_when_file_missing(
    inbox: Path, annotations_dir: Path
):
    """Missing source file → slug derived from filename stem (no frontmatter to read)."""
    src = inbox / "ghost.md"
    # File never written — annotation_count_for must still produce a slug.

    service = DiscardService(annotations_dir=annotations_dir)
    slug, count = service.annotation_count_for(src)

    assert slug == "ghost"  # filename stem
    assert count == 0


# ── discard() — happy path ───────────────────────────────────────────────────


def test_discard_recycles_source_file(inbox: Path, annotations_dir: Path):
    """Source file → recycle bin call invoked exactly once with that path."""
    src = inbox / "to-trash.md"
    _write_source(src, title="To Trash")

    fake_recycle = MagicMock()
    service = DiscardService(recycle_bin_fn=fake_recycle, annotations_dir=annotations_dir)
    report = service.discard(src, base="inbox")

    fake_recycle.assert_called_once_with(src)
    assert report.deleted_file is True
    assert report.file_path == src


def test_discard_returns_report_with_zero_annotations(inbox: Path, annotations_dir: Path):
    """No annotation file → report.annotation_count=0, annotation_deleted=False."""
    src = inbox / "lonely.md"
    _write_source(src, title="Lonely")

    fake_recycle = MagicMock()
    service = DiscardService(recycle_bin_fn=fake_recycle, annotations_dir=annotations_dir)
    report = service.discard(src, base="inbox")

    assert report.annotation_count == 0
    assert report.annotation_deleted is False
    # Only the source got recycled, no annotation companion call.
    assert fake_recycle.call_count == 1


# ── discard() — annotation 連動刪 ────────────────────────────────────────────


def test_discard_recycles_annotation_when_present(inbox: Path, annotations_dir: Path, vault: Path):
    """When ``KB/Annotations/{slug}.md`` exists → both files sent to recycle bin."""
    src = inbox / "with-notes.md"
    _write_source(src, title="With Notes")

    ann_set = AnnotationSet(
        slug="with-notes",
        source_filename="with-notes.md",
        base="inbox",
        items=[
            Highlight(text="key insight"),
            Highlight(text="another note"),
            Highlight(text="third highlight"),
        ],
    )
    store = AnnotationStore()
    store.save(ann_set)
    ann_path = annotations_dir / "with-notes.md"
    assert ann_path.exists()

    fake_recycle = MagicMock()
    service = DiscardService(
        annotation_store=store,
        recycle_bin_fn=fake_recycle,
        annotations_dir=annotations_dir,
    )
    report = service.discard(src, base="inbox")

    # Two recycle-bin calls: source first, then annotation
    assert fake_recycle.call_count == 2
    fake_recycle.assert_any_call(src)
    fake_recycle.assert_any_call(ann_path)

    assert report.annotation_count == 3
    assert report.annotation_deleted is True
    assert report.deleted_file is True
    assert report.slug == "with-notes"


def test_discard_skips_annotation_call_when_not_present(inbox: Path, annotations_dir: Path):
    """No annotation file → recycle_bin called only once (for the source)."""
    src = inbox / "no-notes.md"
    _write_source(src, title="No Notes")

    fake_recycle = MagicMock()
    service = DiscardService(recycle_bin_fn=fake_recycle, annotations_dir=annotations_dir)
    report = service.discard(src, base="inbox")

    assert fake_recycle.call_count == 1
    fake_recycle.assert_called_once_with(src)
    assert report.annotation_deleted is False


# ── discard() — idempotency / error edges ───────────────────────────────────


def test_discard_missing_file_does_not_raise(inbox: Path, annotations_dir: Path):
    """Double-clicking the discard button must be a no-op, not a 500."""
    src = inbox / "ghost.md"  # not written

    fake_recycle = MagicMock()
    service = DiscardService(recycle_bin_fn=fake_recycle, annotations_dir=annotations_dir)
    report = service.discard(src, base="inbox")

    # Source recycle skipped because file did not exist.
    fake_recycle.assert_not_called()
    assert report.deleted_file is False
    assert report.annotation_deleted is False
    assert report.annotation_count == 0


def test_discard_missing_file_still_recycles_orphan_annotation(inbox: Path, annotations_dir: Path):
    """Source already gone but annotation orphaned → annotation still recycled.

    Edge case: if the source file was deleted manually outside Reader (修修
    moved it via Finder) the annotation companion is now an orphan. The
    discard endpoint should still clean it up so vault doesn't pile up
    KB/Annotations/* with no matching source.
    """
    src = inbox / "ghost.md"  # not written

    ann_set = AnnotationSet(
        slug="ghost",
        source_filename="ghost.md",
        base="inbox",
        items=[Highlight(text="orphan note")],
    )
    store = AnnotationStore()
    store.save(ann_set)
    ann_path = annotations_dir / "ghost.md"

    fake_recycle = MagicMock()
    service = DiscardService(
        annotation_store=store,
        recycle_bin_fn=fake_recycle,
        annotations_dir=annotations_dir,
    )
    report = service.discard(src, base="inbox")

    fake_recycle.assert_called_once_with(ann_path)
    assert report.deleted_file is False
    assert report.annotation_deleted is True
    assert report.annotation_count == 1


# ── DiscardReport contract ──────────────────────────────────────────────────


def test_discard_report_is_frozen_dataclass():
    """Report is intentionally immutable — caller flash-message logic shouldn't mutate."""
    report = DiscardReport(
        file_path=Path("/tmp/foo"),
        slug="foo",
        annotation_count=0,
        deleted_file=True,
        annotation_deleted=False,
    )
    with pytest.raises(Exception):  # FrozenInstanceError on dataclass
        report.deleted_file = False  # type: ignore[misc]


def test_default_annotations_dir_uses_vault_path(inbox: Path, vault: Path, monkeypatch):
    """Without ``annotations_dir`` override → resolves via ``shared.config.get_vault_path``.

    Ensures ``VAULT_PATH`` monkeypatches in tests + production wiring both
    flow into the same path resolution (no hardcoded path in the service).
    """
    src = inbox / "vault-routed.md"
    _write_source(src, title="Vault Routed")

    ann_set = AnnotationSet(
        slug="vault-routed",
        source_filename="vault-routed.md",
        base="inbox",
        items=[Highlight(text="x")],
    )
    store = AnnotationStore()
    store.save(ann_set)

    fake_recycle = MagicMock()
    # NOTE: no ``annotations_dir`` ctor arg → service falls back to get_vault_path()
    service = DiscardService(annotation_store=store, recycle_bin_fn=fake_recycle)
    report = service.discard(src, base="inbox")

    assert report.annotation_deleted is True
    assert report.annotation_count == 1
    # Both calls used the vault-rooted KB/Annotations path.
    expected_ann = vault / "KB" / "Annotations" / "vault-routed.md"
    fake_recycle.assert_any_call(expected_ann)
