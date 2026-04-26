# Ingest v2 Redesign — Decision Questionnaire

**用法**：每題勾選一個選項（用 `[x]` 取代 `[ ]`），nuance 寫 Comments / overrides 區。

**對應提案**：[docs/plans/2026-04-26-ingest-v2-redesign-plan.md](2026-04-26-ingest-v2-redesign-plan.md)
**背景記憶**：
- [project_textbook_ingest_v2_design.md](../../memory/claude/project_textbook_ingest_v2_design.md)
- [project_robin_aggregator_gap.md](../../memory/claude/project_robin_aggregator_gap.md)
- [feedback_kb_concept_aggregator_principle.md](../../memory/claude/feedback_kb_concept_aggregator_principle.md)

**Step 1（Hygiene PR）已開**：fix/config-and-broken-pages — 不依賴本 questionnaire。

---

## Q1 — Step 1 / Step 2 / Step 3 sequencing：序貫還是並行？

**Tradeoff**：

- **序貫**（Step 1 → 2 → 3）：Step 1 修完並 merge 後，桌機 IDE 跑 ingest 才能正確讀 vault path（A-5），ADR 草稿才能引用「已修」的現狀；穩但慢。
- **並行**（Step 1 + Step 2 同時推 / Step 3 等 ADR accept）：Step 1 是純 hygiene、Step 2 是純文件，兩者完全沒互依；ADR 草稿不引用 Step 1 PR 也不影響準確性；快一輪 turn。
- **Step 3 開工依賴 ADR**（不論 1/2 並行與否，Step 3 應等 ADR-011 accept）。

**選一個**：

- [x] **A — Step 1 + Step 2 並行**（建議；Step 3 等 ADR accept）
- [ ] **B — 嚴格序貫 Step 1 → 2 → 3**
- [ ] **C — 三步全並行**（Step 3 一邊 implement 一邊改 ADR；不建議：implement 跑半路若 ADR 改設計會 rework）

**Comments / overrides**：

> _（修修自由補充）_

---

## Q2 — 既有 ch1 已 update 的 6 個 concept page 怎麼處理？

背景：2026-04-25 ch1 ingest 用 v1 path 在 6 個既有 concept body 末尾 append `## 更新（2026-04-25）` block，內容是新教科書資訊。這違反 P1（aggregator 哲學）但內容本身有用。

6 頁：磷酸肌酸系統 / ATP再合成 / 肌酸激酶系統 / 磷酸肌酸能量穿梭 / 肌酸代謝 / 運動營養學

**Tradeoff**：

- **A 維持 v1 schema 直到全本 v2 backfill**（建議）：v2 implementation 完成 + 重 ingest ch1 時，一次性 LLM diff-merge 所有 ch1 update sections 進主體（同邏輯適用未來所有 source），最有效率；但這 6 頁短期內仍然 ugly。
- **B 立刻手動把 ch1 update merge into main body**：6 頁手寫合併（每頁 ~10-30 分鐘），中間狀態乾淨；但 v2 implementation 後重 ingest ch1 又會再 update 一次，等於白做。
- **C 立刻刪除 ch1 update sections**：6 頁直接 `## 更新（2026-04-25）` 整段砍掉，等 v2 重 ingest；最乾淨但 lose ingest cost（~半小時 Opus 4.7）。

**選一個**：

- [x] **A — 維持，等 v2 backfill 一次解決**（建議）
- [ ] **B — 立刻手動合併**
- [ ] **C — 立刻刪 update sections，等 v2 重 ingest**

**Comments / overrides**：

> _（修修自由補充）_

---

## Q3 — ADR-010-v2 新增 ADR-011 還是替換 ADR-010？

**Tradeoff**：

- **A 新增 ADR-011-textbook-ingest-v2 + ADR-010 標 superseded**（建議）：保留歷史脈絡（v1 為什麼這樣設計、為什麼後來不夠用）；對未來的 reader 友善；但兩個 ADR 並存增加查找成本。
- **B 直接 in-place 改寫 ADR-010**：單一 source-of-truth；但 git history 才看得到 v1 → v2 演進。
- **C ADR-011 但不標 ADR-010 superseded**：兩 ADR 都「Accepted」狀態並存；混亂，不建議。

ADR convention：repo 既有 ADR 全部走「新增 + 舊版標 superseded」（如 ADR-001 → ADR-006 演進）。

**選一個**：

- [x] **A — 新 ADR-011，ADR-010 標 superseded**（建議；對齊 repo convention）
- [ ] **B — 直接改寫 ADR-010**
- [ ] **C — 其他**

**Comments / overrides**：

> _（修修自由補充）_

---

## Q4 — Vision LLM 用 Opus 4.7 還是 Sonnet 4.6？

背景：每章圖表 export 後 call Vision LLM 寫 domain-aware describe 段。一本中型教科書 ~50-150 圖。

**Tradeoff**：

- **A Sonnet 4.6**（建議）：成本約 Opus 1/5；對教科書 figure（biochem pathway / muscle anatomy / exercise physiology chart）domain-aware prompt 描述夠用；ingest 全程 Opus + Vision step Sonnet 不影響核心 deep extract（chapter source / concept extract 仍 Opus）。
- **B Opus 4.7**：品質高 5%-10%（推測，沒實測），但成本 5x；對複雜醫學示意圖（如 mitochondria cross-section）可能值得；違反「核心 deep extract 用最強，輔助 step 可降」直覺。
- **C 先 Sonnet，發現品質不夠再升 Opus**：實證主義；建議併入 A 採行。

**選一個**：

- [x] **A — Sonnet 4.6 預設**（建議；domain-aware prompt 補品質）
- [ ] **B — Opus 4.7 全程**
- [ ] **C — Sonnet 預設、發現問題升 Opus**（A 的軟版）

**Comments / overrides**：

> _（修修自由補充）_

---

## 補充自由提問區

> _（修修自由 input — 想到我沒列到的題）_

---

## 拍板後我做什麼

1. 把本 questionnaire 答案寫進 `docs/plans/2026-04-26-ingest-v2-redesign-plan.md` §8 → 標 Decided
2. 開始寫 ADR-011-textbook-ingest-v2（依 Q3 結果決定 location）
3. ADR-011 開 PR review → accept
4. Step 3 implementation 排序：A-1 (`shared/kb_writer.py` + `_update_wiki_page` 重寫) → A-2 (`extract_concepts` 注入既有 body) → A-3 (`parse_book.py` 圖片 export + Vision describe，依 Q4 決定 model)
5. 重 ingest ch1（依 Q2 決定 scope：A 全 backfill / B 跳過 / C 重做）
6. 批 ingest 剩 10 章
