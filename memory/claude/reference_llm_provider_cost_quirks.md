---
name: 跨 LLM provider 計費/token 欄位陷阱
description: Anthropic / xAI / Gemini usage 回傳 shape 和 cache 計費邏輯的差異，record_api_call 欄位 mapping 要點
type: reference
originSessionId: f2ea9d48-f32a-4c33-bf30-c54837e598ec
---
# 跨 LLM provider 計費陷阱（步驟 2-4 實作血淚）

實作 `shared/anthropic_client.py` / `xai_client.py` / `gemini_client.py` 三家 wrapper 時踩過的 usage response shape 差異，寫進這裡避免下次又踩。

## `prompt_tokens` 是否包含 cached？

| Provider | prompt_tokens 含 cached？ | 計算實際 input |
|---|---|---|
| Anthropic | **否**（`input_tokens` / `cache_read_input_tokens` / `cache_creation_input_tokens` 三欄分開） | 直接用 `input_tokens` |
| xAI | **是**（`prompt_tokens` 含 cached，`prompt_tokens_details.cached_tokens` 另外標） | `prompt_tokens - cached_tokens` |
| Gemini | **否**（`prompt_token_count` 是扣掉 cache 的實際計費 input，`cached_content_token_count` 另外標） | 直接用 `prompt_token_count` |

踩過的坑：xai_client 早期沒扣 cached，結果 cache 命中時 input 被雙重計費。修法在 `_record_usage` 做 `max(prompt_tokens - cached, 0)`。

## `cache_write` 計費

| Provider | cache_write 計費？ | 怎麼填 record_api_call |
|---|---|---|
| Anthropic | **有**（`cache_creation_input_tokens` × 1.25-2× input price） | 實填 `cache_creation_input_tokens` |
| xAI | **無**（implicit auto-cache，寫入不收費） | 固定 0 |
| Gemini | **無**（implicit cache 不收費；走 Context Caching API 建立才有寫入費，那是另一個 API） | 固定 0 |

## Reasoning model 的 thinking token

| Provider | thinking 算 output 嗎？ | 欄位 |
|---|---|---|
| Anthropic | **是**（Claude extended thinking 已併入 `output_tokens`） | 直接用 `output_tokens` |
| xAI | **是**（Grok reasoning 併入 `completion_tokens`） | 直接用 `completion_tokens` |
| Gemini | **是 但分兩欄**（`candidates_token_count` = 實際可讀輸出，`thoughts_token_count` = 內部推理，**兩個都要加**） | `candidates + thoughts` |

踩過的坑：gemini_client 早期只取 `candidates_token_count`，cost DB 少算 60-80%（Gemini 2.5 Pro thinking 實測常為 candidates 的 2-5 倍）。

## Gemini 特別注意：`max_output_tokens` 包含 thinking

**Why:** `max_output_tokens` 是 thinking + candidates 的總上限，不是兩者各自的上限。

**踩過的坑:** `ask_gemini(prompt, max_tokens=200, thinking_budget=512)` → thinking 吃掉 200，實際文字 output 只有 0-10 chars，會觸發「回應為空」RuntimeError。E2E smoke 打到 7 chars 就是這個。

**How to apply:**
- 短 classification / JSON extraction 任務：明確設 `thinking_budget=0` 關掉 thinking
- 或設 `max_tokens ≥ 4 × thinking_budget` 確保實際輸出有空間
- 步驟 4 PR #53 留的 borderline #3：考慮在 `ask_gemini` 加 `thinking_budget = min(thinking_budget, max_tokens // 4)` 自動縮放

## Message role 格式

| Provider | system 訊息位置 | assistant 角色名 |
|---|---|---|
| Anthropic | 獨立 `system=` 參數，**不放 messages** | `"assistant"` |
| xAI (OpenAI-compat) | 既可 `{"role":"system"}` 也可 `system=`（OpenAI 慣例 system 在 messages 第一個） | `"assistant"` |
| Gemini | 獨立 `config.system_instruction`，messages 內 role 只能是 `"user"` / `"model"`（不是 `"assistant"`） | `"model"`（不是 `"assistant"`） |

**How to apply:** 跨 provider `ask_multi(messages)` 時，wrapper 要自己把 `role="assistant"` 轉 `"model"`（Gemini），不能盲 pass-through；role="system" 混進 messages 時 Gemini 會拒絕，要過濾或 fold 進 system_instruction（PR #53 borderline #2 留下處理）。
