"""Daily task ↔ Google Calendar drift sweep (N543).

Runs :func:`shared.calendar_reconcile.sweep`: auto-links the unambiguous
``unlinked`` tasks (event exists, task lost the link) and Slacks 修修 a report when
there is anything to auto-fix or confirm. The safety net for the recurring class of
bug where a Nami calendar op leaves the task unlinked and nobody notices.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from shared.calendar_reconcile import sweep
from shared.config import get_vault_path
from shared.log import get_logger

logger = get_logger("nakama.franky.calendar_reconcile")


@dataclass
class ReconcileResult:
    status: str  # ok | repaired | drift
    repaired: int
    drifts: int
    detail: str

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "repaired": self.repaired,
            "drifts": self.drifts,
            "detail": self.detail,
        }


def run_once(
    *,
    dry_run: bool = False,
    slack_bot: Optional[Any] = None,
    vault_root: Optional[Path] = None,
) -> ReconcileResult:
    """One sweep. ``dry_run`` audits only (no writes, no Slack). Otherwise auto-links the
    safe cases and posts a Slack report iff there is drift or a repair to announce."""
    vault = vault_root or get_vault_path()
    report = sweep(vault, repair=not dry_run)

    if report.repaired:
        status = "repaired"
    elif report.drifts:
        status = "drift"
    else:
        status = "ok"
    detail = f"repaired={len(report.repaired)} drifts={len(report.drifts)}"

    text = report.slack_text()
    if text and slack_bot is not None and not dry_run:
        try:
            slack_bot.post_plain(text, context="calendar_reconcile")
        except Exception as exc:  # noqa: BLE001 — a Slack hiccup mustn't fail the cron
            logger.warning("calendar_reconcile Slack post failed: %s", exc)

    logger.info("calendar_reconcile %s (%s)", status, detail)
    return ReconcileResult(status, len(report.repaired), len(report.drifts), detail)
