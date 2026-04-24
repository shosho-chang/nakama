# ADR-005b: Usopp WordPress Publishing

**Date:** 2026-04-22
**Status:** Proposed
**Phase:** Phase 1 Week 2-3
**Supersedes section of:** [ADR-005](ADR-005-publishing-infrastructure.md)

---

## Context

Usopp 從 approval queue claim 已核准的 `DraftV1`（schema 見 [ADR-005a](ADR-005a-brook-gutenberg-pipeline.md)），負責推到 shosho.tw 的 WordPress：建 post、寫 SEOPress metadata、觸發 cache purge、切 publish。Multi-model review 三家一致指出幾個 blocker：

- **SEOPress REST API 契約脆弱**（review §2.1）：plugin 升版可能 breaking change，靜默失敗。
- **publish flow 非原子**（review §2.5）：建 post → 寫 SEO → publish 中途 crash 會產生孤兒 post / 重複文章。
- **Auth / secret 未定義**（review §2.6）：application password 洩漏 = 全站 game over。
- **LiteSpeed cache 失效被輕忽**（review §2.7）：依賴 plugin 自動偵測不可靠。

本 ADR 定義 Usopp 如何在這些限制下穩定發布。Brook compose 行為由 ADR-005a 負責；HITL approval 佇列由 ADR-006 負責。

**跨 ADR 邊界約定**（明確列出以避免重複實作）：

- **Gutenberg block validator**：由 [ADR-005a](ADR-005a-brook-gutenberg-pipeline.md) 定義的 `validate_gutenberg_ast()` 負責。Usopp §4 的 `validated` 步驟**消費** ADR-005a 已驗證過的輸出（`DraftV1.content`），**不重複** block syntax / AST 的完整驗證，僅做「WP 端寫入後回讀比對關鍵欄位」這個最後一哩的完整性檢查。
- **Compliance 詞彙黑名單**：由 ADR-005a 的 Brook compose guardrail 在 draft 生成階段初步掃描，Usopp 在 publish 前再做一次終端 compliance check（見 §10），兩層為 defense-in-depth。
- **DraftV1.content 介面**：Usopp 只透過 ADR-005a 承諾的 serializer 介面取得 WP 可接受的 HTML / block markup，不直接假設 content 的 internal format（raw HTML vs JSON AST）。

## Decision

### 1. Publish State Machine

Usopp 為每個 draft 維護持久化 state（存 `state.db` 的 `publish_jobs` 表）：

```
queued
  ↓ claim (atomic, reliability.md §2)
claimed
  ↓ media_uploaded（若有 featured image）
media_ready
  ↓ post_created（status=draft）
post_draft
  ↓ seo_written（或 seo_skipped 降級）
seo_ready
  ↓ validated（fetch post 回來比對 content 一致）
validated
  ↓ published（PATCH status=publish）
published
  ↓ cache_purged
done
```

每步寫 `publish_jobs` 表後才前進下一步。Crash 恢復時依當前 state 續跑（reliability.md §10）。

Retry 規則（reliability.md §5）：
- 5xx / timeout → tenacity exponential backoff（2→4→8 秒，max 3 次）
- 4xx auth / permission → 不 retry，直接進 DLQ
- 同步失敗 3 次 → DLQ + Critical alert

### 2. Idempotency 設計（reliability.md §1）

每個 publish job 帶兩層 idempotency key：

- **Nakama 側**：`publish_jobs.draft_id`（UNIQUE index），重複 claim 同 draft_id 直接回既有結果
- **WordPress 側**：WP post custom field `nakama_draft_id`，Usopp `create_post` 前先 `GET /wp/v2/posts?meta_key=nakama_draft_id&meta_value=...`，命中即視為已發

粗流程：

```python
def publish(draft: DraftV1) -> PublishResultV1:
    job = publish_jobs.get_or_create(draft.draft_id)
    if job.state == "done":
        return PublishResultV1(status="already_published", post_id=job.post_id)
    existing = wp.find_by_meta("nakama_draft_id", draft.draft_id)
    if existing:
        job.mark_done(post_id=existing.id, reason="wp_side_dedup")
        return PublishResultV1(status="already_published", post_id=existing.id)
    # ... state machine 推進（進入 §2.1 的 race-safe claim）
```

#### 2.1 Race Condition 防護（選項 A：Application-level advisory lock）

上面流程的 `find_by_meta` 與 `create_post` 之間非原子：兩個 Usopp worker 同搶同一 draft，雙方都看到 `existing=None` 後各自 `create_post`，會產生重複文章。

**決定採用選項 A：Nakama 側 advisory lock（透過 `shared/locks.py` 封裝 SQLite `BEGIN IMMEDIATE` 交易）**，不採用選項 B（WP 端 `register_post_meta` 加 `unique => true`）。

**為何選 A 而非 B**：
1. WordPress core 的 `register_post_meta` 沒有原生 SQL-level UNIQUE constraint 支援（`postmeta` table 的 `meta_key` 本來就不 unique），要達成「`nakama_draft_id` 全站唯一」需寫 custom plugin + `pre_insert_post` filter 自建唯一性檢查，複雜度高於 Nakama 側處理。
2. Usopp 是唯一的 publisher agent（reliability.md §2 的 atomic claim 已保證同一 draft 只有一個 worker claim 成功），真正的 race 只發生在 `publish_jobs.claim` 後到 `create_post` 回傳之間——這段完全在 Nakama 側，lock 放 Nakama 才是正確的作用域。
3. Nakama 側 lock 可直接在現有 `state.db`（WAL mode）以 `BEGIN IMMEDIATE` 達成，不引入新基礎設施。

**實作**：

```python
# shared/locks.py
@contextmanager
def advisory_lock(conn: sqlite3.Connection, key: str, timeout_s: float = 5.0):
    """SQLite 層 advisory lock，同 key 同時只有一個 holder。"""
    # 內部用 locks 表 + UNIQUE(key) + BEGIN IMMEDIATE 實作
    ...

# agents/usopp/publisher.py
def publish(draft: DraftV1) -> PublishResultV1:
    job = publish_jobs.get_or_create(draft.draft_id)
    if job.state == "done":
        return PublishResultV1(status="already_published", post_id=job.post_id)
    with advisory_lock(state_db, key=f"usopp_draft_{draft.draft_id}", timeout_s=5.0):
        existing = wp.find_by_meta("nakama_draft_id", draft.draft_id)
        if existing:
            job.mark_done(post_id=existing.id, reason="wp_side_dedup")
            return PublishResultV1(status="already_published", post_id=existing.id)
        # ... 在 lock 內完成到 post_draft 狀態（含 create_post 回傳 post_id）
        # lock 離開後，WP 端已有 nakama_draft_id meta，後續 worker 的 find_by_meta 會命中
```

**同時仍要求 WP 側 `register_post_meta` 正確設定**（即使不用作唯一性約束），因為 WP REST API 預設不允許按 custom meta 查詢 post：

```php
// WP functions.php 或 nakama-publisher plugin
register_post_meta('post', 'nakama_draft_id', [
    'show_in_rest'  => true,
    'single'        => true,
    'type'          => 'string',
    'auth_callback' => function() { return current_user_can('edit_posts'); },
]);
```

沒有這行，§2 的 `find_by_meta` 會靜默回傳空陣列，雙層防護退化為單層，advisory lock 崩潰後會復發重複文章（見開工 Checklist）。

### 3. SEOPress 脆弱性防禦（review §2.1）

**契約隔離層**：`shared/schemas/external/seopress.py` 定義 v1 schema，`extra="forbid"`（schemas.md §8）。任何 payload 不符合直接 raise `SEOPressSchemaDriftError`。

**降級策略（硬規則）**：

```
正常：POST /wp-json/seopress/v1/posts/{id}（REST 路徑）
  ↓ schema drift 或 404/410
Fallback A：直接寫 post meta keys
  - _seopress_titles_title
  - _seopress_titles_desc
  - _seopress_analysis_target_kw
  （透過 POST /wp/v2/posts/{id} meta 欄位）
  ↓ 仍失敗
Fallback B：publish 照發，SEO meta 留空
  + WARNING log + Critical alert（修修 24h 內人工補 SEO）
```

Fallback A 的 meta key 在 ADR 層固定（抓 SEOPress 9.4.1 source code 求證後寫下）：

```python
SEOPRESS_META_KEYS_V941 = {
    "title": "_seopress_titles_title",
    "description": "_seopress_titles_desc",
    "focus_keyword": "_seopress_analysis_target_kw",
    "canonical": "_seopress_robots_canonical",
}
```

**CI smoke test**：每次部署跑一個 staging WP 的 write-then-read 驗證（見 §8 測試）；plugin 升版後紅燈立現。

### 4. Atomic Publish（review §2.5）

Post 永遠先建 `status=draft`，全驗證通過後才切 `status=publish`：

```python
# Step 4a: 建 draft
post = wp.create_post(
    title=draft.title,
    content=draft.content.raw_html,
    status="draft",                     # 關鍵：先 draft
    slug=draft.slug_candidates[0],
    categories=[cat_id_map[draft.primary_category]] + secondary_ids,
    tags=[tag_id_map[t] for t in draft.tags if t in tag_id_map],
    meta={"nakama_draft_id": draft.draft_id},
)
# Step 4b: 寫 SEO
seo_result = write_seopress_meta(post.id, draft)
# Step 4c: 驗證（讀回來比對）
fetched = wp.get_post(post.id)
assert fetched.meta["nakama_draft_id"] == draft.draft_id
# Step 4d: 切 publish（若 Bridge approve 選 publish；選 schedule 則 status=future）
wp.update_post(post.id, status="publish")
```

任一步 crash → WP 側留的是 draft（沒上線），人工介入可直接在 WP 後台處理或 Usopp 恢復續跑。

**安全網**：每日 Franky cron 掃 `status=draft` 且 `nakama_draft_id` 非空但 `publish_jobs.state != done` 的 post → Critical alert。

### 5. LiteSpeed Cache Purge（Day 1 實測後修訂，2026-04-24）

**原提案（已作廢）**：Publish 成功後**顯式**呼叫 LiteSpeed purge endpoint，不依賴 plugin 自動偵測。

**Day 1 實測結論**（完整紀錄見 [docs/runbooks/litespeed-purge.md](../runbooks/litespeed-purge.md)）：
- **REST endpoint 不存在** — `POST /wp-json/litespeed/v1/purge` 回 HTTP 404 `rest_no_route`；LiteSpeed plugin v1/v3 namespace 無任何 purge route（v3 是 QUIC.cloud CDN 管理）。原提案的 endpoint 是虛構的。
- **admin-ajax** 需 wp-admin session nonce，Python headless 無法合理取得。
- **WP-CLI via SSH** 需 daemon 端權限放大，不建議。
- **意外發現（已採用）**：LiteSpeed plugin hook 到 WP `save_post`，**Usopp 走 WP REST API 的寫入路徑天然觸發 auto-invalidate**（實測：hit → POST update → miss → 2s 後 hit re-populated）。explicit purge call 完全不需要。

**修訂後規則**：
- `LITESPEED_PURGE_METHOD=noop`（`.env.example` 與 VPS 雙方皆此值）為**生產正解**，不是 fallback。
- `shared/litespeed_purge.py` 保留 `purge_url()` 簽名 + state machine `cache_purged` stage，供未來若出現**非 WP-REST 寫入路徑**（e.g. 直接改 DB / wp-cli batch 腳本）時重新接上的錨點。目前所有寫入皆透過 WP REST，故 `cache_purged=False` 是合法值，不代表失敗。
- `PublishResultV1.cache_purged = false` 語意從「purge 失敗或未嘗試」改為「WP plugin hook 已處理 cache，explicit purge unnecessary」。
- SLO「Cache purge 成功率 > 95%」作廢（不再量測 explicit call 成功率）；改以「post 發布後 X 秒內 homepage 反映」作 Phase 2 SLO 候選。

**原 hard rule「顯式呼叫、不依賴 plugin 自動偵測」的放寬理由**：multi-model review 提出此硬規則是為防止「依賴 plugin 可能失靈的偵測機制」。Day 1 實測證明 `save_post` 是 WP core hook 而非 LiteSpeed 的自偵測行為，可信度等同 WP 本身；硬規則只對「不經 WP core hook 的寫入路徑」適用（目前無此類路徑）。

### 6. Category / Tag 策略

**Category**：
- Phase 1 **固定 13 個 slug → WP category ID 的 map**，啟動時讀 `GET /wp/v2/categories` 建快取
- Draft 的 `primary_category` slug 不在 map → fail fast（拼錯防禦，review §3 Grok 獨到觀點）
- **禁止**自動建 category

**Tag**：
- Draft 的 `tags` 從 ADR-005a 已過濾為既有 slug
- Usopp 再做第二層驗證：slug 不在既有 497 → skip + WARNING log，不自動建

### 7. Auth / Secret / 最小權限（review §2.6）

| 項目 | 規範 |
|---|---|
| WP user 角色 | 自訂 `nakama_publisher`（繼承 Editor，明確禁止 `edit_users`/`manage_options`/`install_plugins`） |
| Application password 存放 | VPS `.env` 權限 `0600`，擁有者 `nakama`，group/others 無讀取 |
| Password 輪換 | 每 90 天手動輪換；WP `application_passwords` API 留舊 password 30 天緩衝 |
| 傳輸 | 一律 HTTPS；HTTP 請求立即失敗 |
| HMAC 範圍 | 僅用於 Nakama 內部 agent 間通訊（event bus），**不**用於 WP REST（WP 只認 application password） |
| Log 遮罩 | password/token 永不出現在 log，替以 `key_id=wp_prod_a9` 尾 4 碼（observability.md §9） |
| Rate limit | Usopp 單進程限 1 req/sec 對 WP，避免 LiteSpeed/WAF 擋 |

**`wp_kses_post` 陷阱**（review §3 Claude 獨到 #2）：Editor 角色會被 `wp_kses_post` 過濾 style/figure attr。`nakama_publisher` 自訂角色需明確 grant `unfiltered_html` capability，或 Brook 產出 AST 時本身就不含被過濾的 attr（建議後者，更安全）。

### 8. 測試策略（review §2.4）

**Docker WP staging**：`docker-compose.yml` 跑 WP 6.9.4 + SEOPress 9.4.1 + LiteSpeed（若可 Docker），複製 192 篇 subset（20 篇覆蓋三類文章）供測試。

**CI**：
- Unit：`responses` library mock WP REST（所有 endpoint 的 recorded cassette）
- Integration：針對 staging WP 做 write-then-read，validate schema drift
- Smoke：部署前跑 5 篇測試 draft 的 end-to-end publish + rollback

**生產永遠不是測試環境**。CI 拒絕打生產 WP（`PYTEST_WP_BASE_URL` 必須非 shosho.tw）。

### 9. Schema（援引 [schemas.md](../principles/schemas.md)）

`shared/schemas/publishing.py`（續 ADR-005a）：

```python
class PublishRequestV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    draft: DraftV1
    action: Literal["publish", "schedule", "draft_only"]
    scheduled_at: AwareDatetime | None = None  # 僅 action=schedule
    featured_media_id: int | None = None       # Bridge approve 時指定
    reviewer: str                               # 修修 Slack user ID

class PublishResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    status: Literal["published", "scheduled", "draft_only", "already_published", "failed"]
    post_id: int | None = None
    permalink: str | None = None
    seo_status: Literal["written", "fallback_meta", "skipped"]
    cache_purged: bool
    failure_reason: str | None = None
    operation_id: constr(pattern=r"^op_[0-9a-f]{8}$")
    completed_at: AwareDatetime
```

`shared/schemas/external/seopress.py` 與 `shared/schemas/external/wordpress.py` 負責外部 API response 解析（anti-corruption layer，schemas.md §8）。

### 10. Compliance Guardrail（發佈前健康內容法規檢查）

健康 / wellness 內容在台灣受《藥事法》《食品安全衛生管理法》《醫療法》規範，**Usopp publish 前必須做一次終端 compliance check**，即使 Brook compose 階段已初篩、HITL 已人工核准。雙層設計為 defense-in-depth（reliability.md §9）。

**觸發條件**：`DraftV1.content` 或 `DraftV1.title` 掃描到高風險詞彙。詞彙清單由 `shared/compliance/medical_claim_vocab.py` 維護（初版清單另以 PR 提出，範例類別）：

- 療效聲稱：治療、治癒、療效、療程、根治
- 診斷語：診斷、確診
- 藥物類比：特效、神奇、專治、替代藥物
- 絕對斷言：百分之百、保證、無副作用

**處理流程**：

```python
class PublishComplianceGateV1(BaseModel):
    """
    Publish 前終端 compliance scan 結果（Brook 入隊時先跑一次、Usopp claim 後 publish 前再跑一次）。

    與 ADR-005a §2 的 `DraftComplianceV1` 不同：
    - `DraftComplianceV1`（ADR-005a）：compose snapshot，Brook self-check 結果（含 disclaimer 宣稱）
    - `PublishComplianceGateV1`（本 schema）：publish gate，雙方在 enqueue + claim 各掃一次（defense in depth）

    任一 bool flag 為 True 時：
    - Bridge HITL UI 隱藏一般 approve，改顯示加強 HITL 的兩步驟確認
    - Usopp claim 後若 `ApprovalPayload.reviewer_compliance_ack != True`，立即 fail 回 approval queue
    """
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    medical_claim: bool = False          # 療效 / 診斷 / 藥物類比詞彙命中
    absolute_assertion: bool = False     # 絕對斷言命中
    matched_terms: list[str] = []        # 命中的詞彙原文（供 HITL 檢視）

# Usopp publish() 前置檢查
flags = compliance.scan(draft)
if flags.medical_claim or flags.absolute_assertion:
    # 強制加強 HITL：即使 approval_queue 已標 approved 也不自動 publish
    approval_queue.reopen_for_compliance_review(
        draft.draft_id,
        flags=flags,
        message=f"偵測到高風險詞彙 {flags.matched_terms}，請修修明確確認後重新核准",
    )
    return PublishResultV1(
        status="failed",
        failure_reason="compliance_flag_requires_explicit_review",
        ...,
    )
```

**硬規則**：

- 任何 `PublishComplianceGateV1` 有 flag 為 `True` 的 draft，**禁止自動 publish**，不論 `approval_queue.status` 狀態
- 加強 HITL 路徑必須有**修修明確文字確認**（例如 Bridge UI 打勾 "我已閱讀並確認本文無違反藥事法 / 醫療法"），非一般的 approve 按鈕
- flag 歷史寫入 `publish_jobs.compliance_flags`（JSON），供稽核追溯

**跟 ADR-005a / ADR-006 的分工**：

- ADR-005a（Brook）：compose 階段初篩，若命中應主動改寫（例：「治療失眠」→「幫助睡眠」），減少進入 Usopp 的 flag 數量
- ADR-006（Approval Queue）：HITL UI 需顯示 `PublishComplianceGateV1.matched_terms`，並在有 flag 時隱藏一般 approve 按鈕，改顯示加強 HITL 的兩步驟確認
- ADR-005b（Usopp）：publish 前最後一關攔截，確保即便上游遺漏或 UI 繞過，也不會發出有法規風險的內容

**與 ADR-006 的介面約束**：`ApprovalPayloadV1` 須包含 `compliance_flags: PublishComplianceGateV1` 欄位；若 flag 為 True 而 `reviewer_compliance_ack: bool` 為 False，Usopp `claim` 後立即 fail 並回退至 approval queue。

## Consequences

### 正面
- SEOPress 升版不再靜默失敗（schema drift 立即紅燈 + 三層降級）
- Publish 原子性 + idempotency → crash 重啟不產生孤兒 / 雙發
- 顯式 cache purge → Franky / 修修發布後立刻看得到新內容
- 最小權限 user + log 遮罩 → 即使 .env 洩漏，攻擊面僅限 Editor 權限內

### 風險與緩解

| 風險 | 緩解 |
|---|---|
| WP `save_post` hook 不再自動 purge（plugin 升級或失靈） | 需重新引入 explicit purge（見 §5 修訂版 — anchor 已保留在 `shared/litespeed_purge.py`）；偵測方式：監控新 publish 後 homepage reflect lag（Phase 2 SLO 候選） |
| SEOPress Fallback A meta key 在新版被改名 | CI smoke test 紅燈；Fallback B 無 SEO 發布 + 人工補 |
| `nakama_publisher` 角色缺 `unfiltered_html` → 產出被過濾 | ADR-005a 的 AST 僅用白名單 block，無特殊 attr |
| WP REST 5xx 狂 retry 撐爆 LiteSpeed | 1 req/sec rate limit + tenacity max 60 秒 budget |
| Idempotency race（worker A/B 同搶 → 重複文章） | §2.1 advisory lock 限制 `find_by_meta`→`create_post` 原子；lock 失效有 WP 側 meta 查詢作第二層 |
| 健康內容法規命中（療效 / 診斷詞彙） | §10 三層防線：Brook 初篩 → Approval Queue 顯示 flag + 加強 HITL → Usopp publish 前終端攔截 |

### SPOF（reliability.md §4）

| SPOF | 影響 | 緩解 |
|---|---|---|
| WP / shosho.tw | 所有 publish 停 | Phase 1：Usopp job 留在 queue，每 5 分鐘 retry；Franky DM Critical |
| state.db（publish_jobs） | 所有 state 丟 | WAL mode + 每日 R2 備份（reliability.md §3） |
| Application password | 無法 auth | 輪換備援：每次輪換時先新建 + 部署 + 驗證成功才刪舊 |
| SEOPress plugin 停用 | SEO 降級到 Fallback B（WARNING） | 不阻塞 publish |
| LiteSpeed plugin 停用 | cache 不即時 | 不阻塞 publish |

### Idempotency
- `draft_id` unique index（Nakama 側）+ `nakama_draft_id` post meta（WP 側）雙層
- `claim_approved_drafts` atomic claim（reliability.md §2）
- `PublishResultV1.status="already_published"` 為明確回報

### Schema Version
- `PublishRequestV1` / `PublishResultV1` / 外部 schema 均 v1
- SEOPress / WP 外部 schema 綁 plugin 版本（檔名或 constant 註明 `_V941`），升版要建新 V 版本

## SLO（observability.md §5）

| 指標 | 目標 |
|---|---|
| 單篇 publish（含 SEO + cache purge）p95 | < 10 秒 |
| Publish 成功率（到 status=published） | > 98% |
| SEOPress 寫入成功率（含 fallback A） | > 99%；Fallback B 觸發每月 < 1 次 |
| ~~Cache purge 成功率~~ | ~~> 95%（失敗非阻塞）~~ — 作廢 2026-04-24（§5 修訂版，WP hook 自動處理；不再量測 explicit call）|
| Schema drift 偵測 MTTR | < 1 小時（CI smoke + Critical alert） |

## 開工 Checklist

**Schema（schemas.md）**
- [ ] `shared/schemas/publishing.py` 補 `PublishRequestV1` / `PublishResultV1`
- [ ] `shared/schemas/external/wordpress.py`、`seopress.py` 建立 + top-of-file 註 plugin 版本
- [ ] 所有 external schema `extra="forbid"` + anti-corruption parser
- [ ] `schema_version` 欄位齊全；外部 schema 含 plugin 版本常數

**Reliability（reliability.md）**
- [ ] `publish_jobs` 表 schema + `draft_id` UNIQUE + state 欄位 Literal
- [ ] Atomic claim pattern（§2）實作 + concurrent worker lock test
- [ ] Idempotency 雙層（Nakama + WP meta）
- [ ] `shared/locks.py` advisory_lock（§2.1）實作 + 兩 worker 同搶同 draft 的 concurrency test（預期只產生 1 篇）
- [ ] WP 側 `register_post_meta('post', 'nakama_draft_id', show_in_rest=true, auth_callback=...)` plugin / `functions.php` 片段 + staging 驗證 `find_by_meta` 可用
- [ ] Tenacity retry config：5xx/timeout only、max 3 次、60 秒 budget、exponential backoff + jitter
- [ ] DLQ 入隊（§8）+ Bridge 顯示
- [ ] Timeout：本機 WP 3 秒、LLM 60 秒
- [ ] Crash 恢復：啟動時掃 `publish_jobs` 非終態 → 續跑
- [ ] SPOF 表列入

**Observability（observability.md）**
- [ ] 每個 publish job 帶 `operation_id`（從 draft 繼承或新生）
- [ ] Structured log 記 `draft_id / post_id / state / step / http_status / latency_ms / cache_purge_status`
- [ ] `publish_duration_ms` histogram by step（create_post / write_seo / purge）
- [ ] `external_api_errors_total{api=wp|seopress|litespeed, status_code}` counter
- [ ] `publish_jobs_in_state{state}` gauge（每分鐘 snapshot）
- [ ] Application password / HMAC 絕不進 log（§9）
- [ ] `/healthz` 加 WP 連線檢查

**安全**
- [ ] `nakama_publisher` 角色註冊 script（`add_role` + capabilities 白名單）
- [ ] `.env` 檔案權限 0600 + ownership 檢查在部署腳本
- [ ] Password 輪換 runbook（`docs/runbooks/rotate-wp-app-password.md`，Phase 1 可先草稿）
- [ ] Audit log 記 publish 動作到 `/var/log/nakama/audit.log`（observability.md §8）

**測試**
- [ ] Docker WP staging `docker-compose.yml` + 20 篇 seed data
- [ ] `responses` cassette 覆蓋 WP / SEOPress / LiteSpeed 主要 endpoint
- [ ] Atomic claim concurrency test（兩 worker 同搶 3 筆）
- [ ] Idempotency test：同 draft_id 連打 3 次只發 1 篇
- [ ] SEOPress schema drift simulation test（故意回傳新欄位 → Fallback A 啟動）
- [ ] CI 禁連生產 WP guard

**Compliance（§10）**
- [ ] `shared/compliance/medical_claim_vocab.py` 初版詞彙清單（以 PR 形式提出）
- [ ] `shared/schemas/publishing.py` 補 `PublishComplianceGateV1`
- [ ] Usopp publish 前置掃描 + 命中時 reopen approval queue 邏輯
- [ ] Test：高風險詞彙 draft 不會自動 publish（即使 approval_queue 已 approved）
- [ ] ADR-006 介面對齊：`ApprovalPayloadV1.compliance_flags` + `reviewer_compliance_ack`

**LiteSpeed purge Day 1 研究**
- [x] Day 1 研究完成 2026-04-24：`docs/runbooks/litespeed-purge.md` 決策紀錄顯示 WP `save_post` hook 已自動處理 cache，`LITESPEED_PURGE_METHOD=noop` 為生產正解；§5 已修訂反映此結論

## 跟 ADR-005a / ADR-006 的介面

- 上游：從 `approval_queue`（ADR-006）claim `ApprovalPayloadV1`，內含 `DraftV1`（ADR-005a）
- 下游：寫回 `approval_queue.status = 'published' | 'failed'` + `PublishResultV1` 存 `publish_jobs`

## Open Questions

1. ~~LiteSpeed purge 的 auth 方式修修要選 (a) WP-CLI via SSH （b) LiteSpeed API token （c) admin-ajax nonce？~~ → **已解決 2026-04-24**：三方案均無需採用。WP `save_post` hook 自動處理 cache invalidation，`LITESPEED_PURGE_METHOD=noop` 為生產正解。詳見 §5 修訂版與 `docs/runbooks/litespeed-purge.md`。
2. `nakama_publisher` 角色要不要 grant `unfiltered_html`？ → 建議不給（AST 本身就安全），若 ADR-005a 需要 raw HTML block 再重新評估
3. 192 篇既有文章要不要 backfill `nakama_draft_id`？ → 不必，新發的才帶
4. `shared/compliance/medical_claim_vocab.py` 初版詞彙清單由誰提案？ → 修修與 Brook agent owner（同一人）另以 PR 提出，本 ADR 僅定義介面

## Notes

- 本 ADR 拆自 ADR-005，回應 multi-model review §2.1、§2.4、§2.5、§2.6、§2.7 blocker
- VPS benchmark（review §2.10）統一在 ADR-007 Franky 範疇處理，本 ADR 僅列為前置依賴
- 2026-04-22 提出

## Changelog

### 2026-04-22 — Revision 2（multi-model verification 回應）

回應三家驗證（claude-sonnet Conditional Go、gemini Go + race condition、grok no-go + compliance）：

- **Context** 新增「跨 ADR 邊界約定」段落，明確 Gutenberg validator 來自 ADR-005a、compliance 詞彙黑名單兩層分工、`DraftV1.content` 介面由 ADR-005a 承諾（回應 grok B1、claude P1、gemini 1.1）
- **§2 Idempotency** 新增 §2.1 Race Condition 防護，選定**選項 A（Nakama 側 advisory lock via `shared/locks.py`）**，並說明為何不選選項 B（WP `register_post_meta unique`）；同時要求 WP 側 `register_post_meta` 正確 `show_in_rest` 設定以讓 `find_by_meta` 運作（回應 gemini race condition、claude P4 blocker B2）
- **§5 LiteSpeed** 由「第一週實測」明確為「**Day 1 指派**」，並定義 fallback 規則：三方案全不可行時 fallback 到 TTL 600s 等待，寫入 `docs/runbooks/litespeed-purge.md`（回應 gemini W2、claude P3）
- **§10 Compliance Guardrail**（新章節）：定義 `PublishComplianceGateV1`、觸發詞彙類別、加強 HITL 流程、硬規則（有 flag 即禁止自動 publish）、與 ADR-005a / ADR-006 的三層分工介面（回應 grok 健康內容 compliance blocker、claude P5）
- **Consequences 風險表** 補 2 列：idempotency race 緩解、compliance 命中三層防線
- **Open Questions** 更新 Q1（Day 1 實測 + fallback）、新增 Q4（medical_claim_vocab 來源）

**未解項目（承認並留給後續 ADR / PR）**：
- claude P2（validated 步驟只比 meta 不比 content）、P6（SLO 分母定義）留待 Phase 1 實作期細化
- grok 新發現的 external probe、cost 估算、tag governance、state.db restore 列為 Medium / Low，不阻塞開工，由 Phase 1 / ADR-007 Franky 範疇承接
- `nakama_publisher` role 的完整 capability 白名單（claude P4 細節）留在 `docs/runbooks/wp-nakama-publisher-role.md` 提案，ADR 不逐條列出
