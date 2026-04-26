# Ch1 v2 ingest 驗收清單

> **建立日**：2026-04-26
> **對應 PR**：PR C（Wiley *Biochemistry for Sport and Exercise Metabolism* Ch.1 v2 re-ingest，已 DONE 但無 PR diff，是直接在 vault 操作）
> **目的**：人眼確認 ch1 v2 ingest 出來的 11 個 concept page + chapter source page + Book Entity 的內容品質，並決定 PR D（批 ingest ch2-ch11）開跑前要不要先補修
> **狀態**：pending — 修修一條一條回覆

---

## 0. 自動化 schema check 結果（已通過，不用人眼複驗）

- [x] 7 個改動 page（2 create + 4 update_merge + 1 update_conflict）全 `schema_version: 2`
- [x] 11 個 v2 ingest 命中的 page 範圍內，0 個 legacy `## 更新（` block 殘留
- [x] 全 vault 唯一 `## 文獻分歧` section 出現在 `磷酸肌酸系統.md`（無誤命中其他頁）
- [x] 4 個 noop page `mentioned_in` count=2（frontmatter + Sources block 兩處都有 ch1 wikilink）
- [x] Book Entity status: partial / chapters_ingested: 1 / 11 章 index 完整
- [x] Attachments：13 PNG + 2 markdown table 全在 `ch1/` 路徑下

---

## 1. 我先掃出的 3 個 Finding（要先決策）

### F1 — `運動營養學.md` 沒被 PR C 處理到（漏網之魚）

- **檔案**：`F:/Shosho LifeOS/KB/Wiki/Concepts/運動營養學.md`
- **問題**：
  - v1 ingest（2026-04-25）的 6 個 update target 之一，但 PR C 4-action plan 沒列
  - 仍是 v1 schema（無 `schema_version` 欄位）
  - body line 103-107 仍有 legacy `## 更新（2026-04-25）` block（純 imperative 段）
  - Book Entity 裡 4-action plan 摘要也漏列它
- **影響**：v1 ingest 留下的 todo-style append 沒清；ADR-011 §6 acceptance「6 個既有 concept page 全 v2 schema、body 末尾無 `## 更新` block」未滿足
- **處置選項**：
  - (a) 補一個小 PR 手動 lazy migrate 這頁（清 update block + 升 schema_version + 加 mentioned_in）
  - (b) 等 PR D ingest ch7（Principles of Metabolic Regulation 含營養代謝調控）或更後章節自然命中再處理
- **修修決策**：(b)

---

### F2 — `磷酸肌酸系統.md` frontmatter 缺 `aliases:` 欄位

- **檔案**：`F:/Shosho LifeOS/KB/Wiki/Concepts/磷酸肌酸系統.md` line 1-18
- **問題**：
  - ADR-011 §3.1.1 表格列 `aliases: list[str]` 為必填（yes，可空 list）
  - 此頁 frontmatter 完全沒這個 key
  - 推測其他 6 個 v2 page（葡萄糖丙胺酸循環 / 肌內三酸甘油酯 / ATP再合成 / 肌酸代謝 / 磷酸肌酸能量穿梭 / 肌酸激酶系統）也可能有同樣問題（待 sweep 確認）
- **影響**：日後 PR D 若 ingest 到「Phosphagen system」「ATP-PCr system」這類同義詞，aliases-based dedup 會 false negative → 可能誤建新 page
- **處置選項**：
  - (a) 手動補（磷酸肌酸系統別名候選：`Phosphagen System`, `ATP-PCr 系統`, `磷酸原系統`, `anaerobic alactic 系統` — body line 24 已寫出）
  - (b) 加進 PR D 的順手任務（每章 ingest 完掃 frontmatter 補 aliases）
  - (c) 寫一個 backfill script 對所有 v2 page 一次補
- **修修決策**：你決定

---

### F3 — Chapter source body 的 13 個 `<<FIG:>>` + 2 個 `<<TAB:>>` 占位符沒被 swap 成 Obsidian image embed（**user-facing：Obsidian 開不顯示圖**）

- **檔案**：`F:/Shosho LifeOS/KB/Wiki/Sources/Books/biochemistry-sport-exercise-2024/ch1.md`
  - figure 占位符在 line 147 / 170 / 174 / 178 / 205 / 209 / 213 / 221 / 228 / 232 / 267 / 314 / 355
  - table 占位符在 line 286 / 318
- **症狀**：Obsidian 開 ch1.md 看到 13 處純文字 `<<FIG:fig-1-1>>` 等占位符；attachment 圖檔在 `Attachments/Books/biochemistry-sport-exercise-2024/ch1/` 下都在但完全不顯示
- **根因（雙因素）**：
  1. **Spec 缺口**：`SKILL.md` Step 4c line 241-246 寫的是「leave the placeholder in place + splice llm_description inline」— 設計意圖是「占位符留著做 reverse-look-up + 旁邊 splice description」。但**完全沒講要把占位符 swap 成 Obsidian image embed `![[Attachments/...png]]`**。即使按 spec 完整執行，Obsidian 還是看不到原圖（只看到 description 文字）
  2. **操作疏漏**：PR C 互動 session 跑 Step 4c 時連 `llm_description` 都沒 splice 進 body，只寫進 frontmatter `figures[].llm_description`。所以現況是 **worst case** — 占位符純文字 + 沒 description。
- **影響**：
  - 修修在 Obsidian 開 ch1.md 看不到任何 figure / table（**V1 figure 品質驗收因此被 block** — 沒法在 Obsidian split view 對照看）
  - PR D 自動跑 ch2-11 會踩同樣 bug（spec 沒明定）
  - retrieval 端如果直接讀 chapter source body，看不到 description（雖然 frontmatter 有，但下游怎麼用沒明文）
- **處置選項**：
  - (a) **短期 patch ch1.md**（純編輯，~10 min）：
    - 13 個 fig 占位符 → `![[Attachments/Books/biochemistry-sport-exercise-2024/ch1/fig-1-N.png]]` + 下方加 `> {llm_description}` blockquote
    - 2 個 tab 占位符 → 直接 splice `Attachments/.../tab-1-N.md` 的 markdown content
  - (b) **長期修 spec + skill**：
    - ADR-011 §3.3 Step 3 + §3.4.1 補明 chapter source body 寫入時的「占位符 → Obsidian image embed + caption」轉換規則
    - `SKILL.md` Step 4c 補 swap 邏輯 + caption splice 格式
    - `chapter-summary.md` prompt 拿掉「保留占位符」改寫「在占位符位置寫 image embed + llm_description caption」
    - 或在 textbook-ingest driver 加 post-process step：LLM 寫完 body 後 regex swap
  - (c) **(a) + (b) 都做**（推薦）— ch1.md 立即可看 + PR D 不會重踩
- **修修決策**：(c) 兩個都做 — **DONE 2026-04-26**
  - **(a) 短期 patch ch1.md** ✅：13 `<<FIG:>>` + 2 `<<TAB:>>` 占位符全 swap 完，grep 0 match
    - 13 figure → `![[Attachments/Books/biochemistry-sport-exercise-2024/ch1/fig-1-N.png]]` + italic caption
    - 2 table → bold caption + spliced markdown table content
  - **(b) 長期修 spec + skill** ✅（branch `fix/ingest-v2-figure-placeholder-swap`，待 commit + PR）：
    - `chapter-summary.md` prompt — 7 處改寫：P3 row / figures input / Avoid 段拆出新 "Placeholder swap rules" section / 寫作鐵則 / Full prompt 範本（拿掉「保留占位符」改「占位符 swap 強制」）
    - `SKILL.md` Step 4c — 改寫 splice 描述為三條 swap rules（fig / tab / eq），加 incident reference 指向本 checklist F3
    - `ADR-011 §3.3 Step 3` — Input 增列 `figures` / `tables` 欄位、新增 `Placeholder swap (強制)` 一列，明定三類占位符的目標 markdown 形式
    - `ADR-011 §3.4.1` — EPUB walker 表格的 `<img>` / `<table>` 行加註「占位符不可 leak」+ 新增「占位符生命週期 invariant」段
    - `ADR-011 §6 Acceptance Criteria` — 加一條「chapter source body 無任何占位符殘留」check

---

## 2. By-design 但需要修修確認接受

### B1 — 4 個 noop page 仍是 `schema_version: 1`

- **頁面**：`能量連續體` / `糖解作用` / `有氧能量系統` / `無氧能量系統`
- **設計依據**：ADR-011 §4.3 lazy migrate trigger 明文 `action != "noop"` — 即 noop action 不 trigger schema migration
- **影響**：vault 有 v1 / v2 page 並存（過渡期到 ~一季後 backfill）
- **修修決策（接受 / 不接受）**：你決定

---

## 3. 人眼驗收項（依優先序）

### V1 — 13 張 figure 的 Vision describe 品質（決定 PR D 走 Sonnet 還是升 Opus）

**這項是 PR D 開跑前最關鍵的驗收**。Vision LLM 預設 Sonnet 4.6（ADR-011 §3.4，cost 約 Opus 1/5），ch1 用 Opus 4.7 in-session 多模態 Read 做的（PR C 例外，因為 Claude Code Opus 跑互動 session）。如果 Sonnet 在自動化 batch 跑 PR D 時品質不夠，284 figures × 全書都會做白工。

**檔案**：`F:/Shosho LifeOS/KB/Wiki/Sources/Books/biochemistry-sport-exercise-2024/ch1.md` 的 frontmatter `figures:` list

**比對方式**：每張 figure 的 `llm_description` ↔ `F:/Shosho LifeOS/Attachments/Books/biochemistry-sport-exercise-2024/ch1/fig-1-{N}.png`（在 Obsidian 裡 split view 對照看）

**重點挑這幾張看就夠**（按複雜度由淺入深）：

| Fig | 重點 | 你的評分 |
|---|---|---|
| **Fig 1.2**（能量連續體曲線） | high-stakes，PCr 主導窗口爭議的視覺證據 | 🟢/🟡/🔴 _（待）_ |
| **Fig 1.4**（PCr shuttle，mi-CK / mm-CK 雙細胞區隔） | 解剖+生化雙維度 | 🟢/🟡/🔴 _（待）_ |
| **Fig 1.5**（Hultman 1990 PCr 雙相恢復） | 數值精確度測試 | 🟢/🟡/🔴 _（待）_ |
| **Fig 1.6**（Romijn 1993 三強度 fat vs CHO 比例） | 多 panel 圖 | 🟢/🟡/🔴 _（待）_ |
| **Fig 1.8**（IMTG） | 新建 concept anchor 圖 | 🟢/🟡/🔴 _（待）_ |
| **Fig 1.9**（glucose-alanine cycle） | 新建 concept anchor 圖 | 🟢/🟡/🔴 _（待）_ |

**評分標準**：
- 🟢 描述精確到「軸+單位+曲線+標註點+與周圍文字的關係」全有 → PR D 維持 Sonnet 4.6 default
- 🟡 描述大致對但缺數字精確度（如「曲線下降」但沒寫 t≈10s）→ PR D 仍走 Sonnet 但加 prompt note
- 🔴 描述模糊或誤解圖意（如把 schematic 當 line plot）→ PR D **必須升 Opus 4.7**，多燒 ~5x quota 但避免 284 figures 全做白工

**修修整體決策**：**Sonnet 4.6**（保留 ADR-011 §3.4 default）— **DONE 2026-04-26**

**評估方式**：派 Sonnet 4.6 sub-agent 對 13 張 figure 重跑 Vision describe（同 prompt template / domain role），跟既有 Opus 4.7 in-session description head-to-head。修修原以為既有是 Sonnet 跑的、人眼看圖評分變沒意義，故改用此 controlled comparison。對照報告：[`docs/research/2026-04-26-ch1-vision-sonnet-rerun.md`](../research/2026-04-26-ch1-vision-sonnet-rerun.md)。

**Tally**：Sonnet 7 張略佳 / Opus 2 張略佳 / 4 張平手

**Sonnet 整體不輸 Opus**（多數張數值精度、雙語、citation 更完整），cost 約 Opus 1/5。

**已知 Sonnet 兩個 weakness（mitigation 已寫進）**：
1. Anatomical localization in schematics（fig-1-6 PCr shuttle Sonnet 把 CrT 位置搞混）— 對 ch2 *Skeletal Muscle Structure* 預期 schematic 多，**ch2 ingest 時挑 2-3 張先 Sonnet 跑、修修人眼 spot-check**；不滿意再單獨升 Opus
2. Minor blemishes（LaTeX `}}` typo + 雙語術語不一致）— driver post-process 可加 sanity check（grep + 中譯表）作 follow-up，非 PR D blocker

**意外 finding**：互動 session Opus Read（PR C）vs Sonnet API 的差異可能比 model tier 差異更大 — Opus session 反而漏些 textbook anchor（Marathon% 算術、β-γ bond、Romijn fat fraction）。implication：未來 session 手動 Read 預期比 batch API 略差。

---

### V2 — `磷酸肌酸系統.md` 的 `## 文獻分歧 / Topic 1` 分析合理性

**檔案**：`F:/Shosho LifeOS/KB/Wiki/Concepts/磷酸肌酸系統.md` line 68-89

**結構（已 self-check）**：
- 教科書 anchor（MacLaren & Morton 2024）：1-10 秒主導 + 10 秒交棒
- 綜述 paper（Creatine Beyond Athletics 2024）：10-15 秒上限
- 討論段：分歧根源 + KB anchor 立場 + 訓練設計含意

**修修要判斷的**：
- [ ] 分歧根源寫的是「主導 vs 完全耗盡」的 endpoint 差異 — 這個 framing 認同嗎？
- [ ] KB anchor 立場選教科書（1-10s 主導 + 10s 交棒），對你 retrieval 用途夠不夠？
- [ ] 訓練設計含意（HIIT 6-10s work / 60-120s rest）認可嗎？
- [ ] Topic 2（PCr 雙相恢復動力學）標為 KB consensus、無分歧 — 這個判斷認同嗎？

**修修整體決策**：_（接受 / 部分修改 / 整段重寫）_

---

### V3 — 4 個 update_merge page 的 lazy migrate 品質

逐頁 open 確認舊 `## 更新` block 真的被 merge 進主體（不是只刪掉）：

| Page | 路徑 | 重點看什麼 | 你的判斷 |
|---|---|---|---|
| `ATP再合成` | `KB/Wiki/Concepts/ATP再合成.md` | 教科書 Table 1.1 速率階層（PCr→ATP / 糖解 / 有氧 三層）+「反應步數律」有沒有真的進主體 | _（待）_ |
| `肌酸代謝` | `KB/Wiki/Concepts/肌酸代謝.md` | Cr ↔ PCr 動態 + Hultman 1990 雙相恢復實驗（75% / 1min + 25% / 3-5min）有沒有進主體 | _（待）_ |
| `磷酸肌酸能量穿梭` | `KB/Wiki/Concepts/磷酸肌酸能量穿梭.md` | mi-CK / mm-CK / CrT 教科書命名 + Fig 1.4 視覺化內容有沒有進主體 | _（待）_ |
| `肌酸激酶系統` | `KB/Wiki/Concepts/肌酸激酶系統.md` | CK isoforms 方向性 + 血液 marker（CK-MB）化學基礎有沒有進主體 | _（待）_ |

**統一檢查項（每頁都要過）**：
- [ ] frontmatter `mentioned_in:` 含 ch1 wikilink + 既有 paper wikilink 兩條
- [ ] body 末尾無 legacy `## 更新` block
- [ ] `aliases:` 欄位是否有寫（同 F2，可能漏）

---

### V4 — 2 個新建 page 的內容深度

| Page | 路徑 | 重點看什麼 | 你的判斷 |
|---|---|---|---|
| `葡萄糖丙胺酸循環` | `KB/Wiki/Concepts/葡萄糖丙胺酸循環.md` | §1.7 anchor / Fig 1.9 內容；Definition + Core Principles + Practical Applications 是否有實質生化機制（不是 LLM stub） | _（待）_ |
| `肌內三酸甘油酯` | `KB/Wiki/Concepts/肌內三酸甘油酯.md` | §1.6 anchor / Fig 1.8 / Table 1.2；IMTG 與肝糖原、血漿游離脂肪酸三大脂質 substrate 的對照 | _（待）_ |

---

### V5 — Chapter source page 的 deep extract 密度

**檔案**：`F:/Shosho LifeOS/KB/Wiki/Sources/Books/biochemistry-sport-exercise-2024/ch1.md`

**重點驗收項**：
- [ ] 每節 verbatim quote 1-2 句（含原書頁碼）
- [ ] 每節 `### Section concept map`（mermaid / nested bullet）
- [ ] `## 章節重點摘要 / Chapter takeaways` 段
- [ ] `## 關鍵參考數據 / Key reference values`（記憶寫 22 條量化 anchor）
- [ ] `## 跨章 / 跨書 連結建議` 段
- [ ] **整體訊息密度跟你手上 epub ch1 比，是否真的「LLM-readable deep extract」（P2 原則）**

**修修整體判斷**：_（夠深 / 還可以再深 / 太囉嗦）_

---

### V6 — VPS-side `/kb/research` live query

**目的**：確認 Obsidian Sync 已把 ch1 v2 推到 VPS，且 retrieval 端能命中 `## 文獻分歧` section（ADR-011 §6 最後一條 acceptance）

**步驟**：
1. [ ] 確認 Obsidian Sync 已推到 VPS（vault 改動 → iCloud Drive → VPS rclone pull，2-5 min）
2. [ ] 跑 query — 推薦走 SSH 直連 uvicorn（CF SBFM 會擋 curl）：
   ```
   ssh nakama-vps 'curl -s "127.0.0.1:8000/kb/research?q=PCr+主導時間多長" | jq'
   ```
   或瀏覽器觸發 Robin Slack `@robin /kb-search PCr 主導時間多長`
3. [ ] 預期命中 `磷酸肌酸系統.md` 並帶 `## 文獻分歧 / Topic 1` 的雙立場敘述
4. [ ] 確認結果不是「只拿 1-10s」或「只拿 10-15s」的單一說法

**修修判斷**：_（命中 / 沒命中 / 命中但不完整）_

---

## 4. 驗收結束後要拍的決策板

驗收完，這 5 個板要拍才能解鎖 PR D：

1. **F1（運動營養學漏網）**：補小 PR / 等 PR D 自然命中？(b)
2. **F2（aliases 欄位缺）**：手動補 / 加進 PR D 順手任務 / backfill script？_（待）_
3. **F3（占位符沒 swap 成 image embed）**：(c) 兩個都做 — **DONE 2026-04-26**（V1 驗收解 block）
4. **B1（4 個 noop page schema_version=1）**：接受 by-design / 改 ADR 強制 noop 也 lazy migrate？_（待）_
5. **V1（Vision describe 品質）**：**Sonnet 4.6**（保留 ADR-011 default）— **DONE 2026-04-26**（Sonnet rerun head-to-head 顯示 Sonnet 整體不輸 Opus；ch2 schematic 多，建議先挑 2-3 張 spot-check 再放手 batch）

---

## 5. 相關 reference

- ADR：[`docs/decisions/ADR-011-textbook-ingest-v2.md`](../decisions/ADR-011-textbook-ingest-v2.md)
- Plan：[`docs/plans/2026-04-26-ingest-v2-redesign-plan.md`](2026-04-26-ingest-v2-redesign-plan.md)
- 拍板紀錄：[`docs/plans/2026-04-26-ingest-v2-decisions.md`](2026-04-26-ingest-v2-decisions.md)
- 進度記憶：[`memory/claude/project_ingest_v2_step3_in_flight_2026_04_26.md`](../../memory/claude/project_ingest_v2_step3_in_flight_2026_04_26.md)
- 4 原則：[`memory/claude/project_textbook_ingest_v2_design.md`](../../memory/claude/project_textbook_ingest_v2_design.md)
- aggregator 哲學：[`memory/claude/feedback_kb_concept_aggregator_principle.md`](../../memory/claude/feedback_kb_concept_aggregator_principle.md)
