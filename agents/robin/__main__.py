"""python -m agents.robin 的入口。"""

import argparse

from agents.robin.agent import RobinAgent


def main() -> None:
    parser = argparse.ArgumentParser(description="Robin — Knowledge Base Agent")
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="互動式模式：每份檔案 ingest 後暫停，等待使用者確認或引導方向",
    )
    args = parser.parse_args()

    agent = RobinAgent(interactive=args.interactive)
    agent.execute()


if __name__ == "__main__":
    main()
