---
name: Render 改用上游 note 時要 audit 上游是否誠實
description: 把硬寫錯誤訊息換成 state.note 之類的上游欄位時，要掃過每個產生該欄位的分支確認沒說謊
type: feedback
originSessionId: 14a01230-820b-4190-aa58-91ef14b9c600
---
把 render 層的 hardcoded error string 換成直接顯示上游 state 的 `note` / `detail` / `message` 欄位時，要先掃過每個產生該欄位的分支，確認上游訊息真的反映實際狀態。

**Why**：PR #84 修 Robin PubMed digest render，把 `"無法取得（無 DOI / PMCID）"` 這個 hardcoded 訊息改用 `ft.get("note")` 顯示真實原因。但上游 `fetch_fulltext()` 的 `not_found` 分支對「PMCID 存在 + 兩條 PMC 下載都失敗 + 無 DOI」這個 case 同樣回 `note="PubMed 無 DOI / PMCID"` — 本來 render 硬寫同一句話，兩個謊互相掩蓋；render 改誠實化後，上游的謊反而被放大。code-review 沒抓到，是 review 流程發現的。

**How to apply**：當改動是「把 render 層 hardcoded error 換成 display upstream field」時，把這個當 two-step 工作：

1. render 層改動
2. 立刻 `grep` 或追所有 return / assignment 該欄位的地方，逐一看訊息是否對得上實際狀態

尤其注意「fallthrough 分支」（trailing `return` / `else`）— 這種分支的訊息通常是寫給「最常見失敗 case」的，其他路徑 fall 進來時會說謊。

不適用於純新增欄位、或 render 只是被動消費既存欄位的情境。
