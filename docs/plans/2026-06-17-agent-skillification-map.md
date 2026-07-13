# Agent Skill 化盤點 + Taste Loop 設計

**Status**: 📋 PROPOSAL 2026-06-17 — 盤點 + 設計，未動程式。等修修拍板後再決定落地順序。
**緣起**: 修修讀完〈萬字長文：做了些爆款 Skills 以後，我對 Skills 的看法〉+ Anthropic 官方 Skill authoring best practices（對照筆記在 vault `Inbox/web/Skill 製作最佳實踐 — 官方文件 vs 萬字長文 對照.md`），想用那套方法盤點 Nakama 八個 agent「有哪些事可以做成 skill」，且要求**每次執行都能增進 skill、讓 agent 越來越貼近修修的喜好與品味**。
**相關**: [CONTENT-PIPELINE.md](../../CONTENT-PIPELINE.md) · [ARCHITECTURE.md](../../ARCHITECTURE.md) · [ADR-001](../decisions/ADR-001-agent-role-assignments.md) · [memory/SCHEMA.md](../../memory/SCHEMA.md)

---

## 0. 一句話結論

Nakama 不是從零開始——它**已經**有三套 taste/learning 基礎設施（`memory/claude/feedback_*.md` 184 條 gotchas、Foundry `edit_log → examples` few-shot 迴路、Brook style-profile 約束＋真實樣本），而且現有 skill 寫法**已經符合**官方 best practice（thin wrapper + canonical Python + description 帶觸發詞與 Do-NOT 邊界）。所以這份盤點的重點不是「再多做幾個 skill」，而是：**(A) 補上品味濃度高、但還沒被 skill 化的工作；(B) 把 Foundry 那條「執行 → 驗收 → 回灌 few-shot」的迴路，標準化成每個品味型 skill 都用的 taste loop。**

---

## 1. 評分方法（從對照筆記濃縮）

判斷「一件事該不該做成 skill、以及優先序」，用五個維度（前三條來自文章與官方的交集，後兩條決定能不能「越用越貼近品味」）：

1. **重複性** — 是不是一組穩定、會反覆跑的流程（不是一次性對話）。官方：Skill 承載可複用工作流。
2. **品味濃度** — 有沒有「只有修修能驗收」的主觀判斷被外化（審美、語氣、選題、節奏）。文章核心：Skill 的價值是把人的經驗/品味外化成約束。
3. **確定性下沉** — 有沒有可以丟給 Python/CLI 的確定性邏輯（讓模型只留判斷）。官方：Thin Harness, Fat Skills + solve-don't-punt。
4. **有 verdict 訊號** — 每次執行後是否有一個清楚的「修修驗收動作」（accept / edit / reject + 為什麼）。**這是「越用越貼近品味」的燃料**；沒有 verdict 的工作學不起來。
5. **gotchas 可累積** — 失敗是否多半是負面邊界（「不要這樣」），而非模型本來就會的正向原則。文章：gotchas 是最高價值內容。

> 每個 skill 都是一種「稅」（name+description 對每個 session 收上下文成本）。候選清單寧可少而精，先做交集分數最高的，不要一次鋪一堆。

---

## 2. 現況盤點：已經有什麼

### 2.1 現有 skills（`.claude/skills/` + `.agents/skills/`）

| Skill                                                                                                                                                                   | 對應 agent / 領域      | 性質                                       |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------ | ---------------------------------------- |
| `kb-search`                                                                                                                                                             | Robin              | 領域：KB 檢索（帶 `scripts/search.py`）          |
| `textbook-ingest`                                                                                                                                                       | Robin              | 領域：教科書攝入                                 |
| `keyword-research`                                                                                                                                                      | Zoro               | 領域：關鍵字研究                                 |
| `seo-audit-post`                                                                                                                                                        | Brook              | 領域：單篇 SEO 體檢（wrap `scripts/audit.py`）    |
| `seo-keyword-enrich`                                                                                                                                                    | Brook              | 領域：GSC 資料 enrich                         |
| `transcribe`                                                                                                                                                            | Foundry / Line 1   | 領域：字幕產出                                  |
| `foundry-replan`                                                                                                                                                        | Foundry            | 領域：單 beat 重 plan（**已內建 taste 迴路**，見 2.2） |
| `to-issues` / `to-prd` / `github-triage` / `diagnose` / `tdd` / `improve-codebase-architecture` / `zoom-out` / `grill-with-docs` / `domain-model` / `project-bootstrap` | 開發團隊（Claude/Codex） | 工程流程，非內容生產                               |

**觀察**：現有 skill 的寫法已經很對——`seo-audit-post/SKILL.md` 的 description 同時寫了觸發詞（「SEO audit <url>」「幫這篇做 SEO 體檢」）和 Do-NOT 邊界（「不要 trigger keyword research」）；body 明確「You do NOT re-implement the pipeline. You shell out to `scripts/audit.py`」。這就是官方要的 thin wrapper。**內容生產線的覆蓋卻很薄**：Nami / Sanji 完全沒有；Zoro 只有 keyword；Robin 與 Brook 最品味濃的步驟（source summary、concept 抽取、語氣重組）還沒被 skill 化。

### 2.2 已經有的三套 taste / learning 機制（這是設計 taste loop 的地基）

1. **gotchas 層 — `memory/claude/feedback_*.md`（184 條）+ `MEMORY.md` 索引**
   這就是文章說的「把失敗追加到 gotchas」，已經是一個 append-only、有 confidence 欄位的記憶庫（schema 見 `memory/SCHEMA.md`：`type: feedback`、`confidence: high|medium|draft`）。裡面已有大量品味型條目，例如 `feedback_no_chinese_creator_references.md`（thumbnail/title 不引用中文 creator）、`feedback_expert_persona_over_style_mimic.md`、`feedback_aesthetic_first_class.md`。**這是跨 skill 的行為層 gotchas。**

2. **few-shot 層 — Foundry `edit_log → examples`**
   `agents/foundry/edit_log.py`：只有 `replan`（帶 user_note）才寫入，註解原文「re-plan with note is the highest-quality taste signal; logging is non-negotiable」，且「doubles as raw material for the promote-to-example UI (PR-5) which curates high-signal entries into `agents/foundry/examples/` for future few-shot」。**這是「每次執行 → 擷取品味訊號 → 回灌 few-shot」一條完整迴路的雛形，目前只在 Foundry 有。**

3. **風格約束層 — Brook style profiles**
   `config/style-profiles/<cat>.yaml`（硬約束：字數上下限、`forbid_emoji`、tag hints、detect_keywords）＋ `agents/brook/style-profiles/<cat>.md`（15–25KB 完整風格指引）＋ `agents/brook/profile_samples/`（修修真實文章樣本）。`style_profile_loader.load_style_profile()` 在 compose 時注入 system prompt。**這就是文章說的「把品味變成約束」＋官方的 examples pattern，已經落地。**

4. **HITL gate = 品味驗收**
   `feedback_hitl_gate_serves_subjective_taste.md`：HITL gate 同時解「A 客觀正確」+「B 主觀品味」；LLM 變強只能吸收 A，**B 永遠在，因為 KB 是修修個人資產、品味只有他自己驗收**。這條決定了 taste loop 的 verdict **不能自動化掉**。

---

## 3. 一條紅線（設計前必須先框住）

**Stage 4 Line 2 心得是修修的聲音，LLM 不可代寫**（CONTENT-PIPELINE.md：「不得產生完成句、段落或第一人稱正文」）。
→ 所以品味型 skill 的定位是**放大修修的品味**（assist / 重組 / 體檢 / 選題 / 排版），**不是替他生成原創觀點正文**。

另一個微妙處：`feedback_expert_persona_over_style_mimic.md` 指出——**寫作審查**要走 expert persona，不要去 mimic 修修過去文章（style mimic 會「鎖住 mediocrity、壓抑進化」）。但 Brook 的 **channel 重組**用 style profile 維持語氣一致是 OK 的。
→ taste loop 必須區分兩種用途：**「語氣一致性」（channel 重組）可以學樣本；「品質判斷 / 選題 / 批評」要走專家視角、不學樣本。** 同一條學習訊號餵錯地方會適得其反。

其餘既有約束照舊：vault 寫入權限（`Journals/` 禁寫、`KB/Raw/` 只補 frontmatter）、美學 first-class（出手前讀 `docs/design-system.md`）、三條紅線（閉環 / 事實驅動 / 窮盡一切）。

---

## 4. Skill 候選盤點（依 pipeline stage + agent）

評分 1–5；「已是 skill?」標 ✅ 表示現成、🟡 表示部分、❌ 表示無。優先序 = 五維交集高且還沒做。

| #   | 候選 skill                                              | Agent / Stage    | 重複  | 品味  | 確定性下沉 | verdict 訊號                               | gotchas | 已是 skill?                      | 優先                                                        |
| --- | ----------------------------------------------------- | ---------------- | --- | --- | ----- | ---------------------------------------- | ------- | ------------------------------ | --------------------------------------------------------- |
| 1   | **channel 語氣重組**（長文→FB/IG/Newsletter，套 style-profile） | Brook / S5       | 5   | 5   | 4     | 5（每篇都驗收改字）                               | 5       | 🟡 pipeline 有、未 skill 化         | **P0**                                                    |
| 2   | **標題發想**（zh-Hant Western-aesthetic、避中文 creator 參考）    | Zoro / S1·S5     | 5   | 5   | 2     | 5（選哪個 / veto）                            | 5       | ❌                              | **P0**                                                    |
| 3   | **source summary + concept/entity 抽取**                | Robin / S3       | 5   | 4   | 4     | 4（promotion review accept/defer/exclude） | 4       | 🟡 在 ingest pipeline、未獨立 skill  | **P0**                                                    |
| 4   | **thumbnail / 封面美學發想**                                | Zoro?/Brook / S5 | 4   | 5   | 3     | 5                                        | 5       | ❌                              | P1                                                        |
| 5   | **PubMed / 文獻 digest 策選**（哪些值得 surface、怎麼下標）          | Robin / S1       | 5   | 4   | 3     | 4                                        | 3       | 🟡 `pubmed_digest` mode、未 skill | P1                                                        |
| 6   | **broll storyboard planning**（節奏 / layout 品味）         | Foundry / S5     | 5   | 5   | 4     | 5（replan note）                           | 5       | 🟡 `foundry-replan` 單 beat 有    | P1                                                        |
| 7   | **Morning Brief 彙整**（什麼重要、怎麼排版）                       | Nami / S7        | 5   | 3   | 4     | 3                                        | 3       | ❌                              | P1                                                        |
| 8   | **Franky 新聞 synthesis / retrospective**（情報取捨品味）       | Franky / S1      | 5   | 3   | 4     | 3                                        | 3       | ❌（有 `news_synthesis.py`）       | P2                                                        |
| 9   | **邀約報價草稿**                                            | Nami             | 3   | 3   | 4     | 4                                        | 3       | ❌                              | P2                                                        |
| 10  | **社群監控 digest / 活動策劃**                                | Sanji / S6       | 4   | 3   | 3     | 3                                        | 3       | ❌                              | P2                                                        |
| 11  | **daily review 開卡 / Reading Context Package**         | Robin / S2·S4    | 5   | 4   | 3     | 4                                        | 3       | 🟡 `daily_review.py`            | P2                                                        |
| —   | WordPress 發布                                          | Usopp / S6       | 5   | 1   | 5     | 1                                        | 2       | （Python daemon 即可）             | **不做 skill**（純確定性、無品味、crash-safe state machine 該留 Python） |

**P0 三個**之所以排最前：品味濃度 5、且**每次執行天然就有 verdict**（重組要改字、標題要選一個、promotion 要 accept/defer）——學習燃料最充足，最能體現「越用越貼近品味」。

---

## 5. Taste Loop 設計：每次執行如何增進 skill

把 Foundry 的 `edit_log → examples` 從「Foundry 專屬」**升格為所有品味型 skill 的標準四件套**。核心是：**raw 訊號自動記、promote 到 store 由人決定、載入時三層注入。**

### 5.1 四件套（每個品味型 skill 的目錄）

```
<skill>/
├── SKILL.md              # 中心短：流程 + 判斷 + 載入指引（< 500 行）
├── GOTCHAS.md            # 負面邊界（每條附「為什麼」），skill-local
├── examples/             # few-shot 正向範例（修修 accept 的高分輸出）
├── style/                # 風格約束（約束 yaml + 完整指引 md + 真實樣本）— 沿用 Brook 形態
└── evals/evals.json      # should-trigger / should-not-trigger + 正例/反例
```

> 跨 skill 的行為型 gotchas 仍寫回 `memory/claude/feedback_*.md`（既有層）；skill-local 的領域 gotchas 放 `GOTCHAS.md`。兩層分工：行為（怎麼跟修修協作）vs 領域（這個 skill 怎麼產出才對品味）。

### 5.2 一次執行的迴路（五步）

```
1. RUN      skill 執行，產出 candidate，並 capture {input, output, context}
2. VERDICT  修修 HITL 驗收：accept / edit（改了什麼）/ reject（為什麼）+ 一句話 note
            ↑ 不可自動化（feedback_hitl_gate_serves_subjective_taste）
3. CAPTURE  自動把 {before, after, user_note, verdict} append 到 raw log（Foundry edit_log 模式）
            ← 只記，不直接改 skill
4. PROMOTE  週期性蒸餾 raw log → 分流四個 store（promotion 要人點頭）：
              reject + 為什麼  → GOTCHAS.md（附 why，不寫全大寫 MUST）
              accept / 高分    → examples/（few-shot）
              edit 改字/色/節奏 → style/（約束 delta）— 僅限「語氣一致性」用途
              該觸發沒觸發 / 誤觸發 → evals（描述優化迴圈）
5. LOAD     下次執行：SKILL.md + GOTCHAS.md 進 context；examples/ + style/ 按需注入
```

### 5.3 訊號分流表（哪種驗收動作餵哪個 store）

| 修修的動作              | 代表的品味訊號    | 寫進                          | 下次怎麼被用                   | 注意                        |
| ------------------ | ---------- | --------------------------- | ------------------------ | ------------------------- |
| reject + 說為什麼      | 負面邊界（最高價值） | `GOTCHAS.md` / `feedback_*` | 載入時帶入、收緊 routing         | 一定附「為什麼」，讓模型能泛化           |
| accept / 打高分       | 正向典範       | `examples/`                 | few-shot 注入              | 別過量，挑代表性的                 |
| edit 改字 / 改色 / 改節奏 | 風格約束 delta | `style/` yaml+md            | compose system prompt 注入 | **僅「語氣一致」用途**；品質判斷/選題不走這條 |
| 該觸發卻沒觸發、或誤觸發       | 路由邊界       | `evals/`                    | description 優化           | 反例要做「近似誤觸」才有效             |

### 5.4 防過擬合與「稅」控制（官方紀律）

- **promote 由人決定**：是否值得沉澱、怎麼命名、邊界在哪——文章與 skill-creator 都強調這步不能全自動。raw log 自動，store 進入要 HITL。
- **泛化而非貼補**：蒸餾時寫「原則 + 為什麼」，不要把單一案例硬寫成規則（skill-creator: generalize, don't overfit）。
- **定期問「這句還有沒有在拉動行為」**：每個 skill 是一種稅；`shared/memory_maintenance`（已有 `stats / expire / archive`）可擴一個 `distill` 子命令，定期把過時 gotchas 歸檔、把重複 example 去重。
- **description 先調**：每次改 routing 邊界都補一筆 eval，跑 should-trigger/should-not-trigger。

### 5.5 為什麼這個設計成立（接回既有系統）

- **不重造輪子**：gotchas 用既有 `memory/` schema；few-shot 用既有 Foundry edit_log 形態；style 用既有 Brook profile 形態。新的只是「把三者標準化成四件套 + 明確的訊號分流」。
- **verdict 已經天然存在**：P0 三個候選每次跑都要修修驗收（改字 / 選標題 / promotion review），不需要額外加 friction，只需要把那個動作「記下來並分流」。
- **尊重紅線**：學的是修修的**品味與邊界**，不是替他寫 Line 2 正文；語氣樣本只用在 channel 重組，不污染品質判斷。

---

## 6. 建議落地順序

1. **先寫 1 個 P0 skill 當模板**（建議 **channel 語氣重組** 或 **標題發想**）——把四件套 + 五步迴路跑通一輪，產出可複製的範式。
2. **抽 raw log + promote 的共用工具**（`shared/taste_loop.py`：append_entry / distill），讓 Foundry edit_log 也收斂到同一套。
3. **照優先序補其餘 P0 → P1**，每個都先寫 evals（描述先調）、再寫 body（刪到只剩會改變行為的判斷）。
4. **P2 與 Usopp 不急**：Usopp 維持純 Python daemon。

每做完一個，照官方 checklist 自審：description 帶觸發詞與邊界？body < 500 行？引用一層深？gotchas 附 why、無全大寫 MUST？evals 有近似誤觸反例？

---

## 7. 來源

- 對照筆記（方法論）：vault `Inbox/web/Skill 製作最佳實踐 — 官方文件 vs 萬字長文 對照.md`
- 官方 Skill authoring best practices：https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
- `anthropics/skills`（skill-creator）：https://github.com/anthropics/skills
- Codebase 依據：`agents/foundry/edit_log.py`、`agents/brook/style_profile_loader.py`、`config/style-profiles/`、`memory/SCHEMA.md`、`memory/claude/MEMORY.md`、`CONTENT-PIPELINE.md`、`.claude/skills/seo-audit-post/SKILL.md`、`.claude/skills/foundry-replan/SKILL.md`、ADR-001

---

## 8. 補充候選（2026-06-17 第二輪盤點）

第一輪漏掉的、或當時權重給低但其實值得做的——特別是 KB 健康維護那一塊（剛好對應社群已有的 compile/query/lint/evolve 切法，見 §9）：

| #   | 候選 skill                       | Agent / Stage         | 品味  | 說明                                                                                                          | 已是 skill?       |
| --- | ------------------------------ | --------------------- | --- | ----------------------------------------------------------------------------------------------------------- | --------------- |
| 12  | **KB lint（健康檢查）**              | Robin / S3·S7         | 3   | CLAUDE.md §6.3：找矛盾主張、過時聲明、孤立頁、重複頁、斷鏈、缺引用。判斷「哪些算問題」有品味，但多半可規則化                                               | ❌               |
| 13  | **KB refactor（重構）**            | Robin / S3            | 4   | CLAUDE.md §6.4：合併重複概念、拆過長頁、把 Output 升格 synthesis。高品味、低頻、必 HITL                                              | ❌               |
| 14  | **KB query → Output 合成**       | Robin / S4            | 4   | CLAUDE.md §6.2：查詢時組織答案、區分事實/判斷/推論，並決定是否存回 `KB/Wiki/Outputs/`。`kb-search` 只做檢索、未到合成                          | 🟡 kb-search 部分  |
| 15  | **annotation → concept weave** | Robin / S2→S3         | 4   | `annotation_weave.py`：把劃線/筆記織進 concept 的「個人觀點」段。哪條 annotation 值得升格＝品味                                       | 🟡 在 pipeline    |
| 16  | **epub / 文章雙語翻譯**              | Robin / S2            | 3   | Reader 的 translate；翻譯語氣有品味（modal particles / 術語表）。`feedback_traditional_chinese`、TW-HK glossary 是現成 gotchas | 🟡 route 有       |
| 17  | **SRT 字幕校正**（中文斷詞 / 多模態仲裁）     | Foundry / Line 1 / S4 | 4   | `feedback_chinese_srt_word_boundary_jieba` 是現成 gotcha。`transcribe` 有，但校正品味未獨立                               | 🟡 transcribe 部分 |
| 18  | **fact-check**                 | 跨 / S3·S4             | 3   | 已有設計稿 `docs/plans/2026-04-27-fact-check-agent-design.md`，可直接 skill 化                                        | ❌               |
| 19  | **合規詞彙 gate**（台灣藥事/醫療法）        | Brook·Usopp / S6      | 1   | `shared.compliance.scan`。純確定性、無品味 → 跟 WordPress 發布一樣，**建議留 Python 不做 skill**                                | （Python）        |
| 20  | **taste-loop distill**（meta）   | shared                | 2   | §5.4 提的：把 raw edit log 蒸餾成 gotchas/examples/style，掛在 `shared/memory_maintenance` 下。是「讓所有 skill 越用越貼品味」的引擎本身 | ❌               |

> Robin 其實是 skill 化價值最高的 agent：ingest / lint / refactor / query→Output / annotation-weave 五件事，剛好就是 Karpathy「LLM-maintained wiki」的四個動詞（compile / query / lint / evolve）+ 個人化那一層。

## 9. 社群借鑒：不要重造輪子（也不要照抄）

廣泛搜尋後，KB 這條線**社群已經有高度重疊的開源 skill**，而且共同祖先就是 Nakama CLAUDE.md 已經在用的 **Karpathy LLM-wiki 模式**（ingest / query / lint / refactor）：

- **`AgriciDaniel/claude-obsidian`** — 「self-organizing AI second brain for Obsidian + Claude Code」，15 個 skill，明確走 Karpathy LLM-wiki 模式。有 **Methodology Modes**（Zettelkasten / PARA / LYT）、對 fleeting/literature/permanent/MOC/project 的 CRUD、Luhmann 編號、connection scoring、`/adopt` 掃描既有 vault 結構。**幾乎是 Nakama Centaur KB 的 1:1 對應物**——最值得逐個 SKILL.md 拆來看它怎麼切 ingest / 怎麼做 connection scoring。
- **`rvk7895/llm-knowledge-bases`** — 「compile / query / lint / evolve your personal knowledge base」。**動詞跟 Nakama §6.1–6.4 工作流完全對齊**，可直接借鑒它的 skill 切分邊界。
- **`ballred/obsidian-claude-pkm`**、**`dlutsyk/Obsidian-Zettelkasten-Claude`** — Obsidian+Claude PKM / Zettelkasten 起手包，可看 frontmatter schema 與 note-type 約定。
- 另有 **Zettelkasten MCP server**（Luhmann 編號 / typed connections / SQLite metadata cache）走 MCP 而非 skill — 對應 Nakama 用 SQLite state.db 的做法。
- 索引站可長期追蹤：`ComposioHQ/awesome-claude-skills`、`VoltAgent/awesome-agent-skills`、`hesreallyhim/awesome-claude-code`。

**借鑒可以、照抄不行（紅線）**：這些社群專案的預設是「**AI 自動歸檔一切**」——drop 任何來源，AI 自己 read/link/file。Nakama 的治理模型剛好相反：Centaur 五條紅線（AI 絕不寫 Permanent 正文/status）、HITL gate 服務主觀品味、Stage 4 Line 2 心得不可代寫。所以：

- ✅ 借：skill 目錄結構、ingest 的 progressive disclosure、connection scoring、note-type frontmatter、compile/query/lint/evolve 的切分。
- ❌ 不照搬：auto-file-everything 的預設；要保留 Nakama 的 promotion review / HITL gate，不能讓 AI 無審核直接寫正式 KB。

**結論**：Robin ingest/lint 不必從零設計——以 `rvk7895/llm-knowledge-bases` 的動詞切分為骨架、`claude-obsidian` 的 note-type CRUD 為參考，套上 Nakama 既有的 Centaur 紅線與 taste loop（§5）即可。
