"""Host ↔ TargetSite 對照表窮舉測試（ADR-009 T5）。

核心不變式：`set(HOST_TO_TARGET_SITE.values()) == set(TargetSite.__args__)`。
新增 target site 時同步兩邊，不然 compose / SEO 流程會 silent miss。
"""

from __future__ import annotations

from typing import get_args

import pytest

from shared.schemas.publishing import TargetSite
from shared.schemas.site_mapping import (
    HOST_TO_TARGET_SITE,
    UnknownHostError,
    host_to_target_site,
)


def test_mapping_covers_every_target_site() -> None:
    """窮舉不變式：TargetSite 的每個值都至少有一個 host 對應。"""
    target_sites = set(get_args(TargetSite))
    mapped = set(HOST_TO_TARGET_SITE.values())
    assert mapped == target_sites, (
        f"TargetSite {target_sites} 與 mapping values {mapped} 不對稱；"
        "新增 TargetSite 要同步加 HOST_TO_TARGET_SITE entry"
    )


def test_mapping_values_are_valid_target_sites() -> None:
    """每個 dict value 都必須是 TargetSite 合法字串。"""
    valid = set(get_args(TargetSite))
    for host, site in HOST_TO_TARGET_SITE.items():
        assert site in valid, f"{host!r} → {site!r} 不是合法 TargetSite"


def test_host_to_target_site_shosho() -> None:
    assert host_to_target_site("shosho.tw") == "wp_shosho"


def test_host_to_target_site_fleet() -> None:
    assert host_to_target_site("fleet.shosho.tw") == "wp_fleet"


def test_host_to_target_site_unknown_raises() -> None:
    with pytest.raises(UnknownHostError, match="unknown host"):
        host_to_target_site("example.com")


def test_host_to_target_site_error_lists_known() -> None:
    """錯誤訊息列出已知 host — 方便 debug 而非沉默 miss。"""
    with pytest.raises(UnknownHostError) as exc_info:
        host_to_target_site("unknown.example")
    msg = str(exc_info.value)
    for known in HOST_TO_TARGET_SITE:
        assert known in msg, f"{known!r} 應該在錯誤訊息裡"


def test_case_sensitive_host() -> None:
    """Host lookup 是 case-sensitive；呼叫方需先 lowercase。"""
    with pytest.raises(UnknownHostError):
        host_to_target_site("Shosho.TW")
