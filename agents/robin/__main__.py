"""python -m agents.robin 的入口。"""

from agents.robin.agent import RobinAgent


def main() -> None:
    agent = RobinAgent()
    agent.execute()


if __name__ == "__main__":
    main()
