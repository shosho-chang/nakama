"""Zoro brainstorm scout — 白天自動偵查熱點、過四道濾網、推一個題到 #brainstorm。

設計來自 docs/decisions/step-5-zoro-brainstorm-p2.md。

Pipeline：

    gather_signals()                    # 從 Trends / Reddit / YouTube / PubMed 收原始訊號
        ↓
    velocity_gate(signals)              # 只留 velocity ≥ 閾值的
        ↓
    signals_to_topics(signals)          # 把 signals 整合成候選 Topic
        ↓
    relevance_gate(topics)              # keyword 預濾 + LLM 判準（scout.md）
        ↓
    novelty_gate(topics)                # agent_memory 查 14d 內已推過的
        ↓
    cooldown_gate(topics)               # 48h 內近似題 skip
        ↓
    pick_best_topic(topics)             # 挑 velocity × relevance 最高的
        ↓
    publish_to_slack(topic)             # 以 Zoro bot 身份 post 到 #brainstorm
    pushed_topics.record(...)           # 記錄供下次 novelty/cooldown 查

**Slice C 範圍**：Reddit hot discovery + Linux cron（cron.conf 05:00 台北）。
**Slice C1**：切主 source 到 Google Trends（en-US trending_now），Reddit 因 VPS
datacenter IP 被匿名 API 封 403，留到 Slice D 做 OAuth。
**Slice D 待接**：Reddit OAuth、youtube_api discover、真 `<@U...>` mention、Nami 晨報整合。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Literal

from shared import pushed_topics
from shared.llm import ask
from shared.log import get_logger
from shared.prompt_loader import load_prompt

logger = get_logger("nakama.zoro.scout")

SignalSource = Literal["trends", "reddit", "youtube", "pubmed"]


@dataclass
class Signal:
    """單一資料源對一個主題的原始觀察。"""

    source: SignalSource
    topic: str
    velocity_score: float  # 0–100，各 source 內部 normalize
    metadata: dict = field(default_factory=dict)


@dataclass
class Topic:
    """過濾後的候選主題，聚合 1+ signals。"""

    title: str
    normalized_keywords: list[str]
    signals: list[Signal]
    velocity_score: float = 0.0
    relevance_score: float = 0.0
    relevance_reason: str = ""
    domain: str | None = None


# ── 1. Data gathering ───────────────────────────────────────────────────────


def gather_signals() -> list[Signal]:
    """收集所有資料源訊號。

    Slice C1（2026-04-24）：主 source = Google Trends（en-US trending_now 濾健康）。
    Reddit 因 VPS datacenter IP 被 Reddit 封 403（匿名 API），留到 Slice D 做 OAuth 時接回。
    """
    signals: list[Signal] = []
    signals.extend(_gather_trends_signals())
    return signals


def _gather_trends_signals() -> list[Signal]:
    """從 Google Trends trending_now en-US 拉健康相關趨勢，轉 Signal。"""
    from agents.zoro import trends_api

    try:
        terms = trends_api.discover_trending_health()
    except Exception as e:
        logger.warning(f"trends signal gather failed: {e}")
        return []

    signals: list[Signal] = []
    for t in terms:
        title = (t.get("title") or "").strip()
        if not title:
            continue
        signals.append(
            Signal(
                source="trends",
                topic=title,
                velocity_score=float(t.get("velocity_score", 0.0)),
                metadata={
                    "volume": t.get("volume", 0),
                    "growth_pct": t.get("score", 0),
                    "related": t.get("related", [])[:5],  # 只留前 5 個 related 避免訊息太長
                },
            )
        )
    logger.info(f"trends gather: {len(signals)} signals from {len(terms)} trends")
    return signals


# ── 2. Velocity gate ────────────────────────────────────────────────────────

DEFAULT_MIN_VELOCITY = 30.0


def velocity_gate(
    signals: list[Signal], *, min_velocity: float = DEFAULT_MIN_VELOCITY
) -> list[Signal]:
    """只留 velocity_score ≥ 閾值的訊號。"""
    return [s for s in signals if s.velocity_score >= min_velocity]


# ── 3. Signals → Topics ────────────────────────────────────────────────────


def signals_to_topics(signals: list[Signal]) -> list[Topic]:
    """把 signals 整合成候選 Topic。

    簡單版：一個 signal = 一個 topic。未來可依 keyword 重疊合併多 source。
    """
    topics: list[Topic] = []
    for s in signals:
        kws = pushed_topics.normalize_keywords(s.topic.replace(",", " ").split())
        topics.append(
            Topic(
                title=s.topic,
                normalized_keywords=kws,
                signals=[s],
                velocity_score=s.velocity_score,
            )
        )
    return topics


# ── 4. Relevance gate ──────────────────────────────────────────────────────

# Keyword pre-filter：cheap 濾掉明顯無關題目，減少 LLM judge call。
# 四大面向對應的種子詞。命中任一個就進 LLM judge；否則 reject。
_KEYWORD_SEEDS: dict[str, list[str]] = {
    "睡眠": ["sleep", "circadian", "insomnia", "nap", "chronotype", "melatonin", "睡眠", "失眠"],
    "飲食": [
        "nutrition",
        "fasting",
        "metabolism",
        "supplement",
        "protein",
        "carb",
        "glucose",
        "keto",
        "fiber",
        "飲食",
        "營養",
        "斷食",
        "血糖",
    ],
    "運動": [
        "exercise",
        "strength",
        "cardio",
        "running",
        "hiit",
        "mobility",
        "recovery",
        "vo2",
        "zone 2",
        "運動",
        "重訓",
        "肌力",
    ],
    "情緒": [
        "stress",
        "mood",
        "anxiety",
        "depression",
        "meditation",
        "mindful",
        "情緒",
        "焦慮",
        "壓力",
        "冥想",
    ],
}

DEFAULT_RELEVANCE_MIN = 0.7


def _keyword_prefilter(topic: Topic) -> set[str]:
    """回傳 topic 命中的領域集合。"""
    text = topic.title.lower()
    hits = set()
    for domain, seeds in _KEYWORD_SEEDS.items():
        if any(seed.lower() in text for seed in seeds):
            hits.add(domain)
    return hits


_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?|\n?```\s*$", re.IGNORECASE)


def _strip_json_fence(raw: str) -> str:
    """剝掉 LLM 有時多加的 ```json … ``` markdown 外殼（即使 prompt 禁止）。"""
    return _JSON_FENCE_RE.sub("", raw.strip()).strip()


def _llm_judge_relevance(topic_text: str) -> dict:
    """呼叫 LLM 用 prompts/zoro/scout.md 判 relevance。回傳 dict with score / reason / domain。"""
    try:
        system = load_prompt("zoro", "scout")
    except FileNotFoundError:
        logger.error("scout prompt missing")
        return {"score": 0.0, "reason": "scout prompt missing", "domain": None}

    try:
        raw = ask(prompt=topic_text, system=system, max_tokens=256)
    except Exception as e:
        logger.warning(f"relevance judge LLM failed: {e}")
        return {"score": 0.0, "reason": f"judge error: {e}", "domain": None}

    cleaned = _strip_json_fence(raw)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning(f"relevance judge returned non-JSON, rejecting: {raw[:120]!r}")
        return {"score": 0.0, "reason": "judge parse failed", "domain": None}

    try:
        score = float(payload.get("score", 0.0))
    except (TypeError, ValueError):
        score = 0.0
    return {
        "score": max(0.0, min(1.0, score)),
        "reason": str(payload.get("reason", ""))[:200],
        "domain": payload.get("domain"),
    }


def relevance_gate(
    topics: list[Topic],
    *,
    llm_judge: bool = True,
    min_score: float = DEFAULT_RELEVANCE_MIN,
) -> list[Topic]:
    """Keyword 預濾 → 命中才送 LLM judge → ≥ min_score 才保留。

    llm_judge=False 時只靠 keyword，score 給 0.75（pass-through）。
    """
    kept: list[Topic] = []
    for t in topics:
        hits = _keyword_prefilter(t)
        if not hits:
            logger.debug(f"relevance reject (no keyword hit): {t.title!r}")
            continue
        if not llm_judge:
            t.relevance_score = 0.75
            t.domain = next(iter(hits))
            kept.append(t)
            continue
        judgment = _llm_judge_relevance(t.title)
        t.relevance_score = judgment["score"]
        t.relevance_reason = judgment["reason"]
        t.domain = judgment["domain"]
        if t.relevance_score >= min_score:
            kept.append(t)
        else:
            logger.debug(
                f"relevance reject (score={t.relevance_score:.2f} < {min_score}): {t.title!r}"
            )
    return kept


# ── 5/6. Novelty & Cooldown gates ─────────────────────────────────────────


def novelty_gate(topics: list[Topic], *, agent: str = "zoro") -> list[Topic]:
    return [t for t in topics if pushed_topics.is_novel(agent, t.normalized_keywords)]


def cooldown_gate(topics: list[Topic], *, agent: str = "zoro") -> list[Topic]:
    return [t for t in topics if not pushed_topics.is_on_cooldown(agent, t.normalized_keywords)]


# ── 7. Pick best topic ─────────────────────────────────────────────────────


def pick_best_topic(topics: list[Topic]) -> Topic | None:
    """挑 velocity × relevance 最高的。空 list 回 None。"""
    if not topics:
        return None
    return max(topics, key=lambda t: t.velocity_score * t.relevance_score)


# ── 8. Publish to Slack ────────────────────────────────────────────────────

_DEFAULT_MENTIONS = ["@Sanji", "@Robin"]


def format_publish_message(topic: Topic, mentions: list[str] | None = None) -> str:
    """Fallback template（用於 LLM compose 失敗時）。純文字無 markdown — Slack mrkdwn
    對 CJK `*bold*` 判斷不穩，讓訊息長得一致。"""
    mentions = mentions or _DEFAULT_MENTIONS
    lines: list[str] = [
        f"🗡️ {topic.title} 在 {topic.signals[0].source if topic.signals else '某處'} 熱起來。",
    ]
    if topic.velocity_score:
        lines.append(
            f"Velocity {topic.velocity_score:.0f}、relevance {topic.relevance_score:.2f}。"
        )
    if topic.relevance_reason:
        lines.append(topic.relevance_reason)
    lines.append("")
    lines.append(f"{' '.join(mentions)} 看要不要聊？")
    return "\n".join(lines)


def _build_compose_input(topic: Topic) -> str:
    """把 Topic 的原始訊號打包成 compose prompt 的 user message。"""
    lines: list[str] = [f"Topic: {topic.title}"]
    if topic.relevance_score:
        lines.append(f"Relevance judge score: {topic.relevance_score:.2f}")
    if topic.relevance_reason:
        lines.append(f"Relevance judge reason: {topic.relevance_reason}")
    if topic.domain:
        lines.append(f"Domain: {topic.domain}")
    lines.append("")
    lines.append("Signals:")
    for s in topic.signals:
        lines.append(f"- Source: {s.source}, velocity={s.velocity_score:.0f}")
        for k, v in (s.metadata or {}).items():
            lines.append(f"  {k}: {v}")
    return "\n".join(lines)


def compose_message(topic: Topic) -> str:
    """用 Zoro 人格 LLM compose 自然口語的 Slack 訊息。

    失敗（prompt 缺 / LLM 炸）→ fallback 到 `format_publish_message` 保底。
    """
    try:
        system = load_prompt("zoro", "compose_message")
    except FileNotFoundError:
        logger.error("compose_message prompt missing, falling back to template")
        return format_publish_message(topic)

    user = _build_compose_input(topic)
    try:
        raw = ask(prompt=user, system=system, max_tokens=500)
    except Exception as e:
        logger.warning(f"compose_message LLM failed ({e}), falling back to template")
        return format_publish_message(topic)

    text = raw.strip()
    # 保險：如果 LLM 忘了邀請，補上
    if "@Sanji" not in text and "@Robin" not in text:
        text = text.rstrip() + "\n\n@Sanji @Robin 看怎麼想？"
    return text


def publish_to_slack(
    topic: Topic,
    *,
    channel: str,
    bot_token: str,
    mentions: list[str] | None = None,
    use_llm_compose: bool = True,
) -> str | None:
    """把 topic post 到指定 Slack channel。回傳 message ts，失敗回 None。

    use_llm_compose=True（預設）→ LLM 用 Zoro 人格寫自然口語訊息。
    False → 走 `format_publish_message` fallback template。
    """
    try:
        from slack_sdk import WebClient
    except ImportError:
        logger.error("slack_sdk not installed")
        return None

    client = WebClient(token=bot_token)
    if use_llm_compose:
        text = compose_message(topic)
    else:
        text = format_publish_message(topic, mentions=mentions)
    try:
        resp = client.chat_postMessage(channel=channel, text=text)
    except Exception as e:
        logger.error(f"publish_to_slack failed: {e}", exc_info=True)
        return None
    return resp.get("ts")


# ── 9. Entry ───────────────────────────────────────────────────────────────


def run(
    *,
    llm_judge: bool = True,
    publish: bool = True,
    record: bool = True,
    channel: str | None = None,
    bot_token: str | None = None,
    mentions: list[str] | None = None,
) -> Topic | None:
    """Scout 主流程。

    publish=True 時需要 `channel` + `bot_token`（未提供則從 env 讀 ZORO_*）。
    env 若也缺 → publish 會 log warning 並跳過，但 record 仍會進 pushed_topics
    （避免下次同題重推）— 除非 record=False（dry-run 用）。
    """
    signals = gather_signals()
    signals = velocity_gate(signals)
    topics = signals_to_topics(signals)
    topics = relevance_gate(topics, llm_judge=llm_judge)
    topics = novelty_gate(topics)
    topics = cooldown_gate(topics)
    best = pick_best_topic(topics)

    if best is None:
        logger.info("scout: no topic passes all gates today")
        return None

    logger.info(
        f"scout picked topic: {best.title!r} "
        f"(velocity={best.velocity_score:.0f}, relevance={best.relevance_score:.2f})"
    )

    if publish:
        ch = channel or os.environ.get("ZORO_BRAINSTORM_CHANNEL_ID", "").strip()
        tok = bot_token or os.environ.get("ZORO_SLACK_BOT_TOKEN", "").strip()
        if not ch or not tok:
            logger.warning(
                "scout: publish skipped (missing ZORO_BRAINSTORM_CHANNEL_ID or "
                "ZORO_SLACK_BOT_TOKEN)"
            )
        else:
            ts = publish_to_slack(best, channel=ch, bot_token=tok, mentions=mentions)
            if ts:
                logger.info(f"scout published to {ch} ts={ts}")

    if record:
        pushed_topics.record("zoro", best.title, best.normalized_keywords)
    return best
