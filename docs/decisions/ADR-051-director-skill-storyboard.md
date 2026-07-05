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
