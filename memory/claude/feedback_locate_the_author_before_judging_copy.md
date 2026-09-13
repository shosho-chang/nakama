---
name: feedback_locate_the_author_before_judging_copy
description: 批評 pipeline 產出的文案前，先查「這行字是哪一關寫的」，並查是不是自己下的指令造成的；對著渲染結果歸咎會把修正裝在錯的地方
metadata:
  type: feedback
---

Pipeline 產出的文案不好時，**第一個動作是定位作者關卡，不是評分**。查法：把
canonical 值與各關實際交出的值逐 run diff 出來，看哪一關動過它，再往上查**是什麼
指令叫它動的**。

**Why**：2026-09-08 review 20260901 蘇予昕長精華的滿版轉場卡。我對著 Resolve
timeline 上的六張卡評分，一路歸咎錯三層：

1. **以為是 Director 在挑金句。** 其實字是上游 `highlight-cut` miner 寫死的
   canonical，37 個 run 完全一致。
2. **改成「Director 擅自改寫 canonical」。** 34/37 個 run 確實改寫了，但——
3. **真正的原因是我自己的 brief。** `advance_with_claude.py` 的 director 指令裡有
   一條「`transition_title` 10 個中文字以內」，是我上一輪為了繞過排版 bug 加的。
   Director 一直在服從我的指令。而那個排版 bug 是 `.title.long` 只有兩階字級，
   13 字 ×128px 撞破 max-width 1600px 斷成孤字。**我用砍文案繞過排版問題，代價由
   主詞和結論付。**

另外兩個查證教訓：

- **逐字相符檢查早就存在**（`_policy.py`），我沒查就假設沒有。它裝在三個 stage 全部
  收完之後才驗，而且四種違規共用一句 "must map one-to-one"——擋下來的那一次沒人
  看得出擋的是文字，於是繞過 gate 直接鋪 timeline，錯字上了片。**裝得晚 + 訊息模糊
  = 等於沒有。**
- 修修指出我判卡片的判準也錯：我把「不是牽拖，是線索」判為合格，理由是它有
  「否定→肯定」的轉折結構。**轉折結構不等於有主張**——主詞不在，冷讀者不知道什麼
  不是牽拖。用「看起來像金句」當判準，正是生成端犯的那個錯。

**How to apply**：
- 抱怨某個欄位的內容前，先 grep 它的 canonical 來源與所有寫入點，**包含我自己寫給
  worker 的 stage brief**。worker 服從指令不是它的錯。
- 提議「加一個檢查」之前，先確認那個檢查是不是已經存在但**裝在太晚的位置**或
  **訊息太模糊**。
- **排版問題就修排版。** 內容遷就版面時，先問「版面為什麼載不動」。
- 判斷卡片／標題文案，第一個測試是**主詞在不在**，不是「讀起來夠不夠嗆」。
- 轉場卡文字標準寫在 `.claude/skills/highlight-cut/SKILL.md`，不重複抄進記憶。
  review 用 `scripts/review_transition_titles.py`，自動判定用
  `scripts/cold_read_transition_titles.py`（冷讀回收測試，見 [[feedback_generator_is_not_the_judge]]）。
