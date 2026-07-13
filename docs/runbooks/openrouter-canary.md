# Runbook：OpenRouter transport canary

> 安全地把 api-tier LLM 呼叫從原生 SDK 切到 OpenRouter，**per-agent 漸進**。
> 設計見 [ADR-049](../decisions/ADR-049-openrouter-transport.md)。

## 前置（一次性）

- [x] OpenRouter 帳號 + `OPENROUTER_API_KEY` 寫進 VPS `.env`。
- [x] BYOK：OpenRouter 後台 Integrations 貼上 OpenAI / Anthropic / xAI key（credit 從原帳號 draw down）。
- [ ] 後台資料政策設 `data_collection: deny`（避免未發布內容被記錄；需要時對特定呼叫加 ZDR）。
- [ ] 存少量 OpenRouter credit（付沒自帶 key 的 model + 超過每月 1M 請求後的 5% BYOK 費）。
- [ ] BYOK 自動繞道計費：client 端 `provider.allow_fallbacks=false` 已預設關，不需後台再設。

## Canary 前盤點

查 VPS `.env` 是否有 `AUTH_*` / `NAKAMA_REQUIRE_MAX_PLAN`，列出哪些 (agent, task) 解析為 subscription —— 這些留 `claude -p` CLI、**不**被 OpenRouter 接管。本機 `.env` 已確認全 api-tier。

## Step 1 — 單一 agent canary（Sanji，低風險社群口吻）

VPS `.env`（**不要**設全域 `LLM_TRANSPORT`，只切 Sanji）：

```
LLM_TRANSPORT_SANJI=openrouter
MODEL_SANJI=gemini-2.5-flash      # 或 gpt-5-mini，A/B 比語氣（兩個都已上架）
```

改 `.env` 後 **reload / restart service** 才吃到新 env（dotenv 在 process 啟動時載入）。跑 1–3 天，然後：

1. Bridge `/bridge/models` → 確認 **Sanji 那列顯示 `OpenRouter`、其餘 `native`**（per-agent override 生效）。
2. Bridge cost panel → Sanji 的 cost 有實際數字。
3. **對帳**：OpenRouter 後台 Activity 花費 vs cost panel 的 Sanji cost，記下 ±誤差與原因（cache / rounding）。
4. 抽幾則 Sanji 社群回覆，確認語氣 / 繁中正常（`gemini-2.5-flash` vs `gpt-5-mini` 擇一）。

對得上 + 語氣 OK → Step 2。不對 → 移除 `LLM_TRANSPORT_SANJI` 並 restart 即退。

## Step 2 — Anthropic api-tier agent（驗 cache 計費）

挑一個 api-tier、`claude-*` 的 agent（如 Zoro / Nami）：

```
LLM_TRANSPORT_ZORO=openrouter
```

重點驗 **Anthropic-via-OpenRouter 的 cache 計費**（當初排斥 LiteLLM 的痛點）：cost panel 的 Anthropic cost 要對得上 OpenRouter 後台（含 cache read / write）。對得上才證明 D5 成立。

## Step 3 — 全域擴

逐 agent 加 `LLM_TRANSPORT_<AGENT>=openrouter`，或直接全域 `LLM_TRANSPORT=openrouter`（訂閱 / xAI carve-out 仍自動留原生）。

## 回滾

- 單一 agent：移除 `LLM_TRANSPORT_<AGENT>`。
- 全部：`LLM_TRANSPORT=native`（或移除）。
- 兩者都需 **restart service** 才生效（env 在啟動載入）。kill-switch 設計讓回滾是「改一行 + 重啟」，秒級。
