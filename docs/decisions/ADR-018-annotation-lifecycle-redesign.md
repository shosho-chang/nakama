# ADR-018: Annotation lifecycle redesign

**Status:** Proposed — pending 2026-05-04 grill final review
**Supersedes:** [ADR-017](./ADR-017-annotation-kb-integration.md)
**Date:** 2026-05-04
**Deciders:** shosho-chang
**Grill session:** docs/plans/2026-05-04-adr-017-regrill-grill-log.md (待寫，本 ADR §Grill 段落含核心 trace)

---

## Context

ADR-017（2026-05-04 ship）凍結了「annotation 跟 source 解耦」的決定，PR #342/#343/#344 三個 slice 已 merged 到 main。同日（2026-05-04 夜）QA 入口 surface 兩層問題：

### 表面問題（P0 hotfix scope，PR #368 已修）
sync LLM 走 raw-text JSON 不穩，systematic 2/3 失敗於 `json.loads`。
**根因**：reasoning model（Opus 4.7）對 raw-text → must-be-valid-JSON contract 不穩。
**修法**：改 Anthropic tool_use forced JSON。獨立工程修復，跟本 ADR 設計取捨無關。

### 深層問題（本 ADR 處理）
ADR-017 frame 不精確 + 伴隨 UX 設計失誤：

1. **ADR-017 §Context §1「lifecycle coupling」** — grill 確認修修 1 年 0-1 次重 ingest 同篇 source、且 annotation 走丟可接受 → 此理由 over-weighted
2. **ADR-017 §Context §2「source corruption」** — 跟 `agents/robin/prompts/summarize.md:27-36` 直接矛盾（ingest pipeline LLM prompt 主動利用 annotation 作 summary 改善 feature）。Grill 釐清此 §Context §2 真意應是「**persistent `KB/Wiki/Sources/{slug}.md` 不該帶 annotation 當持久存儲**」（source page = cross-source 公正 reference 角色），但原 ADR 寫成「ingest pipeline 處理不來」 — 兩個 framing 結論一樣（不嵌 source page）但理由不同
3. **「同步到 KB」獨立按鈕的認知負擔** — user 按 source ingest 後不知 annotation 進沒進 KB；按「同步到 KB」按鈕不知做什麼。兩個觸發點分離造成 mental model 斷層

### Grill 結論（2026-05-04）

修修真實 mental model 三條原則：
- annotation **必須持久化**（之後寫文章 reference 用）
- source page **不嵌 annotation**（cross-source 公正 reference 角色）
- 「進 KB」應該**一個操作完成**，不該分多個按鈕

修修 reference annotation 的 frequency：
- **主要**：寫單一 concept 文章 → 翻 concept page `## 個人觀點`
- **主要**：寫 cross-source 主題 → 翻 `KB/Annotations/{slug}.md` 個別檔
- 偶爾：quote 具體某條 → 全域 search（待設計，獨立 issue）

---

## Decision

### 保留（ADR-017 真確的部分）

1. **annotation 拆獨立檔** — `KB/Annotations/{slug}.md`（ADR-017 §Decision 對的）
2. **annotation push to concept page `## 個人觀點`** push 機制 — per-source HTML comment boundary 隔離（PR #343 對的）
3. **source page 不嵌 annotation** — 保留 ADR-017 真意，但理由 reframe 為「source page = cross-source 公正 reference」

### 改變（ADR-017 設計缺陷）

4. **砍除「同步到 KB」reader header 按鈕** — 認知負擔過重、跟 ingest 觸發點分離不合修修 mental model
5. **改自動觸發** — annotation push to concept page 由 lifecycle event 自動觸發、無 button：
   - **(X)** source ingest 結束時 auto-push 已存全部 annotation
   - **(Y)** annotation save 後 debounce auto-push（若 source 已 ingest 過）
6. **Reader header 新增「Ingest 進 KB」按鈕** — 跟 inbox 既有 `/start` flow 共用後端，兩個入口並行存在（reader 直接觸發 / inbox 不開 reader 直接觸發 兩個場景都 cover）

### 新框架的詞彙

- **「ingest」嚴格意義**：只指 Robin source pipeline（Inbox → `KB/Wiki/Sources/{slug}.md` + 抽 concept），**不**含 annotation 進 KB。Annotation 進 KB 走 (X)+(Y) lifecycle event auto-push，不歸 ingest 詞彙
- **「annotation push to concept」** 取代「同步到 KB」 — 後者按鈕廢除後不再使用

---

## Consequences

### 已 ship code 的 disposition

| Asset | 狀態 |
|---|---|
| PR #342（annotation store 解耦） | 保留，跟新設計相容 |
| PR #343（push to concept page 機制） | 保留，後端 logic 對的 |
| PR #344（「同步到 KB」按鈕 + `/sync-annotations/{slug}` endpoint） | **後端保留** + **前端按鈕砍除**；endpoint 改為僅 internal 觸發，不暴露 user button |
| PR #368（P0 hotfix tool_use forced JSON） | **unhold + merge** — 修底層 LLM contract，跟 button 廢除無關 |
| `KB/Annotations/{slug}.md` 已存資料 | 保留，schema 無變 |
| concept page 已 push 過的 `## 個人觀點` block | 保留，per-source boundary marker 還在 |

### 新增 work（follow-up plan）

1. **Reader header「Ingest 進 KB」按鈕** — 跟 inbox `/start` 共用後端
2. **(X) 自動觸發** — Robin ingest pipeline 結束時 trigger annotation push（呼叫既有 `ConceptPageAnnotationMerger.sync_source_to_concepts`）
3. **(Y) debounce 自動觸發** — Reader annotation save 後若 source 已 ingest，debounce 30s + blur tab 觸發 push
4. **砍除 reader header「同步到 KB」按鈕** — 移除 `syncBtn` + `syncToKB()` 函式 + 相關 CSS

### Migration

無需 — 既有 vault 三個 sync 過的 concept page（creatine / cardio / ACSM）資料保留，新觸發機制是 additive。

---

## Considered alternatives（為何不選）

| 選項 | 拒絕理由 |
|---|---|
| **(a) annotation 嵌入 source page** | 違反「source = 公正 cross-source reference」原則（修修 grill ACK） |
| **保持 ADR-017 + 「同步到 KB」按鈕** | 認知負擔過重、修修 mental model 斷層（grill 起點 = 修修 surface frustration） |
| **只做 (X) 不做 (Y)** | source 已 ingest 過、後續補標 annotation 場景無自動 path → 又一個 manual button |
| **只做 (Y) 不做 (X)** | 第一次 ingest 完成的瞬間 push 沒 trigger，要等下次 annotation save → UX 詭異 |
| **「同步到 KB」按鈕保留但改名** | 換湯不換藥，不解 mental model 斷層 |

---

## Grill log（核心 trace）

完整 grill 過程：see CONTEXT.md update 與本檔 §Context；簡述：

- **Q1 凍結「ingest」嚴格意義** — 修修選 B（嚴格意義 = Robin source pipeline）
- **Q2 §Context §1 lifecycle coupling 垮** — 0-1 次/年 + annotation 走丟可接受
- **Q3 §Context §2 reframe** — 修修選 B framing（真意是「source = cross-source 公正 reference」，不是「ingest 處理不來」）
- **Q4 reference scenario 凍結** — 主要 (i)+(ii)，(iii) 偶爾
- **Q5 觸發點 = (X)+(Y) 並行**，reader header 加 ingest button

---

## Related

- **CONTEXT.md**: `agents/robin/CONTEXT.md`（lazy 建於本次 grill，凍結本 ADR 涉及 term）
- **Memory feedback**: `memory/claude/feedback_grill_before_planning.md`（規劃前必走 Grill me；ADR-017 是反例）
- **Concept aggregator principle**: `memory/claude/feedback_kb_concept_aggregator_principle.md`（concept page = cross-source aggregator，跟本 ADR `## 個人觀點` push 機制相容 — 「個人觀點」是補充不是 dump-style ## 更新）
