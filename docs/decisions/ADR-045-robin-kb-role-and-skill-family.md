# ADR-045 Robin 在 Centaur KB 的角色 + KB skill 家族邊界

- 狀態：Proposed（v2，已過 3-way panel 兩輪；待修修拍板）
- 日期：2026-06-18
- 決策者：修修
- 關聯：
  - **延續 ADR-043**（Centaur 永久層）+ **Centaur 規格 v0.2** — 本 ADR 不改 Centaur KB 的儲存架構（人寫 `KB/Permanent/` 權威層 + AI 產出 `KB/Wiki/` 候選層），只把 **Robin 在這套模型裡的角色**與要長在上面的 **skill 家族邊界**定清楚。
  - **延續 ADR-042**（KB 輕量化 / retire textbook lane）— 釐清新 `ingest` skill 與 textbook 殘留的關係（D-C-5）。
  - **對齊 CONTENT-PIPELINE.md**（Stage 4 Line 2：不得產生完成句、段落或第一人稱正文）+ **ADR-001**（Robin = Knowledge Base / Nami = Secretary，D-F）+ **ADR-024**（RCP 是寫作前材料、不是 draft，D-G）。
  - 設計來源：`docs/plans/2026-06-17-robin-two-brain-and-ingest-skill-design.md`（§1–10）、`docs/plans/2026-06-17-agent-skillification-map.md`。

> **命名澄清（v1 → v2）**：v1 草稿沿用設計 doc 的「雙腦不對稱」一詞。修修指出該詞是 Cowork 設計階段新造、非他確定的術語；他確定的術語是 **Centaur KB**。Panel（Codex + Gemini）兩輪亦一致判「雙腦」為 ceremony（無新 invariant、且與 candidate/authoritative 措辭矛盾）。**v2 移除「雙腦」，全文回到 Centaur KB 既有講法。**

> **Panel audit trail（v1 → v2）**：3-way panel 兩輪（Claude 起草 → Codex/GPT-5 + Gemini 審）。逐字 audit：`docs/research/2026-06-18-codex-robin-two-brain-audit.md`、`…-gemini-…-audit.md`（R1，Gemini 2.5 Pro）、`…-codex-…-audit-r2.md`、`…-gemini-…-audit-r2.md`（R2，Gemini 2.5 Flash — Pro 當日 503）。整合矩陣：`docs/research/2026-06-18-robin-two-brain-panel-integration-matrix.md`。兩輪 Codex 皆 Reject-as-written、Gemini 皆 Approve-with-modifications；v2 採納見各決策。

---

## 脈絡

盤點（agent-skillification-map）發現 **Robin 是 skill 化價值最高的 agent**，KB 那條線社群已有高度重疊的開源 skill（Karpathy LLM-wiki：ingest / query / lint / evolve）。設計階段修修把他心中的執行邏輯講了一次請 Cowork 對照既有設計——核心邏輯是：

> 修修一手寫自己的永久卡（`KB/Permanent/`）；Robin 維護他從讀物 ingest 出來的筆記（`KB/Wiki/`）。兩層分開。Robin 跟修修學（吃修修的品味與判斷），修修不必回頭去讀 Robin 那層。

這條邏輯**本來就符合 Centaur KB**（ADR-043 / 規格 v0.2），不需要新架構或新名詞。本 ADR 只做兩件事：(1) 把 **Robin 在 Centaur KB 裡的角色與紅線**講死；(2) 框住要長在上面的 **skill 家族邊界**，使後續每個 SKILL.md 是 P7 執行級、不必每個都回來重審架構。**本 ADR 不設計個別 SKILL.md 細節。**

### 兩層的本質（Panel 校正：candidate vs authoritative，非「兩個對等的腦」）

|  | Robin 的 Wiki（`KB/Wiki/`） | 修修的永久卡（`KB/Permanent/`） |
|---|---|---|
| 載體 | Sources / Concepts / Entities | 永久卡 + 卡間 typed edges |
| 建構者 | Robin 從讀物 ingest | 修修本人手寫 |
| tier | **candidate / 草稿級**（ADR-043 D-3：`status: candidate` + `#ai-draft`，retrieval 降權） | **authoritative / 權威**（retrieval-first 優先，ADR-043 D-4） |
| 對方可否寫 | 修修可讀可不讀、不必維護 | **AI 絕不寫正文/status/連結**（紅線 1）；只回填白名單記帳欄 |

- **餵養方向不對稱**（這是 workflow 事實，不是新的儲存規則）：修修餵 Robin（全文 + 劃線 + 品味 + 每次 accept/defer/exclude）；Robin 餵修修（候選 Concept、洞見、推薦、defer 建議）。Robin 跟修修學，修修不必讀 Robin 那層。
- **重要事實（Panel grounding）**：檢索層 `shared/kb_hybrid_search.py:285-305` **已經**把 `KB/Permanent/` 排在 Wiki 前（Permanent-first tier）、`shared/kb_indexer.py:9-10` **已經**同時索引 Wiki + Annotations + Permanent。所以「candidate vs authoritative」是**已被程式碼 enforce 的真實架構**；本 ADR 不新增任何儲存/檢索 invariant。

---

## 決策

### D-A：ingest 吃全文，劃線是「強調訊號」不是內容過濾器（事實已校正）

Robin **ingest 整份來源全文**；修修的劃線只是「他在意這裡」的 emphasis signal，不是只 ingest 劃線段。

- **理由（硬）**：只 ingest 劃線＝修修注意力的鏡子，**永遠給不了「他漏看的東西」**——那「意想不到的洞見」就破功。
- **實際資料流（Panel + Claude 自驗校正 v1 錯誤）**：全文 → `_generate_summary` 產摘要（≤30000 字 pass-through 不截斷；超過走 `_map_reduce_summary` 分塊摘要）→ **Concept 抽取吃的是摘要 `summary_body`，不是原始全文**（`agents/robin/ingest.py:414,438` `_get_concept_plan(summary=summary_body)`）。Phase 1 的 Literature Note 才從 annotation render（`_render_literature` → `write_literature_note`）。
  > v1 誤寫「Concept 抽取從全文走」，Codex + Claude 自驗推翻，已改。
- **可選增強（不做）**：把 annotation set 當 emphasis 自動注入 concept 抽取 prompt（現 `_get_concept_plan` 無 annotation 參數，**未實作**）。等第一次真實 ingest 後再評估，先不做。

### D-B：Robin 對永久卡只「記帳 + 建議」，絕不 authoring

- **記帳（白名單三欄）**：唯一寫入口 `shared/permanent_layer.py` 的 `update_permanent_bookkeeping()`，只允許 `ALLOWED_BOOKKEEPING_KEYS = {source_refs, modified, aliases}`（line 37 已驗）；`assert_not_permanent_target()`（line 69）擋 agent 寫入路徑解析到 `KB/Permanent/`。
- **建議（不落修修的卡）**：開卡時卡名/typed-edge 候選 chips、Phase 5 沿修修親手連結補 backlink + Concept 頁 defer 建議（規格 v0.2 §8）、MOC 的 `%%agent-robin-unfiled%%` + 孤兒標記、insight/recommend。
- **絕不碰**：正文、status、修修的卡間連結。
- 這條完全承接 ADR-043 D-2 / 規格 v0.2 §7 紅線 1，**不新增權限、不開新寫入路徑**。

### D-C：skill 家族邊界（含 Panel 校正的事實 + 非重疊契約）

家族分三族；本 ADR 只定邊界，不設計 SKILL.md。

| Skill | 族 | 做什麼 | 與既有能力的關係（已校正） |
|---|---|---|---|
| `ingest` | Robin Wiki | 全文 + 劃線 → 候選 Wiki（route C 先行） | thin wrapper over `IngestPipeline`；見 D-C-5 |
| `kb-lint` | Robin Wiki | 孤兒/斷鏈/矛盾/重複/過時/缺引用 + **CJK 錨點健檢**，只報不修 | 新增；含錨點 rot 檢查（見風險） |
| `kb-query` | Robin Wiki | 帶引用的綜合答案 | thin wrapper over 既有 `kb-search`；**需明確 `scope={permanent,candidate,all}`** |
| `gap-scout` | Robin Wiki | 掃 Wiki 缺漏 → 找新文獻 → 推薦 | 用 `shared/pubmed_client.py`+`arxiv_client.py`（已存在）+WebSearch；**寫 Inbox/Watchlist，不 auto-ingest** |
| `insight` | 橋 | 挖修修沒想到的連結 | 原創；只建議不寫 Permanent |
| `recommend` | 橋 | 排「下一步讀什麼」 | 原創 |
| `gather` | 橋 | per-topic 寫作前組裝 | **future**；新 `TopicContextPackageBuilder`，見 D-G |
| `daily-review-assist` | 橋 | 開卡建議卡名 + chips | 既有 N522/N523 對話介面化 |
| `taste-profile` | 引擎 | 建品味模型餵上面所有 skill | 跨 skill 基建；先 stub；含 novelty injection（見風險） |

校正後的重疊釐清：

- **D-C-1 五條紅線約束整個家族**（規格 v0.2 §7），不只 pipeline。紅線 5（終端證據只能 Sources/Raw/Annotations）對 `kb-query` 合成、`insight`/`gap-scout` 推導都適用，已由 `shared/provenance_linter.py` 在 Concept/Output 寫入時 enforce。
- **D-C-2 vs Centaur daily review**：`daily-review-assist` 是既有 `agents/robin/daily_review.py` + N522/N523 的對話介面化，**不另造 LLM 判斷管線**；AI 絕不寫 Permanent 正文。低優先。
- **D-C-3 vs Zoro Scout**：`gap-scout` 用 `shared/pubmed_client.py` + `arxiv_client.py`（已存在）+ WebSearch；與 Zoro overlap **僅概念性**（Zoro `brainstorm_scout.gather_signals()` 現只跑 Google Trends，reddit/youtube/pubmed 宣告未接）。**紅線**：web 找到的東西人讀完劃線才 ingest，AI 不從 web 直接寫 KB。
- **D-C-4 vs 既有 `kb-search`**：`kb-search` 是 HTTP wrapper over `/kb/research` → `shared.kb_hybrid_search`（engine=hybrid，已索引 + boost Permanent），**不是** FTS5-only。`kb-query` 在其上加合成層，且**必須帶 scope tier**（否則跨 candidate/authoritative 混引）。兩者並存：kb-search=檢索、kb-query=合成。
- **D-C-5 vs textbook 殘留**：repo **無** tracked `textbook-ingest` skill（`git ls-files` 證實；worktree 內不存在；主倉庫本機 untracked 殘留與此無關）。tracked 殘留是文件/prompt/script：`docs/capabilities/textbook-ingest.md`、`prompts/robin/categories/textbook`、`scripts/Invoke-IngestTextbook.ps1` + 一串 ADR/plan。ADR-042 已 retire 該 lane。新 `ingest`（route C thin wrapper）是**唯一前進方向**；route B（書）走 Centaur 每日迴圈，不復用 textbook 殘留。建議 Wave 1 後把本機 untracked 殘留 skill 目錄送回收桶。
- **D-C-6 非重疊契約（接 Gemini /draft-map schism）**：`kb-query` = 對問題給帶引用的**綜合答案**；`/draft-map`（ADR-043 D-8，**future、未建**）= 對 `KB/Permanent/` 的 **read-only 組裝**；`gather` = 跨層（candidate + authoritative）的**寫作前 scaffold 組裝**；`insight` = **判斷 / defer 建議**。四者輸出形態不同，不並列競爭。

### D-D：分波落地（Wave 1 只做 ingest）

| 波 | 落地 | 先決 |
|---|---|---|
| Wave 0 | 本 ADR（角色 + 家族邊界）→ panel → 修修拍板 | — |
| Wave 1 | **`ingest`（route C）只此一個** | Wave 0 |
| Wave 2 | `kb-lint` + `kb-query`(帶 scope tier) + `taste-profile`(stub) | Wave 1 跑過幾篇真實 ingest |
| Wave 3 | `gather` + `gap-scout` + `recommend` + `insight` | Wave 2 + taste-profile 有料 |
| 擱置 | **route O（D-E）** | 待 provenance DAG + linter wired（見 D-E） |

- **理由 1（硬）**：Wave 2/3 的 skill 都需要**被 ingest 填過的 Wiki**（現 `KB/Wiki/Concepts/` 0 筆）才有東西可查/挖/補洞。
- **理由 2（硬）**：符合 `feedback_iterative_playbook_refinement`（run 過實際輸出再修 playbook）。
- **波次微調（Panel R1-8）**：`kb-query` 留 Wave 2（合成需有料的 Wiki，採 Gemini）；但 scope-tier 契約（D-C-4）列為它的前置工作（採 Codex）。`kb-lint` 可與之同波。

### D-E：route O（自有產出回流）— **擱置，標 future/experimental**

成熟 Permanent → 部落格/腳本/電子報 = 完成的思考結晶，可顯式 ingest 回 KB（`source_type=own-output`）成 Stage 7→1 複利飛輪。**但 firewall 目前不可安全實作，故擱置**（修修 2026-06-18 拍板）。

- **問題（Panel R2-1~3，已驗）**：紅線 5 的 `shared/provenance_linter.py` 只在 **wikilink 層**分 terminal/derived（path-class），writer 只傳 `page_path/body/mentioned_in`（`output_writer.py:93-96`、`kb_writer.py:255-258`），**無 provenance graph**。own-output 一旦當 Source 收進，會被當 terminal（合法證據），洗掉它的衍生出身 → Concept 能引用「自己生出來的那篇輸出」當獨立證據（citation laundering）。`provenance:self` 只標**作者**、非**依賴來源**，擋不住；transitive 循環（output1→ConceptA→output2→確認 ConceptA）更抓不到。
- **既有 bug（route O 前必修）**：`KB/Permanent/` 在 linter 既非 terminal 也非 derived → 落入 `unknown` 放行（`provenance_linter.py:134`）。
- **解 route O 真正需要的**：provenance DAG（記 `derived_from` / `cites_as_evidence`，做 graph reachability，拒絕 Concept/Output 指向其祖先/後代中任何 self-origin 節點）+ 擴 `provenance_linter` 吃 `source_type`/`provenance`/derivation closure。**這些都還沒蓋。**
- **擱置語意**：route O 移出所有近期波次；§10.4 本標「待設計」，本 ADR 硬化門檻為「待 provenance DAG + linter wired 才設計」。

### D-F：KB 邊界 — Robin（領域知識）vs Nami（操作型）

判準：**這東西會不會 compound 成可複用的領域知識？**

- **進 KB（Robin = Knowledge Base）**：讀物（route B/C/E）、（未來）思考結晶（route O）、領域 fleeting idea。
- **不進 KB（Nami = Secretary / TaskNotes）**：會議紀錄、報價、行政、交易型雜務。
- **一致性**：對齊 ADR-001（Robin = Knowledge Base、Nami = Secretary 含邀約報價）；本 ADR 不改職責，只把判準明文化。
- **fleeting 灰區解法（Panel R2-4，採納）**：判準**不可在 capture-time 判定**（fleeting 常混合操作 + 領域）。Centaur v0.2 §4 的 Fleeting 由**人 + Nami 捕捉**。故：(a) **預設一律先進 Nami 捕捉**；(b) **每日回顧 triage** 時才用判準決定升進 KB（Robin）或留 TaskNotes（Nami）；(c) 「mixed」可雙掛。即「Robin 收領域 fleeting」指 **triage 後**，非捕捉時。triage 欄位細節留 skill 落地。

### D-G：`gather`（per-topic 寫作前組裝）— **future skill**

修修說「我要寫關於 X」→ Robin 跨 Permanent + Wiki 撈相關卡/Concept/Source/Entity +（若有）MOC + 證據叢集 + 問題清單 + outline skeleton，**攤出來排給修修看，不代筆**（ADR-024 紅線）。

- **地基（已驗）**：`agents/robin/reading_context_package.py` 的 `ReadingContextPackageBuilder.build(reading_source, ...)` 現為 **per-reading-source**（單一來源）；`thousand_sunny/routers/writing_assist.py` 亦 per source。
- **成本校正（Panel R2-5,6，採納）**：(a) `/draft-map` **目前不存在於 code**（只在 ADR-043，未建）→ 不能宣稱「復用 /draft-map」；(b) per-source → per-topic **不是便宜 refactor**，topic→N sources 需 retrieval/rank/dedupe/tier 分離/aggregate → **另開 `TopicContextPackageBuilder`，勿 mutate 既有 RCP**。
- **架構落點**：視為未來 `ContextAssembly` service 的一個 mode——`/draft-map` = `ContextAssembly(scope=permanent, mode=read_only)`；`gather` = `ContextAssembly(scope=mixed, mode=scaffold)`；RCP 維持 per-reading-source。
- **紅線**：`gather` 純確定性聚合 + 排版，**輸出零 prose、零完成句**（CONTENT-PIPELINE Stage 4 Line 2 / ADR-027 scaffold-not-ghostwrite）。

---

## 決策依據（已驗證 file:line / 事實）

- **D-A**：`agents/robin/ingest.py:296,309-330`（_generate_summary pass-through / map-reduce）、`:414,438`（_get_concept_plan 吃 summary_body）、`:150-151,243-253`（Literature 從 annotation render）。
- **D-B**：`shared/permanent_layer.py:37`（ALLOWED_BOOKKEEPING_KEYS）、`:69`（assert_not_permanent_target）；規格 v0.2 §7 紅線 1 / §8。
- **D-B 檢索已混 tier**：`shared/kb_hybrid_search.py:285-305`（Permanent-first）、`shared/kb_indexer.py:9-10`（索引 Wiki+Annotations+Permanent）。
- **D-C-3**：`agents/zoro/brainstorm_scout.py:74-85`（gather_signals 只 trends）；`shared/pubmed_client.py`、`shared/arxiv_client.py` 存在。
- **D-C-4**：`.claude/skills/kb-search/`（HTTP wrapper）→ `/kb/research` → `shared.kb_hybrid_search`。
- **D-C-5**：`git ls-files` 無 `*textbook*` skill；tracked 殘留為 `docs/capabilities/textbook-ingest.md`、`scripts/Invoke-IngestTextbook.ps1` 等；ADR-042。
- **D-E**：`shared/provenance_linter.py:8`（placeholder）、`:118-134`（terminal/derived/unknown 分類，Permanent 落 unknown）、`:190-252`（lint_page 只看單頁 wikilink）；`shared/output_writer.py:93-96`、`shared/kb_writer.py:255-258`（writer 只傳 page_path/body/mentioned_in）。
- **D-F**：ADR-001:24-25（Robin=Knowledge Base / Nami=Secretary）；規格 v0.2 §4（Fleeting 人+Nami 捕捉）、:106（每日回顧消化 open fleeting）。
- **D-G**：`agents/robin/reading_context_package.py:201-208`（build per-reading-source）；`thousand_sunny/routers/writing_assist.py:197,217-219`（per source）；`/draft-map` 僅見 ADR-043，無實作。
- **Stage 4 Line 2**：CONTENT-PIPELINE.md:33-36（不得產生完成句/段落/第一人稱正文）。

---

## 風險與緩解

- **CJK 錨點 rot（Panel R1-7 / R2-9）**：Obsidian `^p-N`/`^loc` 區塊錨對無詞界的中文不穩，改句即可能靜默斷鏈；D-A 的 `source_refs` 直接依賴它。**緩解**：指派 `kb-lint` 定期驗 `Raw`/`Annotations` 的區塊錨、斷鏈報修修（ADR-043 panel 已 flag，本 ADR 落實 owner）。
- **taste-loop 過擬合 / echo chamber（Panel R1-9 / R2-10）**：`taste-profile` 越學越貼品味，可能只推修修已知、扼殺「意想不到的洞見」。**緩解**：`taste-profile` 含 configurable **novelty injection**（定期摻離群文件/連結）；`gather`/`insight`/`recommend` 調用時注入發散視角。
- **nag-factory / 認知過載（Panel R1-9 / R2-4 blind spot）**：8 skill 會生大量 suggestion/candidate/review item，增修修認知負荷，重演「人寫層永遠空著」的安靜死法。**緩解**：每 skill 落地定 review 節奏（stale recommendation 自動歸檔，呼應規格 v0.2 §「14 天未動歸檔」）；分波落地控制同時上線的 skill 數。
- **過度工程化**：個人系統，紅線鎖只做 author 欄 + tripwire 測試，不 hash、不 runtime 硬擋（延續 ADR-043）。
- **route O 循環**：擱置至 provenance DAG 就緒（D-E）。

---

## Vault 路徑/權限影響評估（ADR-028 觸發檢查）

Centaur KB 用到的 `KB/Wiki/`、`KB/Permanent/`、`KB/Literature/`、`KB/Annotations/`、`KB/Fleeting/`、`KB/MOCs/` **都已存在且已被 ADR-043 / 規格 v0.2 治理**；本 ADR 不新增資料夾、不開新寫入路徑、不改誰能寫哪（紅線承接，非新增）。**因此不修改 `docs/VAULT-LAYOUT.md` 與 LifeOS `CLAUDE.md`。** 未來 Wave 2/3 skill 或 route O 落地時若動到 vault 路徑/權限，再於該 PR 同步（reviewer 抓）。

---

## 落地交接

- **Wave 1（ADR 過後）**：照設計 doc §6 P9 六要素建 `.claude/skills/ingest/`（thin wrapper over `IngestPipeline`，不改 pipeline 對外行為，四件套 + evals）。**只此一個 skill。** D-A 的事實校正同步進 SKILL.md 草稿（concept 抽取從摘要走、annotation-emphasis 為未實作可選增強）。
- **不在 Wave 1**：家族其餘 skill（Wave 2/3）、route O（擱置）、annotation-emphasis 增強。
