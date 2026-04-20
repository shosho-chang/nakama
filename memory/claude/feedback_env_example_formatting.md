---
name: .env.example 註解獨立一行
description: .env.example 不要用 inline 註解 — python-dotenv 會把整串當 value；註解放獨立行
type: feedback
originSessionId: f2ea9d48-f32a-4c33-bf30-c54837e598ec
---
# .env.example 註解規則：獨立一行，不要 inline

**Rule:** `.env.example` 的說明註解必須放在獨立行（`# ...` 起頭），不要寫在 key=value 後面的 inline 位置。

**Why:** python-dotenv 對 inline `#` 註解處理不一致。例如：
```
XAI_BASE_URL=                  # 選填，預設 https://api.x.ai/v1
```
複製到 `.env` 後，dotenv 會把整串 `"# 選填，預設 https://api.x.ai/v1"` 當成 `XAI_BASE_URL` 的值，httpx 收到這字串會噴 `UnsupportedProtocol: Request URL is missing an 'http://' or 'https://' protocol`。PR #51 實作 xAI 時真的踩到這坑，debug 花了 15 分鐘才定位。

**How to apply:**
```
# 正確寫法：說明放上一行
# 選填，覆寫 endpoint；留空自動使用 https://api.x.ai/v1
XAI_BASE_URL=

# 錯誤寫法：inline 註解可能被 dotenv 吞進值
XAI_BASE_URL=                  # 選填，預設 https://api.x.ai/v1
```

另外在讀 env 的 code 端也要做防禦：`os.environ.get(key) or default` 只擋得住空字串，擋不住含 `#` 的垃圾值。最保險是驗證格式後 fallback：
```python
raw = (os.environ.get("XAI_BASE_URL") or "").strip()
base_url = raw if raw.startswith(("http://", "https://")) else "https://api.x.ai/v1"
```

適用範圍：所有 `.env.example` / `.env.*.example` / 寫給部署者參考的 env 模板。
