---
name: 本機 shell ops auto mode 下直接做
description: pip install / python -m / 本機 CLI 跑 — 在修修本機 dev 環境且 auto mode 開著時，直接執行不要丟給修修
type: feedback
---

修修明確指出（2026-05-07 ADR-022 rebuild 收尾）：FlagEmbedding 沒裝、vault path 沒對、dim assertion 卡住，我每次都是「告訴修修跑」而不是自己跑。修修反問：「這種工作你不能幫我做嗎？」

**Why**：
- Auto mode 授權「主動推進、minimize interruptions」
- 這些都是本機 reversible / 標準 dev shell ops（pip install / python -m / git status 等）
- 修修在打字「請你跑」/ 我在打字「你跑」是雙倍沒必要的延遲
- 我只在「會擾動 shared infra（main merge / push to others' branch / 上傳到第三方服務）」才該停下確認

**How to apply**：
1. **本機 reversible op → 直接跑**：pip install / python -m / 本地 CLI / 跑 tests / sqlite query / 看 log 等
2. **包含環境變數的 CLI** → 我預先 env 給好（不要叫修修改 .env），或用 `$env:VAR=...; cmd` 寫一行
3. **長時間 op（model download GB / 重 build）→ run_in_background + 通知**：不要打斷修修等
4. **真的需要修修出手才停**：
   - GUI 互動（Reader UI 視覺驗收 / OAuth login 開瀏覽器）
   - 跨機器（VPS deploy / Mac 端跑）
   - 寫 production secret / billing 動作 / 對外發訊
   - 修改 main / push to others' branch / merge to default branch
5. **失敗 / dep missing → fix code 跟 install dep 一起來**：不只報「dep 缺」，連同 requirements.txt 補 + 安裝指令直接執行
