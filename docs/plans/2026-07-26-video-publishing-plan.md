# 影片多平台發布設計 — grill 成果 + handoff

**Date:** 2026-07-26
**Status:** **Grill 暫停於 Q6**，交棒給 title/thumbnail session 接續
**Stage anchor:** Stage 6 發布（`CONTENT-PIPELINE.md` 觀察 #4 / 結構性優先序前三名）
**Scope:** 長片（8–12min 橫式）+ 短片（60–120s 直式）→ YouTube（v1）→ Meta 三面（v2）
**暫停原因（修修 2026-07-26）:** Q6 起的每一題都被「title / thumbnail 怎麼決定」的形狀決定；在本 session 繼續問等於替另一個 session 猜答案。

---

## 給接手 session 的閱讀指引

1. **§1 實抓現況** — 全部是實際查證過的事實（含 VPS 生產庫查詢），不是推論。可直接當前提用
2. **§2 已凍結決策** — 修修已裁決，**不要重新討論**，除非你手上有新事實推翻它
3. **§3 交接契約（Q6）** — 這是**要你裁的**。三個待答問題在 §3.3
4. **§4 未開始 grill 的分支** — 還沒問到的題目 + 我的預備建議（**建議不等於決策**，標記清楚）
5. **§5 反向依賴** — 發布層對 title/thumbnail session 的硬要求（最小欄位 + 落檔）
6. **§6 下一步順序** — Slice 0 探針必須先行的理由

紅線提醒：本專案 `CLAUDE.md` 的「嚴禁幻想」凌駕一切。本文件每個事實都附檔案路徑或行號；**接手時若發現任何一條對不上，那條就當作未驗證處理，不要沿用**。

---

## §1 實抓現況（2026-07-26 驗證）

| 事實 | 證據 |
|---|---|
| 發布層只有 WordPress | `agents/usopp/publisher.py`（完整 state machine：claimed → media_ready → post_draft → seo_ready → validated → published → cache_purged） |
| **那套機器從沒真的跑過** | VPS 生產庫（ssh 直查）：`approval_queue` **2 列**（2026-04-27 / 04-29，皆 `rejected`、皆 `wordpress`、皆 `brook`）；`publish_jobs` **0 列**，且 `agents/usopp/` 內**無任何 DELETE 該表的 code** → 發布狀態機生產環境**零次成功執行**；`nakama-usopp.service` 仍 `active`，空轉三個月 |
| YT 上傳 0 實作 | `agents/zoro/youtube_api.py` = search/scout；`shared/youtube_ingest.py` = KB ingest。全 repo 無 upload code |
| YT OAuth 從未設定 | `.env` 的 `YOUTUBE_CLIENT_ID` / `_SECRET` / `_REFRESH_TOKEN` 三個**全空**、**全 repo 零 consumer**；`YOUTUBE_API_KEY` 有值但是 read-only 那把（Zoro / ADR-035 watchlist 用） |
| 影片終點是 Resolve timeline 不是檔案 | highlight-cut Step 3/6/7；export mp4 目前 100% 手動 |
| Resolve render 自動化已驗證可用 | `scripts/run_short_director.py:415-475`（`SetRenderSettings` / `AddRenderJob` / `StartRendering` / `IsRenderingInProgress` 輪詢 / `DeleteRenderJob`，Resolve 20.3）— 目前只用於**單幀樣張**驗構圖 |
| 字幕是真 subtitle 軌，不是畫面圖層 | `build_resolve_project.py:287-290`（`timeline.AddTrack("subtitle")`）、`run_short_director.py:390-391`。樣式靠 DRT 模板攜帶（`build_resolve_project.py:46-54`，API 不開放 subtitle style preset） |
| 每段字幕已平移到 0 起點 | `run_highlight_cut.py:144-145` `_segment_srt`「時間平移到 0 起點」→ `highlights/srt/<id>_rNNN.srt` 可直接當該支影片的 CC 上傳 |
| 量級 | `G:\footages\` 9 集（**6 集為 2026 年 7 月**：財富階梯 / Christina Wallace / 呂冠緯 / 鄭國威 / 李海碩 / 謝伯讓）；謝伯讓集 `winners.json` **7 個當選段**（`punch-L5` / `story-L1` / `util-L4` + `punch-S1..S4`）→ 一集 3 長 + 3~4 短，**約 40 支積壓** |
| 平台額度不是瓶頸 | 官方文件實抓：預設 100 `search.list` + **100 `videos.insert`** + 其他 endpoint 合計 10,000 units/day。audit 只綁「要加額度」，**不綁「能否公開發布」** |
| **瓶頸是修修的審核點擊數** | 40 支 × v1 一平台 = 40 次決策；v2 加 Meta 後一集約 19 個投放 |
| title-brainstorm 預設不落檔 | `.claude/skills/title-brainstorm/SKILL.md:38`：「只有明確要求存檔才寫 `AgentOutputs/title-brainstorm/<來源>-<日期>.md`；不寫 session／暫存目錄」→ **產出是對話 markdown，發布層讀不到**。這是實際斷點 |
| 縮圖已有儲存慣例 | ADR-033 D7 hybrid：候選在 `data/thumbnails/{slug}/runs/{ts}/v{N}.png`（gitignored），**選中的進 vault**（因 ADR-030 D1「vault 是 committed state 的 canonical SoT」） |
| 修修的 voice profile 存在 | `data/brook/style-profiles-fable5/`（`00-voice-core.md` / `01-mechanics.md` / `02-lexicon.md` / `20-negative-constraints.md` / `21-exemplars.md` / `30-generation-protocol.md`） |

### 1.1 未確認的外部風險（必須用探針解，禁止寫進設計當前提）

第三方上傳工具使用者回報影片被降權（[porjo/youtubeuploader#86](https://github.com/porjo/youtubeuploader/issues/86)）：

> 「the video was uploaded using a third party tool that failed our verification. Because of this, we set limited access for the video.」

**此句不在任何官方文件內。** 觸發條件查不到，也無法確認「自己的 OAuth project 傳到自己的頻道」是否會中。→ §6 Slice 0 探針的存在理由。

---

## §2 已凍結決策（修修 2026-07-26 裁決）

### Q1 — 平台範圍 ✅
**v1 = YouTube 長片 + YouTube Shorts。v2 = Meta 三面（IG / FB / Threads）只吃短片。其他一律不做**（無 TikTok / X / Spotify / Apple Podcast）。

修修原話：「可以先把 YouTube 這條路跑順，接下來會做的就是把短片放到 Meta 的 platform 上面…其他都不用，就這兩個平臺。」

→ **Meta 三面視為單一 platform family**（一組授權、一個 adapter 家族、三個目的地），不是三個獨立整合。

### Q2 — 上傳肌肉的位置 ✅
**桌機當 uploader，VPS 當控制面**，兩邊走 HTTP claim/report 契約。

```
VPS（24/7）                        桌機（24/7，Resolve + 原檔在此）
─────────────────                  ──────────────────────────────
release 狀態機 / 帳本               uploader worker
per-platform 文案                   ├ 讀 Resolve 匯出的 mp4
Bridge 審核 + 排程 UI               ├ resumable upload → YouTube
publish 時間帳本                    └ 回報 video_id / URL / 失敗
      ↑                                      │
      └──── HTTP（API key，claim / report）───┘
```

理由：
- **硬約束**：YouTube API 只吃 POST 上去的 bytes（無「給我 URL 你自己抓」）→ 持有檔案的機器**必須**是上傳者
- 桌機 24/7 開機（修修確認），原檔也在桌機
- VPS 磁碟緊：Franky critical 線 `disk > 95%` 或 `free < 5GB`（`ADR-007:338`）；1–2 GB 檔案過境是拿告警線換方便
- VPS 規格 2vCPU / 4GB，遵循既有 compute tier split（`memory/claude/feedback_compute_tier_split.md`）
- 兩台的 `state.db` 是不同檔案（VPS `/home/nakama/data/state.db` vs 本機 `E:\nakama\data\`）→ 桌機 worker 必須走 HTTP，不能直讀 queue
- 逃生門（先不做）：家中 NAS（DS918+，24/7 + 住宅 IP + 已有反向隧道）可接手 uploader，契約不變

**推論（已凍結）：upload 與 publish 時間解耦。**
- **upload** = approve 後盡快傳成 private（機會性執行，不承諾鐘點）
- **publish** = 交給平台原生排程（YT `privacyStatus=private` + `publishAt`），時間由平台的鐘執行，桌機與 VPS 都不需在那一刻活著
- 效果：修修在 UI 排的發布時間是**硬承諾**，不是「希望我家電腦那時候開著」

### Q3 — 資料模型：新開，不沿用舊零件 ✅
**新開 `releases` / `release_targets` 兩張表 + 自己的審核頁面；只借舊那套的「做法」（狀態機紀律、原子認領、`operation_id` 追蹤、CLI 備援），不借它的「零件」。**

拒絕沿用 `approval_queue` 的理由 — 該表為「一篇 WP 文字稿 → 一個網站」設計，影片要補五樣才夠用：
1. 檔案身分與所在機器（ADR-006 自述不支援二進位 payload）
2. 一支影片對多平台的群組關係（無 `release_id` 概念）
3. 排程時間需為**可查欄位**（現在藏在 payload JSON 裡，畫不出行程表）
4. **跨機器認領** — ADR-006 §3 自註明現行機制僅同 process（module singleton conn + RLock），跨 process 需 Phase 2 fencing token
5. 大檔斷線續傳（resumable session URI / 已傳 bytes）

在一張只有 2 列死資料的表上動五次手術，且繼承的是未經生產驗證的假設。

**三層模型**：

| 層 | 是什麼 | 現在在哪 |
|---|---|---|
| **Episode** | 一集訪談 | 已存在：`G:\footages\<YYYYMMDD> <來賓>` |
| **Cut** | 一條 Resolve timeline 匯出的一支成品影片。canonical key = `(episode, winner_id)`，如 `20260723謝伯讓/punch-S1`；帶 `format: long \| short` | 已存在於 `highlights/winners.json`，本設計把它抬成一等公民 |
| **Release Target** | `(Cut × platform)`。**執行單位**，各自擁有 title / description / hashtags / 縮圖 / 排程時間 / 狀態 / 平台回傳 id + URL | 全新 |

**Release Target 必須獨立成列的硬理由**：YT 成功 + IG 失敗是必然會發生的事。一列管四平台則「一半成功」無處可記，重試無從下手。

⚠️ **雙 id 陷阱**：winner id（`punch-S1`）≠ Resolve timeline 顯示名（`短1 - <標題>（緊·導播）`）。40 支時這個對應**不能靠人記**，必須機器保證。

### Q4a — 誰按下匯出：系統驅動、觸發權在修修 ✅
```
python scripts/publish_prep.py "20260723 謝伯讓"                  # 這集全部定稿的都出
python scripts/publish_prep.py "20260723 謝伯讓" --cut punch-S1    # 只出這一支
```

流程：讀 `winners.json` → 找到對應 timeline → 排 render job → 等完成 → 檔案落標準位置 → **自動登錄成一筆 Release（草稿狀態）**。

跑完的語意是「**登錄了**」不是「發布了」——系統手上多了檔案 + 草稿 Release，等文案、等排程、等修修核准。

為什麼不做「watch folder 撿檔案」：檔名要人對上 cut id，40 支時是必然出錯的環節。
為什麼不做「materialize 完自動出片」：出的會是還沒微調的版本。修修覺得某支調好了才對它下指令。

### Q4b — 字幕：長片不燒、只上 CC；短片必燒 ✅
**修修裁決（理由優於我原本的建議，已採納）**：
1. 手機觀看時燒上去的字幕會跟系統 CC 重疊，難看
2. 台灣現在預設把字幕打開，有上傳 CC 就夠
3. **上傳的 CC 之後校對發現錯誤可以馬上改** ← 對這條有終檢流程的產線特別有份量（燒進去的改不了）

技術後果（已驗證）：
- Resolve subtitle 軌**預設不燒進畫面** → **長片乾淨畫面是零額外工作**
- ⚠️ **短片的 render 設定必須明確打開燒錄**，否則短片會出成完全沒字幕的片子。**這是本設計唯一的 render 地雷**
- 長片的 CC 直接用 `highlights/srt/<id>_rNNN.srt`（已平移到 0 起點），走 `captions.insert`

附帶效益：CC 文字進 YouTube 搜尋索引（燒在畫面上的字 YT 讀不到）；外語自動翻譯是從校正過的正確文字翻。

### Q5 — 文案生成 ✅（部分，細節待 §3）
- **標題**：上游 title/thumbnail session **決定好**才交過來 → 審核頁只**顯示已決定的標題 + 留改寫欄位**，不做 Top-5 點選
- **描述**：LLM 產草稿，**必須吃修修的 voice profile**（`data/brook/style-profiles-fable5/`）避免 AI slop；修修在審核頁改（修修原話：「Description 就是讓 LLM 來吃我的 voice 產草稿，避免 AI slop。我可以在審核頁改」）
- **固定段 / 變動段切開**（修修：「我的確這些 repurpose 的影片都會有一些固定的 CTA 要插入」）
- **長片自動分章**（修修確認），短片不分章

描述欄結構：

```
┌─ 變動（LLM 產、修修改）
│  這支的 hook 兩三句
├─ 變動（長片才有）
│  ⏱ 00:00 戒手機的第一步
│    01:42 睡眠的三個功能        ← 從該段 SRT 自動分章
├─ 變動（LLM 從選段企劃報告搬）
│  本集引用：
│  ・Lancet 2024 失智症可預防風險 45–47%（族群層級數據，非個人）
│  ・Science 2010 心思漫遊與快樂度
├─ 固定（設定檔，40 支共用）
│  來賓與著作 / 完整訪談 podcast / IG / FB / 訂閱 CTA / hashtags
└─
```

**為什麼固定段要獨立成設定檔**：改一次 CTA 或加一個平台連結就套用全部，不用重生 40 支文案。

**順手撿到的設計**：選段企劃報告已寫「Lancet 45–47% 是族群層級數據——**字卡必標出處**」。字卡重剪很貴，**描述欄補出處零成本**。→ 規則：**brand-lens 的出處要求自動流進描述欄「本集引用」段**，不靠人記得。等於用發布層吸收剪輯層的成本。

### 詞彙凍結
- **channel**（既有，`CONTENT-PIPELINE.md:20`）= Stage 5 的**產出型**：影片 / 部落格 / FB post / IG carousel
- **platform**（本設計）= Stage 6 的**目的地帳號**：YouTube / IG / FB / Threads
  - 不可混用：「影片」一個 channel 落到四個 platform；「IG carousel」與「短片 Reel」是不同 channel 落到同一個 platform
  - `approval_queue.target_platform`（值 `wp_shosho` / `ig`）已站在正確一邊，code 與本切法一致
- **Episode / Cut / Release Target** — 定義見 Q3 表
- **「LLM 代筆邊界」** = ADR-027 / `CONTENT-PIPELINE.md:36`「不得產生完成句、段落或第一人稱正文」**只管 Stage 4 的原子文章正文**（修修自己的聲音）。**Stage 5/6 的社群與平台文案 LLM 可代筆**（有 style profile 把關）——先例：已 ship 的 FB renderer（4 語氣版本）+ IG renderer。**寫下來是為了避免以後有人拿 ADR-027 擋發布文案**

---

## §3 交接契約（Q6）— **要接手 session 裁決的部分**

### 3.1 問題
`title-brainstorm` 目前**預設不落檔**（`SKILL.md:38`），產出是對話 markdown。發布層需要**機器可讀的已決定資料**。兩個 session 平行進行，交接面若不釘死，之後對接就是重工。

### 3.2 我的提案（可被接手 session 推翻）
一個 **per-cut 交接檔**，放 episode 資料夾（跟 mp4 / srt / winners.json 同居，維持「一集一個自包含資料夾」的既有慣例；且上游 skill 與 uploader 都在桌機跑，同機器不過網路）：

`G:\footages\<episode>\highlights\publish\<cut_id>.json`

```json
{
  "cut_id": "punch-S1",
  "format": "short",
  "title": {
    "chosen": "手機沒偷走你的注意力，它偷走的是耐心",
    "alternates": ["...", "..."]
  },
  "thumbnail": null,
  "citations": ["Science 2010 心思漫遊與快樂度"],
  "brand_flags": ["Lancet 45–47% 為族群層級數據，需標出處"],
  "decided_at": "2026-07-26T21:30:00+08:00"
}
```

長片多縮圖欄位（指向 ADR-033 選中後進 vault 的那張）：
```json
"thumbnail": { "path": "Attachments/thumbnails/20260723-長1-v3.png" }
```

**發布層只讀不寫這個檔** — 它是上游產物，不是共用可變狀態（同 ADR-051 pattern：創意判斷歸 skill，契約歸 pipeline）。

### 3.3 三個待答問題
1. **縮圖只有長片需要嗎？**（我的推測：短片是 Shorts / Reels，縮圖在那些版位幾乎不影響曝光 → 長片 only。**未查證，需接手 session 確認或實測**）
2. **`brand_flags` / `citations` 由誰填？** 它們現在活在選段企劃報告的**散文**裡。上游填了，描述欄「本集引用」就能自動生成；不填，發布層得回頭 parse markdown 散文（脆、會漏）
3. **交接檔放 episode 資料夾**（不進 vault、不進資料庫）可以嗎？理由：它屬於「這一集的製作產物」，與 `winners.json` 同層級

### 3.4 最小契約（發布層的硬需求）
不論交接檔最終長什麼樣，發布層**必須**拿到：

| 欄位 | 用途 | 缺了會怎樣 |
|---|---|---|
| `cut_id` | 對上 `winners.json` 與 Resolve timeline | 無法知道這份 metadata 屬於哪支影片 |
| `format`（long/short）| 決定 render preset（燒字幕與否）、是否分章、是否需縮圖 | render 出錯（短片沒字幕 / 長片燒了字幕） |
| `title.chosen` | 平台標題 | 無法發布 |
| `thumbnail.path`（長片）| YT 縮圖上傳 | 長片只能用 YT 自動抽的幀 |

其餘欄位（推導鏈、alternates、分數、角度標籤…）**全部歸上游 session 決定**，發布層不介入。

---

## §4 未開始 grill 的分支（**建議 ≠ 決策**）

以下都還沒問過修修。**接手 session 不要把我的預備建議當成已凍結決策。**

### 4.1 ⚠️ vault 還是資料庫當 SoT？（我還沒問，但這題很重）
本專案有一條反覆確立的原則：**vault 是 committed state 的 canonical SoT，其他表面是投影**（ADR-030 D1、ADR-033 D7、ADR-041 D1「the vault is the source of truth; the calendar is a downstream representation」）。

Q3 決定了「新開兩張表」，但**沒有回答這兩張表是不是 SoT**。

我的預備立場（**未經修修裁決**）：
- **DB 當 release plan + 執行狀態的 SoT**，vault 只收**發布完成後的結果**（URL / 發布時間 / 最終標題）單向寫回 episode 或 project 頁
- 理由：release plan 是**數天期的操作意圖**，不是耐久知識；而且它是**表單形狀**（per-platform 欄位），不是 markdown 形狀。ADR-041 v3 的痛（byte-splice token、per-entry projection、dual-read 遷移）正來自把表單形狀的東西塞進 markdown
- **這是刻意偏離 house 原則，因此需要 ADR 記錄**（否則半年後有人會來「修正」它）

### 4.2 歸屬：這套 code 住哪？
`CONTENT-PIPELINE.md:207` **自我矛盾**：同一句話說「發布層**不要再擴 Usopp**」又說「**新開 `agents/usopp/` sub-publisher**」。必須凍結一個。

預備建議：住 `agents/usopp/`（Usopp 的 ADR-001 職責就是 Publisher），但**與 WP 那條路平行、不共用零件**（Q3 已定）。同時把 `CONTENT-PIPELINE.md:207` 那句矛盾改掉。

### 4.3 審核手勢層級（Q3 時提過，修修未明確裁決）
- **(a) 一集一頁 board 批次核准** — Pros：符合 highlight-cut 一次出 7 支的節奏；40 支積壓時審核從 ~40 次點擊降到 6 次；同頁看得到整週 cadence。Cons：一頁塞 7×(標題+描述+時間) 很重；一支文案沒想好會擋住整集，需要「先跳過」逃生門
- **(b) 一支一卡** — Pros：UI 簡單。Cons：一集點 7 次；看不到 cadence
- 預備建議：**(a) 當 view、(b) 當 model**（資料層 per-Target 一列保證失敗隔離，UI 預設 board + 批次核准，保留單支核准）

### 4.4 排程語意與 cadence 規則
- 修修實際在 UI 上設定什麼？（每支各自的發布時間？還是「這集從週一開始每天一支」的節奏樣板？）
- 需不需要規則擋「同一天塞兩支短片」？
- Bridge 上要不要一個發布行程表視圖（ADR-041 已有 Google Calendar 整合可借鏡，但**發布時間屬於平台，不是修修的行事曆**，不建議混進同一個 calendar）
- v2 Meta：**IG Graph API 是否支援排程發布尚未查證**。若不支援，Nakama 得自己持鐘（桌機 24/7 可以扛），與 YT 的原生排程並存 → **兩套排程語意，必須在 UI 上讓修修看得出差別**

### 4.5 OAuth / token
- YT：`.env` 三個欄位空著，需一次性 consent 流程。既有前例可抄：`shared/google_calendar.py`（token 落 `data/google_calendar_token.json` + filelock 防併發 refresh）+ `scripts/google_calendar_auth.py`
- **token 放桌機還是 VPS？** uploader 在桌機 → token 必須在桌機。但 VPS 若要讀 YT Analytics（Stage 7）會需要自己一份
- Franky 到期探針（既有 pattern：`memory/claude/project_zoro_coach.md` 的 VPS token 探針 cron）
- Meta 的 long-lived token 續期策略：未查證

### 4.6 其他缺口
- **上傳失敗 / 斷線續傳**：resumable session URI 存哪、retry 上限、失敗怎麼通知（既有 `agents/franky/alert_router.py`）
- **重複上傳防護**：YT 沒有天然 idempotency key。預備建議：DB 側 claim + 上傳完成即寫 `video_id`，並把 session URI 持久化讓 crash 後續傳而非重傳
- **發布後回寫**：video URL / 發布時間寫回 vault（呼應 4.1）
- **Stage 7 迴路**：YT Analytics 回收 → 餵回 Zoro topic discovery（`CONTENT-PIPELINE.md` 觀察 #1 的斷裂迴路）。**明確 out of scope，但架構上不要擋死**

---

## §5 反向依賴：發布層對 title/thumbnail session 的要求

1. **必須落檔**（現在預設不落檔 → 發布層讀不到）。格式與位置可談，最小欄位見 §3.4
2. **`format` 必填** — 它決定 render preset。短片 render 若沒打開字幕燒錄，出來的片子沒有字幕
3. **長片必須有縮圖路徑**；短片若確認不需要，明確填 `null` 而不是省略欄位（省略 = 不知道是「不需要」還是「還沒做」）
4. **`citations` / `brand_flags` 最好由上游填**（見 §3.3 第 2 題）。它們是選段企劃報告已經產生的資訊，只是目前是散文
5. **標題是「已決定」而非「候選」** — 修修明確說「接收決定的資料」。若上游改成交候選池，發布層的審核頁形狀要跟著改，請回頭通知

---

## §6 下一步順序（強烈建議照這個順序）

### Slice 0 — 探針（**在寫任何 code 之前**）
拿一支已剪好的短片（如 `punch-S1`），最土的 30 行 script：
1. 跑一次 YT OAuth consent，拿到 refresh token
2. 上傳成 `private`
3. 設 `publishAt` 為 10 分鐘後
4. **確認它真的在指定時間變公開**
5. **確認沒被標「third party tool failed our verification」降權**（§1.1）

**這一步過不了，後面全部不用做。**

理由就是 §1 那兩列死資料：2026-04 那套的死因不是 schema 選錯，是**先蓋了工廠、跑了三輪 multi-model review、卻沒先確認貨出得去**。同樣的錯不要犯第二次。

### 之後
1. 收斂 §3 交接契約（本 handoff 的目的）
2. 裁決 §4.1（vault vs DB）→ 因為它偏離 house 原則，**需要 ADR**
3. 裁決 §4.2（歸屬）+ 修掉 `CONTENT-PIPELINE.md:207` 的矛盾
4. `publish_prep.py`（Q4a）——render + 登錄，可獨立驗收
5. 描述生成器（Q5）——吃 voice profile
6. Bridge 審核 board（§4.3）——UI slice 必須 browser UAT（`memory/claude/feedback_ui_browser_verification_before_merge.md`）
7. Uploader worker + HTTP claim/report 契約（Q2）
8. v2 Meta 三面

### ADR 建議
本設計符合 ADR 三條件（難以反轉 / 無 context 會被誤解 / 有真實 trade-off），至少三個決策該進 ADR：
- **Q2 桌機 uploader + VPS 控制面**（跨機器 worker 契約，半年內難改）
- **Q3 不沿用 `approval_queue`**（未來一定有人問「為什麼有兩套 queue」；答案是那兩列死資料）
- **§4.1 DB 當 SoT**（刻意偏離 vault-as-SoT house 原則）

ADR 定稿後建議跑 `multi-agent-panel`——Q2 與 §4.1 都屬於「架構鎖定 + 我有強烈偏好」，正是 confirmation bias 最會咬人的位置。
（註：修修 2026-07-26 已 retire multi-agent-panel skill——改為直接派 3 個 Opus 5 subagent 分 lens 對抗性審查，criteria 不變。ADR-054 v2→v3 即用此法。）

---

## §7 接手 session 回覆（2026-07-26，title/thumbnail session — ADR-054 v3 已收接縫）

**§3.3 三題已裁**（細節見 `docs/decisions/ADR-054-packaging-title-thumbnail-brainstorm.md` 附錄 D）：

1. **縮圖只長片需要 — 是**，且理由升級為硬平台事實：Test & Compare 官方不支援 Shorts。
   短片 `thumbnail: null` 明填（≠ 省略）。
2. **`citations` / `brand_flags` 由 packaging skill 填**。更正一個前提：它們**不是只活在散文裡**
   —— `highlights/review_brandlens.json` 是機器可讀 JSON（散文的是選段企劃報告）。skill 從
   JSON 搬進 `packages.json`，發布層零 parse。
3. **交接檔不放 episode 資料夾** —— 統一為 **vault `Attachments/packaging/<episode>/` 是唯一
   交接面**：`packages.json`（skill 寫）+ `approval.json`（Bridge 寫）。理由：審核 UI（VPS）
   與 uploader（桌機）都讀得到 vault；發布層在桌機讀本機 vault 同樣零網路。
   **§3.2 提案的 `highlights/publish/<cut_id>.json` 不做** —— 同一份資料兩個位置必然漂移。
   episode 資料夾只放 working set（推導鏈、抽格、中間物），發布層不讀。

**§5 第 5 條 — 形狀變了，發布層要跟進**：
- 標題「已決定」= `approval.json.primary_package` 指向的那組（修修在 gate approve 時定）。
  發布層上傳用它，審核頁照原設計「顯示已決定 + 改寫欄」。
- **新增**：每支長片另有 2 組 A/B 備用 package（標題+縮圖）。**Test & Compare 無 API**
  （已查證：桌機 Studio only），所以 A/B 測試是修修上傳後在 Studio 手動建 —— 審核頁請加
  「複製 A/B 備用」按鈕（純文字複製 + 縮圖檔路徑），把手動成本壓到最低。
- 短片標題 = LLM 直出單條（修修 2026-07-26 二修：「短影片的 title 直接用 LLM 決定就好」），
  無 alternates。

**§5 其餘四條**：全部照辦 —— 落檔 ✅（packages.json）、`format` 必填 ✅（一等欄位；短片燒字幕
的 render 地雷收到）、長片縮圖 vault-relative 路徑 ✅、citations/brand_flags 上游填 ✅。
`packages.json` schema 草案在 ADR-054 附錄 C，凍結前請對一眼欄位夠不夠。

**§1 兩條前提的獨立驗證結果**（接手側重驗，皆一致）：`.env` 三個 YouTube OAuth key 值全空 ✅；
`YOUTUBE_API_KEY` 唯讀有值 ✅。另補一條你們會用到的：修修 cutout 表情庫已從 VPS Syncthing
`.stversions` 還原 17 張／7 表情（曾於 2026-06-05 被刪，兩側 vault 都消失過 —— vault 沒有
備份、Syncthing 版本控制只在 VPS 側有開，這對 §4.1 的 SoT 討論是個 data point）。

**§6 Slice 0 探針不依賴本 session 的任何產出，建議立即先行。**
