# Podcast IG Carousel — 鄭國威 EP120 tracer-bullet Task Prompt

> Stage anchor: Content Pipeline Stage 5（製作）  
> Branch/worktree: `codex/podcast-ig-carousel-pilot` / `E:\nakama\worktrees\podcast-ig-carousel-pilot`  
> Domain contract: `agents/brook/CONTEXT.md`  
> Decisions: ADR-063、ADR-064

## 1. 目標

實作獨立 `/ig-cards` Podcast Carousel flow，從鄭國威 EP120 的乾淨逐字稿產生一份有逐字稿 evidence、經三個獨立 agent lens 查證收斂的 channel-native Copy Spec，套用已核准的 1080×1350 設計系統，輸出可在輕量 Review Web App 逐卡核准與回饋的完整 Carousel asset。

## 2. 範圍

### Repo 內新增／修改

- `.claude/skills/ig-cards/SKILL.md` — canonical skill entrypoint；只跑 Podcast Carousel，不觸發 Blog／FB。
- `shared/schemas/podcast_carousel.py` — `PodcastCarouselCopySpecV1`、page/evidence、review manifest/feedback 的 typed schema 與 invariants。
- `agents/brook/podcast_carousel_copy.py` — 從乾淨逐字稿建立唯一主 Copy Spec；保存候選角度的內部判斷，但不輸出多套待審 Carousel。
- `agents/brook/podcast_carousel_panel.py` — 平行獨立執行 IG Audience、Episode Editorial、Brand and Evidence 三個 lens，查證 findings 並收斂成修訂。
- `agents/brook/podcast_carousel_render.py` — 將 Copy Spec 與 cutouts 套入資料驅動頁型，輸出 1080×1350 PNG、fit diagnostics、artifact hashes 與 review manifest。不可用字串搜尋替換硬編碼 placeholder；應把視覺 mockup 萃取成資料驅動 page components，再用既有可用的 browser/HTML screenshot runtime render。
- `scripts/run_podcast_carousel.py` — episode-local revision orchestration、template snapshot、partial rerender、approved materialization。
- `thousand_sunny/routers/carousel_review.py` — `/bridge/ig-cards/{episode_slug}` page-based route、manifest validation、媒體讀取與 revision-bound feedback。
- `thousand_sunny/templates/bridge/carousel_review.html` — 桌機五欄 grid、逐卡 decision/feedback、click-to-open evidence detail panel；遵守 `docs/design-system.md`。
- `thousand_sunny/app.py` — 掛載 Carousel sibling router，沿用既有 auth 與 Thousand Sunny process。
- `tests/agents/brook/test_podcast_carousel_copy.py`
- `tests/agents/brook/test_podcast_carousel_panel.py`
- `tests/agents/brook/test_podcast_carousel_render.py`
- `tests/scripts/test_run_podcast_carousel.py`
- `tests/test_carousel_review.py`

### 設計系統來源

- `E:\Company\02_品牌資源_BrandAssets\Shosho Abnormal Universe Design System\templates\ig-carousel-episode\IgCarouselEpisode.dc.html`
- 同資料夾 `README.md`、`ds-base.js`、`support.js`，以及其引用的 logo/pattern assets。
- 設計系統是唯一 authoring source；episode package 只保存 content-addressed 唯讀 template snapshot，不建立第二套可編輯模板。

### Episode pilot

- `G:\footages\20260721 鄭國威\ig-carousel\` — 唯一 episode artifact root，與 `packaging\` 同層。

## 3. 輸入

- 必要語意來源：`G:\footages\20260721 鄭國威\transcript_prose.md`。
- Evidence 定位來源：`G:\footages\20260721 鄭國威\transcript.srt` 與 `subs\aligned_segments.json`；Copy Spec 的 evidence 必須同時保存原文、說話者、來源定位與可用的時間範圍。若自動對齊無法可靠定位，該 evidence fail closed，不得猜時間。
- Episode metadata：`packaging\packages.json`、`packaging\briefs\full.json`；只能讀取可證實的集數／來賓資訊，不得把其中封面候選文案當 Carousel 正文。
- Cutouts：`packaging\cutouts\cutouts_manifest.json`，現有 guest 8 張、host 6 張；不重新去背。
- `social_brief.md` 是選填。EP120 現況沒有該檔，pilot 必須驗證「無 brief 仍自行建立 Episode Highlight Arc」的路徑。
- 設計系統內所有示例文字都只是 placeholder，禁止成為 Copy Spec fallback。
- 實作基線：isolated worktree 已有 `thousand_sunny/app.py` 與長 Highlight review route；Carousel 直接掛同一 app，不依賴主工作區尚未提交的 finished-cut 擴充，也不從 dirty root 複製檔案。

## 4. 輸出

### Episode package

```text
<episode>/ig-carousel/
  current.json
  templates/<template_sha256>/...
  revisions/r001/
    copy_spec.v1.json
    panel/
      ig_audience.json
      episode_editorial.json
      brand_evidence.json
      synthesis.json
    pages/01.png ... NN.png
    fit_diagnostics.json
    review_manifest.v1.json
  review_feedback.v1.json
  approved/
    01.png ... NN.png
    manifest.json
```

- Copy Spec 頁型骨架：一個 cover → 一個 hook → ordered `content_sequence`（point 與零個以上 re_hook）→ 一個 quote → 一個 CTA。
- 每個 page 有跨 revision 穩定的 `page_id`、`role`、Display Copy、最多一個且必為同區塊完整原字串的 `emphasis`、一個以上 Transcript Evidence、content hash。
- Quote variant：EP120 為偶數，預設 B（主持人問題＋直接相連的來賓回答）；找不到可靠配對時 fail-soft 降級 A，不補假問題。允許人工 override。
- 10 頁內標 `api_compatible`；11–20 頁標 `manual_only`；不得為了 API 相容刪除重要內容。
- Web App URL：`/bridge/ig-cards/20260721%20鄭國威`（實際 slug encoding 依既有 episode root resolver）。
- 所有卡片核准後才把當次 revision materialize 到 `approved/`；publish 不在本 task。

## 5. 驗收

### Copy 與 evidence

- 從 EP120 真實逐字稿產生一份主版本；沒有任何設計模板 placeholder 混入。
- 非引言頁使用 Social Editorial Voice；引言／B 版問題可縮短順句但不改原意，且均可展開查看 immutable evidence。
- 不允許把不連續時間段拼成單一句來賓金句；跨段 evidence 只能支撐社群編輯的內容重點。
- 內容點數不鎖 4／6；不湊數、不因頁數方便漏掉整集重要主題；總頁數 20 以內。
- Re-hook 只有在開啟另一重要主題、重建注意力或建立新懸念時出現，獨立佔頁，並重用 P2 layout。測試 fixture 至少涵蓋有 Re-hook 與無 Re-hook 兩條路徑。
- 三個 reviewer lens 互不看彼此輸出；主 agent 對每個 finding 查證。Brand/Evidence 的事實、歸屬、斷章取義 blocker 必須解決；其他未採納建議要在 synthesis 保存理由。不得以平均分／多數決自動送出。

### Render

- 每張輸出精確為 1080×1350；頁碼與內容編號依 ordered sequence 自動重算，Re-hook 不占 point 編號。
- headline/body/quote 等區塊各自 fit；不裁字、不省略、不由 renderer 改寫。低於 pilot 可讀範圍才 fit 的頁仍 render 並標 `needs_review`。
- 同 revision 重跑使用相同 Template Snapshot；artifact 未變時 hash 穩定。修改單頁只重 render 受影響頁，結構／順序改動才重建相關頁碼與整體 manifest。
- 缺 cutout、evidence 無法定位、image 尺寸錯誤、manifest path 越界或 hash 不符都 fail closed。

### Review Web App

- 桌機寬度可見每列 5 張；10 張時兩列完成全局掃描。responsive 小螢幕可降欄，但不做 swipe-only viewer。
- 每張卡片下方有 `approved`／`needs_changes` 與 1–3 行 feedback；v1 無 inline copy editor。
- 點卡片開側邊 detail panel，顯示放大成圖、Display Copy、原始 evidence、說話者與時間位置。
- Feedback 綁定 manifest SHA、revision 與 `page_id`；stale manifest submit 回 409。只有同 revision 全頁 approved 才能 approve carousel。
- 新 revision 只可沿用 artifact hash 未變頁面的 approval；內容或圖片變更頁不得沿用舊核准。
- 沿用 lightweight review app 的登入、auth redirect 與 server-owned feedback path；不得信任 client 提供任意 artifact path。

### Tests / regression

- 上列新單元、router、CLI tests 全綠；至少有一條 EP120 fixture-level E2E 驗證 Copy Spec → panel receipts → PNG → manifest → feedback → approval materialization。
- 既有 `tests/agents/brook/test_ig_renderer.py`、`tests/test_repurpose_engine.py`、`tests/scripts/test_run_repurpose.py` 維持全綠，證明舊 Blog／FB／IG fan-out 未被新 canonical flow 破壞。
- UI 依 `docs/design-system.md` 完成 keyboard focus、semantic controls、loading/empty/error/disabled states 與 reduced-motion 檢查；以實際 browser screenshot 做視覺 QA。
- 鄭國威 pilot 的 Review Web App 可實際開啟並完成一輪 feedback；記錄哪些頁觸發 Fit Escalation，再據此決定是否新增最低字級 design token。

## 6. 邊界

- 不把新功能塞進 `agents/brook/ig_renderer.py`，也不移除或重寫舊 ADR-014 RepurposeEngine fan-out。
- 不開發書本 Carousel、身心健康資訊 Carousel 或 generic Social Post framework；它們等 Podcast Carousel tracer bullet 跑順後 fork。
- 不把 `ig-carousel/` 放進 `packaging/`；`packaging/cutouts/` 只作輸入。
- 不修改逐字稿、SRT、aligned segments、既有 packaging 圖片或 cutouts。
- 不實作 Instagram 發布、caption/alt-text 產生、平台帳號整合或 CTA 平台設定 UI；Apple Podcasts／Spotify／YouTube 保持模板既有視覺。
- 不新增 copy-only 人工 gate，也不在 Web App 提供 inline 文字編輯。
- 不先發明硬字數限制或永久最低字級；以 EP120 pilot evidence 再校準。
- 不把 episode media／PNG 大檔提交進 git；repo 只提交程式、schema、skill、tests 與 docs。
