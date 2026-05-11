---
name: Session start / compact reload 第一件事必讀 MEMORY.md
description: CLAUDE.md §0 已明文規定「每次對話開始讀 MEMORY.md 載入持久記憶索引」；compact 後 reload 也算對話開始；不能用「已讀 session handoff doc」當藉口跳過——handoff 是任務級，MEMORY 是 cross-task 規則級
type: feedback
created: 2026-05-06
---

**規則**：每次 session 開始（含 compact 後 reload），第一個 tool call 必須是 `Read memory/claude/MEMORY.md`。讀完掃 index 找跟當前任務相關的條目（vault path、之前任務狀態、feedback rules、reference paths）。

**Why**:

2026-05-06 ADR-020 panel session compact reload 後犯了重大錯：

1. 修修 prompt 是「讀 memory/claude/project_session_2026_05_06_adr_020_panel.md」— 我照字面只讀那一份 handoff doc
2. 結果忽略 MEMORY.md 裡早就存在的 `reference_vault_paths_mac.md`（line 45）— 這條明確寫「Windows active = `E:\Shosho LifeOS\`，F: 已停用、stale Syncthing 殘留要避開」
3. 加上 `additional working directories` 系統設定還掛著 `F:\Shosho LifeOS\Tasks/Projects/Templates`（過時）
4. 於是我跑去 F: vault 做 reality check，看到 81 個 concept / 0 個 stub / 「Sport Nutrition 找不到」，全部對不上 memory 寫的 622/544
5. 我還倒推 memory 數字錯了，建議修修「reality check」— 等於用一個錯誤前提推翻一份正確的 memory
6. 修修糾正：「你該不會又看到 F 槽去了吧？」— **這是 5/3 textbook ingest 同一個錯誤的重複犯**

memory 系統存在的整個 point 是 cross-session retrieval。不讀 MEMORY.md = 把 memory 當擺設、把每次 session 當零起跳。

**How to apply**:

1. **Session 開始 / compact reload 後**，第一個 tool call 永遠是 `Read E:\nakama\memory\claude\MEMORY.md`
   - 即使修修的 prompt 看起來只要求讀某個特定檔（e.g. handoff doc），也要先讀 MEMORY.md
   - 即使 system reminder 說「MEMORY.md was read before the last conversation was summarized」— compact 之後 context 重啟，那次讀的內容已經不在當前 context 裡，必須重讀
2. **讀完掃 index 找相關條目**：
   - 任務涉及 vault → 找 `reference_vault_paths_*` / `project_disk_layout_*`
   - 任務涉及 ADR → 找 `feedback_adr_principle_conflict_check`
   - 任務涉及 subagent dispatch → 找 `feedback_subagent_prompt_must_inline_principles`
   - 任務涉及 context budget → 找 `feedback_context_budget_200k_250k`
3. **`additional working directories` 系統設定不可信任** — 那是 session 啟動時固化的 snapshot，可能 inherit stale path（如 F:）。Active 狀態以 memory（`reference_vault_paths_mac.md`）+ 實際驗證（`obsidian.json` `open:true`）為準。
4. **如果跨檔對帳數字跟 memory 對不上**，第一假設應該是「我看錯地方」而不是「memory 錯了」。Memory 是 deliberate 寫入、有 commit hash、有 timestamp 的 frozen state；活檔狀態反而比較容易因為路徑錯而誤讀。要先驗證 path 再質疑 memory。

**對照其他 feedback memory**:

- 跟 `feedback_context_budget_200k_250k` 性質類似 — 都是 process-level meta rule（不關特定 task），都要主動執行不依賴 surface 提醒。
- 跟 `reference_vault_paths_mac.md` 是 reference→feedback 升級：reference 講「path 是什麼」，這條講「為什麼 path 沒被讀到 / 怎麼避免再犯」。

**Trigger word**: 修修出現「你該不會又…」「為什麼這次又…」這類「重複犯錯」語氣 → 立刻停手，回頭查 memory 是否有相關 reference / feedback 條目，不是繼續往前推。
