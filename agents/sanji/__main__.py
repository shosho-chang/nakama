"""python -m agents.sanji 的入口。

步驟 2（2026-04-20）第一版：只載入人格 prompt + 呼叫 `shared.llm.ask()`，
跑一次 self-introduction 驗證 xAI Grok 端到端通了（router 解析 → xAI
wrapper → cost tracking）。

社群監控、Slack bot、會員互動等功能留給後續步驟（Q3 P1 起）。
"""

from __future__ import annotations

from agents.base import BaseAgent
from shared.llm import ask
from shared.prompt_loader import load_prompt

# 第一版 smoke prompt — 讓 Sanji 用人格自我介紹，實際場景由 Slack gateway 餵真 user msg
_SMOKE_USER_MSG = "自我介紹一下，讓還沒見過你的社群成員認識你。"


class SanjiAgent(BaseAgent):
    name = "sanji"

    def run(self) -> str:
        system = load_prompt("sanji", "persona")
        reply = ask(prompt=_SMOKE_USER_MSG, system=system, max_tokens=512)
        self.logger.info(f"[sanji] self-intro:\n{reply}")
        return f"sanji smoke-run ok ({len(reply)} chars)"


if __name__ == "__main__":
    SanjiAgent().execute()
