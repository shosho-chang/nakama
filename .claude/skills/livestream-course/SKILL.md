---
name: livestream-course
description: >
  自由艦隊主題直播／讀書會的 Zoom 錄影 → 校對字幕（Memo Dual-Audit）→ 剪頭尾與中場休息 →
  燒字幕 → 上傳 Bunny Stream → 社群「直播錄影」課程新增一課（草稿 → 修修核准介紹文 → 上線）。
  Use when 修修 says 「直播錄影上課程」「把這場直播做成字幕版」「直播字幕」「燒字幕上 Bunny」
  「把 <日期> 直播放到課程區」，或指著 `E:\Company\07_自由艦隊營運\recording\<資料夾>` 要處理直播。
  也是 Slack 私訊 Sanji「直播做成字幕版」後，freedom-fleet 背景 Claude 讀的流程（背景模式另讀
  references/unattended.md）。
  不要用在：內部會議（那是 freedom-fleet 的 fleet.minutes 會議紀錄）、podcast 訪談集（podcast-pipeline）。
---

# livestream-course — 直播錄影 → 字幕版課程

整體計畫與決策出處：`docs/plans/2026-11-19-livestream-to-course.md`。
2026-11-19「AI Agent」直播是第一次完整跑，下面每一步都是那次實際走過、踩過坑後的版本。

**分工**：製作（字幕、剪接、燒字幕、介紹文、章節）在 **nakama**（本 repo）；發布（Bunny、建課、上線）在
**freedom-fleet**（`E:\freedom-fleet-runtime` 跑，金鑰在那邊的 `.env`）。

## 鐵則（2026-11-20 修修糾正過的）

1. **問修修之前先自己查到底。** 稽核／仲裁留下的待確認字詞，先 web search（產品、模型名）、
   查 `memory/`、翻聊天室／投影片講者備註／前後文／他自己後面的回答、用
   `scripts/livestream_relisten.py` 重聽原音。只問「查遍仍無解、確實只有他知道」的，問時附上已查證據與最佳猜測。
   他的自創詞：**人馬筆記本**（Centaur 筆記系統）、**克朗**（大腦坐在機器人裡的比喻：LLM＋Agent Harness）。
2. **「」每次出現都標**，不是只標第一次（派仲裁時要明講）。《》只在講到書名本身時加。
3. **不送 Auphonic**：直播常超過單一帳號額度（免費方案 2 hr），而且錄音有會員聲音。用 `--local-decode`。
4. 剪接點、中場休息由我抓，給修修時附時間點與那一句話；不要讓他自己找。
5. 所有指令前加 `PYTHONUTF8=1`（Windows cp1252 主控台印中文會讓 status／seal 的輸出炸掉）。

## 背景模式（Slack 觸發）

修修在 Slack 私訊 Sanji 一句話（例：「直播做成字幕版」），freedom-fleet 就在桌機背景用 Claude Agent SDK
開一個 Claude（工作目錄 `E:\nakama`）照本 skill 跑。哪些句子算觸發由 freedom-fleet 判斷，不在本 repo。

**背景模式開工前必讀 [references/unattended.md](references/unattended.md)**，那裡的規則優先於本文。跟互動模式的差別：

- **問修修**：互動模式直接在對話裡問；背景模式不能用 AskUserQuestion，要**結束這一輪、回 `status: ask`**，
  由程式私訊修修，他回覆後程式用同一個對話接回來（prompt 是「修修回覆：<原文>」）。
- **關卡只有兩個**：關卡 1 字詞（查遍仍無解才問，沒有就跳過）、關卡 2 審核包（剪接點＋介紹文＋章節＋90 秒試片）。
- **工具有白名單**：只能寫 episode 資料夾與共用術語表；Bash 只能跑指定的幾支 script 與 ffprobe／ffmpeg。
  被擋就 `status: failed`，不換寫法繞過。
- **發布不碰**：§7 的上傳、建課、上線全部由 freedom-fleet 的固定程式做；Claude 做到燒好整場、回 `artifacts_ready` 為止。

## 0. 素材與參考資料

錄影資料夾（Zoom 本機錄影）：`E:\Company\07_自由艦隊營運\recording\<YYYY-MM-DD HH.MM.SS 主題>\`
— `video*.mp4`（Zoom 畫面，例：2144×1200 25fps）、`audio*.m4a`（單軌混音）、`chat.txt`、`zoom-transcript.vtt`。

- 這個資料夾**就是 episode 根目錄**，`episode_id` ＝資料夾名稱原字（含空格、點）。
- 會議紀錄程式（freedom-fleet S0）會在社群活動的資料夾留 `.livestream.json`（`event_id`、`post_id`、`title`、
  `start_at`、`zoom_meeting_id`…），活動標題取 `title`。
- **投影片**：
  - 背景模式：程式會先把投影片複製到 `<episode>\refs\slides.pptx`；**沒有這個檔＝沒找到投影片**，不要自己去找，
    少了投影片就照樣做，審核包裡講一聲。
  - 互動模式：修修之後會放固定資料夾（位置確定後更新這裡）；在那之前，找 `C:\Users\Shosho\Downloads` 裡
    **直播開始前最後修改**的那份 `.pptx`（同名多版時看修改時間，不看檔名版號），複製到 `<episode>\refs\`。
- 建 `<episode>\refs\`：
  - 投影片文字＋講者備註抽成 `slides-text.md`：
    ```powershell
    E:\nakama\.venv-v2\Scripts\python.exe scripts\livestream_slides_text.py --pptx "<episode>\refs\slides.pptx" --out "<episode>\refs\slides-text.md"
    ```
    每張一節 `## 投影片 N`（依投影片編號），文字每段一行，講者備註壓成一行 `[講者備註] …`；輸出已存在就覆寫。
  - `本場參考資料.md`：講者、發言會員（`chat.txt` 發言者＋`zoom-transcript.vtt` 發言者＋畫面名牌）、已知辨識錯誤、VTT 時間差。
    **會員名字是個資，只放錄影資料夾，不進 git。**
  - `terms.txt`：每次出現都要加「」的術語（一行一個）。先放投影片上的概念名，S4 仲裁後再補稽核標過「」的詞。
    Agent、Context 這種常當一般名詞用的單字不收。
- 共用術語表 `E:\Company\07_自由艦隊營運\recording\術語表.md`：新出現的人名、產品名、被聽錯的字追加一列，回報時列出新增了哪些。
- `zoom-transcript.vtt` 的時間軸跟錄影不同（11-19 那場差 +367 s），只能當文字旁證。用開場第一句與中場回來第一句兩點對齊算出差值。

## 1. 音檔

```powershell
$env:PYTHONUTF8=1
E:\nakama\.venv-v2\Scripts\python.exe scripts\run_audio_prep.py "<episode>" --audio "<episode>\audio*.m4a" --local-decode
```
產出 `normalized.wav`（16-bit PCM，時間軸＝原始錄影）＋ `normalized-handoff.v1.json`。2 小時約 5 秒。

## 2. Memo 辨識＋辨識／斷句稽核（S3）

照 `.claude/skills/podcast-pipeline/references/memo-dual-audit-production-runbook.md` S3 原樣跑
（`run-memo-bundled` → 必要時 repair → `prepare-recognition` → 兩個 recognition audit worker →
`accept-recognition` → `prepare-cues` → 兩個 cue audit worker → `accept-cues` → `status` 要 `ready:true`）。
程式本身不需要因為單軌、無來賓而修改。

- 2 小時直播 Memo 約 5 分鐘。`--prompt` 在 `--max-context 0` 下無作用，不用調。
- **中場休息會出現靜音幻覺**（「(看畫面)」連發、「在這裏我們可以看到一座古老的建築物」、字幕組套話）。
  這不是 catastrophic：時間軸完好、休息前後辨識正常。記下 cue 範圍，S4 brief 叫稽核不要處理它，燒字幕時整段拿掉。
- recognition／cue audit worker 用 Sonnet 就夠（純結構檢查）。先跑一次 timing QC 決定要不要 repair：
  ```powershell
  E:\nakama\.venv-v2\Scripts\python.exe scripts\livestream_tools.py timing-qc "<Memo SRT>" --wav "<episode>\normalized.wav"
  ```
  印 JSON 摘要（零／負長度、重疊、最後一句超出音檔、格式壞掉的 cue），有問題 exit 1。
- worker audit 的 `review_manifest_sha256`＝review JSON 檔的 SHA-256，用
  `scripts\livestream_tools.py sha256 "<review JSON>"` 取得；worker 寫完 audit JSON 後跑
  `scripts\livestream_tools.py canonical-json "<audit JSON>" --episode "<episode>"` 改成 canonical bytes
  （runbook 要求：排序 key、緊湊、結尾無換行）。不必另寫 python。

## 3. 全文稽核、仲裁、雙 ASR、finalize（S4–S6）

1. `podcast_subtitle_release.py init`（16 個 `--path` 用 runbook 的預設相對路徑；`audio_decisions` 用預設
   `subtitle-work/memo-dual-audit-v1/audio-decisions.json` 或與 build-audio-decisions 實際寫出的位置一致）→ `seal`。
2. 兩個全文稽核 worker：brief 範本在 [references/text-audit-brief.md](references/text-audit-brief.md)，填本場值後存 scratchpad。
3. `merge-official` → 仲裁 C（brief 要講：只能選既有提案、major 一律 keep_unresolved、**「」每次出現都標**、
   先 dry-run importer 到 scratchpad 再寫正式檔）→ `apply-official-arbitration` → `seal` → `status`。
4. `awaiting_major_dual_asr`：照 podcast-pipeline SKILL 的四步（prepare-major-audio、faster、qwen、build-audio-decisions）→ `seal` → `ready_to_finalize` → `finalize`。
   產出 `<episode>\subtitle-release\memo-dual-audit-v1\release.srt`（時間軸＝原始錄影，保留 Memo 標點）。
5. 把仲裁的 unresolved 非 major 項目 + major 項目**照鐵則 1 自己解**：
   ```powershell
   E:\nakama\.venv-v2\Scripts\python.exe scripts\livestream_relisten.py --srt "<release.srt>" --wav "<episode>\normalized.wav" --cues 1041,1142 --hint "<本場專有名詞>" --out "<scratch>\relisten.json"
   ```
   有證據的寫進 `<episode>\refs\fixes.json`（`start`／`original` 逐字照 release.srt／`replacement`／`source` 寫根據），
   剩下的一次問修修（附證據與猜測，他不知道就保留原文）。背景模式這就是**關卡 1**。

## 4. 剪接點

- **開頭**：主持人（曉宣）正式開場那句之前（例：「哈囉大家，我們時間其實差不多了」）。之前的等人閒聊剪掉——也含會員私事。
- **結尾**：大家說完晚安、掰掰之後；補拍團體照之前。
- **中場休息**：從宣布休息那幾句結束（例：「好，我就停止」之後）到回來第一句（「大家陸陸續續都回來了」）之前，**影片也剪掉**（修修 2026-11-20）。
- 用 release.srt 的 cue 時間定秒數（原始錄影秒數），回報時列出時間點與那句話。

## 5. 顯示層＋燒字幕

```powershell
$env:PYTHONUTF8=1
# 顯示層：去括號標註、簡轉繁、語助詞、只留《》「」、半形英數補空白、術語加「」、人工勘誤、剪接平移、依字寬換行
E:\nakama\.venv-v2\Scripts\python.exe scripts\livestream_subtitle_burn.py prepare --srt "<release.srt>" `
  --start <開頭秒> --end <結尾秒> --cut <休息起>-<休息迄> `
  --terms "<episode>\refs\terms.txt" --fixes "<episode>\refs\fixes.json" --out "<episode>\burn\display-vN.srt"

# 先燒 90 秒試片：同一份 display-vN.srt、同一組 --start/--cut，加 --preview-seconds 90
E:\nakama\.venv-v2\Scripts\python.exe scripts\livestream_subtitle_burn.py burn --video "<episode>\video*.mp4" `
  --srt "<episode>\burn\display-vN.srt" --start <開頭秒> --end <結尾秒> --cut <休息起>-<休息迄> `
  --preview-seconds 90 --out "<episode>\burn\preview-vN.mp4"

# 試片沒問題再燒整場（拿掉 --preview-seconds）
E:\nakama\.venv-v2\Scripts\python.exe scripts\livestream_subtitle_burn.py burn --video "<episode>\video*.mp4" `
  --srt "<episode>\burn\display-vN.srt" --start <開頭秒> --end <結尾秒> --cut <休息起>-<休息迄> `
  --out "<episode>\burn\<YYYY-MM-DD>-<主題>-字幕版-vN.mp4"
```
- `prepare` 和 `burn` 的 `--start／--end／--cut` 必須同一組；字幕先燒在剪掉前的時間軸、再剪，不會錯位。
- `--preview-seconds` 只燒 `--start` 起那幾秒（`cmd_burn`：end＝start＋秒數，落在範圍外的 `--cut` 自動略過），
  所以直接用整場的 display-vN.srt，不必另外 prepare 一份。
- 樣式（修修 2026-11-20 看過試片）：LINE Seed TW 粗體、半透明深底、置中於左側投影片區（右側 224px 是 Zoom 視訊欄）。字型在使用者字型夾，程式預設已指好。
- 2 小時片 medium preset 約 7 分鐘。燒完抽幾張畫面確認勘誤過的句子；跨中場剪接點確認前後句接得上。
- 版本號往上加（v1、v2…），舊版不覆蓋。

## 6. 介紹文與章節

兩份都依**成片**（剪掉頭與中場之後）寫，存在 `<episode>\refs\`，建課時一起帶進去。背景模式放在關卡 2 審核包裡給修修看。

### 介紹文 `refs\intro.html`

- 參考課程裡 lesson 184、185 的寫法：三到五段第三人稱敘述（例「這場直播前半段，張修修先從……」），
  依投影片大綱＋release 內容寫；會員只寫有在直播中公開發言的、不寫私事。
- 上一場（2026-11-19，lesson 379，修修核准過）的結構：四段敘述＋最後一段章節列表。每段一個 WordPress paragraph block：
  ```html
  <!-- wp:paragraph -->
  <p>第一段敘述……</p>
  <!-- /wp:paragraph -->
  ```
  block 之間空一行；`&` 寫成 `&amp;`（例 `Q&amp;A`）。最後一段是章節列表，一章一行、行間用 `<br>`：
  `00:00:00 開場｜<重點><br>00:01:20 <主題>｜<重點><br>…`
- 同時存一份純文字 `refs\intro-preview.md`（段落＋章節列表，不含 HTML）給修修過目。

### 章節 `refs\chapters.json`

- 依成片時間軸切 **20–30 章**（上一場 30 章：開場、各主題段、每個會員提問各一章、結尾）。
- 時間換算：成片秒數＝原始錄影秒數 − 開頭秒；在中場之後的再減（休息迄 − 休息起）。
  注意 display-vN.srt 只做了頭的平移，中場之後的時間**還沒**扣掉休息長度，不能直接拿來當章節時間。
- 格式（`E:\freedom-fleet-runtime\fleet\course\cli.py` 的 `_read_chapters`）：
  ```json
  [{"start": 0, "title": "開場"}, {"start": 80, "title": "<主題>"}, {"start": 457, "title": "會員提問：<問題>"}]
  ```
  非空清單；`start` 是成片秒數（上一場用整數），**嚴格遞增、小於片長**；`title` 不可空白。
- 標題分兩層，照上一場：
  - `chapters.json` 的 `title`＝**主題**（短，例「開場」「<投影片段落名>」「會員提問：<問題>」，不含「｜重點」），播放器章節列顯示這個；
  - 介紹文與審核包裡的章節列表＝`HH:MM:SS 主題｜重點`（重點一句話，例「<主題>｜<這段講了什麼>」）。
  兩邊的時間與主題要一致。

## 7. 發布（freedom-fleet，`E:\freedom-fleet-runtime`）

> **背景模式下這一節全部由 freedom-fleet 的固定程式做**（關卡 2 核准、Claude 回 `artifacts_ready` 之後上傳＋建草稿；
> 修修說「可以上線」時 publish）。背景 Claude 不跑這一節的任何指令。以下是互動模式的做法。

```powershell
cd E:\freedom-fleet-runtime
.venv\Scripts\python -m fleet.course upload --file "<燒好的 mp4>" --title "<YYYY-MM-DD 主題>"
```
上傳＋等轉檔，印 guid、片長。**先寫介紹文、章節（§6）給修修核准，再建課**——介紹文與章節是建課時一起寫進去的。

- 章節標題格式：`YYYY-MM-DD 【X月主題】<活動標題>`（例「2026-11-19 【十一月主題】…」；讀書會：`YYYY-MM-DD X月讀書會《書名》`），
  活動標題取 `.livestream.json` 的 `title`。
```powershell
.venv\Scripts\python -m fleet.course create-lesson --guid <guid> --section-title "<章節標題>" `
  --intro-file "<episode>\refs\intro.html" --chapters "<episode>\refs\chapters.json"
```
建出**草稿**章節＋單元（會員看不到）。`--chapters-display` 預設 `strip`（章節列在播放器下方）。章節 `start` 要小於
Bunny 回報的片長，否則 create-lesson 會拒絕。修修用管理員身分看過、核准後：
```powershell
.venv\Scripts\python -m fleet.course publish --lesson-id <N>
```
`publish` 會觸發 FluentCommunity 的「單元上線」事件（等同後台發布），只在修修說可以時跑。

## 完工

修修用會員身分在 `fleet.shosho.tw/deck/course/recording/lessons` 看得到新的一課：影片能播、字幕正確、
章節對得上、介紹文是他核准的版本。
