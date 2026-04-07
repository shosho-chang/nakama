"""python -m agents.usopp 的入口。"""

# Usopp — Community Monitor（狙擊手）
# TODO: 監控 WordPress Fluent Community

from agents.base import BaseAgent


class UsoppAgent(BaseAgent):
    name = "usopp"

    def run(self) -> str:
        raise NotImplementedError("Usopp agent 尚未實作")


if __name__ == "__main__":
    UsoppAgent().execute()
