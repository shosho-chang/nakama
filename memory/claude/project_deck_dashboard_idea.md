---
name: 甲板儀表板（Deck Dashboard）想法
description: Thousand Sunny 首頁設計成 multi-agent 控制台，根據 VPS/Local 顯示不同可操作功能
type: project
originSessionId: b5636c88-2145-418f-b98f-ef364ae150df
---
**修修在 2026-04-17 對話中提出的新功能想法（尚未開 PR，下次對話展開設計）。**

## 核心概念

把 Thousand Sunny 的首頁（`/`）設計成「船員甲板」— 一個 multi-agent 控制台 / dashboard，可以：
- 看到各個 Agent 目前的工作狀態
- 直接從這裡叫某個 Agent 去做事

**Why:** 目前 VPS `/` 是 fallback redirect 到 `/brook/chat`，沒有真正的入口頁。多 agent 系統需要一個 control plane，符合 Nakama 主題。

## 環境差異設計（重要）

不同環境顯示不同可操作功能：

| 環境 | 可叫 Robin ingest？ | 可叫 Brook 聊天？ | 可叫 Zoro 關鍵字研究？ |
|---|---|---|---|
| **VPS** (DISABLE_ROBIN=1) | ❌ 不顯示 | ✅ | ✅ |
| **Local** (Robin 啟用) | ✅ 完整功能 | ✅ | ✅ |

判斷依據：env var `DISABLE_ROBIN`（或更通用的 capability detection）

## 待設計（下次對話展開）

1. **資料來源**：Agent 狀態從哪裡抓？
   - Robin：當前 ingest session 進度（從 `sessions` dict？）
   - Zoro：最近一次 keyword research 結果？
   - Brook：active conversations 數？
   - 是否需要新建 status API endpoint？
2. **UI 風格**：
   - 卡片式（每個 Agent 一張卡）？
   - 海賊王船員形象 icon？
3. **可操作 actions**：
   - 「叫 Robin ingest 這個檔」要從甲板進入 ingest UI 嗎？
   - 還是只是 link 到各 Agent 的子頁面？
4. **Auth**：甲板本身需登入（用 `?next=/`）

## How to apply

下次對話如果修修提到「甲板」、「dashboard」、「首頁」、或要做 Thousand Sunny 的入口頁，從這個 memory 開始展開設計。先做設計討論再開 PR（branch 名建議 `feat/deck-dashboard`）。

依賴前置作業：無（PR #14 已修好 auth，可以開始）。
