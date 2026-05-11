---
name: HITL test 檔 commit 前必跑 ruff format（避免 system-reminder 重 dump 整檔）
description: 寫 failing test → commit → push → CI fail → 才 ruff format → linter 修改觸發 system-reminder dump 整份檔案進 context。2026-05-05 EPUB workflow 5 次踩同坑，~60k token 浪費。
type: feedback
---

寫 HITL phase failing tests 時，**commit 前一定要先跑 `.venv/Scripts/ruff.exe format`** + `ruff check`。不然 push 後 CI 會 fail，被迫補 format commit，linter 修改 → system-reminder 把整份檔案 reinject 進 context。

**Why**：2026-05-05 Slice 1 / 3 / 4 / 5 各踩一次。每次踩到 system 把整份 200-500 行 test 檔重新 dump 進 context（含完整內容，不是 diff）。一次 ~10-15k token。5 次累積 ~60k 浪費。

**How to apply**：

HITL test commit 流程固化：

```bash
# 寫完 test 檔之後
cd E:/nakama && .venv/Scripts/ruff.exe format tests/<your_new_files> 2>&1 | tail -3
cd E:/nakama && .venv/Scripts/ruff.exe check tests/<your_new_files> 2>&1 | tail -3
# 兩個都過了才 git add + commit
```

或更穩：sandcastle agent 跑前我自己一次 `ruff format .` + `ruff check . --fix` 整 repo——確保 baseline 乾淨。

**Edge case**：若 agent 自己也會跑 format（multi-file PR），那 PR 內部 format 衝突就不是我問題。但 HITL phase commit ONLY 是我的 test 檔，這個 ruff 必跑。

**Detection signature**：CI 報「Would reformat: tests/...」加上 system-reminder 開始重 dump 整份檔案。
