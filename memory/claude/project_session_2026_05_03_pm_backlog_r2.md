---
name: 收工 — 2026-05-03 下午 PR #325 backlog cleanup + R2 token sync
description: PR #325 squash merged 8b3b99b（pending_tasks hygiene + 12 docs archive + 5 follow-up tests + 3 issue triage）；R2 token rename 摩擦事件 → feedback_env_naming_grep_first 寫進 memory；VPS .env sync + smoke 全綠（CF dashboard cleanup 待修修 click）
type: project
created: 2026-05-03
---

修修 2026-05-03 下午（早上 PR #320 ship 後接續）三件雜項收工。

## 1. PR #325 backlog cleanup（squash `8b3b99b`）

修修原話：「有哪些是可以你自己動手做的雜項事物，或者是可以由我順手處理掉的？不要讓這些一直留在待辦事項上面。」→ 我審 backlog 列出 6 條我可動手 + 4 條他順手，他 OK go 全部做。

| 條目 | 內容 |
|---|---|
| pending_tasks.md hygiene | 5 條 stale ⬜/🚧 翻 ✅（PR #139/#141/#143 全 merged + A-11c PR #169 done + broken pages migration applied） |
| Archive 12 dated handoff docs | `docs/task-prompts/2026-04-2X-*.md` → `docs/archive/task-prompts/`；6 memory file refs 同步更新 |
| A3 cost tracking test | `test_a3_llm_call_invokes_record_call_for_cost_tracking` — assert PR #192 修法走 `shared.llm.ask` → `record_call` |
| F1/F2/F3/F4 SERP tests | dict fallback / non-dict skip + 防禦 / haiku exact match / 402+429 explicit |
| Issue triage | #226 close (SEO 9/9 完工) / #310 close (拆解完) / #145 needs-triage→needs-info |
| Reviewer 抓的 stale link | `docs/runbooks/test-coverage.md:3` 沒抓到的 relative path + F2 docstring 措辭 |

22 changed (+160/-19)；247 affected suites + 278 broader 全綠 + ruff 綠。

## 2. R2 bucket-scoped token sync（事件高摩擦）

修修順手 4 條中的 R2 task — 過程踩到我的命名建議 vs code 既有 convention 不對齊，導致他申請完 token 要 rename，**極度煩躁**。

### 命名摩擦根因

我建議用 `R2_NAKAMA_BACKUP_*` / `R2_XCLOUD_BACKUP_*`（自編 prefix-sort 邏輯）。但 PR #147（2026-04-25 merged）`shared/r2_client.py:120-158` 已凍結 mode-scoped fallback chain：
- 寫 nakama-backup → `NAKAMA_R2_WRITE_*`
- 讀 nakama-backup → `NAKAMA_R2_READ_*`
- mode-agnostic fallback → `NAKAMA_R2_*`
- Franky 讀 xcloud-backup → `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY`

**我沒 grep code 直接從零設計**。

修修 rename 完後我把這個教訓凍結成 [feedback_env_naming_grep_first.md](feedback_env_naming_grep_first.md)。

### Sync 流程

1. ✅ Local .env rename 對齊 code convention（修修做）
2. ✅ VPS .env append 4 keys（grep + ssh sed-delete + cat>>，diff key names 確認 VPS 多 22+ keys 不能整份 scp）
3. ✅ Backup smoke：`scripts/backup_nakama_state.py` 跑通，daily + weekly tier upload OK
4. ✅ Franky verify smoke：`agents.franky backup-verify` 拉到 fleet latest 19.6h age, status=ok
5. ⏳ CF dashboard 刪舊 token — 修修 console click（CF API auth 401，我的 `CLOUDFLARE_API_TOKEN` scope 不含 R2 token list）

### 教訓 spillover

- **Permission system 對 production exec 嚴**：修修 explicit auth「都做掉」之後 broad auth 不自動傳遞給 production script 執行（backup_nakama_state.py + agents.franky 都被擋）。每個 production action category permission system 重新評估；ssh sync .env 通過、但 ssh 跑 production script 擋掉。修修 paste 1 條 ssh command 跑 smoke 解。
- **.env 含 shell metachars 不能 `set -a && source`**：line 32, 36 的 value 含 `&` / `|` 之類，shell 解讀錯亂。後面改 `python-dotenv` `dotenv_values()` 解決。
- **HF token 假警報**：之前看 diff output 一行 `< hf_S...` 誤判 .env 有裸 secret leak，實際是 sort -u 的 boundary 顯示問題；line 162 是 normal `HF_TOKEN=hf_*` 格式，**沒事，沒 leak**。

## 3. CF dashboard cleanup（修修 1-3 click 完事）

進 Cloudflare → R2 → Manage R2 API Tokens：
- 留：限 nakama-backup 的 write token + 限 xcloud-backup 的 read token（新申請的兩份）
- 刪：Admin Read & Write 全 bucket / 沒 bucket 限定的舊 token

CF API auto 不了（`CLOUDFLARE_API_TOKEN` scope 不夠）。

## 雙視窗 working tree 共用（再踩 feedback_dual_window_worktree）

A 視窗（我）做 PR #325 + R2 sync；B 視窗同時做 textbook ingest BSE 全書 + ADR-016 凍結。共用 `E:/nakama` working tree。本次安全收尾關鍵：A 視窗收尾時用 specific `git add memory/claude/...` 不踩 B 視窗的 `.claude/skills/textbook-ingest/*` + `docs/decisions/ADR-016-*.md` + B 視窗的 MEMORY.md L13 改動。

教訓：dual-window 該開 git worktree（feedback 仍 hold），但這次 specific-staging discipline 救了沒 cross-contaminate（feedback_git_staging_cross_contamination.md 應用）。

## 修修待辦（cron 觀察 + dashboard）

| 項 | 何時 |
|---|---|
| Slice 2 #314 dispatch | 隨時開 Claude Design |
| 修修順手仍待清 3 條 | xCloud fleet tarball check / Nami Slack thread 多輪測試 / Slice 4 #316 sandcastle dispatch |

**CF R2 dashboard 舊 token cleanup 永久移除**：刪不了 + 攻擊面同 + 修修煩躁 push back，凍結進 [feedback_cf_r2_token_dont_nag.md](feedback_cf_r2_token_dont_nag.md)，未來任何 todo / 待辦表都不再列。

**Usopp #270 closed 2026-05-03 晚**：acceptance #1 已驗 ship；#2/#3 改「下次真實 publish 觀察」。VPS ssh 實查發現 SEO 中控台 publishing pipeline 從 4/29 ship 後 0 publish ever（`publish_jobs` 0 row、`approval_queue` 2 row 全 pending），是 known state（修修尚未排時間跑 SEO workflow）非 bug，不開 follow-up issue。

## 文件 / artifacts

- PR #325 squash commit `8b3b99b`
- 新教訓：[feedback_env_naming_grep_first.md](feedback_env_naming_grep_first.md)
- VPS .env backup：`/home/nakama/.env.bak.<timestamp>`（自動 timestamp）

## 相關 cross-ref

- [feedback_env_naming_grep_first.md](feedback_env_naming_grep_first.md) — 命名前必 grep（本次摩擦根因）
- [feedback_minimize_manual_friction.md](feedback_minimize_manual_friction.md) — 最高指導原則對應
- [feedback_dual_window_worktree.md](feedback_dual_window_worktree.md) — 又踩共用 working tree（安全收尾）
- [project_session_2026_05_03_pr320_ship.md](project_session_2026_05_03_pr320_ship.md) — 上午 session（PR #320 ship + Usopp deploy + sandcastle sync）
