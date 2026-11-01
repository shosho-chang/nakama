---
name: 動產線之前先掃所有 worktree 有沒有未推送的 commit
description: 修好的東西可能躺在別的 worktree 沒推；從主 checkout 跑產線＝用缺料的程式碼，會把同一個 bug 重新發現一次
metadata:
  type: feedback
---

**碰任何產線（render、物化、出圖）之前，先掃一遍所有 sibling worktree 有沒有未推送的
commit 或未 commit 的改動。**

```bash
for d in /e/nakama-*; do
  N=$(git -C "$d" log --oneline origin/main..HEAD 2>/dev/null | wc -l)
  M=$(git -C "$d" status --short 2>/dev/null | wc -l)
  [ "$N" -gt 0 ] || [ "$M" -gt 0 ] && echo "$(basename $d): $N 個未推 commit, $M 個未 commit 改動"
done
```

**Why:** 2026-09-17 整晚，修修一再抓到成品有問題——轉場卡字型不一致、Hero 大字卡又出現、
`intentional_aroll` 與 `semantic_kind` 的矛盾沒被擋。我每次都從主 checkout（`E:\nakama`，
main）跑產線，然後在 main 上「重新發現」問題、重新修一遍。

最後是**修修自己想到的**：「是不是你現在沒有用到我在另外一個視窗修正的 code？」

查出來：`E:\nakama-transition-title-standard`（branch `fix/transition-title-standard`）
有 **21 個 9/08–9/09 的 commit 從來沒推上去**，34 個檔案、+2241 行，而且落後 main 31 個
commit、躺了九天。裡面正是那些症狀的修正：

- `2b8e0c13` 滿版轉場卡加第三階字級，13 字以上不再斷成孤字
- `2c05dab2` pipeline 那份轉場卡也要分階字級——同一個 bug 有兩份拷貝
- `e6f673d0` / `d7bc8369` / `3eddffc8` Hero 文字標準與落點
- `ffec8af6` intentional_aroll 與 semantic_kind 矛盾要在 Director 這關擋
- `dfcf3344` 補回品牌 badge

對照證據：`video/compositions/transition_title/compositions/transition_title_wide.html`
在 main 上只有單一 `font-size: 104px`（註解還自陳「兩邊各寫一次就會漂掉」），分支上是
`168 / 128 / 104` 三階且收斂成單一真相來源。

**How to apply:**

- 掃到未推送的東西 → **先問修修要不要先落地**，不要逕自從 main 出片。
- 產線出來的東西跟預期不符時，這是**第一個**要排除的假設，不是最後一個。用「我是不是在
  用舊程式碼」開頭，比在 main 上重新 debug 便宜一個數量級。
- 同理適用於「這個功能不是已經拿掉了嗎」這類問題——[[feedback_retirement_must_leave_the_engine]]
  講的是退役只改手冊；這一條講的是修正根本沒進 main。兩者症狀一樣：**規則在別處，機器照舊**。
