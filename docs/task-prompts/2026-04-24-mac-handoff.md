# Mac Session Handoff — 2026-04-24

**桌機正在做什麼：** PR #97 (Usopp Slice C1 daemon) follow-up — `agents/usopp/__main__.py` + `.env.example` 三合一修（load_config / USOPP_* env drift / operation_id in warning log）。

**Mac 不能碰的檔案：**
- `agents/usopp/*`
- `shared/wordpress_client.py`
- `.env.example`

**建議挑 A + B 一起收（半 session 量）。**

---

## 任務 A：LifeOS template 同步（純 vault，不碰 repo）

### 1. 目標

`Templates/tpl-project.md` + `Templates/tpl-action.md` 對齊 gold standard `Projects/肌酸的妙用.md` 的 frontmatter / 內容結構，讓 Nami `project-bootstrap` skill 建出的 project 當下就對得上 gold standard。

### 2. 範圍

| 路徑 | 動作 |
|---|---|
| `F:\Shosho LifeOS\Templates\tpl-project.md` | 改寫 |
| `F:\Shosho LifeOS\Templates\tpl-action.md` | 改寫 |
| `F:\Shosho LifeOS\Projects\肌酸的妙用.md` | 只讀（gold standard 參考） |

**不碰**：`memory/`、repo 內任何 `.py` 檔。純 vault。

### 3. 輸入

- Gold standard: `F:\Shosho LifeOS\Projects\肌酸的妙用.md`
- 現狀記憶: `memory/claude/project_lifeos_template_drift.md`
- 下游消費：Nami `project-bootstrap` skill 的 frontmatter 欄位期望

### 4. 輸出

兩份 template 檔，frontmatter key 與肌酸那篇 1:1 對應（type、area、content_type、priority、status、created、updated、tags 等）；body section 骨架（## 目標 / ## 背景 / ## 相關 Action / ## 參考資料）一致。

### 5. 驗收（Definition of Done）

- [ ] `tpl-project.md` frontmatter 跟肌酸那篇 key 集合完全一致
- [ ] `tpl-action.md` 跟 gold standard 既有 Action 檔（修修挑一個 recent）的 frontmatter 一致
- [ ] 兩份 template 的 section 骨架跟 gold standard 呼應
- [ ] 用 template 建一個測試 project（`Projects/_test-template-sync.md` 然後 recycle bin 刪掉）frontmatter valid
- [ ] 寫一條 commit 註記到 Vault Journal（不必 commit，這是 LifeOS 不是 repo）

### 6. 邊界

- ❌ 不動 `Templates/` 以外的 vault 目錄
- ❌ 不改 Nami `project-bootstrap` skill 的 code（skill 端已經對 gold standard schema，template 對齊即可）
- ❌ 不碰 repo 的任何東西

---

## 任務 B：Franky `r2_backup_verify` 擴展 `nakama-backup` bucket freshness

### 1. 目標

Franky 5-min cron 的 R2 backup freshness probe 從目前只看 `xcloud-backup` 擴展到也看 `nakama-backup` bucket。超過 48 小時沒更新 → Critical alert via `alert_router.dispatch`。

### 2. 範圍

| 路徑 | 動作 |
|---|---|
| `agents/franky/health_check.py`（或相關 r2 probe 模組，開工先 grep 定位） | 擴增一個新 probe `probe_r2_backup_nakama` |
| `tests/agents/franky/test_health_check.py` 或對應測試檔 | 新增測試 |
| `agents/franky/alert_router.py` | 只讀不改（用既有 dispatch） |
| `docs/capabilities/franky-monitor.md` | 更新 probe 列表（如果現有 capability card 有列） |

**不碰**：`agents/usopp/*`、`shared/wordpress_client.py`、`.env.example`（桌機在動）。

### 3. 輸入

- 既有 `probe_r2_backup`（`xcloud-backup` 那個）作為實作範本
- PR #88 加的 `nakama-backup` bucket（memory `project_nakama_backup_deployed.md`）
- `shared/r2_client.py`（如果存在，grep 定位）

### 4. 輸出

- 新 probe 函式 `probe_r2_backup_nakama` 或 `probe_r2_backups(["xcloud-backup", "nakama-backup"])` 的泛化版本（兩種設計都 OK，挑 code-review 比較乾淨的）
- 對應 test：bucket 空 → fail、最新 object 新於 48hr → ok、最新 object 超過 48hr → fail + Critical alert
- `health_probe_state` 表的 target 欄位多一個 `r2_backup_nakama` entry（CHECK constraint 如果有需要更新）

### 5. 驗收

- [ ] Franky `run_once()` tick 會跑新 probe
- [ ] 超過 48hr threshold 會觸發 Critical `alert_router.dispatch`
- [ ] 既有 `xcloud-backup` probe 行為完全不變（regression test 通過）
- [ ] 全 repo `pytest` 綠（baseline 1035 passed / 1 skipped）
- [ ] `ruff check` + `ruff format` 綠
- [ ] Capability card（如有）更新 probe 列表
- [ ] P7 完工格式交付

### 6. 邊界

- ❌ 不改 `xcloud-backup` probe 既有邏輯（只擴展）
- ❌ 不改 `alert_router.py` 的 dispatch 行為（只 consume）
- ❌ 不改 `agents/usopp/*`（桌機在動）
- ❌ 不動 `.env.example`（桌機在動）
- ❌ 不動 VPS 設定（這是 code 變更，部署是另一件事）

### 7. VPS 部署

**不在本任務範圍**。PR merged 後修修手動部署（`ssh nakama-vps` + git pull + 重啟 nakama-gateway service）。

---

## Handoff 注意事項

1. **衝突預防**：桌機 PR #97 follow-up 合前，Mac 不要 rebase 到桌機 branch；各自開 feature branch from `main`。
2. **PR 命名建議**：
   - A 不開 PR（vault 不在 repo）
   - B 開 `feature/franky-nakama-backup-probe`
3. **完工後**：B 走 `feedback_pr_review_merge_flow.md`（自動 code-review → 報告 → 等修修授權 → squash merge）
4. **A 完工後**：修修可以在 Nami 做 `project-bootstrap` smoke test 驗證 template 正常（Mac 端不必做這步）

---

## 為什麼選這兩個

- **A**：Nami 的前置依賴，template 漂著就會一直噁心；純 vault 工作，完全 zero-overlap。
- **B**：nakama-backup bucket 目前沒人監控（PR #88 的 gap），實際 reliability 漏洞；只動 `agents/franky/`，跟桌機 usopp follow-up 檔案層完全分離。

桌機 follow-up 預估 30 分鐘內搞定（三合一小修 + 測試 + review），Mac A + B 預估一起 1-2 小時。
