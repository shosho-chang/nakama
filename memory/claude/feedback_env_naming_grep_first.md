---
name: 建議修修申請 API token / 取 env key 名前必先 grep code
description: 要修修申請新 API token / 設 env key / 命名前必須 grep code 真實讀的 naming convention，不能憑印象或從零設計，否則修修申請完要 rename 極度煩躁
type: feedback
created: 2026-05-03
---

**規則**：要修修做 user-facing 命名動作（申請 API token、設 env key、命 bucket、命 service 等）之前，**必先 grep code 確認既有 naming convention**，不能從零設計或憑印象。

**Why**: 2026-05-03 R2 bucket-scoped token 任務，我建議修修申請 token 用 `R2_NAKAMA_BACKUP_*` / `R2_XCLOUD_BACKUP_*` 命名，**但沒 grep code**。實際上 PR #147（2026-04-25 merged）已凍結 `shared/r2_client.py:120-158` mode-scoped fallback chain，code 真讀的是：

- 寫 nakama-backup → `NAKAMA_R2_WRITE_*`
- 讀 nakama-backup → `NAKAMA_R2_READ_*`
- nakama-backup mode-agnostic fallback → `NAKAMA_R2_*`
- Franky 讀 xcloud-backup → `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY`

修修申請完 4 對 token + 寫進 .env，全部用我建議的命名 → code 一個都不讀 → 我才發現要修修 rename。修修原話：「我真的很厭惡做這種工作，會讓我極度的煩躁，之後你叫我申請一些 API 或是 name 之前，你可不可以先做一個完整的搜尋？不要讓我做重複這種工作，然後 rename 這種事實在是太令人厭煩了」。

**How to apply**:

要修修做這些動作前必先 grep：

| 動作 | 必先做的搜尋 |
|---|---|
| 申請新 API token + 取名 | grep `os.getenv\|os.environ\[` 找 code 真讀的 env key 名 |
| 設新 env key | grep 檢查既有同 domain key 命名 convention（前綴/排序/snake-vs-camel） |
| 命新 bucket / service | grep 既有 bucket name / service name pattern + 看 ADR / runbook |
| 命新 DB column / table | grep schema 既有命名（singular vs plural、prefix） |
| 命新 file / directory | check sibling files 既有命名 + 看 docs/decisions/ ADR |

**搜尋三步驟**（最低標準，每個命名建議都要過）：

1. `grep "<the thing being named>"` — 找既有出現
2. 找到既有 module 後 read 50 行 surrounding context — 確認 convention 邏輯
3. 跟我建議的命名 cross-check — 對齊 convention 才 propose

**反面案例**：「我建議叫 `R2_NAKAMA_BACKUP_*` 因為這樣 prefix-sort 容易看」← **不能用**。修修要的是「跟既有 code convention 對齊」，不是「我覺得好看的命名」。

**何時這個規則不適用**：

- **新 module / 第一個 occurrence** — 沒 prior art 時，必須提案命名 convention，但要顯式 flag「這是新 convention，沒既有 code 約束」+ 寫進 ADR
- **修修主動說「重新設計這塊命名」** — 那是顯式重構，不是踩既有 convention

**配套**：[feedback_minimize_manual_friction.md](feedback_minimize_manual_friction.md) 同一條最高指導原則的具體應用 — 命名搜尋失誤 = 修修 rename 摩擦力，要消除。
