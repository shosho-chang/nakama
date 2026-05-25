---
name: 大 ADR 先 grill-with-docs 再 multi-agent-panel 是必要 sequence，不是 nice-to-have
description: 修修在 13-Q grill 多次「聽你的判斷」+ Claude v1 confident 凍結 → panel 矩陣顯示其中 4 項選錯（rapidfuzz primary、Tier 3 UI 3d、cold-start retrieval、寄居 Brook drift）；panel 整合 17 採納大幅修正 v1
type: feedback
---

當寫一份會 6-month lock 的 ADR（架構決定 / data model / agent contract / 跨 module 介面），**grill + panel 兩步走完整**，不是「grill 完就 ship」也不是「直接 panel 不用 grill」。

**Why**：grill-with-docs 把分歧逼出來、user 表態，但 user 常在多分支裡 delegate「聽你的判斷」給 Claude — 這正是 confirmation bias 最容易中的點。**Claude v1 confident 的判斷是 panel 必抓的訊號**。2026-05-25 ADR-032 跑完整 sequence：13-Q grill 內 user delegate 7 題（Q2/Q3/Q4/Q5/Q10 部分/Q11/Q12），Claude v1 凍結後跑 3-way panel → matrix 顯示 4 項 v1 選錯：
- rapidfuzz primary（Codex + Gemini 一致：應 exact-copy + hard fail）
- Tier 3 UI 3 天估時（Codex + Gemini 一致：5-7 天 fantasy）
- cold-start examples retrieval（Codex + Gemini 一致：corpus 空純浪費 token）
- 寄居 Brook（Gemini 獨家：ADR-027 narrow 後該建新 agent）

外加 Codex 抓出 code grounding 大破（「shipped」實際在 sibling worktree／spike 目錄）+ FCPXML adjust-transform 單位錯。Gemini 抓出整套 Mandarin pre-processing 漏寫（cn2an 數字正規化、全形標點、台灣「」brackets、smart cue join、LINE Seed TW @font-face）。Panel 整合：17 採納 / 4 reject / 2 defer，最終 v2 工程量 9.5d → 13.5d，避免實作半路才發現 v1 漏洞。

**Where**：本 session 完整流程文件保存在 [memory_grill_then_panel_pattern]（本檔即是）。Codex audit verbatim `docs/research/2026-05-25-codex-adr032-audit.md`、Gemini audit verbatim `docs/research/2026-05-25-gemini-adr032-audit.md`。ADR-032 §Panel Integration 內含完整 17 項 matrix。

**How to apply**：

- 任何 ADR 觸發以下任一條件 → 走 grill + panel 完整 sequence：
  - 架構鎖死（hard to undo within 6 months，例如 data model / ingest pipeline / agent contract / 跨 service 介面）
  - 數字宣稱（cost / latency / throughput / engineering days 估值）
  - Claude confident reject 替代方案 ≥ 3 個 — confident 的地方就是 confirmation bias 最容易中的地方
- 順序固定：先 grill（凍結 Q & A）→ Claude 寫 v1 draft → commit v1 → multi-agent-panel skill 跑（Codex + Gemini）→ Claude 整合 matrix → v2 → ship
- **不要省略 grill 直接跑 panel** — panel reviewer 沒有 Q & A 對話脈絡，會 review 文件表面而不是底下的 reasoning，價值打折
- **不要省略 panel 直接 ship grill 出來的 v1** — user delegate 給 Claude 的點 = bias 集中區，panel 是唯一外部校正
- Panel 時 Codex 用 `gpt-5` + `model_reasoning_effort=medium`（見 [feedback_codex_medium_reasoning_for_long_audit]），不要用 default xhigh 或 gpt-5-codex variant — 後者長 audit 容易連線斷
- 工程量估值在 panel 後通常 +30-50%（v1 樂觀 → v2 加 Mandarin / fixture / acceptance gate / 更多測試）— 不要把這當「Claude 不準」，而是 confirmation bias 校正後的真實數字
