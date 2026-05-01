"""Brook Blog Renderer — Stage 2 blog renderer for Line 1 podcast repurpose pipeline.

Input:  Stage1Result (structured narrative JSON from Line1Extractor) + EpisodeMetadata
Output: ChannelArtifact(filename=BLOG_FILENAME, channel="blog")

Pipeline:
1. Build YAML frontmatter from Stage 1 data + episode metadata (scalar-safe).
2. Build LLM prompt with Stage 1 JSON + people.md style profile body.
3. Call Sonnet 4.6 via shared.llm.ask_multi to write 8-segment blog body.
4. Validate word count against the style profile bounds; warn if outside.
5. Combine frontmatter + blog body → blog.md artifact (trailing newline).

Style profile data (body, word_count, category, tags) is sourced from
``agents.brook.style_profile_loader.load_style_profile("people")`` so the
canonical ``config/style-profiles/people.yaml`` is the single source of truth
(no hardcoded category / tags / word-count thresholds in this module).
"""

from __future__ import annotations

import json

import yaml

from agents.brook.repurpose_engine import (
    BLOG_FILENAME,
    ChannelArtifact,
    EpisodeMetadata,
    Stage1Result,
)
from agents.brook.style_profile_loader import StyleProfile, load_style_profile
from shared.llm import ask_multi
from shared.log import get_logger

logger = get_logger("nakama.brook.blog_renderer")

_DEFAULT_MODEL = "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _yaml_safe_scalar(value: str) -> str:
    """Sanitize a scalar destined for YAML frontmatter.

    PyYAML's safe quoting handles ``:`` ``"`` etc. but does NOT collapse newlines
    inside scalars — a stray ``\\n`` produces a multi-line block scalar that
    Obsidian / frontmatter parsers misread (see feedback_yaml_scalar_safety.md).
    Collapse line breaks to single spaces and strip control whitespace.
    """
    if not isinstance(value, str):
        return value
    return " ".join(value.replace("\r", "\n").split("\n")).strip()


def _build_frontmatter(
    *,
    title: str,
    meta_description: str,
    category: str,
    tags: list[str],
    podcast_episode_url: str,
) -> str:
    """Construct YAML frontmatter block with scalar-safe string values."""
    data = {
        "title": _yaml_safe_scalar(title),
        "meta_description": _yaml_safe_scalar(meta_description),
        "category": _yaml_safe_scalar(category),
        "tags": list(tags),
        "podcast_episode_url": _yaml_safe_scalar(podcast_episode_url),
    }
    yaml_str = yaml.safe_dump(
        data,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )
    return f"---\n{yaml_str}---"


def _build_messages(
    stage1_data: dict,
    metadata: EpisodeMetadata,
    people_md: str,
    podcast_episode_url: str,
    word_count_min: int,
    word_count_max: int,
) -> list[dict]:
    stage1_json = json.dumps(stage1_data, ensure_ascii=False, indent=2)
    guest = metadata.extra.get("guest", "受訪者")
    target_low = word_count_min + 200
    target_high = word_count_max - 500
    content = f"""## 主持人：{metadata.host}
## 來賓：{guest}
## Podcast 連結：{podcast_episode_url}

---

## 人物文風格側寫

{people_md}

---

## Stage 1 結構化素材

以下是從 SRT 訪談萃取的結構化敘事素材，用於撰寫人物專訪部落格文章：

```json
{stage1_json}
```

---

## 撰稿指示

請根據以上 Stage 1 素材，撰寫一篇繁體中文人物專訪部落格文章。

**結構要求（嚴格遵守）：**
1. **開場鉤子**（無 H2）：從 hooks[] 中選一個，讓讀者產生懸念
2. **身份速寫**（無 H2）：用 identity_sketch 建立「這人值得聽」（1-2 段）
3. **## 起點段落**（H2 敘事章節標題，動詞驅動）：用 origin 材料
4. **## 轉折段落**（H2 敘事章節標題）：用 turning_point 材料
5. **## 重生段落**（H2 敘事章節標題）：用 rebirth 材料
6. **## 金句 H2**（必備元素）：`## 『來自 quotes[] 的受訪者名言』`
7. **## 現在段落**（H2 敘事章節標題）：用 present_action 材料
8. **## 結尾段落**（H2 敘事章節標題）：用 ending_direction 方向，留白引向 podcast

**其他要求：**
- ≥1 個 block quote 引用 quotes[] 中的金句
- block quote **格式必須是** `>「受訪者原話」（EP## {guest}）`
- 依 people.md 既有慣例：全形引號 + 全形括號 + EP 編號 + 來賓姓名
- 文末加 podcast 收聽連結：`> 🎙️ 收聽本集 → {podcast_episode_url}`
- 字數目標：{target_low}-{target_high} 字（硬上下限 {word_count_min}-{word_count_max}）
- 語氣遵守 people.md 風格側寫
- H2 標題用**敘事章節標題格式**（動詞驅動、帶情境感、不做 FAQ 問句）

**禁止：**
- 輸出 YAML frontmatter 或任何 markdown fence（只輸出 markdown 正文）
- bullet list 整理人物成就
- 結尾做 takeaway 總結

請直接輸出 markdown 正文（從第一段開始，無需任何前置說明）。
"""
    return [{"role": "user", "content": content}]


def _check_word_count(body: str, *, min_count: int, max_count: int) -> None:
    """Log warning if body character count is outside the configured range.

    Note: ``len(body)`` measures characters, not English words — the chosen
    metric for CJK-heavy content where char count ≈ "字數" in the prompt.
    Bounds come from the style profile (``config/style-profiles/people.yaml``).
    """
    count = len(body)
    if count < min_count:
        logger.warning(
            "blog body word count %d is below minimum %d (字數偏短，可能素材不足)",
            count,
            min_count,
        )
    elif count > max_count:
        logger.warning(
            "blog body word count %d exceeds maximum %d (字數偏長，考慮精簡)",
            count,
            max_count,
        )


def _require_stage1_field(data: dict, key: str) -> object:
    """Pull a required key from stage1.data with a typed error if missing."""
    if key not in data:
        raise ValueError(
            f"BlogRenderer: stage1.data is missing required field {key!r} — "
            "ensure the Stage 1 extractor schema is consumed correctly"
        )
    value = data[key]
    if value is None or value == "":
        raise ValueError(f"BlogRenderer: stage1.data[{key!r}] is empty")
    return value


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


class BlogRenderer:
    """Stage 2 blog renderer for Line 1 podcast repurpose pipeline.

    Consumes Stage1Result from Line1Extractor → outputs blog.md with YAML
    frontmatter + 8-segment narrative body via Sonnet 4.6.

    Implements ChannelRenderer Protocol (agents.brook.repurpose_engine).

    Style profile (body, category, tags, word-count bounds) is sourced from
    ``load_style_profile("people")`` by default; pass ``style_profile=`` to
    override (e.g. tests).

    Cost tracking is automatic via ``ask_multi`` → ``_record_anthropic_usage``.
    """

    def __init__(
        self,
        *,
        style_profile: StyleProfile | None = None,
        model: str | None = None,
    ) -> None:
        self._profile = style_profile or load_style_profile("people")
        self._model = model or _DEFAULT_MODEL

    def render(self, stage1: Stage1Result, metadata: EpisodeMetadata) -> list[ChannelArtifact]:
        """Render blog.md from Stage 1 structured narrative JSON.

        Args:
            stage1: Stage1Result from Line1Extractor (validated JSON).
            metadata: EpisodeMetadata — host/guest names, episode URL.

        Returns:
            Single-element list with ChannelArtifact(filename=BLOG_FILENAME, channel="blog").

        Raises:
            ValueError: If Stage 1 data is missing required fields.
        """
        data = stage1.data
        title_candidates = _require_stage1_field(data, "title_candidates")
        if not isinstance(title_candidates, list) or not title_candidates:
            raise ValueError(
                "BlogRenderer: stage1.data['title_candidates'] must be a non-empty list"
            )
        title = str(title_candidates[0])
        meta_description = str(_require_stage1_field(data, "meta_description"))

        podcast_episode_url = metadata.extra.get("podcast_episode_url", "")

        frontmatter = _build_frontmatter(
            title=title,
            meta_description=meta_description,
            category=self._profile.primary_category,
            tags=list(self._profile.default_tag_hints),
            podcast_episode_url=podcast_episode_url,
        )

        messages = _build_messages(
            data,
            metadata,
            self._profile.body,
            podcast_episode_url,
            self._profile.word_count_min,
            self._profile.word_count_max,
        )
        system = (
            "你是 Brook，不正常人類研究所 Podcast 的 AI 寫作助手。"
            "任務是根據 Stage 1 素材撰寫繁體中文人物專訪部落格正文。"
            "只輸出 markdown 正文，不輸出 YAML frontmatter 或任何 markdown fence。"
        )

        body = ask_multi(messages, system=system, model=self._model, max_tokens=12288)
        _check_word_count(
            body,
            min_count=self._profile.word_count_min,
            max_count=self._profile.word_count_max,
        )

        body_trimmed = body.rstrip() + "\n"
        content = f"{frontmatter}\n{body_trimmed}"
        return [ChannelArtifact(filename=BLOG_FILENAME, content=content, channel="blog")]
