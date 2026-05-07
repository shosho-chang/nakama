# ADR-021 決策表（整合 Codex + Gemini panel）

兩家審查獨立給「**不要 ship as-is**」結論。Codex 抓代碼 drift（K=30 推理錯、schema simplification 會炸 UI、scan loop 硬編、現有 derived 檔被忽略），Gemini 抓**架構與長期視角**（資訊論不對稱、5 年熵增、跨語言 retrieval 根本沒做、HITL gate 是 flow-breaker）。

非策略性的 tactical 修正（K 換 evaluation plan、改 async background、scan loop 真的要改、承認既有 `book_digest_writer` 等檔）我直接 fold 進 v2，不另列。**這份決策表只列你需要判的 4 個 strategic fork**。

---

## Fork A — Annotation 存放架構

> File 2 prose 是 derived view of File 1 — 但 derive 是**單向有損**：CFI 位置可以產 prose，prose 不能還原 CFI。把 derived File 2 推上 retrieve 中心，是把次要檔當主要、主要檔當配角。

### 真實會出什麼事

**情境 1（半年後 schema drift）**：你 2025 年 11 月 highlight 一段 paper、寫一條 reflection 進 File 2。2026 年 4 月你升級 LLM、prose generator 邏輯改了（例如新增「自動補 wikilink」功能）。你刪掉 File 2 重產 — **新 prose 自動加 `[[Concepts/睡眠]]`，但舊 File 2 沒這 wikilink**。如果你過去 6 個月有手改過 File 2 任何一處（即使我們 ADR 寫「不要手改」，難保），那次 regenerate 會無聲覆寫。

**情境 2（concept 名改了）**：你 5 月命名 `KB/Wiki/Concepts/Sleep.md`，11 月改名成 `KB/Wiki/Concepts/睡眠.md`（alias）。File 2 內的 wikilink 全部要重產才會更新。File 1 不受影響。**兩檔狀態不同步沒人發現**，graph view 上半年的反思變孤兒。

**情境 3（同步 regenerate 失敗）**：Reader UI save，server 寫 File 1 成功、prose generator 跑到一半 timeout / OOM、File 2 沒寫。下次你打開 Obsidian 看 Syntheses，少了那條 reflection。**沒 audit trail**。

### 三個選項的白話

**(P) 雙檔分裂**（ADR-021 原案）
- File 1（JSON 位置）+ File 2（prose 內容），canonical 是 File 1
- **代價**：兩檔同步、drift 風險、**File 2 是 derived 但 retrieve 從 File 2 讀** = 架構頭重腳輕

**(Q) 退回 ADR-017 單檔**
- 一切照舊，`KB/Annotations/{slug}.md` JSON 是唯一檔
- 額外做「**Obsidian 友善 prose 視圖**」當 View，**但 indexer 直接讀 JSON 不讀 prose view**
- prose view 純粹是給人類偶爾 peek 用，**不參與 retrieve**
- **代價**：retrieve 要 parse JSON 對 reflection 撈 body — 多寫一個 indexer adapter

**(R) W3C 風結構化單檔**
- 把 `KB/Annotations/{slug}.md` 內每條 item 設計成「**target + body 同 record**」（W3C Web Annotation Data Model 格式）
- Target = cfi / ref（位置）；body = text / note / reflection（內容）— 都在同一條 row 內
- indexer 直接讀這檔的 structured items，**不需要 prose 副本**
- 想成資料庫一張 table，每 row 是一個 highlight/annotation/reflection，row 同時帶位置 + 內容
- **代價**：要改 ADR-017 v2 schema（但不算 migration、只是 enrich），retrieve 邏輯要新增「結構化 item iterator」
- **長處**：Hypothesis、Readwise、W3C 都這樣做，不是我們發明的，**沒有 drift 可能**

### 我的偏好

**(R)**。理由：

1. **零 drift 風險** — 一檔一真相，沒 derive、沒 sync hook、沒 timeout 失敗點
2. **跟既有 industry pattern 對齊**（Hypothesis / Readwise / W3C） — 不是我們蓋空中樓閣
3. **遷移成本可控** — ADR-017 v2 schema 已經很接近，加 `body` 欄位到所有 item type、移除「兩檔」的整個概念，end of story
4. **Obsidian 可讀性問題用 view-only renderer 解決**：你偶爾要 peek 時，Web UI 或 plugin 渲染 JSON → prose 即時生成，**不持久化**

(P) 我原本選但 panel 戳出架構顛倒、(Q) 過於保守還是要做 prose generator 跟 indexer adapter 兩套程式碼。

---

## Fork B — Brook 輸出（evidence + outline）寫到哪

> Project 頁面是**你寫稿的真實資料**。讓 Brook 在裡面寫 30 條 evidence + outline + reject log + 之後新功能的 N 個 section，5 年後變垃圾場。

### 真實會出什麼事

**情境 1（5 年後 Brook 擴大）**：今天 Brook 寫 2 個 section（evidence + outline）。3 年後 Brook 加了「related podcast 引用」「FB post draft」「IG carousel hook」「YouTube hook」「SEO keyword density 分析」— 每個 section 都想塞 Project 頁面。**Project 頁面 90% 是 LLM 內容，10% 是你的構思**。

**情境 2（你打開 Obsidian 想看「肌酸的妙用」進度）**：scroll 過 Zoro 30 條 keyword、Brook 30 條 evidence（每條附 quote）、Brook 5-7 段 outline（每段 200 字）、Brook 已 reject 的 15 條 log、之後加的 N section... 才看到你自己寫的 outline 修改記號。**Vault 簡潔 preference 直接被破壞**。

**情境 3（夾帶 mutation 衝突）**：你在 Obsidian 內手改 outline 第 3 段，同時 Web UI 那邊你按 reject 把 evidence #7 剔掉、Brook 自動 regenerate outline（因為 evidence 變了）— **Web UI 寫的 outline 蓋掉你手改的版本**，你在 Obsidian 內辛苦改的字消失。

### 三個選項的白話

**(S) Project 頁面 + marker contract**（ADR-021 原案）
- Brook 寫進 Project 頁面 body，用 marker（如 `%%BROOK-EVIDENCE-START%%` ... `%%BROOK-EVIDENCE-END%%`）框出 LLM 區
- 跟既有 `%%KW-START%%/%%KW-END%%`（Zoro 用）同 pattern
- **代價**：marker 區 5 年後會 ×N 增多，Project 頁面真的是「人 + agent 混居檔」

**(T) Sidecar 在 vault**
- `Projects/{slug}.brook.md` 或 `.brook.json` 跟 Project 頁面同目錄
- Project 頁面只存 outline + 你的構思
- Obsidian 看得到 sidecar — 違反你「vault 不增檔」preference

**(U) Sidecar 在 vault 外**（server-side store）
- 存 Thousand Sunny VPS（SQLite / JSON / per-project file）
- Obsidian 完全看不到（**跟你「LLM-maintained 內容走 Web UI 不走 Obsidian」一致**）
- Web UI 連 server 讀
- Project 頁面在 Obsidian 完全乾淨：只 frontmatter（topic + Zoro keywords）+ 你的 outline 改寫
- **代價**：vault backup 不會備份這些 evidence — 但 evidence 本來就 derived（從 KB/Annotations + KB/Wiki 重產 cheap），不需備份

### 我的偏好

**(U)**。理由：

1. **跟你 stated preference 完全對齊** — 你已說「Obsidian 只看時間軸 + Project 頁面，LLM-maintained 走 Web UI」。Brook evidence + outline = 高度 LLM-maintained，本來就不該在 Obsidian 顯眼處
2. **Project 頁面長期保持瘦** — 永遠只 frontmatter + 你的 outline，Brook 加再多功能都不污染
3. **Obsidian backup / sync 不負擔 derived 內容** — 你的 vault Sync conflict 已經有了（看到 `index.sync-conflict-*.md`），減少 derived 寫入是好事
4. **跟 ADR-017 v2 prose-view-only 同精神** — derived 內容不進 vault canonical 路徑

(S) 5 年後變垃圾場、(T) 你已 surface 不開心於 vault 檔多。

**這條跟 Fork A 的 (R) 結合特別漂亮** — A 選 (R) 後 KB/Annotations 內就是 single source of truth、indexer 直接讀；B 選 (U) 後 Brook 不污染 vault；兩個合起來 **vault 全部回到「人 + canonical 資料」狀態，Web UI 才是 LLM 互動 surface**。

---

## Fork C — Brook synthesize 流程順序

> 把 Brook 切成「先廣搜（divergent）→ 你 review（convergent judgment）→ 再 outline（generative）」三段，每段思考模式都不同。Gemini 說這是 **flow-breaker**：你進 review 模式時還沒看到 outline 結構，沒上下文判斷哪條 evidence 該留。

### 真實會出什麼事

**情境 1（sequential β / ADR-021 原案）**：

1. Brook 廣搜出 30 條 evidence → Web UI panel 顯示
2. **你看 30 條裸 evidence**（每條一個 highlight quote + 你當時 annotation）
3. 「這條 creatine on muscle protein synthesis 的 paper 該不該留？」— **你不知道**，因為你還沒看 outline 會怎麼用它
4. 直覺操作：**全 accept** → HITL gate 失效，等於沒做
5. Brook 跑 outline → 你看到「第 3 段引用了 evidence #7」想退回去 reject #7 → 又要再跑一次 outline

**情境 2（unified W）**：

1. Brook 一次出 evidence 30 條 + 草稿 outline 5 段（每段引用 5-6 條 evidence）
2. **你直接看到「第 3 段：肌酸對中老年認知功能 — 引用 evidence #7, #12, #15, #22」**
3. 對著 evidence 列表 + outline 段落 review：「evidence #7 對第 3 段不相關 → 從第 3 段拿掉」「evidence #15 應該升到第 1 段」
4. 按 finalize → Brook 重產一次 outline（依你的調整）
5. **review 是在結構脈絡內做的，每個判斷都有上下文**

### 兩個選項的白話

**(V) Sequential HITL**（ADR-021 原案 = β）
- 廣搜 → review evidence → outline 三段
- 中間 review 是 evidence-only context

**(W) Unified Synthesize + post-gen review**
- Brook 一次跑廣搜 + outline，輸出綁定（每段引用哪條 evidence 一目了然）
- 你 review 是 in context — 對著 outline 看 evidence
- Reject 顆粒度從「reject evidence」升級成「從 outline 第 X 段移除這條 evidence」（更細）
- 修改後 Brook 重 generate outline（**只重 generate outline，不重廣搜** — 廣搜結果 cached）

### 我的偏好

**(W)**。理由：

1. **跟人類 reading-while-writing 工作模式對齊** — 你描述的 Step 5「右螢幕寫稿、左螢幕看 evidence」就是 in-context review，C-fork 不過是把這 pattern 提早到 Step 3
2. **HITL 不會失效** — 看到 outline 結構你才知道哪條 evidence 真的有用、哪條被亂引；不在結構內 review 大概率全 accept = 假 HITL
3. **token cost 不增反降** — sequential HITL 下你 reject 後會重跑 outline、cost 等於 outline ×2；unified 一次 generate，後續調整只 regenerate outline (廣搜 cached)
4. **Codex 也說「one-shot 是現在的工作 shape，沒看到 sequential HITL 證據」** — Codex + Gemini 在這條都偏向反 ADR-021

我原本選 (V) 的時候沒給你 (W) 這個 framing — 我框題框錯。

---

## Fork D — 跨語言 retrieval（最大盲點）

> 你寫繁中 / KB 80% 是英文 paper / 書本是 bilingual。`kb_search` 走 BM25 + 單一 dense embedding，**根本沒有 cross-lingual** — 繁中 query 撈英文 paper recall 接近 0。Brook 廣搜在 bilingual corpus 上**現在就壞**。

### 真實會出什麼事

**情境**：你開「肌酸對中老年認知功能的影響」Project，topic = 繁中。Zoro 撈 keyword 通常英中混（`creatine`, `cognitive function`, `肌酸`, `老年認知`）。

- BM25 lane: 繁中字（肌酸、認知）只能命中**繁中內容** — 你的繁中 reflection 命中、英文 paper 命中 0
- Dense vector lane: 繁中 query 跟英文 paper 在同 embedding space 但**中英 cluster 距離極遠**（用一般 model）— 命中質量差
- 結果：Brook「廣搜 30 條」實際撈到的全是繁中內容（你的 reflection），英文 paper 一條都沒撈到 — **你最豐富的素材（KB 內幾百篇英文 paper）全部漏掉**

**這是隱形的** — Brook 不會報「我搜不到英文 paper」，它會自信回 30 條繁中結果，你以為廣搜成功了。

### 三個選項的白話

**(X) LLM 翻譯 query**
- Brook 廣搜前先把繁中 query 用 LLM 翻成英文（cheap），原 query + 英文 query 都跑
- 簡單、cost 加一次 LLM call
- **代價**：query 失去原語言色彩（「妙用」「中老年」這類詞翻成英文會丟細節）；KB 內**繁中 reflection 用英文 query 又撈不到**
- 半調子

**(Y) 換 multilingual embedding 模型**
- 把 dense vector lane 換成多語 model（如 `BAAI/bge-m3`、`sentence-transformers/LaBSE`）
- 中英 query 在同 embedding space 真的對齊
- 一次性 re-embed 全 KB（828 chunks 不算多、本機 GPU 跑幾分鐘）
- **長期解**：之後不管查中查英都自動跨語言
- **代價**：要驗 model 質量、Brook 可能要調 prompt（multilingual model 對 zh prompt 偏好不同）

**(Z) 雙 query 跑後合併**
- 繁中原 query 跑一次、LLM 翻譯版跑一次、兩邊結果合併去重
- 不換 embedding model
- **代價**：(X) 的問題還在，只是兩 query 比一 query 多撈一些；長期還是沒真 CLIR

### 我的偏好

**(Y) 多語 embedding 模型**。理由：

1. **這是真解** — 跨語言 retrieval 是現有解決方案（LaBSE、bge-m3），業界已驗證
2. **一次性工程，永久受益** — 之後 KB 規模大、加新語言、不用每次補翻譯
3. **不增加每 query LLM cost** — (X)(Z) 每次廣搜都要 LLM 翻譯 query，cost 累積
4. **KB index 重 embed 一次而已** — 本機 GPU 5070 Ti 16GB VRAM 跑 bge-m3 over 3000 chunks 估幾分鐘
5. **跟你 hardware 投資 (RTX 5070 Ti) 對齊** — 你買 GPU 就是為了這類本機 ML

(X) 半調子、(Z) 不解根本問題。

**這個 Fork D 該獨立成一個前置 ADR**（embedding model 切換是 architectural lock-in），不是 ADR-021 的 sub-decision。我寫 v2 時應該把 Fork D 拉成 ADR-022 的 dependency，而不是夾在 ADR-021 內。

---

## 組合套餐建議

我自己組裝後最一致的套餐：

| Fork | 我選 | 為什麼一致 |
|---|---|---|
| A | **(R) W3C 風單檔** | 沒有 drift，retrieve 直接讀 canonical |
| B | **(U) Sidecar server-side** | Vault 真乾淨；Brook 只跟 Web UI 互動 |
| C | **(W) Unified Synthesize + post-gen review** | 真 HITL，不假 review |
| D | **(Y) Multilingual embedding** | 唯一真解，獨立成 ADR-022 |

→ 三個結果：

1. **Vault 簡潔回升** — KB/Annotations 一檔、KB/Wiki/Syntheses 不存在、Project 頁面瘦
2. **Web UI 是 LLM 互動唯一 surface** — 跟你 stated ideal 對齊
3. **Retrieve 真的可運作** — 跨語言 + structured items 直接 indexable

**ADR-021 v2 範圍會大改**：
- 不再分 File 1/File 2（A=R）
- 不寫進 Project page（B=U）
- 流程從 3 段變 2 段（C=W）
- D 拆出去成 ADR-022「multilingual embedding migration」當前置 dependency

---

## 你判 4 條

A：(P) / (Q) / (R)？
B：(S) / (T) / (U)？
C：(V) / (W)？
D：(X) / (Y) / (Z)？

或者「全聽我建議的組合套餐 R/U/W/Y」？或哪幾條想先暫停 / 反推？
