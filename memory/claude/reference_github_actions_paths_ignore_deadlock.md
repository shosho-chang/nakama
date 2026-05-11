---
name: GitHub Actions paths-ignore + required-check deadlock
description: 在 branch protection 要求 status check 的 repo 上，paths-ignore 會讓純 docs PR 永遠 BLOCKED。解法：companion workflow 鏡像 paths 過濾，emit 同名 job 的 SUCCESS。
type: reference
---

## 問題

當 `.github/workflows/ci.yml` 用 `paths-ignore: docs/**, **.md, memory/**` 來省 GHA quota，遇上 branch protection 要求 `lint-and-test` status check 時：

- 純 docs / memory PR 的所有 path 都 match ignore → workflow 被**整個 skip**
- Skip 的 workflow **不 emit status check**
- branch protection 看不到 `lint-and-test` → 永遠 BLOCKED
- `enforce_admins: true` 時，`gh pr merge --admin` 也被擋
- 結果：純 docs PR merge 不進去

## 解法

加 companion workflow（命名要不同 `name:` 但 job 名要跟 required check 一樣）：

```yaml
# .github/workflows/ci-skip.yml
name: CI (skip-pass)

on:
  push:
    branches: [main]
    paths:                   # ← 鏡像 ci.yml 的 paths-ignore
      - 'docs/**'
      - '**.md'
      - 'memory/**'
      - '.github/ISSUE_TEMPLATE/**'
  pull_request:
    branches: [main]
    paths:
      - 'docs/**'
      - '**.md'
      - 'memory/**'
      - '.github/ISSUE_TEMPLATE/**'

jobs:
  lint-and-test:             # ← job 名必須跟 required check 一樣
    runs-on: ubuntu-latest
    steps:
      - run: echo "Auto-pass (only ignored paths changed)"
```

## 互斥保證

| PR 類型 | ci.yml | ci-skip.yml | required check 過？|
|---|---|---|---|
| 純 docs/memory | skip | run+pass | ✅ ci-skip 補滿 |
| 純 code | run | skip | ✅ ci.yml 真跑 |
| Mixed | run | run | ✅ 兩個都過 |

## Drift 風險

`ci-skip.yml` 的 `paths` 必須跟 `ci.yml` 的 `paths-ignore` **完全鏡像**。若有人改了 `ci.yml` 沒同步 `ci-skip.yml`，deadlock 會回來。

緩解：兩個檔案各自加 header comment 提醒對方存在 + 同步義務。

## 為什麼 panel 沒抓到

2026-05-08 設計 panel（Claude → Codex → Gemini × 2 round）審 memory system redesign 時討論過 `paths-ignore` 但沒模擬「pure docs PR + required status check」的 interaction。三個 model 都默認「paths-ignore = workflow skip = OK」，沒有走完整 status-check 流程。**實際 ship PR #504 才撞到**（PR #504 是首個觸發此 deadlock 的 PR）。

修補 PR #505 加 ci-skip.yml 變成 v2 設計補丁。

## 適用範圍

任何 repo 同時有：
1. `paths-ignore` 在 required workflow
2. branch protection require 該 workflow 的 status

都會遇到。常見於：
- monorepo 想省 CI
- docs-heavy repo 想跳過 lint
- 任何「不想對純 doc commit 跑 test」的設定

## 相關檔案

- `.github/workflows/ci.yml`（main workflow，含 paths-ignore）
- `.github/workflows/ci-skip.yml`（companion，要鏡像 paths）
- 引入 PR：#505
- 受害 PR：#504（推到一半才發現）

## 教訓

設計 CI 路徑優化時，必須走完 status check 全流程才算驗證 — 不能停在「workflow skipped 看起來 OK」。
