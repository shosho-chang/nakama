---
name: feedback-verify-audit-claim-before-grill
description: 跑 architecture audit 後進 grill 前先驗證 audit 對該 candidate 的具體 claim — 別直接照 audit 開設計題
metadata:
  type: feedback
---

跑 `/improve-codebase-architecture` 或類似 audit 後，挑了一個 candidate 要 grill 之前，**第一步是讀原始程式碼驗證 audit 對這個 candidate 的具體 claim**，不是直接開設計題。

## Why

2026-05-26 promotion polymorphism grill：

- audit 報告 `#4 LLM facade` 的 claim：「`llm_observability.record_call()` 接受 auth_requested / auth_actual / fallback_reason，但 `ask_multi()` 沒填這些欄位 — DB 是 None」
- 我直接信了，把 #4 列為首推、blast radius 最小
- 進 grill 第一輪讀 `anthropic_client.py` 真實 code 才發現：`ask_claude_multi` lines 421-471 **明確完整呼叫** `_record_anthropic_usage(... auth_requested=requested, auth_actual=actual, fallback_reason=reason)`
- 也就是 audit 描述錯誤；silent downgrade 其實有寫進 DB。
- 整個 #4 設計題的前提（CallResult value-object 解 observability 缺口）失效，要切到 #1 重來

Explore agent 跑 audit 本質是「快速 sweep + 大膽 claim」，準確率不是 100%。建立在錯誤前提上的 grill 整輪白做。

## How to apply

- audit 報告挑定 candidate 後，**進 grill 第一輪先讀 candidate 涉及的核心檔案**（不只看 audit 摘錄的行號 — 完整 read 該函式 / 模組）
- 對 audit 的具體 claim（「X 沒做」「Y 是 None」「Z 雙寫」）逐項對照真實 code 驗證
- 發現 audit 過度誇大或錯誤 → **立刻 surface 給 user**，不要硬把錯誤前提 grill 下去
- 修正後重新評估 candidate ROI；可能變得不值得做，要誠實切到別的 candidate
- 對應 [[feedback_decision_lookup_via_adr_not_grep]] 的同類紀律 — 不靠第一印象、看真實 source of truth
