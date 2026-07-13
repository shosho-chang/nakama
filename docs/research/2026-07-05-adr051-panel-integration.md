# ADR-051 panel 整合矩陣（Claude v1 × Codex × Gemini）

- Date: 2026-07-05
- Inputs: [Codex audit](2026-07-05-codex-adr051-audit.md)、[Gemini audit](2026-07-05-gemini-adr051-audit.md)
- 兩份 verdict 皆為 **approve with modifications**（Codex 5 修、Gemini 4 修）

## 矩陣

| # | 議題 | Claude v1 | Codex | Gemini | 型態 | 處置 |
|---|------|-----------|-------|--------|------|------|
| 1 | D7 文獻 highlight 跨語言 | 「PyMuPDF 定位引用句，全自動」 | 稿=中文改述、論文=英文，精確比對不成立；要求記錄英文原句+人工確認 | concur + 加碼：字幕與英文頁面同屏認知負荷 | **3-way** | **採納**：Director 讀論文選定英文原句 → PyMuPDF 精確定位該句 → Bridge 審核確認；doc_highlight 進字幕禁飛區規則 |
| 2 | D5 交接物設計 | 單一 assets_queue.yaml | 拆 asset_requests（意圖）/ asset_manifest（履約+digest+license），Codex-computer-use 降為實作細節 | concur（human-in-middle 仍脆，但拆分較好） | **3-way** | **採納**：request/manifest 雙檔；manifest 驗收寫回 sha256（PR-B 已落 schema） |
| 3 | D1 skill vs 程式 | skill 當導演 | 反對框架：「creative 不可程式化」為假；要求 skill 只 orchestrate、契約歸 deterministic 工具＋run log 留痕＋skill 版本化 | 反對 Codex 嚴重度：solo creator 品味累積 > 可重現性，skill 是核心資產不是負債 | **分歧** | **修辭重framing 採納、選擇不變**：skill 保持導演；新增硬規則 — 每集跑完寫 run log（搜尋詞/候選/否決理由/skill 版本），schema 慣例只能經 PR 建立不可即席 |
| 4 | 節奏數字 | 2.4/min vs 1/min 寫進手冊 | 兩支影片 15 秒抽樣＝假設非政策；另抓 prompt(1.5–2.5/min) vs guardrail(4/min) 數字互斥 | 未反對 | **2-way** | **採納**：手冊寫成 heuristic（健康型低密度預設、書籍型高密度容許）；guardrail 上限對齊 prompt 預算 |
| 5 | export_hash 死路徑 | 未察覺（Claude 實作中獨立發現） | 同步抓出 | — | **2-way** | **已修**（PR #988） |
| 6 | promote-to-example 死目錄 | 未察覺 | 抓出（agents/foundry/examples） | — | 單源(驗證屬實) | **已修**（PR #988） |
| 7 | guardrails 無 enforcement | 未察覺 | 「enforced by validator」是空話，無 code | — | 單源(驗證屬實) | **採納**：計畫新增 `validate-storyboard` CLI（納入 PR-B 後續或 PR-E 前置） |
| 8 | D8 v1 七類型範圍 | 修修裁決 5+2 | Alternative 3 建議 v1 縮水 | 主修 #1：縮到 3 類（transition_title/bigstat/generic_asset），quote_card 等讓真實需求長出來 | **2-way 質疑用戶裁決** | **上呈修修**（本文件 §裁決） |
| 9 | 視覺一致性 / Visual Brand Guide | 未著墨 | 未著墨 | 主修 #2：四種視覺來源會拼裝怪；要求 video 版 brand 規則（外部素材處理、KOL 邊框、動效節奏）先於 PR-C | 單源(有理) | **採納**：STYLE.md 增「video visual grammar」節，PR-C 前置 |
| 10 | 跨語言搜尋詞生成 | 未著墨 | 未著墨 | 主修 #3：中文概念→英文搜尋詞是關鍵創意步驟，手冊須有專門段落+查詢擴展 | 單源(有理) | **採納**：進 SKILL.md 必要步驟 |
| 11 | 章節卡可讀時長 | 錨定語音 1.5–3s | — | max(語音時長, 最低可讀時間)；長中文標題 1.8s 讀不完 | 單源(有理) | **採納**：`duration = max(speech, min_readable(len(title)))` |
| 12 | 字幕禁飛區 | 未著墨 | — | 常駐繁中字幕與 B-roll 文字/highlight 疊撞 | 單源(有理) | **採納**：compositions 底部 safe zone；doc_highlight 期間字幕處理策略記入手冊 |
| 13 | 幀率混流 | 未著墨 | — | A-roll/stock/KOL 幀率不一 → judder；30fps conform 步驟 | 單源(有理) | **採納**：manifest 驗收含 ffprobe 幀率檢查，非 30fps 先 conform |
| 14 | Envato 授權模式假設 | 隱含吃到飽 | — | 訂閱 vs 單購影響流程設計 | 單源 | **採納（低成本）**：ADR 明寫假設 Elements 訂閱制（MCP 即 Elements）；若改單購需重審 D5 |
| 15 | asset staleness | 未著墨 | 檔案替換沿用過期審核 | — | 單源(有理) | **已修**：AssetSpec.sha256 + dispatcher digest 驗收（PR #988） |

## 裁決紀錄（修修 2026-07-05）

- §8 D8 範圍：**採居中版** — schema 七類維持（PR #988 已落地）；PR-C compositions 做章節卡＋書封卡＋金句卡；**doc_highlight 緩到 v1.1**（跨語言英文原句配對流程等第一集跑完再實作）。Gemini 激進三類版否決（頻道分析顯示書封卡/金句卡為書籍型最高頻視覺）。
