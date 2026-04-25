---
name: 寫 todo / pending_tasks 必附 use case
description: 寫 backlog todo 一行字時必須附「我什麼時候會用」的具體場景，沒寫就是 design smell；直接拿無 use case todo 開做會 over-build 沒人用的 feature
type: feedback
originSessionId: c6399fca-d109-4f35-807f-e564c7010f0c
---
寫 `project_pending_tasks.md` 等 backlog 條目時，**每一條 todo 必須附一句 use case 推演**：

> 這 feature 解決什麼具體場景？我（user）什麼時候會用？

沒寫就是 design smell — 之後拿這條開做時容易 over-build 解決不存在的問題。

**Why**：2026-04-25 PR #141 paste-image feature 從一行「Robin Reader：metadata 卡片顯示 + 貼上圖片顯示（本機測試）」開出。實作完 review 過 6 條 hard contract，準備 smoke test 時 user 自己問「我想不到這在我工作流中會用在哪」 → 推演 4 個可能 use case 全部站不住（既有的圖 image_fetcher 自動下載、編輯動作在 Obsidian 不在 Reader、註解走 /save-annotations、跨 paper 截圖罕見）。結論：解決了一個不存在的問題。拆 PR 重做浪費已跑的 review/merge 工。

**How to apply**：

1. **寫 todo 條目** — 至少包含：
   ```
   - ⬜ <feature 名> — <use case 一句>：<什麼觸發 / 我做什麼動作 / 期望什麼結果>
   ```
   反面例：「⬜ Reader 加 paste image」
   正面例：「⬜ Reader 加 paste image — 跨 paper cross-reference：讀 A paper 想插 B paper 的圖時，截圖 → reader 內 Cmd+V → 自動寫 Files/ + 插 wikilink」（這個 use case 寫出來自己就會發現很罕見）

2. **拿 todo 開做前** — 先讀那條 use case：
   - use case 模糊 → 停下找 user 確認
   - use case 清楚但只有 1 個極稀情境 → 仍找 user 確認
   - use case 清楚且日常會發生 → 開做

3. **review todo 時** — 一行字無 use case 的 todo 視為 not-ready，不該被當 input。發現時補 use case 再排程。

4. **CLAUDE.md 三條紅線扣回**：
   - 「不要為假設未來需求設計」
   - 「Don't add features beyond what the task requires」
   - todo 不寫 use case = 對未來假設需求設計

適用範圍：`project_pending_tasks.md`、GH issue body、ADR backlog 段、handoff doc 的 §7「下一步」。
