"""Tests for shared.journal_blocklist — yaml-driven negative filter.

Doesn't touch the production yaml; each test passes its own ``blocklist_path``
to keep the cache clean (calls reload() before each).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.journal_blocklist import is_blocked, reload


@pytest.fixture(autouse=True)
def _clear_cache():
    reload()
    yield
    reload()


def _write_yaml(tmp_path: Path, journals: list[str]) -> Path:
    p = tmp_path / "blocklist.yaml"
    lines = ["block:"] + [f"  - {j}" for j in journals]
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def test_is_blocked_matches_listed_journal(tmp_path):
    p = _write_yaml(tmp_path, ["Nutrients", "Frontiers in public health"])
    assert is_blocked("Nutrients", blocklist_path=p) is True
    assert is_blocked("Frontiers in public health", blocklist_path=p) is True


def test_is_blocked_returns_false_for_unlisted(tmp_path):
    p = _write_yaml(tmp_path, ["Nutrients"])
    assert is_blocked("JAMA", blocklist_path=p) is False
    assert is_blocked("Lancet", blocklist_path=p) is False
    assert is_blocked("Frontiers in immunology", blocklist_path=p) is False


def test_is_blocked_normalizes_case_and_extra_whitespace(tmp_path):
    """大小寫差 / 多餘 whitespace 不影響命中（normalize 只留 a-z0-9）。

    注意：normalize 不做 substring match，所以 "Sensors (Basel)" 跟
    "Sensors (Basel, Switzerland)" 是不同 key。實務 PubMed 期刊全名固定，
    這不是問題；blocklist yaml 寫法跟 PubMed 給的 source field 一致即可。
    """
    p = _write_yaml(tmp_path, ["Sensors (Basel, Switzerland)"])
    assert is_blocked("Sensors (Basel, Switzerland)", blocklist_path=p) is True
    assert is_blocked("SENSORS (BASEL, SWITZERLAND)", blocklist_path=p) is True
    assert is_blocked("Sensors  (Basel,  Switzerland)", blocklist_path=p) is True
    # Substring 不命中（這是 by design，避免 "Sensors" 誤殺 "Sensors and Actuators"）
    assert is_blocked("Sensors (Basel)", blocklist_path=p) is False


def test_is_blocked_normalizes_ampersand_and_and(tmp_path):
    p = _write_yaml(tmp_path, ["Gut & Liver"])
    assert is_blocked("Gut and Liver", blocklist_path=p) is True
    assert is_blocked("Gut & Liver", blocklist_path=p) is True


def test_is_blocked_empty_input_returns_false(tmp_path):
    p = _write_yaml(tmp_path, ["Nutrients"])
    assert is_blocked("", blocklist_path=p) is False
    assert is_blocked("   ", blocklist_path=p) is False


def test_is_blocked_returns_false_when_yaml_missing(tmp_path):
    nonexistent = tmp_path / "nope.yaml"
    assert is_blocked("Nutrients", blocklist_path=nonexistent) is False


def test_is_blocked_handles_empty_yaml(tmp_path):
    p = tmp_path / "empty.yaml"
    p.write_text("block: []\n", encoding="utf-8")
    assert is_blocked("Nutrients", blocklist_path=p) is False


def test_is_blocked_skips_non_string_entries(tmp_path):
    """yaml 裡若混 None / 非 string entry 不應 raise，跳過該項。"""
    p = tmp_path / "mixed.yaml"
    p.write_text(
        "block:\n  - Nutrients\n  - null\n  - 42\n  - Frontiers in public health\n",
        encoding="utf-8",
    )
    assert is_blocked("Nutrients", blocklist_path=p) is True
    assert is_blocked("Frontiers in public health", blocklist_path=p) is True
    assert is_blocked("Other", blocklist_path=p) is False


def test_production_yaml_blocks_known_mdpi_keeps_immunology_endocrinology():
    """Sanity-check 上線 yaml 的修修 5/5 凍結配置：MDPI 全 block + Frontiers 保留兩個。"""
    # 用 production path（_BLOCKLIST_PATH 預設）
    assert is_blocked("Nutrients") is True
    assert is_blocked("International journal of molecular sciences") is True
    assert is_blocked("Sensors (Basel, Switzerland)") is True
    assert is_blocked("Cells") is True
    assert is_blocked("Genes") is True
    assert is_blocked("Frontiers in public health") is True
    assert is_blocked("Frontiers in cellular and infection microbiology") is True

    # 修修明確保留：
    assert is_blocked("Frontiers in immunology") is False
    assert is_blocked("Frontiers in endocrinology") is False

    # 頂刊絕對不能 block：
    assert is_blocked("JAMA") is False
    assert is_blocked("Lancet") is False
    assert is_blocked("The New England journal of medicine") is False
    assert is_blocked("Nature") is False
    assert is_blocked("Science") is False
    assert is_blocked("BMJ") is False
    assert is_blocked("Cell") is False
    assert is_blocked("British journal of sports medicine") is False
