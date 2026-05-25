---
name: Sandcastle GH_TOKEN 過期的徵兆 + 快速 rotate（host gh auth token → .sandcastle/.env）
description: 2026-05-25 overnight dispatch 時 .sandcastle/.env 的 GH_TOKEN 失效（3 週沒用），PromptPreprocessor `gh issue list` 401 unauthorized → sandcastle 直接 abort 0 iteration。fix = `gh auth token` 取 host 現用 token 覆寫
type: reference
---

`E:/sandcastle-test/` 的 `.sandcastle/.env` 內 `GH_TOKEN` 跟 host machine 的 `gh auth status` 是兩套 credential。host 的 keyring auth 隨時用 OK，但 `.env` 內的 PAT 三週就過期（GitHub PAT default 90d expiry 算短的；fine-grained token 也常 30/60d）。

**徵兆**：

```
PromptError: Command `gh issue list --label sandcastle ...` exited with code 1:
non-200 OK status code: 401 Unauthorized
body: {"message": "Bad credentials", "documentation_url": "https://docs.github.com/rest", "status": "401"}
```

PromptPreprocessor 在 host 端執行 prompt 內的 `!gh ...` shell embed，吃 `.env` 內 GH_TOKEN → 401 → sandcastle 直接 abort，**0 iteration 跑**。

**Fix（5 分鐘）**：

```powershell
# 1. 從 host gh keyring 撈當前 valid token
gh auth token > E:/tmp/_token.txt
# 內容類似 ghp_xxxxxxxx... 或 gho_xxxxxxxx...

# 2. 編輯 .sandcastle/.env，替換 GH_TOKEN=<old> 那行為新值
# （手動 notepad，或寫 PowerShell 一行）

# 3. 重跑 Sandcastle
cd E:/sandcastle-test
$env:MSYS_NO_PATHCONV='1'; npx tsx .sandcastle/main.mts
```

**注意**：

- `.sandcastle/.env` 不該 git commit（已在 .gitignore），所以 rotate 後不會污染 repo
- 容器內也用同個 GH_TOKEN（main.mts `env: passthrough`），所以 rotate 一次同時修 host preprocessor + container agent 兩處
- Claude 自動跑 sandcastle 時若遇此 401 應**不要嘗試自動寫 .env**，credential rotation 是 user-only action
- 一般 90 天 review 一次（calendar reminder）or 用 fine-grained token + 設 expiry 提醒避免凌晨踩到
- 跟 [feedback_sandcastle_default] + [reference_sandcastle] cross-ref；本檔專責 token 一個 failure mode
