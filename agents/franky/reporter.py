"""Franky — 工程週報產生器。

包含：
- SystemHealthChecker：讀取 VPS 磁碟/記憶體狀態
- _parse_backlog：解析 dev-backlog.md 的 checkbox 統計
- ReportGenerator：呼叫 Claude 產出週報，並寫入 vault
"""

import re
import shutil
from dataclasses import dataclass, field
from datetime import date, timedelta

import yaml

from shared.anthropic_client import ask_claude
from shared.log import get_logger
from shared.obsidian_writer import list_files, read_page, write_page
from shared.prompt_loader import load_prompt

logger = get_logger("nakama.franky")


# ─── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class HealthSnapshot:
    disk_pct: int
    memory_pct: int
    status: str          # "ok" | "warn" | "error"
    notes: list[str] = field(default_factory=list)


@dataclass
class SectionStats:
    open_count: int
    closed_count: int
    blockers: list[str] = field(default_factory=list)


@dataclass
class BacklogStats:
    sections: dict[str, SectionStats]
    total_open: int
    total_closed: int
    blockers: list[str]


@dataclass
class WeeklyReport:
    period: str           # "2026-W15"
    period_start: str     # "2026-04-07"
    generated_at: str     # "2026-04-13"
    open_tasks: int
    closed_tasks: int
    blocked_count: int
    health: HealthSnapshot
    nakama_agents_done: int
    nakama_agents_pending: int
    top_blockers: list[str]
    body_markdown: str


# ─── System Health ─────────────────────────────────────────────────────────────


class SystemHealthChecker:
    """VPS 系統健康快照。"""

    WARN_DISK = 80
    ERROR_DISK = 90
    WARN_MEM = 85
    ERROR_MEM = 95

    def check(self) -> HealthSnapshot:
        disk_pct = self._disk_pct()
        memory_pct = self._memory_pct()
        notes: list[str] = []

        # 判定健康狀態
        if disk_pct >= self.ERROR_DISK or memory_pct >= self.ERROR_MEM:
            status = "error"
            if disk_pct >= self.ERROR_DISK:
                notes.append(f"⚠️ 磁碟使用率危險：{disk_pct}%（閾值 {self.ERROR_DISK}%）")
            if memory_pct >= self.ERROR_MEM:
                notes.append(f"⚠️ 記憶體使用率危險：{memory_pct}%（閾值 {self.ERROR_MEM}%）")
        elif disk_pct >= self.WARN_DISK or memory_pct >= self.WARN_MEM:
            status = "warn"
            if disk_pct >= self.WARN_DISK:
                notes.append(f"注意：磁碟使用率偏高 {disk_pct}%（閾值 {self.WARN_DISK}%）")
            if memory_pct >= self.WARN_MEM:
                notes.append(f"注意：記憶體使用率偏高 {memory_pct}%（閾值 {self.WARN_MEM}%）")
        else:
            status = "ok"

        return HealthSnapshot(
            disk_pct=disk_pct,
            memory_pct=memory_pct,
            status=status,
            notes=notes,
        )

    def _disk_pct(self) -> int:
        try:
            import psutil
            usage = psutil.disk_usage("/")
            return int(usage.percent)
        except ImportError:
            usage = shutil.disk_usage("/")
            return int(usage.used / usage.total * 100)
        except Exception as e:
            logger.warning(f"無法取得磁碟使用率：{e}")
            return 0

    def _memory_pct(self) -> int:
        try:
            import psutil
            return int(psutil.virtual_memory().percent)
        except ImportError:
            logger.warning("psutil 未安裝，略過記憶體檢查")
            return 0
        except Exception as e:
            logger.warning(f"無法取得記憶體使用率：{e}")
            return 0


# ─── Backlog Parser ────────────────────────────────────────────────────────────


def _parse_backlog(raw: str) -> BacklogStats:
    """解析 dev-backlog.md 的 checkbox 統計。

    純字串處理，不呼叫 Claude。
    依 '## 標題' 分段，計算各段的 open/closed 數量，提取 blocked: 項目。
    """
    sections: dict[str, SectionStats] = {}
    all_blockers: list[str] = []

    # 移除 frontmatter
    body = re.sub(r"^---[\s\S]*?---\s*", "", raw, count=1)

    # 以 ## 標題分段
    parts = re.split(r"\n## ", body)
    for part in parts:
        if not part.strip():
            continue
        lines = part.strip().splitlines()
        heading = lines[0].strip()
        content = "\n".join(lines[1:])

        open_items = re.findall(r"^- \[ \]", content, re.MULTILINE)
        closed_items = re.findall(r"^- \[x\]", content, re.MULTILINE | re.IGNORECASE)

        # blocked: 項目（大小寫不敏感）
        blockers = [
            line.strip()
            for line in content.splitlines()
            if re.search(r"blocked:", line, re.IGNORECASE)
        ]
        all_blockers.extend(blockers)

        sections[heading] = SectionStats(
            open_count=len(open_items),
            closed_count=len(closed_items),
            blockers=blockers,
        )

    total_open = sum(s.open_count for s in sections.values())
    total_closed = sum(s.closed_count for s in sections.values())

    return BacklogStats(
        sections=sections,
        total_open=total_open,
        total_closed=total_closed,
        blockers=all_blockers,
    )


def _count_nakama_agents(sections: dict[str, SectionStats]) -> tuple[int, int]:
    """從 Nakama Agents 章節取得已完成/待完成數量。"""
    for key, stats in sections.items():
        if "Nakama" in key:
            return stats.closed_count, stats.open_count
    return 0, 0


def _iso_week_start(today: date) -> str:
    """取得 today 所在 ISO 週的週一日期（yyyy-MM-dd）。"""
    monday = today - timedelta(days=today.weekday())
    return monday.strftime("%Y-%m-%d")


def _current_period(today: date) -> str:
    """回傳 ISO 週期字串，如 '2026-W15'。"""
    year, week, _ = today.isocalendar()
    return f"{year}-W{week:02d}"


# ─── Report Generator ──────────────────────────────────────────────────────────


class ReportGenerator:
    """讀取 backlog + 健康資料，呼叫 Claude 產出週報，並寫入 vault。"""

    def generate(
        self,
        backlog_raw: str | None,
        health: HealthSnapshot,
        last_report: str | None,
        memory_context: str = "",
    ) -> WeeklyReport:
        today = date.today()
        period = _current_period(today)
        period_start = _iso_week_start(today)
        generated_at = today.strftime("%Y-%m-%d")

        # 解析 backlog
        if backlog_raw:
            stats = _parse_backlog(backlog_raw)
        else:
            stats = BacklogStats(sections={}, total_open=0, total_closed=0, blockers=[])
            backlog_raw = "（dev-backlog.md 不存在，無法取得任務清單）"

        nakama_done, nakama_pending = _count_nakama_agents(stats.sections)

        # 上週報告摘要（供 Claude 對比）
        last_summary = self._extract_last_summary(last_report)

        # 系統健康 notes 格式化
        health_notes_str = "\n".join(f"- {n}" for n in health.notes) if health.notes else "- 無異常"

        # top_blockers：取前 3 條，去除 markdown 標記
        raw_blockers = stats.blockers[:3]
        top_blockers = [
            re.sub(r"^- \[[ x]\]\s*blocked:\s*", "", b, flags=re.IGNORECASE).strip()
            for b in raw_blockers
        ]

        # 呼叫 Claude 產出報告內文
        prompt = load_prompt(
            "franky",
            "weekly_report",
            period=period,
            period_start=period_start,
            backlog_raw=backlog_raw,
            last_report_summary=last_summary,
            disk_pct=str(health.disk_pct),
            memory_pct=str(health.memory_pct),
            health_status=health.status,
            health_notes=health_notes_str,
            open_tasks=str(stats.total_open),
            closed_tasks=str(stats.total_closed),
            blocked_count=str(len(stats.blockers)),
        )

        body = ask_claude(prompt, system=memory_context, max_tokens=2048)

        return WeeklyReport(
            period=period,
            period_start=period_start,
            generated_at=generated_at,
            open_tasks=stats.total_open,
            closed_tasks=stats.total_closed,
            blocked_count=len(stats.blockers),
            health=health,
            nakama_agents_done=nakama_done,
            nakama_agents_pending=nakama_pending,
            top_blockers=top_blockers,
            body_markdown=body,
        )

    def write(self, report: WeeklyReport) -> str:
        """將週報寫入 AgentReports/franky/{period}.md，回傳相對路徑。"""
        relative_path = f"AgentReports/franky/{report.period}.md"

        frontmatter: dict = {
            "type": "franky-report",
            "agent": "franky",
            "period": report.period,
            "period_start": report.period_start,
            "generated_at": report.generated_at,
            "open_tasks": report.open_tasks,
            "closed_tasks": report.closed_tasks,
            "blocked_count": report.blocked_count,
            "health_status": report.health.status,
            "system_disk_pct": report.health.disk_pct,
            "system_memory_pct": report.health.memory_pct,
            "nakama_agents_done": report.nakama_agents_done,
            "nakama_agents_pending": report.nakama_agents_pending,
            "top_blockers": report.top_blockers,
        }

        write_page(relative_path, frontmatter, report.body_markdown)
        logger.info(f"週報已寫入：{relative_path}")
        return relative_path

    def _extract_last_summary(self, last_report: str | None) -> str:
        """從上週報告中提取 frontmatter 統計，供 Claude 對比用。"""
        if not last_report:
            return "（無上週報告）"

        try:
            match = re.match(r"^---\n([\s\S]*?)\n---", last_report)
            if not match:
                return "（無法解析上週報告）"
            fm = yaml.safe_load(match.group(1))
            return (
                f"上週（{fm.get('period', '?')}）："
                f"open={fm.get('open_tasks', '?')}, "
                f"closed={fm.get('closed_tasks', '?')}, "
                f"blocked={fm.get('blocked_count', '?')}, "
                f"health={fm.get('health_status', '?')}"
            )
        except Exception:
            return "（無法解析上週報告）"
