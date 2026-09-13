---
name: feedback_legacy_route_is_stop_the_line
description: subagent 或 code 回報「legacy route」時立刻停下來查是不是走錯線；活的流程要求死掉的前置條件，先懷疑路線不要先補條件
metadata:
  type: feedback
---

**「legacy route」「deprecated」「舊版契約」從 code 或 subagent 口中出現時，那是 stop-the-line
訊號，不是執行細節。** 停下來確認現在這條線是不是已經被取代，再決定要不要繼續。

**第二條，更可遷移**：**活的流程要求一個死掉的前置條件時，先懷疑自己走錯路線，
不要先去補那個前置條件。** 「這個必填欄位只有 X 生得出來，而我們不用 X 了」——
正確的問題是「為什麼活的流程會要求一個已經不用的東西」，不是「要不要補一個替代的 X」。

**Why**：2026-09-11，20260721 呂冠緯的三支長精華。`podcast-pipeline/SKILL.md` 的 S9 整節在
描述 ADR-065 的 `podcast_highlight_visual_orchestrator.py`，而 `highlight-cut` 與 `longform-cut`
兩份 skill 開頭都掛著「⛔ 已停用，long 的生產唯一路線 = ADR-066」。我照我當時正在讀的那一份跑。

**三次警訊被讀過去**：三個 Director subagent 各自在回報裡寫「work packet 沒有
`long_highlight_contract` v2 marker → **legacy ADR-065 route**」。我讀了三次，當成執行細節。

**然後我把 blocker 誤診成設計題**：`accept-director` 要一份只有 `CodexExecDispatcher`
生得出來的執行收據（要真實 subprocess 的 stdout/stderr 與 orchestrator_pid），我就去問修修
「A 補一個 Claude dispatcher／B 這次讓 Codex 跑」。修修的回應是：

> 你不是說你已經解決指定用 codex 這個問題了嗎？怎麼現在又發生這個問題了？

而答案是**那個問題早就修好了**——ADR-066 的 `run_finished_cut_production.py` 有
`--semantic-worker {codex,handoff}`，`handoff` 就是「停下來交給當下正在跑的 agent」。
我是在一條沒人維護的死線上，重新發現它沒有 Claude 支援。那個 A/B 二選一是在為封閉的路蓋橋。

**How to apply**：
- 長片視覺線＝ADR-066 `run_finished_cut_production.py --semantic-worker handoff`，
  手冊是 `longform-cut/SKILL.md` 的「ADR-066 實跑手冊」節。**不是** `podcast_highlight_visual_orchestrator.py`
- 動一條線之前，先在該線的**專屬 skill** 開頭找有沒有 ⛔ banner；orchestration skill（podcast-pipeline）
  的描述可能落後。2026-09-11 已把 ⛔ 補進 S9 與 S8F 派工圖（commit `7a9f3b39`），
  但下次遇到新的線，仍然要自己去專屬 skill 確認一次
- subagent 回報裡的「legacy」「deprecated」「v1 fallback」要當成要回答的問題，不是背景資訊
- 相關：[[feedback_semantic_work_runs_on_host_agent]]、[[feedback_fix_failures_dont_report_them]]
