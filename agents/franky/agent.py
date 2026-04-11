"""Franky — 工程週報 Agent（船匠）。

每週一 01:00 執行：
1. 讀取 AgentReports/dev-backlog.md
2. 取得上週報告（供對比）
3. 系統健康檢查
4. 呼叫 Claude 產出週報
5. 寫入 AgentReports/franky/YYYY-WW.md
6. 記錄到 KB/log.md
7. emit 事件給 Nami
"""

from agents.base import BaseAgent
from agents.franky.reporter import ReportGenerator, SystemHealthChecker
from shared.config import get_agent_config, get_vault_path
from shared.events import emit
from shared.log import kb_log
from shared.memory import remember
from shared.obsidian_writer import list_files, read_page


class FrankyAgent(BaseAgent):
    name = "franky"

    def __init__(self) -> None:
        super().__init__()
        self.config = get_agent_config("franky")
        self.vault = get_vault_path()
        self.reporter = ReportGenerator()

    def run(self) -> str:
        # 1. 讀取 dev-backlog.md
        backlog_raw = read_page("AgentReports/dev-backlog.md")
        if not backlog_raw:
            self.logger.warning("AgentReports/dev-backlog.md 不存在，以空白 backlog 繼續")
            kb_log(self.name, "warn", "dev-backlog.md 不存在，健康報告照跑")

        # 2. 讀取上週報告供對比
        last_report = self._load_last_report()

        # 3. 系統健康檢查
        health = SystemHealthChecker().check()
        if health.status != "ok":
            self.logger.warning(f"系統健康狀態：{health.status} — {health.notes}")

        # 4 & 5. 產出並寫入週報
        memory_ctx = self.get_memory_context()
        report = self.reporter.generate(backlog_raw, health, last_report, memory_context=memory_ctx)
        report_path = self.reporter.write(report)

        # 6. KB log
        kb_log(
            self.name,
            "report",
            f"Generated {report.period}: open={report.open_tasks}, "
            f"closed={report.closed_tasks}, blocked={report.blocked_count}, "
            f"health={report.health.status}",
        )

        # 7. emit 事件給 Nami
        emit("franky", "engineering_report_ready", {
            "period": report.period,
            "report_path": report_path,
            "open_tasks": report.open_tasks,
            "closed_tasks": report.closed_tasks,
            "blocked_count": report.blocked_count,
            "health_status": report.health.status,
        })

        summary = (
            f"Report {report.period}: "
            f"{report.open_tasks} open, {report.closed_tasks} closed, "
            f"{report.blocked_count} blocked, health={report.health.status}"
        )
        self.logger.info(summary)

        # 8. 記錄事件到 Tier 3 記憶
        remember(
            agent="franky",
            type="episodic",
            title=f"Weekly Report: {report.period}",
            content=(
                f"Period: {report.period} ({report.period_start})\n"
                f"Open: {report.open_tasks}, Closed: {report.closed_tasks}, "
                f"Blocked: {report.blocked_count}\n"
                f"Health: {report.health.status}\n"
                f"Report: {report_path}"
            ),
            tags=["weekly-report", report.period],
            confidence="high",
            source=report_path,
        )

        return summary

    def _load_last_report(self):
        """載入上一份週報（若存在），供 Claude 對比用。"""
        reports = list_files("AgentReports/franky", suffix=".md")
        if not reports:
            return None
        # list_files 回傳 sorted，最後一個是最新的
        last_path = reports[-1]
        # 讀取相對路徑
        relative = last_path.relative_to(self.vault).as_posix()
        return read_page(relative)
