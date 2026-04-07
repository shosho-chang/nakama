"""python -m agents.nami 的入口。"""

# Nami — Secretary（航海士）
# TODO: 整合各 agent 當日產出，產出 Morning Brief

from agents.base import BaseAgent


class NamiAgent(BaseAgent):
    name = "nami"

    def run(self) -> str:
        raise NotImplementedError("Nami agent 尚未實作")


if __name__ == "__main__":
    NamiAgent().execute()
