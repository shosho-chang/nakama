# ADR-051: Director 分鏡 skill — 創意層走 agent skill，苦力層留 pipeline

- **Status**: Accepted（修修 2026-07-05 grill 逐題裁決）
- **Amends**: ADR-032（storyboard planner 定位）、ADR-038（replan 迴路不變）、ADR-050（Brook video line 歸屬不變）
- **Related**: `agents/brook/script_video/CONTEXT.md`（詞彙表）、`docs/plans/2026-07-05-director-skill-plan.md`（實施切片）

## 決定

「讀校正後字幕 → 分鏡判斷 → 取得外部素材 → 產 storyboard.yaml」這一層創意工作，
做成 **Claude agent skill**（`.claude/skills/brook-director/`），不做成 pipeline 程式；
render / emit / cache / Bridge 審核等既有 pipeline 程式維持不動，由 skill 呼叫。
`plan` CLI 指令保留為 skill 內可選的初稿產生器。

## 為什麼

1. **v1 的 7 種 B-roll 類型裡有 3 種需要外部素材獲取**（Envato stock、KOL YouTube
   片段、文獻 PDF）— 搜尋 → 挑選 → 換關鍵字重搜 → 下載交接是多回合 agent 工作，
   單次 LLM call 的 `plan` 程式做不到。
2. **冷啟動零範例**（few-shot corpus 0/5，ADR-032 §9）。品味未成文前，固定 prompt
   一次到位的失敗成本高；skill 手冊「跑一集 → 教訓寫回手冊」是最快的品味累積迴路。
3. **不關掉未來自動化**：判斷穩定後可把規則結晶回 `plan` prompt（明確列為 roadmap，
   非本 ADR 範圍）。

**Rejected**: (a) 全部塞進 `broll_planner.md` 固定 prompt + 自動素材 worker —
創意迴圈無法程式化、冷啟動失敗成本高；(b) 混合雙入口（Hyperframes 類走 plan、
外部素材類走 skill）— 同一 storyboard 兩個寫入者，時間軸協調複雜。

## 裁決明細（D1–D10，修修逐題核可）

| # | 決策 | 內容 |
|---|------|------|
| D1 | Director 形態 | Skill（Claude 當導演）；機器苦力不動；穩定規則未來結晶回 plan prompt |
| D2 | Big Title Transition | 滿版章節卡（聲音不斷）；「第 N 點」句式必觸發、隱性語意章節從嚴判斷；時長錨定該句語音（1.5–3s） |
| D3 | Motion graphics 版面 | Director 依內容選，初期預設滿版；透明疊加待 alpha 輸出驗證（Hyperframes alpha → DaVinci import）通過才開放 |
| D4 | MG 素材庫成長 | 菜單優先；即席寫新 composition 必須存回 `video/compositions/` 成可重用資產＋過視覺審核 |
| D5 | Envato stock | 批次交接：`assets_queue.yaml` ＋現成 Codex prompt，一次下載全部，回來驗收續跑；挑選給首選＋兩備選（預覽連結入 storyboard） |
| D6 | KOL footage | 全自動（搜→字幕定位→抽幀確認→yt-dlp 下載指定秒數）＋護欄：單一來源取用總長上限 20s、storyboard 強制記出處、出處清單可自動生成 description |
| D7 | 文獻 highlight | 稿尾來源清單（DOI/連結一行一篇）；PDF 三層 fallback：KB → open access 自抓 → paywall 修修供檔；PyMuPDF 定位引用句 bbox → `doc_highlight` composition（縮圖→推近→黃 highlight）；**配對不到來源寧缺勿猜** |
| D8 | B-roll v1 類型 | 修修五種（big title / motion graphic / stock / doc highlight / KOL）＋書封卡＋金句卡；重點疊加字卡列 v1.5（alpha 驗證本次排入）；螢幕錄影＝外供素材槽位（修修供檔、Director 排版位） |
| D9 | 審核介面 | 沿用 Bridge UI `/brook/video` 兩層 HITL（text / visual approved），對話審當備援；UI 小改顯示新類型的來源與候選預覽 |
| D10 | 首次 E2E | 修修新拍的 RAW 一集串到底（mistake removal → Director → DaVinci）；缺的 B-roll 場景型別第二集補驗 |

## 頻道分析輸入（2026-07-05，@shoshotw 觀看前 10）

寫進 skill 手冊的節奏規則來源：

- 兩型影片節奏差 2.5–3 倍：**書籍型** ~2.4 次畫面變化/分、talking head 佔 ~37%
  （B-roll 為主體）；**健康型** ~1 次/分、talking head 佔 ~65%（B-roll 為證據點綴）。
  Director 開工先分型、套不同節制預算。
- 觸發語意：提研究 → 文件截圖；提人名/名言 → 照片/金句卡；抽象感受 → stock 實拍。
- 開場：書籍型 ~15s 內進首個 B-roll；健康型可至 30s+。
- 報告：scratchpad `shoshotw_channel_analysis.md`（ephemeral；規則已收進 skill 手冊）。

## Consequences

- storyboard schema 需擴充：`render_target` 增 `asset`（外部素材：stock / KOL /
  screen_recording / 外供檔），`component` 增 `transition_title` / `book_cover` /
  `quote_card` / `doc_highlight`；guardrails 詞彙同步。
- 出現「素材等待」狀態：storyboard 卡在 Codex 下載完成前 — skill 需有明確
  resume 點與驗收步驟。
- Bridge UI 需認得 `asset` 類 beat（顯示來源連結、候選預覽、出處）。
- KOL footage 的編輯責任（引用分寸）屬修修編輯決策；系統責任＝護欄與出處留痕。

## Panel review v2 修訂（2026-07-05，Codex + Gemini 3-way）

兩份外部審計皆 approve-with-modifications（原文：`docs/research/2026-07-05-codex-adr051-audit.md`、
`2026-07-05-gemini-adr051-audit.md`；整合矩陣：`2026-07-05-adr051-panel-integration.md`）。
修修逐項簽核後的修訂：

1. **D1 重framing（選擇不變）**：skill 是導演（orchestrator + 品味載體），但契約歸
   deterministic 工具 — 每集跑完必寫 run log（搜尋詞、候選、否決理由、skill 版本），
   schema/檔名慣例只能經 PR 建立、skill 不可即席發明。（Codex #1；Gemini 反對其
   嚴重度但接受紀律）
2. **D5 交接物拆雙檔**：`asset_requests.yaml`（意圖：搜尋詞/時長/氛圍/負面約束）＋
   `asset_manifest.yaml`（履約：落地路徑/sha256/幀率檢查/license 註記）。
   Codex-computer-use 下載降為實作細節，可換人工/別的工具。（Codex #3、Gemini #4）
3. **D7 跨語言修正（3-way 共識）**：撤回「PyMuPDF 對中文引用句全自動定位」——稿是
   中文改述、論文是英文，精確比對不成立。改為：Director 讀論文**選定英文原句** →
   PyMuPDF 定位該句 bbox → Bridge 審核顯示「中文改述 ↔ 英文原句」配對供修修確認。
   **doc_highlight composition 緩到 v1.1**（修修裁決）。
4. **D8 v1 範圍居中版（修修裁決）**：schema 七類維持；PR-C compositions =
   `transition_title`＋`book_cover`＋`quote_card`；doc_highlight 延後。
   Gemini「縮到三類」否決 — 頻道分析顯示書封卡/金句卡為書籍型最高頻視覺。
5. **新增 video visual grammar 前置（Gemini #2）**：PR-C 開工前在 STYLE.md 增訂 —
   外部素材處理規則（KOL 片段固定邊框、stock 調色原則）、動效節奏、
   **字幕禁飛區**（常駐繁中字幕區域不放關鍵視覺資訊）、幀率 conform 到 30fps。
6. **章節卡時長規則修正（Gemini）**：`duration = max(語音時長, 標題最低可讀時間)`，
   不是單純錨定語音。
7. **跨語言搜尋詞生成入手冊（Gemini #3）**：中文概念 → 英文 Envato/YouTube 搜尋詞
   是必要步驟，SKILL.md 須含查詢擴展指引（一個概念出多組不同切面的英文詞）。
8. **節奏數字降為 heuristic（Codex）**：兩支影片 15 秒抽樣是假設不是政策；另修
   guardrail(4/min) 與 planner prompt(1.5–2.5/min) 的數字互斥 — 以 prompt 預算為準。
9. **明寫授權假設（Gemini）**：D5 假設 Envato **Elements 訂閱制**（吃到飽，candidates
   多下載零邊際成本）；若改單購模式需重審 D5 挑選流程。
10. **已修 bug（Codex 抓出，PR #988）**：export_hash 預設路徑 ADR-050 搬遷後全斷；
    Bridge promote-to-example 寫進死目錄 `agents/foundry/examples`；
    AssetSpec.sha256 digest 驗收堵 asset 檔案替換 staleness 洞。
11. **guardrails enforcement 缺口（Codex）**：`validate-storyboard` CLI 列入實施計畫
    （hard limits 目前無 code 強制，僅 prompt 約定）。
