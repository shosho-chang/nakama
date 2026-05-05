---
name: 守 context window 的關鍵是 offload 工作到不會 surface 結果的子系統
description: 反直覺結論 — 不是壓縮 prompt 是 offload。Sandcastle (docker isolation) > Agent tool subagent > 主線 implement。AFK 全自主 4 issue ship 4hr 主線 only 22% context（192k messages）的實證教訓。
type: feedback
created: 2026-05-06
---

執行長時間 AFK 任務（多 PR sync + 多 sandcastle batch）時，主線 context 守得住的關鍵 **不是壓縮 prompt 而是 offload 工作到不會 surface 結果的子系統**。

**Why**：2026-05-06 凌晨修修去睡前授權「4 個 issue 全 sandcastle 直接執行完畢，切記不能爆主線 context」。9 個 task / 4 小時 wall（PR sync 5 條 + sandcastle 3 輪 + 早報），主線 192k messages = 22% / 1M。實證守住。

**How to apply**：

按 isolation 強度排序 4 種 offload 機制，由強到弱：

1. **Sandcastle (Matt Pocock TS lib)** — process-level isolation，docker container 跑 Claude CLI subagent，自己的 75-124k context，**從來不回主線**。主線只讀 log file 結尾的 P7-COMPLETION（commit hash + 一行）。實作工作 / TDD red→green / debug / 探索全在 docker，主線完全不知道也不需要知道。**Hard isolation，靠系統不是 prompt 規範**。
2. **Agent tool subagent (general-purpose / Explore)** — 次級隔離，subagent 結果以 string 回主線。比 inline 工作省，但 P7 report 5-10k 字會回（見 [feedback_minimal_subagent_prompt.md](feedback_minimal_subagent_prompt.md)）。
3. **Background bash + TaskOutput block** — wait 期間主線真 idle，但 background bash 把 stdout 全寫進 file，最後 Read 時進 context。
4. **Monitor 工具** — 比 background bash 好，只 emit pattern-matched 行（pass / fail）。selective output。

**反直覺結論**：壓 prompt 邊際收益小（系統 prompt 已 8.7k 固定），真正爆 context 的是「主線在做工作」— 探索讀檔、subagent 回 P7 report、CI poll output 累積。只要工作 offload 到 sandcastle，主線就只剩 orchestration（commit hash + PR URL + git status），mechanical 流程一個 task 不超過 5k token。

**Edge cases / cost**：
- CI poll 用 background bash + 30s sleep loop 會累積 60+ 條 timestamp（每條 ~30 token）— 換 Monitor 只 emit 結果。
- Sandcastle log 看 progress 時用 `tail -50` 不要 read 全文（log 動輒 200+ 行）。
- 流程 mechanical 時不要打開 codebase 探索（探索是 sandcastle subagent 的事）。

**整套框架**：主線 = orchestrator，sandcastle = implementer，CI poll = background event source，TaskOutput = blocking sleep。**主線 context 留給決策，不留給看細節**。
