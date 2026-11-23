# 主題直播 → 字幕版課程：整體計畫

> 2026-11-19 與修修討論後定案的流程與切分。決策都標了出處（修修原話或程式碼位置）；
> 標「待查證」的是還沒讀到證據的事，動工那一段要先查，不能照寫的做。

## 目標與完工定義

修修下一個指令後，一場主題直播的 Zoom 錄影會依序經過下列處理：

1. 字幕經過校對（走 podcast 的 Memo 雙重審核流程）；
2. 字幕燒進影片；
3. 影片上傳到 Bunny Stream（library 579407），轉檔完成；
4. 在社群「直播錄影」課程新增一課，影片用 FluentPlayer 播放；
5. 課程頁附上這場直播主題的介紹文，由修修過目後才上架。

**完工**＝真的拿一場直播跑完全程，修修在 `fleet.shosho.tw/deck/course/recording/lessons`
用會員身分看得到新的一課，影片能播、字幕正確、介紹文已核准。只有程式結構正確不算完工。

## 已定案的決策（2026-11-19 修修）

| # | 決策 | 出處 |
|---|---|---|
| D1 | 錄影來源：Zoom 本機錄影，放在 `E:\Company\07_自由艦隊營運\recording\<YYYY-MM-DD HH.MM.SS 主題>\`（`video*.mp4`、`audio*.m4a`、`chat.txt`、`zoom-transcript.vtt`） | 修修原話；資料夾實測 |
| D2 | 上課程的是**整場直播**（不是只上中段第二機位） | 修修原話 |
| D3 | 每場直播在既有「直播錄影」課程（slug `recording`，space 21）**新增一課** | 修修原話＋`fleet_gamification_stack.md:103` |
| D4 | 校對參考：投影片或書稿；另要**維護一份專有名詞表**，包含會員名字 | 修修原話 |
| D5 | 製作（字幕、燒字幕）放 **nakama**；發布（Bunny、建課、介紹文）放 **freedom-fleet** | 修修同意建議 A |
| D6 | 字幕走 **podcast 的 Memo 雙重審核流程**（ADR-063） | 修修選 B 的選項 1 |
| D7 | 上課程的版本**剪掉頭尾**（等待、試音、收尾閒聊）；剪接點由 agent 抓，在上架前過目時一起確認 | 修修 2026-11-19「頭尾剪掉沒有問題」 |
| D8 | Bunny Stream API 金鑰放本機 `E:\freedom-fleet-runtime\.env` 的 `BUNNY_STREAM_API_KEY`（修修自己貼，已確認有設定） | 修修原話 |
| D9 | 授權用 SSH 到 VPS 做唯讀查詢 | 修修原話 |

### S4 的決策（2026-11-20 起）

| # | 決策 | 出處 |
|---|---|---|
| D10 | 指令入口採**做法二＋護欄**：私訊 Sanji 一句話 → freedom-fleet 在桌機背景用 Claude Agent SDK 照 `livestream-course` skill 跑（工作目錄 `E:\nakama`） | 修修 2026-11-20 選 |
| D11 | 護欄＝白名單（hook）：只能寫 episode 資料夾與 `recording\術語表.md`；Bash 只能用 `.venv-v2` 的 python 跑 7 支 script（`run_audio_prep`、`podcast_subtitle_v2_evidence`、`podcast_subtitle_v2_simple_step7`、`podcast_subtitle_release`、`livestream_relisten`、`livestream_subtitle_burn`、`livestream_slides_text`），另可 ffprobe／ffmpeg（路徑限 episode 內）；指令不能有 `; & \| < >`、反引號、`$(`；沒有 git、pip、`python -c` | S4 設計 |
| D12 | 關卡改成**結束這一輪＋resume**：Claude 回 structured output（`ask`／`artifacts_ready`／`failed`），程式私訊修修；他回覆後用同一對話接回（prompt「修修回覆：<原文>」）。關卡兩個：字詞（查遍無解才問）、審核包（剪接點＋介紹文＋章節＋90 秒試片） | S4 設計；規則在 `.claude/skills/livestream-course/references/unattended.md` |
| D13 | 上傳 Bunny、建草稿由固定程式在關卡 2 核准、Claude 回 `artifacts_ready` 後做；**上線**由固定程式在修修說「可以上線」時做。Claude 不碰發布 | S4 設計 |
| D14 | MVP：建成草稿之後**不接受改稿** | S4 設計 |
| D15 | `E:\nakama` 每次背景執行前先 `git pull --ff-only`，讓背景 Claude 讀到最新的 skill 與 script | S4 設計 |

## 社群站上的實際結構（2026-11-19 SSH 唯讀查詢）

- 「直播錄影」課程（space 21）裡，**每場直播是一個章節（`course_section`）**，標題格式
  `YYYY-MM-DD 【X月主題】標題` 或 `YYYY-MM-DD X月讀書會《書名》`；章節底下放 `直播錄影`（影片）、
  `投影片以及補充資料`／`課程投影片`（附 PDF）等課。
- **介紹文寫在影片那一課的內文**，放在播放器 block 下面。有寫的範例：lesson 86（摘要＋附時間點的段落清單）、
  lesson 184、185（三到五段第三人稱敘述，例如「這場直播前半段，張修修先從……」）。
  多數近期的課只有播放器、沒有介紹文。
- 4 月兩場（lesson 255、258）完全沒有影片。
- 社群活動（FluentEvent，`fe_event_details`）的 `meta` 裡存了 Zoom 會議網址；今天的「AI Agent 分享會」
  （fe_event post 9972）對應的 Zoom 會議編號就是錄影那場 `84546629824`。**這是分辨直播和會議的依據**（見 S0）。

## 先擋住的問題：直播會被當成會議

會議紀錄排程（`E:\freedom-fleet-runtime` 的 `fleet.minutes`，每 5 分鐘一次）會掃同一個錄影根目錄，
**每一個符合「日期 時間 主題」格式的資料夾都會被當成內部會議**
（`fleet/minutes/meetings.py:24`）。程式沒有任何區分直播和會議的條件。

2026-11-19 的「AI Agent」直播已經被它接走（log：`waiting-transcript 2026-11-19 18.52.50 AI Agent`）。
經修修同意，在該資料夾放了兩個標記檔先擋住：

- `結案報告.md`：讓程式認定這場已處理完（`meetings.py:40-41`）；
- `.minutes-notified`：只有 `結案報告.md` 的話，程式會**送給曉宣審**（`cli.py:126-129`），所以要再加這個。

這只救這一場。**第一段（S0）必須在會議紀錄程式裡區分直播和會議**，否則每場直播都會重演。

### S0 的判別方式

錄影資料夾 → 會議紀錄程式本來就會對到 Zoom 上實際舉行的那一場（`meetings.find_instance_uuid`）→
拿那場的 **Zoom 會議編號**去問社群站：有沒有哪個社群活動的會議網址是這個編號？

- 有 → 這是社群活動（直播／讀書會），**不寫會議紀錄**，在資料夾留一個 `.livestream.json`
  （活動編號、活動標題、開始時間、Zoom 會議編號），交給直播流程；
- 沒有 → 照舊當內部會議處理；
- 問不到（VPS 連不上、站台錯誤）→ **這次先不處理**，下次排程再問。寧可晚寫會議紀錄，也不要把直播寫成會議紀錄。

查詢路徑照既有慣例：本機 SSH → VPS 上的 freedom-fleet → 外掛 REST（架構鐵則 4：production 讀寫一律走 REST）。
外掛目前沒有讀取活動的端點，要新增一個唯讀端點。

不採用的做法：建立 Zoom 會議時在 agenda 蓋標記。理由：要同時改 nakama（Sanji Slack 流程）和 freedom-fleet，
已經建好的三場要回填，而且在網站上手動建的活動抓不到。

## 流程（八步）與現況

| 步 | 做什麼 | 在哪個 repo | 現成的 | 缺的 |
|---|---|---|---|---|
| 1 | 收到指令、認出哪個資料夾是直播 | freedom-fleet | `fleet.local` 本機排程、`fleet.minutes.meetings.discover()` | 直播／會議的判別（S0） |
| 2 | m4a → normalized.wav（Auphonic） | nakama | `scripts/run_audio_prep.py --audio <path>`（`:213`） | 直播資料夾 → episode 資料夾的對應方式 |
| 3 | Memo 辨識 → 雙重文字審核 → 雙 ASR → release SRT | nakama | `scripts/podcast_subtitle_release.py`（init/status/seal/finalize） | podcast 專屬前提要拆掉（見下方「Memo 套到直播的差異」） |
| 4 | 套字幕規則 | nakama | 字幕規則（《》「」保留、cue_builder 斷句） | Memo 產線尚未落實《》「」（`feedback_subtitle_house_style`），seal 前要補 |
| 5 | 燒字幕＋實際播放抽查 | nakama | `scripts/publish_prep.py:352` 的 ffmpeg 燒字幕 | 橫式長片字幕樣式（現有的是直式短片樣式） |
| 6 | 上傳 Bunny、等轉檔、取片長和縮圖 | freedom-fleet | 無（舊遷移工具只讀 Bunny，透過 FluentPlayer Pro 的 `BunnyCDNService`） | 本機直接上傳的程式；**Stream API 金鑰**（見「待辦」） |
| 7 | 新增一課，掛 FluentPlayer media | freedom-fleet | 三層寫入的形狀已實測（`wp/fleet-gamification/tools/bunny-media-migrate.php`）；本機 → VPS 用 SSH（`fleet/minutes/remote.py`） | 「**新建**一課」的程式（舊工具只改過既有的課） |
| 8 | 寫介紹文 → 修修過目 → 上架 | freedom-fleet | Sanji 的 Slack 私訊／核准模式（`fleet.minutes` 送審流程） | 介紹文的寫法要先看過去的課（待查證：課程頁要登入） |

### Memo 套到直播的差異

podcast 流程的前提（`.claude/skills/podcast-pipeline/SKILL.md` 的「Standing authorization」與 S0）：

- 三軌音檔：`Audio/Live-Mix.wav`、主持人 `1_COMBO-1.wav`、來賓 `2_COMBO-2.wav` → **直播只有一軌 m4a**；
- 來賓身分要交叉確認 → **直播沒有來賓**，但有會員發言（Q&A）；
- `episode_id` 必須等於 episode 資料夾名稱 → 直播資料夾名稱有空格和時間，要決定 episode 資料夾放哪、叫什麼；
- Stage 5 之後的 Resolve／Editorial Master 是 podcast 剪輯用 → **直播不進 Resolve**，release SRT 直接交燒字幕。

**待查證**：`podcast_subtitle_release.py` 在單軌、無來賓的輸入下能不能跑；哪些檢查寫死了 podcast 結構。
這是第二段（S2）的第一件事。

## 專有名詞表

- **位置**：`E:\Company\07_自由艦隊營運\recording\術語表.md`，跟會議紀錄共用同一份。
  理由：同一批人名（修修、曉宣、會員）兩邊都會用到，一份比較好維護。會員名字是個資，**不進任何 git repo**。
- **會員名字的來源**：每場直播的 `chat.txt` 發言者，加上社群站的會員名單（待查證：用哪個欄位、要不要只收有參加直播的人）。
- **維護方式**：每場直播跑完，把新出現的人名、校對時改過的專有名詞追加成新的一列，送修修過目時一併列出新增了哪些。

## 分段（每段都能單獨驗收）

| 段 | 內容 | 驗收 |
|---|---|---|
| **S0** | 會議紀錄程式區分直播和會議；直播資料夾不再被寫成會議紀錄 | 新的直播資料夾放進來，會議紀錄排程 log 顯示跳過；既有週會照常處理 |
| **S1** | 發布端：拿一支已有字幕的影片，跑通「上傳 Bunny → 等轉檔 → 新建一課（三層寫入）」，先建成未公開的課 | 修修用會員帳號看得到這課、影片能播；FluentPlayer 判定片長來源是 `server` |
| **S2** | 製作端：用 2026-11-19「AI Agent」直播跑 Memo 流程 → 燒字幕 | 修修實際看過燒好字幕的影片 |
| **S3** | 介紹文＋修修過目關卡，串起 S1、S2，完整跑一場 | 課程頁出現新的一課，介紹文是修修核准的版本 |
| **S4** | 指令入口（Slack 叫 Sanji） | 一句話觸發，跑完 Sanji 回報 |

## 進度（2026-11-20）

- **S0 已上線**：freedom-fleet #22（會議紀錄程式拿 Zoom 會議編號問社群站，社群活動留 `.livestream.json`）。
- **S1 已上線**：freedom-fleet #23（`python -m fleet.course upload / create-lesson / publish`；外掛
  `POST /course-lessons`、`/course-lessons/{id}/publish`，權限 `fleet_course_publish`）。正式站實測過、測試資料已刪。
- **S2 進行中**（nakama 本分支）：
  - Auphonic 單一帳號額度只有 2 hr、直播 2.15 hr → 修修選「不送 Auphonic」：`run_audio_prep.py --local-decode`。
  - Memo Dual-Audit 程式本身不需改：單軌、無來賓直接跑通到 `release.srt`（3355 cue；全文稽核 A/B、仲裁 C、
    5 項重大項目雙 ASR 皆保留原文）。
  - 顯示層＋燒字幕：`scripts/livestream_subtitle_burn.py`（`shared/livestream_subtitles.py`）。剪接點 597.3–7647.3 s，
    中場休息幻覺段 5240–5537 s 整段拿掉，術語清單 `refs/terms.txt`。
  - 參考資料：投影片在 Downloads 最新一份（修修之後會放固定資料夾）→ 複製到 `<錄影資料夾>/refs/`。
  - 輸出：`<錄影資料夾>/burn/2026-11-19-AI-Agent-字幕版-v3.mp4`（22 處人工勘誤、剪掉中場休息，1:52:34）。
- **S3 完成（2026-11-20）**：介紹文（4 段＋30 條章節）修修核准；播放器章節 freedom-fleet #24；
  lesson 379（section 378）已上線，一般會員帳號實測看得到。**第一場直播全程跑完。**
- 流程寫成 skill：`.claude/skills/livestream-course/`。
- 剩下：S4（Slack 叫 Sanji 一句話觸發）；課程網址改帶日期（目前是「直播錄影」的拼音）；投影片固定資料夾位置待修修定。
- **S4 實作中**（D10–D15）：
  - nakama（`feat/livestream-s4`）：投影片抽字改成白名單內的 `scripts/livestream_slides_text.py`；SKILL 補上
    「介紹文與章節」一節（`refs\chapters.json`、`create-lesson --chapters`）與「背景模式」一節；背景規則寫在
    `references/unattended.md`；`gateway/handlers/sanji.py` docstring 註明私訊經 `preempt_dm` →
    `fleet.minutes.tracker dm` → `fleet.livestream.queue`。
  - freedom-fleet：`fleet.livestream.queue`、背景 Agent SDK 執行、白名單 hook、上傳／建課／上線（另一條線）。
  - 待解：S3 的 recognition／cue worker audit 要填 review JSON 的 SHA-256（canonical bytes），白名單內沒有工具算得出來
    （見 `unattended.md` 第 5 節）。

## 待辦

- （已解決 2026-11-19）Bunny 金鑰、看過去的課、頭尾要不要剪 → 見 D7–D9。
- 頭尾剪掉之後，字幕時間軸要以**剪後的影片**為準重新算（D7 與字幕規則「時間軸＝原始錄影」的關係：
  Memo 流程照原始錄影跑，燒字幕前再依剪接點平移）。
