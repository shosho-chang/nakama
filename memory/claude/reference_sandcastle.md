---
name: Matt Pocock Sandcastle — TS library for AFK Claude Code in Docker worktrees
description: github.com/mattpocock/sandcastle；5 templates；defaults sonnet-4-6；4/4 通過（桌機 Win 3 + Mac 1）含首次 multi-issue batch run；templates 凍結進 docs/runbooks/sandcastle-templates/；runbook 在 docs/runbooks/sandcastle.md
type: reference
created: 2026-04-29
updated: 2026-05-02
---

Matt Pocock 的 AFK runner，**TS library** in Docker，跟 mattpocock/skills repo 是 paired 工具但分開 repo。**不是** Anthropic claude-code-plugins 的 ralph-loop plugin（Matt 自己拒絕後者）。

Repo: https://github.com/mattpocock/sandcastle

## 架構（2026-04 update）

- **Planner agent** — 看 issue backlog 決定哪個先做
- **Implementation agents** × N — 每個 sandboxed in Docker on independent git branch / worktree
- **Reviewer agent** — Opus 把關
- **Merger agent** — 整合 + conflict resolve + patch back to target branch

## Matt 自己的關鍵主張

1. **「Anthropic ralph-loop plugin sucks」** — https://www.aihero.dev/why-the-anthropic-ralph-plugin-sucks。理由：plugin 把所有 iter 塞同 session，context 累積腐壞；bash loop 每 iter fresh context 才在「smart zone」
2. **HITL Ralph + AFK Ralph 兩階段** — 難的部分人類一起做，搞清楚才丟 AFK

## Templates (5 種，src/templates/)

| Template | 用途 |
|---|---|
| `blank` | 裸 scaffold，自己寫 |
| `simple-loop` | 單 agent 序列拉 GitHub issue，最簡單 |
| `sequential-reviewer` | implementer + reviewer 兩階段，中等複雜度 |
| `parallel-planner` | planner 拆任務 + N implementer 並行 |
| `parallel-planner-with-review` | planner + parallel impl + per-branch reviewer，最完整 4-agent |

## 預設 Dockerfile 結構（重要）

`node:22-bookworm` base + `git/curl/jq/gh` + `agent` user (UID 1000，由原 `node` user rename) + Claude Code CLI 透過 `curl https://claude.ai/install.sh | bash` + `WORKDIR /home/agent` + `ENTRYPOINT ["sleep","infinity"]`。Sandcastle bind-mount git worktree 到 `/home/agent/workspace`。

對 Python 專案（nakama）只要在這基底上 apt install `python3 python3-pip` + 設 `PIP_USER=1` 就夠（不需 venv，sandbox 已隔離）。

## Reviewer cost 不是問題

simple-loop 和 sequential-reviewer 兩個 template main.mts 預設都用 `claudeCode("claude-sonnet-4-6")`，不是 Opus。原本擔心的成本 gap 解除。

## 對 nakama 的 fit（updated 2026-04-30）

| Gap | 解法 |
|---|---|
| 語言 (TS/npm) | 裝在 `E:\sandcastle-test\` 獨立目錄、`cwd: "../nakama"` 指向 target；nakama 不污染 |
| Dockerfile | 客製版已寫在 `E:\sandcastle-test\templates\Dockerfile`（node:22-bookworm + python3） |
| Docker Desktop | 修修 2026-04-30 下載中 |
| Reviewer cost | N/A — 預設已 sonnet-4-6 |
| Node.js | 也要裝（Win 上沒）— 跟 Docker 一起補 |

## 2026-04-30 改決定：試水

修修決定 override 「先不裝」拍板，理由：「想試試看」。
- 試水 candidate：issue #240（pure test add）、#239（小 API 加 kwarg）
- 安裝包：runbook 在 `E:\sandcastle-test\RUNBOOK.md`，4 客製檔在 `E:\sandcastle-test\templates\`
- 退場條件：3 次連敗 / cost > baseline 2x / setup > 3h
- 退場做：PowerShell 回收桶刪 sandcastle-test + docker rmi + label delete + 回 single-worktree 模式

Baseline 對照：[feedback_phase3_single_worktree_proven.md](feedback_phase3_single_worktree_proven.md) PR #260 single-worktree sequential 2h50m / 4 slices / 0 leak。

## 2026-04-30 首次試水結果（issue #240 → PR #263）

成功端到端：sandcastle 自取 issue → 探索 → 寫測試 → ruff/pytest 全綠 → commit → merge 回 host branch。
**3:56 wall**（13:23:58 → 13:27:54），image build 5 分鐘一次性、image size 2.1GB、final context 60k tokens、估 cost ~$0.26。

### 踩到 4 個坑（裝 sandcastle 時）

1. **`npx sandcastle init` 互動 prompt 4 個（sandbox provider / backlog manager / label / build image）不吃 CLI flag** — `--template` `--agent` `--model` 三個 flag 設了仍會卡 clack `select`。解：手動建 `.sandcastle/` + 拷 4 個檔（`Dockerfile` / `main.mts` / `prompt.md` / `.env.example`）+ 補 `.gitignore`（`.env\nlogs/\nworktrees/\n`）。
2. **`.sandcastle/.env` 的 lookup 路徑是 `<cwd>/.sandcastle/.env`，cwd 是 target repo (`../nakama`)** — 我裝在 `E:\sandcastle-test\`、env 在那邊但 sandcastle 找的是 `E:\nakama\.sandcastle\.env`。解：main.mts 內顯式 `docker({ env: { ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY!, GH_TOKEN: process.env.GH_TOKEN! } })` + 跑 `tsx --env-file=.sandcastle/.env`。
3. **MSYS path mangling 把 `/home/agent/...` 解成 `C:/Program Files/Git/home/agent/...`** — `docker run --entrypoint /home/agent/.local/bin/claude ...` 直接炸。解：前綴 `MSYS_NO_PATHCONV=1`。
4. **container 內 `gh issue close` 被 sandbox 擋** — agent 完成 commit 但無法 close issue，PR body 加 `closes #N` 走 auto-close 替代。

### 對 nakama 的價值定位（first run 後）

| 條件 | 適用 | 不適用 |
|---|---|---|
| Issue 範圍 | 單 file / pure test add / 小 kwarg / 非業務邏輯 | 跨多 module、需設計判斷、UI 美學 |
| 期望品質 | TDD 風格、結構複製現成 pattern | 創意架構、新 abstraction |
| 監督模式 | AFK（睡覺/吃飯時跑）+ 醒來 review PR | 即時 iterate、邊做邊改方向 |

## 2026-04-30 第二試水結果（issue #239 → PR #264）

成功且更實質：4-file 改動（2 production + 2 test），closes ADR-009 T6 encapsulation breach（switch `client._get_service()` private → `client.query()` public）。
agent 自走 TDD（red 驗證 → green 實作）+ 自 P7-COMPLETION 報告。

| | Trial #1 (#240) | Trial #2 (#239) |
|---|---|---|
| Wall | 3:56 | ~6:50 |
| Context | 60k | 80k |
| 估 cost | ~$0.26 | ~$0.40 |
| Files | 1 test | 4（2 prod + 2 test） |
| 涉及 production code | ❌ | ✅ |
| TDD discipline | n/a（pure test add） | ✅ red→green |
| Result | ✅ | ✅ |

## 2026-05-02 第三試水結果（Mac AFK 首跑，issue #270 → PR #307）

Mac MacBook Pro 副機首次裝 sandcastle setup，目標 issue #270（Usopp daemon `database is locked` busy_timeout 拉長）。Setup 一次性裝 ~45 min（含 6 個 Mac-specific gotcha 除錯，全凍結進 PR #306 templates），trial run wall 4:23、1 iter、context 81k。

| | Trial #1 (#240) | Trial #2 (#239) | Trial #3 (#270) |
|---|---|---|---|
| Host | 桌機 Win | 桌機 Win | **Mac** |
| Wall | 3:56 | ~6:50 | ~4:23 |
| Context | 60k | 80k | 81k |
| pip install (cold) | n/a | n/a | 92.5s |
| 估 cost | ~$0.26 | ~$0.40 | ~$0.30 |
| Files | 1 test | 4 (2 prod + 2 test) | 2 (1 prod + 1 test) |
| Production code | ❌ | ✅ | ✅ |
| TDD discipline | n/a | ✅ red→green | ✅ red→green |
| Self P7-COMPLETION | n/a | ✅ | ✅ |
| Result | ✅ | ✅ | ✅ |

**Agent 行為亮點**：
- Issue body 寫「修 `approval_queue.py:256`」但同時提「PR #85 把 PRAGMA 收齊在 `_get_conn()`」— agent 沿 reference 找到 `shared/state.py:26` 的 central PRAGMA location，正確 redirect 修對地方
- 自己 `pip3 install pytest hypothesis ruff` 補 dev tools（Dockerfile 沒預裝）
- Acceptance #2/#3（VPS smoke + 3-day observation）標 deferred 給 host operator，不貪心

## Mac setup 6 個 gotcha（PR #306 全清）

桌機 Windows trial 1+2 累 4 坑，Mac trial 新踩 6 個：

1. **Templates 漏 `cwd` 設定**（跨平台） — sandcastle 預設 `process.cwd()` 當 target，sandcastle-test 不是 git repo會炸。修：`main.mts` 加 `cwd: "../nakama"`。
2. **`.gitignore` 沒屏蔽 `.sandcastle/` artifacts**（跨平台） — sandcastle 在 cwd 寫 `.sandcastle/{worktrees,logs,patches}/`，污染 nakama working tree。修：nakama `.gitignore` 加 `.sandcastle/`。
3. **Mac host UID 502 ≠ image agent UID 1000**（**Mac only**） — sandcastle `--user $hostUid` 跑 container，host 502 進 image 沒 `/home/agent` 寫權限 → SandboxLifecycle.ts 第一步 `git config --global` 直接炸 (exit 255)。修：Dockerfile 末尾 `chmod -R 0777 /home/agent`。Linux UID 1000 dodge；Windows Docker Desktop UID mapping 不同也 dodge。
4. **Hook timeout 60s 對 nakama 太短**（跨平台） — 預設 `GIT_SETUP_TIMEOUT_MS = 60000`，nakama requirements.txt 61 行 deps cold container 90s+。修：`main.mts` hook 加 `timeoutMs: 600000`（10 min）。
5. **`prompt.md` jq expression backslash escape**（跨平台） — 寫成 `join(\",\")` jq parser unexpected token `\\`。修：拿掉多餘 escape 成 `join(",")`，shell single-quote 已包整段不需二次 escape。
6. **Docker not in PATH on Mac shell**（**Mac only**） — Docker Desktop 安裝沒自動進 zsh PATH。修：用 absolute path `/Applications/Docker.app/Contents/Resources/bin/docker` 或 `PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"` 前綴。

加上桌機 round 1 那 4 坑（init prompt / .env lookup / MSYS path mangling / `gh issue close` 擋）— 完整 10 坑全清，templates 凍結在 [docs/runbooks/sandcastle-templates/](../../docs/runbooks/sandcastle-templates/) 跨機 sync。

## 2026-05-05 第四試水結果（首次 multi-issue batch，PR #426）

桌機 Win，3 issue 一次跑（FU-2 #421 reader sidebar 反思入口、FU-3 #422 queue_processor --watch、FU-5 #424 textbook-ingest OS-neutral docs），`maxIterations: 5` + `merge-to-head` strategy。

| | Trial 1 (#240) | Trial 2 (#239) | Trial 3 (#270) | Trial 4 (3 issues) |
|---|---|---|---|---|
| Host | 桌機 Win | 桌機 Win | Mac | 桌機 Win |
| Wall | 3:56 | ~6:50 | ~4:23 | **~12 min** |
| Files | 1 test | 4 (2+2) | 2 (1+1) | 4 across 3 commits |
| Production code | ❌ | ✅ | ✅ | ✅ |
| Multi-issue | ❌ | ❌ | ❌ | ✅（3 個） |
| Result | ✅ | ✅ | ✅ | ✅ 3/3 commits 落地 |

**亮點**：
- agent 沒因 prompt.md「UI/aesthetic out of scope」自我跳過 #421 — 那是 wiring（既有 modal + 既有 .icon-btn），不是真的設計工作，agent 正確判斷做了
- 一次 invocation 12 min vs 三次 invocation 估計 18-21 min（sandbox setup 攤平）— multi-issue batch 是 throughput 倍增器
- 全部 sonnet-4-6，估 cost ~$1 整個 batch

**操作教訓**：merge-to-head 把 3 commits 都疊到 host branch，PR review 變寬（3 個 unrelated domain）；若要 focused PR-per-issue 得 cherry-pick 拆出來。下次 multi-issue batch 看 issue 是否 same-domain；不同 domain 還是分批跑乾淨。

## 結論：4/4 通過 → 信心擴大 + multi-issue 模式進入工具箱

**正式採用 + Mac AFK 副機 unblock**。Sandcastle 收進 nakama 工具集，runbook 在 [docs/runbooks/sandcastle.md](../../docs/runbooks/sandcastle.md)；canonical templates 在 [docs/runbooks/sandcastle-templates/](../../docs/runbooks/sandcastle-templates/)。

退場條件保留（3 次連敗 / cost > baseline 2x / 一次嚴重 leak），目前無觸發。

### 採用後操作模板（跨平台）

1. tag issue `sandcastle` label（先確認 acceptance 條件清楚）
2. 跑 sandcastle：
   - **macOS**：`cd ~/Documents/sandcastle-test && npx tsx --env-file=.sandcastle/.env .sandcastle/main.mts`
   - **Windows (Git Bash)**：`cd E:\sandcastle-test\ && MSYS_NO_PATHCONV=1 npx tsx --env-file=.sandcastle/.env .sandcastle/main.mts`
3. agent 完工後在 host：sandcastle 已 merge 進 host current branch；push branch + 開 PR
4. 在 host run `/review` + `gh pr merge --squash --delete-branch`（round 2 後 `gh issue close` 不再被擋，但走 PR auto-close 仍是 canonical 路徑）

完整 SOP 見 docs/runbooks/sandcastle.md。
