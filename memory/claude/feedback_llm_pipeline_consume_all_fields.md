---
name: LLM pipeline 必須 consume 所有它教 LLM 產出的 schema 欄位
description: prompt 教 LLM 輸出 X 欄位（如 pick: true|false）就必須在 pipeline 內 consume；不然 schema drift 默默 leak 到 output
type: feedback
created: 2026-04-26
originSessionId: franky-news-slice-a-review
---

LLM 的 prompt 寫了「請輸出 `pick: true|false`」+ 規則「overall ≥ 3.5 才設 true」，但 pipeline render 時從沒讀 `pick` 欄位 → 所有 score 結果都被印出，包括 `pick: false` + `overall: 1.2`。Schema drift 沒人擋。

PR #171 (Franky news Slice A) reviewer 抓到。修法：render 前加一行 filter：
```python
scored = [s for s in scored if s["score_result"].get("pick", True) is not False]
```
（用 `True` default + `is not False` strict check — LLM 漏欄位不整批 drop，但明確 false 會擋）

**Why:** 兩階段 LLM pipeline（curate → score）每階段教 LLM 不同 schema，schema 對齊靠 prompt 教 + pipeline 讀。少一邊就 silent failure — 不會 crash，只是品質悄悄掉。code review 不主動驗，要靠人記。

**How to apply:**
- 任何 LLM pipeline 寫 prompt 教 schema 欄位 X，**寫程式時對應加 consumer 邏輯**（filter / branch / display）
- 沒實際 consume 的欄位**從 prompt 拿掉**，不要留空殼讓 LLM 浪費 token + 給你假信號
- code review 時主動掃：grep prompt schema 欄位 → grep code 是否有 read → 缺的就是 schema drift bug
- 兩階段 pipeline 特別容易踩（curate 有意義的欄位 vs score 有意義的欄位混淆）— PubMed digest 有同樣風險（用 `editor_pick` split editor_picks vs others，兩邊都顯示但分區）— 可以是 reference 設計
