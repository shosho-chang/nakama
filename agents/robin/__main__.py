"""python -m agents.robin 的入口。"""

import argparse
import sys

# Windows cp1252 stdout 無法印中文 — 統一 UTF-8（log 檔也會用到）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from agents.robin.agent import RobinAgent
from agents.robin.pubmed_digest import PubMedDigestPipeline


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
        agent = PubMedDigestPipeline(dry_run=args.dry_run)
    else:
        agent = RobinAgent(interactive=args.interactive)
    agent.execute()


if __name__ == "__main__":
    main()
