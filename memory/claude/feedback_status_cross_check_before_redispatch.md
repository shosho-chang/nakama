---
name: 看到「待跑」狀態先 cross-check artifact 不直接 redispatch
description: 跨 session 接手任務看到任一處寫「pending / 待跑 / sub-step done」前必交叉驗證 source-of-truth artifact，不要單一 timeline 字串就 dispatch 重跑（idempotent task 浪費 token，non-idempotent 會壞事）
type: feedback
---

接手任務看到「Phase X 待跑 / pending」**永遠先 cross-check source-of-truth artifact**，不要看單一 timeline 字串就 dispatch。

**Why**：2026-05-03 Sport Nutrition 4E ingest case — Book Entity 的 Status timeline 寫「Phase B reconciliation 待跑」，但 KB/log.md 同日已有 entry「440 new Concept stubbed + 45 existing updated」，我沒看 log.md 直接 dispatch Opus 4.7 background subagent，跑出 idempotent re-verification（0 new、0 update），**浪費 115K tokens / 11 min wall**。Book Entity 的 timeline 行純文字 ≠ 真實狀態。

**How to apply**：

接手任一 multi-step task，看到 status string 寫 pending / 待跑 / 沒開始前，必跑這三條 verification：

1. **Log/audit file 直接看當日 entry** — KB/log.md / git log / runbook history。Source-of-truth 寫在 log，timeline 字串是 free-text 容易漏更新
2. **Artifact spot check** — 隨機抽 1-2 個產出物驗證（這次該抽 1 個 concept page 看 `mentioned_in:` 有沒有 sport-nutrition chapter ref）
3. **Count 對齊預期** — 已知 baseline「Phase B 後概念頁約多 N 個」, 實 ls | wc -l 看真值。對 = 已跑 / 不對 = 沒跑

三條都對 = 已完成不需重跑。三條有出入 = 真的 pending 才 dispatch。

**Idempotent 設計救了這次**（Phase B 的 dedupe 邏輯讓 0 new + 0 update 是 safe outcome）。但 non-idempotent task（PR merge / DB migration / production deploy）做這種「看 timeline 就重做」會直接壞事。

**Cross-ref**：[feedback_sync_before_grill.md](feedback_sync_before_grill.md) 是同類概念但更窄（grill 前 sync），這條是 task dispatch 前 cross-check 的更廣應用。
