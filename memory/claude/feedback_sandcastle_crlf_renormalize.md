---
name: Sandcastle 產出 PR 前必 renormalize CRLF
description: E:\nakama-sandcastle clone 的 core.autocrlf=true + repo 無 .gitattributes → Sandcastle 編輯過的檔案被重存成 CRLF，PR 變整檔 false diff；出 PR 前必先 LF 化
type: feedback
---

Sandcastle 跑完一個 issue、要把 branch 推成 PR 前，**先檢查 diff 是不是整檔重寫**。若是，幾乎一定是 CRLF 污染，不是真的改動。

**Why**：

`E:\nakama-sandcastle`（Sandcastle 的專屬 clone，見 [[reference_sandcastle]]）的 `core.autocrlf=true`，而 nakama repo **沒有 `.gitattributes`** 強制 LF。後果：Sandcastle 一旦碰過某檔（即使只改一行），存檔時整檔行尾被轉成 CRLF，git diff 就把它當成「每一行都改了」的 whole-file rewrite。

實際踩到（2026-06-08，ADR-044 #858）：books.py 的 PR diff 顯示 1290 行整檔重寫，真改動其實只有 8 個 endpoint 加 `Depends(...)`。是我 merge-gate review 時抓到的 —— 沒有 rubber-stamp Sandcastle 輸出（這點本身要守，見 [[feedback_sandcastle_default]]）。

**How to apply**：

出 PR 前，在 Sandcastle clone 裡：

```bash
cd E:/nakama-sandcastle
git config core.autocrlf false      # 停止繼續轉
git reset origin/main               # 退掉污染的 staged 版本（保留工作區改動）
# 對每個真正編輯過的檔案 LF 化：
sed -i 's/\r$//' <edited-file> ...
git add <explicit-paths>            # 不要 git add .
git commit ...                      # 重新提交，diff 變回乾淨的 +N/-M
```

驗收：`git diff origin/main --stat` 行數應對得上真改動規模，不是整檔。

根治選項（未做）：給 repo 加 `.gitattributes`（`* text=auto eol=lf` + 對 .py/.js/.html/.css 明確 `text eol=lf`）。在那之前，每次 Sandcastle 產出都要手動 renormalize。
