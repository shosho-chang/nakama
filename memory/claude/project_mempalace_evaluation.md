---
name: MemPalace 整合評估
description: MemPalace 開源專案的整合評估過程與結論——目前觀望，條件滿足後才整合。最後查核日 2026-04-11
type: project
tags: [mempalace, integration, tier3, zoro-watch]
created: 2026-04-11
updated: 2026-04-11
confidence: high
ttl: permanent
---

MemPalace（`milla-jovovich/mempalace`）是一個外部開源專案，評估後決定暫不整合，交由 Zoro 每週監控。

**定位：** 作為 Claude 對話記憶層（個人偏好 + 討論歷史），與 Obsidian KB/Wiki 分開，互補而非替代。未來可能取代 ADR-002 中 Tier 3 的 Mem0 選項。

**評估結論：目前不整合。**

**Why:** 中文支援完全缺失，專案極新（2026-04-06 上線，5 天），穩定性 bug 仍在。

**最新狀態（2026-04-11 查核）：**
- v3.1.0（2026-04-09），GitHub ~40.8k stars，161 open issues
- LongMemEval 96.6%（Mem0 ~85%），ConvoMem 92.9%（Mem0 30-45%）
- 啟動僅 ~170 tokens，MCP 原生 19 個工具
- 原文保留 + AAAK 30x 壓縮（Mem0 是有損 LLM 萃取）
- SQLite 時序知識圖譜（內建矛盾偵測）
- **無任何中文 / CJK / 多語言支援**
- **修修認為比 Mem0 更強，中文語意搜尋完善後可作為 Mem0 的替代方案**

**整合條件（全部滿足才行動）：**
- [ ] Chinese embedding model 就位（目前無）
- [ ] room_detector_local.py 加入中文關鍵字（目前無）
- [x] Issue #290（MCP 版本不一致）— 已關閉（v3.1.0 修復）
- [ ] Issue #303（Windows split）— 仍 Open
- [ ] Issue #327（JSONL parser 丟失 user messages）— 仍 Open
- [ ] Open issues 穩定 < 60 個（目前 161 個）

**追蹤方式：** Zoro 每週自動檢查（`config/zoro-watch-mempalace.yaml`），條件全滿足時發 email 通知修修。

**How to apply:** 若 Zoro 發出整合通知，或修修主動問起 MemPalace 進度，再重新評估是否整合。在那之前不需要手動追蹤。ADR-002 的 Tier 3 進階選項應同時考慮 MemPalace（不只 Mem0）。
