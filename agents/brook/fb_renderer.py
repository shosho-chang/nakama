"""Brook FB Renderer — Stage 2 FB renderer for Line 1 podcast repurpose pipeline.

Input:  Stage1Result (structured narrative JSON from Line1Extractor) + EpisodeMetadata
Output: 4 × ChannelArtifact(filename=fb-{tonal}.md, channel=fb-{tonal})

Pipeline:
1. Load fb-post.md style profile (single voice + 3 sub-scenarios + 4 tonal modulations).
2. For each tonal in FB_TONALS, build prompt with Stage 1 JSON + profile + tonal directive.
3. Fan out 4 Sonnet 4.6 calls via ThreadPoolExecutor (renderer-internal parallelism).
4. Validate per-variant word count against profile bounds; warn if outside.
5. Return list[ChannelArtifact] of length 4 (one per tonal).

Voice / tone strategy
---------------------
Per ``fb-post.md §1.2`` (修修 2026-05-01 拍板):

- Voice profile is SINGLE (not 4 sub-tonal sections — 修修 simplified)
- Output IS 4 tonal variants, but they share voice; only emotional color shifts:
  - light    : 🤣 emoji 入文、自我吐槽密度高 (fb-interview-2 周慕姿樣本參考)
  - emotional: 私人共鳴段加重、自我露出深 (fb-article-5 / fb-article-3 樣本參考)
  - serious  : 議題倡議口吻、量化證據密度高 (fb-article-4 焦慮世代樣本參考)
  - neutral  : 純資訊／list 結構、emoji 最少 (fb-article-2 12 books 樣本參考)

Sub-scenario routing for Line 1 podcast: episode_type ∈ {narrative_journey,
myth_busting, framework, listicle} all map to 子場景 A (訪談宣傳) — Line 1 is
podcast EP repurpose by definition. (子場景 B 個人感想 / 子場景 C 嘉賓側寫
保留給未來 non-podcast workflows; not consumed by Line 1.)

Style profile data (body, word_count, tags) sourced from
``load_style_profile("fb-post")`` so the canonical
``config/style-profiles/fb-post.yaml`` is the single source of truth.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Literal

from agents.brook.repurpose_engine import (
    FB_TONALS,
    ChannelArtifact,
    EpisodeMetadata,
    Stage1Result,
    fb_filename,
)
from agents.brook.style_profile_loader import StyleProfile, load_style_profile
from shared.llm import ask_multi
from shared.log import get_logger

logger = get_logger("nakama.brook.fb_renderer")

_DEFAULT_MODEL = "claude-sonnet-4-6"
_FB_PROFILE_CATEGORY = "fb-post"

Tonal = Literal["light", "emotional", "serious", "neutral"]


# ---------------------------------------------------------------------------
# Tonal directives (injected into prompt to differentiate the 4 variants)
# ---------------------------------------------------------------------------

# Each directive describes the TONAL color modulation within 修修's single voice.
# All four still follow fb-post.md §3-6 (hooks / 節奏 / 自我露出 / CTA);
# only emotional weight + emoji density + structural choice differ.
_TONAL_DIRECTIVES: dict[Tonal, str] = {
    "light": (
        "**tonal=light（輕鬆風趣）**\n"
        "- emoji 密度最高：🤣 至少 2 個、😓 1 個（自嘲、笑點）\n"
        "- 修修自我吐槽密度高，括號吐槽 2-3 個\n"
        "- 開場 hook 偏 §3.C 反差／自我吐槽（「這應該是我笑得最誇張⋯⋯」式）\n"
        "- 句尾助詞「哈哈」「啦」「喔」自然散佈\n"
        "- 樣本參考：fb-interview-2.txt 周慕姿（笑到哭那場）\n"
    ),
    "emotional": (
        "**tonal=emotional（感性）**\n"
        "- emoji 中等：☺️ 1-2 個（期待、感謝）；不用 🤣\n"
        "- 私人共鳴段加重：修修個人故事與來賓對照、自我露出至少 2 段\n"
        "- 開場 hook 偏 §3.D 個人連結式（「我追隨最久」「兩年前的這個時候」）\n"
        "- 結尾走感謝段或人生展望句（「迴向給」「希望」「等不及」）\n"
        "- 樣本參考：fb-article-5.txt 輝誠老師、fb-article-3.txt Intel 離職\n"
    ),
    "serious": (
        "**tonal=serious（嚴肅）**\n"
        "- emoji 極少：最多 1 個 ☺️ 在結尾；正文不用 emoji\n"
        "- 議題倡議口吻：用具體量化數字（百分比、研究、立法案例）建立急迫性\n"
        "- 開場 hook 偏 §3.B 時點切入或 §3.A EP 編號 + 戲劇性副標\n"
        "- 結尾走呼籲段（「大家一起來⋯⋯」「強烈推薦」）\n"
        "- 樣本參考：fb-article-4.txt 失控的焦慮世代\n"
    ),
    "neutral": (
        "**tonal=neutral（一般、純資訊）**\n"
        "- emoji 最少：0-1 個整篇\n"
        "- 純資訊／結構化：偏列表、客觀介紹來賓功業、量化背書多\n"
        "- 開場 hook 偏 §3.A EP 編號 + 戲劇性副標（直接破題）\n"
        "- 結尾走 CTA（折扣碼、訪談連結）+ 簡短期待句\n"
        "- 樣本參考：fb-interview-1.txt 王文靜、fb-article-2.txt 12 本書\n"
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_stage1_field(data: dict, key: str) -> object:
    """Pull a required key from stage1.data with a typed error if missing."""
    if key not in data:
        raise ValueError(
            f"FBRenderer: stage1.data is missing required field {key!r} — "
            "ensure the Stage 1 extractor schema is consumed correctly"
        )
    value = data[key]
    if value is None or value == "":
        raise ValueError(f"FBRenderer: stage1.data[{key!r}] is empty")
    return value


def _build_messages(
    *,
    stage1_data: dict,
    metadata: EpisodeMetadata,
    profile_body: str,
    tonal: Tonal,
    word_count_min: int,
    word_count_max: int,
    podcast_episode_url: str,
) -> list[dict]:
    """Build single-turn user message for one tonal variant.

    Per fb-post.md, FB output is plain Chinese text (no markdown headers, no
    YAML frontmatter, no ``##`` / ``###``). The ``.md`` extension is just
    file convention; content is plain text suitable for FB paste.
    """
    stage1_json = json.dumps(stage1_data, ensure_ascii=False, indent=2)
    guest = metadata.extra.get("guest", "受訪者")
    directive = _TONAL_DIRECTIVES[tonal]

    # Target window: pull off both ends so LLM aims for a comfortable middle.
    # Bounds 800-3500 (yaml union of 訪談宣傳 800-1500 + 個人感想 1500-3500);
    # for Line 1 podcast (always 訪談宣傳 sub-scenario) we narrow to 1000-1500.
    target_low = 1000
    target_high = 1500

    content = f"""## 主持人：{metadata.host}
## 來賓：{guest}
## Podcast 連結：{podcast_episode_url}

---

## FB 短文風格側寫（fb-post.md）

{profile_body}

---

## Stage 1 結構化素材

以下是從 SRT 訪談萃取的結構化敘事素材，用於撰寫 FB 訪談宣傳貼文：

```json
{stage1_json}
```

---

## 撰稿指示

請根據以上 Stage 1 素材，撰寫一篇繁體中文 FB 訪談宣傳貼文（**子場景 A 訪談宣傳**，
不是子場景 B 個人感想或子場景 C 嘉賓側寫）。

### Tonal 指令（本次寫作的情緒色調）

{directive}

### 結構（fb-post.md §2.A 訪談宣傳骨架）

1. **開場 hook**：依上方 tonal 指令選擇 §3 開場 pattern；使用 EP 編號 + 戲劇性副標
   或時點/反差 hook，第一句／第一段就鎖住讀者
2. **來賓速寫（1-2 段）**：用 identity_sketch 建立「這人值得聽」、量化背書
3. **人物背景／轉折故事（2-4 段）**：用 origin / turning_point / rebirth 材料展開
4. **訪談亮點預告（1-2 段）**：用 present_action 預告本集亮點，不全劇透
5. **CTA 段**：podcast 收聽連結 + 課程連結（如適用）；「想聽完整版去 podcast」
6. **結尾留白／展望句**：依 tonal 指令選結尾風格

### 字數與格式

- 字數目標：**{target_low}-{target_high} 中文字元**（硬上下限 {word_count_min}-{word_count_max}）
- **不用 markdown 標題語法**（FB 不渲染 markdown，用純文字 + 換行 + zero-width
  spacer `​` 即可）
- 段落短（1-3 句），段間空一行；偶用單句段落作為節奏停頓
- 引用來賓金句：直接用 `「來賓原話」` 全形引號，**不要用 markdown blockquote**
- 至少 1 個來賓金句引用（從 quotes[] 選短而有力的）
- 第一人稱「我」自然散佈（訪談宣傳子場景密度 5-30 次／篇，不要灌「我」）
- 括號吐槽（修修招牌）依 tonal 指令的密度，0-3 個

### 禁止

- 禁止輸出 markdown 標題（`#` `##` `###`）
- 禁止 hashtag（`#健康` `#podcast`）
- 禁止 YAML frontmatter
- 禁止 takeaway bullet list
- 禁止劇透完整訪談內容（要留懸念引人去聽）
- 禁止用簡體中文或日式漢字

請直接輸出 FB 貼文純文字（從第一段開始，無需任何前置說明、無需 markdown
fence）。Podcast 連結 `{podcast_episode_url}` 須出現在 CTA 段。
"""
    return [{"role": "user", "content": content}]


def _check_word_count(body: str, *, tonal: Tonal, min_count: int, max_count: int) -> None:
    """Log warning if body character count is outside the configured range.

    ``len(body)`` measures CJK characters which is the unit used in fb-post.md
    word-count discussions (字 = 中文字元數).
    """
    count = len(body)
    if count < min_count:
        logger.warning(
            "fb body (tonal=%s) word count %d is below minimum %d (字數偏短，可能素材不足)",
            tonal,
            count,
            min_count,
        )
    elif count > max_count:
        logger.warning(
            "fb body (tonal=%s) word count %d exceeds maximum %d (字數偏長，考慮精簡)",
            tonal,
            count,
            max_count,
        )


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


class FBRenderer:
    """Stage 2 FB renderer for Line 1 podcast repurpose pipeline.

    Consumes Stage1Result from Line1Extractor → outputs 4 fb-{tonal}.md
    artifacts (one per tonal variant) via parallel Sonnet 4.6 calls.

    Implements ChannelRenderer Protocol (agents.brook.repurpose_engine).

    Style profile (body, word-count bounds, tag hints) is sourced from
    ``load_style_profile("fb-post")`` by default; pass ``style_profile=`` to
    override (e.g. tests).

    Internal parallelism: 4 tonal variants run concurrently in a thread pool
    (one ``ask_multi`` call per tonal). Failures are RAISED, not isolated —
    per-channel error isolation lives at the engine level (RepurposeEngine
    catches a failed renderer.render() and continues other channels).

    Cost tracking is automatic via ``ask_multi`` → ``_record_anthropic_usage``.
    """

    def __init__(
        self,
        *,
        style_profile: StyleProfile | None = None,
        model: str | None = None,
        max_workers: int = 4,
    ) -> None:
        self._profile = style_profile or load_style_profile(_FB_PROFILE_CATEGORY)
        self._model = model or _DEFAULT_MODEL
        self._max_workers = max_workers

    def render(self, stage1: Stage1Result, metadata: EpisodeMetadata) -> list[ChannelArtifact]:
        """Render 4 fb-{tonal}.md variants in parallel.

        Args:
            stage1: Stage1Result from Line1Extractor (validated JSON).
            metadata: EpisodeMetadata — host/guest names, podcast URL.

        Returns:
            List of 4 ChannelArtifact instances, one per tonal in FB_TONALS
            order. Order is stable (FB_TONALS tuple ordering) regardless of
            which thread finishes first.

        Raises:
            ValueError: If Stage 1 data is missing required fields.
            Exception: If any tonal variant LLM call raises (NOT isolated
                here — engine layer handles per-channel isolation).
        """
        data = stage1.data
        # Validate required Stage 1 fields up front (fail fast before
        # spinning up the thread pool for 4 wasted calls).
        for key in ("identity_sketch", "origin", "turning_point", "rebirth", "quotes"):
            _require_stage1_field(data, key)

        podcast_episode_url = metadata.extra.get("podcast_episode_url", "")
        system = (
            "你是 Brook，不正常人類研究所 Podcast 的 AI 寫作助手。"
            "任務是根據 Stage 1 素材撰寫繁體中文 FB 訪談宣傳貼文。"
            "FB 不渲染 markdown，只輸出純文字（不要 markdown 標題、frontmatter、hashtag）。"
        )

        # Submit all 4 tonal calls to thread pool, collect by tonal name so we
        # can return artifacts in deterministic FB_TONALS order regardless of
        # which thread finishes first.
        results: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures = {}
            for tonal in FB_TONALS:
                messages = _build_messages(
                    stage1_data=data,
                    metadata=metadata,
                    profile_body=self._profile.body,
                    tonal=tonal,  # type: ignore[arg-type]
                    word_count_min=self._profile.word_count_min,
                    word_count_max=self._profile.word_count_max,
                    podcast_episode_url=podcast_episode_url,
                )
                fut = pool.submit(
                    ask_multi,
                    messages,
                    system=system,
                    model=self._model,
                    max_tokens=8192,
                )
                futures[fut] = tonal

            for fut in as_completed(futures):
                tonal = futures[fut]
                # Re-raise on failure — engine handles per-channel isolation.
                body = fut.result()
                _check_word_count(
                    body,
                    tonal=tonal,  # type: ignore[arg-type]
                    min_count=self._profile.word_count_min,
                    max_count=self._profile.word_count_max,
                )
                results[tonal] = body.rstrip() + "\n"

        # Emit artifacts in deterministic FB_TONALS order.
        return [
            ChannelArtifact(
                filename=fb_filename(tonal),
                content=results[tonal],
                channel=f"fb-{tonal}",
            )
            for tonal in FB_TONALS
        ]
