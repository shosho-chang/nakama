# ADR-045 Panel 整合矩陣 — Robin 雙腦架構 + KB skill 家族

> 3-way panel（Claude 起草 → Codex/GPT-5 + Gemini 審），兩輪。2026-06-18。
> Round 1（D-A~D-D）：`2026-06-18-codex-robin-two-brain-audit.md`、`2026-06-18-gemini-robin-two-brain-audit.md`（Gemini 2.5 Pro）。
> Round 2（D-E~D-G）：`2026-06-18-codex-robin-two-brain-audit-r2.md`、`2026-06-18-gemini-robin-two-brain-audit-r2.md`（Gemini 2.5 Flash — Pro 整個下午 503）。
> Claude 另對所有 load-bearing code 宣稱做了獨立 file:line 自驗（見各列「Claude 自驗」）。

## Verdict 總表

| Round | Codex | Gemini |
|---|---|---|
| R1 D-A~D-D | **Reject as written**（9 改） | Approve-with-modifications（6 改） |
| R2 D-E~D-G | **Reject as written**（9 改） | Approve-with-modifications（6 改） |

兩個外部模型在「事實錯誤 + 框架是 ceremony + firewall 不可實作」上高度一致；分歧只在 D-D 波次邊界（見下）。

---

## Round 1（D-A~D-D）整合

| # | 議題 | Claude v1 立場 | Codex | Gemini | 三方 pattern | 決議 |
|---|---|---|---|---|---|---|
| R1-1 | **D-A concept 抽取來源** | 「Source Summary 與 Concept 抽取都從全文走」 | 錯：`_get_concept_plan` 只傳 `summary=summary_body`（ingest.py:438）；全文只進 `_generate_summary`，>30000 字走 map-reduce | 同意 Codex | universal（+Claude 自驗 line 414/438 屬實） | **採納**：改為「全文→摘要（full-body 或 map-reduce）→從摘要抽 Concept」 |
| R1-2 | **D-A attention-mirror 論證** | 全文 ingest 才能給漏看洞見 | 論證對，但「annotation 當 emphasis」未實作（`_get_concept_plan` 無 annotation 參數） | — | 2-of-2 | **採納**：保留論證，但標明 emphasis 注入是「未實作的可選增強」（§4，先不做） |
| R1-3 | **D-B 雙腦框架** | 「兩個獨立的腦」 | ceremony；`kb_hybrid_search:285-305` 已 Permanent-first 混 tier，無新 invariant | 同：是 candidate/authoritative 的改名，且「完整理解層」vs「低信任 candidate」自相矛盾 | **universal**（+Claude 自驗 boost 屬實） | **🚩 升級給修修**（見開放題 Q1）——「雙腦」是修修原話 |
| R1-4 | **D-C-4 kb-search 本質** | 「FTS5 + scripts/search.py」 | 錯：是 HTTP over `/kb/research`→`kb_hybrid_search`(engine=hybrid)；已索引+boost Permanent | 同意 | universal（+Claude 自驗） | **採納**：kb-query 不是 Wiki-only，需明確 `scope={permanent,candidate,all}` 契約 |
| R1-5 | **D-C-5 textbook-ingest** | 「本機未 tracked 殘留」 | worktree 內不存在；tracked 殘留是 docs/prompts/scripts（`docs/capabilities/textbook-ingest.md`、`scripts/Invoke-IngestTextbook.ps1`） | 同意 Codex | universal（+Claude 自驗 `git ls-files`） | **採納**：改成精確事實 |
| R1-6 | **D-C-3 Zoro Scout** | 「接 Zoro keyword/PubMed/arXiv」 | 過度宣稱：`gather_signals()` 只跑 trends；reddit/youtube/pubmed 宣告未接 | — | 2-of-2（+Claude 自驗） | **採納**：gap-scout 直接用 `shared/pubmed_client.py`+`arxiv_client.py`（已存在）+WebSearch；Zoro overlap 僅概念性 |
| R1-7 | **CJK anchor rot** | 未提 | 漏掉（Gemini 點名 Codex 漏） | D-A 的 `^p-N` 錨對中文脆弱（ADR-043 已 flag），ADR-045 未提 mitigation | single→but grounded in ADR-043 | **採納**：加風險，指派 kb-lint 做錨點健檢 |
| R1-8 | **D-D 波次** | kb-query 等 Wiki | kb-query 不必等（search 已可用）→ 該按依賴拆波 | kb-query 要等（合成需 substrate=Concepts，現為 0）→ 延後正確 | **1-1 衝突** | **Claude 裁**：kb-query 留 Wave 2（合成需 substrate），但 scope-tier 契約是前置工作（採 Gemini 主軸 + Codex 的 scope 點） |
| R1-9 | **taste-loop 過擬合 / nag-factory 治理** | 未提 | 未提 | taste-profile 會造 echo chamber；8 skill 增認知負荷→需治理 SLA | single（Gemini 獨見，高價值） | **採納**：加風險 + novelty injection + 治理註記 |
| R1-10 | **/draft-map schism** | 未提 | 未提 | ADR-043 D-8 的 /draft-map（Permanent 合成）與新 skill 切兩個域，未說如何互動 | single（Gemini 獨見） | **採納**（並在 D-G 一併處理，見 R2-7） |

---

## Round 2（D-E~D-G）整合

| # | 議題 | Claude v1 立場 | Codex r2 | Gemini r2 | pattern | 決議 |
|---|---|---|---|---|---|---|
| R2-1 | **D-E firewall 可實作性** | provenance:self + derivation-set 擋自我餵食 | **不可實作**：linter 只看 wikilink path-class；writer 只傳 page_path/body/mentioned_in；無 provenance graph。`provenance:self` 是「作者」非「依賴來源」 | 同意：firewall 目前是 conceptual，非 enforced；需大量新基建 | **universal**（+Claude 自驗 placeholder + writer 簽名） | **🚩 升級給修修**（見 Q2）：route O 降級 quarantine/延後 |
| R2-2 | **D-E transitive 循環** | derivation-set 處理 | 抓不到（lint 只看單頁）；需 graph reachability | 同：需 transitive cycle detection over full graph | universal | **採納**：明列 transitive 禁例；firewall 需 provenance DAG |
| R2-3 | **D-E linter 既有 gap** | 未提 | `KB/Permanent/` 既非 terminal 非 derived → 落入 `unknown` 放行 | 同意，critical loophole | 2-of-2 | **採納**：列為既有 linter bug，route O 前必修 |
| R2-4 | **D-F capture-time 可判定性** | 判準=「會不會 compound」 | 不可在 capture 判（混合 note）；Centaur §4 Fleeting 是 Nami 捕捉→「Robin 收領域 fleeting」=triage 後非 capture | 同：punt 給人增認知負荷；需 triage 機制 | **universal** | **採納**：default→Nami 捕捉；每日回顧 triage 決定；加 triage 欄位；「mixed」可雙掛 |
| R2-5 | **D-G /draft-map 復用** | gather 復用 /draft-map Permanent 邏輯 | **/draft-map 不存在於 code**（只在 ADR-043，未建）→ 無物可復用 | 同：unification 是 ceremonial | universal（+Claude 自驗 grep 無實作） | **採納**：移除「復用」宣稱；改為 future `ContextAssembly` service 的一個 mode |
| R2-6 | **D-G per-source→per-topic 成本** | 擴 RCP 即可 | **不便宜**：topic→N sources 需 retrieval/rank/dedupe/tier/aggregate→另開 `TopicContextPackageBuilder`，勿改 RCP | 同：非 trivial 架構轉變 | universal（+Claude 自驗 RCP per-source 簽名） | **採納**：gather 為新 package type，不 mutate `ReadingContextPackageBuilder.build()` |
| R2-7 | **D-G 非重疊契約** | gather 是跨層組裝 | 需明列：kb-query=search、/draft-map=Permanent-only read-only、gather=mixed scaffold、insight=judgment/defer | 同（並接回 R1-10 schism） | 2-of-2 | **採納**：寫入非重疊契約表 |
| R2-8 | **D-G assembly vs drafting** | gather 不代筆 | outline+MOC+evidence 近 drafting，需顯式 W-rules | **反駁 Codex**：ADR-024/CONTENT-PIPELINE 已定界，Codex 在 re-litigate 已定紅線 | 1-1（Gemini 反駁 Codex） | **Claude 裁**：採 Gemini——紅線已存在；但加一句 gather 輸出契約「只排版、零 prose」以絕後患 |
| R2-9 | **D-E CJK anchor rot for route O** | 未提 | 未提 | own-output 的 source_refs 若指 CJK Raw/Annotation 錨，繼承 rot 風險 | single（Gemini，接 R1-7） | **採納**：併入 R1-7 的 kb-lint 錨點健檢 |
| R2-10 | **D-G taste-loop 過擬合** | 未提 | 未提 | gather 重用 taste-profile→echo chamber，缺 novelty injection | single（Gemini，接 R1-9） | **採納**：併入 R1-9 |

---

## 升級給修修裁決的開放題

**Q1（框架）**：Codex+Gemini 兩輪一致說「雙腦」是 ceremony、建議改用 ADR-043 既有的「Candidate Layer / Authoritative Layer」。但「雙腦不對稱」是修修親自定的架構意圖。兩條路：
- (A) **保留「雙腦」當 workflow/心智模型層**，明寫「它疊在 candidate+authoritative 儲存架構之上，不新增任何儲存/檢索 invariant；它捕捉的是**餵養方向不對稱** + Robin 的 Wiki 是可被 insight/gap-scout 獨立挖掘的理解層」。
- (B) **採模型建議**，全文改用 candidate/authoritative，移除「雙腦」與「完整理解層 vs 低信任」的矛盾措辭。

**Q2（route O 時程）**：兩輪一致——route O firewall 用 `provenance:self` + 區域 derivation-set **不可實作/不安全**，真正需要的是 provenance DAG（graph reachability）+ 擴 `provenance_linter`（含修 `unknown` 放行 bug）。建議 **route O 移出近期波次，標 future/experimental**，等 provenance graph 與 linter wired 後再設計。（§10.4 本就標「待設計」，panel 只是硬化了門檻。）

**Q3（次要）**：D-D 波次——kb-query 留 Wave 2（合成需 substrate，採 Gemini），但 scope-tier 契約列為前置。kb-lint 可與 kb-query 同波。確認即可。

---

## 不採納（single-source noise / 已被反駁）

- Codex r2「assembly 會變 drafting 需 re-litigate 紅線」→ Gemini 正確反駁：ADR-024/CONTENT-PIPELINE 已定界，不重開（R2-8）。僅加一句輸出契約收尾。
