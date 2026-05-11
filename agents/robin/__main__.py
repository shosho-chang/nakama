"""python -m agents.robin 的入口。"""

import argparse

# Windows cp1252 stdout 無法印中文 — 統一 UTF-8（log 檔也會用到）。
# Helper handles idempotency + missing reconfigure() (wrapped streams).
from shared.log import force_utf8_console

force_utf8_console()

from agents.robin.agent import RobinAgent  # noqa: E402
from agents.robin.pubmed_digest import PubMedDigestPipeline  # noqa: E402
from shared.heartbeat import record_failure, record_success  # noqa: E402

# Phase 5B-2 — heartbeat key consumed by probe_cron_freshness via CRON_SCHEDULES.
# Stable across releases (changing breaks the probe's prior-state continuity).
# Only the pubmed_digest mode is instrumented; --mode ingest is a manual file watcher
# whose absence loses no work (operator just hasn't dropped files in inbox).
_JOB_NAME_PUBMED = "robin-pubmed-digest"


def _run_pubmed_digest(*, dry_run: bool) -> None:
    agent = PubMedDigestPipeline(dry_run=dry_run)
    if dry_run:
        # dry-run is manual / ad-hoc — recording its outcomes would corrupt the
        # cron staleness signal. Only the production path emits heartbeats.
        agent.execute()
        return
    try:
        agent.execute()
    except Exception as exc:
        record_failure(_JOB_NAME_PUBMED, f"{type(exc).__name__}: {exc}"[:200])
        raise
    record_success(_JOB_NAME_PUBMED)


def main() -> None:
    parser = argparse.ArgumentParser(description="Robin — Knowledge Base Agent")
    parser.add_argument(
        "--mode",
        choices=["ingest", "pubmed_digest"],
        default="ingest",
        help="執行模式：ingest = 既有 KB 檔案 ingest（預設）；pubmed_digest = 每日 PubMed 精選",
    )
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="互動式模式（僅 ingest mode 適用）：每份檔案 ingest 後暫停",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="pubmed_digest mode：跑完 fetch + curate + score 但不寫 vault、不標 seen",
    )
    args = parser.parse_args()

    if args.mode == "pubmed_digest":
        _run_pubmed_digest(dry_run=args.dry_run)
    else:
        RobinAgent(interactive=args.interactive).execute()


if __name__ == "__main__":
    main()
