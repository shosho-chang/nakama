---
name: Phase 1 WP 整合憑證 setup 進度 checkpoint
description: 2026-04-22 session 結束時的憑證建立進度，compact 後續用此檔接續
type: project
tags: [phase-1, credentials, setup, checkpoint]
created: 2026-04-22
---

以 2026-04-22 對話為基準的進度記錄。規劃完整紀錄在 [docs/decisions/ADR-005/006/007](../../docs/decisions/) + [docs/plans/phase-1-brook-usopp-franky.md](../../docs/plans/phase-1-brook-usopp-franky.md) + `Case Studies/2026-04-22 Nakama WP + Community 整合架構規劃.md`。

## 已完成的 credential setup

| 項目 | 狀態 | 位置 / env key |
|---|---|---|
| WP bot on shosho.tw | ✅ 完成 | `WP_SHOSHO_APP_PASSWORD` |
| WP bot on fleet.shosho.tw | ✅ 完成（關 FluentSecurity `disable_app_login` 後建成） | `WP_FLEET_APP_PASSWORD` |
| GCP Service Account + JSON | ✅ 完成 | `/home/nakama/secrets/gcp-nakama-franky.json` (chmod 600, root-owned) |
| Service Account email | ✅ 已授權 | `nakama-franky@nakama-monitoring.iam.gserviceaccount.com` |
| GA4 shosho.tw | ✅ 已有，補做 SA Viewer 權限 + Google Signals | `GA4_PROPERTY_SHOSHO` |
| GA4 fleet.shosho.tw | ✅ 新建 property + 貼 gtag 到 Bricks Custom Code | `GA4_PROPERTY_FLEET` |
| GSC shosho.tw Domain property | ✅ SA 授權 | `sc-domain:shosho.tw` |
| GSC fleet.shosho.tw Domain property | ✅ SA 授權 | `sc-domain:fleet.shosho.tw` |
| Cloudflare API Token | ✅ read-only、4 permissions、限 VPS IP | `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_ZONE_ID` |
| Cloudflare R2 Token | ✅ Object Read only | `R2_ACCESS_KEY_ID` + `R2_SECRET_ACCESS_KEY` + `R2_BUCKET_NAME` |
| Slack Franky bot | ✅ 完成（2026-04-22） | `SLACK_FRANKY_BOT_TOKEN` + `SLACK_FRANKY_APP_TOKEN` |
| Brook 風格訓練文章挑選 | ✅ 完成（36 篇：讀書心得 10 / 人物 13 / 科普 13） | `F:\Shosho LifeOS\Projects\Brook 風格訓練.md` |

## 待完成（Phase 1 開工前剩餘）

- [ ] **VPS baseline 壓測**（ADR-007 §6 要求：24 小時實測 CPU p95 < 60%、RAM < 3GB、headroom ≥ 500MB。未通過不開工）
- [ ] 修修快速 review 8 份 ADR + 3 份原則（30-60 分鐘即可）

## 2026-04-22 ADR 修訂全部完工

**產出 8 份 ADR + 3 份原則文件 + 2 輪 multi-model review**：

ADR（`docs/decisions/`）：
- ADR-005（split index，指向 a/b/c）
- ADR-005a — Brook → Gutenberg Pipeline（Phase 1）
- ADR-005b — Usopp → WP + SEOPress（Phase 1）
- ADR-005c — Bricks Template 維護（人工流程，無 code）
- ADR-006 — HITL Approval Queue（Phase 1 瘦身版）
- ADR-006b — Obsidian Vault Sync（Phase 2 research）
- ADR-007 — Franky Infra Monitoring（Phase 1 瘦身版）
- ADR-008 — SEO Observability（Phase 2）

原則（`docs/principles/`）：
- schemas.md — Pydantic 契約、版本欄位、嚴格 extra=forbid
- reliability.md — idempotency、atomic claim、SPOF、retry、DLQ
- observability.md — structured log、operation_id、外部 probe、SLO、alert 三級

**2 輪 multi-model review 結論**（12 個 job 並行跑）：
- Round 1（原 ADR）：3 份 ADR × 3 模型 = 9 job，平均 4.2/10，全數需修
- Round 2（修訂後）：4 份 Phase-1 ADR × 3 模型 = 12 job
  - ADR-007: 3/3 Go ✅
  - ADR-005a/005b/006: 多數 Go 帶小條件，經 3 個並行 agent 修完 schema-level 問題
- 修完後**沒有剩餘 blocker**

Phase 1 實作 gate 清空，等 VPS baseline 壓測 + 修修 ADR review。

## Multi-Model Panel 方法論（Phase 2 值得實作 `shared/multi_model_panel.py`）

- **Gemini 2.5 Pro = 吹哨者**（嚴苛，專抓 schema / 契約 edge case）
- **Claude Sonnet 4.6 = 仲裁者**（平衡、篇幅最長、最具體）
- **Grok 4 = 啦啦隊**（樂觀、短而精煉，會漏 blocker）
- **必三家 triangulate**：單家 review 必漏。Round 2 修訂後 Gemini 最正面（從 3/10 跳到 Go 品質極高），驗證原則文件有效

## 2026-04-22 Multi-Model Review 結論

9 次 review（3 ADR × 3 模型，Claude Sonnet 4.6 / Gemini 2.5 Pro / Grok 4）全部成功。

**評分**：ADR-005 4.3/10、ADR-006 4.3/10、ADR-007 4.0/10。**沒一份直接通過**。

**Top 5 blocker（開工前必解）**：
1. `shared/gutenberg_builder.py` + HTML validator（避免 LLM 產破碎 HTML）
2. approval_queue atomic `claim_approved_drafts`（防 race）
3. Franky 外部 uptime probe（UptimeRobot 類，防 VPS 掛= Franky 掛= 警報掛）
4. Pydantic schema + 版本欄位（`shared/schemas/` 目錄）
5. VPS baseline 壓測

**建議拆分**：
- ADR-005 → 005a (Brook→Gutenberg) / 005b (Usopp→WP+SEOPress) / 005c (Bricks docs)
- ADR-006 → 核心 queue v1 + 006b (Obsidian 雙向同步 Phase 2)
- ADR-007 → 瘦身版（VPS+R2+Slack） + ADR-008 (GSC+GA4+Cloudflare Phase 2)

**模型個性**（對未來 multi-model panel 有用）：
- Gemini 2.5 Pro = **吹哨者**（挑 schema / 契約）
- Claude Sonnet 4.6 = **仲裁者**（平衡風險與實用）
- Grok 4 = **啦啦隊**（樂觀、短而精煉，會漏 blocker）

## 上一輪決議（2026-04-22 第二 session）

- **Bricks AI Studio 延後購買**：Bricks 內建 HTML/CSS paste 已足夠（Phase 1 Brook/Usopp 走 Gutenberg post_content，不碰 Bricks sections）；官方 AI agent + MCP 已 in-progress。需要時再買 Single £10 試即可。
- **Template 維護不做 agent 自動化**：Template 改版頻率低（月級），人工貼 HTML 3 分鐘搞定，不造大砲。Claude Design 出圖 → 人工貼進 Bricks editor。

## 過程中的關鍵踩坑（寫進 runbook 避免未來重犯）

1. **FluentSecurity 關 Application Passwords**：fleet 站比 shosho 多裝 fluent-security，filter `wp_is_application_passwords_available` 被關。解法：WP admin → FluentAuth → disable_app_login toggle off
2. **Cloudflare zone = root domain**：fleet.shosho.tw 共用 shosho.tw zone，`.env` 只要一個 `CLOUDFLARE_ZONE_ID`，Franky 用 `clientRequestHTTPHost` GraphQL dimension 分流量
3. **GCP Service Account email 格式**：`<sa-name>@<project-id>.iam.gserviceaccount.com`，不是用 21-digit Unique ID
4. **GCP 建 SA 時 Step 2/3 完全跳過**：不給 project-level role，權限在 GSC/GA4 各自 property-level 加

## Phase 1 開工 gate

以下三件完成才進 Phase 1 程式實作：
- [ ] 憑證全填（上表剩兩項 + Slack bot）
- [ ] Brook 訓練文章挑好
- [ ] 3 份 ADR 修修 review 通過

修修說「可以開工」→ 開 `feature/phase-1-infra` branch，按 phase-1 plan 週節奏跑。

## 相關文件

- Runbook：[docs/runbooks/setup-wp-integration-credentials.md](../../docs/runbooks/setup-wp-integration-credentials.md)
- Plan：[docs/plans/phase-1-brook-usopp-franky.md](../../docs/plans/phase-1-brook-usopp-franky.md)
- ADR：[ADR-005](../../docs/decisions/ADR-005-publishing-infrastructure.md) / [ADR-006](../../docs/decisions/ADR-006-hitl-approval-queue.md) / [ADR-007](../../docs/decisions/ADR-007-franky-scope-expansion.md)
- Case Study：`F:\Shosho LifeOS\Case Studies\2026-04-22 Nakama WP + Community 整合架構規劃.md`
