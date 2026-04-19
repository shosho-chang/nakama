---
name: 外部 API 契約陷阱清單
description: 跟 Claude / Gemini / Auphonic 對接過程中踩過的 API 契約變動與非顯而易見 quirk
type: reference
created: 2026-04-17
updated: 2026-04-17
confidence: high
---

踩過的坑（PR #22 live-run 實測發現），寫 code 或 debug 同類問題時先來這裡對一下。

## Claude API（Anthropic SDK）

### Claude 4.7+ 廢除 `temperature`
- 傳 `temperature` 參數會收 400 `temperature is deprecated for this model`
- 舊的 Sonnet 4 / Haiku 4.5 還吃
- 解法：wrapper 預設 `temperature=None`，只在非 None 時才塞進 kwargs
- 檔案：`shared/anthropic_client.py`

### Model ID 別寫死日期後綴
- `claude-opus-4-20250918` 已不存在（404）
- 用別名 `claude-opus-4-7`、`claude-sonnet-4-6`、`claude-haiku-4-5-20251001`
- Claude 4.7 的正式 model ID：`claude-opus-4-7`

## Gemini API（google-genai SDK）

### Pydantic class 要傳給 `response_schema`，不是 `response_json_schema`
- `response_json_schema` 期望 dict（JSON schema）；傳 Pydantic class 會 `TypeError: Object of type ModelMetaclass is not JSON serializable`
- `response_schema` 才支援 Pydantic class
- 檔案：`shared/gemini_client.py`

### Gemini 2.5 Pro dynamic thinking 會吃爆 `max_output_tokens`
- 預設 `max_output_tokens=1024` 對結構化輸出常常 thinking 先吃完 → `finish_reason=MAX_TOKENS`、`response.parsed/text` 都是 None
- 解法：預設調到 8192；錯誤訊息加 `finish_reason / thoughts_token_count / candidates_token_count` 診斷

## Auphonic REST API

### `filtermethod` 只接受 `hipfilter` / `autoeq` / `bwe`
- `voice_autoeq` / `voice_autoeq_bandwidth` 舊版名稱已不認（400 明列合法 enum）
- `autoeq` 對應 voice EQ

### 下載 URL 不要手組
- 正確格式是 `/api/download/audio-result/{uuid}/{filename}`，不是 `/api/production/{uuid}/download/{filename}`
- `production.json` 回傳的 `output_files[0].download_url` 直接有正確 URL，用它就好

### 產物 `ending` 欄位不含 `.`
- `output_files[0].ending` 回傳的是 `"wav"`，不是 `".wav"`
- 手組檔名時要自己補 `.`

## python-dotenv

### 空值行的 inline `#` 註解會被當值讀
- `AUPHONIC_OUTPUT_BITRATE=       # MP3/AAC bitrate...`：python-dotenv 可能把 `# MP3/AAC bitrate...` 整串當 bitrate 的值
- 有值 + inline `#` 通常會正確剝掉；空值 + `#` 是陷阱
- 解法：wrapper 讀 env 時自己剝 `#` 後內容（見 `shared/auphonic.py:_strip_inline_comment`）
- 或更穩：`.env.example` 的註解放上一行，不放同一行

## trendspy（Google Trends 後繼套件）

### `language=` kwarg 會被截斷到 `[:2].lower()`
- `Trends(language="zh-TW", tzs=480)` → `self.language = "zh"`（trendspy 0.1.6 client.py line 22）
- 連 deprecated `hl=` fallback 也做同樣 [:2] 截斷
- pytrends 以前保留完整 `hl="zh-TW"` 送 Google，trendspy 沒有任何建構子路徑保留
- Workaround：`tr._default_params['hl'] = 'zh-TW'` 後塞（破壞封裝，慎用）
- 實際影響小：Google Trends 主要用 `geo=` 做地區、`hl=` 主要影響回應語言；zh vs zh-TW 差異多在相關查詢的繁/簡混入

### `related_queries()` 是 flat dict（與 pytrends 的 nested dict 不同）
- pytrends: `{keyword: {"top": df, "rising": df}}` → 要 `related[topic].get("top")`
- trendspy: `{"top": df, "rising": df}` → 直接 `related.get("top")`
- 遷移時四個 API（`TrendReq`/`build_payload`/`related_queries`/`interest_over_time`）都要改，不是 drop-in

### package 別名叫 `trendspy` 不是 `trendspyg`
- `trendspyg`（flack0x）是完全不同的套件（RSS + CSV scraper），**不要裝錯**
- 正確：`pip install trendspy>=0.1.6`（sdil87/trendspy）
