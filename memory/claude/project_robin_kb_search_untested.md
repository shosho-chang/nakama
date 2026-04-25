---
name: Robin KB Research 功能狀態
description: /kb/research 2026-04-25 重驗 E2E 通過（PR #119 修 Entities normalize bug 後），Obsidian UI 待修修再改
type: project
tags: [robin, kb-search, obsidian]
created: 2026-04-11
updated: 2026-04-25
confidence: high
ttl: 90d
originSessionId: ecac2e9b-d409-4922-b30f-4270e46d6df0
---
## 已完成

**2026-04-13**：
- `/kb/research` endpoint 第一次測試通過（從 Obsidian 成功呼叫、回傳 8 筆結果）
- 修復 `nakama-config.md` 的 `robin_url` 尾部斜線問題（雙斜線導致 404）
- 修復 DataviewJS 自毀 bug：regex 匹配到自己 source code 中的 `<!-- kb-results -->`，改為 DOM 渲染 + localStorage 持久化

**2026-04-25**：skill 化前 E2E 重驗（PR #119 merged 後 `kb_search.py` 改過 — 修了 "Entities" type normalize bug）：
- 中文 query「肌酸對認知功能的影響」→ 8 筆，4.8s。5/8 強命中、2/8 OK、1/8 reach（心肺適能）
- 英文 query「sleep and longevity」→ 8 筆，6.1s。7/8 強命中、1/8 reach
- type normalize fix 沒倒退（`source` / `concept` / `entity` 規範值）
- path 格式一致（`KB/Wiki/Sources/<slug>`）

## 已知 enhancement（不是 bug）

- top_k=8 寫死 → KB 沒夠多相關時模型會用 reach 結果填，「心肺適能」連兩 query 都被推上來
- skill 化時可考慮：dynamic top_k（讓 LLM 決定 highly-relevant 上限）或 confidence threshold

## 待修

- KB Research 結果的 UI 呈現方式修修想再調整（具體需求待定）

**How to apply:** 修修提出具體調整需求時再改。E2E 已驗、可開 `kb-search` skill scaffolding。
