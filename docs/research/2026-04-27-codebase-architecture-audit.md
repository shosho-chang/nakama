# 2026-04-27 — Codebase Architecture Audit

> 第一次跑 `/improve-codebase-architecture`（mattpocock skill）對 nakama repo 做的 deepening 候選掃描。記錄 12 個候選、deletion test、ROI 排序，以及哪些已動 / 哪些 pending — 下一輪做 audit 時可以對照看 trajectory。

## Skill source

- `mattpocock/skills` repo → `improve-codebase-architecture/`
- 本地 install 到 `.claude/skills/improve-codebase-architecture/`（路徑改 docs/adr → docs/decisions、ADR 命名 ADR-NNN-slug.md）+ `.claude/skills/domain-model/`

## 整體 trajectory

**改善中**。`docs/principles/{schemas,reliability,observability}.md` 三大原則已落地，ADR-006（approval queue）/ ADR-009（SEO 架構）是好的深 module 範例。剩餘 friction 集中在：

- **Shallow wrapper 病** — client 層 + doc_index
- **Forward-declared deep orchestration** — Usopp publisher 600 行尚未拆

---

## 12 候選 deepening 機會

### ① LLM client facade — shallow + 測試 mock 走錯 seam **[DONE — PR #208]**
- `shared/llm.py` (78L) + `shared/llm_router.py` (86L) + `shared/anthropic_client.py` + `shared/gemini_client.py`
- `ask()` 純 dispatcher 沒行為；測試 mock `ask_claude_multi` 而非 `llm.py` seam，refactor 一動全紅
- Deletion test：刪了 → caller 直接 import provider，complexity 沒消失反而暴露 depth
- Shape：SHALLOW + TESTABILITY-ABSTRACTION｜In-process

### ② Approval queue FSM 跟 payload schema 沒對齊（audit framing 誤判）**[DONE — PR #217]**
- `shared/approval_queue.py:46-76` + `shared/schemas/approval.py:106-145`
- ~~audit 原描述「FSM `ALL_STATUSES` 跟 payload `action_type` 兩個 namespace 沒鎖；Chopper Phase 2 加 `reply_comment` 必須兩邊手動同步、漏寫 silent fail」~~ **誤判（兩件事）**：
  1. **「沒鎖」不成立** — `ALL_STATUSES` 是 FSM 集合（status / 8 個值），跟 payload `action_type`（discriminator / 2 個值）是兩個語意完全不同的維度，根本不該鎖在一起；FSM 跟 DB CHECK 已經有 import-time assert 鎖了，action_type 由 Pydantic discriminated union 強制 Literal
  2. **「漏寫 silent fail」不存在** — 加新 action_type 時：Pydantic union 不認得會 `ValidationError`、`enqueue()` 內 4 helpers 走 `else: raise ValueError`、usopp dispatcher `isinstance else mark_failed`，每個 touch point 都是 explicit fail-fast 不 silent
- **真正的 deepening 機會**：4 個 helper（`_target_platform / _target_site / _title_of / _diff_target_id`）是 isinstance ladder anti-pattern — 加新 payload type 要動 4 處 helper、邏輯散在 queue 層而非歸 payload class 自身
- PR #217 完成 push-down：把 4 個 derived 屬性（`target_platform / title / diff_target_id`，`target_site` 已是 field）做成 PublishWpPostV1 / UpdateWpPostV1 的 `@property`，刪 4 個 helper（含全部 isinstance ladder）；新加 ReplyCommentV1 只需在 class 內 implement 3 個 property，不必動 `approval_queue.py`
- Net 影響：approval_queue.py −24 LOC / schemas/approval.py +29 LOC（+1 net），cohesion shift 才是重點而非行數
- Shape：MISPLACED RESPONSIBILITY（derived attribute 的歸屬，不是 TIGHT COUPLING）
- 教訓：跟 ④ 一樣 audit shape 描述不準 — 「兩個 namespace 沒鎖」是 false framing（兩個 namespace 不是同一個東西）；real shape 是 OOP polymorphism 漏寫
- 不重開 ADR-006：本 PR 不改 FSM、不改 DB schema、不改 DoD；只是 derived attribute 歸位
- ~~_可考慮重開 ADR-006_~~ — 不必

### ③ KB writer 三層嵌套 — 4-action dispatcher 內部混 I/O + LLM + I/O（Ousterhout 術語誤用）**[NO-OP + docstring fix — verified 2026-04-28]**
- `shared/kb_writer.py:591-786`（197 行，非 250）+ `agents/robin/ingest.py:484-526`（caller `_execute_concept_action`）
- ~~audit「SHALLOW interface 隱藏 deep impl」當 bug 描述~~ **術語誤用**：Ousterhout 〈Philosophy of Software Design〉「**深模組（deep module）正是 small interface + large impl**」是該書推崇的設計範式，audit 把這形狀當 bug 倒過來
  - ADR-011 §3.5 line 307 明寫「按 action 分派」— dispatcher 是 ADR 凍結設計
  - Caller `_execute_concept_action` 一次 call 把 whole bag 傳進去（line 514-523），不必知道哪個 action 要哪個 arg；deep module 介面正確
  - 拆 4 個 public function 等於把 dispatcher 推上 caller — caller 變成 isinstance ladder，**zero-net cohesion shift**
- **真正的 friction**：`update_merge` 內部 `_ask_llm()` 走 Claude Opus 4.7 max_tokens=16000，但 docstring 沒標 cost；caller 看不出此 action 是 LLM 而其他 3 action 是純 I/O — leaky abstraction（這是 deep module 的常見 cost transparency 議題，**不是要重構成 shallow**）
- 修法：docstring action 列表加 cost note（update_merge ~$0.10–$0.50 per call），其他 3 action 純 file I/O；caller 自行 batch-budget
- 不拆 dispatcher 理由：(a) ADR-011 §3.5 凍結 (b) deep module 是 stated design pattern (c) caller 端 ladder 等價
- 教訓：audit 用 Ousterhout 術語但反向使用 — small interface + large impl 是好事不是壞事；應分清 (i) deep module（小介面、大實作、cost 透明）vs (ii) leaky abstraction（小介面、大實作、cost 不透明）；後者修 docstring 不修架構
- Shape verdict：**deep module + leaky abstraction（docstring 修，不重構）**

### ④ 內容檢查邏輯散在三處 — 沒統一 sanitizer（audit framing 誤判）**[DONE — PR #214]**
- ~~audit 原描述「醫療詞彙 / block-level tag / slug regex 三套散在三處要統一」~~ **誤判**：三模組是三個不同關注點：
  - `gutenberg_validator.py` = HTML/AST 結構驗證（wp: comment pairing、`<p>` cleanliness）
  - `kb_writer.py:73-86` = LLM-emitted slug path traversal 防護（`Path` 不 collapse `..`）
  - `compliance_scan.py` + `medical_claim_vocab.py` = 醫療詞彙黑名單
- **真正的重複**只在第三組：Slice B 已產出完整 vocab（`medical_claim_vocab.py`，~50 patterns + TC↔SC mirror，Usopp publisher 在用），但 `compliance_scan.py` Phase 1 seed（6 patterns，自己 docstring 標 deprecated）沒被收掉，Brook compose + seo-audit-post skill 還在 import seed → compose-time gate 比 publish-time gate **更鬆**（同 content 兩 gate 結果不一致）
- PR #214 完成 deprecation：抽 `disclaimer.py`（compose-time positive signal，跟 medical vocab polarity 相反 → 該分開）+ `__init__.py` 加 `scan_draft_compliance(text)` orchestrator + 3 callers migrate + 刪 seed module + test 搬到 `tests/shared/test_compliance.py`；net **−57 LOC**
- 教訓：跟 ⑨ 一樣 audit shape 描述對錯模組 — 寫「散在三處沒統一」當下沒 verify 三模組真的在做同一件事；真實狀況是兩個分散場合的「真相之間」（gutenberg 結構 vs slug 安全 vs 醫療 vocab）拉一個 false unifier 才是 false sharing 反方向
- Shape：DUPLICATED（兩 compliance scanner，**不是**三 sanitizer）

### ⑤ anthropic_client 跟 gemini_client 樣板重複 **[DONE — PR #208，併入 ①]**
- `shared/anthropic_client.py` + `shared/gemini_client.py`
- singleton factory / set_current_agent / usage 計費 / retry 兩邊 200 行幾乎一樣；gemini 還 re-export anthropic 的 set_current_agent
- Shape：DUPLICATED

### ⑥ `get_context()` shallow interface 蓋 deep impl（audit framing 誤判）**[NO-OP — verified 2026-04-28]**
- `shared/memory.py:145-165`
- ~~audit 原描述「一函式三 input 內部讀 Tier 1/2/3 + truncate；bug 藏在 truncation」~~ **誤判**：實際 `get_context()` 只讀 Tier 2（`shared.md` + `agents/{agent}.md`），15 行純 concat；不讀 Tier 1（CLAUDE.md 是 Claude Code 自動載入），不讀 Tier 3（`search_memory` 是 `shared.state` re-export，不在 `get_context` 路徑），不 truncate（`max_tokens=500` 跟 `task` 兩個參數都標 `預留給未來壓縮，目前未使用`）
- **真正的 shape**：ADR-002 line 89-97 設計 `task` 篩 episodic、`max_tokens` 自動壓縮；Phase 1 只實作 step 1+2 的 shared+agent concat — 兩個參數是 ADR 預留 scaffolding，不是 dead code 也不是 deep impl 的 shallow facade
- 不動 code 理由：兩個 unused 參數是 ADR-documented intentional preservation（升級到 episodic / 壓縮時介面不變），不能就 YAGNI 砍掉與 ADR 衝突；caller `agents/base.py:39` + `agents/robin/ingest.py:51` 已經傳 `task` 進去，將來實作時 caller 不必動
- 教訓：audit 看到 doctring 提「Tier 1/2/3」就反射說「一函式蓋三 input」，沒實際 trace 函式 body 真讀什麼
- Shape verdict：**incomplete vs ADR**（不是 SHALLOW interface 隱藏 deep impl）

### ⑦ Prompt loader 隱式注入 shared partial — 呼叫端看不到依賴（部分 framing；ROI 偏低）**[DEFER — evaluated 2026-04-28]**
- `shared/prompt_loader.py` + `prompts/shared/{domain,writing-style,vault-conventions}.md`
- audit 原描述「改 `domain.md` 會無聲改 Robin / Brook / Nami 行為，agent code 看不出有這依賴」**部分對**：
  - 16/40 prompt 用 partial token、13 caller 跨 9 agent/handler，blast radius 確實存在
  - **但** `template.format_map(variables)` 對 unused token no-op，所以「implicit」只發生在實際用到 `{writing_style}` `{domain}` `{vault_conventions}` 的 prompt 上 — token 本身就是 in-template declaration（讀 `prompts/robin/summarize.md` 看到 `{writing_style}` 即知道有此依賴）
  - agent code（Python）看不到依賴是真的，但 prompt 是 first-class 設計 artifact、template 自己 declare 算 OK
- 不動 code 理由：cost > value — 16 prompt 加 frontmatter `partials:` declaration 或改成 explicit kwargs 都要動 13 caller，換來「在 Python 端也看得到 partial 依賴」邊際 clarity 偏低；token-as-declaration 已是事實上的 self-document
- 觸發再評估條件：(a) 有第三方介接 prompt build pipeline（要 introspect 依賴）；(b) 增加 4+ 新 partial 變得難 grep
- Shape verdict：**TIGHT COUPLING (implicit)**（framing 對但 ROI 偏低，DEFER）

### ⑧ Usopp publisher 600 行 monolith — 隱含 retry orchestration
- `agents/usopp/publisher.py` + `shared/wordpress_client.py` + `shared/seopress_writer.py`
- claim → validate → publish → seopress fallback → record + retry，全擠在 publisher.py；Chopper 之後要做留言 publishing 拿不出來
- Shape：DEEP 但 reusable 部分該抽出
- ADR-001+006 預設此 coupling，動之前要評估

### ⑨ `shared/doc_index.py` Windows path bug（audit 描述誤判）**[DONE — PR #211]**
- `shared/doc_index.py` (272L)
- ~~audit 原描述「純 pass-through `state.db`，每個 function 1:1 query」~~ **誤判**：實際是 FTS5 over markdown 的 search index，含真實商業邏輯（file walk + frontmatter/H1 title 抽取 + category bucketing + snippet HTML escape with sentinel swap，防 XSS）。Deletion test 不過，刪了複雜度不會消失。
- 真正修的：`_walk_markdown` 用 `str(p.relative_to(...))` 在 Windows 產生 `\\` 分隔，`_category_for` split on `/` 全歸 `'other'`，所以 `stats()` 在 Windows dev 顯示 `{'other': 5}`、`/bridge/docs` per-category 計數壞掉（VPS Linux 沒事）。改 `as_posix()`。
- 連動：`tests/shared/test_doc_index.py::test_stats_returns_per_category_counts` 先前在 Windows 紅，PR #211 修綠
- 教訓：下次跑 audit skill 要把候選 deletion test 真的對 file 跑一次，不要只看模組名稱猜 shape

### ⑩ `shared/anomaly.py` 抽 56 行純數學 — false sharing（audit framing 誤判）**[NO-OP — verified 2026-04-28]**
- `shared/anomaly.py` (76L, 兩函式 + 一 frozen dataclass) + `agents/franky/anomaly_daemon.py` (唯一 consumer) + `tests/shared/test_anomaly.py`
- ~~audit 原描述「抽 3-sigma math 但 metric 選擇 / SQL agg 還在 franky 內，沒人 reuse」~~ **誤判**：模組 docstring (`shared/anomaly.py:1-9`) 明寫「Split…so the baseline / 3σ math can be unit-tested with plain `list[float]` inputs — no DB, no alert plumbing, no cron context」，分離目的是 testability 不是 reuse；`tests/shared/test_anomaly.py` 直接 import `BaselineStats / is_3sigma_anomaly / rolling_baseline` 用純 list 跑邊界測（cold-start / flat-baseline / one-sided），達成宣稱目的
- 「false sharing」要求 abstraction 沒服務 stated purpose；這裡 stated purpose 是 testability、實際 達成，**不是 false sharing**；audit 把「沒第二個 consumer」直接等於「false sharing」忽略 testability 也算 valid stated purpose
- 「metric 選擇 / SQL agg 應抽到 shared」違反 YAGNI：目前無第二個 anomaly consumer，Chopper / Brook / Robin 都不做 anomaly detection，現在抽是為虛構 reuse 設計
- 教訓：audit 看到 single-consumer abstraction 就反射說「抽得不夠廣」，但 stated purpose 可能是 testability 而非 reuse — 該分清 false sharing（沒服務 purpose）vs YAGNI 過度抽象的反方向陷阱
- Shape verdict：**intentional testability split**（不是 FALSE SHARING）

### ⑪ `shared/seo_audit/` + `shared/seo_enrich/` 沒 re-export — 只有 skill 看得見 **[seo_enrich DONE — PR #212；seo_audit 已 OK]**
- `shared/seo_audit/*.py` (7 files) + `shared/seo_enrich/*.py` (3 files)
- ~~純函式 in shared/ 但沒 re-export~~ — `seo_audit/__init__.py` 早就 re-export 過（Slice D.1 就做了）；只有 `seo_enrich/__init__.py` 之前是空的
- PR #212 補 `seo_enrich/__init__.py` 三 module 公開 API（detect_cannibalization / filter_striking_distance / summarize_serp / load_cannibalization_thresholds），對齊 seo_audit 模式
- Shape：FALSE SHARING（discoverability）→ resolved

### ⑫ SEO 三 skill 跨 boundary share schema — _ADR-009 已 acknowledge_
- 不算候選：ADR-009 §Consequences §Risk #3 已記錄、有 schema_version + fixture mitigation

---

## ROI 排序（pending）

**剩 pending（真有 code change 機會）**：
- **⑧ Usopp publisher** — ADR-001+006 設計如此，等實質要做 Chopper 留言 publisher 才動

**No-op (framing 誤判 / 或 ROI 偏低，verified 不動架構)**：
- ③ kb_writer dispatcher — Ousterhout 術語誤用（deep module 是好事），ADR-011 §3.5 凍結 dispatcher 設計；只 update_merge LLM cost 不透明 → docstring 1 行修
- ⑥ memory.get_context — ADR scaffolding 半實作，不是 SHALLOW interface 蓋 deep impl
- ⑩ anomaly — testability split 達成 stated purpose，不是 false sharing
- ⑦ prompt_loader — implicit 真存在但 token 本身是 in-template declaration，refactor cost > marginal clarity gain
- ⑫ — ADR-009 已 acknowledge schema_version + fixture mitigation

**已完成（PR merged）**：
- ① + ⑤（合併處理）— PR #208 merged 2026-04-27 為 commit `e043e2a`
- ⑨ doc_index Windows path bug — PR #211 merged 2026-04-27 為 `9362cfe`
- ⑪ seo_enrich re-export — PR #212 merged 2026-04-27 為 `a095ad8`
- ④ sanitizer 收編（compliance scanner 收編）— PR #214 merged 2026-04-28 為 `97fe5b2`
- ② approval payload helpers push-down — PR #217 merged 2026-04-28（audit framing 第三次誤判紀錄）

---

## audit skill framing 誤判 ledger（6 件 / 12 候選 = 50%）

| # | audit 寫的 shape | 真實 shape | 動 code? |
|---|---|---|---|
| ② | FSM 跟 payload 兩 namespace 沒鎖、漏寫 silent fail | OOP polymorphism 漏寫（isinstance ladder push down 成 @property）| Yes — PR #217 |
| ③ | SHALLOW interface 隱藏 deep impl（誤當 bug） | Ousterhout deep module 正範式（small interface + large impl 是好事）；只 update_merge LLM cost 不透明，docstring 修 | Docs only — PR #220 |
| ④ | 三套 sanitizer（醫療詞彙 + tag + slug regex）散在三處 | 兩個 compliance scanner deprecation 沒收尾（其他兩個是不同關注點不該綁）| Yes — PR #214 |
| ⑥ | 一函式三 input 內部讀 Tier 1/2/3 + truncate | ADR scaffolding 半實作，只讀 Tier 2，task / max_tokens 是預留參數 | No |
| ⑨ | 純 pass-through `state.db`，每個 function 1:1 query | FTS5 over markdown index + Windows path bug | Yes — PR #211 |
| ⑩ | 抽 3-sigma math 但其他抽得不夠廣，沒人 reuse | testability split 已達成 stated purpose，YAGNI 違反 false sharing 反向 | No |

**meta 規律（6 次採樣，半數誤判）**：
1. **textual coupling 反射** — audit 看到名字像 share/Tier/dispatcher 就反射說「沒鎖、太 shallow、抽得不夠廣」，但 Pydantic Literal + import-time assert + 顯式 raise 通常已處理 fail-fast
2. **single consumer ≠ false sharing** — testability、edge-case isolation 是 valid stated purpose；audit 不該把「沒第二個 consumer」直接等於「false sharing」
3. **dead param ≠ shallow facade** — ADR-documented intentional preservation（如 ADR-002 `task` / `max_tokens`）長得像 dead code，要對照 ADR 看 stated future use
4. **Ousterhout 術語反向** — 「small interface + large impl」=「深模組」是該書推崇的設計範式；audit 卻把 `SHALLOW interface 隱藏 deep impl` 當 bug 描述（③）。應分清 (i) deep module（cost 透明）vs (ii) leaky abstraction（cost 不透明，docstring 修不重構）
5. **真 hit 集中在 OOP 收尾課題** — ② polymorphism / ④ deprecation / ⑨ Windows path / ⑪ re-export 都是 cohesion 錯位 / 漏 close-the-loop / OS-specific bug，不是 type system 課題

**下次跑 skill 必須**：
- 對每個候選跑 deletion test（讓編譯/測試 break）+ grep 真實 fail path（不只看 module 名）
- 對照 ADR 看 dead param 是否是 documented preservation
- 確認 abstraction 的 stated purpose（看 docstring + tests），別把 testability 當 false sharing
- 用 Ousterhout 術語前先 sanity check：deep module 是好事；分清「深模組」vs「leaky abstraction」（前者修架構、後者修 docstring）

---

## 下一輪 audit 觸發條件

- Sprint boundary 或 monthly check-in
- 加新 agent（Chopper）前
- 完成這次 backlog 任意 ≥3 項後（看新候選有沒有冒出來）
