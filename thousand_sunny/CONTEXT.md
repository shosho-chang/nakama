# Thousand Sunny

Web presentation 平台 / chassis：所有對外 web UI、Bridge dashboard、各 agent router、HMAC cookie + API key auth。**Sunny 是船本身（platform），不是 agent crew member**（ADR-029 §2 凍結）。Bridge 是 Sunny 上的 ops 控制台 surface 子集。

## Language

**vault-as-substrate**:
Nakama 的讀資料策略 — Obsidian vault 的 `.md` 檔是 knowledge 層唯一 source of truth；不另建 structured DB 副本來鏡像 vault 內容。Bridge surface 透過 (a) FS-direct read 跟 (b) LLM-over-vault 兩條路徑 consume。
_Avoid_: vault-mirror DB, vault index table, vault sync to SQL

**LLM-over-vault**:
ad-hoc query 機制 — 把 vault 子集（path glob / date range / type filter 預先 scope）concat 進 LLM prompt、自然語言提問、LLM 回結構化結果。**不建 FTS / embedding 替代**；今日 corpus 限 < 200KB-1MB 範圍，超過時走 retrieval pre-filter + LLM（evolution 不是 abandonment）。
_Avoid_: vault search index, vault FTS, vault vector store

**Knowledge layer** (vault) vs **State layer** (state.db):
兩個 substrate **並列**，不是「vault 最底層、SQLite 是另一個東西」。
- **Knowledge layer = vault**：content artifact（KB / Projects / Daily / Digests），人類也讀、markdown-shaped、低頻 mutation
- **State layer = `state.db` (SQLite)**：operational state（cost、drafts、user_memories、audit_results），機器讀寫密集、structured-shaped、高頻 mutation

新功能設計前先決定資料落點。**不要把高頻 mutation 塞進 vault**；**不要把人類可讀的長文 artifact 塞進 SQLite**。

**FS-direct read**:
透過 `shared.blob_loader.VaultBlobLoader` 直讀 vault `.md` 檔案的 deterministic read 路徑；list/detail/date-nav 之類「我知道我要哪一檔」的 access pattern。
_Avoid_: vault scrape, vault crawl

**Obsidian CLI** (`obsidian` command):
Obsidian 官方 first-party CLI；`obsidian eval` 可呼叫所有 plugin runtime API（Dataview / Tasks / Templater）。**硬約束**：Obsidian app 須在跑（Headless Sync 例外）— **VPS 不可用**，桌機可用。是 Tier B (LifeOS Dashboard mirror) / Tier C (project workspace) 的 future lever，不是 Tier A scope。詳見 [reference_obsidian_cli](../memory/claude/reference_obsidian_cli.md)。

**Digest**:
Robin / Franky 每日產出的 vault markdown 檔，per day per type。Tier A 第一刀 scope = `KB/Wiki/Digests/PubMed/{YYYY-MM-DD}.md`（Robin）+ `KB/Wiki/Digests/AI/{YYYY-MM-DD}.md`（Franky）兩 type；未來可加 podcast / weekly 等 type。
_Avoid_: Daily Brief（Nami `AgentBriefs/`，是不同 pipeline）, Book digest（不同形狀，跟 Reader 整合）

**Digest viewer**:
Tier A 第一刀的 Bridge surface — `/bridge/digests` unified landing（today snapshot + 7d timeline）+ `/bridge/digests/{type}/{date}` detail + `/bridge/digests/ask` 自然語言跨日 query。走 FS-direct read + LLM-over-vault。

**Bridge surface**:
Thousand Sunny 上認證後的 ops console 頁面（`/bridge/*` route 子集）。**單一使用者**（HMAC cookie + WEB_PASSWORD），mutation pattern 鎖定 form POST + 303 + native `<dialog>`，零 JS framework。
_Avoid_: dashboard（太籠統）, admin page

**Progress design**:
`/progress` partner-facing static surface 的視覺風格（mono-led meta + section kicker + 單一橘色 accent ≤ 4% + lightning bolt 99/1 stamp + max-width 1180px scrollable doc）。靈感：antfu.me / karpathy.ai / rauno.me。**Bridge surface 跟 Progress 共用 `/static/shosho/tokens.css` design tokens 跟 `sho-*` utility class**，視覺一致；密度差異是 ops console vs scrollable doc 的合理區隔。

## LLM-over-vault 限制（4 條 — 設計時務必知道）

1. **「Obsidian CLI 強大功能」是桌機限定** — `obsidian eval` 等 plugin runtime call 需 Obsidian app 在跑，VPS 沒有。LifeOS Dashboard 想用 dataviewjs 結果，VPS 端要嘛 Python 重寫聚合、要嘛接受 desktop-resident agent dispatch topology。
2. **Vault 不是唯一 substrate** — state.db 是 first-class 同輩。高頻 mutation / 結構化 query 走 SQL，不要硬塞 markdown。
3. **「Feed it everything」今天還沒到** — 200k context 塞 ~150KB 中文，1M context 塞更多但 $/query 高 5-10×。手動 path/date scope **是 feature 不是 bug**；corpus 增長時 evolution path = retrieval pre-filter + LLM。
4. **Read path 漂亮，Write path 難很多** — read = glob + concat + LLM 乾淨；write 撞 (a) concurrency（user 同時在 Obsidian 編輯）、(b) idempotency（patch 不重複）、(c) Issue #231（Bridge 禁寫 vault）。Tier C 要逐題拆。

## Relationships

- **Digest viewer** 走 **FS-direct read**（list / detail / 7d timeline）+ **LLM-over-vault**（`/digests/ask`）兩條路徑
- **Bridge surface** mutation 寫 **State layer**（`state.db`），**不寫** Knowledge layer（vault）— Issue #231 約束
- **Knowledge layer** 寫入由 agent 端負責（Robin / Franky / Brook via Usopp），**不**由 Bridge UI 直寫
- **Obsidian CLI** 是 Tier B/C 的 future lever；桌機 Obsidian 開著時可由 desktop-resident agent 呼叫；**不在** Tier A scope

## Flagged ambiguities

- 「**dashboard**」三義：(a) `/bridge` Fleet dashboard（Sunny 內現有 landing）；(b) Obsidian `Dashboards/?? Dashboard.md`（dataviewjs 聚合，Tier B 來源）；(c) Tier B 落地後的 `/bridge/lifeos`（鏡像 b 到 Sunny）— 提到時加 prefix（fleet dashboard / Obsidian dashboard / LifeOS Bridge dashboard）
- 「**digest**」三義：(a) Robin PubMed digest（Tier A scope）；(b) Franky AI news digest（Tier A scope）；(c) Robin Book digest（不同形狀，**not in Tier A**）— 提到時加 type prefix（PubMed digest / AI digest / Book digest）
- 「**project**」三義：(a) LifeOS content-creation project（`Projects/<title>.md`，Tier C 主題）；(b) ADR-001 agent context（Robin/Brook…）；(c) GitHub Projects — 提到時加 prefix（LifeOS project / agent context / GH project）

## Example dialogue

> **Dev**：「Tier C 要做 task 建立，應該寫 vault 還是 state.db？」
> **Domain expert**：「task 是高頻 mutation 又有人類可讀需求 — 兩面都有。第一原則看 Knowledge layer vs State layer：number-of-pomodoros-today 這種 aggregate 走 state.db；task 本身的長描述 + wikilink 留 vault。但 Bridge 不直寫 vault — 走 `obsidian create` CLI（桌機 agent 落地後）或 agent→Usopp draft pattern。」

> **Dev**：「使用者問『過去一個月有沒有 Nature × 運動的研究』要怎麼答？」
> **Domain expert**：「LLM-over-vault。`/bridge/digests/ask` 表單收 query，後端 glob `KB/Wiki/Digests/PubMed/2026-04-*.md` 全 concat 進 prompt，Sonnet 回答 + 引用日期。不建 FTS index — 哲學上拒絕；技術上規模也不需要。」
