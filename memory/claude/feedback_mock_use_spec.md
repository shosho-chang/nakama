---
name: Mock 第三方 SDK 用 spec/autospec，不要光 patch class name
description: MagicMock 預設對任何 attribute access 都返回 truthy MagicMock，掩蓋「呼叫不存在的 method」bug；要用 spec=ActualClass 或 autospec=True 限制屬性集
type: feedback
originSessionId: cleanup-pr-135-2026-04-25
---

規則：mock 第三方 SDK class 時用 `mock.create_autospec(GoogleCls)` / `patch(..., autospec=True)` / 顯式 `spec=ActualClass`，不要光 `patch("module.Class")` 收 `m.return_value` MagicMock。

**Why:** MagicMock 預設對任何 attribute access / method call 都返回 truthy MagicMock。所以「程式碼呼叫實際不存在的 method」靜默通過 unit test，真打外部 API 才 raise `AttributeError`。

**案例（PR #132 → PR #135 修）**：GSC client 寫 `http = creds.authorize(httplib2.Http(...))`。`Credentials.authorize()` 在 google-auth 2.x 已移除，但 unit test mock `service_account.Credentials.from_service_account_file`，return value 是 MagicMock，`.authorize()` 永遠存在 → 8 個 unit test 全綠 → smoke test 第一秒就炸 `AttributeError: 'Credentials' object has no attribute 'authorize'`。

**How to apply:**

- mock 外部 SDK class 時優先 `autospec=True`：
  ```python
  with patch("shared.gsc_client.service_account.Credentials.from_service_account_file", autospec=True) as m:
      ...
  ```
- 或用 `create_autospec(RealClass)` 建 spec'd MagicMock
- 對 `build()` / 工廠函式回傳的 client 物件，校驗 build kwargs 的真實 type（`isinstance` 校驗），不光看 truthy
- Smoke test 仍是最終防線（mock 再嚴也防不了真實 API 變動）— 修修手動跑或 dev-only marker pytest 一次

**配套教訓**：
- [feedback_test_realism.md](feedback_test_realism.md) — mock 輸入形狀要對齊真實契約
- [feedback_model_construct_bypasses_validators.md](feedback_model_construct_bypasses_validators.md) — pydantic `model_construct()` 跳過 validator 的同類陷阱
- [reference_api_contract_pitfalls.md](reference_api_contract_pitfalls.md) §Google Auth — 此案例的 SDK 變動細節
