---
name: Agent SDK 支援 OAuth 訂閱額度（推翻舊記載）
description: claude-agent-sdk 可走 CLAUDE_CODE_OAUTH_TOKEN 吃訂閱額度；2026-07-29 遷移計畫「不支援」的記載是錯的
type: reference
---

**`claude-agent-sdk` 的 `query()` 可以走 Claude 訂閱額度**，不是只能吃 Anthropic API credit。

`docs/plans/2026-07-29-nami-agent-sdk-migration-plan.md:209` 寫「不追求用 subscription quota（文件已確認 Agent SDK 不支援）」——**這句話是錯的**，2026-08-18 實測推翻。

**驗證方法（可重跑）**：拔掉 `ANTHROPIC_API_KEY`、餵一個故意無效的 `CLAUDE_CODE_OAUTH_TOKEN`，跑一次最小 `query()`：

- 回 `Failed to authenticate. API Error: 401 OAuth access token is invalid.` → OAuth 路徑是活的（它去驗 token 了）
- 若回「找不到 API key」才代表不支援

**機制**：SDK 自帶一支真正的 Claude Code CLI binary（`<dist-packages>/claude_agent_sdk/_bundled/claude`），裡面 72 處引用 `CLAUDE_CODE_OAUTH_TOKEN`、也有 `setup-token` 指令。`_internal/transport/subprocess_cli.py` 的 env 合併把整份環境變數傳進子進程，只清 OTEL 的 `TRACEPARENT`/`TRACESTATE`，**不碰 auth 變數**。`ClaudeAgentOptions.env` 會覆寫繼承的環境（合併順序 `{**inherited, ..., **options.env}`）。

**How to apply**：
- 要讓某條 SDK 路徑走訂閱，用 `ClaudeAgentOptions.env` 傳 `{"CLAUDE_CODE_OAUTH_TOKEN": token, "ANTHROPIC_API_KEY": ""}`——**必須同時清空 API key**，兩憑證並存時 CLI 的優先序沒有文件保證
- 這樣只影響 SDK 子進程，同一行程內其他 `shared.llm` 呼叫照舊
- token 取得：在已登入訂閱的機器跑 `claude setup-token`
- Nami 的實作見 `gateway/handlers/nami.py` 的 `_sdk_auth_env()`（flag：`NAMI_SDK_OAUTH_TOKEN`），PR #1173
- ~~走訂閱後 cost 回報 0~~ **錯，2026-09-25 更正**：走訂閱時 `ResultMessage.total_cost_usd` **不是 0**，是 client 端依內建價目表算的「API 等值估計」。證據：VPS journal 2026-09-24 16:47:20 `nami sdk result: ... subtype=success cost_usd=0.06852`，當時 Anthropic API credit 已耗盡、Nami 走 `NAMI_SDK_OAUTH_TOKEN`，只可能是訂閱。所以這個數字**不是實際花費**，不能入帳；`max_budget_usd` 在訂閱下是否仍會煞車尚未驗證（ADR-070 U2/S0）
- OAuth token 會過期需換發（見 [[feedback_oauth_env_pinning_long_batch]] 的 2026-05-16 事故）；`claude setup-token` 產的是一年期 token
- 別名（`opus` 等）解析成「SDK 內附 CLI 那個版本認得的最新版」，不是真正最新：2026-09-25 VPS SDK 0.2.128 / 桌機 0.2.134 都把 `opus` 解成 `claude-opus-5`、不認得 5.5。要跟上最新版得升 SDK（ADR-070 D10）
- 全面遷移計畫見 `docs/decisions/ADR-070-llm-two-lanes-subscription-and-openrouter.md`；VPS 設定位置見 [[reference_vps_llm_config_locations]]

相關：[[feedback_subscription_first_no_api_spend]]（互動 pipeline 一律走訂閱額度的既有裁決）
