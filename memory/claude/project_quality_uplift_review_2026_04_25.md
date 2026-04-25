---
name: Quality Uplift 5-PR review 結果（2026-04-25）
description: 5 個 quality uplift PR 的 review 結論 + 已修 critical + 待修 major/minor 清單
type: project
originSessionId: 74954c3c-fdc6-4064-931b-fe3250a7d804
---
5 個 quality uplift PR 第一輪 review 完成，3 個 critical 已 fix push，剩餘 major/minor 待逐一修。

**Why:** 5 PR 一起 review 是 Phase 1-9 quality uplift 的 ship-readiness gate。等修修自己 review 過 + VPS smoke 後分別 squash merge。

**How to apply:** 修第二輪時直接照下表清 major；新增類似 PR 時把跨 PR 重複的問題（mock spec / .env inline 註解）當 PR-template checklist。

## 5 PR review verdict

| PR | Verdict | Critical | Major | Minor | Tests |
|----|---------|----------|-------|-------|-------|
| #146 Phase 1 DR | Request | 1 ✅ fixed | 5 | 9 | 15/15 |
| #147 Phase 2A backup | Request | 1 ✅ fixed | 1 | 5 | 44/44 |
| #152 Phase 3 obs | Request | 0 | 3 | 6 | 77/77 |
| #154 Phase 2B integ+B2 | Request | 0 | 1 | 5 | 26/26 |
| #157 Phase 9 ver+doc | Request | 1 ✅ fixed | 2 | 5 | 127/127 |

## Critical fixes shipped

- **#146** rebase drop 重複 commit `9bc0255`（已透過 PR #140 squash 進 main 了，殘在這裡是 scope creep）— 強制推 `feat/dr-drill-runbook` 後 PR 從 12 檔 → 5 檔
- **#147** `.env.example:23-25` 三個 retention env 的 inline 註解 → 獨立行（dotenv 把 `# daily tier...` 解成 truthy 字串會 `int()` ValueError）
- **#157** `/bridge/docs` snippet XSS：`shared/doc_index.py` 改用 `\x01`/`\x02` sentinel + `html.escape()` + 替換回 `<mark>`，加 `test_search_snippet_escapes_html_in_body` regression test

## Major 待修（merge 後 follow-up，不 block）

**#146 (5)**：
- `docs/runbooks/disaster-recovery.md:98` runbook 撒謊 `mv` 走原檔後 script 不會再做 `.pre-restore` 備份（修 step 2 改 `cp` 或拿掉 misleading 註解）
- `tests/scripts/test_restore_from_r2.py:58` mock 沒 `spec=R2Client`
- `scripts/restore_from_r2.py:111` 越權碰 `client._s3.download_file` — R2Client 應加 `download_file` 對齊 B2Client 介面
- `scripts/restore_from_r2.py:116` `except Exception` 太寬，掩蓋 programming bugs
- `secret-rotation.md` 漏 YouTube OAuth (CLIENT_ID/SECRET/REFRESH_TOKEN)

**#147 (1)**：`agents/franky/health_check.py:402` probe 是 read-only 但用 `from_nakama_backup_env()` 預設 `mode="write"` — 改 `mode="read"`

**#152 (3)**：
- `/bridge/health` 沒有入口（Hub `index.html` + memory/cost/drafts/franky 全部 chassis-nav 沒加 link）
- chassis-nav 出現第三種 taxonomy（titlecase no-zh `active`），跟 drafts/memory/cost (uppercase + zh + `active`) 和 franky (uppercase + no-zh + `is-current`) 三種並存
- `tests/shared/test_alerts.py:14-17` mock 沒 `spec=FrankySlackBot`

**#154 (1)**：`verify_backup_integrity.py` + `mirror_backup_to_secondary.py` 沒接 `record_failure` + `alert("error", ...)` — 失敗 silent，違反 #152 想消滅的 anti-pattern；要等 #152 merge 後接

**#157 (2)**：
- conventional-commits regex 不接 `feat!:` BREAKING `!` 後綴（PR description 說有但 regex 沒寫）
- `_INDEXED_ROOTS` 漏 repo-root markdown（`README.md`、`ARCHITECTURE.md`、`CLAUDE.md`、`CHANGELOG.md`）

## 跨 PR 主題

- **`feedback_mock_use_spec` 在 #146/#152/#154 都中招**（3/5 PR）— 開 PR 前要把這條當 self-check checkbox
- **`int(... or "30")` 同一行 defensive fix 出現在 #147/#152/#154/#157 四個 PR**，git auto-resolve 不會踩
- **`/bridge/*` 新頁面 chassis-nav 不一致**（#152 / #157 各自加自己一套），需要一輪統一 sweep
- **`verify_db` dedupe**：#146 inline + #154 shared 版 byte-identical 除一行 ruff line-split — #146 merge 後追 follow-up 把 #154 merge 後的 inline 換成 `from shared.sqlite_integrity import verify_db`

## 建議 merge 順序（修完 critical 後）

1. **#146** (rebase 完成、only 5 檔) — DR foundation，零依賴
2. **#147** (.env.example 修完) — backup multi-tier，零依賴
3. **#157** (XSS 修完) — version control + doc index，零依賴
4. **#152** — observability，三個 Major 同 PR 修完再 merge
5. **#154** — 最後，等 #152 merge 提供 alert/heartbeat API 後接

## 修修手動 follow-up

- VPS `.env` append（不要整份覆蓋）—— `NAKAMA_R2_WRITE_*` / `NAKAMA_R2_READ_*` / `B2_*` / 三個新 retention vars
- VPS crontab 加 3 行：04:30 mirror、03:30 週日 verify、04:00 backup（已有）
- `systemctl restart thousand-sunny nakama-gateway`
- Branch protection setup（runbook 在 #157）
- DR drill 執行（runbook 在 #146）

## Sub-agent 共用 worktree 教訓（這次踩過）

5 個 review sub-agent 並行跑時，每個都 `gh pr checkout` 在主 worktree，互踩 branch state。下次必須開 worktree（`git worktree add`）。已寫入 feedback_dual_window_worktree 但沒延伸到 sub-agent — 補一條 feedback。
