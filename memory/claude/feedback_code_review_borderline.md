---
name: Code review borderline 真 bug 仍要向使用者報告
description: 自動 code-review 閾值 80，<80 但你確信真 bug 也要向使用者 surface、讓他決定修不修
type: feedback
---

自動 code-review 協議過濾掉 score <80 的 issue，若你自己檢視後仍認為某個 <80 的 issue 是真 bug（非誤報、非 nitpick），應該在向使用者的文字回覆裡明確指出、讓他決定要不要修，而非只依協議沉默。

**Why:** PR #21 的 code review 抓到 `accept_suggestion` 分支沒寫 corrections 的真 bug（score 75），按協議不達 80 該跳過；但其實是 Pass 1 prompt 明示 Opus「不要硬改」uncertain 項目 → suggestion 永遠套不上去。我向修修 surface 後他選 A 修掉，事後驗證確實是真 bug。

**How to apply:**
1. 依協議先把 issue 分類為 0/25/50/75/100
2. 若某個 50-79 分的 issue 你自己再檢視後判斷 **這會在生產環境實際發生**，在給使用者的總結文字裡列出
3. 提供簡短修法 + 讓使用者在「修 vs 直接 merge」二選一
4. GitHub PR 留言仍照協議走（<80 就不留言）— 這只影響給使用者的報告
