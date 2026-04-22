# ADR-006b: Obsidian Vault ↔ Approval Queue 雙向同步（Research）

**Date:** 2026-04-22
**Status:** Research needed（**不是 Proposed**）
**Related:** [ADR-006](ADR-006-hitl-approval-queue.md)（Phase 1 核心 queue，本 ADR 為 Phase 2 研究延伸）

---

## Context

修修的寫作工作流以 Obsidian LifeOS（`F:\Shosho LifeOS`）為主力。Nakama 的 approval queue 產出 draft 後，若能讓修修在熟悉的 Obsidian 裡改稿，再把改後的 markdown 拉回 queue 繼續流程，體驗會比只用 Bridge inline edit 更自然（尤其長文）。

**核心難題**：
- 修修的 vault 在 **Windows 本機**（`F:\Shosho LifeOS`）
- Nakama agent 跑在 **Linux VPS**（`/home/nakama/`，Vultr 4GB 小機）
- 兩台機器之間的檔案同步是獨立的**基礎設施決定**，不是 Nakama 程式碼可以直接解決的事

ADR-006 原版把這個假設塞進「UI 按 export to Obsidian → Bridge 直接寫 `F:\Shosho LifeOS\Drafts\{id}.md`」，multi-model review 三家（Claude / Gemini / Grok）一致指出這是架構幻覺（見 [multi-model-review/ADR-006--CONSOLIDATED.md §2.1](multi-model-review/ADR-006--CONSOLIDATED.md)）：Linux VPS 上的 process 無法直接存取 Windows 本機檔案，背後需要同步層，而該層的**延遲、衝突、失敗**直接破壞工作流。

本 ADR 把 Obsidian 整合切出 Phase 1，**只陳列選項與風險**，待 Phase 2 Brook/Usopp 穩定後再決定。

---

## Status：Why not decided yet

**目前不決定的理由**：
1. Phase 1 Bridge inline edit 對短文（community reply、IG caption、newsletter 1500 字以內）已足夠
2. 長文（書評 5000+ 字）修修現在仍可手動從 Bridge 複製 markdown 貼進 Obsidian，再複製回 Bridge — 摩擦存在但可接受
3. Obsidian 整合真正需要處理的是**雙向 conflict resolution** 與**所有權邊界**，兩者都需要有實際使用觀察才能設計對，不能 upfront 猜
4. 三家 review 一致建議先切出 Phase 1 落地，本 ADR 執行此建議

**什麼時候會回來決定**：
- Phase 1 Brook + Usopp 全上線且跑了 2 週
- 修修累積至少 20 篇 approval 體驗，能回答「Bridge inline edit 夠不夠、什麼情境下真的想去 Obsidian」
- 此時再開 decision session，從下方 options 裡選

---

## Options（不排序，含 pros / cons）

### A. Obsidian Sync 官方服務（$8/月）

**做法**：修修訂閱 Obsidian Sync，Nakama VPS 上另開一個 vault 副本，也裝 Obsidian Sync desktop 讓雙邊自動同步。

**Pros**：
- 零自建，官方維護
- 衝突偵測內建（diff 界面可選版本）
- 雙向即時
- 不影響既有 vault 結構

**Cons**：
- VPS 要跑 Obsidian desktop（Linux 版）長駐，或找 CLI 替代（非官方）—— VPS 資源被吃
- 月費疊加（已訂閱其他服務時在意）
- Obsidian Sync 合約是個人使用，agent 自動讀寫是否違反 ToS 待確認
- Nakama agent 要等 sync 完成才能讀到修修改好的稿，sync 延遲 = 工作流延遲

### B. Git-based（vault 放 git repo）

**做法**：`F:\Shosho LifeOS\Drafts\` 作為 git submodule 或獨立 repo，修修本機 commit + push，VPS Nakama 定時 pull；VPS 寫入 draft 時 commit + push 回來。

**Pros**：
- 完全 audit（git log 天生是 audit log）
- conflict 走 git merge，工具成熟
- 免費、透明、可控
- 未來可開 GitHub Actions 做 CI 驗證 frontmatter

**Cons**：
- 修修在 Obsidian 裡要手動 commit/push，或用 Obsidian Git plugin（多一個習慣）
- 雙邊同時改同一檔 → merge conflict 要人工解
- Binary（圖片）在 git 會膨脹 repo，圖檔要 Git LFS 或 R2 外放
- 設定門檻較高（SSH key、權限）
- vault 全檔案進 repo 還是只同步 Drafts 子目錄需要選

### C. rclone + cron（VPS 拉 / 推 vault）

**做法**：修修 vault 透過 SFTP / WebDAV / OneDrive / Dropbox 暴露，VPS rclone 定時同步。

**Pros**：
- rclone 成熟，支援多 backend
- 設定一次後全自動
- 不綁特定平台

**Cons**：
- 本機要跑 SFTP server 或付費雲端中繼，增加攻擊面
- cron 粒度決定延遲（5 分鐘是一般可接受下限，但仍比即時差）
- 衝突處理 rclone 預設「新的覆蓋舊的」，無 merge 語義
- 修修電腦關機時 VPS 看到的是過期 vault

### D. Nakama 不寫 vault，修修手動下載（最簡單）

**做法**：Bridge UI 提供「Download as markdown」按鈕，修修手動存到 `F:\Shosho LifeOS\Drafts\`；反向由修修手動把改後 markdown 貼回 Bridge inline edit。

**Pros**：
- 零同步基礎設施
- 明確權責：Bridge 永遠是 source of truth，Obsidian 只是個人編輯工具
- 修修掌控何時 export / import，不會被沒注意的背景同步打擾
- Phase 1 現狀

**Cons**：
- 摩擦最高，完全手動
- 修修可能在 Bridge / Obsidian 同時改，會產生版本不一致
- 文章追蹤 weak：Obsidian 那份檔案跟 Bridge draft id 的對應要修修自己記

### E. 開 Obsidian plugin 讓 vault 直接連 Nakama API

**做法**：寫一個 Obsidian plugin，在 vault 內提供「從 Nakama 拉 draft」「推回 Nakama」按鈕，透過 HTTPS 打 `/bridge/drafts/{id}` API，檔案存 vault 內。

**Pros**：
- Vault 檔案純地端，不需任何跨機同步層
- 動作由修修顯式觸發，不會背景 race
- 可把 Bridge 的 Pydantic schema 直接映射到 plugin UI，強制欄位正確
- Plugin 可打包成社群 plugin，日後可能開源

**Cons**：
- 需寫 Obsidian plugin（TypeScript、Obsidian plugin API）—— 成本最高
- Plugin 要長期維護跟 Obsidian 版本更新
- 初期 bug 多時工作流不穩
- Plugin 與 Nakama API 的版本相容性要自管

---

## 未解問題（研究階段必須想清楚再決策）

### 權限與傳輸

- VPS 上的 Nakama agent 如何**合法且安全**訪問修修 Windows 本機 `F:\` 路徑？
- 不開放 Windows 本機 port 的前提下，單向（VPS → 修修）的 push 是否需要 polling 機制？
- SFTP / WebDAV / Obsidian Sync 各自的 auth、TLS、rate limit 差異

### Bi-directional conflict resolution

- Bridge UI 與 Obsidian 同時改同一 draft 時，誰贏？
- 基於時間戳的 last-write-wins 足夠嗎？還是需要 3-way merge？
- 前端要不要顯示「這份 draft 正在 Obsidian 編輯」的 lock 狀態？

### 所有權邊界（誰 owns 什麼）

哪些資料 Nakama-owned（Bridge 改、Obsidian 只讀）？哪些 user-owned（Obsidian 改、Bridge 跟）？

| 欄位 | 應該誰改？ | 初步判斷 |
|---|---|---|
| `title` / `slug` | 雙向 | user-owned |
| `post_content_html` | 雙向 | user-owned |
| `seo_focus_keyword` | Brook 產出後修修調 | user-owned |
| `status` | Nakama FSM 管 | **Nakama-owned**（Obsidian 不該改） |
| `operation_id` / `payload_version` | Nakama 產出 | **Nakama-owned** |
| `review_note` | Bridge 改 | Nakama-owned |

→ 這意味著 Obsidian 那份檔案應該是**payload 的子集 projection**，不是 full row dump。

### Schema 一致性

Obsidian 檔案的 frontmatter 跟 `ApprovalPayloadV1` 的對應：
- 怎麼保證修修改 frontmatter 時不會把 `schema_version` 改壞？
- Plugin / reimport 流程要有 Pydantic validate 攔截

### 同步失敗回退

- 同步層掛掉時，Bridge 仍要能 approve（不能把核心工作流綁在脆弱同步上）
- Phase 1 已內建 CLI fallback（[ADR-006 §10](ADR-006-hitl-approval-queue.md)），本 ADR 無論選哪個方案都不能讓這條 fallback 失效

---

## Decision

**Deferred**。Phase 1 走 Option D（手動下載），Phase 2 Brook/Usopp 跑穩後再開 session 評估 A-E。

**Phase 1 不受此影響**：
- Bridge UI 提供 inline edit（短文足夠）
- 長文場景下，修修手動 Bridge ↔ Obsidian 複製貼上（D 方案）
- 此 ADR 不阻塞任何 Phase 1 開工

---

## Consequences

### 正面
- Phase 1 scope 明確，不拖核心 queue 落地
- 預留設計空間，不用現在猜修修 2 週後真正想要什麼
- 讓「同步基礎設施」與「approval queue 邏輯」兩件事分開，日後選任一 option 都不需重寫 queue

### 風險
- Phase 1 若修修在 Bridge inline 改長文不順，會把怨氣累積到 Phase 2 才解
- 三個月後仍未決 → Brook 長文流程可能出現「永遠手動複製」的慣性，屆時回頭接 Obsidian 要打掉重來

### Mitigation
- Phase 1 結束時（Brook + Usopp 穩 2 週）**強制**開 session review 本 ADR
- 期間修修累積「想去 Obsidian 改但沒得改」的 case，當作 A-E 選項的決策輸入

---

## Notes

- Multi-model review 共識 → [multi-model-review/ADR-006--CONSOLIDATED.md §2.1](multi-model-review/ADR-006--CONSOLIDATED.md)
- LifeOS vault 寫入規則 → CLAUDE.md §Vault 寫入規則
- 研究時需參考：Obsidian Sync 官方 ToS、Obsidian Local REST API plugin、Obsidian Advanced URI plugin、Obsidian Git plugin
