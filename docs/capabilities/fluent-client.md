# Capability Card — `nakama-fluent-client`

**Status:** Phase 2/3 planned（not started）
**License:** MIT（計畫開源）
**Scope:** Fluent 全家桶（FluentCommunity / FluentCRM / FluentCart / FluentMessaging）統一 Python client。

---

## 能力（Phase 2 優先）

### FluentCommunity（社群）
| 功能 | 方法 |
|---|---|
| 列 space 裡的 discussions | `fc.space(slug).discussions.list` |
| 以 bot 身份發 discussion | `fc.space(slug).discussions.create` |
| 回覆特定 discussion | `fc.discussion(id).reply` |
| 取 member profile（含 FluentCRM tag） | `fc.member(user_id).profile` |
| 監聽 webhook（action `fluent_community/feed/created` 等 via PHP mu-plugin） | `fc.webhook.verify` |

### FluentCRM（名單）
| 功能 | 方法 |
|---|---|
| 查 subscriber（by email / tag） | `crm.subscriber.find` |
| 加 / 改 tag | `crm.subscriber.tag / untag` |
| 發送 campaign（排程 / 立即） | `crm.campaign.send` |
| 查 campaign metrics | `crm.campaign.stats` |

### FluentCart（電商，Phase 3）
| 功能 | 方法 |
|---|---|
| 列 product | `cart.product.list` |
| 查 customer order history | `cart.customer.orders` |

## Auth

全系列共用 WP Application Password（REST API 基底）。

## 不做的事

- 不做 FluentSupport（客服）— 未來擴充
- 不做 FluentForm submission CRUD（用 WP REST 原生）

## 依賴

- Python 3.10+
- `httpx >= 0.25`
- `pydantic >= 2.0`
- 相容 `nakama-wp-client`（因為 Fluent 跑在 WP 上，共用 auth）

## 契約測試

- Unit: mock
- Live: `@pytest.mark.live_fluent` + env `FLUENT_LIVE_TEST=1`（要 community 站 bot 帳號）

## Roadmap

- [ ] v0.1 — FluentCommunity 讀 + 寫（Chopper Phase 3）
- [ ] v0.2 — FluentCRM campaign send（Usopp newsletter Phase 2）
- [ ] v0.3 — FluentCart + Messaging（未來）
