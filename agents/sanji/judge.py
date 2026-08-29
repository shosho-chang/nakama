"""打卡判定漏斗——Phase 1 實作（①機械 ②Haiku 初判 ⑥provisional ⑦佇列）。

裁決原則（agents/sanji/CONTEXT.md）：
- 這不是金融系統，**錯放的代價遠低於錯擋**——判定不確定或判定系統故障時，
  一律往寬鬆的方向落（provisional：先給分、標記，事後不通過走沖正）。
- 只有「明顯不是打卡」（廣告、亂碼、明顯無關）才 reject。
- 有照片＝證據標準成立 → 機械通過，不花 LLM（漏斗①解決大多數）。

Phase 2 再加：③信任分 ⑤問本人；影像視覺判定；perceptual hash 去重。
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from shared.agent_sdk import subscription_env
from shared.llm_router import get_model
from shared.log import get_logger

logger = get_logger("nakama.sanji.judge")

_MAX_TEXT = 1500  # 貼文送判定的截斷長度


@dataclass(frozen=True)
class Decision:
    action: str  # approve / provisional / queue / reject
    note: str


_SYSTEM_PROMPT = """你是「自由艦隊」社群的打卡審核員。\
會員在習慣挑戰中貼出當日練習紀錄，你判斷這是否為可信的本人打卡。

判定標準（寬鬆優先）：
- pass：看起來是本人的練習紀錄或心得，跟挑戰主題相關（描述練習、感受、過程都算）
- fail：明顯不是打卡——廣告、推銷、亂碼、與主題完全無關的內容
- unsure：無法確定

只輸出一行 JSON，不要任何其他文字：
{"verdict": "pass|fail|unsure", "reason": "十字以內"}"""


def mechanical_precheck(feed: dict) -> Decision | None:
    """漏斗①：零 LLM 的機械判定。回 None = 交給 Haiku。"""
    media = feed.get("media") or []
    text = str(feed.get("message") or "").strip()

    if media:
        # 照片/影音即證據標準（打卡活動的規則就是貼證據）。
        return Decision("approve", f"mechanical:media x{len(media)}")

    if len(text) < 10:
        # 無媒體又幾乎沒字——資訊不足，進佇列等人（或 48h fail-open）。
        return Decision("queue", "mechanical:short-text-no-media")

    return None


async def _haiku_check(feed: dict, theme: str) -> Decision:
    text = str(feed.get("message") or "")[:_MAX_TEXT]
    prompt = f"挑戰主題：{theme}\n\n會員貼文：\n{text}"

    options = ClaudeAgentOptions(
        model=get_model(agent="sanji", task="gam_judge"),
        system_prompt=_SYSTEM_PROMPT,
        tools=[],  # 純文字判定，零工具
        setting_sources=[],  # 不載入本機設定（Nami S0-Q1 教訓）
        max_turns=1,
        env=subscription_env(),  # 承重牆：沒有這行會在 API 額度空時秒死
    )

    result_text = ""
    # SDK 對 error 的形狀是「先 yield ResultMessage 再 raise」——drain 到自然結束，
    # 不 break（子進程清理，PR #1121 review B1 的教訓）。
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage) and not result_text:
            result_text = str(getattr(message, "result", "") or "")

    m = re.search(r"\{.*\}", result_text, re.DOTALL)
    if not m:
        return Decision("provisional", "haiku:unparseable")
    try:
        parsed = json.loads(m.group(0))
    except json.JSONDecodeError:
        return Decision("provisional", "haiku:bad-json")

    verdict = str(parsed.get("verdict", "")).lower()
    reason = str(parsed.get("reason", ""))[:80]
    if verdict == "pass":
        return Decision("approve", f"haiku:pass:{reason}")
    if verdict == "fail":
        return Decision("reject", f"haiku:fail:{reason}")
    return Decision("provisional", f"haiku:unsure:{reason}")


def judge_feed(feed: dict, theme: str) -> Decision:
    """完整判定：機械層 → Haiku。判定系統本身故障 → provisional（fail-open 精神：
    quota 耗盡或 SDK 掛掉時，會員體驗不卡住；每日對帳仍會覆核 provisional 案）。
    """
    mech = mechanical_precheck(feed)
    if mech is not None:
        return mech

    try:
        return asyncio.run(_haiku_check(feed, theme))
    except Exception as exc:  # noqa: BLE001 — 判定故障必須降級而不是炸掉 loop
        logger.warning(f"[judge] haiku check failed, falling back to provisional: {exc}")
        return Decision("provisional", f"haiku:error:{type(exc).__name__}")
