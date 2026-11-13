"""Tag whitelist / blacklist 過濾器（Phase 1 scope，Brook compose 呼叫）。

使用方法：
    from shared.tag_filter import filter_tags

    result = filter_tags(["book-review", "cancer-cure", "longevity-science"])
    result.accepted  # ["book-review", "longevity-science"]
    result.rejected  # [("cancer-cure", "blacklisted")]

註冊檔案：`config/tag-whitelist.yaml`
    strict_whitelist: bool   # False 時未匹配白名單僅警告不硬 reject
    whitelist: [slug, ...]
    blacklist: [slug, ...]

與 DraftV1.tags slug 驗證（shared/schemas/publishing.py）為兩道獨立檢查：
pydantic regex 只驗格式；tag_filter 驗語意白/黑名單。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import yaml

_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "config" / "tag-whitelist.yaml"


class TagFilterResult(NamedTuple):
    accepted: list[str]
    rejected: list[tuple[str, str]]  # (tag, reason)


@dataclass(frozen=True)
class TagRegistry:
    strict_whitelist: bool
    whitelist: frozenset[str]
    blacklist: frozenset[str]


_registry_cache: TagRegistry | None = None


def load_registry(path: Path | None = None) -> TagRegistry:
    """讀 yaml 註冊檔並快取。顯式 path 會跳過快取（利於測試）。"""
    global _registry_cache
    if path is None and _registry_cache is not None:
        return _registry_cache

    target = path or _REGISTRY_PATH
    if not target.exists():
        raise FileNotFoundError(f"tag whitelist registry not found: {target}")

    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    registry = TagRegistry(
        strict_whitelist=bool(data.get("strict_whitelist", False)),
        whitelist=frozenset(data.get("whitelist") or []),
        blacklist=frozenset(data.get("blacklist") or []),
    )
    if path is None:
        _registry_cache = registry
    return registry


def reset_cache() -> None:
    """測試用：清掉 module-level 快取。"""
    global _registry_cache
    _registry_cache = None


def filter_tags(
    candidates: list[str],
    *,
    max_tags: int = 10,
    registry: TagRegistry | None = None,
) -> TagFilterResult:
    """依白/黑名單過濾候選 tag，回傳 accepted + rejected(with reason)。

    過濾順序：
    1. 去重（保序）；重複的視為 rejected reason="duplicate"
    2. blacklist 命中 → rejected reason="blacklisted"
    3. strict_whitelist=True 且不在 whitelist → rejected reason="not_in_whitelist"
    4. strict_whitelist=False 且不在 whitelist → 直接 accepted（不列 rejected）
       （Phase 1 seed whitelist 不完整；切 true 前先讓 compose 通過，觀察期內不阻擋。
       呼叫端若要觀察「哪些被通過的 tag 不在 whitelist」，請看 accepted vs. registry）
    5. accepted 超過 max_tags → 多出的 rejected reason="over_limit"
    """
    reg = registry or load_registry()

    seen: set[str] = set()
    deduped: list[str] = []
    duplicates: list[tuple[str, str]] = []
    for tag in candidates:
        if tag in seen:
            duplicates.append((tag, "duplicate"))
            continue
        seen.add(tag)
        deduped.append(tag)

    accepted: list[str] = []
    rejected: list[tuple[str, str]] = list(duplicates)

    for tag in deduped:
        if tag in reg.blacklist:
            rejected.append((tag, "blacklisted"))
            continue
        if tag not in reg.whitelist:
            if reg.strict_whitelist:
                rejected.append((tag, "not_in_whitelist"))
                continue
            # non-strict：Phase 1 seed whitelist 不完整，未匹配仍 accept
            accepted.append(tag)
            continue
        accepted.append(tag)

    if len(accepted) > max_tags:
        overflow = accepted[max_tags:]
        accepted = accepted[:max_tags]
        rejected.extend((tag, "over_limit") for tag in overflow)

    return TagFilterResult(accepted=accepted, rejected=rejected)
