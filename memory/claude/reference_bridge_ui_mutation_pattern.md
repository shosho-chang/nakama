---
name: Bridge UI mutation 標準範式
description: Bridge `/bridge/*` 加 mutation endpoint 的標準做法（cookie auth + form post + 303 redirect + native dialog modal），無 JS framework 依賴
type: reference
tags: [bridge, ui, fastapi, pattern, dialog, htmx-alternative]
created: 2026-04-25
updated: 2026-04-25
confidence: high
ttl: 360d
originSessionId: 17b79f57-77eb-4db5-8b5e-806439bf9adf
---
Bridge UI 加 mutation（reviewer 操作 row 的按鈕）走以下範式，PR #140 drafts approve/reject/edit/requeue 是 reference 實作：

## Endpoint
- 走 `page_router`（`prefix="/bridge"`，**不**走 `router = APIRouter(prefix="/bridge", dependencies=[Depends(require_auth_or_key)])`）
- 每個 endpoint 開頭手動 `if not check_auth(nakama_auth): return RedirectResponse("/login?next=...", status_code=302)`
- form data 用 FastAPI `Form(..., min_length=1)`（避免空字串）；JSON body 用 `Form(payload: str)` + 自己 `json.loads`（不是 `Body(...)`，因為瀏覽器原生 form 不送 JSON）
- Reviewer hard-coded：`_REVIEWER = "shosho"` 模組常數，單一使用者前提；多 reviewer 才考慮 `Depends(get_current_user)`
- Errors：404 row 不存在、409 status 不可 transition、400 payload validation 失敗、422 form validation 失敗（FastAPI 自動）
- Success：`return RedirectResponse("/bridge/<list_or_detail>", status_code=303)` — 303 而非 302，POST→GET 語意正確

## UI（Jinja template）
- Status-aware action panel：`{% if draft.status in ('pending', 'in_review') %}...{% elif draft.status == 'failed' %}...{% else %}<read-only note>{% endif %}`
- 簡單動作（approve / requeue）：`<form method="post" action="...">` 直接包 `<button type="submit">` — 不用 fetch / JS
- 需要輸入的動作（reject reason / edit payload）：native HTML5 `<dialog>` element + `onclick="document.getElementById('X').showModal()"` — 不需要 modal framework
- modal `<form>` 包整個 dialog content，submit 直接送 form data 給 endpoint
- Pre-fill：edit modal 的 textarea 用 `{{ payload_pretty }}` 預填（pydantic `model_dump_json(indent=2)` 出來的 JSON）
- Status chip palette：每個狀態獨立顏色（pending 橘 / in_review 琥珀 / approved 翠 / claimed 靛 / failed 緋 / rejected 灰邊 / archived 淡邊 / broken 虛線）— 不要全部 fall through 同一個 chip-pending 顏色

## Tests
- `TestClient` form post：`client.post(url, data={"key": "value"}, follow_redirects=False)`
- 必驗 `r.status_code == 303` + `r.headers["location"]` 對到下一頁
- DB state 每個 endpoint 一個 happy + 1-2 failure（404 / 409 / 422 / 400）

## 為什麼不寫 JSON API path
- Bridge 是內部單一 reviewer 工具，不需要 API client（沒有 mobile / 第三方）
- form post + 303 是 web 標準 pattern，不需要 fetch / spinner / error toast — 失敗顯示 FastAPI 預設 error page 即可
- 之後若 Phase 4 多 reviewer 或要 keyboard shortcut，再考慮加 JSON `/api/drafts/{id}/transition` 變 progressive enhancement

## 適用範圍
- Bridge `/bridge/drafts` ✓ (PR #140)
- Bridge `/bridge/memory` mutation（已有 PATCH/DELETE，走 JSON API；可考慮 future 加 form path 給「忘記 X 個記憶」按鈕）
- Bridge `/bridge/cost` 沒 mutation（純 dashboard）
- 未來 Chopper 社群 review UI / Sanji 公告 review UI 可沿用
