"""Brainstorm orchestrator — 用戶主導版（Q3 P1）。

Trigger: `@nakama brainstorm <主題>` → 這裡。

P1 設計：
- 由 topic keyword 選 2 個「參與者」agent（relevance-based routing）
- 每個參與者各給一段觀點（~150 字），用各自人格 prompt 過 LLM
- Nami 最後做 synthesizer，給 action items
- 全部走完一次 post 回 Slack（不做 per-turn streaming）

不實作（留給 P2/P3）：
- 10-turn loop、🛑 / 收斂 stop 條件、nightly budget cap
- Zoro agent-initiated triggers、Nami 晨報整合
- Free-form multi-agent cross-talk

設計決策見 memory/claude/project_multi_model_architecture.md（Q3 區段）。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from shared.llm import ask
from shared.llm_context import set_current_agent
from shared.log import get_logger
from shared.prompt_loader import load_prompt

logger = get_logger("nakama.gateway.orchestrator")

# 每個參與者 agent 的簡短 bio + 會被選中的 topic 關鍵字。
# routing 是 topic 關鍵字 → participants 集合。命中越多的 agent 優先。
# 目標：給不同 domain 的觀點組合，避免兩個 agent 講同一件事。
_PARTICIPANT_PROFILES: dict[str, dict] = {
    "sanji": {
        "emoji": ":cook:",
        "voice": "社群廚師視角：生活習慣、飲食、情緒、社群氣氛",
        "topics": [
            "飲食",
            "食物",
            "料理",
            "習慣",
            "情緒",
            "壓力",
            "睡眠",
            "社群",
            "會員",
            "自由艦隊",
        ],
    },
    "robin": {
        "emoji": ":books:",
        "voice": "考古學家視角：文獻 / 研究證據 / 知識庫既有整理",
        "topics": [
            "研究",
            "論文",
            "期刊",
            "證據",
            "機轉",
            "機制",
            "knowledge",
            "知識庫",
            "KB",
            "文獻",
        ],
    },
    "zoro": {
        "emoji": ":crossed_swords:",
        "voice": "劍士視角：市場趨勢、觀眾聲音、競爭情報",
        "topics": [
            "趨勢",
            "關鍵字",
            "SEO",
            "搜尋",
            "YouTube",
            "觀眾",
            "市場",
            "競爭",
            "對手",
            "熱度",
        ],
    },
    "brook": {
        "emoji": ":musical_note:",
        "voice": "音樂家視角：故事結構、長文敘事、觸動讀者的切入",
        "topics": ["文章", "寫文", "標題", "腳本", "長文", "部落格", "敘事", "故事", "觀點"],
    },
}

# Nami 永遠是 synthesizer（決策見 Q3.5 — Summary owner: Nami）
_SYNTHESIZER = "nami"

# 預設：如果 topic 沒命中任何 profile，走保守的「社群 + 研究」組合
_DEFAULT_PARTICIPANTS = ["sanji", "robin"]

# 單一參與者字數上限（避免 Grok / Claude 寫到失控）
_PARTICIPANT_MAX_TOKENS = 512
_SYNTHESIZER_MAX_TOKENS = 768


@dataclass
class BrainstormResult:
    """Orchestrator 回傳結構 — handler / bot 層取來組 Slack blocks。"""

    topic: str
    participants: list[str]
    views: dict[str, str]  # agent_name → 觀點文字
    synthesis: str  # Nami 的整合 + action items


def select_participants(topic: str, *, max_count: int = 2) -> list[str]:
    """Topic keyword 命中數排序，取前 N 個 agent。

    完全不命中就回預設 `_DEFAULT_PARTICIPANTS`，Nami 不在候選（它永遠是 synthesizer）。
    """
    topic_lower = topic.lower()
    scored: list[tuple[int, str]] = []
    for agent, profile in _PARTICIPANT_PROFILES.items():
        hits = sum(1 for kw in profile["topics"] if kw.lower() in topic_lower)
        if hits > 0:
            scored.append((hits, agent))

    if not scored:
        return list(_DEFAULT_PARTICIPANTS[:max_count])

    scored.sort(key=lambda x: (-x[0], x[1]))  # 命中多的先，平手依字母排穩定性
    return [agent for _, agent in scored[:max_count]]


def _collect_views_parallel(participants: list[str], topic: str) -> dict[str, str]:
    """跑所有 participants 並回傳 views dict，保留 participants 原順序。

    `feedback_parallel_sub_agents.md`：獨立 sub-agent 要並行（時間 > token 成本）。
    兩個 participant 各約 2-5s 的 LLM call，並行可省一半等待。synthesizer 必須
    等兩邊回來才能開跑，所以只平行化這一階段。

    threading.local 每 worker thread 有獨立副本，所以各自的 `set_current_agent`
    不會互相污染 cost tracking 的 agent 標記。`_run_participant` 自己包 try/except，
    future.result() 不會拋出；failed agent 會寫進 views 的占位文字。
    """
    if not participants:
        return {}
    if len(participants) == 1:
        agent = participants[0]
        return {agent: _run_participant(agent, topic)}

    views: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=len(participants)) as pool:
        futures = [pool.submit(_run_participant, agent, topic) for agent in participants]
        # zip 讓 views 的 insertion order == participants 順序，下游的 Slack block
        # 順序才穩定，即使 robin 的 future 比 sanji 早完成也一樣。
        for agent, future in zip(participants, futures):
            views[agent] = future.result()
    return views


def _run_participant(agent: str, topic: str) -> str:
    """叫某個 agent 用它的 persona 對 topic 給一段觀點。

    每個參與者自己的 persona prompt 載入（先試 persona，再試 agent_system 之類
    fallback）。找不到就用一個通用 system。
    """
    set_current_agent(agent)

    system = _load_persona(agent)
    profile = _PARTICIPANT_PROFILES.get(agent, {})
    voice = profile.get("voice", "")
    user_msg = (
        f"以下是用戶丟到 brainstorm 的主題：\n\n「{topic}」\n\n"
        f"請用你的角色視角（{voice}）給一段 120-180 字的觀點 / 建議，"
        "要具體、有 actionable 內容，不要空話。不需要自我介紹，直接切入。"
    )
    try:
        return ask(prompt=user_msg, system=system, max_tokens=_PARTICIPANT_MAX_TOKENS).strip()
    except Exception as e:
        logger.error(f"{agent} brainstorm 失敗：{e}", exc_info=True)
        return f"（{agent} 此次暫時沒給出觀點：{e}）"


def _load_persona(agent: str) -> str:
    """嘗試載入 agent persona。找不到就給一個最簡化的 fallback。"""
    for candidate in ("persona", "slack", "agent_system"):
        try:
            return load_prompt(agent, candidate)
        except FileNotFoundError:
            continue
    return (
        f"你是 {agent}，張修修海賊團的一員。用繁體中文、台灣語境，語氣自然口語，直接給有用的觀點。"
    )


def _synthesize(topic: str, views: dict[str, str]) -> str:
    """Nami 當 synthesizer：把各家觀點收斂成 action items。"""
    set_current_agent(_SYNTHESIZER)

    sections = "\n\n".join(f"【{agent}】\n{view}" for agent, view in views.items() if view)
    user_msg = (
        f"Topic：{topic}\n\n"
        f"以下是船員們對這個主題的觀點：\n\n{sections}\n\n"
        "請你（Nami）做整合：\n"
        "1. 用 2-3 句指出這些觀點的共識 / 分歧\n"
        "2. 給修修 2-3 個具體 action items（下一步可以做什麼）\n"
        "3. 口語、直接、不要客套、不超過 200 字"
    )
    system = _load_persona(_SYNTHESIZER)
    try:
        return ask(prompt=user_msg, system=system, max_tokens=_SYNTHESIZER_MAX_TOKENS).strip()
    except Exception as e:
        logger.error(f"synthesizer 失敗：{e}", exc_info=True)
        return f"（整合階段出狀況：{e}。觀點已附上，請自己取捨。）"


def run_brainstorm(topic: str, *, max_participants: int = 2) -> BrainstormResult:
    """跑一次 P1 brainstorm。

    參數只需要 topic；選參與者、跑每家觀點、最後 Nami 收斂都包在這。
    回傳 `BrainstormResult`，呼叫端（handler / bot）負責格式化成 Slack blocks。
    """
    topic = topic.strip()
    if not topic:
        return BrainstormResult(
            topic="",
            participants=[],
            views={},
            synthesis="（brainstorm 需要一個主題，試試「@nakama brainstorm 如何戒宵夜」）",
        )

    participants = select_participants(topic, max_count=max_participants)
    logger.info(f"brainstorm 主題={topic!r} 參與者={participants}")

    views = _collect_views_parallel(participants, topic)
    synthesis = _synthesize(topic, views)

    return BrainstormResult(
        topic=topic,
        participants=participants,
        views=views,
        synthesis=synthesis,
    )


def format_brainstorm_blocks(result: BrainstormResult) -> tuple[str, list[dict]]:
    """把 BrainstormResult 變成 Slack Block Kit。回 (fallback_text, blocks)。"""
    from gateway.formatters import AGENT_EMOJI

    if not result.topic:
        return (
            result.synthesis,
            [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": result.synthesis},
                }
            ],
        )

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f":sparkles: Brainstorm｜{result.topic}"},
        }
    ]
    for agent, view in result.views.items():
        emoji = AGENT_EMOJI.get(agent, ":robot_face:")
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{emoji} *{agent.capitalize()}*\n{view}",
                },
            }
        )
    blocks.append({"type": "divider"})
    nami_emoji = AGENT_EMOJI.get(_SYNTHESIZER, ":tangerine:")
    blocks.append(
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{nami_emoji} *{_SYNTHESIZER.capitalize()}（整合）*\n{result.synthesis}",
            },
        }
    )

    fallback = f"[brainstorm] {result.topic}\n\n{result.synthesis}"
    return fallback, blocks
