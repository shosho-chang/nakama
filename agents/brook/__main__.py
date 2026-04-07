"""python -m agents.brook 的入口。"""

# Brook — Publish（音樂家）
# TODO: 發布 WordPress、上傳 YouTube

from agents.base import BaseAgent


class BrookAgent(BaseAgent):
    name = "brook"

    def run(self) -> str:
        raise NotImplementedError("Brook agent 尚未實作")


if __name__ == "__main__":
    BrookAgent().execute()
