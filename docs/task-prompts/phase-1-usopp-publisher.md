# Task Prompt — Phase 1 Usopp WordPress Publisher

**Framework:** P9 六要素（CLAUDE.md §工作方法論）
**Status:** 凍結，待 dispatch
**Source ADR:** [ADR-005b](../decisions/ADR-005b-usopp-wp-publishing.md)（正典，本 prompt 不重述細節）
**Plan section:** [phase-1-brook-usopp-franky.md §1.0a / §1.0d](../plans/phase-1-brook-usopp-franky.md)
**Upstream dependency:** PR #72 foundation（ADR-006 approval queue ✅ + ADR-005a schema ✅）

---

## 1. 目標

把已 approved 的 `DraftV1` 從 approval queue 可靠地推到 shosho.tw WordPress（建 post → 寫 SEOPress meta → 切 publish → purge LiteSpeed cache），crash 可恢復、雙發不可能、SEO plugin 升版不靜默失敗、台灣健康法規詞彙命中時硬攔截。

## 2. 範圍

**新增檔案**（明確路徑）：

| 路徑 | 內容 |
|---|---|
| `shared/wordpress_client.py` | WP REST + media + post CRUD；httpx + tenacity；1 req/sec rate limit |
| `shared/schemas/external/wordpress.py` | WP REST response anti-corruption schema（ADR-005b §9） |
| `shared/schemas/external/seopress.py` | SEOPress v9.4.1 payload schema + `SEOPRESS_META_KEYS_V941` 常數（ADR-005b §3） |
| `shared/schemas/publishing.py`（擴增） | 補 `PublishRequestV1` / `PublishResultV1` / `PublishComplianceGateV1`（ADR-005b §9、§10） |
| `shared/locks.py` | `advisory_lock()` SQLite 單機 advisory lock（ADR-005b §2.1） |
| `shared/compliance/medical_claim_vocab.py` | 台灣藥事法／醫療法詞彙黑名單 + `compliance.scan(draft)` function（ADR-005b §10） |
| `shared/seopress_writer.py` | 三層降級寫入（正常 REST → Fallback A post meta → Fallback B skip + alert） |
| `shared/litespeed_purge.py` | 顯式 purge；Day 1 研究結論決定 endpoint；無則 fallback 等 TTL |
| `agents/usopp/publisher.py` | State machine + `publish(draft) → PublishResultV1` 主流程 |
| `agents/usopp/__main__.py`（改寫） | Daemon：poll approval_queue → `claim_approved_drafts` → publisher.publish() |
| `migrations/002_publish_jobs.sql` | `publish_jobs` 表 schema（ADR-005b §1 state machine 的持久化） |
| `docs/runbooks/litespeed-purge.md` | Day 1 研究產出（ADR-005b §5） |
| `docs/runbooks/rotate-wp-app-password.md` | Phase 1 草稿版（ADR-005b §7） |
| `docs/runbooks/wp-nakama-publisher-role.md` | `nakama_publisher` WP role capability 白名單（ADR-005b Notes） |

**測試**（檔案結構對應）：

- `tests/shared/test_wordpress_client.py` — `responses` cassette + retry 行為
- `tests/shared/test_seopress_writer.py` — schema drift simulation + fallback chain
- `tests/shared/test_locks.py` — 兩 worker 同搶同 key 的 concurrency test
- `tests/shared/test_compliance.py` — 高風險詞彙掃描
- `tests/agents/usopp/test_publisher.py` — state machine + idempotency + crash recovery
- `tests/e2e/test_phase1_publish_flow.py`（`@pytest.mark.live_wp`） — Docker WP staging 全流程
- `tests/fixtures/wp_staging/docker-compose.yml` — WP 6.9.4 + SEOPress 9.4.1 + 20 篇 seed（ADR-005b §8）

## 3. 輸入

| 來源 | 內容 | 已 ready？ |
|---|---|---|
| `approval_queue` | `ApprovalPayloadV1`（含 `DraftV1` + `PublishComplianceGateV1` + `reviewer_compliance_ack`） | ✅ PR #72 |
| `DraftV1.content.raw_html` | Gutenberg builder 輸出的 WP-safe HTML | ✅ PR #72 |
| `shared/approval_queue.claim_approved_drafts()` | Atomic claim API | ✅ PR #72 |
| `.env` 憑證 | `WP_SHOSHO_APP_PASSWORD` / `WP_FLEET_APP_PASSWORD` | ✅ setup 完（見 `project_phase1_infra_checkpoint.md`） |
| `nakama_publisher` WP role | 自訂 role，Editor 繼承並拔 user/plugin/option 權限 | ❌ 本任務內 runbook 產出 + 修修於 WP admin 手動建 |
| `register_post_meta('post', 'nakama_draft_id', show_in_rest=true)` WP snippet | 讓 REST 可以 `find_by_meta` 查到 idempotency key | ❌ 本任務內 plugin 片段 + 修修 paste 到 `functions.php` |
| SEOPress plugin v9.4.1 | staging & prod 已安裝 | ✅ |
| LiteSpeed plugin | prod shosho.tw 已啟用 | ✅（auth 方式 Day 1 實測） |

## 4. 輸出

- `agents/usopp/` 可 `python -m agents.usopp` 起 daemon，每 30 秒 poll 一次 queue
- `shared/wordpress_client.py` 是**獨立模組**，其他 agent（Franky 健康檢查、未來 Chopper）可直接 import 用
- `PublishResultV1` 透過 `approval_queue.mark_published()` / `mark_failed()` 寫回 queue
- `publish_jobs` 表每步落地，crash 可續跑
- 7 份文件（見 §2 runbook 清單）
- **capability card**：`docs/capabilities/wordpress-publisher.md`（對齊既有 `wordpress-client.md` 風格，開源準備；`feedback_open_source_ready.md`）
- 更新 `docs/capabilities/wordpress-client.md` 反映實作進度（原為 placeholder）

## 5. 驗收（Definition of Done）

**不漏 ADR-005b 開工 Checklist 任何一項**（見 ADR §開工 Checklist），外加：

- [ ] 全 repo `pytest` pass，無 regression（baseline：669 tests pass）
- [ ] `ruff check` + `ruff format` clean（`feedback_ci_precheck.md`）
- [ ] Docker WP staging 跑過一次完整 publish flow，至少 5 篇 test draft 全綠
- [ ] `@pytest.mark.live_wp` 測試對 staging 可跑，對生產 `PYTEST_WP_BASE_URL` guard 擋下
- [ ] **Atomic claim concurrency test**（10 worker × 20 draft）無雙發、無 lock error
- [ ] **Idempotency test**：同 `draft_id` 連打 3 次，WP 端只有 1 篇 post
- [ ] **Compliance blocker test**：含「治癒」的 draft 即使 approval_queue 已 approved，Usopp `publish()` 直接 fail 回 queue 並 log WARNING
- [ ] **SEOPress schema drift simulation**：mock 回傳新欄位 → Fallback A 自動啟動、WARNING log、Critical alert 發出
- [ ] **Crash recovery**：故意在 `seo_ready` 狀態 kill process，重啟 daemon 能續跑到 `done`
- [ ] LiteSpeed purge Day 1 決策已寫入 `docs/runbooks/litespeed-purge.md`
- [ ] `/healthz` endpoint 新增 WP 連線檢查（Franky 會 poll 這個）
- [ ] Capability card + runbook 三份文件完工
- [ ] P7 完工格式交付（CLAUDE.md §P7 完工格式）

**VPS 部署 gate**（實作可先在開發機完成，部署由修修授權）：

- [ ] `feedback_vps_env_drift_check.md`：VPS `.env` 新 key（`LITESPEED_PURGE_METHOD` 等）實際存在
- [ ] VPS `thousand-sunny` + `nakama-gateway` 重啟流程寫入 PR description（`feedback_vps_two_services.md`）

## 6. 邊界（明確不碰）

**不在 Phase 1 做**（scope creep 守門）：

- ❌ **FluentCRM newsletter**：Phase 2（ADR-005b 明列不涵蓋）
- ❌ **FluentCommunity 發文／Chopper 社群回覆**：Phase 3
- ❌ **多 target adapter（IG / FB / YouTube）**：Phase 4
- ❌ **Obsidian vault 雙向同步**：[ADR-006b](../decisions/ADR-006b-obsidian-vault-sync.md) Phase 2 research
- ❌ **Cloudflare / GA4 / GSC 監控**：[ADR-008](../decisions/ADR-008-seo-observability.md) Phase 2
- ❌ **litestream 連續 replication**：Phase 2（Phase 1 daily R2 snapshot 由 Franky 負責）
- ❌ **多 worker / fencing token**：Phase 2（Phase 1 單 worker + `_conn_lock`）
- ❌ **圖片自動生成**：`project_brook_image_pipeline.md` Phase 2
- ❌ **自動建 WP category / tag**：ADR-005b §6 硬規則禁止
- ❌ **`nakama_publisher` 授與 `unfiltered_html`**：ADR-005b Open Question 2 建議不給；ADR-005a 的 AST 白名單已足夠
- ❌ **192 篇既有文章 backfill `nakama_draft_id`**：ADR-005b Open Question 3 決定不做

**不能碰的既有檔案**（避免副作用）：

- `shared/approval_queue.py` / `shared/schemas/approval.py` / `shared/schemas/publishing.py` 的 V1 schema 形狀**不改**；如需新欄位走 V2 + migrator（`schemas.md §3`）
- `state.db` PRAGMA / connection helper 的既有行為不改
- `shared/gutenberg_builder.py` / `shared/gutenberg_validator.py` 是 ADR-005a 的 canonical constructor，**只消費**不重寫
- Brook compose 流程（`agents/brook/`）不動，Usopp 是 queue 的下游 consumer
- Franky 的 health_check / alert_router 由 ADR-007 負責；本任務只新增 `/healthz` 的 WP 連線檢查 hook，不實作 Franky 告警路徑

**架構決策需要回來問**（`feedback_ask_on_architecture.md`）：

- 若 Day 1 LiteSpeed 三方案全不可行 → fallback 到 TTL 等待是 ADR 已決的，執行即可；但若發現第四方案（例如自架 purge service）→ 停下來問修修
- 若 `wordpress_client.py` 的介面設計偏離 capability card（`docs/capabilities/wordpress-client.md`）→ flag deviation（`feedback_design_deviation_discovery.md`），不默默改

---

## 實施順序建議（不是強制）

切成三個可獨立 PR 的 slice，每個 slice 都能單獨 merge：

1. **Slice A — 基礎設施**（3-4 天）
   `shared/wordpress_client.py` + `shared/schemas/external/` + `shared/locks.py` + Docker WP staging + 單元測試
   → 其他 agent 也能開始用 wp_client，Franky 的 WP 健康檢查 unblock

2. **Slice B — Publisher 主流程**（3-4 天）
   `shared/seopress_writer.py` + `shared/litespeed_purge.py` + `shared/compliance/` + `agents/usopp/publisher.py` + `migrations/002_publish_jobs.sql` + 整合測試
   → Usopp 可手動餵 draft 跑全流程

3. **Slice C — Daemon + E2E**（2-3 天）
   `agents/usopp/__main__.py` daemon + `/healthz` WP 檢查 + E2E test + runbook + capability card
   → 可 VPS 部署，修修實戰發第一篇

## 交付方式

每個 slice 開 feature branch（`feature/usopp-publisher-slice-a` 等），PR 走 `feedback_pr_review_merge_flow.md`：自動跑 code-review → 報告 → 等修修授權 → squash merge → pull + 刪 branch。

每個 PR 交付時附 [P7 完工格式](../../CLAUDE.md)（What changed / Impact / Self-review / Remaining）。
