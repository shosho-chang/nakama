# 關鍵字評分 — Zoro / WebSearch 兩條 branch

`title-brainstorm` Step 2 的 disclosed reference。每個候選關鍵字要三個分：
**熱門度**（搜尋需求）、**競爭度**（多飽和／多難排）、**機會分 opportunity**
（＝熱門度高 × 競爭度低，且本篇 cite 得到 hook 兌現）。挑 opportunity 最高的 5–8 個進標題。

## Branch A — Zoro（在 nakama repo 環境，`agents/zoro` import 得到）

分數直接來自 Zoro，不用自己估：

```bash
# repo 根目錄跑（零 LLM 成本；評分由 subagent 做）
python -c "from agents.zoro.keyword_research import collect_keyword_signals; import json; \
print(json.dumps(collect_keyword_signals('<主題>', en_topic='<english topic>'), ensure_ascii=False))"
```

回傳 `zh` / `en` 兩輪 raw signals（youtube / trends / autocomplete / twitter / reddit），
以及 `sources_used` / `sources_failed`。`en_topic` 省略時 `en_skipped: true`、`en` 為 `null`。
評分（`search_volume` / `competition` / `opportunity`）由呼叫端 subagent 在 signals 上做判斷，
不由此函式呼叫 LLM。相依 httpx / trendspy；`YOUTUBE_API_KEY` 選配。

## Branch B — WebSearch（獨立環境／沒有 repo）

沒有 volume 數字，用可觀察訊號打分，**每個分附你用的訊號當理由**：

| 分 | 熱門度 訊號 | 競爭度 訊號 |
|---|---|---|
| 高 | 自己就是搜尋主題／有 Wikipedia／多家媒體都寫／出現在相關搜尋 | 首頁被強站（大媒體、專業站）佔滿，且多篇同角度同框 |
| 中 | 長尾但常見用語／幾篇內容 | 幾個對手但切入分散 |
| 低 | 幾乎搜不到／沒人寫 | 首頁多論壇／UGC／沒人這樣下過 |

**機會分 = 熱門度高 × 競爭度低，且這個字本篇 cite 得到 hook。** 兌現不了的字，機會分一律壓低。
沒人下過的角度（低競爭缺口）優先卡位。
