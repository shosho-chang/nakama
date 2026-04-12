---
name: Robin KB search endpoint 未測試
description: Robin 的 POST /kb/research endpoint（kb_search.py + web.py）在 2026-04-10 新增後尚未測試
type: project
tags: [robin, testing, kb-search]
created: 2026-04-11
updated: 2026-04-11
confidence: high
ttl: 90d
---
`agents/robin/kb_search.py` 和 `agents/robin/web.py` 的 `/kb/research` endpoint 在 2026-04-10 commit c49b630 新增後，修修確認尚未跑過任何測試。

2026-04-12 code review 發現並修復了 `get_client("robin")` TypeError bug（commit cd744b9），該 bug 會導致整個 endpoint 無法使用。Bug 已修但 endpoint **仍未實際測試**。

**Why:** 當天開發完就結束了，沒有補測試。
**How to apply:** 下次開工時提醒需要先測試或補 unit test，再繼續新功能開發。
