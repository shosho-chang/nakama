---
name: youtube-dp
description: >
  YouTube 照稿影片的 DP 總調度：畫面規劃表（visual_plan.json）全部核准之後，把每一列交給對應分類的元件
  做成素材（`<episode>/visuals/`），做不出來的標「待製作」、要修修提供的標「等素材」，最後出一份結果報告。
  Triggers: /youtube-dp、「跑 DP」「規劃表核准了，開始做素材」「做這集的畫面」。
  不用在：Podcast 長精華（brook-dp）、短片（shortform-dp）、單一分類的設計討論（各 youtube-* skill）。
---

# youtube-dp — YouTube 照稿影片 DP 總調度

**版本：v0.1（2026-11-16）**

規格：`docs/plans/2026-11-15-youtube-script-video-rough-cut-and-director.md` §1（第 6 步 DP）、§3.8、§4.4–4.5。
每一類怎麼做、規則與檢查在各自的 skill：`youtube-chapter-card`、`youtube-section-title`、`youtube-concept-title`、
`youtube-person`、`youtube-book-cover`、`youtube-doc-highlight`、`youtube-quote-card`、`youtube-side-bullets`、
`youtube-stock`、`youtube-others-clip`，
畫面搬移在 `youtube-reframe`。本 skill 只管順序與交接。

## 流程

1. **確認全部核准**（規格 §4.4）：Bridge 上沒有待審、重排的列。正式流程**只做核准的列**。
2. **整集要下載的東西一次問**：先跑 `--dry-run`，再逐列看哪些要外部檔案，整理成**一張清單**給修修核准
   （每份：檔名、來源、大小）——不要一份一份打斷他。清單外臨時要下載的再另外問。
   - 書封：出版社或書店官方的平面封面圖有常設授權（`memory/claude/feedback_book_cover_download_authorized.md`），
     不用問；立體展示圖（有書背、白底）不能用。書庫有的書直接用（`data/books/<書名>/cover.*`）。
   - 人物照片：照 §3.8 順序找（Wikimedia 商用授權 → 官方 → 本人公開大頭照），要下載就列進清單；
     「出處」填本機照片路徑。
   - 文件 highlighter：原始文件（PDF／網頁列印成 PDF），列進清單；拿到後寫說明檔、出檢查圖（`youtube-doc-highlight`）。
   - 他人影片引用：修修在 Bridge 標「要」的片段（`clips download --dry-run` 列出檔名、來源、大小），列進清單（`youtube-others-clip`）。
3. **渲**：

   ```bash
   python -m agents.brook.youtube_video.dp "G:/Footages/<ep>" --dry-run
   python -m agents.brook.youtube_video.dp "G:/Footages/<ep>"
   ```

   一張約 30–90 秒；整集幾十張要在背景跑。收據對得上的會沿用，不重渲；`--category`、`--row` 只做部分、
   `--force` 強制重渲。`--include-pending` 讓待審的列也做——**只給流程測試用**，不會改規劃表的狀態。
4. **看報告** `<episode>/visuals/dp_report.json`：
   - `failed`：照訊息修（多半是字太長、缺檔、出處格式），修完只重跑那幾列。
   - `pending_build`：這一類還沒有元件（分類清單 readiness 不是 ready）。
   - `awaiting_material`：等修修提供（第 9、12 類、頻道動畫）。
5. 交給鋪時間軸（下一段）。修修在 Resolve 看過、在 Bridge 標重排寫原因 → 判斷回 Director（計畫錯）或回 DP（做得不好）。

## 交件（所有分類共用，`agents/brook/youtube_video/visuals/delivery.py`）

`<episode>/visuals/<row_id>.mov`（ProRes 4444 帶 alpha、29.97）＋ `<row_id>.json` 收據（分類、變數、`start_sec`、
`duration_sec`；章節卡多 `insert_sec`）。收據的 `start_sec` 是**規劃表的時間＝初剪時間軸的時間**。
