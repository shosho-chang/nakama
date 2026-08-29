# Finished Cut Amendments（過渡措施）

> **這個目錄是過渡的。** ADR-066 的 follow-up 把 amendment 升格成 public command
> （`request_amendment`）之後，`operations/` 應該刪除，`journal/` 轉為由 aggregate 產生。

## 為什麼存在

Amendment 是套用在**已封存 Release** 上的機械式、非語意變換：它沿用 base Release 的整條
`AcceptedStage` chain，只替換 `MaterializationPlan`。它不能鑄造 acceptance，也不會派工
semantic worker。

ADR-066 目前沒有 `request_amendment` 命令。L04（`20260805 林之晨` / `long3-fresh-20260828-r4`）
的兩次修訂因此是用 episode-local operation 完成的，而那兩支腳本原本住在 gitignored 的
`.cache/` 底下——**當時 current Release 的重建能力完全不在版控裡**。本目錄消除那個單點風險。

## 內容

| 路徑 | 是什麼 |
|---|---|
| `journal/<episode>.json` | 型別化的修訂鏈紀錄，schema `nakama.finished_cut_amendment_journal.v1` |
| `operations/*.py` | 實際執行過該次修訂的腳本，被 journal 以 SHA-256 釘住 |
| `_journal.py` | journal 的型別化讀取器與不變式驗證 |

## L04 修訂鏈

```
release-8ca1a6eb  plan-1410680187…             20 components  ← 原始 run authority 產出
      ↓ amendment 1  suppress_components（5 個 supporting_title → intentional A-roll）
release-22a0424   plan-suppression-e8080c9b…   15 components
      ↓ amendment 2  replace_component_assets（5 張 fullscreen_transition 換 v4 asset）
release-af65a1d7  plan-transition-v4-833e4ac1…  15 components  ← CURRENT
```

三份 Release 共用同一組 `run_id` / `command_id` / director / dp / visual `acceptance_id`——
這是「機械式修訂」的定義性不變式，`_journal.py` 與測試都會驗它。

## journal 是怎麼產生的

**operation 參數是從兩端 sealed Release 實際 diff 推導出來的，不是從腳本常數抄的。**
`suppress_components` 取 `semantic_kind` 變成 `intentional_aroll` 的事件；
`replace_component_assets` 取 `asset_ref` 改變的 component。因此 journal 描述的是
Release 真正發生的事，不是腳本宣稱它做了什麼。

## 不要做

- 不要把 `operations/` 當成新修訂的範本。它們伸手進 package 私有層、且寫死 L04 常數；
  新的機械修訂應該等 `request_amendment` 落地。
- 不要手改 `journal/*.json`。它描述已封存的不可變 Release；改它等於偽造 provenance。
- 不要重跑 `operations/*.py` 的 `publish`。current 已經是它們的產物，重跑會再疊一層。
