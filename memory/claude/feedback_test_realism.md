---
name: Mock 測試資料要反映生產契約
description: 寫 integration test 的 mock 時，輸入形狀要對齊真實 API／prompt 契約，不能只求 test pass
---

為 integration test 設計 mock 回傳時，要先確認生產環境下該回傳的**實際形狀**（看 prompt、看 API schema、看 upstream 契約），再照那個形狀做 mock。不能為了讓測試好寫就 mock 任意組合。

**Why:** PR #21 的 `test_correct_with_llm_applies_verdicts` 我 mock 了 Opus 回傳 line 1 在 `corrections` AND `uncertain` 都有（雙入）。但 Pass 1 的 prompt 明白寫「不確定的修正必須放入 uncertain 清單，不要硬改」— 實際 Opus 只會把 uncertain 放 uncertain dict、不會進 corrections。我的 mock 是不可能出現的情況，結果測試全綠但漏掉 `accept_suggestion` 分支沒寫 corrections 的 bug。code review 才抓出來。

**How to apply:**
1. 寫 mock 前，先重讀 system prompt、API schema、函式 docstring 的「契約」section
2. 問自己：這個 mock 形狀在生產環境出現的條件是什麼？**一定會出現嗎？**
3. 至少寫一個 test 完全照典型生產形狀（別為了覆蓋率硬擺特殊 case）
4. 若真的需要測邊界條件（e.g. 雙入），要在 test name / docstring 裡明示這是邊界、不是典型
