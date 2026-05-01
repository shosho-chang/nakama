# Runbook：用 Sandcastle 跑 AFK Issue

[Sandcastle](https://github.com/mattpocock/sandcastle)（Matt Pocock）是 TS library — 在 Docker 容器裡跑 Claude Code agent 自動拉 GitHub issue → 寫程式 → 跑測試 → commit → merge 回 host branch。對 nakama 適合「明確 acceptance、單 file 或小範圍 production code change、無設計判斷」的 issue。

詳細背景見 [memory/claude/reference_sandcastle.md](../../memory/claude/reference_sandcastle.md)。

## 何時用 vs 不用

| 適用 | 不適用 |
|---|---|
| Pure test addition（如 PR #263） | UI / 美學任務 |
| 小 API 加 kwarg + closing tech debt（如 PR #264） | 跨多 module 的設計改動 |
| Acceptance 條列清楚、TDD 可走 | 需要設計判斷的 spike |
| 你想睡覺 / 吃飯 / AFK | 你正要邊做邊改方向 |

對比 baseline：single-worktree sequential AFK（[feedback_phase3_single_worktree_proven.md](../../memory/claude/feedback_phase3_single_worktree_proven.md)）。Sandcastle 的勝點是 isolation（Docker sandbox）+ 自動 merge-back，但 setup 一次性 ~30 分鐘。

## Prerequisites

第一次跑前 host 機器要齊：

```bash
docker --version    # ≥ 27.x，daemon running
node --version      # ≥ 22.x
npm --version       # ≥ 10.x
gh auth status      # logged in，token 含 repo scope
```

## One-time setup（跨平台）

Sandcastle 裝在 nakama repo **外部 sibling 目錄**，避免污染 nakama node_modules。客製過的 4 個 templates 凍結在 nakama repo `docs/runbooks/sandcastle-templates/`，從那邊拷貝即可（不要再從別處抄）。

平台對照：

| Platform | sandcastle-test 路徑 | nakama 路徑 |
|---|---|---|
| macOS / Linux | `~/Documents/sandcastle-test/` | `~/Documents/nakama/` |
| Windows | `E:\sandcastle-test\` | `E:\nakama\` |

```bash
# macOS / Linux 範例（Windows 換對應路徑、用 Git Bash 或 WSL）
cd ~/Documents/sandcastle-test  # 不存在就先 mkdir
npm init -y
npm install --save-dev @ai-hero/sandcastle tsx

mkdir -p .sandcastle
cp ~/Documents/nakama/docs/runbooks/sandcastle-templates/Dockerfile     .sandcastle/Dockerfile
cp ~/Documents/nakama/docs/runbooks/sandcastle-templates/main.mts        .sandcastle/main.mts
cp ~/Documents/nakama/docs/runbooks/sandcastle-templates/prompt.md       .sandcastle/prompt.md
cp ~/Documents/nakama/docs/runbooks/sandcastle-templates/.env.example    .sandcastle/.env.example

printf '.env\nlogs/\nworktrees/\nnode_modules/\n' > .sandcastle/.gitignore

# 填 .env (secrets — 不要打進 chat)
grep "^ANTHROPIC_API_KEY=" ~/Documents/nakama/.env > .sandcastle/.env
echo "GH_TOKEN=$(gh auth token)" >> .sandcastle/.env
```

Build image（一次性 ~5-10 分鐘）：

```bash
cd ~/Documents/sandcastle-test
docker build -t sandcastle:nakama -f .sandcastle/Dockerfile .sandcastle
```

驗證（macOS / Linux）：

```bash
docker run --rm --entrypoint python3 sandcastle:nakama --version    # → Python 3.11.x
docker run --rm --entrypoint gh sandcastle:nakama --version          # → gh 2.x
docker run --rm --entrypoint /home/agent/.local/bin/claude sandcastle:nakama --version
```

Windows 用 Git Bash 跑 docker 要加 `MSYS_NO_PATHCONV=1` 前綴（path mangling 防護，下方坑段詳述）。

## Run procedure

### 1. Tag issue

```bash
gh issue edit <N> --add-label "sandcastle"
```

確認 issue body 有清楚的 `## Acceptance` 條列（複製 PR #238 / #239 風格）。

### 2. 確認 nakama working tree 乾淨

```bash
# macOS/Linux
cd ~/Documents/nakama
# Windows: cd E:/nakama

git status --short   # 應為空；若有未 commit 改動 → stash 後再跑
git stash push -m "before-sandcastle" -- <file1> <file2>
```

### 3. 跑 sandcastle

```bash
# macOS/Linux
cd ~/Documents/sandcastle-test
npx tsx --env-file=.sandcastle/.env .sandcastle/main.mts

# Windows (Git Bash)
cd E:/sandcastle-test
MSYS_NO_PATHCONV=1 npx tsx --env-file=.sandcastle/.env .sandcastle/main.mts
```

預期：

- 容器啟動 → `pip install` 跑完（cold container ~90-120s for nakama 60+ deps；image cache 命中 ~10s）
- agent 拉 `gh issue list --label sandcastle` → 看到目標 issue
- agent explore → plan → write test → run pytest → commit Conventional Commits format（`fix(scope): summary` + `Closes #N`）
- agent commit message 含 `Closes #N`（PR merge 時 GitHub auto-close）— round 2 驗證 `gh issue close` 不再被 sandbox 擋，但走 PR auto-close 仍是 canonical 路徑
- 輸出 `<promise>COMPLETE</promise>`
- 主機端 sandcastle 把 worktree branch merge 回當前 HEAD

第一個 issue 預估 wall：~4-7 分鐘（image cache + 單 issue）。

### 4. 把 commit 移到乾淨 branch + 開 PR

agent 是 merge 回**當前 host branch**。如果你不想把 commit 留在那條 branch，rebase 出來：

```bash
# macOS/Linux: cd ~/Documents/nakama
# Windows: cd E:/nakama

# 假設目前在 chore/something，commit 是 da7aef5
git switch -c sandcastle/issue-<N>
git branch -f chore/something <previous-HEAD>     # 把原 branch label 退回
git rebase --onto main <previous-HEAD> sandcastle/issue-<N>   # 只留 sandcastle commit on main
git push -u origin sandcastle/issue-<N>
gh pr create --base main --title "..." --body "... Closes #<N>"
```

`Closes #<N>` 寫在 PR body，merge 時 GitHub 會自動 close issue。

### 5. Review + merge

Focused PR (<100 LOC、單 domain、tests 綠) 走標準流程：

```bash
gh pr merge <PR#> --squash --delete-branch
git switch chore/something && git stash pop   # 還原 stash
```

## 已知坑（預先警示）

1. **`npx sandcastle init` 互動 prompt 不吃 CLI flag**（跨平台） — `--template/--agent/--model` 設了仍會卡 4 個 clack `select`。**解：跳過 init、從 `docs/runbooks/sandcastle-templates/` 拷檔手動建 `.sandcastle/`**（已是 above setup 流程）。

2. **`.sandcastle/.env` lookup 是 `<cwd>/.sandcastle/.env`，cwd 是 target repo**（跨平台） — 我們把 `.sandcastle/` 放在 sandcastle-test 但 sandcastle 找的是 `nakama/.sandcastle/.env`。**解：`main.mts` 內顯式 `docker({ env: { ANTHROPIC_API_KEY: ..., GH_TOKEN: ... } })` + 跑 `tsx --env-file=.sandcastle/.env`**（templates/main.mts 已含此修正）。

3. **MSYS path mangling 把 `/home/agent/...` 解成 `C:/Program Files/Git/home/agent/...`**（**Windows only**） — `docker run --entrypoint /home/agent/...` 直接炸。**解：前綴 `MSYS_NO_PATHCONV=1`**。macOS / Linux 不會踩。

4. **container 內 `gh issue close` 曾被 sandbox 擋**（historical，round 2 之後不再擋） — round 1 PR #263 / #264 時 sandbox 政策擋 `gh issue close`，round 2（PR #288/#289）已 work。仍建議走 PR body `Closes #N` auto-close 為 canonical 路徑，避免依賴 sandbox 政策飄移。

## Exit criteria（採用後監控）

任一觸發 → 退場回 single-worktree 模式：

- 連 3 次試水沒 clean exit
- 平均 cost > PR #260 baseline 2x
- 一次嚴重 leak（污染 main tree、commit 到無關 branch、刪到無關檔案）

退場做：

```bash
docker rmi sandcastle:nakama
docker image prune -f
gh label delete sandcastle --yes

# macOS/Linux: 刪 sandcastle-test/
rm -rf ~/Documents/sandcastle-test  # CLAUDE.md rm deny — 改用 mv 進垃圾桶或 Finder 拖

# Windows: PowerShell 回收桶刪 E:\sandcastle-test\
# Add-Type -AssemblyName Microsoft.VisualBasic
# [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory('E:\sandcastle-test', 'OnlyErrorDialogs', 'SendToRecycleBin')
```

並更新 [memory/claude/reference_sandcastle.md](../../memory/claude/reference_sandcastle.md) 記錄退場理由。

## 試水戰績（截至 2026-05-02）

| Trial | Issue | PR | Host | Wall | Context | 估 cost | 結果 |
|---|---|---|---|---|---|---|---|
| #1 | #240（5xx retry test） | #263 | 桌機 Win | 3:56 | 60k | ~$0.26 | ✅ |
| #2 | #239（GSCClient kwarg） | #264 | 桌機 Win | ~6:50 | 80k | ~$0.40 | ✅ |
| #3 | #270（busy_timeout PRAGMA） | #307 | **Mac** | ~4:23 | 81k | ~$0.30 | ✅ |

3/3 通過。Mac AFK 副機首跑驗證跨平台 setup work（PR #306 凍結 templates + 6 個 Mac-specific gotchas 全清）。詳見 [reference_sandcastle.md](../../memory/claude/reference_sandcastle.md)。
