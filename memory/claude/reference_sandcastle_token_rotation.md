---
name: Sandcastle 401 Bad credentials = 99% 漏 --env-file flag, 不是 token 過期
description: 2026-05-25→26 反覆踩坑 — Sandcastle 401 看起來像 token 過期，實際是 `npx tsx .sandcastle/main.mts` 漏掉 `--env-file=.sandcastle/.env`，process.env.GH_TOKEN 變成 undefined 字串傳進 docker -e GH_TOKEN=undefined。先驗 flag 再考慮 rotate
type: reference
---

## 徵兆

```
PromptError: Command `gh issue list --label sandcastle ...` exited with code 1:
non-200 OK status code: 401 Unauthorized
body: {"message": "Bad credentials", "status": "401"}
```

PromptPreprocessor 在 docker 容器內呼叫 `gh issue list`，gh 拿不到有效 token → 401 → sandcastle abort，**0 iteration 跑**。

## 真正 root cause（2026-05-26 確認）

`E:/sandcastle-test/.sandcastle/main.mts` 第 7 行註解明寫：

```
npx tsx --env-file=.sandcastle/.env .sandcastle/main.mts
```

但 `session_handoff` / playbook / Claude 預設指令常常**漏掉 `--env-file=.sandcastle/.env`**：

```powershell
npx tsx .sandcastle/main.mts   # ❌ process.env.GH_TOKEN = undefined
```

`main.mts` 用 `GH_TOKEN: process.env.GH_TOKEN!` 把 env pass 進 docker container。TS 的 `!` non-null assertion 只是編譯時 type；runtime undefined 會變成字串 `"undefined"` 進 `docker run -e GH_TOKEN=undefined`。容器內 gh 把 "undefined" 當 token 丟給 GitHub → 401 Bad credentials。

**Host gh auth token 跟 .env 裡 GH_TOKEN 即使完全相同也不會 fix 這個** — 因為根本沒被 load 進 process.env。

## Diagnose order（5 分鐘）

別預設 token 過期，先按順序驗：

1. **檢查命令是否含 `--env-file=.sandcastle/.env`** → 99% 答案在這
2. Host `gh auth token` 是否還 valid：`curl -H "Authorization: token $(gh auth token)" https://api.github.com/user` 看 200 還是 401
3. `.env` 內 token 跟 host 是否一致：`grep ^GH_TOKEN= E:/sandcastle-test/.sandcastle/.env` vs `gh auth token`
4. **容器內** gh 是否能用該 token（最深層）：
   ```
   TOKEN=$(grep ^GH_TOKEN= .env | cut -d= -f2-)
   docker run --rm --entrypoint bash -e GH_TOKEN="$TOKEN" sandcastle:nakama -c 'gh api user'
   ```

如果 1 是真，2/3/4 完全沒必要查。

## 正確 dispatch 命令

```powershell
cd E:/sandcastle-test
$env:MSYS_NO_PATHCONV='1'
npx tsx --env-file=.sandcastle/.env .sandcastle/main.mts
```

PowerShell 也可：

```powershell
$env:MSYS_NO_PATHCONV='1'; cd E:/sandcastle-test; npx tsx --env-file=.sandcastle/.env .sandcastle/main.mts
```

## Token 真的過期的情況（罕見）

GitHub PAT default 90d；fine-grained token 30/60d。host gh OAuth `gho_*` 由 gh CLI 維護自動續期，幾乎不會過期。

若步驟 2 host curl 也回 401 → token 真的過期：

```powershell
gh auth refresh   # 或 gh auth login 重新走 OAuth
# 然後 sync .env:
$tok = gh auth token
(Get-Content .sandcastle/.env) -replace '^GH_TOKEN=.*', "GH_TOKEN=$tok" | Set-Content .sandcastle/.env
```

## 注意

- `.sandcastle/.env` 不該 git commit（在 .gitignore）
- 容器內跟 host preprocessor 都用同個 GH_TOKEN（main.mts `env: passthrough`）
- Claude 自動跑 sandcastle 時遇 401 應**不要嘗試自動寫 .env**，credential rotation 是 user-only action；但**可以**自動 retry with `--env-file` flag（這只是 invocation 修正）
- 跟 [[feedback_sandcastle_default]] + [[reference_sandcastle]] cross-ref；本檔專責 401 一個 failure mode
