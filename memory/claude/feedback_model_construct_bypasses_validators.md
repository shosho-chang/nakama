---
name: pydantic model_construct() 跳過 validators — 消費端要補 defensive check
description: pydantic model_construct() 不跑 field validators，下游拿到「看似已驗證」的 model 可能帶 naive datetime、空字串、不當值；Usopp publisher Bug 2 實證
type: feedback
tags: [pydantic, schemas, defensive-programming, python]
---
pydantic 的 `Model.model_construct(**fields)` 是 zero-validation 建構，不跑 `@field_validator` / `@model_validator` / 類型 coerce（含 `AwareDatetime` 這種 typed alias）。任何 `model_construct()` 路徑產生的 instance，下游要當作「只通過 dataclass-level 形狀檢查」看待，不能假設 invariants 成立。

**Why:** ADR-005a 明確用 `GutenbergHTMLV1.model_construct()` 避 `_ast_and_html_consistent` validator 無限遞迴（builder 自己是 canonical constructor）。但 Usopp Slice B 的 `PublishRequestV1.scheduled_at: AwareDatetime | None` 遇到同樣路徑時，naive datetime 會一路過到 Stage 4 才在 `astimezone()` 丟 `ValueError`，留下無意義錯誤訊息跟卡住的 `publish_jobs` row。Bug 2 (PR #77 local review 7 blockers 之一) 的根因就是這個。

**How to apply:**

- Schema 作者：寫 validator 時假設「有人會 model_construct() 跳過我」，把最 critical 的 invariant 同時寫成 property 或 method，讓 consumer 可以 on-demand re-check
- Consumer 作者（尤其 agent / state machine 入口）：對 AwareDatetime / constr / min_length 這種「用 pydantic 宣稱但可能被 model_construct 跳過」的欄位做 defensive check，用 raise 自己的 domain error（PublisherError 之類）
- 特別危險的欄位：tz-aware datetime、non-empty str constraint、pattern match constraint、constrained list length
- 兩個場景會踩到：(1) test fixtures 用 model_construct 塞不合格資料；(2) 生產路徑 serialize-round-trip（JSON → dict → model_construct）
- 好例子（Bug 2 修法）：`if request.scheduled_at.tzinfo is None: raise PublisherError("scheduled_at must be timezone-aware")` — 顯式 domain failure 比 astimezone() 的 ValueError 好 debug

**Anti-pattern:**

- 假設 `foo: AwareDatetime` 的 `foo` 一定 tz-aware
- 假設 `foo: constr(min_length=10)` 的 `foo` 一定 `len >= 10`
- 依賴 pydantic 在 transport boundary 前已驗過
