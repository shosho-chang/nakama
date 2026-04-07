"""BaseAgent — 所有 agent 的抽象基底類別。

提供統一的 lifecycle：init → run → cleanup
以及共用的 logging、狀態追蹤、錯誤處理。
"""

from abc import ABC, abstractmethod

from shared.log import get_logger, kb_log
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

    def execute(self) -> None:
        """完整的執行 lifecycle：紀錄開始 → run → 紀錄結束 → 錯誤通知。"""
        self.logger.info(f"[{self.name}] 開始執行")
        self._run_id = start_run(self.name)

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
