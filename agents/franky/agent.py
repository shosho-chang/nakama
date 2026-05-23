"""Franky — 工程週報 Agent（船匠）。

每週一 01:00 執行：
1. 讀取 data/agent_reports/franky/dev-backlog.md（repo, ADR-028 §4）
2. 取得上週報告（供對比）
3. 系統健康檢查
4. 呼叫 Claude 產出週報
5. 跑 vault layout audit（ADR-028 §11 β），結果 append 到週報 body
6. 寫入 data/agent_reports/franky/weekly/YYYY-WW.md（repo）
7. 記錄到 KB/log.md
8. emit 事件給 Nami

ADR-028 §4 / Phase A PR-A2: outputs migrated from vault
``AgentReports/franky/`` + ``AgentReports/dev-backlog.md`` to repo
``data/agent_reports/franky/`` per E1 escalation decision.
"""

from pathlib import Path

from agents.base import BaseAgent
from agents.franky.reporter import ReportGenerator, SystemHealthChecker
from shared.config import get_agent_config, get_vault_path
from shared.events import emit
from shared.log import kb_log
from shared.memory import remember
from shared.repo_writer import list_repo_files, read_repo_page


class FrankyAgent(BaseAgent):
    name = "franky"

    def __init__(self) -> None:
        super().__init__()
        self.config = get_agent_config("franky")
        self.vault = get_vault_path()
        self.reporter = ReportGenerator()

    def run(self) -> str:
        # 1. 讀取 dev-backlog.md（repo, ADR-028 §4）
        backlog_raw = read_repo_page("data/agent_reports/franky/dev-backlog.md")
        if not backlog_raw:
            self.logger.warning(
                "data/agent_reports/franky/dev-backlog.md 不存在，以空白 backlog 繼續"
            )
            kb_log(self.name, "warn", "dev-backlog.md 不存在，健康報告照跑")

        # 2. 讀取上週報告供對比
        last_report = self._load_last_report()

        # 3. 系統健康檢查
        health = SystemHealthChecker().check()
        if health.status != "ok":
            self.logger.warning(f"系統健康狀態：{health.status} — {health.notes}")

        # 4. 產出週報
        memory_ctx = self.get_memory_context()
        report = self.reporter.generate(backlog_raw, health, last_report, memory_context=memory_ctx)

        # 5. Vault layout audit (ADR-028 §11 β) — append findings to body before write.
        audit_md = self._run_vault_audit()
        if audit_md:
            report.body_markdown = report.body_markdown.rstrip() + "\n\n" + audit_md

        # 6. 寫入週報
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
        emit(
            "franky",
            "engineering_report_ready",
            {
                "period": report.period,
                "report_path": report_path,
                "open_tasks": report.open_tasks,
                "closed_tasks": report.closed_tasks,
                "blocked_count": report.blocked_count,
                "health_status": report.health.status,
            },
        )

        summary = (
            f"Report {report.period}: "
            f"{report.open_tasks} open, {report.closed_tasks} closed, "
            f"{report.blocked_count} blocked, health={report.health.status}"
        )
        self.logger.info(summary)

        # 暫存報告資訊，供 record_episodic() 使用
        self._report_info = {
            "period": report.period,
            "period_start": report.period_start,
            "open_tasks": report.open_tasks,
            "closed_tasks": report.closed_tasks,
            "blocked_count": report.blocked_count,
            "health_status": report.health.status,
            "report_path": report_path,
        }

        return summary

    def record_episodic(self, summary: str) -> None:
        """Override: 記錄更豐富的週報 episodic 記憶。"""
        info = getattr(self, "_report_info", None)
        if not info:
            super().record_episodic(summary)
            return

        remember(
            agent="franky",
            type="episodic",
            title=f"Weekly Report: {info['period']}",
            content=(
                f"Period: {info['period']} ({info['period_start']})\n"
                f"Open: {info['open_tasks']}, Closed: {info['closed_tasks']}, "
                f"Blocked: {info['blocked_count']}\n"
                f"Health: {info['health_status']}\n"
                f"Report: {info['report_path']}"
            ),
            tags=["weekly-report", info["period"]],
            confidence="high",
            source=info["report_path"],
        )

    def _load_last_report(self):
        """載入上一份週報（若存在），供 Claude 對比用。

        ADR-028 §4: reports now live in repo (``data/agent_reports/franky/weekly/``)
        not vault (``AgentReports/franky/``)."""
        reports = list_repo_files("data/agent_reports/franky/weekly", suffix=".md")
        if not reports:
            return None
        last_path = reports[-1]
        return last_path.read_text(encoding="utf-8")

    def _run_vault_audit(self) -> str | None:
        """Run ``scripts.vault_layout_audit`` and return its markdown report.

        Returns ``None`` on any failure (e.g. layout doc missing in CI sandbox);
        weekly digest must not crash on audit issues. Errors-severity findings
        are logged as warnings so they surface in monitoring without blocking
        the report write.
        """
        try:
            from scripts.vault_layout_audit import run_audit

            repo_root = Path(__file__).resolve().parent.parent.parent
            layout_doc = repo_root / "docs" / "VAULT-LAYOUT.md"
            if not layout_doc.exists() or not self.vault.exists():
                self.logger.info("vault audit 跳過（layout doc 或 vault root 不存在）")
                return None
            report = run_audit(self.vault, repo_root, layout_doc)
            if report.has_errors:
                self.logger.warning(
                    "vault audit 偵測到 %d 個 error-severity finding",
                    report.error_count,
                )
                try:
                    kb_log(
                        self.name,
                        "warn",
                        f"vault audit: {report.error_count} error, {report.warn_count} warn",
                    )
                except Exception as e:  # noqa: BLE001 — kb_log failure must not eat audit output
                    self.logger.warning(f"kb_log failed (audit markdown still returned): {e}")
            return report.to_markdown()
        except Exception as e:  # noqa: BLE001 — audit must never block weekly digest
            self.logger.warning(f"vault audit 失敗，跳過：{e}", exc_info=True)
            return None
