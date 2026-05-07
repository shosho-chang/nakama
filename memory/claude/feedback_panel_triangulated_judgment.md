---
name: panel triangulated judgment
description: 高風險決策若已跑 multi-agent-panel 拿到 Codex+Gemini 兩家獨立 audit，使用者偏好 Claude 直接判而非 defer 回 user
type: feedback
---

當高風險決策（ADR、架構選擇）已透過 `multi-agent-panel` skill 取得 Codex + Gemini 獨立 audit 後，**修修偏好 Claude 整合三家意見直接判，不要再 defer fork 選擇給 user**。

**Why**：修修原話「既然你現在已經有了三家的意見，我想應該會比我來判斷還準」（2026-05-07 ADR-021 panel review）。三家 audit 已經消化掉 single-LLM 的 confirmation bias，整合矩陣本身就是 high-confidence 的信號；再 defer 回 user 只是把 cognitive load 推回去，對決策品質沒貢獻。修修的 expertise 在內容創作 + 系統 stated preferences（vault 簡潔、Obsidian access pattern），這些 panel 已 explicit 納入考量。

**How to apply**：

- Panel review 完跑出 strategic forks → 整合矩陣後**直接給組合套餐建議 + 一句話確認意願**，不要列 4 個選項要 user 逐條判
- 仍要 surface 矩陣全文（user 可選擇 review），但 default 動作是 Claude lock decisions 並繼續產出 v2 artifacts
- 例外：fork 的選擇直接違反 user 已 stated preference（vault rules / agent role / 內容方向）— 這時要 surface 給 user，因為 panel 不知道 stated preference
- 也適用於：grill 過程中你已蒐集大量 user signals + panel triangulation → 該你判時就判，不要過度民主化

**反向：什麼情況 panel 不夠、必須 defer**：

- 涉及修修個人時間 / 金錢 / 心力分配的決策（哪 line 先做、要不要花錢買 X）— panel 不知道修修當下狀態
- 美學 / tone / 內容方向（panel 沒 user taste）
- 跟個人習慣 / vault 結構 mutation 直接相關但 panel 沒 explicit 證據的場景
