---
name: 給修修貼的指令跑在 PowerShell——禁 $()，.env 追加防黏行
description: 修修終端是 PowerShell：ssh 指令不可含 $()（PS 會先吃掉）；.env 追加前先保證檔尾換行，sed 會保留「無結尾換行」
type: feedback
---

兩個 2026-08-18 各翻車一次的操作教訓：

**1. 修修的終端是 PowerShell，貼給他的 ssh 指令不能含 `$(...)`。**
PowerShell 在雙引號字串裡會先解析 `$(date +%Y…)` / `$(grep …)`（`\$` 跳脫是 bash
語法，PS 不認），送到遠端前就炸。改寫原則：固定字串代替時間戳、用 `sed -n 's/^A=/B=/p'`
之類純 sed 代替 `$(grep|cut)` 命令替換。多行 heredoc（`<< 'EOF'`）在 PS 貼上沒問題。

**2. 對 `.env` 追加內容前，先確保檔尾有換行。**
GNU sed / printf 的輸出黏上「無結尾換行」的檔尾後，會把 `KEY=value` 黏成一行
（2026-08-18 事故：`NAMI_SDK_OAUTH_TOKEN=<tok>CLAUDE_CODE_OAUTH_TOKEN=<tok># 註解`
三段黏成 322 字元巨行，token 損壞、Nami 二度停擺；且 sed 對「最後一行無換行」的輸入
**輸出也不補換行**，黏行會連鎖）。修復/追加 `.env` 這類關鍵檔改用 Python 讀-改-寫
（`'\n'.join(lines) + '\n'`），或先 `sed -i -e '$a\' file` 補檔尾換行再追加。

**Why：** 這兩個坑的失敗模式都極難診斷（PS 的錯誤指向無關指令；黏行後遮罩式
grep 會把災難藏起來、認證錯誤訊息誤導向 token 本身）。

**How to apply：** 產生「給修修自己貼」的指令時 mentally lint：有沒有 `$(`？有沒有
對可能無結尾換行的檔案做 append？兩者任一命中就改寫。
