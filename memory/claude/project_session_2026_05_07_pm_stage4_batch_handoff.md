---
name: 5/7 PM Stage 4-5 完成 handoff（5.2 + 6 留人）
description: Stage 4 main batch + 4.5b/c rerun + 5.0/5.1/5.3 自動驗證全跑完；24/28 PASS（4 章 wikilink-soft-FAIL 結構性）；verify_staging walker matching bug 已 patch；累積 ~$45；Stage 5.2 人眼 + Stage 6 ship 留修修親手
type: project
created: 2026-05-07
updated: 2026-05-07
---

## TL;DR (5/7 23:00 final state)

5/7 PM 12hr+ AFK pipeline 跑完 Stage 4 + 4.5 + 5（自動部分）：

- ✅ Stage 4 main: 28 章 ingest，BSE 11 PASS + SN 7 PASS + 4 wikilink-FAIL + 6 OverloadedError ($29.81)
- 🚨→✅ Stage 4.5a: 撞 `--book-id 不換 raw` script bug，污染 BSE staging 3 junk + 我誤刪 BSE ch11；recovery 補回 ($0.40 wasted)
- ✅ Stage 4.5b: 6 SN OverloadedError rerun（proper `--raw-path`）→ 3 PASS / 3 wikilink-FAIL ($5)
- ✅ Stage 4.5c: 7 wikilink-FAIL re-ingest（同 Sonnet 4.6）→ 3 PASS / 4 持續 FAIL（content shape 限制）($8)
- ✅ Stage 5.0: verify_staging 25/29 PASS（先 patch 過 walker matching bug — chapter_title 對 walker payload 而非 filename idx）
- ✅ Stage 5.1: subagent 抽檢 5 章（BSE ch1/ch6/ch10 + SN ch3/ch12）全 PASS，無 prompt-leak / hallucination
- ⚠️ Stage 5.3: 566 concept pages 都健康（median 439w，無 empty stub），但 L3 hard-rule wiring 仍 deferred

**最終可 ship 24/28**（BSE 11 + SN 13）+ **4 章 wikilink-soft-FAIL**（SN walker_pos 8/12/19/22 = real ch2/6/13/16，verbatim/anchors/figs 全綠僅 wikilinks 短）

**Grand total cost ≈ $45**（Sonnet 4.6 全程 per user 一致 model 要求）

## 留給修修親手的事

1. **5.2 人眼 spot-check** — Obsidian 開 BSE ch1 + SN ch3 + SN ch12 各看一段，確認可讀性
2. **5.2 wikilink-FAIL 4 章裁示** — SN ch2/6/13/16：accept-with-note 或 patch chapter-source.md prompt 重 ingest（但 re-ingest 已試 4.5c 沒解，可能要從 prompt rule 改）
3. **6 ship** — 把 `KB/Wiki.staging/*` 複製到 `KB/Wiki/` 正式區

## Stage 8 follow-up（可選未做）

- concept slug validator patch（unsafe slug warnings：`Ergogenic Aid`、`Na+-K+ Pump`、`creatine supplementation` 等含空格/+號被拒；現 1902 dispatched 但部分 slug 直接被 reject）
- L2/L3 hard-rule wiring 進 concept_dispatch（Stage 1.5 ship 了 validators 但沒接，所以 staging concepts 都沒有 `level:` frontmatter）
- ADR-020 final commit / memory consolidation

## 重要 commit 摘要（branch: docs/kb-stub-crisis-memory）

| Hash | What |
|---|---|
| 2bd5073 | Stage 4 prep: commit run_s8_batch.py |
| (uncommitted) | scripts/verify_staging.py — patch chapter_title 對 walker matching bug |

**verify_staging.py 已 patch 但 uncommitted** — 修修決定要不要保留時 commit。

## verify_staging bug 細節

兩個 ingest mode 寫檔規則不同：
- **batch mode** (`run_s8_batch.py`): 直接傳 `payload` 給 phase1，filename = `ch{walker_pos}.md`
- **CLI single-chapter** (`run_s8_preflight --chapter-index N`): `_pick_chapter` D4 reassign `payload.chapter_index` = N，filename = `ch{N}.md`（real_ch_num 當 N ≤ real chapters，否則 fallback 走 walker_pos）

→ 同一個 staging dir 裡 filename idx 既可能是 walker_pos 也可能是 real_ch_num，verify_staging 把 filename idx 當 real_ch_num 餵 `_pick_chapter` 變成大半 mismatch。

**Patch**：先用 frontmatter `chapter_title` 對 walker payload；title 不在的話再 fallback 到原 `_pick_chapter(payloads, filename_idx)`。Patch 在 `E:\nakama-stage4\scripts\verify_staging.py` line ~91-105。

## 主 repo vs worktree state

- 主 repo `E:\nakama` 還在 `impl/N454-brook-synthesize-store`（另一視窗 5/7 早切的）
- 我這 session 全程在 worktree `E:\nakama-stage4` @ `docs/kb-stub-crisis-memory` (HEAD 2bd5073)
- `.env` 用 export-source 進來（cp .env 是 deny 機密規則）
- venv 共用 `E:/nakama/.venv`

## Batch 章節→walker_pos→filename 對應表

**BSE walker pos 1-2 = front matter，3-13 = real ch1-11，14-15 = appendix**：

| Real ch | Title | walker_pos / filename |
|---|---|---|
| 1 | Energy Sources | 3 / ch3.md (or ch1.md from Stage 2 host) |
| 2 | Skeletal Muscle | 4 / ch4.md |
| ... | ... | ... |
| 10 | Endurance Exercise | 12 / ch12.md |
| 11 | High-intensity Intermittent | 13 / ch13.md（4.5a 我誤刪、後再 ingest 復原）|

**SN walker pos 1 = ch1，2-7 = front matter，8-23 = real ch2-17，24-26 = appendix**：

| Real ch | walker_pos / filename |
|---|---|
| 1 | 1 / ch1.md |
| 2 | 8 / ch8.md |
| 3 | 9 / ch9.md |
| ... | ... |
| 17 | 23 / ch23.md |

## Cost breakdown

| Stage | Cost |
|---|---|
| 4 main batch (28 ch) | $29.81 |
| 4.5a wasted (script bug) | $0.40 |
| 4.5b (6 ch SN rerun) | ~$5 |
| 4.5c (7 ch wikilink rerun) | ~$8 |
| BSE ch11 recovery | ~$1 |
| 5.1 subagent | ~$0.50 |
| **Grand total** | **~$45** |

5/6 burn $22.23 + 5/7 $45 = $67 累積 textbook ingest v3 投資。Path C OAuth 試了沒過（anti-automation 429），全走 Path A API key。

## References

- 主 plan：`docs/plans/2026-05-07-textbook-ingest-v3-path-b-rewrite.md`
- 5/6 burn handoff：`memory/claude/project_session_2026_05_06_07_s8_burn_handoff.md`
- ADR-020：`docs/decisions/ADR-020-textbook-ingest-v3-rewrite.md`
- batch log：`E:/nakama-stage4/docs/runs/2026-05-07-s8-batch.log`
- final report：`E:/nakama-stage4/docs/runs/2026-05-06-s8-final-report.md`（檔名 hardcoded 5-06）
- 4.5b log：`E:/nakama-stage4/docs/runs/2026-05-07-s8-stage4_5b-rerun.log`
- 4.5c log：`E:/nakama-stage4/docs/runs/2026-05-07-s8-stage4_5c-wikilink-rerun.log`
- staging-verify report：`E:/nakama-stage4/docs/runs/2026-05-07-staging-verify.json`

## TL;DR

5/7 下午 12hr+ AFK pipeline，到 18:01 為止：
- ✅ Stage 1a-1.5 全 ship（5 個 sandcastle dispatch + 2 host patch）
- ✅ Stage 2 (BSE ch1+ch10) + Stage 3 (BSE ch3) — 6 條 deterministic gate 全綠
- ✅ Stage 4.0 OAuth-in-Docker dry-run **發現 anti-automation 429**（plan-anticipated）→ fallback Path A
- 🔄 Stage 4 Path A host batch 跑中（28 章；BSE 11/11 完、SN 2/17 完，~14 章 remaining ~3.5hr）
- 📋 Stage 4.5 / 5.0 / 5.1 / 5.3 待跑
- ⏸ Stage 5.2 人眼 + Stage 6 ship 是計畫設計的 stop point（要修修親手）

Context messages 235.7k → handoff 到 fresh session。

## Commits (branch: docs/kb-stub-crisis-memory)

| Hash | Stage | What |
|---|---|---|
| 93d5754 | (前置) | 5/7 Path B 完整計畫 |
| f37394d | (前置) | 5/6→5/7 burn handoff memory |
| a11fa42 | **1a** | `_assemble_body` 純函式 + 9 unit tests |
| 55d8ca9 | **1b** | runner JSON wiring + 真章號檔名 + `--dry-run` + 5 tests |
| 48fd127 | **1c** | JSON-only chapter-source prompt + 4-rule deterministic acceptance gate + 14 tests |
| c48e8ba | **1.5** | verify_verbatim/verify_staging CLIs + L2/L3 concept_validators + ADR-020 §Phase 1.5 freeze + 18 tests |
| 0c566a5 | **1a.1** | NFKC-tolerant anchor compare（U+2018/19/1C/1D/13/14 explicit normalize）+ prompt punctuation rule + 4 tests |
| 19c5f47 | (4 prep) | get_client() OAuth fallback — sandcastle Path C subprocess auth |
| a16da0a | (4 prep) | get_client() ANTHROPIC_AUTH_TOKEN 優先（CLAUDE_CODE_OAUTH_TOKEN 在 container 被 Claude Code CLI 消費掉） |
| 2bd5073 | (4 prep) | commit `scripts/run_s8_batch.py` (was untracked since 5/6 burn) |

**注意**：commits 19c5f47 + a16da0a 在 host batch 切到 Path A 之後其實對 host 不需要（host 用 ANTHROPIC_API_KEY），但對未來再嘗試 sandcastle Path C 仍有用。**修修在主 repo 切到 `impl/N454-brook-synthesize-store` 後手動回退過 `shared/anthropic_client.py`** — 那是另一視窗的選擇，不要動。Path A worktree 用的是 `docs/kb-stub-crisis-memory` 的版本（含 OAuth fallback）。

## 主 repo vs worktree state

**主 repo `E:\nakama` 已切到 `impl/N454-brook-synthesize-store`** by 另一視窗（違反原本的口頭協議，已寫進 `feedback_dual_window_worktree.md` 強化規則）。我這邊已開 worktree：

- 路徑：`E:\nakama-stage4`
- branch：`docs/kb-stub-crisis-memory`
- HEAD：2bd5073
- `.env` 是手動 export-source 而非 file copy（cp .env 被 deny — 機密檔規則對的）
- 跑 batch 的 venv 是 `E:/nakama/.venv`（共用，pip deps OK）

**Stage 4.5+ 之後所有命令** cwd = `E:\nakama-stage4`，python 用 `E:/nakama/.venv/Scripts/python.exe`，前面 `set -a; source E:/nakama/.env; set +a` export env。

## Batch in-flight 狀態

- 啟動 cmd：`cd E:/nakama-stage4 && set -a && source E:/nakama/.env && set +a && E:/nakama/.venv/Scripts/python.exe -m scripts.run_s8_batch --vault-root "E:/Shosho LifeOS" --books bse,sn --continue-on-fail`
- 啟動時間：15:09
- 背景 Bash task id：`bep2yhh9g`
- log 檔：`E:/nakama-stage4/docs/runs/2026-05-07-s8-batch.log`
- 最終 report 會寫到：`E:/nakama-stage4/docs/runs/2026-05-06-s8-final-report.md`（檔名 hardcoded 5-06，是 batch script 內定）+ `E:/nakama-stage4/docs/runs/s8-batch-progress.json`
- 章鐘表現（Phase 1 LLM call 開始時間，每章 wall = 下章 - 此章）：
  - BSE ch1@15:09 ch2@15:21 ch3@15:28 ch4@15:46 ch5@16:02 ch6@16:23 ch7@16:35 ch8@16:53 ch9@17:02 ch10@17:12 ch11@17:26 — **BSE 全跑完 ✅**
  - SN ch1@17:39 ch2@17:55 ch3@18:01 — SN 2/17 完，目前在 ch3
- 平均 wall：~12-18 min/章。剩 14 章估 ~3hr。
- 沒踩 429（Path A API key 限流寬鬆）、沒 ValueError、沒 fatal。

## 已知問題（不阻擋章節 ship，但 Stage 5 要關注）

1. **「unsafe concept slug」warnings 大量** — 含空格或特殊字元的 concept name 被 slug validator 拒絕：
   - 例：`Ergogenic Aid`、`Sprint Training`、`Na+-K+ Pump`、`Muscle Fatigue`、`glycogen phosphorylase`、`glycogen synthase`
   - 結果：這些 concept 不寫 KB/Wiki.staging/Concepts/，concept count 比 LLM 抽出的少
   - Stage 5.3 要看實際 L1/L2/L3 數量（章節報告會顯示 dispatch 成功的數量；slug fail 的不算）
   - root cause patch 不在 Stage 4 scope（要動 `shared/concept_dispatch.py` 或 slug validator），列入 Stage 5/6 follow-up
2. **5/6 burn 殘留 staging 檔**（其他 BSE 章 ch3/ch4/ch5/ch6/ch7/ch11/ch12/ch13 + Sport Nutrition 章）— 這次 batch 會 overwrite 它們；Stage 5.0 verify_staging 會自動掃所有檔，舊 burn 檔若是這次沒 overwrite 的會留，要 spot check。

## Cost so far

- Stage 2 ch1 + ch10 host：~$0.25（estimated, 觀測 gap report $0.00）
- Stage 3 ch3 host：~$0.18
- Stage 4.0 sandcastle dispatches × 3：OAuth Max（不付費，但消耗 quota）
- Stage 4 Path A host batch（in flight）：估 28 章 × ~$0.20 平均 = ~$5-6 total
- Total Path A 估：~$6-7

不到 plan §Stage 4 預估「Path A fallback 多花 ~$15」一半。原因 = JSON-only prompt 比 5/6 burn 的 body-emit prompt 便宜 3x。

## 起手 checklist（fresh session 開頭跑）

1. 讀 `MEMORY.md` 然後讀本 handoff
2. 讀 `feedback_context_check_before_multistage.md`（每次 fresh session 必讀）
3. 確認 batch 狀態：
   ```bash
   tail -50 "E:/nakama-stage4/docs/runs/2026-05-07-s8-batch.log"
   wc -l "E:/nakama-stage4/docs/runs/2026-05-07-s8-batch.log"
   grep -c "Phase 1: calling LLM" "E:/nakama-stage4/docs/runs/2026-05-07-s8-batch.log"  # 完成章數的 proxy
   ls -la "E:/Shosho LifeOS/KB/Wiki.staging/Sources/Books/sport-nutrition-jeukendrup-4e/" 2>&1 | head
   ```
4. 如果 batch 還在跑 → 架 Monitor 或等下次 task notification（task id 已不適用，本背景命令是 fresh session 之外的）
5. 如果 batch 完工 → 看 `2026-05-06-s8-final-report.md`（hardcoded date naming）統計 PASS/FAIL/ERROR 章數
6. 進 Stage 4.5：失敗章 host rerun（單章 cmd：`cd E:/nakama-stage4 && set -a && source E:/nakama/.env && set +a && python -m scripts.run_s8_preflight --book-id <book> --chapter-index <N>`）
7. 進 Stage 5.0：`python -m scripts.verify_staging --vault-root "E:/Shosho LifeOS"`
8. 進 Stage 5.1（subagent 抽檢） + 5.3（concept validator）
9. ⏸ **Stage 5.2 人眼 + Stage 6 ship 不要動，handoff 給修修**

## 預計剩餘工作量

| Stage | 內容 | wall | cost |
|---|---|---|---|
| 4 (剩 14 章) | SN ch3-17 batch in flight | ~3hr | ~$3 |
| 4.5 | 失敗章 rerun（估 0-3 章）| 15-30min | <$1 |
| 5.0 | verify_staging 自動 | 1min | $0 |
| 5.1 | subagent 抽檢 | 5min | ~$0.50 |
| 5.3 | concept_validators 跑 | 1min | $0 |
| 5.2 + 6 | (handoff 修修) | — | — |
| 8 | memory + ADR + final commit/PR | 30min | $0 |

## References

- Plan：`docs/plans/2026-05-07-textbook-ingest-v3-path-b-rewrite.md`
- 5/7 morning handoff：`memory/claude/project_session_2026_05_07_path_b_plan_handoff.md`
- 5/6 burn handoff：`memory/claude/project_session_2026_05_06_07_s8_burn_handoff.md`
- ADR-020：`docs/decisions/ADR-020-textbook-ingest-v3-rewrite.md`（含 §Phase 1.5 freeze）
- Sandcastle main.mts（branch=docs/kb-stub-crisis-memory，已加 vault mount + ANTHROPIC_AUTH_TOKEN）：`E:/sandcastle-test/.sandcastle/main.mts`

## 教訓 → 已寫進 memory

- `feedback_dual_window_worktree.md` 已加「長 AFK pipeline 觸發 → 第一動作 worktree」sub-rule（修修後來 revert 那 sub-rule，原本既有規則仍涵蓋這場景；不要 re-add）
- 主 repo 在另一視窗切 branch 害 batch crash 的事故是 4/25→5/5→5/6→5/7 第四次踩同樣 pattern；下次 fresh session 開長 batch 前**主動開 worktree** 是預防 #1 條
