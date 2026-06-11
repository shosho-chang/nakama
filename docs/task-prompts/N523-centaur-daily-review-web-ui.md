# N523 — Centaur 每日回顧 Web UI + 開卡 endpoint

> **上游文件（先讀）**：`docs/plans/centaur-zettelkasten/centaur-kb-prototype-v2.html`（**UI 規格附件**——每日回顧 view + 開卡 drawer 的視覺與流程以此為準）、`Centaur-Zettelkasten-規格-v0.2.md` §5 §6、`docs/design-system.md`（必讀，美學是 first-class requirement）。
> **依賴**：N520（bookkeeping API）、N522（資料 schema）。**worktree**：`E:\nakama-N523-daily-review-ui`，branch `feat/n523-daily-review-ui`。

## 1. 目標

Thousand Sunny 上的每日回顧頁與人工寫卡入口——pilot 的核心人機介面。

## 2. 範圍

- `thousand_sunny/routers/kb_review.py`：
  - `GET /kb/review` — 每日回顧頁（讀 N522 輸出）
  - `POST /kb/api/permanent` — **human-authoring endpoint**，全系統唯一 Permanent body 寫入口；寫入帶 `author: human`；組裝 v0.2 §3 結構（frontmatter + 正文 + typed edges inline）
  - 動作回寫：skip / later（更新 N522 的狀態檔）
- templates + static：照 prototype v2 — 三段（fleeting / 候選 / 清掃）、開卡 drawer（卡名可改、source_refs 預填、Robin 建議 chips + 全量搜尋兩層選擇器、檔案預覽、**空正文阻擋**）、用語「每日回顧」
- 存檔後 Phase 5 hook：Literature `mined_concepts` + `status: mined` 回填、fleeting `status: processed` + 回收桶、`KB/index.md` 更新、`KB/log.md` append（鏡像規則照 v0.2 §8 — 只沿人寫連結傳播）
- design system 合規：`--sho-*` token、LINE Seed TW、`_shosho_asset_version()` pattern、theme.js、AI slop 禁用清單逐項檢查

## 3. 輸入

prototype v2、v0.2 §5 §6 §8、design-system.md、N522 schema、N520 `update_permanent_bookkeeping()`。

## 4. 輸出

可用的 `/kb/review` 頁 + endpoint + 測試（含「endpoint 寫入帶 `author: human`」與「空正文 422」斷言）。

## 5. 驗收

端到端手測：開頁 → 開卡 → 填正文與關係 → 存檔 → vault 出現正確永久卡 → Literature `mined_concepts` 回填 → log 有紀錄 → fleeting 原檔進回收桶。aesthetics：對照 design-system AI slop 清單零違規；states（loading/empty/error/focus）皆有設計。

## 6. 邊界

只做每日回顧 view（Permanent browser / Literature / MOC / Wiki / 系統 = N525）；不做 Obsidian plugin；不做 `obsidian://` 路徑（已被 Web UI 取代）。
