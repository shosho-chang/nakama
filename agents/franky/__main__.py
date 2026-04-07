"""python -m agents.franky 的入口。"""

# Franky — Repurpose（船匠）
# TODO: 改寫文章、SEO、社群貼文、IG Carousel

from agents.base import BaseAgent


class FrankyAgent(BaseAgent):
    name = "franky"

    def run(self) -> str:
        raise NotImplementedError("Franky agent 尚未實作")


if __name__ == "__main__":
    FrankyAgent().execute()
