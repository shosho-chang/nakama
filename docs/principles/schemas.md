# Nakama Schema 原則

所有跨模組、跨 agent、跨服務的資料結構都必須遵守以下原則。這是一份**無條件規範**，新程式碼與 ADR 直接援引，不用每次重新爭論。

---

## 1. 契約先於實作

任何跨界傳遞的資料（函式參數除外）必須先定義 Pydantic schema，再寫生產該資料或消費該資料的 code。

**跨界的定義**：
- agent → agent（透過 event bus / approval queue / shared state）
- agent → LLM（結構化輸出）
- Nakama → 外部服務（WP REST、Slack、GSC API）
- 外部服務 → Nakama（webhook / polling 結果）
- Nakama → 資料庫（存進 state.db、memory DB 的 JSON payload）

**不是跨界**：
- 同一模組內部的 dataclass / typed dict（自由使用）
- 單次函式呼叫的參數（type hint 足夠）

## 2. Schema 存放位置

```
shared/schemas/
├── __init__.py            # 公開 re-export
├── publishing.py          # ADR-005 系列的 draft / publish_result / focus_keyword
├── approval.py            # ADR-006 的 approval_queue payload + review note
├── monitoring.py          # ADR-007 的 alert_state / health_check / backup_verify
├── external/
│   ├── wordpress.py       # WP REST response shapes
│   ├── seopress.py        # SEOPress metadata
│   ├── cloudflare.py      # CF GraphQL response
│   ├── gsc.py             # Search Console row
│   └── ga4.py             # GA4 dimension + metric
└── events.py              # Event bus message envelope（跨 agent）
```

每份 schema 檔 top of file 註明對應的 ADR 編號，schema 改動時要同步更新 ADR 的 schema 區塊。

## 3. 版本欄位是硬規則

所有進入 **持久化儲存**（state.db、memory DB、檔案）的 schema 必須有 `schema_version: int` 欄位。

```python
class ApprovalPayloadV1(BaseModel):
    schema_version: Literal[1] = 1
    draft_id: str
    target_platform: Literal["wp_shosho", "wp_fleet", "ig", "yt"]
    action: Literal["publish", "update", "schedule"]
    content: GutenbergHTMLV1
    seo: SEOMetadataV1
```

**為什麼**：你會改 schema 的（幾乎必然）。有版本欄位時，migration 是「寫 V1→V2 轉換器」；沒版本時，是「猜這筆資料是舊的還是新的」。

**版本升級流程**：
1. 新增 `class FooV2(BaseModel)`，舊的 `FooV1` 留著不動
2. 寫 `migrate_v1_to_v2(v1: FooV1) -> FooV2`
3. Reader 端：`parse_foo(raw: dict) -> FooV2`，內部 dispatch 依 `schema_version` 欄位
4. Writer 端：永遠寫 V2，不再寫 V1
5. `FooV1` 保留到所有歷史資料 migrate 完（通常一季以後）才刪

## 4. 嚴格模式 `model_config`

每個 schema 都必須設 `extra="forbid"`：

```python
class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ...
```

**為什麼**：外部 API 靜默加欄位很常見（SEOPress、WP、Cloudflare 都會）。`forbid` 讓你第一次收到未知欄位就炸，不會靜默吞掉新資訊。

`frozen=True`：pydantic 的 immutability，避免 downstream 程式碼誤改經過 schema 驗證過的資料。只有 writer owns mutation。

## 5. 時間欄位一律 aware UTC

```python
from datetime import datetime, timezone
from pydantic import BaseModel, Field, AwareDatetime

class Event(BaseModel):
    created_at: AwareDatetime  # Pydantic v2 會強制 tzinfo 非 None
```

**禁止** naive datetime 存入任何 schema。寫入前轉 UTC，顯示時再 convert to Asia/Taipei（見 `reference_vps_timezone.md`）。

**SQLite 儲存**：用 ISO 8601 字串 `"2026-04-22T14:00:00+08:00"`，不用 unix timestamp（debug 困難）。

## 6. ID 慣例

| ID 類別 | 格式 | 例子 |
|---|---|---|
| Draft | `draft_{timestamp}_{short_hash}` | `draft_20260422T140000_a3f2e9` |
| Approval queue row | SQLite auto-increment `int` | `42` |
| Event bus message | UUIDv7（時序可排） | `01K4R...` |
| External IDs | 原樣保留（WP post ID `int`、Slack thread_ts `str`） | — |

Pydantic schema 用 `constr(pattern=...)` 強制格式，不用裸 `str`。

## 7. Enum / Literal 優先於 str

**禁止**：`status: str = "pending"`
**正確**：`status: Literal["pending", "in_review", "approved", "rejected", "published", "failed"]`

**為什麼**：狀態機字串被誤拼成 `"appoved"` 時你想要第一秒就知道，不是三天後 SQL 查詢漏一筆才發現。

## 8. 外部 API Schema 的反脆弱

外部 API（SEOPress、WP、Cloudflare）會在下次小版本改欄位名。策略：

```python
class WPPostResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    title: RenderedField  # nested
    status: Literal["draft", "publish", "pending", "future", "private"]
    date_gmt: AwareDatetime

def parse_wp_response(raw: dict) -> WPPostResponseV1:
    """所有 WP API 回傳都經過這個 parser，不讓 raw dict 流進 business logic。"""
    try:
        return WPPostResponseV1.model_validate(raw)
    except ValidationError as e:
        logger.error("WP response schema drift", extra={"raw": raw, "error": str(e)})
        raise WPSchemaDriftError(f"WP API changed contract: {e}") from e
```

**隔離原則**：外部 API 的混亂止於 `shared/schemas/external/`，**不得**讓 raw dict 流進 agent 邏輯。這是 anti-corruption layer。

## 9. LLM 結構化輸出

讓 LLM 產 JSON 時，用 `ask_llm_structured()`（TBD 新 helper），內部以 Pydantic schema 當 JSON Schema source：

```python
class SEOSuggestion(BaseModel):
    title: constr(min_length=10, max_length=60)
    meta_description: constr(min_length=50, max_length=155)
    focus_keyword: constr(pattern=r"^[一-龥A-Za-z0-9 ]+$")

result: SEOSuggestion = ask_llm_structured(prompt, schema=SEOSuggestion)
```

LLM 輸出不符 schema 時自動 retry 一次（把 validation error 塞回 prompt 當 correction），再失敗就 raise。

## 10. Schema Registry（Phase 2）

當 schema 數量超過 ~20 個時，建 `shared/schemas/_registry.py`：

```python
REGISTRY: dict[tuple[str, int], type[BaseModel]] = {
    ("approval", 1): ApprovalPayloadV1,
    ("approval", 2): ApprovalPayloadV2,
    ("draft", 1): DraftV1,
    ...
}

def resolve(name: str, version: int) -> type[BaseModel]:
    return REGISTRY[(name, version)]
```

讓 generic deserialize code（例如從 state.db 讀任何 payload）可以反查型別。Phase 1 手動 import 即可，先不做 registry。

---

## 11. 不做的事

- **不用 TypedDict / dataclass 代替 Pydantic**。兩者沒有 validation，跨界會踩坑。
- **不讓 schema 變遞迴巨獸**。每個 schema 最多 3 層巢狀，超過就拆成獨立 schema。
- **不在 schema 裡放計算欄位**（用 `@computed_field` 僅限 display / serialize，不做 business logic）。
- **不用 schema 做授權**。`reviewer: str` 只是記錄，驗證走 Bridge auth layer。
