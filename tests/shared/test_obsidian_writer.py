"""shared.obsidian_writer：17 個模組共用的 vault 寫入底層（2026-11 測試審計補的防線）。

- 不准寫／追加／刪除 ``Journals/``（CLAUDE.md「Vault 寫入規則」）。
- 不准跳出 vault（``..``、絕對路徑）。
- ``delete_page`` 移到 vault 的 ``.trash/``，不永久刪除。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shared import obsidian_writer as ow


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    monkeypatch.setenv("VAULT_PATH", str(root))
    return root


def test_write_and_read_page_roundtrip(vault: Path) -> None:
    target = ow.write_page("KB/Wiki/Sources/a.md", {"title": "A"}, "body")

    assert target == (vault / "KB/Wiki/Sources/a.md").resolve()
    text = ow.read_page("KB/Wiki/Sources/a.md")
    assert text is not None and text.startswith("---\ntitle: A\n") and "body" in text


@pytest.mark.parametrize(
    "path",
    ["Journals/Daily/2026-11-14.md", "journals/Weekly/x.md", "JOURNALS/a.md"],
)
def test_write_page_refuses_journals(vault: Path, path: str) -> None:
    with pytest.raises(ow.VaultWriteForbidden, match="Journals"):
        ow.write_page(path, {}, "x")
    assert not (vault / "Journals").exists()


def test_append_refuses_journals(vault: Path) -> None:
    with pytest.raises(ow.VaultWriteForbidden):
        ow.append_to_file("Journals/Daily/2026-11-14.md", "x")


def test_delete_refuses_journals_and_leaves_file(vault: Path) -> None:
    journal = vault / "Journals" / "Daily" / "2026-11-14.md"
    journal.parent.mkdir(parents=True)
    journal.write_text("日記", encoding="utf-8")

    with pytest.raises(ow.VaultWriteForbidden):
        ow.delete_page("Journals/Daily/2026-11-14.md")

    assert journal.read_text(encoding="utf-8") == "日記"


@pytest.mark.parametrize("path", ["../outside.md", "KB/../../outside.md"])
def test_paths_escaping_the_vault_are_refused(vault: Path, path: str) -> None:
    with pytest.raises(ow.VaultWriteForbidden, match="不在 vault"):
        ow.write_page(path, {}, "x")
    assert not (vault.parent / "outside.md").exists()


def test_dotdot_into_journals_is_refused(vault: Path) -> None:
    with pytest.raises(ow.VaultWriteForbidden, match="Journals"):
        ow.write_page("KB/../Journals/x.md", {}, "x")


def test_absolute_path_outside_vault_is_refused(vault: Path, tmp_path: Path) -> None:
    with pytest.raises(ow.VaultWriteForbidden):
        ow.write_page(str(tmp_path / "elsewhere.md"), {}, "x")


def test_folder_merely_starting_with_journals_is_allowed(vault: Path) -> None:
    ow.write_page("JournalsArchive/x.md", {}, "x")
    assert (vault / "JournalsArchive" / "x.md").exists()


def test_delete_moves_to_trash_keeping_structure(vault: Path) -> None:
    ow.write_page("TaskNotes/Tasks/看牙醫.md", {"title": "看牙醫"}, "內容")

    assert ow.delete_page("TaskNotes/Tasks/看牙醫.md") is True

    assert not (vault / "TaskNotes/Tasks/看牙醫.md").exists()
    trashed = vault / ".trash" / "TaskNotes" / "Tasks" / "看牙醫.md"
    assert "內容" in trashed.read_text(encoding="utf-8")


def test_delete_same_name_twice_keeps_both_copies(vault: Path) -> None:
    ow.write_page("Projects/p.md", {}, "第一版")
    ow.delete_page("Projects/p.md")
    ow.write_page("Projects/p.md", {}, "第二版")
    ow.delete_page("Projects/p.md")

    trashed = sorted((vault / ".trash" / "Projects").glob("p*.md"))
    assert len(trashed) == 2
    bodies = {p.read_text(encoding="utf-8").split("\n\n", 1)[1].strip() for p in trashed}
    assert bodies == {"第一版", "第二版"}


def test_delete_missing_file_returns_false(vault: Path) -> None:
    assert ow.delete_page("TaskNotes/Tasks/nope.md") is False


def test_delete_inside_trash_is_a_noop(vault: Path) -> None:
    ow.write_page("x.md", {}, "x")
    ow.delete_page("x.md")

    assert ow.delete_page(".trash/x.md") is False
    assert (vault / ".trash" / "x.md").exists()
