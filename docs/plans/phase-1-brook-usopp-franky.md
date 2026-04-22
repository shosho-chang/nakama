# Phase 1 Plan — Brook + Usopp + Franky 三路並行

**Date:** 2026-04-22
**Priority:** Brook = Usopp = Franky > Chopper（Chopper 在 Phase 3）
**Scope:** 讓修修可以「提主題 → Brook 寫 → Bridge approve → Usopp 發到 shosho.tw」全流程 + Franky 監控基礎設施

---

## 前置 blocker（修修手動處理）

1. **跑一遍 [runbook: setup-wp-integration-credentials](../runbooks/setup-wp-integration-credentials.md)**（約 90 分鐘）
2. **挑選 Brook 訓練文章**（見 `F:\Shosho LifeOS\Projects\Brook 風格訓練.md`，每類 5-10 篇）
3. **決定是否裝 Bricks AI Studio**（推薦裝，等 Phase 2 Claude Design 整合才真正用）

這三件事不完成前，Phase 1 code 可以寫但不能 end-to-end 跑。

---

## Phase 1.0 — 基礎架構（week 1）

### 1.0a `shared/wordpress_client.py`
- `WordPressClient(base_url, user, app_password)` config-driven
- `.post.create(title, content, status, categories, tags, ...)` → post_id
- `.post.update(post_id, ...)`
- `.post.publish(post_id)` / `.post.unpublish(post_id)`
- `.post.list(category=..., tag=..., per_page=20)`
- `.media.upload(file_path, alt_text)` → media_id
- `.seopress.set_meta(post_id, title, description, focus_keyword, canonical)`
- Retry with exponential backoff（3 次）
- httpx async client
- Test: mock + VCR cassette + `@pytest.mark.live_wp`

### 1.0b `shared/approval_queue.py`
- 見 ADR-006 資料模型
- `ApprovalQueue()` CRUD
- 整合進 `state.db`（新 table）

### 1.0c Bridge UI — `/bridge/drafts`
- `thousand_sunny/routers/bridge_drafts.py`
- Queue table / detail / approve / reject / edit / export-to-obsidian
- 美學對齊 design-system.md（不走 AI slop default）

### 1.0d `agents/usopp/` 骨架
- `agents/usopp/__main__.py` — daemon，poll approval_queue 撿 approved 執行
- `agents/usopp/publishers/wordpress.py` — WP publisher
- `agents/usopp/publishers/base.py` — `PublishTarget` protocol

**交付**：Bridge 手動塞一筆 draft → approve → Usopp 撿起 → publish 到 shosho.tw（用 test post，手動 trash）

---

## Phase 1.1 — Brook 基本 compose（week 2）

### 1.1a `agents/brook/composer.py`
- 輸入：Obsidian project file path（frontmatter: content_type, search_topic, ...）
- 流程：
  1. 讀 frontmatter + `%%KW-START%% ... %%KW-END%%` 區段
  2. 若 KW 區段空白 → call Zoro `/zoro/keyword-research` 填入
  3. call Robin `/kb/research` 拿 concept/entity 列表
  4. 依 content_type 套 style profile（book-review / people / science 三擇一）
  5. Claude Sonnet + prompt caching 產 Gutenberg HTML draft
  6. 附 featured_image_brief + SEO meta（title/description/focus_keyword）
  7. enqueue 到 approval_queue

### 1.1b 三類 style profile extraction
- 等修修挑完文章 → 用 wp-cli 抽 full content → 餵給 Claude extract style features
- 輸出 `config/style-profiles/book-review.yaml`, `people.yaml`, `science.yaml`
- 各 yaml 欄位：tone, vocabulary, sentence_length_pattern, signature_structure, cta_style, anti_patterns（要避免的）

### 1.1c Brook Web UI（`/brook/chat` 既有）整合
- 既有 chat page 加「enqueue to Usopp」按鈕
- 用戶用 chat 迭代滿意 → 按鈕 → 進 approval queue

**交付**：修修在 Obsidian 開一個 Project 檔、標 `content_type: blog`、主題 `睡眠 神經科學` → Brook 產 draft → Bridge → approve → 發到 shosho.tw 當草稿（不自動 publish）

---

## Phase 1.2 — Franky 監控核心（week 2-3，和 Brook 並行）

**Scope**：依 [ADR-007](../decisions/ADR-007-franky-scope-expansion.md) slim 版，Phase 1 只做 VPS + WP + Nakama service + R2 backup verify + UptimeRobot 外部 probe + 告警去重 + 週報。**GSC / GA4 / Cloudflare 已拆到 [ADR-008](../decisions/ADR-008-seo-observability.md)（Phase 2），本階段不做。**

### 1.2a `agents/franky/health_check.py`
- systemd timer 每 5 分鐘跑（flock 避免重疊）
- 檢查：VPS RAM/CPU/disk、Nakama service、WP×2 site HTTP、`/healthz` endpoint
- `health_probe_state` 表：連 3 次 fail 才升 Critical（避免單次網路抖動刷屏）

### 1.2b `agents/franky/alert_router.py`
- 三級告警分派（Critical → Franky Slack DM + UptimeRobot SMS / Warning → Bridge / Info → log）
- `alert_state` 表去重（15 分鐘內同類告警只發一次，見 ADR-007 §4）

### 1.2c `agents/franky/r2_backup_verify.py`
- 每日 cron 驗證 R2 有新備份檔（大小合理、SHA 可讀回）
- state.db snapshot 每日推到 R2（ADR-007 §5）

### 1.2d Bridge `/bridge/franky` dashboard + 週報
- 圖表：RAM trend / cron success rate / R2 backup status / 告警次數 / LLM 成本
- Weekly digest：每週一早上 10:00 Slack DM（VPS 平均資源、cron 成功率、備份狀態、告警次數、LLM 花費）
- `/healthz` endpoint 供 UptimeRobot 每 5 分鐘探測（外部 probe，不走 VPS 告警通道）

**交付**：修修 Slack 收到 Franky 「Nakama 啟動」DM + `/bridge/franky` 顯示即時數據 + VPS down 時 UptimeRobot email/SMS 通知繞過 VPS

---

## Phase 1.3 — 端到端整合測試（week 3）

### 1.3a E2E smoke test
腳本：`tests/e2e/phase1_publish_flow.py`（`@pytest.mark.live_wp`）
1. 建立一個 test project file 在 vault
2. 跑 Brook composer
3. 驗證 approval_queue 有 draft
4. 模擬 Bridge approve
5. 驗證 Usopp 確實 POST 到 shosho.tw
6. 驗證 SEOPress meta 已寫
7. 手動去 shosho.tw 看文章
8. cleanup：trash 該 test post

### 1.3b 實戰測試
- 修修提一個真實主題
- 全流程跑
- 修修 review draft、改、approve
- 上到 shosho.tw 當 **draft**（不 publish，先保留 30 分鐘讓修修驗看起來 OK）
- 確認無誤 → 手動 Bridge 再送 publish 或手動 WP admin 發布

**交付**：修修親手發一篇 Phase 1 全流程產的文章到 shosho.tw

---

## Phase 1.4 — 穩定化（week 4）

- 修 E2E 測試發現的 bug
- 寫 `docs/capabilities/*.md` capability cards
- 寫 `README` 更新 agent status
- 更新 ADR-001 反映新狀態（Franky 擴編、Usopp 上線）
- Code review → squash merge → VPS deploy

---

## 階段里程碑

| Week | 里程碑 |
|---|---|
| Week 0 | 修修跑 runbook + 挑訓練文章 |
| Week 1 | shared lib + Bridge approval queue + Usopp 骨架可測 |
| Week 2 | Brook composer 跑通 + Franky 健康監控上線 |
| Week 3 | E2E smoke test pass + 實戰發第一篇 |
| Week 4 | 穩定 + code review + deploy + doc |

每週 Nami Morning Brief 會彙整這週 milestone 進度。

---

## 不在 Phase 1 做（避免 scope creep）

- **GSC 每日排名 / GA4 讀者分析 / Cloudflare 流量監控**（已拆到 [ADR-008](../decisions/ADR-008-seo-observability.md) Phase 2，blocked by Phase 1 + 72h soak）
- Brook 圖片自動生成（見 project_brook_image_pipeline，Phase 2）
- Repurpose（blog → IG carousel，Phase 2）
- FluentCRM newsletter 自動發（Phase 2）
- Chopper 社群互動（Phase 3）
- Sanji 社群觀察（Phase 3）
- FB / IG / YouTube 發布（Phase 4）
- Bricks AI Studio 實際整合（裝了但 Phase 2 再用）
- Claude Design 實際整合（Phase 2）
- Obsidian vault 雙向同步（已拆到 [ADR-006b](../decisions/ADR-006b-obsidian-vault-sync.md) Phase 2 research）

---

## 風險 & 對策

| 風險 | 對策 |
|---|---|
| WP bot 帳號 Application Password 失效 | Franky 每日健康檢查驗證，失效立即 Critical 告警 |
| SEOPress REST endpoint 行為和文件不符 | Phase 1.0a 測試階段驗證實際行為，若 broken 就手動走 post_meta 設定 |
| 大文章 payload 撐爆 SQLite | 文章 > 100KB 時 payload_source 強制走 obsidian vault，DB 只存 metadata |
| state.db 單點故障 | Franky 每日備份到 R2 |
| 修修挑文章慢 / style profile 延後 | Brook Phase 1.1b 可先用 few-shot（2-3 篇代表作），full profile extraction 延後 |

---

## 下一階段預告（暫定）

**Phase 2**（Brook 升級）：
- 圖片 pipeline（Unsplash / Pexels / Flux）
- Claude Design 視覺協作（Bricks AI Studio 橋接）
- FluentCRM newsletter publishing
- Bridge approval 改進（diff view 升級、keyboard shortcuts）

**Phase 3**（社群整合）：
- ADR-008: FluentCommunity + Chopper 社群互動
- Chopper 三階段 HITL
- Sanji 觀察模式（daily digest / weekly insight / realtime alert）

**Phase 4**（多平台發布）：
- Usopp 多 target adapter（IG / FB / YouTube）
- Repurpose flow（blog → carousel / Reels / Shorts）
