# Pilot Run 001 — Centaur route C 文章 ingest 端到端（N524）

> **這是 fixture 跑出來的紀錄，不是真 vault。** 用一篇合成文章 + 合成 V3 annotation
> set 跑完整條 route C ingest 鏈，LLM 全程 mock（Source digest / concept plan 用
> deterministic stub），驗證 N524 接線把每個 artifact 都產出、紅線 5 正確攔截。
> 真 vault 的 pilot 由修修在 N52x 收尾時手動跑。
>
> 日期：2026-06-11 · 對應 task prompt `docs/task-prompts/N524-centaur-route-c-ingest.md` §5

---

## 1. Fixture

- **原文**：`KB/Raw/Articles/cbti-and-sleep-pressure.md`
  （frontmatter `title: CBT-I and Sleep Pressure`、`author: Colleen Carney`）。
- **Reader 劃線**（合成 V3 annotation set，`KB/Annotations/cbti-and-sleep-pressure.md`）：
  - highlight：「睡眠壓力由腺苷累積驅動」
  - annotation：「CBT-I 比安眠藥更持久」+ note「這點要記起來——藥物只治標」
    （強評價訊號，P-1 候選種子）
- **LLM mock**：
  - summary call → 固定綜整字串（帶 `^p-1` 錨）
  - concept-plan call（system 帶「回傳純 JSON」）→ 一個 clean concept
    `sleep-pressure`，`## Sources` cite `[[Sources/CBT-I-and-Sleep-Pressure]]`
    （終端證據指向 Source 頁，紅線 5 合規）+ 一個 entity `Colleen Carney`。

入口：`IngestPipeline().ingest(raw, source_type="article", annotation_slug="cbti-and-sleep-pressure")`。

---

## 2. 產出（全部命中）

```
KB/Annotations/cbti-and-sleep-pressure.md     (Reader fixture，未被 ingest 改寫)
KB/Literature/cbti-and-sleep-pressure.md       ← Phase 1 render（N521 writer）
KB/Wiki/Sources/CBT-I-and-Sleep-Pressure.md    ← Phase 2 P-3 Source digest
KB/Wiki/Concepts/sleep-pressure.md             ← Phase 2 P-5 Concept（過紅線 5 lint）
KB/Wiki/Entities/Colleen-Carney.md             ← Phase 2 P-4 Entity
KB/index.md                                     ← index 寫入鏈
```

驗收對照（task prompt §5）：

| 驗收項 | 結果 |
|---|---|
| `KB/Literature/` 產出 | ✅ `type: literature`、`anchor_type: excerpt`、`status: digested`、保留記帳區 marker |
| `Wiki/Sources/` 產出 | ✅ `author: agent_robin` + `original_author: Colleen Carney`（P-3 §5 provenance 分離，紅線 3） |
| `Wiki/Concepts/`（或 merge 既有） | ✅ `sleep-pressure.md` 建立（既有則走 update_merge diff-merge） |
| index/log 全產出 | ✅ `KB/index.md` 加 `- [[CBT-I-and-Sleep-Pressure]] — article：…`；`kb_log` 記 Source/Literature/concept-create |
| 每個事實宣稱有錨點 | ✅ concept body `^p-1`、Literature 劃線帶 `^p-N`（N521 deterministic 編號） |

Literature frontmatter（實跑擷取）：

```yaml
type: literature
source_kind: article
slug: cbti-and-sleep-pressure
annotations: "[[Annotations/cbti-and-sleep-pressure]]"
mined_concepts: []
status: digested
anchor_type: excerpt
captured: 2026-06-11
ingested: 2026-06-11
schema_version: 3
```

---

## 3. 紅線 5 對抗測試（本任務最重要的正確性點）

citation lint 不只在 pilot happy-path 跑過，另有對抗性 fixture（測試碼）：

- **違規 fixture**：concept `## Sources` cite `[[Concepts/another-concept]]`
  → `ProvenanceViolation`，concept 頁**不寫入**
  （`test_ingest_route_c.py::test_route_c_rejects_concept_self_feeding`、
  `test_kb_writer.py::TestRedLine5Enforcement`）。
- **合規 fixture**：cite `[[Sources/…]]` / `[[Raw/…]]` / `[[Annotations/…]]` → pass
  （`test_provenance_linter.py::test_concept_citing_sources_raw_annotations_passes`）。
- **不誤殺**：`## Related Concepts` 內 concept↔concept 連結是「概念關係」非「終端證據」，
  不觸紅線 5（`test_related_concepts_section_is_not_evidence`）；裸 stem `[[foo]]`
  無法判層級 → unknown，不判違規（`test_bare_stem_wikilink_is_unknown_not_violation`）。

enforcement 落點：`shared/kb_writer.py:_enforce_concept_provenance`（concept 三個寫入點
create / update_merge / update_conflict 在 `_write_page_file` 前各過一道）+
`shared/output_writer.py:write_output_page`（Output 同道）。底層是
`shared/provenance_linter.py:ProvenanceLinter.lint_page`（N520 deferred stub → N524 真檢查）。

---

## 4. 隔日每日回顧（N522 銜接）

task prompt §5 第三條「隔日 N522 job 把該文章的候選帶進每日回顧」**未在本 pilot
跑驗證**（誠實標記）：N522 每日回顧 job 讀 `KB/Annotations/` 昨日 delta + index，
route C ingest 並不改 annotation set（Reader 擁有，N524 零改動），所以該文章的劃線
本來就在 N522 的掃描範圍內——銜接是「資料已就位」而非「N524 主動推送」。實際 N522
job 對此 annotation set 的候選輸出，待真 vault pilot 或 N522 自身的 job 測試覆蓋。

---

## 5. 不紮實處（誠實）

- LLM 全 mock：Source digest / concept plan 的**真實輸出品質**（覆蓋率、錨點密度、
  diff-merge 矛盾標記）未在本 pilot 驗證，只驗接線與 schema。
- P-3/P-4/P-5 的 prompt 模板：本任務把 Prompt 規格 §1 共同前置掛上
  （`CENTAUR_SYSTEM_PREFIX`），但 concept/entity 抽取仍走既有 `robin` prompt 檔
  （`extract_concepts` / `write_entity`），未逐字改寫成 P-3/P-4/P-5 全文模板——
  既有 prompt 已覆蓋同樣的職責（dedup / 4-action / entity upsert），重寫風險大於收益；
  紅線 5 的硬 enforcement 在寫入層而非 prompt 措辭，故功能正確性不依賴 prompt 改寫。
- `KB/Wiki/Outputs/` 儲存層（`output_writer.py`）已鋪 + 測，但 query workflow
  （P-8 回答 → P-9 蒸餾 → 問修修「值得存嗎？」）本任務不做（task prompt §2.4 邊界）。
