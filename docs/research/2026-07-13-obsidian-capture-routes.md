# 四條材料匯入路線 — Obsidian Capture 指南（對外可攜版．最終版 2026-07-15）

> 給沒有 Nakama 系統的使用者：如何把「文章 / YouTube / Podcast / Kobo 電子書」的
> 劃線與註記匯進自己的 Obsidian vault。每條路線列出要裝什麼、怎麼設定、實際操作流程，
> 以及社群目前的實作現況（2026-07 查證）。
>
> 落檔形狀對應 [ingest capture-profile 補丁](../plans/2026-07-15-ingest-capture-profile-patch.md)：
> **Shape A = 有全文**、**Shape B = 只有劃線集**；四條路線各對一個 `capture_profile`，ingest 依此解析。
>
> 最終版更正（vs 2026-07-13 初稿）：① 文章改「整篇 clip → 在 Obsidian 劃線註記」（route B）；
> ② YouTube 改 **Media Extended v4 自帶下載字幕 + 跟讀高亮 + 逐字稿複製行**，不再用 YTranscript
> 組合（Obsidian 確實做得到卡拉OK式同步高亮，初稿說沒有是錯的）；③ Kobo 即使商店書也推 plugin。

## 總覽

| 路線 | `capture_profile` | 主推薦工具 | 費用 | 落檔形狀 | Locator |
|---|---|---|---|---|---|
| ① 文章 | `article-web` | Obsidian Web Clipper（整篇 clip）+ 在 Obsidian 劃線 | 免費 | **A** | 段落 |
| ② YouTube | `youtube-transcript` | Media Extended v4（下載字幕 + 跟讀 + 逐字稿複製行） | 免費 | **A** | 時間戳 |
| ③ Podcast | `podcast-snipd` | Snipd app + 官方 Obsidian plugin | 免費（AI free tier 每週 2 集） | **B** | 音檔連結 |
| ④ 電子書 | `ebook-kobo` | Kobo Highlights Importer（USB 讀 sqlite） | 免費 | **B** | 章節 |

**共同終點**：材料落進 `Inbox/`，劃線 `==...==`、想法寫劃線下一行 `note:: 想法`，工具的定位資訊
（時間戳、章節）原樣保留。之後 `/ingest` 一律吃得動。

---

## 路線 ① 文章 — Web Clipper 整篇 clip，在 Obsidian 劃線註記

**為什麼是這條（route B）**：官方 Web Clipper 的 highlighter **只能劃線、不能在劃線當下寫想法**
（[GitHub roadmap](https://github.com/obsidianmd/obsidian-clipper) 把「Annotate highlights」列為未來功能，
還沒實作）。既然想法反正都要在 Obsidian 補，就**整篇抓下來、劃線和註記全在 Obsidian 一次做**——
跟後面 YouTube 逐字稿的體驗一致，也零情境切換。

### 安裝與設定（一次性）

1. 瀏覽器商店裝 [Obsidian Web Clipper](https://obsidian.md/clipper)（Obsidian 需 ≥ 1.7.2；
   Chrome / Safari / Firefox / Edge / Brave / Arc 全支援）。
2. 擴充功能 → 齒輪 → **General** 連接 vault。
3. Template：**Note location** 改 `Inbox/`；**Note name** 保持 `{{title}}`。
   —— highlighter 那三個選項**不用管**（我們不在瀏覽器劃線），預設整篇 clip 即可。

### 日常操作

1. 讀到想收的文章，按 Clipper → **Clip**，整篇正文落進 `Inbox/`。
2. 在 Obsidian 打開該筆記閱讀，**選取要劃的句子 → 用 `==...==` 包起來**（Obsidian 原生 highlight 語法）。
3. 想法寫在該劃線**下一行** `note:: 你的想法`。
4. （選配）嫌手動包 `==` 慢：裝 [Sidebar Highlights](https://www.obsidianstats.com/tags/comment) 之類的
   標註 plugin，選取 → 快捷鍵一鍵劃線；但**想法仍寫成 `note::`**（跟 ingest 解析對齊，別用 plugin 的
   footnote 註解格式）。

落檔是 **Shape A**（全文 + inline 劃線）。

---

## 路線 ② YouTube — Media Extended v4（下載字幕 + 跟讀 + 逐字稿複製行）

**社群現況與選型**：2025 論壇對影片筆記[沒有單一共識](https://forum.obsidian.md/t/taking-notes-while-watching-video-in-2025/96837)。
我們選 [Media Extended v4](https://mx.aidenlx.site/docs/) 單一 plugin（**不再搭 YTranscript**），因為 v4
自己就能下載 YouTube 字幕、播放時**逐字稿跟讀高亮**（[Read Along tutorial](https://mx.aidenlx.site/docs/v4/tutorials/read-along-with-transcript)：
「播放時逐字稿高亮當前行並自動捲動」），而且**複製逐字稿的行會自帶時間戳連結** —— 一個 plugin 收齊。

### 安裝與設定（一次性）

1. Obsidian → Community plugins → 裝並啟用 **Media Extended**。
2. **設 Main Daemon**：指令面板（`Ctrl+P`）跑 **「Open main daemon setup wizard」**，照精靈設一次
   （本地播放器 + 下載字幕靠它）。不想設 daemon 的替代路：啟用 **Web Viewer Integration**，用 Obsidian
   內建瀏覽器看片一樣能下載字幕。

### 日常操作

1. `Ctrl+P` → **「Open media quick switcher」** → 貼 YouTube URL → 影片在 player 分頁開啟。
2. **下載字幕**：player 選單按鈕 → hover **「Download subtitles」** → 選字幕軌 → 它下載、存進 vault、
   自動開逐字稿面板。（下載失敗＝YouTube 要 token：把 CC 字幕鈕開一下、等幾秒、關掉再試。）
3. 把 player 分頁拖到筆記旁邊，播放 —— **逐字稿跟著高亮當前行**，你邊看邊跟讀。
4. **捕捉台詞 + 時間戳**（主力動作）：在逐字稿面板點那一行 → `Ctrl+C` → 貼進筆記，自帶格式
   `[08:38](url#t=NNN) 那句台詞`。後面接 `note:: 你的想法`。（要純引用不要連結用 `Ctrl+Shift+V`。）
5. **只標時刻、不抓台詞**（例如「這裡出現一張圖」）：用 player 的星星鈕
   **「Take timestamp in last active note」**，它只插時間戳、你自己打描述。

> ⚠️ 星星（Insert Timestamp）**不會**自動帶台詞——那要靠第 4 步「逐字稿面板複製」。兩者是不同動作。

落檔是 **Shape A**（下載的完整字幕 = 全文；捕捉的行帶時間戳 locator）。

**可靠度**：YouTube 2026 初改過 caption API，若某天下載不到字幕，先更新 Media Extended 版本；
最後手段是 YouTube 頁「顯示逐字稿」手動複製貼進筆記。

---

## 路線 ③ Podcast — Snipd + 官方 Obsidian plugin

**社群現況**：Snipd 是目前唯一有官方 Obsidian 整合的 podcast app
（[2025 底上架](https://forum.obsidian.md/t/snipd-plugin-to-sync-your-podcast-highlights-to-obsidian-with-transcript-ai-summary-and-rich-metadata/108426)）；
Airr 已死、Apple/Spotify 無匯出。Podcast 音檔不像 YouTube 有現成字幕，Obsidian 端無免費轉錄路，
所以走 Snipd。

### 安裝（一次性）

1. 手機裝 Snipd（iOS/Android），註冊**免費帳號**，訂閱節目搬過來（支援 OPML 匯入）。
2. Obsidian → Community plugins → 裝啟用 **「Snipd Official」**。
3. Plugin 設定 → **Connect** 登入（[官方指引](https://support.snipd.com/en/articles/12750692-sync-your-snips-to-obsidian)：
   連結用系統瀏覽器開，不要 Obsidian 內建瀏覽器）→ 同步資料夾設 `Inbox/` → **Start syncing**。
4. （建議）自訂 snip template：把「個人筆記」欄 render 成 `note:: ...`，跟其他路線對齊。

### 日常操作

1. 聽到重點按 snip 鈕 —— Snipd 擷取該片段（逐字稿節錄 + AI 摘要 + 音檔跳轉連結），想法當場語音/打字附註。
2. 新 snip 自動同步進 vault（**一集一檔**，該集 snips 集中）。可設「只同步編輯過/加星的 snips」，
   避免隨手 snip 灌進 vault。

**費用**：plugin 與同步免費；Snipd free tier 的 AI（自動標題/摘要）限每週 2 集，重度用要 premium。
落檔天生 **Shape B**（片段節錄，無全集逐字稿）。

**兩個延伸**：
- Snipd 2026 更新後也能**匯入 YouTube**——若你習慣手機看片、想一個工具收所有影音，YouTube 也可走 Snipd
  （但就變 Shape B）。桌機看片仍建議路線 ②。
- **不換 app 的 fallback**：Media Extended 播 podcast 的 mp3 URL，邊聽邊插時間戳 + 手打筆記，零新帳號。

---

## 路線 ④ 電子書 — Kobo Highlights Importer（USB）

**社群現況與選型**：長年標準是讀 Kobo 的 `KoboReader.sqlite`
（[community plugin](https://github.com/OGKevin/obsidian-kobo-highlights-import)）。Kobo 2025-09 的
內建匯出[實測不穩](https://blog.the-ebook-reader.com/2025/09/18/kobo-now-supports-exporting-annotations/)
且只支援商店書。**即使你的書都是商店買的，仍推 plugin**——理由：讀完一本接一次 USB 的摩擦極小、免費、
輸出格式自己控（可 render 成 `note::`）；商店書和 sideload 書通吃。

### 主推薦：Kobo Highlights Importer（免費）

安裝（一次性）：
1. Obsidian → Community plugins → 裝啟用「Kobo Highlights Importer」。
2. 輸出資料夾設 `Inbox/`。
3. （建議）Eta.js template 把「附註」欄 render 成 `note:: ...`
   （變數有 `highlight.text` / `highlight.note` / `dateCreated` / `color`）。

日常操作（讀完一本或想同步時）：
1. USB 接上 Kobo，等電腦掛載。
2. Obsidian 按 plugin 的 import 鈕，選裝置裡 `.kobo/KoboReader.sqlite`（`.kobo` 是隱藏資料夾，
   第一次要開「顯示隱藏檔案」）。
3. 匯入 —— 一本書一檔：書名/作者/ISBN frontmatter + **依章節分組**的劃線 blockquote + 附註。

落檔 **Shape B**（劃線集，無書全文——版權因素是常態，ingest 的 B 路徑會誠實標示）。

### 為什麼不用 Readwise（即使商店書）

[Readwise Kobo 整合](https://docs.readwise.io/readwise/docs/importing-highlights/kobo)的雲端自動同步
**只吃商店書**（sideload 書一樣得接 USB），而且要付月費（約 US$8/月起）。對「一兩週讀完一本」的頻率，
它省下的只是「免接 USB」，卻要訂閱。**只有在**你所有書都商店買、討厭接 USB、又願意付費、或本來就想用
Readwise 收齊全來源時才划算。**次選**是 Kobo 內建 Export（Markdown，Kobo.com 下載，30 天刪，限商店書）——
能用就用、不能用回主推薦。

---

## 落檔後的統一 convention ＋ 對 ingest 的 profile 對照

| 項目 | 規則 |
|---|---|
| 落點 | `Inbox/`（等 `/ingest` 處理） |
| 劃線 | 文章/YouTube 用 `==...==`（quote）；Podcast/Kobo 維持工具輸出的 snip / blockquote |
| 個人想法 | 對應劃線下一行 `note:: 想法`（Snipd / Kobo importer 用 template 對齊此格式） |
| 定位資訊 | YouTube 時間戳、Kobo 章節、Podcast 音檔連結——工具原樣保留，ingest 存進 Literature Note 引文行 |

四條路線對到 ingest 的 `capture_profile`（ingest 依此判 Shape 與解析 locator）：

| 路線 | `capture_profile` | Shape | ingest 怎麼解 |
|---|---|---|---|
| 文章 | `article-web` | A | inline `==...==` → quote；段落序位 → locator；全文存 Raw |
| YouTube | `youtube-transcript` | A | `[MM:SS](…#t=) 台詞` → quote + 時間戳 locator；完整字幕當全文 |
| Podcast | `podcast-snipd` | B | snip 節錄 → quote；音檔連結 → locator；AI 摘要另標 |
| 電子書 | `ebook-kobo` | B | blockquote → quote；章節·序位 → locator |

之後的流程交給 Librarian plugin：`/ingest` 判 profile 與 Shape → 保留 locator render Literature Note →
Source 摘要 → Concept/Entity 提案（使用者裁決）→ KB。詳見
[ingest 補丁](../plans/2026-07-15-ingest-capture-profile-patch.md)與
[skills 規格](../plans/2026-07-13-portable-librarian-skills-spec.md)。
