---
name: Matt Pocock Sandcastle — TS library for AFK Claude Code in Docker worktrees
description: github.com/mattpocock/sandcastle；5 templates；defaults sonnet-4-6；2026-04-30 首次試水成功 issue #240 → PR #263，3:56 wall，~$0.26
type: reference
created: 2026-04-29
updated: 2026-04-30
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

### 下一步

- 第 2 試水：issue #239（API kwarg）— 等 #263 merge 後跑
- 若 2/3 次成功 → 寫 sandcastle 操作手冊到 `docs/runbooks/`，正式採用
- 若 ≥1 次連敗 → 回 single-worktree 模式
