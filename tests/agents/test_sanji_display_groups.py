"""顯示分類 ↔ 分數表的漂移防護。

plugin 的 ``GROUP_SOURCES`` / ``SOURCE_LABELS`` 是**顯示**分類，經濟規則在 rules.py。
兩邊各自演化沒關係，但「分數表有的來源，UI 一定要能標示與篩選到」是硬約束——
少一個就是有人的帳目在自己的航海日誌裡看不到（或篩選時憑空消失）。
"""

from __future__ import annotations

import re
from pathlib import Path

from agents.sanji import rules

PHP = Path(__file__).resolve().parents[2] / "wp/fleet-gamification/includes/class-voyage-page.php"


def _php_array(name: str) -> str:
    """撈出 `private const NAME = array( ... );` 的內容（含巢狀 array）。"""
    src = PHP.read_text(encoding="utf-8")
    start = src.index(f"private const {name} = array(")
    depth = 0
    for i in range(src.index("array(", start), len(src)):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError(f"{name} 沒有收尾")


def _keys(block: str) -> set[str]:
    return set(re.findall(r"'([a-z0-9_]+)'\s*=>", block))


def _values(block: str) -> set[str]:
    """所有出現在巢狀 array 裡的字串值（= 來源名）。"""
    inner = re.findall(r"array\(([^()]*)\)", block)
    out: set[str] = set()
    for chunk in inner:
        out |= set(re.findall(r"'([a-z0-9_]+)'", chunk))
    return out


def test_source_labels_cover_xp_table() -> None:
    labels = _keys(_php_array("SOURCE_LABELS"))
    missing = set(rules.XP_TABLE) - labels
    assert not missing, f"SOURCE_LABELS 少了：{sorted(missing)}"


def test_group_sources_cover_xp_table() -> None:
    grouped = _values(_php_array("GROUP_SOURCES"))
    missing = set(rules.XP_TABLE) - grouped
    assert not missing, f"GROUP_SOURCES 少了：{sorted(missing)}"


def test_challenge_group_matches_rules() -> None:
    """挑戰榜的來源集合是經濟規則，UI 的『挑戰』篩選必須完全對齊。"""
    block = _php_array("GROUP_SOURCES")
    challenge = re.search(r"'challenge'\s*=>\s*array\(([^()]*)\)", block)
    assert challenge, "GROUP_SOURCES 沒有 challenge 群組"
    php_set = set(re.findall(r"'([a-z0-9_]+)'", challenge.group(1)))
    assert php_set == set(rules.CHALLENGE_SOURCES), (
        f"挑戰群組漂移：PHP={sorted(php_set)} rules={sorted(rules.CHALLENGE_SOURCES)}"
    )


def test_group_keys_have_labels() -> None:
    labels = _keys(_php_array("GROUP_LABELS"))
    groups = _keys(_php_array("GROUP_SOURCES"))
    assert groups <= labels, f"有群組沒有中文標籤：{sorted(groups - labels)}"
    assert "all" in labels, "預設『全部』選項不見了"
