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
# N529 — 每日回顧 5am cron 的 heartbeat key（probe_cron_freshness 經 CRON_SCHEDULES 消費）。
_JOB_NAME_DAILY_REVIEW = "robin-daily-review"


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


def _run_daily_review(*, weekly: bool) -> None:
    """N529 — 跑每日回顧並持久化 bundle，供 weekly dashboard 起床即見。"""
    from agents.robin.daily_review import run_daily_review, save_review_bundle
    from shared.config import get_vault_path

    try:
        bundle = run_daily_review(weekly=weekly, notify=True)
        save_review_bundle(get_vault_path(), bundle)
    except Exception as exc:
        record_failure(_JOB_NAME_DAILY_REVIEW, f"{type(exc).__name__}: {exc}"[:200])
        raise
    record_success(_JOB_NAME_DAILY_REVIEW)


def _run_book_ingest() -> None:
    """Route B（Slice 4C）：排清書本 ingest 佇列（Reader「Ingest 整本書」入列的書）。"""
    from agents.robin.book_ingest import drain

    n = drain()
    print(f"book ingest: 處理了 {n} 本書")


def main() -> None:
    parser = argparse.ArgumentParser(description="Robin — Knowledge Base Agent")
    parser.add_argument(
        "--mode",
        choices=["ingest", "pubmed_digest", "daily_review", "book_ingest"],
        default="ingest",
        help=(
            "執行模式：ingest = 既有 KB 檔案 ingest（預設）；"
            "pubmed_digest = 每日 PubMed 精選；"
            "daily_review = Centaur 每日回顧（5am cron，產候選卡 + 持久化給 dashboard）；"
            "book_ingest = 排清書本 ingest 佇列（route B）"
        ),
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
    parser.add_argument(
        "--weekly",
        action="store_true",
        help="daily_review：強制每週清掃；平日 cron 免帶，週一自動跑",
    )
    args = parser.parse_args()

    if args.mode == "pubmed_digest":
        _run_pubmed_digest(dry_run=args.dry_run)
    elif args.mode == "daily_review":
        # 週一自動帶每週清掃（台北日曆），cron 因此只需單行每天跑；--weekly 可強制覆寫。
        from agents.robin.daily_review import _local_today, is_weekly_sweep_day

        weekly = args.weekly or is_weekly_sweep_day(_local_today())
        _run_daily_review(weekly=weekly)
    elif args.mode == "book_ingest":
        _run_book_ingest()
    else:
        RobinAgent(interactive=args.interactive).execute()


if __name__ == "__main__":
    main()
