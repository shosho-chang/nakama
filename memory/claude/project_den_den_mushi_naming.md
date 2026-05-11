---
type: project
visibility: claude
agent: claude
confidence: high
created: 2026-05-10
expires: permanent
status: DEPRECATED 2026-05-10
superseded_by: project_news_coo_naming_and_scope.md
name: Den Den Mushi 命名決策
description: Standalone Chrome extension 命名分層：repo / manifest / 簡稱 / code identifier 對應
---

Standalone Chrome extension（網頁抓取 + LLM 翻譯 + 寫入 Vault），與 Nakama 其他 agent 不同 process / 不同 repo。

## 命名分層

| 層 | 名字 |
|---|---|
| Repo dir | `E:\den-den-mushi` |
| Git branch prefix | `feat/...` (一般 nakama 慣例) |
| Manifest `name` (Chrome store / toolbar tooltip) | `Den Den Mushi` |
| 對話 / commit / log 簡稱 | `Mushi` |
| Code identifier (class/module) | `DenDenMushi` 或 `Mushi` (TBD by code style) |

## Why
- One Piece 主題延續 Nakama 命名（Robin/Nami/Zoro/Brook/Franky/Usopp/Sanji/Sunny 已用）
- Den Den Mushi（電伝虫）= 翻譯訊號 + 跨世界傳遞，雙重 metaphor 對應 extension 的兩個功能：翻譯 + 抓取
- "Baby Den Den Mushi" 對應 portable/extension 形態，但 repo 用 full name `den-den-mushi` 比較專業
- 修修拍板 2026-05-10

## How to apply
- 新建 `E:\den-den-mushi` repo（standalone, not under nakama monorepo）
- Chrome ext manifest.json `name: "Den Den Mushi"`
- 對話、PR title、commit 用 `Mushi` 簡稱即可（避免冗長）
- 未來若有 backend endpoint 整合 Nakama，命名為 `mushi_*` (e.g. `/api/mushi/import`)
