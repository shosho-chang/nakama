"""python -m agents.nami 的入口（stub）。

Nami 真實邏輯在 ``gateway/handlers/nami.py``，由 nakama-gateway Slack 服務
觸發（不是 cron-style agent）。這個 ``__main__`` 保留作 Morning Brief 未來
實作的 hook — 真做 morning brief 時把 ``run()`` 實作起來，並 unblock
``cron.conf`` 內 disabled 的 07:00 entry。
"""

from agents.base import BaseAgent


class NamiAgent(BaseAgent):
    name = "nami"

    def run(self) -> str:
        raise NotImplementedError("Morning Brief 未實作。Nami DM 走 gateway/handlers/nami.py。")


if __name__ == "__main__":
    NamiAgent().execute()
