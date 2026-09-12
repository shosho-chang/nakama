---
name: feedback_semantic_work_runs_on_host_agent
description: 語意工作一律由「當下正在執行的 agent」自己做，不因為 code 寫死就去派 Codex；ADR-066 _composition.py 寫死 Codex 是違規
metadata:
  type: feedback
---

pipeline 裡的語意工作（miner、Director、DP、visual review、persona 審）**由當下正在跑的
agent 自己執行**。是 Claude 在跑就 Claude 做，是 Codex 在跑就 Codex 做。**不要因為某段
code 寫死了某個供應商，就照著去派那個供應商。**

**Why**：2026-09-07 蘇予昕長片線，我讀到 ADR-066 的
`agents/brook/script_video/finished_cut_production/_composition.py:819` 寫死
`CodexSemanticAdapter` + `SubprocessCodexProcessRunner`，就準備去 spawn Codex CLI 跑
Director/DP。修修打斷：「**不要管是要給 CodeX 還是 Claude，這裡就是目前是哪一個 Agent
執行，就是給哪一個執行。之前已經講了好幾次了。**」

關鍵事實：**skill 是對的，code 是錯的。** `highlight-cut/SKILL.md:25` 明訂
「skill 不呼叫 repo 的 API `llm_router`，也不把單一供應商 model 寫進 Python——
這讓同一份 skill 在不同 platform 依 host 自動選擇」。ADR-066 的 composition 違反它，
而且 `run_finished_cut_production.py` CLI 沒有開 `process_runner` 注入點。

**How to apply**：
- 看到 code 寫死供應商 → 那是 bug，不是指示。照 skill 做，並把 code 的違規回報成待修項。
- `run_short_broll.py` / `run_short_titles.py` / `run_short_sfx.py` 是 **materializer**，
  不等於 `brook-director` / `brook-dp` skill 的執行——skill 執行是 agent 的工作。
- 待修：`_composition.py` 應把語意 worker 變成可注入，CLI 開對應參數。
- 相關：[[feedback_sandcastle_default]]（並行 dispatch 的預設環境）。
