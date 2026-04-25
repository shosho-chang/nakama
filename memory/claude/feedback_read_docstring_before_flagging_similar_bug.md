---
name: 標「類似 bug」前要讀模組 docstring
description: 不同模組常對「malformed input」採刻意相反的 lenient/strict 政策，憑表面相似性掛 follow-up 會誤判
type: feedback
originSessionId: 30872c1e-80cd-4a8a-ac1b-11865e5cf671
---
PR #133 ultrareview 修了三個地方對 malformed GSC row 的處理：

- `shared/seo_enrich/cannibalization.py` — `不會 raise validation error`（docstring §190），缺 key 直接 skip
- `_select_primary_metric` in `enrich.py` — broken guard 修成 explicit skip-on-malformed
- `shared/seo_enrich/striking_distance.py` — `keys[1]` missing **應該** crash（docstring §35-36 + `Raises:` 段明寫 `IndexError` / `KeyError` / `ValidationError` 是 caller 違約信號）

我先把 striking_distance 列為「相同類別 follow-up」、寫進 P7 remaining work 也寫進 pending_tasks，下一輪才從 docstring 看出那是 ADR-009 T6 契約刻意行為，不是 gap。撤回 claim 多一輪心智成本。

**Why:** 同 repo 內不同模組對「上游 malformed row」常採刻意相反的政策 —— SEO 偵測類函式偏 lenient（單一 keyword skip 不該毀整個 enrich pipeline），契約解碼類函式偏 strict（caller 違反 dimension 契約就該 crash 讓上游發現）。表面 pattern 相似（都讀 `row["keys"][0]`），但對 malformed input 的合約立場相反。

**How to apply:** 修完一個 bug 後想標其他地方有「同類 bug」前，**先讀那個檔的 module docstring + `Raises:` 段 + 既有 test**。如果 docstring 把 crash 列為 documented behaviour，就不是 bug、不掛 follow-up。在 review 留 note 時也要把這層判斷寫清楚（「這檔對 malformed 採嚴格契約，跟 X 檔的 lenient 不同」），避免下一個人重做同樣的判斷工夫。
