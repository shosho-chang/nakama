"""python -m agents.sanji 的入口。"""

# Sanji — Producer（廚師）
# TODO: 選題、標題建議、內容大綱

from agents.base import BaseAgent


class SanjiAgent(BaseAgent):
    name = "sanji"

    def run(self) -> str:
        raise NotImplementedError("Sanji agent 尚未實作")


if __name__ == "__main__":
    SanjiAgent().execute()
