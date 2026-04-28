---
name: 2026-04-27 codebase architecture audit
description: 12 deepening 候選 + ROI 排序 + 哪些 done 哪些 pending；下次想做架構改善先看這份再決定要不要重跑 skill
type: project
originSessionId: 3d901e7b-183e-450a-a0bf-2f06311b6452
---
2026-04-27 第一次跑 `/improve-codebase-architecture`（mattpocock skill）對 nakama repo 完整掃過，產出 12 個 deepening 候選。

**完整清單 + 細節**：[docs/research/2026-04-27-codebase-architecture-audit.md](../../docs/research/2026-04-27-codebase-architecture-audit.md)

**已完成（PR merged）**：
- ① LLM facade shallow + ⑤ anthropic/gemini 樣板重複 — PR #208 merged 2026-04-27 為 `e043e2a`
- ⑨ doc_index — PR #211 merged 2026-04-27 為 `9362cfe`（audit 描述誤判 #1 — 不是 shallow pass-through，是 FTS5 + Windows path bug；`as_posix()` 一行 fix）
- ⑪ seo_enrich re-export — PR #212 merged 2026-04-27 為 `a095ad8`
- ④ sanitizer 收編 — PR #214 merged 2026-04-28 為 `97fe5b2`（audit framing 誤判 #2 — 兩個 compliance scanner deprecation；net −57 LOC）
- ② approval payload helpers push-down — PR #217 merged 2026-04-28（audit framing 誤判 #3 — 真 shape 是 isinstance ladder push down @property）

**No-op verified（framing 誤判 / ROI 偏低）2026-04-28**：
- ⑥ memory.get_context — audit 寫「Tier 1/2/3 + truncate」全錯（誤判 #4），實際只讀 Tier 2、`task` / `max_tokens` 是 ADR-002 預留 scaffolding（line 89-97 寫明 future episodic + 壓縮）；保留參數 = 不動 code
- ⑩ anomaly — audit 寫「false sharing 抽得不夠廣」誤判 #5：模組 docstring 言明 testability split，`tests/shared/test_anomaly.py` 用純 list 測，stated purpose 達成；YAGNI 不該為虛構 reuse 擴大；保留現狀
- ⑦ prompt_loader — implicit 真存在（16/40 prompt 用 partial token、13 caller）但 `format_map` 對 unused token no-op、token 本身是 in-template declaration、refactor cost > marginal clarity gain；DEFER 直到有第三方 prompt build pipeline 介接

**Pending（真要動 code）**：
- **③ KB writer interface 收緊** — ADR-011 §3.5 已 ack 待收緊，197-line `upsert_concept_page(action, ...)` 4-action dispatcher，唯一 caller `agents/robin/ingest.py:514`；中優先
- **⑧ Usopp publisher 600 行 monolith** — 等實質要做 Chopper 留言 publisher 才動

**audit skill 教訓（5 次驗證後）**：12 候選裡 5 個 framing 誤判（②④⑥⑨⑩）—

| 誤判類型 | audit 反射 | 真實 shape |
|---|---|---|
| textual coupling 反射 | 「沒鎖、會 silent fail」 | Pydantic Literal + assert + raise 已 fail-fast，real 是 OOP polymorphism 漏寫（②） |
| single consumer ≠ false sharing | 「抽得不夠廣，沒人 reuse」 | testability / edge-case isolation 是 valid stated purpose（⑩） |
| dead param ≠ shallow facade | 「一函式 N input 蓋 deep impl」 | ADR-documented intentional preservation（⑥） |
| 名字反射 ≠ 真實邏輯 | 「shallow pass-through」 | FTS5 / OS-specific bug 才是真 hit（⑨） |
| 命名重複 ≠ 真重複 | 「三套 sanitizer 統一」 | 三個關注點不同，真重複只在兩個（④） |

**meta 規律**：audit 真正命中的是 **cohesion 錯位 / 漏 close-the-loop / OS-specific bug / re-export 缺漏 / 過時 deprecation**，都是 OOP / 收尾課題；不是 type system 或 abstraction-degree 課題。

**下次跑 skill 必須**（強化版）：
1. 對每個候選跑 deletion test 讓 import / test 真 break
2. grep 真實 fail path（不只看 module 名，要看 docstring + tests + ADR）
3. 對照 ADR 看 dead param 是否是 documented preservation
4. 確認 abstraction 的 stated purpose（docstring + 既存 test 用法）— testability 算 valid，不該被 framed 為 false sharing

**Why**：audit 的價值不在「找一次」，在「baseline 對照下次掃」+ ledger 累積讓 skill bias 變看得見。下次跑 skill 拿這份比 + 用上面 4 步驟先過篩，再決定要不要動 code。

**How to apply**：要動架構任務前先讀這份。已 done 的不重新分析；no-op verified 的不重評；pending 的按 ROI 排（目前真要動只剩 ③）。重跑整個 audit 的觸發條件：sprint boundary、加新 agent（Chopper）、完成 backlog ≥3 項。
