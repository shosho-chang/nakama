# ADR-046 三來源全量 ingest 進 KB — 解除 ADR-043 時間 gate（改 capability 投資節流）

**Status:** Proposed（panel-reviewed v2；待修修拍板）
**Date:** 2026-06-20
**Supersedes (部分):** ADR-043 §決策 7（3 個月 adoption time-gate）
**Relates:** ADR-043 / ADR-045 / ADR-035 / ADR-024 / ADR-042 / ADR-034 / ADR-017

**審查軌跡（v1 → v2）：** Claude 起草 v1 → 4 個 codebase 查證 sub-agent（逐條對現況 code）+ Codex(gpt-5.5) 反方審 + Gemini 2.5 Pro 不同推理鏈審 → v2。Codex/Gemini **皆判 Rework**；v2 採納其結構性重排。審查全文：`docs/research/2026-06-20-{codex,gemini}-adr046-audit.md`。

---

## Addendum 2026-08-11 — 入庫說明 + 實作狀態實測

**為什麼現在才進 main：** 本文件 2026-06-20 寫完後停在 branch `docs/adr-046-three-source-kb-ingest`（commit `43cd1e2`，訊息註明「未拍板，parking」），從未開 PR。但 **Slice 0A / 0B 的實作已經先行進了 main**，且 code 與 `docs/VAULT-LAYOUT.md` 共 12 處以上 cite「ADR-046」——形成「code 引用一份不存在的 ADR」的懸空參照。本次入庫即為消除該懸空，**不代表拍板**：Status 維持 Proposed。

**下列狀態是 2026-08-11 對 main（`09bd260`）逐條 grep 實測的結果，非 ADR 撰寫時的預估：**

| Slice | 狀態 | 證據（main 上的實際位置） |
|---|---|---|
| 0A 統一 raw 存放 | ✅ 已上 | `shared/reading_source_registry.py:75`、`:495`；`shared/schemas/reading_source.py:42`、`:62`；`scripts/migrate_video_transcripts_to_raw.py`；`docs/VAULT-LAYOUT.md:121`、`:219`、`:220` |
| 0B 文獻筆記觸發統一 | ✅ 已上 | `thousand_sunny/routers/robin.py:679`、`:2200`、`:2272`；`scripts/backfill_literature_notes.py` |
| 1 CJK 錨點穩定 | ❌ 未做 | KB 路徑無錨點指紋／healing 實作（`fingerprint` 命中全在 `agents/brook/script_video/` 字幕對齊，與本 ADR 無關） |
| 2 source-qualified 每日回顧 + backfill | ⚠️ 一半 | **已做**：anchor 跨片碰撞修掉了——`_candidate_id(slug, anchors)`（`agents/robin/daily_review.py:469`）已是 `{slug, anchor}` 複合 key。<br>**未做**：backfill / review-since 仍缺，`daily_review.py:10` docstring 與 `:343` 的過濾都還是「只收昨日 `created_at`」，既有註記補不進來 |
| 3 Wiki 降級 + 砍 legacy merge | ⚠️ 一半 | **已做**：`ConceptPageV2.status: Literal["candidate","superseded"]`（`shared/schemas/kb.py:111`）。<br>**未做**：`promotion_renderer.py` 不 emit status／`#ai-draft`（grep 零命中）；legacy `kb_writer.upsert_concept_page`（`shared/kb_writer.py:689`）的 `update_merge` 路徑仍在，deprecation chore 沒跑 |
| 4 promotion `llm` mode | ❌ 未做 | `DryRunConceptMatcher` 仍是預設（`agents/robin/promotion/dry_run_entity_matcher.py`），`NAKAMA_PROMOTION_MODE` 預設 `dry_run` |
| 5 影片 concept | ❌ 未做 | — |
| 6 整本書／影片全文 ingest | ❌ 未做（本 ADR 本就不承諾，需另開 spike） | — |

**給後續接手者的三個提醒：**
1. **Slice 1 的優先序警告仍然成立且更急迫。** Gemini 判 CJK 錨點必須在候選生成前解決，理由是「錨壞 → 候選連結 born-broken → 修修拒收」。但現況是 Slice 2 的候選生成已先落地一半、Slice 1 一行沒動——**順序被顛倒了**。接手前先確認這個風險是否已由別的機制吸收。
2. **Slice 3 目前是半套，比沒做更危險。** schema 有 `status` 欄但 renderer 不寫，等於欄位永遠是 default——ADR-043 §決策 3 的「AI 產出必須降級標記」在實際輸出上**沒有生效**。
3. 本 addendum 只陳述實測到的 code 狀態，**不推斷當初是誰、基於什麼決定先做 0A/0B**——git 紀錄裡沒有這段脈絡。

---

## 0. v1 的錯（誠實記錄，避免重蹈）
1. **「daily_review 完全 source-agnostic、影片畫線零 code 自動變候選」= 過度宣稱。** 沒有 source 過濾沒錯（`daily_review.py:219-227` 只掃 Annotations、無 source_kind filter），但 Codex 抓到：候選只用 anchor 當 key（`daily_review.py:419-422`），影片 `t=123` 時間碼**跨片必撞**；且只收 `created_at==yesterday`（`daily_review.py:289-292`），**既有註記不 backfill 不會出現**。
2. **把「Wiki 降級」當已實作。** ADR-043 §決策 3 的 `status: candidate`/`#ai-draft` 在 code **還沒做**：`ConceptPageV2` 無 `status` 欄且 `extra="forbid"`（`shared/schemas/kb.py:104-117`），`promotion_renderer` 不 emit status（`promotion_renderer.py:226-241`）。Slice 4 前必須先補。
3. **Slice 5「整本書一次雲端 ingest」與現況架構衝突。** 書現況是 EPUB 逐章 source-map（`source_map_builder.py:312-327`）+ 逐章 `LlmClaimExtractor`（`source_map_extractor.py:96-134`），不是「整本丟一個雲端 call」。整本 1M-context 是**新設計**，非 reuse。
4. **行號 stale**：source_kind 推斷在 `literature_writer.py:614`（非 :563）；permanent 在 `kb_review.py:284`（非 :278）；書儲存在 `book_storage.py:102-116`（`books.py:286` 只是 upload route）；影片 annotate def 在 `robin.py:2025`（store.save 在 :2119）。

---

## 1. Context（macro 框架已三方查證為正確）
- ADR-045 §D-F（`ADR-045:110`）：「進 KB（Robin = Knowledge Base）：**讀物（route B/C/E）**」——三來源都該進 KB。
- ADR-043 §決策 5（`ADR-043:40`）：三條收斂在 Stage 3 脊椎；`AI 摘要 → AI 候選 →【紅線：人寫】→ 永久卡`；只在證據來源不同（B: N519 claim / C: LLM 摘要 / E: 畫線叢集）。
- ADR-043 §決策 7（`ADR-043:44`）：3 個月硬 gate；「未過 gate：不擴 B/E 收斂」。
- **查證判斷題 A（Agent 4）**：全 repo 文件**無一處**說「書/影片**不該**走 LLM 進 Wiki」——只說「分波 / gated / 未上線」。即「還沒做」，非「不做」。修修反駁成立。
- **查證判斷題 B**：無比 ADR-045 更新的決策推翻三來源規劃（2026-06-17 handoff/two-brain plan 皆未推翻）。
- **修修反駁（本 ADR 動機）**：時間 gate 拿「是否養成寫卡習慣」當通過條件，卻只接 1/3 來源（文章）的候選；書/影片產不出候選 → 餓死測試、結構性偏向失敗。**Codex §4 + Gemini §3 皆判此論點成立。**

## 1.5 現況資產（4-agent 逐條查證後修正版）
| 階段 | 文章 C | 影片 E | 書 B |
|---|---|---|---|
| ① 收集 | ✅ `Inbox/web` | ✅ `Watchlist/youtube/{id}/transcript.vtt`（`robin.py:1768`）| ✅ `book_storage.py:102`|
| ② 轉純文字 | ✅ md | ⚠️ `_parse_webvtt`（`robin.py:652`）只供 reader 顯示（`robin.py:1843`）| ✅ spine/章（`source_map_builder.py:312`）|
| ③ 註記 | ✅ `/robin/save-annotations`（`robin.py:806`）| ✅ `/robin/watchlist/{id}/annotation`（def `robin.py:2025`, save `:2119`）| ✅ `books.py:578`|
| ④ 文獻筆記 render | ⚠️ 只 ingest skill | ❌ renderer 在（`literature_writer.py:344`）、**無觸發** | ✅ 存即背景 render（`books.py:620`）|
| ⑤ 候選：永久卡 | ✅ daily_review（無 source filter，但見 §0.1 缺陷）| ⚠️ 同左、**有 anchor 碰撞 + backfill 缺口** | ⚠️ 同左 |
| ⑤ 候選：Wiki | ✅ N519 `LlmClaimExtractor`（`promotion_wiring.py:183`，gated `llm` mode）| ❌ 只 SourcePage+speaker（`video_source_map_builder.py:228`）、零 concept | ✅ N519（同文章，逐章）|
| ⑥ 人寫永久卡 | ✅ `/kb/api/permanent`（`kb_review.py:284`，source-agnostic）| ✅ | ✅ |

---

## 2. Decision（panel 重排：拆兩段，gate 改 capability 節流）

**2-A：現在解 gate（便宜、無/低 LLM）—— 證據累積 + 永久卡候選。**
三來源的「畫線 → 文獻筆記 → 每日回顧候選 → 人寫永久卡」立即全通。這是修修要的「累積」，且不碰昂貴 Wiki 富化。

**2-B：Wiki concept 富化維持 gated，但改「capability 節流」非「時間閘」。**
Slice（demotion 實作 / 真 matcher / create-target / 影片 concept / 整本 ingest）逐項需通過**能力前置 + 數值前導指標**才投（§5），非等 3 個月。

**2-C：紅線不動**（本 ADR 不鬆）：AI 永不寫 `KB/Permanent/` 正文/status/連結；Wiki Concepts 維持 candidate/`#ai-draft`（且本 ADR 要求**先把降級實作出來**，見 Slice 3）。

---

## 3. 目標架構（不變）：per-source adapter（①–⑤）→ 統一脊椎（⑥–⑧）。新來源 = 補 adapter，禁止為單一來源改脊椎。

---

## 4. 切片計畫（panel 重排：穩定地基 → CJK → 使用者價值 → gated 富化）

### Slice 0A — 統一 raw 存放（修技術債，對齊 canonical VAULT-LAYOUT）
- **問題（已對 `docs/VAULT-LAYOUT.md` 查證）**：canonical 文件早在 `KB/Raw/` 下保留 `Videos/`（§2「reserved」）給影片原始內容，與 `KB/Raw/Articles/`（文章，已用）並排；但影片 reader（ADR-035 後做）把逐字稿放進 `Watchlist/youtube/{id}/transcript.vtt`，**沒對齊保留位** = 三路上游分散的技術債（各自單獨開發、早於「統一 ingest」概念）。
- **做什麼**：影片逐字稿 `Watchlist/youtube/{id}/` → `KB/Raw/Videos/`（對齊文章 `Inbox/web` → `KB/Raw/Articles` 的 promote 模式）；`Watchlist/` 回歸「待看影片佇列」角色。文章維持現況（已對齊）。
- **書的例外（刻意、非技術債）**：EPUB 二進位**不進 vault**（VAULT-LAYOUT §11：vault 只放文字 + 小圖，Syncthing 同步預算 / 手機友善），維持 `data/books/`。三路真正匯流在更下游的 `KB/Annotations/`（畫線層），那層本就統一——**不強搬 EPUB 進 vault**。
- **硬前置（修修 2026-06-20 指示）**：**先做完整 blast-radius 程式碼盤點**（列出每個讀/寫/列舉 transcript 與 `Watchlist/youtube` 路徑的 code site + 當前行號）+ 既有逐字稿一次性遷移計畫 + 同 PR 更新 `VAULT-LAYOUT.md`（§6 α 規定：動 vault 路徑必須同步），**查證過才動，避免搬出 bug**。
- **驗收**：影片 reader 從新位置讀逐字稿正常；既有影片不失聯；watchlist 列表頁正常；VAULT-LAYOUT.md 同步。

### Slice 0B — 文獻筆記觸發統一（無 LLM）
- 文獻筆記觸發統一：影片 `robin.py:2119` annotate **與 delete（`robin.py:2185`）**、文章 `robin.py:806` 存/刪註記後 `write_literature_note(slug)`（auto-infer），對齊書 `books.py:620`。**delete 也要 re-render**（否則 stale，Codex §3）。回填 `youtube_Ch4Sl0POBhU`。
- **驗收**：三來源存/刪畫線即時反映 Literature Note；idempotent。

### Slice 1 — CJK 錨點穩定（Gemini 判為最高優先，必須在候選生成前）
- **問題**：中文無詞界，`^p`/char-offset 區塊錨改句即斷鏈（ADR-043 §決策依據）。書/影片的候選**唯一輸入是 annotation 錨點**，錨壞 → 候選連結 born-broken → 修修拒收 → 餵 gate 同樣的失敗訊號（Gemini §2，本 ADR 的存在意義被自我推翻）。
- **做什麼**：穩固 locator（影片用 `t=秒` float 本就穩；書/文章評估 char-offset + 文字指紋 reconciliation/healing）。先有健檢/修復，再生候選。
- **驗收**：改原文後既有錨點可偵測斷裂並修復，不靜默失聯。

### Slice 2 — source-qualified 每日回顧 + backfill（修 §0.1 缺陷，落地「畫線叢集→候選」）
- **做什麼**：候選 key 由 anchor-only 改 `{slug, anchor}`（修跨片 `t=` 碰撞，Codex §3）；item 帶 `source_kind`；支援 **review-since / backfill**（既有影片/書註記能補進候選，非只 yesterday）；**明確定義「畫線叢集」演算法**（影片=時間碼鄰近、書=章節/段、文章=段落）——Gemini §2 指此演算法 v1 未定義、CJK 下非 trivial。
- **驗收**：既有 `youtube_Ch4Sl0POBhU` 畫線經 backfill 長出**不撞、連結正確**的候選卡於 `/kb/review`。

### Slice 3（gated, capability）— 先補 ADR-043 Wiki 降級 + 砍 legacy 自動 merge
- **前置（Codex §2 / Gemini §2）**：`ConceptPageV2` 加 `status` 欄、`promotion_renderer` emit `status: candidate` + `#ai-draft`、`kb_search` 加 `scope` tier（ADR-043 §決策 3/4 的**未實作部分**）。
- **deprecation chore（Gemini §5）**：現存 route-C `kb_writer.upsert_concept_page` 仍 LLM diff-merge 成 canonical（`kb_writer.py:785`、`ingest.py:543`），**違背降級**；本 slice 一併改成寫 candidate 或退役，不留 hybrid 不一致路徑。

### Slice 4（gated, capability）— promotion `llm` mode（B/C 先）
- **前置（Codex §3）**：`create_global_concept` 的 ConceptReviewItem `canonical_match=None` → target resolution 回 None → gate fail（`promotion_targets.py:74`、`promotion_acceptance_gate.py:120`）必先修；補真 ConceptMatcher（現 `DryRunConceptMatcher`）；跨語 alias（卡片盒↔Zettelkasten）去重（Gemini §2）。
- 然後 `NAKAMA_PROMOTION_MODE=llm`，書/文章 → 候選 Wiki Concept/Entity（HITL）。

### Slice 5（gated）— 影片 concept（ADR-035 Phase 3）
- 影片 concept 聚合排在 SourcePage + speaker Entity **實際用穩之後**（ADR-035 §159 friction-conditional，非無條件）。

### Slice 6（移出本 ADR → 另開 spike/ADR）— 整本書 / 影片全文 LLM ingest
- 整本 1M-context ingest 是新設計（非 reuse 逐章架構，Codex §5）；需 spike 評估**成本上限 / 版權 / context-window / fallback**（Gemini §5：per-book 成本天花板 + 月預算 + 超額需人工確認）。本 ADR 不承諾。

---

## 5. Success Metrics（取代時間 gate）= capability 前置 + 數值投資節流
非時間閘、非被動 dashboard（Codex §4 / Gemini §3 皆批「cooldown 只是軟 gate」）。改**前導數值門檻**：
- **解 2-A 即測**：候選/週、永久卡/週、**候選→永久卡轉換率**、stale-candidate 天數、修修每日 review 耗時。
- **投資節流範例**：「Slice 2 的候選→永久卡轉換率連續 2 週 > 10% 才啟 Slice 3/4」（Gemini §3）；「LLM 月成本超 $X 暫停富化」。
- **降溫**：若候選暴量但卡零增長 → 暫停富化、回頭修候選品質/review UI（**非砍已建能力**）。

---

## 6. 風險（含 panel 補的盲點）
| 風險 | 緩解 |
|---|---|
| **CJK 錨點 rot（最高，Gemini）** | Slice 1 先解，候選生成前 |
| **HITL review 疲勞（Gemini §1/§5）** | 三來源候選暴增前先做品質門檻（confidence）+ review UI 效率 + 每日 review 時間預算；轉換率當核心健康指標 |
| **來源可變性 / link rot（Gemini §5）** | source 改/刪會靜默廢掉 annotation+卡連結；需 content-hash 偵測 + flag 受影響卡（idempotent re-render 不夠）|
| **架構債：legacy 非合規路徑（Gemini/Codex）** | Slice 3 含 deprecation chore，不留 hybrid |
| **Token/成本（整本書，Codex §5）** | 移 Slice 6 spike；嚴格成本上限 |
| **anchor 跨片碰撞（Codex §3）** | Slice 2 改 `{slug,anchor}` key |

## 7. 不在範圍
route O（ADR-045 §D-E 擱置）；完整 Bridge authoring UI（維持 Obsidian-first）；kb-lint/kb-query/gather（ADR-045 §D-D Wave 2/3）；整本書/影片全文 ingest（→ Slice 6 spike）。
