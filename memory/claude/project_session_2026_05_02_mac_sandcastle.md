---
name: 收工 — 2026-05-02 Mac sandcastle setup + AFK 副機上線
description: PR #306 + #307 + #308 三 PR chain merged；Mac AFK 副機 sandcastle round 3 通過 (4:23 wall, 1 iter, ~$0.30)；templates 凍結進 repo 跨機 sync；Usopp #270 busy_timeout fix shipped；6 個 Mac-specific gotcha 全清；桌機開機後 sync templates + rebuild image 待辦
type: project
created: 2026-05-02
---

修修 2026-05-02 上 Mac 副機從零裝 sandcastle，沿路踩 6 個 Mac-specific gotcha 全清進 PR #306 templates，trial round 3 解掉 issue #270 (Usopp daemon database lock 修法 #1)，三 PR chain 全 merged。

## 三 PR 戰績

| PR | Title | LOC | 重點 |
|---|---|---|---|
| **#306** | Sandcastle cross-platform templates + Mac runbook | +308/-33 | 6 commits chain：cwd / .gitignore / UID chmod / pip timeout / jq escape / runbook 跨平台拆 macOS vs Windows |
| **#307** | Usopp #270 busy_timeout 5s→30s（sandcastle agent 寫的） | +20/-1 | wall 4:23, 1 iter, context 81k, ~$0.30 估 cost；agent 沿 PR #85 reference 把 fix 從 issue body 寫的 `approval_queue.py:256` redirect 到 `shared/state.py:26` 的 `_get_conn()` central PRAGMA location |
| **#308** | Round 3 收工 doc drift + memory + Dockerfile dev tools 預裝 | +62/-18 | 修 sandcastle.md L111/L113 stale；reference_sandcastle.md round 3 戰績 + Mac 6 坑；Dockerfile 預裝 pytest/ruff/hypothesis/pytest-cov |

## Mac 副機 6 個 gotcha（全清）

桌機 round 1+2 累 4 坑，Mac 新踩 6 個，全進 PR #306 templates。詳見 [reference_sandcastle.md](reference_sandcastle.md) 「Mac setup 6 個 gotcha」段：

1. Templates 漏 `cwd` 設定（跨平台）
2. `.gitignore` 沒屏蔽 `.sandcastle/` artifacts（跨平台）
3. **Mac host UID 502 ≠ image agent UID 1000**（Mac only — 桌機 Win Docker Desktop dodge）
4. Hook timeout 60s 對 nakama 太短（跨平台 — 桌機未踩因為 image 已預裝？或之前 trial 規模小）
5. `prompt.md` jq expression backslash escape（跨平台）
6. **Docker not in PATH on Mac shell**（Mac only）

## 桌機開機後 sync 待辦（修修人為）

1. **拷新版 templates 進 sandcastle-test**：
   ```bash
   cd E:\sandcastle-test
   cp E:\nakama\docs\runbooks\sandcastle-templates\Dockerfile     .sandcastle\Dockerfile
   cp E:\nakama\docs\runbooks\sandcastle-templates\main.mts        .sandcastle\main.mts
   cp E:\nakama\docs\runbooks\sandcastle-templates\prompt.md       .sandcastle\prompt.md
   ```
2. **Image rebuild**（含 dev tools 預裝）：
   ```bash
   docker build -t sandcastle:nakama -f .sandcastle\Dockerfile .sandcastle
   ```
3. **驗 image 含 dev tools**：
   ```bash
   MSYS_NO_PATHCONV=1 docker run --rm --entrypoint /home/agent/.local/bin/pytest sandcastle:nakama --version  # → pytest 9.0.3
   ```

桌機原本 `E:\sandcastle-test\templates\` 跟 canonical version 可能有 diff — 修修自己 review 是否保留某些桌機 customization 再 reconcile。

## Acceptance #270 留 host operator 驗

Usopp `busy_timeout` 5s→30s 已 ship 進 main (PR #307)，但 acceptance #2/#3 待 host:
- **#2**：VPS deploy 後 cron 時段（05:30 / 06:30）e2e publish 跑通一次
- **#3**：連 3 天觀察 `/var/log/nakama-usopp` 0 traceback

修修桌機 deploy 走標準 [docs/runbooks/deploy-usopp-vps.md](../../docs/runbooks/deploy-usopp-vps.md) runbook。**Deploy 後要 restart usopp daemon** 才會 re-init connection 帶新 PRAGMA（`_conn` module-level cache）。

## Sandcastle agent 行為亮點

trial #3 agent 表現比預期好：
- Issue body 寫「修 `approval_queue.py:256`」，但 reference 提「PR #85 PRAGMA 收齊在 `_get_conn()`」 — agent 沿 reference 自動 redirect 到 `shared/state.py:26` central location 改一處覆蓋全 caller
- TDD red→green：先寫 failing test → 確認 red → 改 PRAGMA → confirm green
- 自己 `pip3 install pytest hypothesis ruff` 補 dev tools（這次教訓進 #308 Dockerfile 預裝）
- Acceptance #2/#3 標 deferred 給 host operator，不貪 production 不需要做的事
- Self P7-COMPLETION 報告完整

## 接下來軸線（清對話前 unblock 狀態）

| 軸線 | 狀態 |
|---|---|
| **Mac AFK 副機** | unblock — 修修出門時可以在 Mac 跑 sandcastle |
| **Line 1 端到端 QA** | 修修主導，需要 1 小時雙人訪談音檔 + Phase A-F；Mac Phase 0+B 已綠（PR #304 doc） |
| **Slice 10 (#293) Bridge UI mutation** | unblocked（#270 PR #307 已 merge）；UI 美學留修修做 Claude Design |
| **Issue housekeeping** | #287 stale (PR #299 已 ship)、#283 PRD body 沒更新 Slice 1-9 進度；待修修決定要不要做 |

## 相關記憶 cross-ref

- [reference_sandcastle.md](reference_sandcastle.md) — 完整 round 3 戰績 + Mac 6 gotchas + 跨平台採用模板
- [feedback_dual_window_worktree.md](feedback_dual_window_worktree.md) — 同機雙視窗必開 worktree（本次單視窗 no collide）
- [user_hardware.md](user_hardware.md) — Mac MBP 副機定位（無 CUDA / API-only / Bridge UI 驗收 / sandcastle AFK）
- [feedback_pr_review_merge_flow.md](feedback_pr_review_merge_flow.md) — PR review/merge 全自動流程（本 session 三 PR 都走）
- [feedback_no_handoff_to_user_mid_work.md](feedback_no_handoff_to_user_mid_work.md) — 不在中途 handoff 修修；6 坑全 self-debug 修完，未停下問
