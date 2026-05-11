---
name: Subagent 從零起跳沒有 memory — 高指導原則必須 inline 進 prompt 不能依賴 surface
description: ADR-016 Phase B subagent 違反「品質>速度>成本」最高指導原則的 root cause — declarative memory 不會自動進 imperative subagent prompt；任何 dispatch 前必把適用 feedback memory 原文 inline，不能假設 subagent 會讀
type: feedback
created: 2026-05-06
---

Dispatch subagent（Agent tool / Sandcastle / 任何 fresh context）時，**修修的最高指導原則必須直接 inline 進 prompt 文字**，不能依賴 memory system 自動 surface。Subagent 從零起跳沒有 conversation history、沒有 MEMORY.md 載入、不會自動讀 feedback memory 檔。

**Why**:

2026-05-06 發現 87.5% Concept page 是空殼，trace 回去最諷刺的時序：

- 2026-05-01 修修 explicit 講「品質 > 速度 > 省錢」→ `feedback_quality_over_speed_cost.md` 寫進 memory
- **2026-05-03 同一天**：修修再次強調最高品質指導原則 → memory updated
- **2026-05-03 同一天**：ADR-016 寫成 + Phase B subagent 被 dispatch + 產出 544 個 stub

修修的最高指導原則 **更新日跟違反日是同一天**。不是 memory 寫晚了，是 subagent prompt 沒 inline。

phase-b-reconciliation.md 引用 ADR-010 / ADR-011 但 **完全沒提 `feedback_quality_over_speed_cost`**。subagent 從零起跳，看到的世界裡：「Quality > speed」只是 prompt 末尾的裝飾性 trailer (line 171)，Step 3 template 本身寫死 stub creation。當二者衝突時，imperative template 贏 declarative trailer。

**How to apply:**

任何 subagent dispatch 前，prompt 必須包含 **「適用最高原則」** 段落，inline 修修以下原則的原文（按任務類型挑選相關的）：

| 任務類型 | 必 inline 的 feedback memory |
|---|---|
| Ingest（textbook / kb / paper） | `feedback_quality_over_speed_cost`, `feedback_kb_concept_aggregator_principle` |
| Compose（article / video script / IG） | `feedback_quality_over_speed_cost`, `feedback_aesthetic_first_class` |
| Extract / Curate | `feedback_quality_over_speed_cost`, `feedback_acceptance_target_clarity` |
| 任何 P9 fanout | 上述全部 + `feedback_no_handoff_to_user_mid_work` |

**Inline 格式**（不是引用 link，是原文 verbatim quote）:

```
# Top-level Principles (verbatim from修修's persistent memory)

## 品質 > 速度 > 成本（最高指導原則）

修修 explicit 講過：「我不求速度也不求省錢，我求品質。品質為第一優先。」

具體應用：
1. 模型選擇：涉及品味/voice/judgment 用 Sonnet 4.6 或 Opus，不為省錢降 Haiku
2. 架構複雜度：multi-stage / validation gate 提升品質就值得
3. ...（從 feedback_quality_over_speed_cost.md How to apply 9 條全 inline）

## [其他適用原則 verbatim quote]
```

**為什麼必須 verbatim 不能改寫**:

改寫會丟失 nuance。「品質 > 速度 > 成本」三個字背後有 9 條具體應用 + 例外清單 + 與其他原則的張力解析。subagent 看到改寫版本只會看到 slogan，不會 internalize 操作邏輯。

**Anti-pattern**:

- prompt 寫「按 ADR-X」而非 inline ADR-X 的具體原則
- prompt 末尾加一句「Quality > speed」當 trailer 而沒展開
- 假設 subagent 會 grep memory（subagent 不會也不該）
- 假設 subagent 看 trigger phrase 就會 surface 對應 feedback（不會）

**主線 agent 的責任**:

主線 dispatch subagent 前的 prompt drafting 工作必須包含「inline 適用原則」這個 step。這不是 nice-to-have、是 **load-bearing 的工作流環節**。漏掉等於把最高指導原則扔進垃圾桶。

**驗收**:

dispatch 任何 subagent prompt 前，回頭 grep 自己的 prompt — 適用原則的 verbatim quote 找得到嗎？找不到 = stop，補上再 dispatch。
