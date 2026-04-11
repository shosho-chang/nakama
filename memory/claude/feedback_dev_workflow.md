---
name: 開發流程規範
description: 修修與 Claude 的協作開發流程——大公司角色分工，兩人認領，每個 Phase 都要走、交接要明確
type: feedback
tags: [workflow, process, collaboration]
created: 2026-04-11
updated: 2026-04-12
confidence: high
ttl: permanent
---

## 核心原則

採用大公司正規角色分工，由修修和 Claude 兩人認領。該做的事都要做，交接要清楚。目標是讓專案能對接交接、規模化、開源、商業化。

---

## 角色分工

| 角色 | 認領 | 職責 |
|------|------|------|
| **PM** | 修修 | 提需求、定優先級、最終驗收 |
| **架構師** | Claude（修修審核） | 可行性評估、技術選型、時程估算、Design Doc / ADR |
| **後端工程師** | Claude | 實作、單元測試、發 PR |
| **前端工程師** | Claude | 實作、單元測試、發 PR |
| **QA** | Claude 初測 → 修修驗收 | 測試計畫、自動化測試、邊界條件 |
| **Code Reviewer** | Claude 自審 + 修修最終 approve | PR 描述清楚，修修看得懂才 merge |
| **Tech Writer** | Claude（修修校對） | ADR、API 文件、CHANGELOG |
| **DevOps** | 修修（Claude 協助） | VPS 部署、監控；套件監控交 Franky Agent |

---

## Feature 開發流程（六個 Phase）

### Phase 1：需求定義（PM → 架構師）
- 修修提出需求（口頭、Issue、或對話中描述）
- **交棒**：修修說完需求 → Claude 進入 Phase 2

### Phase 2：評估與設計（架構師 → PM 審核）
Claude 產出：
- 可行性分析
- 時程估算（拆到小時等級）
- 技術方案（含替代方案比較）
- ADR 或 Design Doc（重大決策時）

**技術選型要求**：上網搜尋最新資訊，綜合論壇與意見領袖討論，不只看官方文件。AI 領域變化極快（如 MemPalace 橫空出世），選型必須基於當下最新生態。

- **交棒**：Claude 提交評估 → 修修審核確認 → 才進入 Phase 3

### Phase 3：開發（工程師）
Claude 執行：
- 開 feature branch
- 實作（分合理的 commit）
- 寫測試（至少 happy path）
- 發 PR（含完整描述）
- **交棒**：Claude 發 PR → 通知修修「PR 已開，請 review」

### Phase 4：審查（Code Review + QA）
- Claude：跑測試、自查程式品質
- 修修：Review PR、提問或要求修改
- **交棒**：修修 approve → 進入 Phase 5

### Phase 5：合併與部署（DevOps + Tech Writer）
- 修修：merge PR → VPS 部署
- Claude：更新文件、CHANGELOG
- **交棒**：部署完成 → 修修進行驗收

### Phase 6：驗收（PM + QA）
- 修修：功能驗收、實際使用測試
- 有問題 → 開 Issue 回到 Phase 3
- **交棒**：修修確認通過 → 結案

---

## 交接原則

1. **每個 Phase 結束，負責人明確交棒** — 說清楚「我做完了，交給你，需要你做 X」
2. **不跳 Phase** — 再小的改動至少走 Phase 2（一句話評估也算）和 Phase 4
3. **所有決策可追溯** — 重大的寫 ADR，一般的寫在 PR / commit 裡
4. **修修仍在熟悉流程** — 如果對話中有跳 Phase 的情況，Claude 要主動提醒

---

## 開發慣例（沿用既有）

- 大型功能用 feature branch → PR → merge
- 規劃階段使用 plan mode，確認方向後才開始實作
- 每個 Phase 完成後立即 commit，不要一次大 commit
- commit message 格式：`feat:` / `docs:` / `fix:` 前綴，中文描述
- 重要架構決策寫 ADR（`docs/decisions/ADR-XXX-*.md`）
- **記憶檔案更新後必須 commit & push** — `memory/claude/` 是透過 git 跨平台共用的，不 push 其他機器看不到

---

## 不重複造輪子

開發新功能前，先搜尋現有的 Claude Code Skills、Plugins、MCP Servers 和開源套件，優先使用已有的成熟方案，不自己重寫。

**Why:** AI 生態系發展快，很多能力已經有人做好了（Obsidian MCP、Playwright MCP、PubMed 工具等）。自己寫不但慢，還要自己維護。

**How to apply:** Phase 2 評估時，技術方案必須包含「是否有現成 skill/plugin/MCP/套件可用」的調研。能用現成的就用，只在沒有合適方案或整合成本過高時才自己寫。

---

## 套件與依賴監控

交由 **Franky Agent**（System Maintenance）定期執行：
- 套件版本更新檢查
- CVE 漏洞掃描
- API key 有效性驗證
- 系統健康檢查

**Why:** AI 生態變化極快，依賴可能快速過時或出現安全漏洞，需要自動化監控而非人工追蹤。

**How to apply:** 開發過程中引入新依賴時，確認 Franky 的監控清單會涵蓋到。
