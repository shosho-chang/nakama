---
name: 教科書 ingest workflow 設計缺口
description: ✅ RESOLVED 2026-04-25 — 升級為 ADR-010；Claude Code Opus 4.7 skill + Karpathy 跨書 Wiki，跳過 embedding/vector store/桌機 GPU
type: project
originSessionId: c6399fca-d109-4f35-807f-e564c7010f0c
---
**Status: ✅ RESOLVED 2026-04-25** — 升級為 [ADR-010-textbook-ingest.md](../../docs/decisions/ADR-010-textbook-ingest.md)。

決策方向（兩個 insight 重塑設計）：

1. **Karpathy KB 哲學**：每章吃完都增建 / 更新 vault 內 Concept / Entity 頁，跨書共享 Wiki 池，每頁帶 `mentioned_in:` backlink
2. **Claude Code Opus 4.7 1M context + Max 200**：教科書數量少 + 絕大多英文 → Claude Code skill 互動式跑，**完全跳過 embedding / vector store / 桌機 GPU**

落地：`.claude/skills/textbook-ingest/`。Phase 2 backlog：Web UI + multi-provider subscription 選擇。

下方為原始 design gap 描述（歷史紀錄保留）。

---

[project_chopper_community_qa.md](project_chopper_community_qa.md) Chopper community Q&A 設計寫「Chopper 可查 Robin 的 KB（教科書、研究文獻）」當前提 — 但 nakama 沒有「整本書 ingest」流程。

現有 ingest pipeline 都是 per-document chunk + categorize（`agents/robin/ingest.py`）—— 不是 superset of「整本書 ingest」。

**待回答的設計問題**（Chopper 開發前要凍結）：

1. **單位**：一本書（PDF / EPUB / Word）→ 章節拆分？整本當一個 source？
   - 一本醫學教科書 1500 頁，整本一個 source 對 retrieval 不友善
   - 章節拆分有層級結構問題：第 X 章第 Y 節 vs 純扁平

2. **Schema**：`type: textbook` 之外要不要加 ISBN / 版本 / 出版年 / 章節索引 / 教科書類型（國考用 / 專業參考 / 科普）？

3. **Vault 落地**：
   - 方案 A：每章一個 `KB/Wiki/Sources/textbook-{book_id}-ch{n}.md`，扁平
   - 方案 B：`KB/Raw/Books/{book_id}.md` 完整原文 + `KB/Wiki/Sources/` 章節索引兩層（對齊 LifeOS CLAUDE.md Layer A/B 規範）
   - 方案 C：每章獨立 + 一個 `KB/Wiki/Entities/Books/{book_id}.md` 串連結

4. **Chopper retrieval**：
   - Embeddings 怎麼分塊？章節 / 段落 / 滑動視窗？
   - Citation 要回到「第 X 章第 Y 節」還是檔名？
   - 跨章合成（多章共同回答一題）怎麼處理？

5. **Ingest trigger**：
   - 在哪個 agent / endpoint / CLI？
   - 走桌機本地（per [feedback_compute_tier_split.md](feedback_compute_tier_split.md)）— 整本教科書 PDF 解析 + chunking + embedding 屬重 ingest
   - 走 Slack 命令 / Robin Reader / 桌機 CLI script？

**依賴**：
- Docling / EPUB parser 開發（P2 backlog，桌機本地）
- Robin schema 統一（content_nature / lang / doi 全 vault 補上）—— 詳見 [project_vault_ingest_flow_drift_2026_04_25.md](project_vault_ingest_flow_drift_2026_04_25.md)

**優先序**：Chopper 開發前就要把這條設計凍結，否則 Chopper retrieval 沒結構化 KB 可查。Chopper 是 Brook = Usopp = Franky 都做完才開的（per chopper_community_qa.md），所以還有時間想清楚。
