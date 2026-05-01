"""Brook IG Renderer — Stage 2 IG carousel renderer for Line 1 podcast repurpose pipeline.

Input:  Stage1Result (structured narrative JSON from Line1Extractor) + EpisodeMetadata
Output: ChannelArtifact(filename=IG_FILENAME, content=cards JSON, channel="ig")

Pipeline:
1. Load ig-carousel.md style profile (4 episode_type × card-count sub-templates).
2. Read ``stage1.data['episode_type']`` → look up expected card count via
   ``EPISODE_TYPE_CARD_COUNT`` (narrative_journey=5, myth_busting=7,
   framework=5, listicle=10).
3. Build Sonnet 4.6 prompt with Stage 1 JSON + ig-carousel.md profile body +
   episode-type-specific card structure directive.
4. LLM call → parse JSON cards.
5. Validate output: card count matches expected, per-card char limits
   (cover ≤10 字, middle ≤12 字), required fields present.
6. Emit single ChannelArtifact with the validated cards JSON.

Episode type → card count mapping (PRD #283 凍結, see issue #291):
- ``narrative_journey``: 5 卡（Bait / Why / 核心引述 / 反差 / CTA）
- ``myth_busting``     : 7 卡（Hook / Myth / Fact / 機制 1 / 機制 2 / 行動 / CTA）
- ``framework``        : 5 卡（Bait / Setup / 4 原則合成 / 4 原則展開 / CTA）
- ``listicle``         : 10 卡（Hook / Setup / 7 points / mid-CTA save / Land）

Stage 1 schema gap (IG-specific): Stage 1 currently does not extract a
``framework_steps[]`` or ``listicle_items[]`` field. For framework / listicle
episode types, IGRenderer's prompt includes a sub-extraction directive telling
the LLM to derive items from ``quotes`` / ``present_action`` / ``rebirth``
text. This is documented in ig-carousel.md §2.3-2.4 and §7 範本 D.

Style profile data sourced from ``load_style_profile("ig-carousel")`` so the
canonical ``config/style-profiles/ig-carousel.yaml`` is the single source of
truth (no hardcoded char-count thresholds in this module).
"""

from __future__ import annotations

import json
import re
from typing import Literal

from agents.brook.repurpose_engine import (
    IG_FILENAME,
    ChannelArtifact,
    EpisodeMetadata,
    Stage1Result,
)
from agents.brook.style_profile_loader import StyleProfile, load_style_profile
from shared.llm import ask_multi
from shared.log import get_logger

logger = get_logger("nakama.brook.ig_renderer")

_DEFAULT_MODEL = "claude-sonnet-4-6"
_IG_PROFILE_CATEGORY = "ig-carousel"

EpisodeType = Literal["narrative_journey", "myth_busting", "framework", "listicle"]

# PRD-frozen card count per episode_type (issue #291).
EPISODE_TYPE_CARD_COUNT: dict[EpisodeType, int] = {
    "narrative_journey": 5,
    "myth_busting": 7,
    "framework": 5,
    "listicle": 10,
}

# Per-card hard char limits from ig-carousel.md §5.
# These are the OUTPUT validation thresholds; the prompt enforces softly.
_COVER_HEADLINE_MAX = 10
_MIDDLE_HEADLINE_MAX = 12
_CARD_BODY_MAX = 80  # per card, ig-carousel.md §5
_TOTAL_CHAR_MIN = 150
_TOTAL_CHAR_MAX = 300


# ---------------------------------------------------------------------------
# Sub-template directives (per episode_type)
# ---------------------------------------------------------------------------

# Each directive describes the canonical card layout for that episode_type
# per ig-carousel.md §2.{1-4}. Injected into the LLM prompt to lock structure.
_SUB_TEMPLATE_DIRECTIVES: dict[EpisodeType, str] = {
    "narrative_journey": (
        "**narrative_journey 5 卡**（來賓敘事弧線；ig-carousel.md §2.1）\n"
        "- C1 封面（Bait）：反轉 hook 或戲劇性數字（≤10 字）\n"
        "- C2 起點（Hook）：來賓的「混亂時期」before 場景（從 stage1.origin）\n"
        "- C3 轉折（Reel）：觸發轉變的關鍵事件／對話（從 stage1.turning_point）\n"
        "- C4 收穫（Reel）：一個關鍵洞察（從 stage1.rebirth）\n"
        "- C5 落地 CTA：podcast 引流（聽 EP 完整版） + Save 動機\n"
        "- 主體：來賓的故事，**不是修修自己的**\n"
    ),
    "myth_busting": (
        "**myth_busting 7 卡**（迷思破解；ig-carousel.md §2.2）\n"
        "- C1 封面（Bait）：「你以為 X，其實 Y」反差句（≤10 字）\n"
        "- C2 迷思（Hook）：常見誤解具體陳述\n"
        "- C3 反證 1（Reel）：研究／數據／案例反駁 #1\n"
        "- C4 反證 2（Reel）：第二角度反駁（不同來源）\n"
        "- C5 真相（Reel）：一句話總結真相\n"
        "- C6 應用（Reel）：「所以你應該／你可以⋯⋯」具體行動\n"
        "- C7 落地 CTA：Save / DM / 聽 EP\n"
        "- 反證材料從 stage1.quotes / stage1.present_action / stage1.rebirth 派生\n"
    ),
    "framework": (
        "**framework 5 卡**（架構／工具教學；ig-carousel.md §2.3）\n"
        "- C1 封面（Bait）：「{X} 的 N 步驟」/「{大師} 的 {框架名}」（≤10 字）\n"
        "- C2 定義（Hook）：框架解決什麼問題（一句話）\n"
        "- C3 步驟 1+2（Reel）：兩步驟並列（如框架 ≥4 步驟，這裡放前半）\n"
        "- C4 步驟 3+N（Reel）：後續步驟（含執行重點）\n"
        "- C5 落地 CTA：DM 模板索取 / 聽 EP 細節\n"
        "- Stage 1 schema 沒有專屬 framework_steps[] 欄位；從 stage1.quotes / "
        "stage1.present_action / stage1.rebirth 抽出 N 步驟\n"
    ),
    "listicle": (
        "**listicle 10 卡**（清單型；ig-carousel.md §2.4）\n"
        "- C1 封面（Bait）：「{N} 個 {對象} 你必須知道」（≤10 字）\n"
        "- C2-C9 項目 1-8（Reel）：每卡一個 item，標題 4-6 字 + 1-2 句說明\n"
        "- C10 落地 CTA：全清單存檔 / DM PDF / Podcast 詳細介紹\n"
        "- Stage 1 schema 沒有專屬 listicle_items[] 欄位；從 stage1.quotes / "
        "stage1.present_action 派生 8 個 items（**來賓的成果／教學整理**，不是修修推薦）\n"
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_stage1_field(data: dict, key: str) -> object:
    """Pull a required key from stage1.data with a typed error if missing."""
    if key not in data:
        raise ValueError(
            f"IGRenderer: stage1.data is missing required field {key!r} — "
            "ensure the Stage 1 extractor schema is consumed correctly"
        )
    value = data[key]
    if value is None or value == "":
        raise ValueError(f"IGRenderer: stage1.data[{key!r}] is empty")
    return value


def _resolve_episode_type(stage1_data: dict) -> EpisodeType:
    """Read & validate ``episode_type`` from Stage 1 data."""
    raw = _require_stage1_field(stage1_data, "episode_type")
    if raw not in EPISODE_TYPE_CARD_COUNT:
        raise ValueError(
            f"IGRenderer: stage1.data['episode_type']={raw!r} not in "
            f"{list(EPISODE_TYPE_CARD_COUNT.keys())} — Stage 1 schema drift?"
        )
    return raw  # type: ignore[return-value]


def _extract_json(text: str) -> str:
    """Strip markdown code fences and return inner JSON string.

    Mirrors line1_extractor._extract_json — Claude sometimes returns the JSON
    wrapped in ```json ... ``` despite the prompt asking for plain JSON.
    """
    matches = re.findall(r"```(?:json)?\s*([\s\S]+?)```", text)
    if matches:
        return matches[-1].strip()
    return text.strip()


def _build_messages(
    *,
    stage1_data: dict,
    metadata: EpisodeMetadata,
    profile_body: str,
    episode_type: EpisodeType,
    expected_card_count: int,
    podcast_episode_url: str,
) -> list[dict]:
    """Build single-turn user message for the IG carousel render call."""
    stage1_json = json.dumps(stage1_data, ensure_ascii=False, indent=2)
    guest = metadata.extra.get("guest", "受訪者")
    directive = _SUB_TEMPLATE_DIRECTIVES[episode_type]

    content = f"""## 主持人：{metadata.host}
## 來賓：{guest}
## Podcast 連結：{podcast_episode_url}

---

## IG carousel 風格側寫（ig-carousel.md）

{profile_body}

---

## Stage 1 結構化素材

```json
{stage1_json}
```

---

## 撰稿指示

請根據以上 Stage 1 素材，產出一個 IG carousel JSON（{expected_card_count} 卡）。

### Episode 子模板（依 stage1.episode_type 路由）

{directive}

### 字數硬限（ig-carousel.md §5）

- **封面卡標題（C1）**：≤{_COVER_HEADLINE_MAX} 字
- **中段卡標題（C2 ~ C{expected_card_count - 1}）**：≤{_MIDDLE_HEADLINE_MAX} 字
- **每卡內文（body）**：≤{_CARD_BODY_MAX} 字
- **整篇 carousel 總字數**：{_TOTAL_CHAR_MIN}-{_TOTAL_CHAR_MAX} 字（含全部 headline + body）

> 單位＝中文字元數（CJK chars，不含空白），不是英文 word。

### 必做

- 每卡只服務 1 個 atomic idea
- 卡 1 hook 走 ig-carousel.md §3 公式庫之一（具體、反直覺、數字醒目）
- 落地卡 1-2 個 CTA（不超過 2 個）；podcast 引流 carousel 必含「聽 EP 完整版」CTA
- AIDA 四階段都對應到（Bait / Hook / Reel / Land）
- 主體永遠是**來賓**的故事／成果／教學，**不是修修自己的**
- **不要捏造任何外部 URL**（DM 行動、Save、Follow 等都用文字描述，不放 URL）；
  只能放 metadata 提供的 podcast URL

### 禁止

- 禁止單卡塞多個 idea（會視覺擁擠）
- 禁止封面 > {_COVER_HEADLINE_MAX} 字
- 禁止把 podcast 全劇透
- 禁止 hashtag 堆疊在卡片內（hashtag 寫在 caption，不在卡片視覺裡）
- 禁止抽象 hook（「健康新觀念」「你需要知道的事」這類無資訊量句）
- 禁止 5 個 CTA 並列在落地卡（讀者癱瘓）
- 禁止單卡放整段反思散文（每卡一個 atomic idea）
- 禁止用簡體中文或日式漢字

### 輸出格式

請輸出**純 JSON**（不加 markdown fence 或任何說明文字），符合以下 schema：

```json
{{
  "episode_type": "{episode_type}",
  "card_count": {expected_card_count},
  "cards": [
    {{
      "role": "C1",
      "headline": "封面 hook（≤{_COVER_HEADLINE_MAX} 字）",
      "body": "（C1 通常無 body，可空字串；其他卡 body ≤{_CARD_BODY_MAX} 字）",
      "char_count": 8
    }}
  ],
  "total_char_count": 200
}}
```

注意：
- `cards[]` 長度必須 = {expected_card_count}
- `role` 走 `C1` ~ `C{expected_card_count}` 順序
- `char_count` 是該卡 headline + body 的中文字元數
- `total_char_count` 是所有 `char_count` 加總，必須在 {_TOTAL_CHAR_MIN}-{_TOTAL_CHAR_MAX}
"""
    return [{"role": "user", "content": content}]


def _validate_cards_payload(
    payload: dict,
    *,
    expected_card_count: int,
    episode_type: EpisodeType,
) -> None:
    """Validate the parsed JSON payload against IG carousel hard limits.

    Raises:
        ValueError: If schema is malformed or any limit is violated.
    """
    if not isinstance(payload, dict):
        raise ValueError(f"IGRenderer: LLM output is not a JSON object: {type(payload).__name__}")

    if payload.get("episode_type") != episode_type:
        raise ValueError(
            f"IGRenderer: payload episode_type={payload.get('episode_type')!r} "
            f"≠ stage1 episode_type={episode_type!r}"
        )

    cards = payload.get("cards")
    if not isinstance(cards, list):
        raise ValueError(f"IGRenderer: cards must be list, got {type(cards).__name__}")

    if len(cards) != expected_card_count:
        raise ValueError(
            f"IGRenderer: cards length {len(cards)} ≠ expected {expected_card_count} "
            f"for episode_type={episode_type!r}"
        )

    if payload.get("card_count") != expected_card_count:
        raise ValueError(
            f"IGRenderer: card_count={payload.get('card_count')} declares "
            f"≠ actual cards length {len(cards)}"
        )

    for idx, card in enumerate(cards, start=1):
        if not isinstance(card, dict):
            raise ValueError(f"IGRenderer: cards[{idx - 1}] is not an object")
        for required in ("role", "headline", "body", "char_count"):
            if required not in card:
                raise ValueError(
                    f"IGRenderer: cards[{idx - 1}] missing required field {required!r}"
                )
        expected_role = f"C{idx}"
        if card["role"] != expected_role:
            raise ValueError(
                f"IGRenderer: cards[{idx - 1}].role={card['role']!r} ≠ {expected_role!r}"
            )

        headline = str(card["headline"])
        head_max = _COVER_HEADLINE_MAX if idx == 1 else _MIDDLE_HEADLINE_MAX
        if len(headline) > head_max:
            raise ValueError(
                f"IGRenderer: card {expected_role} headline ({len(headline)} 字) "
                f"exceeds limit {head_max}: {headline!r}"
            )

        body = str(card.get("body") or "")
        if len(body) > _CARD_BODY_MAX:
            raise ValueError(
                f"IGRenderer: card {expected_role} body ({len(body)} 字) "
                f"exceeds limit {_CARD_BODY_MAX}"
            )

    total = payload.get("total_char_count")
    if not isinstance(total, int):
        raise ValueError(f"IGRenderer: total_char_count must be int, got {type(total).__name__}")
    # Soft band — log warning rather than raise, since LLM small drift is
    # tolerable (a 295 vs 300 split shouldn't fail-close the whole pipeline).
    if total < _TOTAL_CHAR_MIN or total > _TOTAL_CHAR_MAX:
        logger.warning(
            "ig total_char_count=%d outside soft band [%d, %d] for episode_type=%s",
            total,
            _TOTAL_CHAR_MIN,
            _TOTAL_CHAR_MAX,
            episode_type,
        )


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


class IGRenderer:
    """Stage 2 IG carousel renderer for Line 1 podcast repurpose pipeline.

    Consumes Stage1Result from Line1Extractor → outputs single ig-cards.json
    artifact via Sonnet 4.6 single-call (no parallelism — 1 carousel per EP).

    Implements ChannelRenderer Protocol (agents.brook.repurpose_engine).

    Routes by ``stage1.data['episode_type']`` to one of 4 sub-templates with
    hardcoded card counts (5/7/5/10) — explicit table, not LLM-judged.

    Style profile (body, char-count limits) sourced from
    ``load_style_profile("ig-carousel")`` by default; pass ``style_profile=``
    to override (e.g. tests).

    Cost tracking is automatic via ``ask_multi`` → ``_record_anthropic_usage``.
    """

    def __init__(
        self,
        *,
        style_profile: StyleProfile | None = None,
        model: str | None = None,
    ) -> None:
        self._profile = style_profile or load_style_profile(_IG_PROFILE_CATEGORY)
        self._model = model or _DEFAULT_MODEL

    def render(self, stage1: Stage1Result, metadata: EpisodeMetadata) -> list[ChannelArtifact]:
        """Render a single ig-cards.json artifact.

        Args:
            stage1: Stage1Result from Line1Extractor (validated JSON).
            metadata: EpisodeMetadata — host/guest names, podcast URL.

        Returns:
            Single-element list with ChannelArtifact(filename=IG_FILENAME,
            channel="ig", content=cards JSON).

        Raises:
            ValueError: If Stage 1 data is missing/invalid, or LLM output
                fails JSON parse / card-count / char-limit validation.
        """
        data = stage1.data
        episode_type = _resolve_episode_type(data)
        expected_count = EPISODE_TYPE_CARD_COUNT[episode_type]

        # Validate other required Stage 1 fields up front (fail fast).
        for key in ("identity_sketch", "origin", "turning_point", "rebirth", "quotes"):
            _require_stage1_field(data, key)

        podcast_episode_url = metadata.extra.get("podcast_episode_url", "")

        messages = _build_messages(
            stage1_data=data,
            metadata=metadata,
            profile_body=self._profile.body,
            episode_type=episode_type,
            expected_card_count=expected_count,
            podcast_episode_url=podcast_episode_url,
        )
        system = (
            "你是 Brook，不正常人類研究所 Podcast 的 AI 寫作助手。"
            "任務是根據 Stage 1 素材產出 IG carousel JSON。"
            "嚴格遵守卡數、字數硬限與輸出 JSON schema。只輸出純 JSON，不加 markdown fence。"
        )

        raw = ask_multi(messages, system=system, model=self._model, max_tokens=4096)
        json_text = _extract_json(raw)
        try:
            payload = json.loads(json_text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"IGRenderer: LLM output is not valid JSON: {exc}; raw[:200]={raw[:200]!r}"
            ) from exc

        _validate_cards_payload(
            payload, expected_card_count=expected_count, episode_type=episode_type
        )

        artifact_content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        return [
            ChannelArtifact(
                filename=IG_FILENAME,
                content=artifact_content,
                channel="ig",
            )
        ]
