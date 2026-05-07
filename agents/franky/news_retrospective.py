"""Franky monthly retrospective (ADR-023 §1 Monthly + §7 S4).

每月最後一個週日 22:00 台北 cron 取代 weekly synthesis 同一 cron slot：
  Input:  上月 proposal_metrics 所有 rows
          + api_calls telemetry（quantitative metric_type 用）
  LLM:    三類 metric_type 分析 + ship rate/wontfix rate + 哲學調整建議
  Output: KB/Wiki/Digests/AI/Retrospective-YYYY-MM.md + Slack DM

metric_type 分流：
  quantitative  → 從 api_calls 取 baseline vs post-ship 數字比對
  checklist     → 列 ✓/✗ 統計
  human_judged  → 報 verification_owner verdict，不 fake quantitative

Hook 進 proposal_metrics (ADR-023 §7 S4)：
  shipped rows  → mark_verified(post_ship_value) — quantitative 自動算，其他記錄狀態
  wontfix rows  → 已是 terminal state，retrospective 只記錄不做 transition

Subcommand modes（agents/franky/__main__.py 端 dispatch）：
  python -m agents.franky retrospective              # full path
  python -m agents.franky retrospective --dry-run    # 不寫 vault、不插 DB、不送 Slack
  python -m agents.franky retrospective --no-publish # 寫 vault 但不送 Slack DM
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from agents.base import BaseAgent
from agents.franky.state.proposal_metrics import (
    list_for_month,
    mark_verified,
)
from shared import llm
from shared.obsidian_writer import append_to_file, write_page
from shared.prompt_loader import load_prompt
from shared.state import _get_conn

_TAIPEI = ZoneInfo("Asia/Taipei")
_RETRO_VAULT_DIR = "KB/Wiki/Digests/AI"

# ---------------------------------------------------------------------------
# "Last Sunday of month" helper (ADR-023 §7 S4 + cron_dispatcher)
# ---------------------------------------------------------------------------


def is_last_sunday_of_month(dt: datetime) -> bool:
    """Return True if `dt` is the last Sunday of its calendar month.

    Works correctly across month/year boundaries (December → January).
    Python weekday(): Monday=0, Sunday=6.
    """
    if dt.weekday() != 6:
        return False
    next_week = dt + timedelta(days=7)
    return next_week.month != dt.month


# ---------------------------------------------------------------------------
# Month helpers
# ---------------------------------------------------------------------------


def _get_last_month(now: datetime) -> tuple[int, int]:
    """Return (year, month) for the month preceding `now`."""
    first_of_current = now.replace(day=1)
    last_month_dt = first_of_current - timedelta(days=1)
    return last_month_dt.year, last_month_dt.month


# ---------------------------------------------------------------------------
# Quantitative metric: api_calls telemetry
# ---------------------------------------------------------------------------

_AGENT_NAME_RE = re.compile(r"agent\s*[=:]\s*['\"]?(\w+)['\"]?", re.IGNORECASE)


def _extract_agent_name(baseline_source: str | None) -> str | None:
    """Parse agent name from a baseline_source expression.

    Handles patterns like:
      "api_calls where agent='robin'"
      "shared.pricing.calc_cost over api_calls.where(agent='robin')"
      "agent=robin"
    """
    if not baseline_source:
        return None
    m = _AGENT_NAME_RE.search(baseline_source)
    return m.group(1) if m else None


def _fetch_api_calls_total(agent: str, since: str | None) -> int | None:
    """Sum input + output tokens for `agent` since `since` (ISO 8601 string).

    Returns total token count, or None if no rows found.
    """
    conn = _get_conn()
    if since:
        row = conn.execute(
            "SELECT SUM(input_tokens + output_tokens)"
            " FROM api_calls WHERE agent = ? AND called_at >= ?",
            (agent, since),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT SUM(input_tokens + output_tokens) FROM api_calls WHERE agent = ?",
            (agent,),
        ).fetchone()
    return row[0] if row and row[0] is not None else None


def _compute_quantitative_post_ship(proposal: dict[str, Any]) -> str | None:
    """Attempt to compute post-ship token telemetry for a quantitative proposal.

    Returns a human-readable summary string if data is available, else None.
    The caller is responsible for deciding whether to call mark_verified.
    """
    agent = _extract_agent_name(proposal.get("baseline_source"))
    if not agent:
        return None
    shipped_at = proposal.get("shipped_at")
    total = _fetch_api_calls_total(agent, shipped_at)
    if total is None:
        return None
    return f"api_calls sum(input+output_tokens) for agent={agent} since shipped_at: {total:,}"


# ---------------------------------------------------------------------------
# Proposal grouping for LLM payload
# ---------------------------------------------------------------------------


def _group_proposals_by_type(
    proposals: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group proposal rows by metric_type."""
    groups: dict[str, list[dict[str, Any]]] = {
        "quantitative": [],
        "checklist": [],
        "human_judged": [],
    }
    for p in proposals:
        mt = p.get("metric_type", "checklist")
        groups.setdefault(mt, []).append(p)
    return groups


def _format_proposal_block(p: dict[str, Any], *, post_ship: str | None = None) -> str:
    """Format a single proposal row as a readable block for the LLM prompt."""
    lines = [
        f"- **{p['proposal_id']}** (status={p['status']})",
        f"  success_metric: {p.get('success_metric', '')}",
        f"  baseline_value: {p.get('baseline_value') or '—'}",
        f"  post_ship_value: {p.get('post_ship_value') or post_ship or '—'}",
    ]
    if p.get("shipped_at"):
        lines.append(f"  shipped_at: {p['shipped_at']}")
    if p.get("verification_owner"):
        lines.append(f"  verification_owner: {p['verification_owner']}")
    return "\n".join(lines)


def _build_proposals_payload(
    proposals: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build structured payload for the retrospective LLM prompt.

    For quantitative shipped proposals, pre-fetches api_calls data so the LLM
    gets concrete numbers instead of having to hallucinate them.
    For human_judged proposals, explicitly marks that no quantitative value exists.
    """
    groups = _group_proposals_by_type(proposals)

    # Pre-compute post_ship for quantitative shipped rows
    quantitative_sections: list[str] = []
    quantitative_post_ships: dict[str, str | None] = {}
    for p in groups.get("quantitative", []):
        ps = None
        if p.get("status") == "shipped":
            ps = _compute_quantitative_post_ship(p)
        quantitative_post_ships[p["proposal_id"]] = ps
        quantitative_sections.append(_format_proposal_block(p, post_ship=ps))

    checklist_sections = [_format_proposal_block(p) for p in groups.get("checklist", [])]
    human_judged_sections = [_format_proposal_block(p) for p in groups.get("human_judged", [])]

    total = len(proposals)
    shipped_count = sum(1 for p in proposals if p.get("status") == "shipped")
    wontfix_count = sum(1 for p in proposals if p.get("status") == "wontfix")
    verified_count = sum(1 for p in proposals if p.get("status") == "verified")

    return {
        "total": total,
        "shipped_count": shipped_count,
        "wontfix_count": wontfix_count,
        "verified_count": verified_count,
        "ship_rate": round(shipped_count / total, 2) if total else 0.0,
        "wontfix_rate": round(wontfix_count / total, 2) if total else 0.0,
        "quantitative_text": "\n".join(quantitative_sections) or "（無）",
        "checklist_text": "\n".join(checklist_sections) or "（無）",
        "human_judged_text": "\n".join(human_judged_sections) or "（無）",
        "quantitative_post_ships": quantitative_post_ships,
    }


# ---------------------------------------------------------------------------
# Vault page renderer
# ---------------------------------------------------------------------------


def _render_retro_page(
    *,
    month_label: str,
    payload: dict[str, Any],
    llm_output: str,
    op_id: str,
) -> tuple[dict, str]:
    """Return (frontmatter_dict, body_markdown) for the monthly vault page."""
    frontmatter: dict[str, Any] = {
        "month": month_label,
        "created_by": "franky",
        "operation_id": op_id,
        "total_proposals": payload["total"],
        "shipped_count": payload["shipped_count"],
        "wontfix_count": payload["wontfix_count"],
        "verified_count": payload["verified_count"],
        "type": "monthly_retrospective",
    }
    body = "\n".join(
        [
            f"# Franky Monthly Retrospective — {month_label}",
            "",
            f"上月 proposal 總數：{payload['total']} /"
            f" shipped：{payload['shipped_count']} /"
            f" wontfix：{payload['wontfix_count']} /"
            f" verified：{payload['verified_count']}",
            "",
            "---",
            "",
            llm_output,
            "",
            "---",
            "",
            f"*Generated by Franky retrospective op={op_id}*",
        ]
    )
    return frontmatter, body


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


class NewsRetrospectivePipeline(BaseAgent):
    """Franky monthly retrospective 子流程（ADR-023 §7 S4）。"""

    name = "franky"

    def __init__(
        self,
        *,
        dry_run: bool = False,
        no_publish: bool = False,
        slack_bot: Any | None = None,
        now: datetime | None = None,
    ) -> None:
        super().__init__()
        self.dry_run = dry_run
        self.no_publish = no_publish
        self._slack_bot_override = slack_bot
        self._now = now
        self.operation_id = f"op_{uuid.uuid4().hex[:8]}"

    def run(self) -> str:
        now = self._now or datetime.now(tz=_TAIPEI)
        year, month = _get_last_month(now)
        month_label = f"{year:04d}-{month:02d}"

        # 1. Load last month's proposals
        proposals = list_for_month(year, month)
        if not proposals:
            return f"上月（{month_label}）無 proposal，略過 retrospective op={self.operation_id}"

        # 2. Build payload (pre-fetches quantitative api_calls data)
        payload = _build_proposals_payload(proposals)

        # 3. LLM retrospective analysis
        try:
            llm_output = self._run_retrospective_llm(payload, month_label)
        except Exception as e:
            self.logger.error(f"retrospective LLM 失敗：{e}", exc_info=True)
            return f"retrospective LLM 失敗：{e}"

        vault_page_path = ""
        if not self.dry_run:
            # 4. Write vault page
            vault_relpath = f"{_RETRO_VAULT_DIR}/Retrospective-{month_label}.md"
            vault_page_path = vault_relpath
            frontmatter, body = _render_retro_page(
                month_label=month_label,
                payload=payload,
                llm_output=llm_output,
                op_id=self.operation_id,
            )
            write_page(vault_relpath, frontmatter, body)
            self._append_kb_log(vault_relpath, payload["total"])

            # 5. Hook into proposal_metrics: mark shipped → verified
            self._mark_shipped_verified(proposals, payload)

        # 6. Slack DM
        slack_ts: str | None = None
        if not self.dry_run and not self.no_publish:
            slack_ts = self._send_slack_summary(
                month_label=month_label,
                payload=payload,
                vault_relpath=vault_page_path,
            )

        return (
            f"month={month_label} proposals={payload['total']}"
            f" shipped={payload['shipped_count']} wontfix={payload['wontfix_count']}"
            f" verified_now={payload['verified_count']}"
            f" vault={vault_page_path or '(skipped)'}"
            f" slack_ts={slack_ts or '(skipped)'} op={self.operation_id}"
            f" (dry_run={self.dry_run} no_publish={self.no_publish})"
        )

    # ------------------------------------------------------------------
    # LLM
    # ------------------------------------------------------------------

    def _run_retrospective_llm(self, payload: dict[str, Any], month_label: str) -> str:
        """One LLM call → retrospective narrative (markdown)."""
        prompt = load_prompt(
            "franky",
            "news_retrospective",
            month_label=month_label,
            total=str(payload["total"]),
            ship_rate=str(payload["ship_rate"]),
            wontfix_rate=str(payload["wontfix_rate"]),
            quantitative_proposals=payload["quantitative_text"],
            checklist_proposals=payload["checklist_text"],
            human_judged_proposals=payload["human_judged_text"],
        )
        raw = llm.ask(prompt, max_tokens=4096)
        self.logger.info(f"retrospective LLM returned {len(raw)} chars (month={month_label})")
        return raw.strip()

    # ------------------------------------------------------------------
    # Hook: mark shipped → verified
    # ------------------------------------------------------------------

    def _mark_shipped_verified(
        self,
        proposals: list[dict[str, Any]],
        payload: dict[str, Any],
    ) -> None:
        """For shipped proposals: call mark_verified with available post-ship data.

        quantitative: uses pre-computed api_calls value if available.
        checklist / human_judged: records "retrospective: shipped, outcome logged".
        wontfix rows are terminal — no transition attempted.
        """
        post_ships = payload.get("quantitative_post_ships", {})
        for p in proposals:
            if p.get("status") != "shipped":
                continue
            pid = p["proposal_id"]
            mt = p.get("metric_type", "checklist")
            if mt == "quantitative":
                val = post_ships.get(pid)
                post_ship_value = (
                    val if val else "retrospective: shipped; api_calls data unavailable"
                )
            elif mt == "human_judged":
                # Do NOT fake a quantitative value for human_judged proposals.
                # Record that the outcome requires owner verification.
                owner = p.get("verification_owner") or "shosho"
                post_ship_value = f"retrospective: shipped; outcome requires {owner} verification"
            else:
                post_ship_value = "retrospective: shipped; checklist outcome logged"

            try:
                mark_verified(pid, post_ship_value=post_ship_value)
                self.logger.info(f"mark_verified: {pid} ({mt})")
            except Exception as e:
                self.logger.warning(f"mark_verified failed for {pid}: {e}")

    # ------------------------------------------------------------------
    # Vault log
    # ------------------------------------------------------------------

    def _append_kb_log(self, relpath: str, count: int) -> None:
        try:
            from datetime import date

            today = date.today().isoformat()
            append_to_file(
                "KB/log.md",
                f"- {today}: Franky monthly retrospective → [{relpath}]({relpath})"
                f" ({count} proposals)\n",
            )
        except Exception as e:
            self.logger.debug(f"KB/log.md append failed: {e}")

    # ------------------------------------------------------------------
    # Slack DM
    # ------------------------------------------------------------------

    def _send_slack_summary(
        self,
        *,
        month_label: str,
        payload: dict[str, Any],
        vault_relpath: str,
    ) -> str | None:
        bot = self._slack_bot_override
        if bot is None:
            from agents.franky.slack_bot import FrankySlackBot

            bot = FrankySlackBot.from_env()

        ship_rate_pct = int(payload["ship_rate"] * 100)
        wontfix_rate_pct = int(payload["wontfix_rate"] * 100)

        # Metric_type distribution
        total = payload["total"]
        q_count = sum(
            1 for line in payload["quantitative_text"].splitlines() if line.startswith("- **")
        )
        c_count = sum(
            1 for line in payload["checklist_text"].splitlines() if line.startswith("- **")
        )
        h_count = sum(
            1 for line in payload["human_judged_text"].splitlines() if line.startswith("- **")
        )

        text = "\n".join(
            [
                f"Franky Monthly Retrospective — {month_label}",
                f"proposals={total} shipped={payload['shipped_count']}"
                f" wontfix={payload['wontfix_count']} verified={payload['verified_count']}",
                f"ship_rate={ship_rate_pct}% wontfix_rate={wontfix_rate_pct}%",
                f"metric_type distribution: quantitative={q_count}"
                f" checklist={c_count} human_judged={h_count}",
                f"vault: {vault_relpath}",
                f"op={self.operation_id}",
            ]
        )
        return bot.post_plain(text, context="news_retrospective")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_retrospective(*, dry_run: bool = False, no_publish: bool = False) -> str:
    """Run monthly retrospective. Returns a summary string."""
    pipeline = NewsRetrospectivePipeline(dry_run=dry_run, no_publish=no_publish)
    if dry_run:
        return pipeline.run()
    pipeline.execute()
    return f"op={pipeline.operation_id}"
