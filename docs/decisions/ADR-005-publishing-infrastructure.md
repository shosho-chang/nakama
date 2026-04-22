# ADR-005: Publishing Infrastructure — Split Index

**Date:** 2026-04-22
**Status:** Superseded by ADR-005a / ADR-005b / ADR-005c on 2026-04-22

---

## Context

本 ADR 原先試圖一次涵蓋 Brook compose 產出、Usopp WP 發布、SEOPress 整合、Bricks template 維護、FluentCRM 與多平台抽象共六件事。Multi-model review（[ADR-005--CONSOLIDATED.md](multi-model-review/ADR-005--CONSOLIDATED.md)）三家一致判定（平均 4.3/10）scope 太寬，混合 Phase 1-3 範圍，審查與開工都卡。

為提升聚焦與可審查性，2026-04-22 正式將本 ADR 拆成三份聚焦 ADR，彼此透過介面契約對接。原 ADR 內容（decision 段落）視為歷史紀錄，實作一律以三份子 ADR 為準。

## 拆分後的 ADR

| 編號 | 聚焦 | Status | Phase |
|---|---|---|---|
| [ADR-005a](ADR-005a-brook-gutenberg-pipeline.md) | Brook compose 結果轉 WP Gutenberg HTML（含 validator、style profile、draft schema） | Proposed | Phase 1 Week 2 |
| [ADR-005b](ADR-005b-usopp-wp-publishing.md) | Usopp 把 draft 推到 WP + SEOPress metadata（含 atomic publish、idempotency、cache purge） | Proposed | Phase 1 Week 2-3 |
| [ADR-005c](ADR-005c-bricks-template-maintenance.md) | Bricks template 人工維護流程（無 code 自動化） | Accepted | Phase 1 docs-only |

拆分後原先的 `shared/fluent_client.py` / 多平台 `PublishTarget` Protocol 一併從 Phase 1 移出，歸入 ADR-008（Chopper / FluentCommunity）與未來 ADR。

## 保留：原決策的 rationale（避免歷史遺失）

- **Gutenberg post_content 做內容格式**：VPS 實測 `post_content` 是標準 Gutenberg blocks（`_bricks_page_content_2` 空），Bricks Post Content element 能正確 render。不走 Bricks 原生內文編輯。
- **SEOPress Pro 9.4.1 做 SEO meta writer**：選它不選 Yoast/RankMath，理由是 SEOPress REST API `POST /wp-json/seopress/v1/posts/{id}` 直接支援 application password 寫 meta，Yoast/RankMath 需自註冊 REST route。
- **既有 category 不新增**（18 個已足，含 9 個 science sub），tag 不自動擴增（已 497 個過多）。
- **Featured image Phase 1 人工**（Envato Elements → WP media library → Bridge 指定 media_id），Phase 2 接 Unsplash/Pexels，Phase 3 Flux 自產。
- **URL slug 英文或拼音**，Brook 產 3 個候選，Bridge 由修修選。

以上決策在三份新 ADR 各自的 Decision 段落被援引與細化。

## Review Blocker 追蹤

Multi-model review §5 列出的 5 個最 blocking 問題在拆分後的分工：

| Blocker | 主責 ADR |
|---|---|
| Draft Object Schema + Gutenberg Validation | ADR-005a |
| 無 Staging 環境 + 無測試策略 | ADR-005a + ADR-005b |
| 狀態機 / idempotency / 原子性 | ADR-005b |
| 資安設計（auth + secret + 最小權限） | ADR-005b |
| VPS 資源 benchmark | 統一入 ADR-007（Franky 範疇）追蹤，兩份子 ADR 列為前置 |

## Notes

- 原 ADR-005 git 歷史仍可查，本檔案為 index，不再維護實作細節
- 拆分日期：2026-04-22，觸發於 multi-model review
