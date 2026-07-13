# ADR-044 Robin 整合到 VPS — 逆轉 DISABLE_ROBIN，VPS 成為唯一 Robin host

- 狀態：**Accepted（v2.1 — panel-reviewed + 修修裁決 4 forks；實作受 Slice 0 blocking gate 控管）**
- 日期：2026-06-08
- 決策者：修修

> **Panel audit trail（v1 → v2）**：3-way panel（Claude 起草 → Codex/GPT-5 repo-grounded audit → Gemini 2.5 Pro distributed-state audit）。逐字 audit 存 `docs/research/2026-06-08-codex-robin-vps-audit.md`、`docs/research/2026-06-08-gemini-robin-vps-audit.md`。**兩者皆 Reject-as-written。** v1 把這件事誤判為「mostly config、低風險」；panel 揭露兩個被 gloss 掉的根本問題:**(A) 安全** — 開公開面會曝露未授權 / 弱密碼的寫 vault endpoint;**(B) 分散式狀態完整性** — 「單寫手」是假安慰,真衝突是 VPS Robin vs 手機 Obsidian 的人機衝突,加上 `state.db` 與 `data/books` 的耦合搬遷會把既有 annotation 變孤兒。
>
> | # | 議題 | Claude v1 | Codex | Gemini | pattern | 處置 |
> |---|------|-----------|-------|--------|---------|------|
> | 1 | `/api/books/*`（含 annotations POST、ingest-request、progress）**未 auth** | 漏（誤稱「每條 route 都有 auth」）| 抓到（books.py:448-619 多條無 check_auth）| 放大後果（未授權遠端寫 vault + DoS）| 2-of-3 強 | **採納，blocking** |
> | 2 | `WEB_PASSWORD` 空字串 → auth 放行（auth.py:36-37）| 漏 | 抓到 | 放大（預設＝無安全）| 2-of-3 | **採納，blocking** |
> | 3 | `dry_run` 仍會 commit 寫檔（promotion_review.py:322-339）| 誤稱「無害」| 抓到 | 放大（UI 假成功→錯誤心智模型）| 2-of-3 | **採納，blocking** |
> | 4 | 單寫手 ≠ 無衝突;人機衝突才是主風險（annotation_store process-local lock + 整檔覆寫）| 標為「低風險、靠紀律」| 抓到技術面 | 三個具體失敗劇本（離線 Obsidian 編輯靜默遺失）| 2-of-3 強 | **採納，重構衝突模型** |
> | 5 | `state.db` 也是 local-only、且與 data/books 耦合;空 DB 上 VPS → book_id 變、annotation 孤兒 | 完全漏 | 提到是 SPOF | 抓到 schism、要求 atomic 搬遷 | single（Gemini）高價值 | **採納，blocking** |
> | 6 | 「config + 一次性、非新功能」 | 主張 | 否定（要寫 auth+測試+backup）| 否定 | 2-of-3 | **採納，撤回該框架** |
> | 7 | upload 原子性（中斷→孤兒 DB record / 半檔）| 漏 | — | Scenario 2 | single（Gemini）| 採納，列風險 |
> | 8 | 文章 reader 會寫檔（image_fetcher 改寫 source md + 下載到 Files/）| 誤稱唯讀 | 抓到 | 用於衝突劇本 | 2-of-3 | **採納，修正** |
> | 9 | `books.py:152-159` 引錯行（那是 legacy redirect，真 POST 在 :283）| 錯 | 修正 | — | single（Codex）| **採納，fact-fix** |
> | 10 | `data/books` 是 cwd 相對 | 我擔心 | STALE — repo-root 錨定（book_storage.py:40-41,88）| — | single（Codex）| **採納，撤回該擔憂** |
> | 11 | 雲端 fallback 預設＝Sonnet 不是 Gemini（llm_router.py:23-25）| 假設 Gemini | 修正 | 加 latency/可靠性維度 | 2-of-3 | 採納，修正成本框架 |
> | 12 | DISABLE_ROBIN 開的面比 ADR 講的多（/execute、/translate、/watchlist/add(yt-dlp)、/kb/research、promotion commit）| 漏舉 | 列舉 | — | single（Codex）| **採納，需逐一決定曝露** |
> | — | 替代方案：書放 Syncthing folder / read-replica / reverse-proxy auth（Cloudflare Access/Tailscale）/ Qwen 當 queue worker | 部分否決 | 提出 | 強推（尤其書放 Syncthing + reverse-proxy）| 2-of-3 | **升級為待 sign-off 的開放 fork** |
- 關聯：
  - **逆轉政策**：`.env.example:41` 的「VPS 設 `DISABLE_ROBIN=1`（ingest + reader 僅本機）」自此作廢；VPS 改為**唯一** Robin host
  - **依賴既有資產**：ADR-017（annotation store）、ADR-024（source promotion）、ADR-035（video reader vertical）、ADR-018（Zotero / Syncthing sync agent 跑本機）、ADR-028（VAULT-LAYOUT）、ADR-030（vault-as-substrate；Syncthing tri-sync 與 `*.sync-conflict-*` data-loss 風險）
  - **被 N519 牽制**：`feat/n519-llm-promotion-extractor` 未 merge → VPS 上 promotion 維持 `dry_run`；本 ADR **不**解 N519
  - **與 ADR-043 正交**：Centaur 永久層（人寫 `KB/Permanent/`）是另一條線；本 ADR 只搬 Robin 的 ingest/reader/annotation 到 VPS

## 脈絡

修修要在外面用手機 / iPad，透過 Web UI **讀書 / 讀文章 / 看影片 → 產生筆記並記錄**。今日拓撲（ADR-007 §2 起的 feature flag 政策）把 Robin（ingest + reader）綁在本機：VPS 設 `DISABLE_ROBIN=1`，理由是「ingest 用本機 GPU LLM + EPUB 檔在本機」。

經 code 核對，這兩個理由現在都不成立或可繞過：

- **本機 GPU LLM 是選用、非必需**：ingest 的 Map 摘要階段優先本機 Qwen（`shared/local_llm.py:22` `qwen3.6-35b-a3b`@localhost:8080），但 `is_server_available()` 失敗即 fallback 雲端 facade（`agents/robin/ingest.py:322-339`，line 338 log「費用較高」）。概念抽取（Gemini）+ diff-merge（Opus 4.7，`shared/kb_writer.py:155`）本就走雲端。
- **C 文章 / E 影片內容本就在 vault、已靠 Syncthing 到 VPS**：文章 `inbox:{path}`，內容在 `Inbox/` + `KB/Raw/`（`shared/reading_source_registry.py:386`、`agents/robin/agent.py:105`）；影片讀 `Watchlist/youtube/{video_id}/manifest.json` + `.vtt` transcript，全在 vault（`reading_source_registry.py:469-474`），影片從 YouTube 線上播。三向 Syncthing（Windows / Mac / VPS，ADR-030:25、ADR-028:437）已把這些 markdown 搬到 VPS。
- **只有 B 書是 local-only**：EPUB blob 在 `data/books/{book_id}/`（`.gitignore:40` `data/*` 忽略、不進 Syncthing）。但**上傳流程已存在**（見決策依據），不需新建。
- **公開面寫入本就有 auth**：每條 Robin/books route 擋 `check_auth(nakama_auth)` cookie。

→ 「把 Robin 搬上 VPS」實際縮成 **config + 一次性搬遷**，不是新功能開發。

## 決策

1. **VPS = 唯一 Robin host（single writer）。** 拿掉 VPS 的 `DISABLE_ROBIN`，Robin（ingest + reader + annotation + promotion review）全在 VPS 跑。**本機停跑 Robin server**，降為純 Obsidian + git control-plane + Sandcastle runner。單寫手 → 杜絕 Robin-vs-Robin 對 Syncthing vault 的並發寫衝突。

2. **三條來源（C 文章 / E 影片 / B 書）一起上。** C/E 內容已在 vault（Syncthing 同步），reader 不需 local 檔，VPS 立即可讀可註。B 走既有 `/robin/books/upload` 把 EPUB 一次性傳上 VPS。

3. **~~全 surface 開（拿掉 `DISABLE_ROBIN`）+ 視為 mostly-config~~ → 撤回（panel）。** v1 假設「auth 已擋全部 route、無需 gating、是 config 不是新功能」。**panel 證偽**:`/api/books/*` 多條 endpoint 無 `check_auth`（含 annotations POST / ingest-request / progress，books.py:448-619）、`WEB_PASSWORD` 空字串會放行（auth.py:36-37）、`dry_run` 仍 commit 寫檔。**改為:拿掉 `DISABLE_ROBIN` 之前，先過 §Blocking requirements**（補 auth + fail-fast + 釐清 dry_run）。promotion 在 N519 前的「dry_run」需重新命名/gating，因為它不是唯讀。

4. **~~衝突模型 = 單寫手 + 人寫不同資料夾 → 低衝突~~ → 重構（panel）。** 單一 Robin 寫手只消掉 Robin-vs-Robin;**真正的主風險是 VPS Robin vs 手機 Obsidian 的人機衝突**。`annotation_store` 是 process-local lock + **整檔覆寫**（annotation_store.py:102,129-135），文章 reader 也會改寫 source md（image_fetcher.py:32,61-74）。離線 Obsidian 編輯同檔 → Syncthing `*.sync-conflict-*` 被 indexer 靜默忽略 = 真 data-loss（ADR-030）。**靠紀律不夠，需技術控制**（見 Blocking requirements + 開放 fork 的衝突策略）。

5. **（新，panel）安全是 ship 前硬門檻。** 補齊所有寫 endpoint 的 auth、`WEB_PASSWORD` 空值 fail-fast、並決定是否前置 reverse-proxy auth（開放 fork）。

6. **（新，panel）資料完整性是 ship 前硬門檻。** `state.db`（書 metadata/progress/book_id）與 `data/books` 是同一邏輯資料單元、皆 local-only 不同步。搬 VPS 必須 **atomic 遷移 state.db + data/books（保 book_id 連續性，否則既有 KB/Annotations 變孤兒）** + 自動備份。

## 決策依據（已驗證 file:line / 事實）

- **上傳流程已存在**：`thousand_sunny/routers/books.py:152-159`（`/robin/books/upload` GET form + POST）、:291-332（收 `UploadFile`，bilingual + original 兩槽）→ `shared/book_storage.store_book_files`。EPUB→VPS 運輸是現成的，不需新建子工程。
- **auth 已在每條 route**：`books.py:71` `from thousand_sunny.auth import check_auth`；:260-261 / :273-274 / :314 / :424 / :498-499 皆 `if not check_auth(nakama_auth)`；cookie 走 `auth.py` `WEB_PASSWORD` + httponly `nakama_auth`。
- **DISABLE_ROBIN gate 範圍**：`thousand_sunny/app.py:71`（lifespan 跳過 promotion wiring）、:132（Robin/books router 不掛載）、:147（foliate-js mount 需 submodule dir）。
- **promotion RuntimeError gate**：`thousand_sunny/promotion_wiring.py:174` `llm` 模式 `raise RuntimeError`；故 VPS 必設 `NAKAMA_PROMOTION_MODE=dry_run`。
- **Map 階段 local LLM 可降級**：`agents/robin/ingest.py:322-339`，`is_server_available()` 失敗回雲端 `ask`。
- **C/E 內容 vault-resident**：`reading_source_registry.py:386`（inbox）、:469-474（youtube transcript path）；`agent.py:105`（copy 到 `KB/Raw`）。
- **B 不同步**：`.gitignore:40` `data/*`；EPUB 在 `data/books/`（`book_storage._DEFAULT_BOOKS_DIR`，`NAKAMA_BOOKS_DIR` 可覆寫）。
- **Syncthing 三向 + 衝突風險**：ADR-030:25（KB/ Projects/ Daily/ Digests/ 三向同步）、ADR-030:12（Gemini 抓到 `*.sync-conflict-*` 被 `digest_indexer` 靜默忽略 = 真 data-loss）、ADR-028:437。
- **依賴套件**：`pyproject.toml:35-36` `ebooklib>=0.18` + `beautifulsoup4>=4.12`（EPUB 解析）。

## 實作 / 啟用步驟（config + 一次性，非新功能）

1. VPS 環境：拿掉 `DISABLE_ROBIN`、設 `NAKAMA_PROMOTION_MODE=dry_run`、確認 `VAULT_PATH` 指向 Syncthing 的 vault（`/home/Shosho LifeOS`）。
2. VPS repo：`git submodule update --init`（foliate-js，否則 reader JS 不掛，`app.py:147`）；`pip install -r requirements.txt`（含 ebooklib / bs4）。
3. `data/books/` 在 VPS 磁碟持久化（非 ephemeral）；確認 `NAKAMA_BOOKS_DIR` 或 cwd 落點正確。
4. 現有 EPUB（財富階梯等）經 `/robin/books/upload` 重傳 VPS 一次。
5. 本機：停 Robin 的 systemd / 啟動腳本（本機不再跑 reader/ingest server）。
6. 重啟 `thousand-sunny.service`，驗 golden path：文章 reader 註記 → 影片 reader 時間戳註記 → 上傳一本書 → foliate 開 + 註記 → 確認 annotation 落 `KB/Annotations` 並 Syncthing 回桌機。

## Blocking requirements（ship 前必過 — panel 共識）

**安全**
- B1. 補 `check_auth`（或 router dependency）到所有寫/讀 endpoint，尤其 `/api/books/*` 的 annotations POST、ingest-request、progress、cover、metadata（books.py:448-619）+ 生產模式 auth 測試。
- B2. `WEB_PASSWORD` 空值或未設時 **fail-to-start**（非 dev）;審 `NAKAMA_DEV_AUTH_BYPASS`。
- B3. 部署 preflight 檢查 `WEB_PASSWORD`/`WEB_SECRET`/`VAULT_PATH`/`DB_PATH`/`NAKAMA_BOOKS_DIR`/`NAKAMA_PROMOTION_MODE`。
- B4. `dry_run` 不是唯讀（仍 commit 寫檔）→ 重新命名/gating，或在 N519 前把 promotion review 整段關掉。

**資料完整性**
- B5. **atomic 遷移 `state.db` + `data/books`**（scp 兩者、保 book_id 連續性）→ 否則既有 `KB/Annotations/{slug}` 變孤兒（Gemini Scenario 3）。
- B6. `state.db` + `data/books` **單元化自動備份/還原**（兩者是同一邏輯資料，不可分開備）。
- B7. upload 原子性:中斷上傳不留孤兒 DB record / 半檔（先寫檔再 insert、失敗清理）。

**衝突控制（取代「靠紀律」）**
- B8. 人機衝突要技術控制:候選方案 — Robin 寫完把 annotation 檔設唯讀（`chmod 444`）+ UI 顯式解鎖鈕;或 sync-conflict 偵測 lint + 告警。決定見開放 fork。
- B9. 釐清並文件化 `Inbox/`、`Watchlist/`、`Files/`、`KB/Attachments/`、`KB/Annotations/` **是否在 VPS Syncthing set**（文章 reader 會寫 `Files/` + 改寫 source md，image_fetcher.py）。

## 開放 fork 的裁決（修修 sign-off，2026-06-08）

1. **書的儲存模型 → `Vault/Books/` Syncthing 資料夾。** EPUB 進同步資料夾,天生備份 + 三機分發,繞開 `data/books` 不同步。需 refactor book 邏輯從同步目錄讀（`book_storage` 改錨 vault 內 `Books/`，非 repo `data/books`）。連帶簡化 B5/B6:EPUB 隨 vault 備份;`state.db` 仍需處理（見下）。
2. **auth → 前置 reverse-proxy。** Cloudflare Access（已跨 CF tunnel，最順）或 Tailscale Funnel 擋在最前;`WEB_PASSWORD` 退為第二層 defense-in-depth。B1-B3 仍要做（不靠單層）。
3. **拓撲 → VPS 唯一寫手 + 技術衝突控制。** 保住手機「寫」筆記目標。B8 技術控制:Robin 寫完 annotation 設唯讀（`chmod 444`）+ UI 顯式解鎖鈕 / sync-conflict 偵測 lint;不靠紀律。
4. **範圍 → C/E 先、B 後。** 文章/影片較 vault-resident、不觸 `state.db`/儲存遷移;先上讓手機能記文章/影片筆記。B 書（含 `Vault/Books/` refactor + `state.db` 處理 + foliate 公開面）後置為獨立切片。

> **裁決後仍開的小問題**:`state.db`（book_id/progress）即使 EPUB 改走 `Vault/Books/` 仍 local-only。B 切片前要決:把 book metadata/progress 也移進 vault（md/json sidecar）讓它隨 Syncthing，或在 B 切片做一次性 `state.db` 遷移。留給 B 切片設計時定。

## 其他風險與緩解（非 blocking）
- **Map 階段成本/延遲/可靠性**：VPS 無 GPU，本機 Qwen 不在 → 摘要走雲端，且**預設 fallback 是 Sonnet 不是 Gemini**（llm_router.py:23-25）;`update_merge` 走 Opus 4.7，單次約 $0.15-1.20（kb_writer.py:633-638）。本機 Qwen 的 sub-second 高可靠 → 變成 network-dependent。緩解:設妥 `MODEL_ROBIN`、評估保留 Qwen 當 queue worker。
- **VPS 資源**：EPUB parse（ebooklib/bs4/markdownify）+ 文章抓圖 + 雲端 LLM 往返。緩解:上線前在 VPS 實測長 EPUB / 長文章 / map-reduce ingest 的耗時。
- **CJK**：非 ASCII 檔名 / EPUB 內部路徑未見測試（Gemini）。緩解:加 CJK 上傳/解析測試。
- **失去本機離線 ingest 能力**：mobile-first 取捨,可接受。

## 不做（out of scope）

- N519 LLM promotion extractor（另條線）
- 多使用者 / 多密碼 auth 升級
- ADR-043 Centaur 永久層（正交）
- 把 EPUB 搬進 vault 讓 Syncthing 帶（違反 ADR-024 claim-dense、且大 binary 拖累 Syncthing）— 維持 upload-to-VPS

## 切片計畫（v2 — security + data-integrity 前置）

> v1 的「先 config 啟用」順序被 panel 否決:在補 auth + 釐清衝突策略前拿掉 `DISABLE_ROBIN` = 把未授權寫面開上公網。重排:

- **Slice 0（blocking gate，先做）**：B1-B4 安全（補 `/api/books/*` auth + 空密碼 fail-fast + preflight + dry_run 釐清）+ 前置 reverse-proxy auth（Cloudflare Access）+ B8 技術衝突控制（annotation 寫後唯讀 + 解鎖 / sync-conflict lint）。**這層不過，不拿掉 `DISABLE_ROBIN`。**
- **Slice 1（C/E 上線）**：B9（釐清 + 確保 `Inbox/`、`Watchlist/`、`Files/`、`KB/Annotations/` 在 VPS Syncthing set）；裝依賴、submodule；拿掉 `DISABLE_ROBIN`；驗文章/影片 reader 註記 golden path（含手機實機）。**不觸 state.db/書儲存。**
- **Slice 2（B 書上線）**：`book_storage` refactor 改錨 `Vault/Books/`（Syncthing）+ `state.db` 遷移/sidecar 決定 + foliate 公開面 + CJK 上傳測試。
- **觀測**：上線後盯 VPS 資源 + 主動掃 `*.sync-conflict-*`（ADR-030 indexer 會靜默忽略）。

## v1 → v2 變更摘要
- 撤回「全 surface 開、mostly-config、低風險」框架（panel 證偽）。
- 新增 B1-B9 blocking requirements（安全 + 資料完整性 + 衝突控制）。
- 衝突模型從「靠紀律」改為「需技術控制」;主風險重定為人機衝突。
- 修正 fact:books.py 上傳真行號（:283/:314/:386/:403）、`data/books` repo-root 錨定（非 cwd）、fallback 預設 Sonnet。
- 4 個開放 fork（書儲存 / auth proxy / 拓撲 / 範圍）升級為待 sign-off。
- 切片重排:security+data-integrity 前置於「拿掉 DISABLE_ROBIN」。
