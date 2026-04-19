---
name: MemPalace 整合評估
description: 放棄觀望，自建 SQLite agent_memory。MemPalace 不做 auto-extraction + CJK 只改一半 + API churn。最後查核日 2026-04-19
type: project
tags: [mempalace, integration, abandoned, self-build]
created: 2026-04-11
updated: 2026-04-19
confidence: high
ttl: permanent
originSessionId: 6dc774b2-8ee7-4655-b691-eebe19832245
---
**結論：放棄等 MemPalace，改自建 SQLite `agent_memory`。**

正確 repo：`MemPalace/mempalace`（不是 `milla-jovovich/mempalace`）。官方警告有惡意山寨站 `mempalace.tech`。

## 為什麼不等（2026-04-19 查核）

1. **CJK 只改一半**：v3.3.1 可掛 `bge-m3` 做 embedding（#699 closed），但 `searcher.py` BM25 tokenizer 還是 hardcoded `\w{2,}` 英文正則，`i18n/zh-TW.json` 是 dead code。issue #973（搜尋忽略 i18n）、#516（中文語意分數為負）都還 open
2. **Open issues 爆增**：161 → 410，穩定性在退化
3. **API churn**：8 天內 v3.3.0 + v3.3.1，v3.3.0 加了 29 個 MCP tools，下游 agent 會被迫頻繁跟改
4. **明確不做 auto-extraction**：README 寫「stores verbatim text, does not summarize, extract, or paraphrase」—— 我們要的自動抽取還是得自建一層
5. **無官方 Docker**：只能 `pip install`，沒 Dockerfile / Helm chart
6. **Benchmark retraction**：2026-04-14 撤回「+34% palace boost」誤導性比較；後爆紅清理期，還在自我修正

## 多 agent 支援（唯一亮點）

"each specialist agent gets its own wing" + `mempalace_list_agents` MCP tool，v3.3.0 加了 file-level locking 避免並發建重複 drawer。符合 nami/zoro/robin 隔離需求。

**但我們自建 SQLite 做 `agent TEXT` 欄位 + index 就能達到相同隔離，無必要為這點切換依賴。**

## 後續決策

- **Franky 不再追蹤**：停止每週監控（可刪 `config/franky-watch-mempalace.yaml`）
- **改自建**：走 `project_agent_memory_design.md` 的四階段計畫
- **未來可回頭評估**：若自建版本跑一年後發現瓶頸，且 MemPalace 到那時穩定（open issues <250 + CJK BM25 修完 + 有 Docker），再考慮遷移
