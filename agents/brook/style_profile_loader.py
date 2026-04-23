"""載入 Brook 三類風格側寫（book-review / people / science）。

每類有一份 yaml（config/style-profiles/<category>.yaml）定義 profile 版本與
word count / emoji 約束，以及一份 markdown（agents/brook/style-profiles/<category>.md）
作為完整風格指引。

`load_style_profile(category)` 回傳不可變 StyleProfile，供 compose 拼 system prompt。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PROFILES_DIR = _REPO_ROOT / "config" / "style-profiles"


@dataclass(frozen=True)
class StyleProfile:
    """單一類別的風格側寫，compose 呼叫時注入 system prompt。

    profile_id 遵循 DraftV1.style_profile_id 的 `slug@M.m.p` pattern（publishing.py）。
    """

    profile_id: str
    category: str
    primary_category: str
    body: str
    word_count_min: int
    word_count_max: int
    forbid_emoji: bool
    default_tag_hints: tuple[str, ...]
    detect_keywords: tuple[str, ...]


def _profile_yaml_path(category: str) -> Path:
    return _PROFILES_DIR / f"{category}.yaml"


def available_categories() -> list[str]:
    if not _PROFILES_DIR.exists():
        return []
    return sorted(p.stem for p in _PROFILES_DIR.glob("*.yaml"))


def load_style_profile(category: str) -> StyleProfile:
    """讀 category 對應的 yaml + md，組裝成 StyleProfile。

    Raises:
        FileNotFoundError: yaml 或 md 不存在
        ValueError: yaml 欄位不符合格式
    """
    yaml_path = _profile_yaml_path(category)
    if not yaml_path.exists():
        raise FileNotFoundError(
            f"style profile yaml not found: {yaml_path} — available: {available_categories()}"
        )

    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    md_rel = data.get("style_profile_md")
    if not md_rel:
        raise ValueError(f"{yaml_path} missing `style_profile_md` key")

    md_path = _REPO_ROOT / md_rel
    if not md_path.exists():
        raise FileNotFoundError(f"style profile md not found: {md_path}")

    word_count = data.get("word_count") or {}
    try:
        word_count_min = int(word_count["min"])
        word_count_max = int(word_count["max"])
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"{yaml_path} word_count.min/max 缺失或非整數") from e

    return StyleProfile(
        profile_id=str(data["profile_id"]),
        category=str(data["category"]),
        primary_category=str(data["primary_category"]),
        body=md_path.read_text(encoding="utf-8"),
        word_count_min=word_count_min,
        word_count_max=word_count_max,
        forbid_emoji=bool(data.get("forbid_emoji", False)),
        default_tag_hints=tuple(str(t) for t in (data.get("default_tag_hints") or [])),
        # YAML 會把 `- 168` 解析成 int；統一轉 str 以供大小寫無關比對
        detect_keywords=tuple(str(k) for k in (data.get("detect_keywords") or [])),
    )


_ASCII_WORD = re.compile(r"^[A-Za-z0-9]+$")

# Yaml-only 快取，避免 detect_category 每次 load 整份 22KB md（只為拿 keywords）。
_keyword_cache: dict[str, tuple[str, ...]] = {}


def _load_detect_keywords(category: str) -> tuple[str, ...]:
    """只讀 yaml 的 detect_keywords 欄位，略過 md 載入。"""
    if category in _keyword_cache:
        return _keyword_cache[category]
    yaml_path = _profile_yaml_path(category)
    if not yaml_path.exists():
        return ()
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    keywords = tuple(str(k) for k in (data.get("detect_keywords") or []))
    _keyword_cache[category] = keywords
    return keywords


def _reset_keyword_cache() -> None:
    """測試用。"""
    _keyword_cache.clear()


def _keyword_hits(keyword: str, topic: str, source_content: str) -> int:
    """ASCII 短字用 word boundary 匹配，CJK 用 substring；CJK 無 \\b 語意可靠。"""
    if _ASCII_WORD.match(keyword):
        pattern = rf"\b{re.escape(keyword)}\b"
        flags = re.IGNORECASE
        return len(re.findall(pattern, topic, flags)) + len(
            re.findall(pattern, source_content, flags)
        )
    topic_l = topic.lower()
    source_l = source_content.lower()
    kw_l = keyword.lower()
    return topic_l.count(kw_l) + source_l.count(kw_l)


def detect_category(topic: str, source_content: str = "") -> str | None:
    """依 topic + 原始素材粗略判斷類別；無匹配回 None（由 caller 決定 fallback）。

    關鍵字匹配規則：
    - ASCII 關鍵字（EP / ISBN / NEAT / VO2 / 168 等）要求 `\\b` word boundary；
      否則 `EP` 會被「step / deep / help」吃到、`168` 會被「1680」吃到。
    - CJK 關鍵字走 substring 匹配（CJK 沒有 `\\b` 語意，詞邊界不可靠）。
    同時命中多類時取 hits 最多的；tie 時回 None，讓 caller 明確指定。
    """
    scores: dict[str, int] = {}
    for category in available_categories():
        total = 0
        for kw in _load_detect_keywords(category):
            total += _keyword_hits(kw, topic, source_content)
        if total > 0:
            scores[category] = total

    if not scores:
        return None
    max_hits = max(scores.values())
    winners = [c for c, s in scores.items() if s == max_hits]
    if len(winners) > 1:
        return None
    return winners[0]
