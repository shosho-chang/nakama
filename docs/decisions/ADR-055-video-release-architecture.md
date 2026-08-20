# ADR-055: 影片發布線架構 — 桌機 uploader、新表不沿用舊 queue、DB 當 SoT

- **Status**: Accepted（D1/D2 為 2026-07-26 修修裁決的追認；D3 修修 2026-08-04
  裁決通過——「依照你的建議繼續做下去」；D4 於 2026-08-20 依三平台實測後
  的 Publish Calendar 規劃補充；D5 於 2026-08-20 完成 Short due-dispatch slice）
- **Date**: 2026-08-04
- **Context**: `docs/plans/2026-07-26-video-publishing-plan.md`（grill 全記錄）、
  ADR-054（packaging 交接契約）。Slice 0 探針已 PASS（#1124：OAuth + 上傳 +
  publishAt 排程 + 無降權標記實測）。

## D1 — 桌機當 uploader，VPS 當控制面（追認 Q2）

YouTube API 只吃 POST bytes（無「給我 URL 你自己抓」）→ 持有檔案的機器必須
是上傳者；原檔與 Resolve 都在桌機（24/7）。VPS 留 release 狀態機、per-platform
文案、Bridge 審核與排程 UI。兩邊走 HTTP claim/report 契約（API key）。

**Upload 與 publish 時間解耦**：upload = approve 後盡快傳成 private（機會性）；
publish = YT 原生排程（`privacyStatus=private` + `publishAt`），平台的鐘執行，
兩台機器都不需在那一刻活著——修修排的發布時間是硬承諾。

逃生門（不做先）：NAS DS918+ 可接手 uploader，契約不變。

## D2 — 新開 releases / release_targets，不沿用 approval_queue（追認 Q3）

`approval_queue` 為「一篇 WP 文字稿 → 一個網站」設計，且生產環境**零次成功
執行**（VPS 實查：2 列 rejected 死資料、publish_jobs 0 列）。影片要補五樣：
檔案身分與所在機器、一對多平台群組（release_id）、排程時間可查欄位、跨機器
認領、大檔斷點續傳。在 2 列死資料的表上動五次手術不如新開。

三層模型：**Episode**（資料夾）→ **Cut**（winners.json 一支成品，
canonical key = episode × cut_id）→ **Release Target**（Cut × platform，
執行單位，各自擁有文案/排程/狀態/平台回傳）。YT 成功 + IG 失敗是必然，
target 獨立成列才有重試單位。

Schema：`migrations/018_releases.sql`（canonical copy 在
`shared/state.py::_init_tables`）；deep module `shared/release_store.py`
五法（register_release / ensure_target / get_release / list_releases /
update_target）。只借舊那套的「做法」（狀態機紀律、fail-loud、冪等），
不借零件。

## D3 — DB 當 release plan 的 SoT，vault 只收發布結果（**待修修裁決**）

**刻意偏離 house 原則**（ADR-030 D1 / ADR-033 D7 / ADR-041 D1：vault 是
committed state 的 canonical SoT）——這正是本 ADR 存在的理由，否則半年後
會有人來「修正」它。

- release plan 是**數天期的操作意圖**（表單形狀、per-platform 欄位、高頻
  狀態轉移），不是耐久知識；ADR-041 v3 的痛（byte-splice、dual-read）正
  來自把表單形狀塞進 markdown
- vault 收**發布完成後的結果**（URL / 發布時間 / 最終標題）單向寫回
  episode 頁——那才是耐久知識
- packaging 交接面維持 ADR-054 §7：vault `Attachments/packaging/` 的
  `packages.json` / `approval.json`（上游 skill 與 Bridge 寫，發布層只讀）

## D4 — Release 只有一個 Campaign Anchor，Target 保持獨立（2026-08-20 amendment）

Publish Calendar 的排程單位是 **Release**，不是個別 Release Target。每個 Release
只有一個 **Campaign Anchor**；設定或移除時，控制面必須在單一 transaction 中把
同一個 UTC instant materialize 到所有 Target 的 `publish_at`。若既有 Target 的
`publish_at` 不一致，projection 必須標成需處理且不任選其中一個時間放進月曆。

Campaign Anchor 不合併 Target 的執行責任：YouTube、Instagram、Facebook 仍各自
保有 status、receipt、error 與 retry 邊界，因此單一平台失敗不會抹除其他平台的
成功結果。Carousel 沿用相同語意，但 anchor 存在 episode-local Publish Job；只有
`queued` job 可調整，claim 或發布開始後即鎖定。

Campaign Anchor 寫入採 compare-and-set：表單必須帶回讀取時的 anchor token，若
Release Target group 或 Carousel job 已被另一個操作者更新，舊表單必須回 409，
不可無聲覆寫較新的發布意圖。

**排程不等於核准，也不會觸發發布。** Calendar 只寫發布意圖；Release Target 的
approval state 與 Carousel Review/Publish Job gate 仍是獨立狀態機。UI 必須同時顯示
一張 Release／Carousel 卡、共同 anchor，以及各平台的獨立狀態，避免把「有日期」
誤讀成「已核准」或「已上傳」。

## D5 — Short 每平台執行政策：Native Arm + Due Dispatcher（2026-08-20 amendment）

第一版 Short 仍只有一個 Campaign Anchor，但三平台的 clock owner 不相同。未來
anchor 核准後，YouTube 用原生 `publishAt`、Facebook Page Reels 用
`video_state=SCHEDULED` + `scheduled_publish_time` 先行 **Native Arm**；兩者在本機
只標 `uploaded`，因為平台接受排程不等於已公開。Instagram Reels 保持 `approved`，
直到桌機 **Due Dispatcher** 在 anchor 到點後走既有 container + `media_publish`。

Release Target dispatch 前必須用單一 conditional SQL mutation 把 `approved` claim 為
`uploading`。`updated_at` 是 lease heartbeat；adapter checkpoint 寫入會刷新它。只有
超過明示 stale threshold 的 `uploading` 可保留 checkpoint 回收續跑。`failed` 不在自動
claim 集合；Bridge 的既有單平台 retry 先把指定 Target 重設為 `approved`，成功 sibling
永不重開。這個 contract 同時防止人工 dispatcher 與 due worker 對同一 Target 重複呼叫。

Due Dispatcher 是小型、Short-only orchestration layer，不建第二張 scheduling table，
也不碰 Carousel。預設 one-shot dry-run；live mode 才可 claim/call adapter 並寫
`usopp-short-due-dispatcher` heartbeat。Calendar 只投影 heartbeat 為
`never_seen | online | stale | failing`，必要時警告未來 Instagram dependency；警告不改
Campaign Anchor 或 Target state。永久服務安裝與真實 probe 仍需另一次 supervised 操作。

## 後果

- 發布層 code 歸屬（plan §4.2）：`agents/usopp/` 但與 WP 線平行不共用零件；
  `CONTENT-PIPELINE.md:207` 的自我矛盾句要一併修——**下個 slice 處理**
- 重複上傳防護：無平台天然 idempotency key → DB claim + `video_id` 上傳完成
  即寫 + `upload_session_uri` 持久化（crash 續傳不重傳）
- Q4b 字幕實測與計畫假設相反（長片 Resolve render 會燒模板軌、短片燒不進）
  → publish_prep：長片 render 前 disable 字幕軌、短片 ffmpeg 燒 tight SRT
