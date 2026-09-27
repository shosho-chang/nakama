# Podcast E2E Gap Audit — 2026-08-19

狀態：明日新訪談 E2E 前的 operational audit。範圍從 Auphonic normalization 到
YouTube 公開與 CC；以 `CONTENT-PIPELINE.md` Stage 4→6 為 anchor。這是 Claude Code
可讀的專案 Skill reference，不是 Codex memory。

## 目標與成功標準

目標：新的一集不靠臨場補洞，能從原始 episode folder 走到一支已核准、已上傳、
有 CC 的長 highlight，而且每一段都能回答「現在在哪、為什麼算完成、失敗怎麼續」。

成功必須同時滿足：

1. Stage 4：normalized audio lineage 可驗；文字真相與顯示字幕分離；未解風險會擋住投影。
2. Stage 5：選段、實際上軌字幕、finished cut、封面標題都有機械可查的 gate。
3. Stage 6：使用者最後一次按核准時，Title／Description／Thumbnail 的當前表單值先落 DB；
   上傳、縮圖、CC 各自可觀測、可恢復，不會重傳已成功影片。
4. 狀態誠實：Web UI、state.db、檔案 manifest 與 YouTube 實際狀態不互相說謊。
5. 可移植：Claude Code 從同一份 Skill 能跑，不依賴 Codex 私有記憶。

## 現行 workflow 與 readiness

| # | Stage | 現行入口／產物 | Readiness | 判定 |
|---|---|---|---|---|
| 1 | Audio source 選定 | episode `Audio/`、`run_audio_prep.py` | 部分可用 | V1 以路徑與 manifest 驗進度；V2 要 content-addressed lineage。|
| 2 | Auphonic normalization | `normalized.wav` + `prep_manifest.json` | V1 可用／V2 in flight | Auphonic 單元測試 42 passed；V2 normalization execution/store 94 passed。鄭國威 receipt 仍是 V1 note，不是 V2 Normalization Receipt。|
| 3 | Recognition | V1 `subs/raw.srt`；V2 規格要求 Memo Recognition Evidence | V1 production／V2 shadow | `podcast-pipeline` 還只認 V1 檔案；V2 模組目前只在主工作區未提交檔，其他 worktree 看不到。更嚴重的是規格已改成 Memo-first，但實際 `production:build_production` 與測試仍固定 Qwen3-ASR primary + Faster-Whisper corroborating，repo 內沒有 Memo production recognizer/importer。|
| 4 | Correction + Canonical | V1 `transcript.srt` + QC；V2 Canonical Transcript + ledger | 斷裂 | 鄭國威 V1 manifest 有 136 uncertain，仍一路進剪輯；V2 要 fail closed，但尚未 cut over。|
| 5 | Verified Projection → Resolve | V1 裸 SRT；V2 projection ID + manifest | contract 有、E2E 未接 | V2 CLI tests 32 passed；fresh-process handoff 單測 passed，但整組很慢。highlight scripts 仍吃裸 SRT。|
| 6 | Highlight mining + shortlist | candidates → persona review → winners | 可用 | 有 shortlist HITL；但 orchestrator 仍以檔案存在推斷，缺單一 stage state。|
| 7 | Materialize + longform production | Resolve timelines + tighten/director/visual/SFX/QC | 大致可用 | 後半段 regression 63 passed；但 `qa_final.json` 可缺席仍往後走。|
| 8 | Finished-cut review | `/bridge/highlights/.../finished` | worktree 已補 | R11 已留下 append-only revisions，`approved_cut=R11`；先前保存／核准 UX 曾 hang、跳動、無 transition。|
| 9 | Packaging | packages + approval + 3 PNG | 可用但帳本漂移 | R11 封面最初錯用 full/N1 文字中心版；後改 TF-duo/N2 圖像中心版。schema 只有 `visual_recipe=podcast`，不足以擋 N1/N2 誤路由。|
| 10 | Release render/register | `publish_prep.py` + releases DB | worktree 已接 | finished approval 可背景 render；舊檔成功誤判已有 mtime/job-status guard。|
| 11 | Description | `publish_description.py --hook-file` | **未接** | 仍是獨立 CLI，packaging approval／publish_prep 不會呼叫；這就是 R11 description 空白的根因。|
| 12 | Publish review + upload | `/bridge/publish` + `publish_upload.py` | worktree 已修多處 | 已修表單未保存、worktree token path、錯誤吞掉、進度目錄；尚無跨 process resumable upload。|
| 13 | CC | `captions.insert` / `--cc-only` | recovery 只有 CLI | 舊 token 缺 `youtube.force-ssl` 導致 403；需重新 OAuth 後 `--cc-only`，不重傳影片。|
| 14 | Publish reconciliation | state.db ↔ YouTube Studio | **未接** | 使用者在 Studio 手動公開後，DB 仍可能是 `uploaded`；沒有 `videos.list` reconciliation worker。|
| 15 | Monitoring | YouTube analytics → Stage 1 | **未接** | CONTENT-PIPELINE Stage 7→1 仍斷。|

## 這幾輪實際踩到的問題

### P0 — 明天開新集前必須處理

1. **沒有單一可執行 baseline**
   - 主工作區有未提交的 `agents/brook/podcast_subtitles/`、ADR-056–062、Stage 5 handoff。
   - `zheng-guowei-long-highlight` worktree 有 finished-review／packaging／publish 修正，卻沒有 V2 模組。
   - 結果：任何一邊單獨跑都不是完整最新 E2E。
   - 明日策略：明確指定 **V1 production + V2 shadow**；先不要宣稱 V2 production cutover。

2. **`podcast-pipeline` orchestrator 的完成條件過時**
   - 仍以 `raw.srt`／`transcript.srt` 存在當完成，與 ADR-056 的 Verified Projection 衝突。
   - 原本只編排到 packaging；finished cut、description、upload、CC、reconcile 沒有一條狀態鏈。
   - `.agents/skills/transcribe/V2.md` 說 Memo-first，但 `agents/brook/podcast_subtitles/production.py` 仍建 Qwen3-ASR primary；這不是已 cut over，而是規格與 executable composition root 分裂。

3. **Final QA gate 可被跳過**
   - 鄭國威已有 R11 export、packaging、YouTube video，但
     `G:\footages\20260721 鄭國威\highlights\qa_final.json` 不存在。
   - `highlight-cut` 說 critical 必修，code／orchestrator 沒有在 finished review 或 publish 前強制。

4. **Description generation 不在主流程**
   - `scripts/publish_description.py` 要人工準備 `--hook-file` 才寫 DB。
   - packaging approve 只落 primary title/thumbnail；publish page 因此可以合法出現空 description。
   - 明天 workaround：進 publish review 前明確執行 description generation，並按 longform Skill 文體契約掃 AI slop。

5. **OAuth/CC preflight 太晚**
   - R11 影片 100% 上傳後才在 `captions.insert` 發現 token 缺 scope。
   - 明天應在 render/upload 前讀 token scopes，缺 `youtube.force-ssl` 直接 fail-fast。
   - 2026-08-19 OAuth 補齊後，`--cc-only` 第一次仍誤報 R11 未登錄：token path 讀 `NAKAMA_DATA_DIR`，release DB 卻只讀 `DB_PATH`／`config.yaml`；Windows 因而誤連 `E:\home\nakama\data\state.db`。同時設定 `NAKAMA_DATA_DIR=E:\nakama\data` 與 `DB_PATH=E:\nakama\data\state.db` 後，`R11_tight_r004.srt` 已成功補傳且 DB error 清空。

### P1 — E2E 可以跑，但要人工守住

6. **Finished review 保存語意不清與 UI 跳動**
   - 「保存草稿」曾停在「正在保存 Review Revision」；後來又顯示 revision 未覆蓋。
   - append-only revision 本身正確，但 UI 沒清楚區分「新增 feedback revision」與「重做成片」。
   - 核准後原本沒有可見 transition；worktree 現已 redirect 到 packaging／publish prep。

7. **封面 recipe 不足以擋 N1/N2 誤用**
   - full episode 的 N1 是雙臉夾中央文字；長 highlight 應是 TF-duo/N2、中央圖像。
   - `visual_recipe=podcast` 同時容納兩者，schema 不知道 composition identity。
   - 明天人工檢查：cut_id 不是 `full` 時，三包 thumbnail path 必須是 `pkg-<cut>-N2-*` 或等價 TF-duo receipt。

8. **Packaging 帳本與實際產物不一致**
   - `packaging/manifest.json` 只記 `full`，後來的 R11 packages 不在 manifest。
   - R11 packages 雖存在且可 review，但 resume 不能靠 canonical manifest 找到它。

9. **Working set／vault 雙落點會漂移**
   - gate 編輯標題只寫 vault `packages.json`；working set 舊值可能在下一次 attach/render 時洗回去。
   - 鄭國威 `run_log.md` 已記錄這個風險，仍未建立 monotonic revision／merge contract。

10. **手改 publish 文案曾在失敗後回滾**
    - 舊 approve endpoint 不提交當前表單，只使用先前 DB 值；token preflight 失敗 redirect 後看起來像改稿消失。
    - worktree 已改成 approve-upload 先保存當前表單，再 preflight。

11. **worktree runtime data 漂移**
    - OAuth token 在 `E:\nakama\data`，Web App 曾找
      `worktrees/.../data/youtube_token.json`。
    - uploader progress 寫 shared data，status endpoint 卻讀 worktree data，造成 0%→直接成功。
    - worktree 已統一用 `NAKAMA_DATA_DIR`／DB parent；明天啟動服務與所有 CLI 都必須帶同一值。

12. **Upload partial success 缺少一鍵 recovery**
    - 影片、縮圖成功而 CC 失敗時，系統正確保留 `video_id`，但 UI 只有錯誤文字，沒有「重新授權／只補 CC」按鈕。
    - `--cc-only` 是安全 recovery，仍需 CLI。

13. **上傳中斷的 resumable 只做了一半**
    - DB 有 `upload_session_uri`，但 code 註解明示跨 process resume 尚未接。
    - crash 後防重複主要依賴已寫入的 `video_id`；若 crash 落在 YouTube 已收完、DB 尚未寫 video_id 的縫，仍有重傳風險。

14. **手動公開不會回寫發布狀態**
    - uploader 成功寫 `uploaded`；使用者在 Studio 改 public，沒有 worker 把 release target 改 `published`、寫 published_at 或驗 CC。

### P2 — 不阻擋明天單集，但阻擋穩定運營

15. 沒有一個 machine-readable episode run ledger 統一 Stage 4–6；各段各用檔案、DB、Resolve project、vault approval 推斷。
16. 沒有 Stage 7 YouTube analytics／CTR／retention 回灌 shortlist、title、thumbnail。
17. local gate URL/port 在文件中有 8765，而本輪 Review App 是 8127；操作文件會把人帶錯服務。
18. Python runtime 約束分裂：Resolve 固定 3.10；廣泛 app tests 又會因 `langdetect` 未安裝而 setup error。需要單一 preflight 報告，不要等 route import 才發現。
19. Subtitle V2 fresh-process integrity tests 很慢：CLI 32 tests 約 2 秒；單一 fresh-process handoff test 約 17 秒，整組在 3 分鐘 timeout 內跑不完。CI 要區分快速 preflight 與完整 integrity suite。

## 明日 Go / No-Go checklist

### 開工前（No-Go 任一失敗就不進 Auphonic）

- [ ] 指定要跑的單一工作目錄／branch，能同時看到本輪 review→publish 修正；V2 僅 shadow。
- [ ] `python` runtime 可 import production 所需依賴；Resolve scripts 明確用 Python 3.10。
- [ ] Resolve Studio 開著，External scripting = Local；短／長字幕 DRT template 路徑存在。
- [ ] episode folder 的 Audio／Video 檔案與角色機位已確認；提供 guest 姓名與正式頭銜。
- [ ] refs 只 enroll 明確相關來源；訪綱／術語／書名版本清楚。
- [ ] 同時設定 `NAKAMA_DATA_DIR=E:\nakama\data` 與 `DB_PATH=E:\nakama\data\state.db`；YouTube token scopes 含 `youtube.force-ssl`。
- [ ] Auphonic accounts／quota preflight、Memo export／cue-boundary來源、GPU/model snapshots 可用。

### 跑動中必停的使用者 gate

1. Audio sanity：確認 exact source、長度與是否完整節目。
2. 字幕 final QC：處理所有 NeedsReview；V1 production 不得只說「QC 有 N 項」就往下。
3. Long shortlist：看候選表，指定要製作的 cut。
4. Finished cut：看成片與實際上軌字幕，只核准一支進 packaging。
5. Packaging：選主標題＋主封面；長 highlight 驗 N2/TF-duo，不接受 N1 中央文字版。
6. Publish：最後確認當前 Title／Description／Thumbnail；按核准前必已保存同一份表單。

### 發布完成 Definition of Done

- [ ] `video_id` 存在，YouTube URL 可開。
- [ ] thumbnail step 成功。
- [ ] CC `captions.insert` 成功；若失敗，只跑 `--cc-only`，禁止重傳影片。
- [ ] YouTube 實際 privacy／publish state 已讀回，不只信本地 DB。
- [ ] 最終 title、description、URL、published_at 寫回 release result；若 DB 與 Studio 不同，明示 reconciliation pending。
- [ ] `qa_final.json`／finished feedback／packaging approval／release target 都能追到同一 episode × cut_id。

## 修復順序

### Sprint 0 — 明日 tracer-bullet 前（半天內）

1. 凍結執行 baseline：V1 production + V2 shadow，整合 review→publish worktree。
2. 寫一支 read-only `podcast_pipeline preflight/status`：輸出每個 gate 的 PASS/FAIL、實際路徑與下一動作。
3. 在 publish review 前 fail-fast 檢查 description 非空、token scopes 完整、`qa_final` critical cleared。
4. 保留 CLI workaround：手動執行 description、CC-only、YouTube state 查詢。
5. 統一 data resolver：token、progress、release store 與 Web App 必須從同一個 typed runtime config 取得 data dir／DB path，禁止 `NAKAMA_DATA_DIR` 與 `DB_PATH` 各自漂移。

### Subtitle V2 Memo-first cutover（未完成，獨立於 Sprint 0 workaround）

1. 實作並測試 immutable Memo `ggml-large-v2.bin` recognition export importer，以及獨立核准的 Memo cue-boundary manifest importer；匯入時驗 hash、size、normalized-audio lineage 與 model identity。
2. 將 `production:build_production` 的 primary recognizer 改為 Memo Evidence importer；Qwen3-ASR／Faster-Whisper 只能是 corroborating 或 targeted-audit source。
3. 把 `podcast-pipeline` 的 subtitle stage 從 V1 `subtitle-gen`／`subtitle-correct` 改接 V2 `run → review/decide → project`，並以 Verified Projection manifest 為唯一完成條件。
4. Resolve、highlight、director 與 CC 全部拒收裸 `transcript.srt`；只消費同一 normalized-audio lineage 的 Projection ID + manifest。
5. 做一集 shadow comparison 與一集 supervised tracer bullet；所有 fail-closed gate 通過後，才把 `CONTENT-PIPELINE.md` 從「V1 production / V2 shadow-run」改成 V2 production。

### Sprint 1 — 讓後半段真正接起來

1. finished approve → packaging approval → publish prep → description generation → publish review，改成明確狀態機。
2. description hook 生成進 Skill／worker，輸出先進 draft，套 longform voice contract，再進 UI。
3. packaging manifest 改成 per cut；schema 增 composition identity，機械擋 full/N1 vs highlight/N2。
4. UI 加 CC-only recovery 與 OAuth scope preflight；upload progress、video、thumbnail、CC 各自 step 狀態。
5. YouTube reconciliation：`videos.list` + `captions.list` 回寫 `published`／CC 狀態。

### Sprint 2 — 收斂 Stage 4 真相鏈

1. 正式 commit／整合 Subtitle V2 deep module、ADRs、tests、Stage 5 handoff。
2. `podcast-pipeline` 進度偵測從 V1 檔案存在改為 V2 status／Verified Projection ID。
3. highlight／Resolve consumers 拒絕裸 SRT，只接受 verified projection manifest。
4. 快速 preflight 與完整 integrity suite 分層，CI 提供明確 timeout budget。

### Sprint 3 — 閉環

1. upload session 跨 process resume。
2. Stage 7 YouTube analytics 回灌 shortlist／title／thumbnail。
3. 把本集每個人工 workaround 轉成新的 regression test 與 runbook 條目。

## 使用者明天需要準備

- 新 episode 的絕對路徑。
- 來賓姓名、正式頭銜；若有訪綱／書／報告，指出哪些版本可當 reference。
- 確認完整訪談結束點；需要排除收工閒聊時提供大約 timecode。
- 在六個 gate 做內容裁決；其餘技術步驟由 pipeline 執行。
- 若要直接付費 API 而非 subscription work packets，需另外明確授權；預設不花 API 錢。

## 本次驗證紀錄

- Auphonic V1：42 passed。
- Subtitle V2 CLI：32 passed。
- Subtitle V2 normalization execution/store：94 passed。
- Verified Projection fresh-process loader：抽一項 passed（約 17 秒）；完整 integrity suite 超過本次 3 分鐘 audit timeout，未宣告全綠。
- Highlight→finished review→packaging→publish：63 passed、1 skipped。
- 鄭國威實物盤點：R11 export、tight SRT、finished approval、N2 packages、release/video_id 存在；`qa_final.json` 缺、packaging manifest 未含 R11、DB 未自動同步手動 public。
