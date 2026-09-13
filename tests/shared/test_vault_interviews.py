"""episode 資料夾 → Vault interview 專案資料夾。推導不出來，只能靠來賓名對。"""

from __future__ import annotations

import pytest

from shared.vault_interviews import (
    VaultInterviewError,
    guest_name,
    interview_dir,
    next_numbered_name,
)


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "AgentOutputs" / "interviews"
    root.mkdir(parents=True)
    return tmp_path


def test_guest_name_strips_the_publish_date():
    assert guest_name("20260901 蘇予昕") == "蘇予昕"
    assert guest_name("20260805_林之晨") == "林之晨"
    assert guest_name("蘇予昕") == "蘇予昕"


def test_guest_name_fails_loud_on_a_date_only_folder():
    with pytest.raises(VaultInterviewError):
        guest_name("20260901")


def test_matches_the_interview_date_folder_not_the_publish_date(vault):
    """上架日 09-01、訪談日 08-31——差一天，別集可能差兩週。只有名字是共通的。"""
    (vault / "AgentOutputs" / "interviews" / "2026-08-31-蘇予昕").mkdir()
    (vault / "AgentOutputs" / "interviews" / "2026-08-04-林之晨").mkdir()
    assert interview_dir("20260901 蘇予昕", vault).name == "2026-08-31-蘇予昕"


def test_no_match_fails_loud_instead_of_creating_a_folder(vault):
    with pytest.raises(VaultInterviewError, match="找不到"):
        interview_dir("20260901 蘇予昕", vault)
    assert list((vault / "AgentOutputs" / "interviews").iterdir()) == []


def test_two_matches_fail_loud(vault):
    root = vault / "AgentOutputs" / "interviews"
    (root / "2026-08-31-蘇予昕").mkdir()
    (root / "2026-09-15-蘇予昕").mkdir()
    with pytest.raises(VaultInterviewError, match="多個"):
        interview_dir("20260901 蘇予昕", vault)


def test_a_guest_whose_name_is_a_suffix_of_another_does_not_match(vault):
    """`-` 前綴是必要的：不加的話「予昕」會對到「蘇予昕」的資料夾。"""
    (vault / "AgentOutputs" / "interviews" / "2026-08-31-蘇予昕").mkdir()
    with pytest.raises(VaultInterviewError, match="找不到"):
        interview_dir("20260901 予昕", vault)


def test_next_number_continues_the_existing_sequence(tmp_path):
    for name in ("01-足跡地圖.md", "02-採集計畫.md", "06-title-brainstorm.md"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    assert next_numbered_name(tmp_path, "選段報告") == "07-選段報告.md"


def test_rerunning_overwrites_its_own_file_instead_of_stacking_numbers(tmp_path):
    (tmp_path / "06-title-brainstorm.md").write_text("x", encoding="utf-8")
    (tmp_path / "07-選段報告.md").write_text("x", encoding="utf-8")
    assert next_numbered_name(tmp_path, "選段報告") == "07-選段報告.md"


def test_first_report_in_an_empty_folder_is_01(tmp_path):
    assert next_numbered_name(tmp_path, "選段報告") == "01-選段報告.md"
