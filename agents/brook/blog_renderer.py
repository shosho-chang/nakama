"""Brook Blog Renderer — Stage 2 blog renderer for Line 1 podcast repurpose pipeline.

Input:  Stage1Result (structured narrative JSON from Line1Extractor) + EpisodeMetadata
Output: ChannelArtifact(filename="blog.md", channel="blog")

Pipeline:
1. Build YAML frontmatter from Stage 1 data + episode metadata
2. Build LLM prompt with Stage 1 JSON + people.md style profile
3. Call Sonnet 4.6 via shared.llm.ask_multi to write 8-segment blog body
4. Validate word count (988-3954); log warning if outside range
5. Combine frontmatter + blog body → blog.md artifact
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from agents.brook.repurpose_engine import ChannelArtifact, EpisodeMetadata, Stage1Result
from shared.llm import ask_multi
from shared.log import get_logger

logger = get_logger("nakama.brook.blog_renderer")

_MODEL = "claude-sonnet-4-6"
_PEOPLE_MD_PATH = Path(__file__).parent / "style-profiles" / "people.md"
_WORD_COUNT_MIN = 988
_WORD_COUNT_MAX = 3954
_DEFAULT_TAGS = ["people", "podcast"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_frontmatter(
    title: str,
    meta_description: str,
    category: str,
    tags: list[str],
    podcast_episode_url: str,
) -> str:
    """Construct YAML frontmatter block."""
    data = {
        "title": title,
        "meta_description": meta_description,
        "category": category,
        "tags": tags,
        "podcast_episode_url": podcast_episode_url,
    }
    yaml_str = yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return f"---\n{yaml_str}---"


def _build_messages(
    stage1_data: dict,
    metadata: EpisodeMetadata,
    people_md: str,
    podcast_episode_url: str,
) -> list[dict]:
    stage1_json = json.dumps(stage1_data, ensure_ascii=False, indent=2)
    guest = metadata.extra.get("guest", "受訪者")
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
- ≥1 個 block quote（`> 「受訪者原話」——來賓姓名`），引用 quotes[] 中的金句
- 文末加 podcast 收聽連結：`> 🎙️ 收聽本集 → {podcast_episode_url}`
- 字數目標：2000-3000 字
- 語氣遵守 people.md 風格側寫
- H2 標題用**敘事章節標題格式**（動詞驅動、帶情境感、不做 FAQ 問句）

**禁止：**
- 輸出 YAML frontmatter 或任何 markdown fence（只輸出 markdown 正文）
- bullet list 整理人物成就
- 結尾做 takeaway 總結

請直接輸出 markdown 正文（從第一段開始，無需任何前置說明）。
"""
    return [{"role": "user", "content": content}]


def _check_word_count(body: str) -> None:
    """Log warning if body character count is outside the observed range."""
    count = len(body)
    if count < _WORD_COUNT_MIN:
        logger.warning(
            "word count %d is below minimum %d (字數偏短，可能素材不足)",
            count,
            _WORD_COUNT_MIN,
        )
    elif count > _WORD_COUNT_MAX:
        logger.warning(
            "word count %d exceeds maximum %d (字數偏長，考慮精簡)",
            count,
            _WORD_COUNT_MAX,
        )


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


class BlogRenderer:
    """Stage 2 blog renderer for Line 1 podcast repurpose pipeline.

    Consumes Stage1Result from Line1Extractor → outputs blog.md with YAML
    frontmatter + 8-segment narrative body via Sonnet 4.6.

    Implements ChannelRenderer Protocol (agents.brook.repurpose_engine).
    Cost tracking is automatic via ask_multi → _record_anthropic_usage.
    """

    def __init__(self, *, people_md: str | None = None) -> None:
        self._people_md = (
            people_md if people_md is not None else _PEOPLE_MD_PATH.read_text(encoding="utf-8")
        )

    def render(self, stage1: Stage1Result, metadata: EpisodeMetadata) -> list[ChannelArtifact]:
        """Render blog.md from Stage 1 structured narrative JSON.

        Args:
            stage1: Stage1Result from Line1Extractor (validated JSON).
            metadata: EpisodeMetadata — host/guest names, episode URL.

        Returns:
            Single-element list with ChannelArtifact(filename="blog.md", channel="blog").
        """
        data = stage1.data
        podcast_episode_url = metadata.extra.get("podcast_episode_url", "")

        frontmatter = _build_frontmatter(
            title=data["title_candidates"][0],
            meta_description=data["meta_description"],
            category="people",
            tags=_DEFAULT_TAGS,
            podcast_episode_url=podcast_episode_url,
        )

        messages = _build_messages(data, metadata, self._people_md, podcast_episode_url)
        system = (
            "你是 Brook，不正常人類研究所 Podcast 的 AI 寫作助手。"
            "任務是根據 Stage 1 素材撰寫繁體中文人物專訪部落格正文。"
            "只輸出 markdown 正文，不輸出 YAML frontmatter 或任何 markdown fence。"
        )

        body = ask_multi(messages, system=system, model=_MODEL, max_tokens=8192)
        _check_word_count(body)

        content = f"{frontmatter}\n{body}"
        return [ChannelArtifact(filename="blog.md", content=content, channel="blog")]
