# ADR-028 Finalization Plan

**Date:** 2026-05-23
**Author:** Claude Opus 4.7 + 修修
**Status:** Draft（待修修拍板後執行）
**Related:** [ADR-028](../decisions/ADR-028-vault-layout-consolidation.md), Issues #624 / #625 / #626

---

## 0. 起點

ADR-028 v2 凍結於 2026-05-19，分三 Phase 落地。在進行此 plan 前的真實狀態：

- **Phase A**（code prep）：全 merged — #616 / #621 / #629 / #630
- **Phase B**（vault big-bang）：**實質完成、artifact 沒 commit**
  - 12 sub-ops × 103 ops 全跑過（manifest in `.tmp/adr028-migration-manifest.json`）
  - 執行腳本還在 `.tmp/`（`adr028_inventory.py` / `phaseb_safe_moves.py` / `flatten_attachments.py` / `inbox_sort.py` / `migrate_files.py` / `migrate_files_resume.py` / `fix_missed_ref.py`）
  - PR #654 / #656 是 code 半邊改動；vault 半邊的 manifest **未 commit 到 `data/migrations/`**
  - #624 issue 沒 close
- **Phase C**：完全沒做
  - Vault `CLAUDE.md` §Directory Model 仍是 2026-04-25 版本
  - `scripts/vault_layout_audit.py` 仍是 stub
  - Franky cron 沒 wire
  - `docs/VAULT-LAYOUT.md` §7 drift 仍 `[待修]`
  - Robin `Inbox/kb` deprecation 殘留未清

---

## 1. 真實 gap 盤點

### 1.1 Vault 實況 vs ADR-028 §3 Phase B 12 sub-ops

| # | Sub-op | Manifest ops | Vault 實況 | 結論 |
|---|---|---|---|---|
| 1 | Nami/Notes/* → AgentOutputs/nami/notes/ | 2 | `AgentOutputs/nami/notes/` 存在、`Nami/` 已刪 | ✅ |
| 2 | AgentReports/franky/* → repo data/agent_reports/franky/weekly/ | 1 | `data/agent_reports/franky/weekly/` 有檔 | ✅ |
| 3 | AgentReports/dev-backlog.md → repo | 1 | `data/agent_reports/franky/dev-backlog.md` 在 | ✅ |
| 4 | KB/Wiki/Outputs/2026-04-27-seo-acceptance/ → AgentOutputs/brook/seo-audit/2026-04-27/ | (含於 9) | `AgentOutputs/brook/seo-audit/` 存在 | ✅ |
| 5 | KB/Wiki/Outputs/style-extractor-prd-draft.md → repo docs/prds/ | (含於 9) | `docs/prds/` 已 merge | ✅ |
| 6 | Case Studies/* → repo docs/case-studies/ | 1 | `docs/case-studies/` 已建 | ✅ |
| 7 | Incidents/2026/04/* → repo docs/incidents/ | 1 | `docs/incidents/` 已建 | ✅ |
| 8 | Inbox 重構 + 建 books/papers/snapshots/ | 16 | `Inbox/{web,books,papers,snapshots}/` 全在；`Inbox/kb/` 已刪 | ✅ |
| 9 | KB/Attachments/{inbox,pubmed}/* → flat | 7 | `inbox/` bucket 已刪、`pubmed/` bucket **仍有 May 22-23 新檔案** | ⚠️ **GAP — VPS deploy 落後** |
| 10 | Files/ Cat A → KB/Attachments/{slug}/ + 改 ref | 35 | `Files/` 已刪、9 個 markdown 已改 ref | ✅ |
| 11 | Files/ Cat B → Attachments/journal-pasted/{YYYY-MM}/ + 改 7 Journal ref | 34 | `Attachments/journal-pasted/` 5 個月份目錄在 | ✅ |
| 12 | 大量資料夾刪除（recycle bin） | 3 | 全清 | ✅ |

**Total: 11/12 sub-ops done, 1 partial gap.**

### 1.2 §6 / sub-op 9 GAP — KB/Attachments/pubmed/ 殘留

`KB/Attachments/pubmed/` 內 12 個檔案 mtime 5/22-5/23，是 Robin PubMed daily digest（05:32 台北）寫入。

- **Code 已修**：`agents/robin/pubmed_digest.py:208-212` 設定 `attachments_abs_dir = vault / "KB/Attachments"`（無 `pubmed/` prefix）— #656 已 merge。
- **VPS deploy 未追上**：5/21 19:48 #656 merge 後，5/22 + 5/23 daily cron 仍寫到 `pubmed/`，代表 VPS 還在 pre-#656 code。

**處置**：
1. 修修 trigger VPS deploy（`ssh nakama-vps && cd /home/nakama && git pull && systemctl restart` 之類，**我無權直接做**）
2. Deploy 完成後，跑一次 `flatten_attachments.py --resume` 把 `pubmed/` 殘留搬到 flat
3. 寫到 `data/migrations/2026-05-23-pubmed-flatten-residual.json` 並 commit

### 1.3 §7 drift table 待翻轉

`docs/VAULT-LAYOUT.md` §7 內 `[待修]` 條目對照：

| Drift entry | 真實狀態 | 該翻 |
|---|---|---|
| D-files-pending | Files/ 已清 | `[已修]` |
| D-agentoutputs-pending | AgentOutputs/ 完成 | `[已修]` |
| D-inbox-pending | Inbox/web/books/papers/snapshots/ 完成 | `[已修]` |
| D-promotion-attachments | PR-A1 #616 已 merge | `[已修]` |
| D-audit-stub | stub 仍在 | **保留 `[待修]`，PR-C1 完成才翻** |
| D1 (Concept dispatcher) | 未做（out of scope） | 保留 |
| D2 (Entity v1 frozen) | 未做（已接受） | 保留 |
| D3 (KB/index.md unmanaged) | 未做 | 保留 |
| D-unicode-norm | 未做（已接受） | 保留 |

### 1.4 Robin Inbox/kb 殘留 refs

實質寫入路徑已切（`agents/robin/agent.py:56` `inbox_path` default = `Inbox/web` per #630），但下列尚有字串/註解殘留：

**需移除**：
- `agents/robin/README.md:15` —「掃描 Inbox/kb/ 中的新檔案」
- `agents/robin/README.md:50` —「把檔案放入 Obsidian vault 的 Inbox/kb/」
- `agents/robin/CONTEXT.md:10` —「Reader 翻譯按鈕產出 Inbox/kb/{slug}-bilingual.md」
- `ARCHITECTURE.md:89,136` — 舊架構描述
- `CONTENT-PIPELINE.md:116,117` — pipeline 表格

**屬於 docstring example，影響低，建議同 PR 一起更新**：
- `agents/robin/reading_context_package.py:376,428` — docstring 範例 `inbox:Inbox/kb/foo.md`

**圖表 + task prompt，下一輪可改**：
- `docs/diagrams/vault-ingest-flow.md:33,90-91,100,154`
- `docs/task-prompts/N509-reading-source-registry.md:53,91,128`

**Drift table 內的歷史引用，不動**：
- `docs/VAULT-LAYOUT.md:353,428-430,438`

### 1.5 Vault CLAUDE.md replacement scope

Draft (`docs/vault-claude-md-replacement-draft.md`) 只 spec 替換 §2 Directory Model（vault CLAUDE.md 行 22-53）。但偵察發現：

- §0 Memory 仍指向 `.claude/memory/MEMORY.md`（vault 沒此目錄）— **draft 沒處理**
- §3 Permissions table 第 70 行仍有 `Schemas/` 列 — **draft 沒處理**
- §3 Permissions 沒移除 `Files/` 反映（line 52 在 §2，但 §3 沒列 Files/）

**處置**：嚴格照 draft 只動 §2，但 §3 + §0 stale 條目同 PR 順手清（要附 commit 紀錄 sha256 before/after）。對 `Schemas/` row 直接刪、§0 用 vault-內 fallback。

---

## 2. 工作拆解

### Job A — 補 #624 artifact + close issue

**範圍**：補建 `data/migrations/2026-05-20-vault-cleanup/`，記載 Phase B 真實執行歷史

**動作**：
1. 從 `.tmp/` 把 manifests 搬出來、commit 到 `data/migrations/2026-05-20-vault-cleanup/`：
   - `manifest.json` (來自 `.tmp/adr028-migration-manifest.json`)
   - `inventory.md` (來自 `.tmp/adr028-vault-inventory.md`)
   - `scripts/` (副本：`adr028_inventory.py`、`phaseb_safe_moves.py`、`flatten_attachments.py`、`inbox_sort.py`、`migrate_files.py`、`migrate_files_resume.py`、`fix_missed_ref.py`）
   - `README.md` 記錄：執行日期、PR 對映（#654 / #656）、12 sub-ops 完成度（11 ✅ / 1 ⚠️ pending VPS deploy）
2. `.tmp/` 內這幾個檔案 → 不刪，但 README 標明 canonical 已搬走
3. close GitHub #624，body 引用新 manifest 路徑 + 1 個 follow-up issue 連結（VPS deploy + pubmed flatten residual）
4. 開 follow-up issue：「VPS deploy pickup #656 + flatten pubmed/ residual」

**Worktree**：`E:/nakama-adr028-c624` (branch `chore/adr028-c624-manifest-commit`)
**PR 大小**：~120 lines 新增（manifest JSON 920 行不算 review-friendly，但是 artifact）
**Risk**：低 — 純 doc/artifact 沉澱

### Job B — PR-C1（issue #625）

**範圍**：audit 腳本完整實作 + vault CLAUDE.md replacement + Franky cron wiring

**Worktree**：`E:/nakama-adr028-c1` (branch `feat/adr028-c1-audit-and-claude-md`)
**PR 大小估計**：~600 lines code + ~100 lines tests + ~50 lines doc

**拆 4 個 commit**：

**B1 — Audit script 完整實作**
- `scripts/vault_layout_audit.py`：
  - 移掉 STUB header + warning
  - `audit_folder_diff` — 從 VAULT-LAYOUT.md §2 fenced code block parse 出 declared tree、walk vault 一層、回報 orphan + missing
  - `audit_code_path_diff` — grep `agents/` `shared/` `gateway/` `thousand_sunny/` 找 `Path("KB/...")` / `"Inbox/..."` literal、跟 §3 producer/consumer matrix 對照、回報未登記的路徑
  - `audit_marker_violations` — grep `Projects/*.md` 找 `%%agent-(\w+)-([\w-]+)-(start|end)%%`、驗 §4 Pattern A registry、check start/end pair 平衡、check marker 不在 human-only section
  - `audit_drift_status` — parse §7 drift 條目，對每條跑對應 check：
    - D1: grep textbook-ingest Phase B code 是否走 dispatcher
    - D3: `KB/index.md` 條目數 vs `KB/Wiki/Sources/*.md` 條目數差距
    - D-promotion-attachments: `shared/promotion_commit.py` 有沒有 attachment migration logic
  - Unicode NFC normalize 所有路徑比對（per §7 D-unicode-norm 風險）
- `tests/scripts/test_vault_layout_audit.py`：
  - Fixture vault（tmp_path）+ fixture VAULT-LAYOUT.md
  - 各 audit_* 對 fixture 跑 → 預期 finding 對得起來
  - End-to-end run_audit on real (clean) vault → 期望 0 error

**B2 — vault CLAUDE.md replacement（vault 寫入，非 repo PR）**
- `E:\Shosho LifeOS\CLAUDE.md` §2 Directory Model（line 22-53）整段替換成 draft 內容
- §3 Permissions table 刪除 `Schemas/` 列（line 70）
- §0 Memory 路徑 `.claude/memory/MEMORY.md` → 改成 `E:\nakama\memory\claude\MEMORY.md`（指 repo canonical）or 直接刪整段（vault 端不維護）
- **不在 PR**：vault 不是 git-tracked。**PR body 記錄 sha256 before/after** for audit
- Vault 寫入要在 sibling worktree 跑（避免主 worktree 寫風險），或在 PR-C1 worktree 內由我直接操作 vault path

**B3 — Franky weekly digest 整合 audit**
- `agents/franky/agent.py`：在 `report = reporter.compose(...)` 之後、`reporter.write(report)` 之前
  - call `scripts.vault_layout_audit.run_audit(vault_root, repo_root, layout_doc)` 拿 `AuditReport`
  - 把 `report.body_markdown += "\n\n" + audit_report.to_markdown()`
  - 如果 `audit_report.has_errors`：丟給 alert_router 一條 AlertV1（severity="warning"）讓 Slack DM 抓到
- `tests/agents/franky/test_agent_vault_audit_wiring.py`：mock audit 回 1 個 error finding、assert reporter body 含 `## Vault Audit`、assert alert dispatcher 被 call

**B4 — Doc update**
- `docs/VAULT-LAYOUT.md` §7 — D-audit-stub 翻 `[已修]`（其餘留待 PR-C2）
- `docs/VAULT-LAYOUT.md` §6 β maintenance — 寫實際 wiring path

**Acceptance**：
- 所有 audit_* fixture test 綠
- 對真實 vault 跑 `python -m scripts.vault_layout_audit` 不報 error
- mock Franky weekly run 產出含 `## Vault Audit` section
- ruff check + ruff format 綠

**Risk**：中
- audit logic 第一次跑可能撞到沒預期的 vault state（要先 dry-run）
- vault CLAUDE.md 寫入是 vault 端操作，不可逆性高，PR body 必須有 before/after sha256

### Job C — PR-C2（issue #626）

**範圍**：drift §7 翻轉 + Robin Inbox/kb dual-read 殘留清

**Worktree**：`E:/nakama-adr028-c2` (branch `feat/adr028-c2-drift-cleanup`)
**PR 大小估計**：~120 lines

**動作**：
1. `docs/VAULT-LAYOUT.md` §7 翻 `[已修]`：
   - D-files-pending / D-agentoutputs-pending / D-inbox-pending / D-promotion-attachments
   - 每條附 resolution PR 引用（#616 / #621 / #629 / #654 / #656）
2. 清 Robin Inbox/kb 殘留 refs（見 §1.4 清單，移除 + docstring update）：
   - `agents/robin/README.md`
   - `agents/robin/CONTEXT.md`
   - `agents/robin/reading_context_package.py`（docstring）
   - `ARCHITECTURE.md`
   - `CONTENT-PIPELINE.md`
   - `docs/diagrams/vault-ingest-flow.md`
   - `docs/task-prompts/N509-reading-source-registry.md`
3. **不在此 PR**：移除實際 dual-read code path — 因為已經 #630 切過了，沒有真實 dual-read 殘存（只剩文字 ref）。spec 寫的「dual-read removal」實質上是 doc/comment-only。
4. 跑 `python -m scripts.vault_layout_audit` 確認 0 error，把 output paste 進 PR body

**Acceptance**：
- audit script 0 error finding
- grep `Inbox/kb` 在生產 code+docs 內無剩餘（drift table + manifest artifact 例外）

**Blocked by**：Job B merge + 1 週觀察視窗（ADR-028 §3 Phase C 規定）

**Risk**：低

### Job D（follow-up）— VPS deploy + pubmed flatten residual

**範圍**：
- 修修 SSH VPS → `git pull` → systemd restart
- 驗下次 PubMed digest 寫入 flat `KB/Attachments/{pmid}/`
- 跑 `flatten_attachments.py --resume` 把 `pubmed/` 殘留搬到 flat（這個我可以做，等修修先解 VPS）

**這個不放進 ADR-028 收尾的 PR**，獨立 issue 處理。

---

## 3. 執行順序 + worktree 規劃

```
Job A (#624 close-out)        ── 我獨立做、5/23 內可結
    │
    ▼
Job B (PR-C1)                 ── 主力工作，1-2 個 session
    │   sibling worktree E:/nakama-adr028-c1
    │   B1 audit → B2 vault CLAUDE.md → B3 Franky → B4 drift D-audit-stub
    │
    ▼ (merge + 1 週觀察)
Job C (PR-C2)                 ── 收尾，~1 hour
    │
    ▼
ADR-028 fully closed

(平行) Job D — 修修先解 VPS deploy，我再跑 pubmed flatten residual
```

**Worktree 命名**：
- `E:/nakama-adr028-finalization` — 此 plan doc（已開）
- `E:/nakama-adr028-c624` — Job A
- `E:/nakama-adr028-c1` — Job B
- `E:/nakama-adr028-c2` — Job C

每個 worktree 收尾後 `git worktree remove`。

---

## 4. 決策點（待修修拍板）

| # | 決策 | 我的建議 |
|---|---|---|
| Q1 | Job A 要不要把 `.tmp/` migration scripts 也搬到 `data/migrations/2026-05-20-vault-cleanup/scripts/`？ | **要**。Phase B audit trail 完整性 > artifact 雜訊。`.tmp/` 內保留 broken note pointing to canonical |
| Q2 | Job B B2 vault CLAUDE.md 修改幅度？ | **draft §2 替換 + §3 刪除 Schemas/ row + §0 memory 路徑修正**。比 #625 spec 多兩處 stale 條目，但同一個 doc 改一次比較 clean |
| Q3 | Job B B3 Franky audit wiring 走哪條 cron？ | **legacy weekly report (`python -m agents.franky` 預設)**，因為它有 `body_markdown` append 點。`digest` 5-section Slack 是 read-only stat aggregator，不適合 |
| Q4 | Job D VPS deploy — 是不是修修先處理？ | **是**。我無權 SSH。Job A close #624 時順手開 follow-up issue 給修修 |
| Q5 | Sandcastle 還是 local worktree？ | **local worktree**。Phase B 跑過了，C1/C2 是 single-author 整合工作，sandcastle multi-agent 並行沒優勢，且 vault 寫入要 careful |
| Q6 | 是否需要 panel review (Codex + Gemini)？ | **Job B audit script 寫完後跑一次 panel**。audit logic 是新代碼、未來會被信任、出 false positive/negative 都會 erode 信任。Job A/C 不需要 |

---

## 5. 風險 + 防範

| 風險 | 防範 |
|---|---|
| audit script 第一次跑 false positive 爆量 → 修修對 Franky weekly report 失去信任 | B4 把 D-audit-stub 翻 `[已修]` 前先 dry-run 一次跑在真 vault、人眼過一遍 |
| vault CLAUDE.md 改錯 → Obsidian agent 行為飄移 | B2 操作前 backup CLAUDE.md → `.tmp/vault-claude-md.backup-2026-05-23.md`、PR body 記 sha256 before/after |
| VPS Robin 持續寫 `KB/Attachments/pubmed/` 累積 → 修完還是有 drift | Job A 開 follow-up issue 等修修 deploy，PR-C2 不 block 它 |
| audit script 跑 Franky cron 拖慢 weekly digest | audit 跑時間估 <5 sec（folder walk + grep），對 weekly cron 影響可忽略 |

---

## 6. Acceptance（全 ADR-028 收尾）

- [ ] Job A：#624 closed、`data/migrations/2026-05-20-vault-cleanup/` 進 main
- [ ] Job B：PR-C1 merged、audit script live、vault CLAUDE.md updated、Franky weekly 含 audit section
- [ ] Job C：PR-C2 merged、§7 drift 翻完、Robin doc-refs 清完
- [ ] Job D（修修主導）：VPS deploy current + pubmed residual flattened
- [ ] `python -m scripts.vault_layout_audit` 對真 vault 跑出 0 error
- [ ] ADR-028 status: Accepted → Implemented（最終 PR 加註）

---

## 7. 修修需要看的下一步

我做的下一步如果你同意這個 plan：

1. **拍板** Q1-Q6 決策點（或修正）
2. 確認 worktree 開法、PR 拆 4 個 commit 的方式 ok
3. **修修自己處理** VPS deploy（Job D 上游）— 或明確授權我 SSH 並 trigger deploy
4. 我從 Job A 開始做下去
