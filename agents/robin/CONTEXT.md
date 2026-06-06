# Robin — Knowledge Base ingest + KB read

Robin 吸收 source（article / paper / book / podcast）→ 抽 concept / entity → 寫 wiki page。

## Language

### Reader-side bilingual (PR #354)

**Bilingual sibling**:
Reader「翻譯」按鈕產出 `Inbox/web/{slug}-bilingual.md`（ADR-028 §5；舊路徑 `Inbox/kb/` 已退場），含中英對照段（英文段落後接 `>` blockquote 中文譯文）。**原 `{slug}.md` 不變**，bilingual 是 sidecar，frontmatter 帶 `derived_from`。修修 annotate 落 bilingual 上。翻譯走自家 translator（`shared/translator.py` Claude Sonnet + 台灣繁中 glossary）。
_Avoid_: 翻譯檔, translated md（用 bilingual sibling）

**Bilingual reader**:
`reader.html` 雙語 toggle — 一份 MD 含中英對照段，UI 切換顯示譯文（`譯文 ✓` / `譯文 ✗`）。**不是兩個檔**。
_Avoid_: 雙語 reader pair（用 bilingual reader）

## Inbox sibling collapse

當 `{stem}.md` + `{stem}-bilingual.md` 同時存在時，inbox 列表只顯示 bilingual 那一筆（user-facing 的閱讀 / annotate target），原 `{stem}.md` hide 起來但保留作再翻譯來源。實作在 `thousand_sunny.routers.robin._get_inbox_files`。

## Source Promotion

### Canonical vocabulary

**Reading Source** = 尚未必然進入正式 KB 的閱讀來源，可來自 ebook、web document 或 inbox document。

**Reading Overlay** = 修修對某個 Reading Source 的個人互動層，包含 `KB/Annotations`、`digest.md`、`notes.md`、highlights、annotations、reflections 與 reading-session metadata。Reading Overlay 記錄「這份 source 對修修意味著什麼」，不是作者 factual claims 的權威來源。

**Source Promotion** = 將原本只是拿來閱讀的 Reader source（ebook / web document / inbox document）提升為 KB 的 knowledge-grade source，走 textbook-grade 的整本/整份 source ingest：切章或切段、產生 Source pages、抽 Concept / Entity、維護 `mentioned_in` backlinks。

**Promotion Review** = Source Promotion 寫入正式 KB 之前的 staging review，讓 LLM 先提出 include / exclude / defer 建議、理由、evidence、risk 與 action，再由修修作為 checkpoint / brake 決定哪些 items commit。

**Promotion Manifest** = 每次 Promotion Review / commit 的 replayable decision record。`KB/Wiki` 是 materialized output；manifest 是決策與恢復來源，保存 model recommendation、human decision、evidence、risk、confidence、source/reader scores、commit batch 與 touched files。

**Source-local Concept** = 對理解單一 Reading Source 有幫助，但尚未值得成為跨來源 KB 概念的局部概念。它可留在 Source page glossary / local concept map。

**Global KB Concept** = 值得進入 `KB/Wiki/Concepts` 的長期概念，通常具備跨 source 聚合價值、內容輸出價值、足夠 evidence / definition / relations / recurrence。

**Reading Context Package** = Robin 從 Stage 3 交給 Stage 4 的寫作前材料包，整理 annotations、notes、digest、promoted source map、Concept links、idea clusters、questions、evidence board 與 outline skeletons。它是給修修手寫的 scaffolding，不是 draft。

**Writing Assist Surface** = Stage 4 的呈現/操作介面，用來顯示 Reading Context Package、插入 links / references / prompts，並輔助修修手寫 Line 2 atomic content。它可由 Brook-owned 或 shared UI 承接，但不得自動 compose 正文。

Promotion 的觸發門檻是 **source quality**，不是 reading completion。讀完一本書只是最自然的提示時機，不是必要條件：修修可以讀完後選擇 promotion，也可以在讀到一半、甚至剛匯入時，因為判斷該 source 足夠扎實而手動 promotion。

Annotation 不是 promotion 的必要輸入；它是 overlay，用來標示修修的 salience、疑問與個人觀點。高品質 source 即使 annotation 很少，仍可 promotion；低含金量 source 即使 annotation 很多，也可以只做 annotation-only sync。

Promotion 的輸出是 **claim-dense source map**，不是 full-text mirror。修修合法購買的原始檔或轉出的完整 raw/original track 可以作為 private evidence 保存；但 `KB/Wiki/Sources/...` 不應散布過長全文。Source page 應保留章節結構、核心主張、重要數據、圖表摘要、關鍵術語、短 quote anchor、Concept/Entity links 與 coverage manifest。Brook 或其他輸出流程優先讀 Source page / Concept pages；需要精確引文時才回 evidence track。

Promotion 與 annotation 的 authority split：**source map 管「作者到底說了什麼」，annotation 管「這對修修意味著什麼」**。Factual claims（作者主張、定義、數據、機制）以 original evidence + promoted source map 為權威；personal salience、疑問、聯想、應用、不同意與創作線索以 annotation/reflection 為權威。Annotation 中的未確認想法不可直接升成 factual claim，應保留在 personal insight / reading notes / questions 類區塊並連回 evidence。

Full Source Promotion 必須先進 **promotion review / staging**，不直接寫入正式 `KB/Wiki`。系統先分析 source，提出兩個可審核列表：預計納入 KB 的 items（Source pages / Concept candidates / Entity candidates / conflicts）與預計排除的 items，兩者都必須附原因。修修在 review UI 中做最後裁決後，通過 acceptance gate 的 items 才 commit；annotation-only sync 可維持較輕量，但 promotion 的 blast radius 較大，不能一鍵無審核寫入正式 KB。

Promotion review 的人機分工：LLM 是主要分類器與建議者，修修主要扮演 checkpoint / brake，而不是逐條分類器。隨模型能力與 KB 掌握度提升，人類介入比例應下降；review UI 應讓 LLM 提供強建議、納入/排除理由、風險與高風險例外，修修只需在關鍵節點暫停、抽查、調整規則或否決衝動 commit。

Promotion review item 的基本 schema 應強制包含 `recommendation`（include/exclude/defer）、`reason`、`evidence`、`risk`、`action`（create/update_merge/update_conflict/noop 等）與 `confidence`。不論納入或排除，都要迫使 LLM 認真輸出判斷根據；缺 evidence anchor 的建議不可直接 commit，應進 `needs_evidence` / defer。

Annotation 可作為 promotion review 的 ranking / exception signal，但不可直接改變 factual action。Review item 應分開記錄 `source_importance`（概念對 source 本身的重要性）與 `reader_salience`（概念對修修本次閱讀的重要性）。低頻但高 annotation signal 的 item 可進人工 exception；高 source importance 即使無 annotation 仍可 include；annotation 中無 evidence 的延伸想法進 personal insight，不直接 create factual Concept。

Promotion commit 的 source 輸出層級：長 source（book / textbook / long report）產生 chapter/section-level Source pages + index/Book Entity 總覽；短 source（article / short document）可維持 single Source page + section anchors。不要把長書寫成單一巨頁；`mentioned_in` 應盡量指到具體章節/section，方便 retrieval、evidence 回查與 acceptance gate。

Promotion 的多語言邊界：雙語 display 只給 Reader 看，不當 factual evidence。英文書以英文 original track 為 evidence；純中文書以中文 original track 為 evidence；Concept canonical layer 要能跨語言聚合，不可因中文/英文名稱不同就各開一頁。Promotion review item 應標記 `evidence_language` 與 `canonical_match`（match_basis: exact_alias / semantic / translation / none + confidence）。低信心 cross-lingual match 進 exception，不自動 merge。

Promotion 的概念層級：先抽 `source-local concepts`，再決定少數是否升為 `global KB Concept`。Source-local concept 對理解單一 source 有幫助，可保留在 Source page glossary / local concept map；只有具備跨 source 聚合價值、長期內容輸出價值、足夠 evidence/definition/relations/recurrence 的概念，才 create/update `KB/Wiki/Concepts`。Review action 應區分 `keep_source_local`、`create_global_concept`、`update_merge_global`、`update_conflict_global`、`exclude`。

Repeated reading semantics：canonical annotation store 應保留 `reading_session` / `reading_round` 維度，每個 highlight / annotation / reflection 都是一次具體互動；但 `digest.md` 與 `notes.md` 預設呈現同一 source 的 merged reading view。UI 可在需要時切換全部 / 某次閱讀 / 最近一次；promotion review 可使用全部 annotation，也可只用某個 reading session 作為 `reader_salience` signal。

After promotion, rereading a source updates personal reading overlay by default (`KB/Annotations`, `digest.md`, `notes.md`) and must not automatically rerun full promotion. If new annotations/reflections reveal a coverage gap, previously excluded high-value concept, or new evidence-worthy section, the system may suggest `delta promotion review`. Full re-promotion with a newer model is a manual action and must not silently overwrite prior reviewed decisions.

Promotion review must write a replayable `promotion manifest` per run. `KB/Wiki` is the materialized output; the manifest is the decision record. It should preserve model recommendation, human decision, reason, evidence, risk, action, confidence, `source_importance`, `reader_salience`, commit status, and touched files. Future newer-model re-runs should diff against prior manifests and mark previously approved/rejected/deferred items instead of forgetting prior review decisions.

Promotion commit is item-level partial commit, not whole-source all-or-nothing. Review can be gradual; approved items may commit while deferred/rejected items remain in the manifest. Each commit batch must be transaction-like in the manifest: batch id, approved/deferred/rejected item ids, touched files, errors, and resulting `promotion_status` (`partial` / `complete` / `needs_review` / `failed`). Partial failures must be visible and auditable.

Source Promotion ownership boundary: Robin owns domain logic; Thousand Sunny owns presentation and human checkpoint UI. Robin/shared should implement source quality analysis, source-local concept extraction, global Concept matching, promotion manifest storage, acceptance gates, and KB commit. Thousand Sunny should expose entry points, review UI, approve/reject/defer actions, and progress/status display. Do not bury promotion domain logic in routes/templates; CLI and future agents must be able to reuse the same Robin/shared service.

Source Promotion requires a lightweight preflight before any expensive analysis job. Preflight should inspect metadata, chapter/section count, word count, language/evidence track availability, rough token/cost/time estimate, and structural risks (weak TOC, OCR issues, mixed language, missing original track) without heavy LLM spend. Full promotion analysis is a queued, cancellable job started only after explicit confirmation, with scope controls (whole source, selected chapters, source map only, concept promotion later).

Promotion commit recovery is manifest-driven, not automatic destructive rollback. Because the vault is a filesystem and may have concurrent edits, each commit batch should record touched files with before/after hashes, operation type, backup path when applicable, errors, and status (`committed` / `partial_failed` / `failed`). On failure, the UI may offer reviewed restore/resume/cleanup actions, but must not silently delete or reset files. Hash mismatch during restore requires human confirmation.

Stage 4 boundary: Source Promotion itself remains Stage 3 and must not auto-generate Shosho's book-review/article draft. However, a separate, explicitly triggered Stage 4 writing-assist action may reduce blank-page friction by organizing Shosho's own annotations/reflections plus promoted source map into prompts, questions, idea clusters, and optional outline candidates. This assist must not produce publishable prose as if it were Shosho's voice; it scaffolds Shosho's writing, it does not replace it.

Writing-assist output boundary: allowed outputs are structure skeletons, question prompts, idea clusters, tension maps, evidence boards, outline candidates, missing-piece prompts, and pointers to Shosho's own annotations/source evidence. It may say what a section needs to answer and which materials could support it; it must not generate completed sentences, finished paragraphs, or a first-person opening in Shosho's voice.

Stage 4 ownership bridge: Robin may produce a `Reading Context Package` from annotations, notes, digest, promoted source map, Concept links, idea clusters, questions, evidence board, and outline skeletons. This package is a Stage 3 → Stage 4 handoff object for Shosho's hand-writing, not a draft. A Brook-owned or shared `Writing Assist Surface` may present the package, insert links/references/prompts, and help Shosho navigate materials, but must not use it to ghostwrite Line 2 atomic content. After Shosho writes the atomic content, Brook may use that finished human-authored piece for Stage 5 multi-channel production.

Documentation source-of-truth layering: `agents/robin/CONTEXT.md` owns canonical vocabulary and domain rules; an ADR in `docs/decisions/` should own the reasons and trade-offs behind Source Promotion; `CONTENT-PIPELINE.md` should own the day-to-day Stage 2/3/4 workflow; PRDs and GitHub issues should own implementation delivery and must not become the only source of truth for domain decisions.

## Centaur Zettelkasten（ADR-043）

把方法論「AI 增強的卡片盒 / The Compounding Vault」(served at `/centaur_zettel`) 落到 nakama 的詞彙層。核心是把 Robin 既有的「人寫、AI 搭 scaffold」紅線（原本只管 Stage 4 的 **Writing Assist Surface**）往下延伸到 Stage 3 的**永久筆記層**。方法論文件由 Claude chat 在未掌握 codebase 下寫成 → 採其 high-level method，實作以 nakama 為準。

**Friction Selection（摩擦篩選）**：
本系統第一性原理。判準一句話——「這個動作親手做，會不會產生一個我原本沒有的判斷或理解？」會 → 留給人（生產性摩擦）；不會、只是執行已有的判斷 → 交給 AI（純損耗摩擦）。下面所有人機分界都從這條推導。

**Permanent Note（永久筆記 / 永久卡）**：
住在 `KB/Permanent/{concept}.md`、由修修**親手用自己的話**寫的原子概念卡，是卡片盒的核心層、Stage 3 的最終產物。依概念命名（不依書名/來源/時間，Matuschak）。AI 對它的**判斷型內容唯讀**。
_Avoid_: concept page（那是 AI 候選）、atomic content（那是 Stage 4 文章，見 flagged ambiguities）

**Concept Candidate（概念候選）**：
AI 從 source 抽出、標 `status: candidate` + `#ai-draft` 的概念草稿，住在 `KB/Wiki/Concepts/`。是修修改寫成 Permanent Note 的原料，**不是成品**。取代舊的「LLM 寫成品概念頁 + Opus auto-merge」。**候選須影響檢索**（consumer 降權、opt-in），不只 frontmatter，否則 Brook 會把 AI 草稿當人類知識引用。**生命週期**：永久卡從候選寫成後，候選轉 `status: superseded` + `promoted_to: [[永久卡]]`（留 provenance、從主搜尋濾掉）。
_Avoid_: finished concept page、canonical concept

**Judgment field / Bookkeeping field（判斷型／記帳型 frontmatter）**：
永久卡的 frontmatter 拆兩類。**判斷型**（人寫，AI 不得改）：`status`、正文、連結關係描述。**記帳型**（AI 可代寫）：`source_refs`、`created`、`modified`、`aliases`、`mentioned_in`/backlinks 鏡像、`tags`。判準同 Friction Selection：填這欄會不會產生新判斷？

**Maturity（成熟度 / `status`）**：
永久卡四級：種子 seedling → 發展中 budding → 成熟 evergreen → 已取代 superseded。**每個向上箭頭都是修修動手**；AI 只能經 lint 提示晉級時機，不得自行改 `status`。

**Link relationship（連結關係）**：
採納一條連結時人寫下的型別化關係：支持 / 矛盾 / 延伸 / 舉例。連結三節點——**AI 提候選連結（可）→ 修修採納並寫關係（僅人，紅線）→ AI 鏡像反向連結（可）**。正向連結與關係寫在永久卡**正文**（故受 by-construction 保護）。
_Avoid_: 把「加連結」當單一動作（它是三節點，只有中間是紅線）

**Connection Discovery（連結探勘）**：
AI 對（趨近全量的）小型 permanent 語料**直接 LLM 推理**，找出修修一時連不起來的連結、pattern 與跨卡矛盾。corpus selector **只餵 `KB/Permanent/` 正文 + 精簡 metadata**，每次 all-corpus call 前做 **token-budget preflight**。**不靠向量檢索**（ADR-042 已移除 dense lane）——規模小到能塞進 context、LLM 推理即可做概念類比；撞方法論規模煞車（200–500 文件）才升級，**且屆時只對 `KB/Permanent/` 建小向量索引**（非 raw vault）。這是 AI 的核心貢獻之一，與「損耗代工」並列，不是附屬。

**MOC（Map of Content）**：
住在 `KB/Wiki/MOCs/` 的活目錄，**AI 維護骨架、修修定邊界**。永久卡不「擁有」MOC 歸屬；AI 把卡掛進 MOC（提建議、修修確認關係）。不在永久卡的鎖底下。

**Fleeting Note（瞬時筆記）**：
`KB/Fleeting/inbox.md` 的一顆種子，最低摩擦捕捉、修修定期 triage。四個去處：丟棄 / 發展成永久卡（人）/ 觸發 ingest / 連到既有卡。AI 只 surface 關聯與建議處置，**不自動清空、不代寫成卡**。

**Spine（共用脊椎）**：
三條來源（B 書 / C 文章 / E 影片）證據到手後共用的同一條 Stage 3 路徑：**AI 文獻摘要 → AI 概念候選 →【紅線】→ 修修親手寫永久卡 → AI 維護 MOC + lint + 連結探勘**。三條只在「證據怎麼來」不同（B: N519 LLM 抽 claim / C: LLM 摘要 / E: 修修畫線叢集），到手後不再各搞各的。

**Authorship by provenance（authorship 看來源，不看 transport）**：
永久卡的「AI 不得寫」鎖是**語意鎖、靠 by-construction + 輕量 tripwire 實現**，不做 hash、不做 runtime 硬擋（這是修修個人筆記系統，概念對齊即可）。修修經 Obsidian 或（未來）Web UI authoring surface 寫檔，即使技術上是「系統寫磁碟」，也算修修的 authorship；AI 路徑的 target resolver 永遠解析不到 `KB/Permanent/`，agent 唯一能碰它的是只改記帳型 key 的 `update_permanent_bookkeeping`。每筆寫入帶 `author: human | agent_*` frontmatter；tripwire = 測試斷言 promotion target 永不回 `KB/Permanent/` + 正文寫入只來自 human 路徑 + dev/CI grep 稽核（比 hash 輕、但可稽核，取代「純慣例=hope」）。

### Relationships（Centaur 層）

- 一個 **Source** 經 Promotion 產生 **Concept Candidate**（多個），修修把候選改寫成 **Permanent Note**（一卡一概念）
- **Permanent Note** 之間由 **Link relationship** 連結；**MOC** 聚合多張 Permanent Note；**Connection Discovery** 跨 Permanent Note 找關聯
- **Fleeting Note** triage 後可長成 **Permanent Note**、或觸發新的 Source ingest
- **Permanent Note** 是 Stage 3 產物；**Writing Assist Surface**（Stage 4）讀 Permanent Note + Reading Context Package 輔助修修寫文章（Line 2 atomic content）——兩者同一條「人寫紅線」，但層級不同

### Example dialogue

> **修修：**「ingest 完這篇文章，AI 是不是就把概念頁寫好了？」
> **領域規則：**「不。AI 只產**概念候選**（`#ai-draft`），住 `KB/Wiki/Concepts/`。把候選改寫成你自己的話、存進 `KB/Permanent/`，那一筆是你親手寫的——那才是**永久卡**。AI 對永久卡的正文與 status 唯讀，只幫你補 `source_refs`、鏡像反向連結這些記帳。」

### Flagged ambiguities（Centaur 層）

- **「atomic content」（Stage 4）vs「atomic note / 永久卡」（Stage 3）** — 同樣是「修修親手寫的原子單位」，但 atomic content = 要發布的文章正文（Writing Assist Surface 輔助、Brook 後續加工）；永久卡 = 卡片盒的durable 概念卡（Connection Discovery / MOC 的節點）。不可混用。
- **「Global KB Concept」語意位移** — CONTEXT.md 上文定義它為「值得進 `KB/Wiki/Concepts` 的長期概念」並暗示是成品。ADR-043 後：`KB/Wiki/Concepts/` 內容**降級為概念候選**（`#ai-draft`），不再是 auto-written 成品；「長期概念的權威層」改由人寫的 **Permanent Note** 承接。舊定義的「成品」語意作廢。
- **方法論「AI 對 permanent/ 絕對唯讀、永不寫入」一句作廢** — 修修補充把 frontmatter 拆判斷型/記帳型後，記帳型 key 由 AI 寫；鎖守的是「判斷」不是「每個字元」。以 ADR-043 的語意鎖為準。
