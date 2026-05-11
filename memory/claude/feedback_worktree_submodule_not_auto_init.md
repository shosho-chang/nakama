---
name: git worktree add 不會自動 init submodule — foliate-js trip wire
description: 任何新開的 worktree 跑 books reader / 用 foliate-js 前必 git submodule update --init，否則 /vendor/foliate-js/*.js 404 → reader 一片空白
type: feedback
created: 2026-05-10
---

`git worktree add E:/nakama-xxx -b feat/yyy origin/main` **不會** 把 submodule 一起 init。`vendor/foliate-js/` 在新 worktree 是空目錄；book reader 的 `import { View } from '/vendor/foliate-js/view.js'` 拿到 404，reader 頁載入但 foliate-view shell 空白一片。

`thousand_sunny.app` 在 boot 時做 `if _foliate_dir.is_dir()` 判斷掛 `/vendor/foliate-js` StaticFiles mount。空目錄 → 條件 False → mount 整個跳過。**即使你後來補跑 submodule init，uvicorn 仍要重啟才會掛上 mount**（is_dir 是 boot-time check）。

**Why:** Phase 1 monolingual-zh pilot 2026-05-10 acceptance walkthrough — 修修拖中文 EPUB 進 upload form，上傳成功（DB row 正確）但開 `/books/{id}` 一片空白；debug 才發現 worktree 沒 init submodule，需重啟 uvicorn。

**How to apply:** 開新 worktree 跑書相關 acceptance（reader / annotations / digest 任何用 foliate-js 的 path）之前：

```bash
git worktree add E:/nakama-<topic> -b feat/<branch> origin/main
git -C E:/nakama-<topic> submodule update --init --recursive
```

`--recursive` 為了萬一未來加 nested submodule；目前 nakama 只有 foliate-js 一個。

對 non-book worktree（純後端 / API / docs）不需要 init submodule — 跳過省時間。

延伸：sandcastle 任務 dispatch prompt 若涉及 reader，必須包含「submodule update --init」步驟，否則 agent 在 cloud worktree 也會踩同一個坑。
