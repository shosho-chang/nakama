"""BaseAgent — 所有 agent 的抽象基底類別。

提供統一的 lifecycle：init → run → cleanup
以及共用的 logging、狀態追蹤、錯誤處理、跨 session 記憶。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from shared.anthropic_client import set_current_agent
from shared.log import get_logger, kb_log
from shared.memory import get_context, memory_as_system_block
from shared.notifier import send_email
from shared.state import finish_run, start_run


class BaseAgent(ABC):
    """Agent 基底類別。"""

    name: str = "base"

    def __init__(self) -> None:
        self.logger = get_logger(f"nakama.{self.name}")
        self._run_id: int | None = None

    @abstractmethod
    def run(self) -> str:
        """執行 agent 的主要邏輯，回傳摘要文字。"""
        ...

    def get_memory_context(self, task: Optional[str] = None) -> str:
        """取得此 agent 的記憶，可注入 Claude system prompt。

        合併 shared.md（全員共用）和 agents/{name}.md（agent 專屬）。
        使用 ADR-002 Tier 2 智能載入。
        """
        return get_context(self.name, task=task)

    def execute(self) -> None:
        """完整的執行 lifecycle：紀錄開始 → run → 紀錄結束 → 錯誤通知。"""
        self.logger.info(f"[{self.name}] 開始執行")
        self._run_id = start_run(self.name)
        set_current_agent(self.name, self._run_id)

        try:
            summary = self.run()
            finish_run(self._run_id, status="done", summary=summary)
            self.logger.info(f"[{self.name}] 執行完成：{summary}")
        except Exception as e:
            finish_run(self._run_id, status="error", summary=str(e))
            self.logger.error(f"[{self.name}] 執行失敗：{e}", exc_info=True)
            kb_log(self.name, "error", str(e))
            send_email(
                f"{self.name} 執行失敗",
                f"Agent {self.name} 在執行過程中發生錯誤：\n\n{e}",
            )
            raise
