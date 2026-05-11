# Robin — Knowledge Base ingest + KB read

Robin 吸收 source（article / paper / book / podcast）→ 抽 concept / entity → 寫 wiki page。

## Language

### Reader-side bilingual (PR #354)

**Bilingual sibling**:
Reader「翻譯」按鈕產出 `Inbox/kb/{slug}-bilingual.md`，含中英對照段（英文段落後接 `>` blockquote 中文譯文）。**原 `{slug}.md` 不變**，bilingual 是 sidecar，frontmatter 帶 `derived_from`。修修 annotate 落 bilingual 上。翻譯走自家 translator（`shared/translator.py` Claude Sonnet + 台灣繁中 glossary）。
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
