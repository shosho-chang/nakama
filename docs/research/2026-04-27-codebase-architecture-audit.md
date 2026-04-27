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

### ② Approval queue FSM 跟 payload schema 沒對齊
- `shared/approval_queue.py:46-76` + `shared/schemas/approval.py:106-145`
- FSM 的 `ALL_STATUSES` 跟 payload `action_type` 兩個 namespace 沒鎖；Chopper Phase 2 加 `reply_comment` 必須兩邊手動同步、漏寫 silent fail
- Shape：TIGHT COUPLING（schema/FSM seam leak）
- _可考慮重開 ADR-006_

### ③ KB writer 三層嵌套 — 4-action dispatcher 內部混 I/O + LLM + I/O
- `shared/kb_writer.py:88-220, 382-500` + `agents/robin/ingest.py:477-530`
- `upsert_concept_page(action, ...)` 250 行，`update_merge` 內部讀檔 → call LLM → 寫檔；呼叫端看不出代價
- Shape：SHALLOW interface 隱藏 deep impl
- ADR-011 §3.5 可考慮收緊

### ④ 內容檢查邏輯散在三處 — 沒統一 sanitizer
- `agents/brook/compliance_scan.py` + `shared/gutenberg_validator.py:38-60` + `shared/kb_writer.py:73-86` + `shared/compliance/medical_claim_vocab.py`
- 醫療詞彙 / block-level tag / slug regex 三套獨立規則；Chopper / Sanji 後要加 IG / Slack 規則 = 第四份
- Shape：FALSE SHARING + DUPLICATED

### ⑤ anthropic_client 跟 gemini_client 樣板重複 **[DONE — PR #208，併入 ①]**
- `shared/anthropic_client.py` + `shared/gemini_client.py`
- singleton factory / set_current_agent / usage 計費 / retry 兩邊 200 行幾乎一樣；gemini 還 re-export anthropic 的 set_current_agent
- Shape：DUPLICATED

### ⑥ `get_context()` shallow interface 蓋 deep impl
- `shared/memory.py:30-100` + `shared/agent_memory.py` + `shared/state.py:110-135`
- 一函式三 input 內部讀 Tier 1/2/3 + truncate；bug 藏在 truncation，測試要同時 mock 檔案 + DB
- Shape：SHALLOW interface 隱藏 deep impl

### ⑦ Prompt loader 隱式注入 shared partial — 呼叫端看不到依賴
- `shared/prompt_loader.py` + `prompts/shared/{domain,writing-style,vault-conventions}.md`
- 改 `domain.md` 會無聲改 Robin / Brook / Nami 行為，agent code 看不出有這依賴
- Shape：TIGHT COUPLING（implicit）

### ⑧ Usopp publisher 600 行 monolith — 隱含 retry orchestration
- `agents/usopp/publisher.py` + `shared/wordpress_client.py` + `shared/seopress_writer.py`
- claim → validate → publish → seopress fallback → record + retry，全擠在 publisher.py；Chopper 之後要做留言 publishing 拿不出來
- Shape：DEEP 但 reusable 部分該抽出
- ADR-001+006 預設此 coupling，動之前要評估

### ⑨ `shared/doc_index.py` 純 pass-through
- `shared/doc_index.py` (~220L)
- 每個 function 1:1 對應底層 `state.db` query，沒商業邏輯
- Deletion test：刪了 → caller 直接查 `files_processed` 表，**複雜度真的消失** ✓
- Shape：SHALLOW pass-through
- 連動：`tests/shared/test_doc_index.py::test_stats_returns_per_category_counts` pre-existing 失敗，動 ⑨ 時順手修

### ⑩ `shared/anomaly.py` 抽 56 行純數學 — false sharing
- `shared/anomaly.py` + `agents/franky/anomaly_daemon.py`
- 抽 3-sigma math 但 metric 選擇 / SQL agg 還在 franky 內，沒人 reuse
- Shape：FALSE SHARING（抽得不夠廣）

### ⑪ `shared/seo_audit/` + `shared/seo_enrich/` 沒 re-export — 只有 skill 看得見
- `shared/seo_audit/*.py` (7 files) + `shared/seo_enrich/*.py` (3 files)
- 純函式 in shared/ 但沒 re-export，Franky weekly health check 想用拿不到、要 hack import
- Shape：FALSE SHARING（discoverability）

### ⑫ SEO 三 skill 跨 boundary share schema — _ADR-009 已 acknowledge_
- 不算候選：ADR-009 §Consequences §Risk #3 已記錄、有 schema_version + fixture mitigation

---

## ROI 排序（pending）

**最值得先動**（locality + leverage 大、爭議低）：
- **④ sanitizer 收編** — 守住 publish 前的安全 gate；Chopper / Sanji 加新平台時一行 settle
- **② approval FSM + schema 鎖死** — Chopper Phase 2 blocker，越早動越省

**漸進式 cleanup**：
- **⑨ doc_index 蒸發** — 半天清掉 + 順手修 pre-existing test failure
- **⑪ seo modules re-export** — `__init__.py` 一行 fix

**先別碰**：
- **⑧ Usopp publisher** — ADR-001+006 設計如此，等實質要做 Chopper 留言 publisher 才動
- **⑫** — ADR 已 acknowledge

**已完成**：
- ① + ⑤（合併處理）— PR #208 merged 2026-04-27 為 commit `e043e2a`，後續 PRs #2-#5 在 `docs/plans/2026-04-27-llm-facade-deepening.md`

---

## 下一輪 audit 觸發條件

- Sprint boundary 或 monthly check-in
- 加新 agent（Chopper）前
- 完成這次 backlog 任意 ≥3 項後（看新候選有沒有冒出來）
