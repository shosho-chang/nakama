# Capability Card — `nakama-wp-client`

**Status:** Phase 1 Slice A merged（PR #73）— live in `shared/wordpress_client.py`
**License:** MIT（計畫開源）
**Scope:** WordPress REST API + SEOPress Pro Python client，site-agnostic、config-driven。用於 Usopp publisher（ADR-005b）及 Franky 健康檢查（ADR-007）。

---

## 能力

| 功能 | 方法 | 必要 WP 端 |
|---|---|---|
| 建 / 改 / 刪 post（含 Gutenberg blocks） | `client.post.create / update / delete` | WP 6.0+ |
| 查 post（filter by category / tag / date） | `client.post.list` | WP 6.0+ |
| 發佈 / 撤下 post | `client.post.publish / unpublish` | WP 6.0+ |
| 上傳 media + set alt text | `client.media.upload` | WP 6.0+ |
| 連結 featured image 到 post | `client.post.set_featured_image` | WP 6.0+ |
| 設 SEO meta（title / desc / focus_keyword / canonical） | `client.seopress.set_meta` | SEOPress 7.5+ |
| 讀 SEO meta | `client.seopress.get_meta` | SEOPress 5.0+ |
| 列 category / tag（唯讀） | `client.taxonomy.list` | WP 6.0+ |
| Webhook HMAC 驗簽 | `client.verify_webhook` | 使用者自建 PHP hook |

## Input / Output 契約

```python
from nakama_wp_client import WordPressClient, PostDraft

client = WordPressClient(
    base_url="https://shosho.tw/wp-json",
    user="bot_usopp",
    app_password=os.getenv("WP_SHOSHO_APP_PASSWORD"),
    timeout=30,
    retries=3,
)

draft = PostDraft(
    title="...",
    content_gutenberg_html="<!-- wp:paragraph --><p>...</p><!-- /wp:paragraph -->",
    slug="my-slug",
    status="draft",
    categories=["book-review"],  # slugs
    tags=["creatine", "brain"],
    excerpt="...",
)

post_id = await client.post.create(draft)
await client.seopress.set_meta(
    post_id,
    title="SEO title",
    description="meta description",
    focus_keyword="肌酸 功效",
)
```

## 不做的事

- 不處理 Bricks builder 的 `_bricks_page_content_2`（專案假設 Bricks 只做 theme template）
- 不做 WP 插件管理（這是維運範疇，交給 Franky 用 wp-cli）
- 不做 user CRUD（bot 帳號由人工在 wp-admin 建）
- 不做 WP.com API（只支援 self-hosted REST）

## 依賴

- Python 3.10+
- `httpx >= 0.25`
- `pydantic >= 2.0`

## 成本

- 自身 zero runtime cost（只打 WP REST）
- WP 端每 post 建立 API ≈ 1-3 次 HTTP call
- 不呼叫 LLM（LLM 由上游 Brook 負責）

## 契約測試

- Unit: mock httpx responses
- VCR: cassette 於 `tests/fixtures/wp_vcr/*.yaml`
- Live: `@pytest.mark.live_wp` + env `WP_LIVE_TEST=1`

## Roadmap

- [ ] v0.1 — Phase 1：post + media + SEOPress
- [ ] v0.2 — Phase 2：bulk update、tag cleanup、Yoast fallback（若未來換 SEO plugin）
- [ ] v0.3 — 支援 WooCommerce product REST（若修修開商店）
