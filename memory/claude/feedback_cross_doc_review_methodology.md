---
name: 跨文件 review methodology（single-ADR multi-model 之上加一層）
description: 多 ADR 協調的專案，single-ADR multi-model review 通過後還要跑 3 agent 跨文件審查抓 schema drift
type: feedback
tags: [review, methodology, multi-agent, adr]
created: 2026-04-22
updated: 2026-04-23
---

**規則**：多 ADR 協調的 feature（Phase 1 這種）ADR 寫完後，single-ADR multi-model review（Gemini / Claude / Grok）通過只是必要不充分條件，要再跑 3 個並行 Opus agent 做跨文件審查才算清：

1. **Consistency audit** — schema 名稱跨 ADR 是否一致？contract A→B→C 有沒有斷裂？monitoring scope 有沒有 claim 卻沒 cover 的失敗模式？
2. **Implementation readiness** — 逐 ADR 評 1-10 具體度，能不能直接 dispatch sub-agent 寫 code？
3. **Plain-language explainer for 修修** — 翻譯成白話，點出該 push back 的地方（補償修修自認「很多看不懂」）

**Why**：2026-04-22 這次發現的 4 個 blocker（`ApprovalPayloadV1` vs `DraftV1` contract drift、`ComplianceFlagsV1` 同名兩份衝突定義、`reviewer_compliance_ack` 欄位完全缺失、plan §1.2c/d/e 跟 ADR-007 slim 版矛盾）single-ADR review 抓不到，因為它們只在**兩份文件比對時才存在**。跑完這輪 Agent B 還發現 ADR 的 FSM / atomic claim 沒寫到 implementation 實際會撞的 Python sqlite3 隱式 transaction vs `BEGIN IMMEDIATE` 衝突。

**How to apply**：
- 觸發條件：≥3 份 ADR 互相引用 + 即將開工 implementation（不是純規劃期）
- 3 agent 並行跑，都是 Opus（judgment-heavy，不能省）
- 如果 Agent A 回報 blocker 超過 2 個，先修完再開 feature branch；修補本身 15-30 分鐘就能清
- Agent C 的白話摘要給修修看，他能指出「這條我 push back」，不用自己讀完整 ADR

**Cost 參考**：這次 3 agent 各用 ~120-130k tokens，總成本約 $10-15 美金；但省下至少 1 天「開工 1 小時就炸 NameError」的 debug + rework 時間，值。
