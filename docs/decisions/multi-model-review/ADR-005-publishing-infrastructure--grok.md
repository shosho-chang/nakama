---
source_adr: ADR-005-publishing-infrastructure
reviewer_model: grok-4
elapsed_seconds: 79
review_date: 2026-04-22
---

# ADR-005-publishing-infrastructure — grok 審查

### 1. 核心假設檢驗
這份 ADR 基於以下未明說的假設：
- WordPress REST API 在 VPS 環境下永遠穩定可用，且不會因 plugin 更新或 WP 版本升級導致斷開（例如 SEOPress API 依賴 WP 核心）。
- HITL（修修）審核流程總是及時且無延遲，Bridge 不會成為瓶頸。
- VPS 資源（2vCPU / 4GB RAM）足以應付所有 agent 並行運作，包括 Brook 的內容生成和 Usopp 的 API 呼叫。
- 第三方 plugin（如 SEOPress Pro、Bricks）不會在短期內停更或改變 API 契約。
- 內容創作者（修修）對 Gutenberg blocks 的熟悉度足夠，不會因人工介入導致延遲。

容易出錯的假設：
- VPS 資源假設最易錯，實際上 Brook 的 AI 呼叫或多 agent 並發可能快速耗盡 RAM，導致 MySQL 崩潰或 API 延遲。
- Plugin 穩定性假設易錯，WP 生態常有 breaking changes，SEOPress API 若在 9.4.1 後變更，Usopp 的 publish flow 會直接失效。
- HITL 及時性假設易錯，如果修修忙碌，draft 堆積會讓整個系統 idle，影響自動化目標。

### 2. 風險分析
- (a) 未被提及但會造成生產問題的風險：API 認證洩漏（application password 若存 env var 未加密，VPS 被入侵即暴露）；plugin 相依性衝突（例如 LiteSpeed Cache 與 SEOPress 的互動可能導致 meta 不一致）；數據一致性問題（Usopp 在 POST /wp/v2/posts 後若網路中斷，SEOPress meta 未寫入，導致 SEO 失效）；法規風險（若內容涉及健康建議，無 disclaimer 可能違反台灣醫事法）。
- (b) 已提及但嚴重度被低估的風險：兩個 site 共用 MySQL + 4GB RAM，ADR 只提 RAM 警戒，但低估為系統崩潰（Brook publish 爆量時，MySQL OOM killer 會殺掉進程，導致資料遺失）；tag 過多影響 Brook 選擇品質，被低估為 Phase 2 問題，但實際上會立即造成內容不相關，SEO 排名掉。
- (c) 已提及但嚴重度被高估的風險：Bricks AI Studio 未裝影響 Claude Design 路徑，高估為重大，因為 Phase 1 不依賴它，人工橋接可暫代；tag 清理需求高估為品質大問題，實際上 497 個 tag 若不擴增，Brook 可透過 Robin 過濾，影響有限。

### 3. 替代方案
有更簡單的替代路徑，如使用 headless WordPress（僅用 WP 作為 CMS backend，前端用 Next.js），這能避開 Bricks theme 的相依性，發布只需 API 呼叫，無需 Gutenberg 細節。作者沒選可能是因為 shosho.tw 已建好 WP + Bricks，轉移成本高，但這忽略了長期維護性。

更穩的第三方工具：用 Contentful 或 Sanity.io 取代自建 WP 發布 flow，這些 CMS 有成熟 SDK，支持 Gutenberg-like blocks 和 SEO meta，無需自寫 wordpress_client.py。作者沒選或許因成本（Contentful 有月費）或對 WP 熟悉，但 WP plugin 生態不穩，第三方如 Sanity 有更好版本控制和 API 穩定性，能直接取代 Usopp 的自建邏輯。另一選項是 Yoast SEO 的 REST 擴充（非自建），但 ADR 選 SEOPress 卻沒解釋為何不選更普及的 Yoast（有更大社群支持）。

### 4. 實作 pitfalls
工程師照 ADR 寫，最容易踩坑：
- 在 `shared/wordpress_client.py` 的 API 契約：POST /seopress/v1/posts/{id} 若 id 未即時生成（WP REST 延遲），會 404 錯誤，retry 邏輯需處理 race condition，但 ADR 未指定 timeout/retry backoff，易導致無限迴圈。
- Draft schema 在 Bridge /bridge/drafts：若 draft object 的 content_html 未驗證 Gutenberg 格式（例如缺少 <!-- wp: -->），POST /wp/v2/posts 會 render 失敗，導致 Bricks template 顯示空白；featured_image_brief 的 json schema 未定義 required fields，易因 Brook 輸出不完整而 crash。
- Usopp 的 publish 方法：PublishTarget protocol 的 site 欄位若未驗證（e.g., "shosho.tw" 打錯成 "shosho.com"），會 HTTP 錯誤無 log；PATCH /wp/v2/posts/{id} 的 status=publish 若無權限檢查，會在 HITL approve 後失敗，檔名如 `shared/approval_queue.py` 需加 lock 避免 concurrent approve 衝突。
- Category/tag 邏輯：在 Brook draft 中若 category slug 不存在（e.g., 拼錯 "neuroscience"），POST /wp/v2/posts 會 400 錯誤，無 fallback。

### 5. 缺失的視角
資安缺失最多：未提 API application password 的輪換、HMAC 驗證如何防 replay attack，或 VPS firewall 對 REST 暴露的保護。效能講得太輕：無討論 API 呼叫 latency（Vultr VPS 到 WP 的 roundtrip 若 >500ms，publish flow 會卡），也無 batching 優化。運維缺失：無備份策略（wpvivid-backup-pro 提了但無自動化），升級 WP/plugin 時的 downtime 未計劃。法規輕忽：健康內容若有醫學主張，無 GDPR/台灣個資法考量（e.g., FluentCRM 蒐集 subscriber 資料）。可觀測性弱：無 logging/monitoring 細節（e.g., Usopp error 未推到 Franky）。成本未提：VPS 月費 + plugin license（SEOPress Pro）若 scale up 會爆。 可測試性缺失：無 unit test 建議 for wordpress_client.py 的 edge cases。可維護性輕：shared lib 抽象好但無 versioning，未來開源時易 breaking changes。

### 6. Phase 拆分建議
應拆成獨立 ADR 的內容：SEOPress 整合（包含 custom schema Phase 2）拆成 ADR-005a，因 API 細節複雜；Featured image pipeline（從人工到 Flux API）拆成 ADR-005b，與 project_brook_image_pipeline.md 連結。延後到 Phase 2+：tag 清理和自動圖片生成（Phase 2），social media 發布（Phase 3），FluentCRM 整合（留給 ADR-008）。必須 Phase 1 完成的：核心 publish flow（Brook draft → Usopp API）、Gutenberg content model、category/tag 策略、shared/wordpress_client.py 實作，這些是 MVP 基礎，無此無法自動化部落格。

### 7. 結論
- (a) 整體可行性評分 6/10（基於 WP 生態但過度依賴不穩 plugin，風險低估，替代方案未探討，Phase 拆分不明確）。
- (b) 建議：修改後通過
- (c) 最 blocking 的 1-2 個問題：VPS 資源不足的風險評估（需實測 benchmark Brook/Usopp 負載，否則生產崩潰）；資安視角缺失（加 API 認證和加密細節，否則暴露漏洞）。