---
name: Mock 測試資料要反映生產契約
description: 寫 integration test 的 mock 時，輸入形狀要對齊真實 API／prompt 契約，不能只求 test pass
---

為 integration test 設計 mock 回傳時，要先確認生產環境下該回傳的**實際形狀**（看 prompt、看 API schema、看 upstream 契約），再照那個形狀做 mock。不能為了讓測試好寫就 mock 任意組合。

**Why:**
- **PR #21**：`test_correct_with_llm_applies_verdicts` 我 mock 了 Opus 回傳 line 1 在 `corrections` AND `uncertain` 都有（雙入）。但 Pass 1 的 prompt 明白寫「不確定的修正必須放入 uncertain 清單，不要硬改」— 實際 Opus 只會把 uncertain 放 uncertain dict、不會進 corrections。我的 mock 是不可能出現的情況，結果測試全綠但漏掉 `accept_suggestion` 分支沒寫 corrections 的 bug。
- **PR #22**：`test_auphonic.py` mock payload 用舊欄位（ending + output_basename），不對齊 `download_url` 新契約 — live run 才爆。
- **PR #23（重犯）**：新 regression test 用 `"这是第一句这是第二句"`（9 中文字）配 `[[0, 2000], [2500, 5000]]`（2 timestamp）。FunASR char-level 真實輸出應該是 ~9 個 timestamp。我的 mock 是不可能形狀，剛好觸發 fallback path 而通過。code review 又抓到。**這是同一個 lesson 第三次出現**。

**How to apply:**
1. 寫 mock 前，先重讀 system prompt、API schema、函式 docstring 的「契約」section
2. 問自己：這個 mock 形狀在生產環境出現的條件是什麼？**一定會出現嗎？**
3. 對 ASR/STT 類 API：char-level、sentence_info、segment-level 是不同 path，mock 要選**最常見的真實 path**（例如 FunASR 通常輸出 `sentence_info`，那 mock 就用這個）
4. 至少寫一個 test 完全照典型生產形狀（別為了覆蓋率硬擺特殊 case）
5. 若真的需要測邊界條件（e.g. 雙入），要在 test name / docstring 裡明示這是邊界、不是典型
6. **寫完 mock 自查清單**：(a) 真實 API 會這樣回嗎？(b) 我有沒有為了 `_process_xxx` 內部 fallback 路徑而擺任意資料？— 若答 yes，重寫
