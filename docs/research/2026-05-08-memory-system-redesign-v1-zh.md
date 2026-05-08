# 記憶系統重新設計 v1 — 跨平台、多 agent（Claude + Codex）

**狀態：** Draft v1，待 panel review
**日期：** 2026-05-08
**作者：** Claude Opus 4.7（1M context）— 單一 LLM 草稿，Codex GPT-5 + Gemini 2.5 Pro 的 panel 審查待跑
**Repo：** nakama (E:\nakama)
**相關：**
- 既有基建：`shared/memory_maintenance.py`（SQLite-backed，但只作用於舊 schema）
- 觸發來源：`memory/claude/feedback_conversation_end.md`
- Schema 並存：`memory/shared.md` + `memory/agents/{robin,franky}.md`（舊）vs `memory/claude/*`（新）
- 環境因素：ADR-021 + ADR-022 剛 ship 完，memory commit 量在 5/7-5/8 達到高峰

---

## 背景

### 這個 repo 為什麼要記憶系統

修修是健康與長壽 AI agent 系統的單人開發者，跨 Windows + Mac 同時開多個 Claude Code 視窗，加上 Sandcastle 雲端沙箱平行跑 sub-agent。記憶系統的存在是**讓 AI 在跨 session 時有連續性** — 記住 user 是誰、專案決策、行為回饋、外部系統指引。

另外，**Codex（透過 ChatGPT 認證的 GPT-5）即將上線**作為這個 repo 的第二個協作 agent。所以記憶系統至少要服務 **2 個 AI agent**（Claude + Codex），跨 **2 個桌面平台**（Win + Mac）跟**雲端 sandbox 執行**。

### 問題盤點（2026-05-08 audit 數據）

**規模失控：**
- `memory/claude/`：**297 個檔案**（feedback: 155 / project: 114 / user: 4 / reference: 23）
- `memory/claude/MEMORY.md`：295 行（system prompt 警告 200 行 truncate 上限 — 已超過）
- 過去 30 天動 `memory/` 的 commit：**264 個**（平均 9 個/天）
- 每個 commit 都走 PR + squash-merge → 每次都跑 CI
- GHA Actions 配額已用 90%（2700/3000 min），memory 相關 CI 是非小的貢獻

**兩套 schema 並存：**
- *舊 schema*（`memory/shared.md`、`memory/agents/{robin,franky}.md`）：frontmatter 有 `type / agent / tags / created / updated / confidence / ttl` — 在 `shared/memory_maintenance.py` 有 SQLite 後端 `expire`/`archive`/`stats` 維護機制
- *新 schema*（`memory/claude/*`）：frontmatter 只有 `name / description / type` 三欄 — **無 expire、無 confidence、無 TTL、無維護**

新 schema 是 AI agent 主要讀寫的，但**沒有衰減機制 → 累積無上限**。

**行為層問題（觸發點）：**

既有 feedback `feedback_conversation_end.md` 教 Claude：當看到「清對話」（或「對話結束」等類似訊號）時：
1. 從 session 挑出值得保留的資訊
2. 寫入 `memory/claude/`
3. 更新 MEMORY.md
4. `git commit && git push`
5. 回覆「記好了」

這個 trigger 產生的後果：
- 每次 session-end 一個 PR
- 每天跨各視窗約 1-3 個 session-end memo
- Push 到 worktree 當下所在的 branch（沒有 branch 紀律）
- **90%+ 是 `project_session_*` handoff 筆記**，過 7 天就過期（git log + GitHub issue 早就保留）

**memory commit 樣本：**
```
17cef58 docs(memory): 5/8 ADR-021 complete + design system v1 ship handoff (#494)
636a2c3 docs(memory): 早報 2026-05-06 overnight (#439)
6872ef0 docs(memory): 收工 2026-05-05 late (#435)
99f8d4a docs(memory): 收工 2026-05-05 evening (#425)
13d5332 docs(memory): 收工 2026-05-05 (#416)
4cdce43 docs(memory): 收工 2026-05-04 夜 + overnight (#375)
b28343e docs(memory): 收工 2026-05-04 晚 (#364)
... [過去一個月 >20 筆]
```

這些 commit **不影響程式碼行為**，是 session 日誌。PR review 跟 CI 在它們身上是純 overhead，零品質訊號。

**多視窗 stash 漂移（今天親身觀察）：**

跑這個重新設計 session 時，另一個 Claude 視窗 stash 了 `wip-mid-bench-2026-05-07`，把 user 的 CI-fix stash 從 `stash@{0}` 擠到 `stash@{1}`。「先 stash 再 clear」的自動觸發加上「pop stash@{0}」的假設，產生**錯誤 stash pop 事件**。同樣的「shared mutable state 假設只有一個視窗」問題也適用於 MEMORY.md 編輯。

### 限制條件

- **C1 — 跨平台同步**：Win + Mac 之間記憶必須無人工介入同步。目前靠 repo 內 + git。**repo 儲存不可妥協**。
- **C2 — 多 agent**：Codex 即將上線；既有記憶不應被 agent-specific feedback 污染（如「Claude 容易過度敘述」對 Codex 不適用；「Codex 跳過錯誤處理」對 Claude 不適用）。
- **C3 — Sandcastle**：雲端 sub-agent 寫記憶後 git merge 回主 repo。衝突解決必須 90%+ 不需手動。
- **C4 — 單人開發**：沒有第二個人類 reviewer。memory 走 PR review 是零品質訊號。main 有 branch protection（無法直接 push）。
- **C5 — 美學 / 訊噪比**：297 個檔案含 155 個 feedback，搜索品質下降。低訊號 `project_session_*` 淹沒重要記憶。
- **C6 — 向後相容**：既有 297 檔案要繼續可用。**禁止 big-bang 遷移**。

---

## 設計決策

### 原則 1 — 記憶 ≠ session 日誌

記憶捕捉**跨 session 不變的事實**：user 是誰、專案決定了什麼、有哪些外部系統、要重複或避免哪些行為模式。Session 日誌（今天做了什麼、debug context、handoff 筆記）屬於 **git commit + GitHub issue**，不是 memory 檔案。**這個概念切割是後續所有決策的基礎**。

**含意**：`project_session_*` 檔案是 anti-pattern。它們會被 archive（不刪除 — git 歷史保存），未來禁止再寫。

### 原則 2 — Append-only 檔案、生成的 index

每筆記憶是一個獨立檔案有 frontmatter。`MEMORY.md` 變成**生成 artifact**，由 `memory_maintenance.py reindex` 從 frontmatter 掃描重建。**任何 agent 都不直接編輯 MEMORY.md**。

**含意**：消除多視窗主要衝突點（平行編輯 MEMORY.md）。衝突只發生在個別記憶檔，這在每個 agent 寫自己檔案時很罕見。

### 原則 3 — Memory commit 跳過 PR

記憶是文件，不是程式碼。單人 dev repo 上 PR review 對文件零品質訊號。memory-only commit 跑 CI 純 overhead。

**選擇 — Option A（`memory-trunk` branch + 定期 fast-forward）：**

```
memory commit 進 `memory-trunk` branch
  → 直接 push（無 PR、無 CI）
  → 每週：`git checkout main && git merge --ff-only memory-trunk`
  → main 仍是正典；memory-trunk 是寫入面
```

為什麼不選 Option B（GitHub branch protection 用路徑豁免）：技術可行但設定複雜且脆。memory-trunk 簡單。

為什麼不選 Option C（CI 加路徑 filter）：只解 CI 成本，沒解 PR review 噪音。CI 成本部分已用 `paths-ignore` 在這個 PR 解決。

**注意**：這代表 memory commit 不會出現在 main git log 直到每週 fast-forward。trade-off：main log 乾淨 vs 略延遲可見性。對單人 dev 可接受。

### 原則 4 — 跨 agent 目錄佈局

```
memory/
├── shared/                     # 跨 agent — Claude + Codex 都讀都寫
│   ├── user/                   # 修修的事實（偏好、角色、語言）
│   ├── project/                # nakama 專案的事實（決策、scope、deadline）
│   ├── reference/              # 外部系統指引（Linear、Slack、Grafana、GitHub）
│   └── decision/               # 結晶化的設計決定（補 ADR — 較小 scope）
├── claude/
│   └── feedback/               # Claude 專屬行為調整（「Claude 容易過度敘述」）
├── codex/
│   └── feedback/               # Codex 專屬行為調整（「Codex 跳過錯誤處理」）
├── _archive/                   # 自動 rotate 的舊檔（project_session_*、過期記憶）
│   └── YYYY-MM/                # 年月分籃
├── INDEX.md                    # 生成 — 不可手動編輯
└── SCHEMA.md                   # Schema 文件，兩個 agent 都讀
```

**讀規則：**
- Claude 讀：`shared/**` + `claude/**`
- Codex 讀：`shared/**` + `codex/**`
- 兩個都不讀 `_archive/**`

**寫規則：**
- Claude 寫：`claude/**` 自由；`shared/**` 謹慎（跨 agent 影響）
- Codex 寫：`codex/**` 自由；`shared/**` 謹慎
- 兩個都不寫 `_archive/**`（只有 `memory_maintenance.py` 可寫）
- 兩個都不寫 `INDEX.md`（只有 `memory_maintenance.py reindex`）

**向後相容：** 既有 `memory/claude/*.md` 在過渡期仍可讀。新寫入走新 layout。**漸進遷移** — `memory_maintenance.py migrate` 走舊檔案，建議目標路徑，確認後套用。

### 原則 5 — Schema 升級（additive 向後相容）

```yaml
---
name: ...                                  # 必填（既有）
description: ...                           # 必填（既有）
type: feedback | user | project | reference | decision   # 既有 + 新增 'decision'
visibility: shared | claude | codex        # 新 — 新檔必填；舊檔依路徑推
confidence: high | medium | draft          # 新 — default 'medium'
created: 2026-05-08                        # 新 — 自動填
expires: 2026-08-08 | permanent            # 新 — type=project 必填（default created+30d）
                                           #     ；user/reference/feedback/decision default 'permanent'
tags: [worktree, multi-agent]              # 新 — optional，搜尋輔助
---
```

`SCHEMA.md` 用範例 + 規則記錄這套。Claude 的 CLAUDE.md 跟 Codex 的等價檔都引用它。

### 原則 6 — Trigger 改革

三層，取代 `feedback_conversation_end.md` 的單一自動觸發：

| 層 | 何時 | 動作 |
|-----|------|------|
| **L1 — 明確 user 觸發** | User 說「記下這個 / save / remember X / update memory」 | 立刻寫入相關檔案。明確指定路徑。|
| **L2 — 強訊號自動觸發** | User 明確 correction（「不要 X，要 Y」）<br>OR 強烈 validation（「對，就這樣 / 這個方向繼續」）<br>OR 發現新的 user/project 事實（不是 session 細節） | 寫入 feedback / shared 檔。在對話中 inline 確認寫了什麼。|
| **L3 — 禁止自動觸發** | 「清對話 / session 結束 / 對話結束」單獨出現 | **不**自動寫。改成：列出候選記憶（「學到 3 件可記的事: …」），問 user 要存哪幾個。User 決定才寫。|

`feedback_conversation_end.md` 改寫成明確捕捉 L3。

### 原則 7 — 維護週期

擴充 `memory_maintenance.py`（目前只有 SQLite）加 file-based commands：

| 指令 | 觸發 | 動作 |
|---|---|---|
| `reindex` | `memory/**` commit 前 hook + 每天 cron | 從 frontmatter 掃描重建 `INDEX.md` |
| `expire` | 每天 cron | 將 `expires` 過的檔案移到 `_archive/YYYY-MM/` |
| `compact-sessions` | 手動或每週 cron | 將所有 `project_session_*` 移到 `_archive/`（清舊） |
| `dedupe` | 手動每季 | LLM-driven 在 feedback 檔找近重複 |
| `migrate-old-to-new-layout` | 手動一次性 | 走舊 `memory/claude/*` → 建議新 layout 路徑 → 確認後套用 |

### 原則 8 — Worktree 紀律（獨立但相關）

Memory 寫入在以下**之一**發生：
- 專屬的 `E:\nakama-memory` worktree 在 `memory-trunk` branch（長期），或
- 當前 task 的 worktree（如果記憶寫入是當前 task 的 critical-path） — 但 commit 必須 cherry-pick / rebase 到 memory-trunk 才能 push。

Memory 寫入**永遠不在** `E:\nakama`（bare-ish 主 repo）。

這是 CLAUDE.md 加「Worktree 紀律」整節的一部分（獨立子決定，後面討論）。

---

## CLAUDE.md 重構

目前 CLAUDE.md（5/7 baseline，195 行）：結構合理但有累積 cruft。建議改動（delta only — 完整檔案在 `docs/research/2026-05-08-claude-md-v2-draft.md` 如 user 同意再寫）：

### 加 3 節

1. **`## Memory 寫入紀律`** — 取代埋在 `feedback_conversation_end.md` 的 trigger，改成明確規則：
   - 三層 trigger（L1 / L2 / L3）
   - Schema 引用（`memory/SCHEMA.md`）
   - Branch 規則（commit 進 `memory-trunk`，永遠不在 feature branch）
   - Worktree 規則（永遠不在 `E:\nakama` 寫記憶）

2. **`## 工作面紀律 (Worktree)`** — 把 2026-05-08 清理 session 的教訓制度化：
   - `E:\nakama` 是 metadata-only（永遠不是工作面）
   - 每個 task 用 sibling worktree（`E:\nakama-<topic>`）
   - subagent dispatch 走 Sandcastle（default）或本機 `isolation: worktree`
   - Stash 紀律（用 `-m`，永遠不 `pop stash@{0}` — 用 message 找回）

3. **`## Multi-agent 協作 (Claude + Codex)`** — 對 shared state 設立期望：
   - 各 agent 讀範圍（Claude: shared+claude；Codex: shared+codex）
   - 各 agent 寫範圍（自己 subdir + 謹慎寫 shared）
   - 衝突解決（append-only 檔案 + `reindex` 重建 INDEX.md）
   - Schema 真理來源：`memory/SCHEMA.md`

### 瘦身

- **行 114-121（AFK / Sandcastle）** — 目前重複引用 2 個 memory 檔。壓成 3 行：
  > 並行/AFK dispatch default = Sandcastle（雲端隔離）。本機 `isolation: worktree` 只用於單一短任務且能盯住整段。詳見 `memory/shared/decision/sandcastle-default.md`。

- **行 100-110（Vault 寫入規則）** — 移到 `docs/agents/vault-writes.md`。CLAUDE.md 留一行 pointer。

### 移除

- 重複 link ADR-001（一次就夠，不要三次）。
- `Agent skills` 段（行 176-190） — engineering skill 的 routing 在那些 skill 自己的 metadata 裡，CLAUDE.md 不必列。「PR-first 文化」留在 `## 工作方法論` 一行。

### 結果

CLAUDE.md 從 195 行 → 約 140-160 行，但**多 3 個實質新節**。淨訊號密度大幅提升。

---

## 分階段執行

### Phase 0 — 立即止血（今天，這個 PR）

在 `chore/memory-design-and-ci-fix` 落地：
1. CI workflow 改善（已起草） — `paths-ignore: memory/**` 阻擋未來 memory 的 CI 成本
2. 這份 design doc → `docs/research/`
3. Codex + Gemini panel audit（這個 PR 把 audit 一併存進來作為 research artifact）
4. 更新 `feedback_conversation_end.md`（L3 禁止 trigger）
5. 新 CLAUDE.md 段落（3 個新節）
6. 新 `memory/SCHEMA.md`
7. 空 `memory/shared/{user,project,reference,decision}/` 跟 `memory/codex/feedback/` 目錄殼（含 `.gitkeep`）

**不動：** 既有 297 檔案在 `memory/claude/*`（過渡期零遷移風險）。

### Phase 1 — 基建（接下來 1-2 週）

1. 擴充 `memory_maintenance.py` 加 file-based `reindex` / `expire` / `compact-sessions`
2. 設每天 cron（既有 nakama VPS）跑 `reindex` + `expire`
3. Pre-commit hook：memory commit 時自動跑 `reindex`
4. 建 `memory-trunk` branch + 每週 fast-forward 到 main

### Phase 2 — 遷移（第 3-4 週）

1. `memory_maintenance.py migrate` 走舊檔，建議目標路徑
2. User 分批確認；檔案移到新 layout
3. `MEMORY.md` 從新結構重新生成
4. 舊 `memory/claude/MEMORY.md` 變 alias / deprecation pointer

### Phase 3 — Codex onboarding（Codex 整合啟動時）

1. Codex 等價於 CLAUDE.md（或 system prompt）的設定讀 `memory/SCHEMA.md`
2. Codex 只寫 `memory/codex/feedback/` 跟（謹慎地）`memory/shared/`
3. 前 2 週監控 inter-agent 摩擦，看狀況調整

### Phase 4 — 清理（第 2 個月+）

1. `dedupe` 跑 feedback 檔（155 → 估計 60-80）
2. `compact-sessions` 把所有 `project_session_*` archive
3. MEMORY.md 最終狀態：~80-100 行 high-signal active 記憶

---

## 開放決策（panel：請對這些 push back）

### 決策 A — `memory-trunk` branch vs path-based exemption

選擇：`memory-trunk` branch。替代：GitHub branch protection 用路徑豁免 `memory/**`。

請 push back：複雜度 vs 維護成本；雙 trunk 模式可能造成混淆；如果 `memory-trunk` 跟 main 偏離超過 fast-forward 怎辦。

### 決策 B — Trigger L3（「清對話」→ confirm-only）

選擇：Claude 列候選，問 user 確認。替代：完全禁止自動寫（只有 L1 明確 trigger + L2 對話中 correction）。

請 push back：confirm 步驟真的減少噪音 vs 增加 friction？User 會不會 rubber-stamp 每個 confirm？

### 決策 C — 跨 agent shared/ 目錄

選擇：`memory/shared/` 兩個 agent 都讀寫。替代：完全 agent 隔離（無共享記憶；跨 agent context 走明確 docs）。

請 push back：shared 寫入是否造成隱式 coupling？一個 agent 的詮釋是否污染另一個的 context？

### 決策 D — Schema additive vs 替換

選擇：additive（既有檔留著，新欄位漸進）。替代：schema-version 欄位；硬切換到 v2。

請 push back：軟 additive 是否造成長期 schema 熵增；vs 硬切換是否風險破壞 297 既有檔。

### 決策 E — Project memory expiry default（30 天）

選擇：`type=project` default `expires: created+30d`。替代：每檔判斷；無自動 expiry。

請 push back：30 天太兇（有些 project 記憶覆蓋整個 ADR cycle，週至月）還是太鬆（多數是 session-handoff 垃圾）？

### 決策 F — `_archive/` 保留

選擇：永遠保留（git history 是正典，archive 只是退出 active search）。替代：6 個月後硬刪（repo 較小）。

請 push back：`_archive/` 多年累積的影響；保留期是否該依 type 區分？

### 決策 G — Codex 細節

開放：Codex（GPT-5 透過 ChatGPT）具體怎麼整合？CLI 呼叫？Subagent dispatch？File-watch model？這個設計假設 Codex 是「可設定的 agent，能告訴它讀這些路徑、寫那些路徑」。如果 Codex 實際整合模式不同，跨 agent layout 可能要調整。

請 push back：multi-agent 假設的真實性；具體 Codex 整合模式。

---

## 我想要 panel 挑戰的具體點

1. **`memory-trunk` 是不是過度工程？** path-based branch protection 豁免是不是用更少 custom 基建達同樣結果？

2. **L3 confirm 模式跟「不要自動寫」實質差別？** User 會不會 rubber-stamp 每個 confirm？是不是該硬禁用？

3. **297 個檔真的是問題嗎，還是 155 個 feedback 才是問題？** 也許 project + reference 沒事，只有 feedback 需要 dedup。**精準修 vs 系統重設計**。

4. **多 agent 假設（Codex 來）真的 load-bearing 嗎？** 如果 Codex 整合方式完全不同（例如當成 Claude 的 sub-agent 而非獨立 agent），shared/codex/claude 切割還有意義嗎？

5. **`_archive/` 增加價值還是只是延後刪除？** 如果記憶從 archive 不會回來，何不直接刪？git history 反正保存。

6. **`shared/decision/` 跟 `docs/decisions/`（ADR）重疊不健康？** 一個小設計決定該去小 ADR 還是 shared/decision 記憶？這條界線模糊。

7. **Phase 0（這個 PR）太貪心？** 可以拆 2 個 PR — 一個 CI fix，一個設計。雙目的 PR 輕度違反「一個 concern 一個 PR」。

8. **新 CLAUDE.md 段落會被讀嗎？** CLAUDE.md 一直長，新段落 AI agent 經常掠過。新內容是不是該放 linked docs 而非主檔？

---

## 預先標記的失敗模式

1. **`memory-trunk` 偏離超過 fast-forward**：如果有人不小心從 main 合進 memory-trunk（或在 memory-trunk commit 非 memory 內容），每週 fast-forward 失敗。緩解：memory-trunk pre-commit hook 拒絕 `memory/**` 之外的改動。

2. **遷移過程中舊 agent 讀舊 layout**：Phase 2 期間，舊 session 可能還在讀 `memory/claude/*.md` 但新 寫入到 `memory/shared/*`。緩解：過渡期保留 symlink？或 CLAUDE.md 明確說「過渡期兩個路徑都讀」。User-facing 複雜度不低。

3. **Reindex race condition**：兩個平行 agent 都跑 `reindex` → 各自產生略不同的 MEMORY.md → push 衝突。緩解：reindex 用 lockfile；如果鎖住，跳過（下次跑會收）。

4. **如果 backward-compat 太鬆，schema 熵增**：6 個月後檔案 frontmatter 可能不一致。緩解：`memory_maintenance.py validate` linter 在 cron 跑，drift 報告寫進 status memory；pre-commit hook 警告 drift。

5. **Sandcastle 寫回衝突**：平行 sub-agent 在不同 sandbox 寫記憶；merge 回。append-only 檔案大致 OK 除了 shared/ 寫入（罕見）。緩解：shared/ 寫入走「propose-then-commit」需要 user 仲裁。

---

## 反向測試

如果**反過來**設計，會不會聽起來合理？

- **反向 1**：「記憶該包含 session 日誌，因為 AI 在新 session 開始時需要它們快速 bootstrap context。」→ 如果你優化 AI bootstrap context 是 plausible。但經驗顯示 90%+ session log 在下個 session 開頭讀一次後再也不讀。git log + 開放 issue 做這個工作做得更好。

- **反向 2**：「不要切割跨 agent 記憶；保持單一池 — agent 從 cross-pollination 受益。」→ 如果 agent profile 相似 plausible。但 Claude 專屬 correction（「不要過度敘述」）會混淆 Codex；Codex 專屬 correction（「不要跳過錯誤」）會混淆 Claude。shared/ 目錄捕捉真正跨 agent 的事實（user、project）而不污染 agent 專屬行為指引。

- **反向 3**：「Memory commit 走 PR 才能保證品質。」→ 團隊環境 plausible。單人 dev 沒第二個人類 review — PR 零品質訊號，只有 CI 成本 + flow friction。

反向都不勝出。設計對主要替代方案 robust。

---

## 致謝

這個設計形塑於今天實際的 debug session — 觀察到 wrong-stash pop、多視窗 MEMORY.md 修改、13 個殭屍 agent worktree、user 既有的 `feedback_conversation_end.md` trigger 模式。Codex 跟 Gemini audit 會作為獨立檔案附加。
