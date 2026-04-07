"""python -m agents.zoro 的入口。"""

# Zoro — Scout（劍士）
# TODO: 追蹤 KOL、PubMed RSS、Google Trends

from agents.base import BaseAgent


class ZoroAgent(BaseAgent):
    name = "zoro"

    def run(self) -> str:
        raise NotImplementedError("Zoro agent 尚未實作")


if __name__ == "__main__":
    ZoroAgent().execute()
