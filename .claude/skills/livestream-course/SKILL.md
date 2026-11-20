---
name: livestream-course
description: >
  自由艦隊主題直播／讀書會的 Zoom 錄影 → 校對字幕（Memo Dual-Audit）→ 剪頭尾與中場休息 →
  燒字幕 → 上傳 Bunny Stream → 社群「直播錄影」課程新增一課（草稿 → 修修核准介紹文 → 上線）。
  Use when 修修 says 「直播錄影上課程」「把這場直播做成字幕版」「直播字幕」「燒字幕上 Bunny」
  「把 <日期> 直播放到課程區」，或指著 `E:\Company\07_自由艦隊營運\recording\<資料夾>` 要處理直播。
  不要用在：內部會議（那是 freedom-fleet 的 fleet.minutes 會議紀錄）、podcast 訪談集（podcast-pipeline）。
---

# livestream-course — 直播錄影 → 字幕版課程

整體計畫與決策出處：`docs/plans/2026-11-19-livestream-to-course.md`。
2026-11-19「AI Agent」直播是第一次完整跑，下面每一步都是那次實際走過、踩過坑後的版本。

**分工**：製作（字幕、剪接、燒字幕）在 **nakama**（本 repo）；發布（Bunny、建課、上線）在
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

## 0. 素材與參考資料

錄影資料夾（Zoom 本機錄影）：`E:\Company\07_自由艦隊營運\recording\<YYYY-MM-DD HH.MM.SS 主題>\`
— `video*.mp4`（Zoom 畫面，例：2144×1200 25fps）、`audio*.m4a`（單軌混音）、`chat.txt`、`zoom-transcript.vtt`。

- 這個資料夾**就是 episode 根目錄**，`episode_id` ＝資料夾名稱原字（含空格、點）。
- 會議紀錄程式（freedom-fleet S0）會在社群活動的資料夾留 `.livestream.json`（活動標題、event／post id、Zoom 編號），活動標題從這裡拿。
- **投影片**：修修之後會放固定資料夾（位置確定後更新這裡）；在那之前，找 `C:\Users\Shosho\Downloads` 裡
  **直播開始前最後修改**的那份 `.pptx`（同名多版時看修改時間，不看檔名版號）。
- 建 `<episode>\refs\`：
  - 複製投影片進來，抽文字＋講者備註成 `slides-text.md`（python 讀 pptx zip 的 `ppt/slides/slide*.xml` 與 `notesSlides`）。
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
- recognition／cue audit worker 用 Sonnet 就夠（純結構檢查）；先手動跑一次 timing QC（zero／負長度／重疊／最後一句超出音檔）決定要不要 repair。

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
   剩下的一次問修修（附證據與猜測，他不知道就保留原文）。

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

# 先燒 90 秒試片（顯示層用同一組參數另外 prepare 一份 --start/--end 對應的 preview.srt）
E:\nakama\.venv-v2\Scripts\python.exe scripts\livestream_subtitle_burn.py burn --video "<episode>\video*.mp4" `
  --srt "<episode>\burn\display-vN.srt" --start <開頭秒> --end <結尾秒> --cut <休息起>-<休息迄> `
  --out "<episode>\burn\<YYYY-MM-DD>-<主題>-字幕版-vN.mp4"
```
- `prepare` 和 `burn` 的 `--start／--end／--cut` 必須同一組；字幕先燒在剪掉前的時間軸、再剪，不會錯位。
- 樣式（修修 2026-11-20 看過試片）：LINE Seed TW 粗體、半透明深底、置中於左側投影片區（右側 224px 是 Zoom 視訊欄）。字型在使用者字型夾，程式預設已指好。
- 2 小時片 medium preset 約 7 分鐘。燒完抽幾張畫面確認勘誤過的句子；跨中場剪接點確認前後句接得上。
- 版本號往上加（v1、v2…），舊版不覆蓋。

## 6. 發布（freedom-fleet，`E:\freedom-fleet-runtime`）

```powershell
cd E:\freedom-fleet-runtime
.venv\Scripts\python -m fleet.course upload --file "<燒好的 mp4>" --title "<YYYY-MM-DD 主題>"
```
上傳＋等轉檔，印 guid、片長。**先寫介紹文、給修修核准，再建課**——介紹文是建課時一起寫進去的。

- 介紹文：參考課程裡 lesson 184、185 的寫法（三到五段第三人稱敘述，例「這場直播前半段，張修修先從……」），
  依投影片大綱＋release 內容寫；會員只寫有在直播中公開發言的、不寫私事。存 `<episode>\refs\intro.html`。
- 章節標題格式：`YYYY-MM-DD 【X月主題】<活動標題>`（讀書會：`YYYY-MM-DD X月讀書會《書名》`），活動標題取 `.livestream.json`。
```powershell
.venv\Scripts\python -m fleet.course create-lesson --guid <guid> --section-title "<章節標題>" --intro-file "<episode>\refs\intro.html"
```
建出**草稿**章節＋單元（會員看不到）。修修用管理員身分看過、核准後：
```powershell
.venv\Scripts\python -m fleet.course publish --lesson-id <N>
```
`publish` 會觸發 FluentCommunity 的「單元上線」事件（等同後台發布），只在修修說可以時跑。

## 完工

修修用會員身分在 `fleet.shosho.tw/deck/course/recording/lessons` 看得到新的一課：影片能播、字幕正確、介紹文是他核准的版本。
