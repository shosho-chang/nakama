---
name: Chopper Community Health Q&A Agent
description: 在 community 平台回答會員健康問題，調用 KB 查詢，記住會員資料
type: project
originSessionId: 387704f9-a851-4156-893b-7b0b74f69276
---
Chopper agent 負責在修修的 community 平台（平台 TBD）回答會員有關身心健康的提問。

**Why:** 會員在社群中提問健康問題（睡眠/飲食/運動/情緒），Chopper 可以查閱 Robin 的 KB（教科書、研究文獻）給出有文獻支撐的回答，並記住個別會員的狀況。

**How to apply:**

設計 Chopper 時注意以下三大挑戰：
1. **平台接入**：Community 平台（Circle.so? Discord?）需要新 gateway endpoint（Webhook/API），和 Slack bot 是同等級的工作量
2. **會員記憶**：需要持久化 DB（SQLite/Redis）—— ConversationStore 是 in-memory，重啟就消失；需要 per-member profile table
3. **成本控制**：KB 查詢 + Sonnet 問答，每次對話 $0.05-0.20，高流量需要 rate limiting / per-member quota

架構草圖：
```
Community Platform → Webhook → Nakama API → Router → Chopper handler
                                                      ↓ tool: kb_search
                                                   Robin KB
                                                      ↓
                                                 Claude answer
                                                      ↓
                                               Member Profile DB
```

**未決問題（用戶決定）：**
- Community 平台是哪個？
- 先做哪一塊：平台接入 / 會員記憶 / KB 問答本身？
- 醫療免責聲明的措辭

**狀態：** 待開發，優先級最後（Brook = Usopp = Franky 都 > Chopper）。

**2026-04-22 更新**：
- Chopper **完全不給診斷或醫療建議**，只做資訊 / 教育 / 引用 KB 文獻
- 上線前在 Slack 內部先「活一陣子」做 dogfooding（不直接暴露給社群會員）
- FluentCommunity 給 Chopper 專屬 space，會員資料（FluentCRM）**可主動引用**（社群最大優勢）
- HITL approval 三階段：Phase A 全 approve → Phase B 信心閾值 + 敏感詞分流 → Phase C 全自動 + audit
- 細節（敏感詞清單、免責措辭、profile 免責、超出範圍行為）上線前再討論
