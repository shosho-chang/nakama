"""shared/vault_rules.py 的 unit tests。"""

import pytest

from shared.vault_rules import VaultRuleViolation, assert_nami_can_read, assert_nami_can_write


class TestAssertNamiCanWrite:
    def test_allows_nami_notes(self):
        assert_nami_can_write("Nami/Notes/sales-kit.md")  # no exception

    def test_allows_nested_nami_notes(self):
        assert_nami_can_write("Nami/Notes/subfolder/report.md")

    def test_rejects_journals(self):
        with pytest.raises(VaultRuleViolation, match="不可寫入"):
            assert_nami_can_write("Journals/diary.md")

    def test_rejects_kb_wiki(self):
        with pytest.raises(VaultRuleViolation, match="不可寫入"):
            assert_nami_can_write("KB/Wiki/foo.md")

    def test_rejects_kb_raw(self):
        with pytest.raises(VaultRuleViolation, match="不可寫入"):
            assert_nami_can_write("KB/Raw/article.md")

    def test_rejects_projects(self):
        with pytest.raises(VaultRuleViolation, match="不可寫入"):
            assert_nami_can_write("Projects/my-project.md")

    def test_rejects_task_notes(self):
        with pytest.raises(VaultRuleViolation, match="不可寫入"):
            assert_nami_can_write("TaskNotes/Tasks/foo.md")

    def test_rejects_path_traversal(self):
        with pytest.raises(VaultRuleViolation, match="traversal"):
            assert_nami_can_write("Nami/Notes/../KB/Raw/steal.md")

    def test_rejects_absolute_path(self):
        with pytest.raises(VaultRuleViolation, match="vault-relative"):
            assert_nami_can_write("/etc/passwd")

    def test_rejects_empty_path(self):
        with pytest.raises(VaultRuleViolation):
            assert_nami_can_write("")

    def test_rejects_whitespace_path(self):
        with pytest.raises(VaultRuleViolation):
            assert_nami_can_write("   ")


class TestAssertNamiCanRead:
    def test_allows_nami_notes(self):
        assert_nami_can_read("Nami/Notes/old-kit.md")

    def test_allows_projects(self):
        assert_nami_can_read("Projects/my-project.md")

    def test_allows_task_notes(self):
        assert_nami_can_read("TaskNotes/Tasks/task-001.md")

    def test_rejects_journals(self):
        with pytest.raises(VaultRuleViolation, match="不可讀取"):
            assert_nami_can_read("Journals/diary.md")

    def test_rejects_kb_wiki(self):
        with pytest.raises(VaultRuleViolation, match="不可讀取"):
            assert_nami_can_read("KB/Wiki/article.md")

    def test_rejects_kb_raw(self):
        with pytest.raises(VaultRuleViolation, match="不可讀取"):
            assert_nami_can_read("KB/Raw/source.md")

    def test_rejects_path_traversal(self):
        with pytest.raises(VaultRuleViolation, match="traversal"):
            assert_nami_can_read("Nami/Notes/../Journals/diary.md")

    def test_rejects_absolute_path(self):
        with pytest.raises(VaultRuleViolation, match="vault-relative"):
            assert_nami_can_read("/home/nakama/secret.md")
