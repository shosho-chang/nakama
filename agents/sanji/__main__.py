"""``python -m agents.sanji`` 入口——gamification 服務（systemd ``nakama-sanji``）。

子命令：
  loop        主輪詢迴圈（預設；分鐘級打卡回饋的常駐服務）
  reconcile   每日對帳一次性執行（排程 05:00 呼叫；也可手動補跑）
  health      打 plugin /health 一次（部署煙霧測試用）

2026-08-23 改寫：取代 2026-04-20 的 Grok 人格 smoke stub（該版驗證的 xAI 端到端
已完成階段性任務；Slack 互動屬 gateway 職責，不在本服務）。
"""

from __future__ import annotations

import argparse
import json
import sys

from shared.log import get_logger

logger = get_logger("nakama.sanji")


def _build(args: argparse.Namespace):
    """組出 (cfg, client, store)——放函式內延遲 import，讓 --help 不需要環境變數。"""
    from agents.sanji.settings import load
    from agents.sanji.store import Store
    from agents.sanji.wp_client import WPClient

    cfg = load()
    client = WPClient(cfg.wp_base_url, cfg.wp_user, cfg.wp_app_password)
    store = Store()
    return cfg, client, store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agents.sanji", description=__doc__)
    sub = parser.add_subparsers(dest="command")
    loop_p = sub.add_parser("loop", help="主輪詢迴圈（常駐）")
    loop_p.add_argument("--theme", default="", help="本季挑戰主題（判定 prompt 用）")
    sub.add_parser("reconcile", help="每日對帳一次性執行")
    sub.add_parser("health", help="打 plugin /health")

    args = parser.parse_args(argv)
    command = args.command or "loop"

    if command == "health":
        cfg, client, store = _build(args)
        try:
            print(json.dumps(client.health(), ensure_ascii=False, indent=2))
            return 0
        finally:
            client.close()
            store.close()

    if command == "reconcile":
        from agents.sanji import reconcile

        cfg, client, store = _build(args)
        try:
            summary = reconcile.run(cfg, client, store)
            print(json.dumps(summary, ensure_ascii=False, default=str))
            return 0
        finally:
            client.close()
            store.close()

    # loop（預設）
    from agents.sanji.loop import SanjiLoop

    cfg, client, store = _build(args)
    theme = getattr(args, "theme", "") or ""
    try:
        SanjiLoop(cfg, client, store, theme=theme).run_forever()
        return 0
    finally:
        client.close()
        store.close()


if __name__ == "__main__":
    sys.exit(main())
