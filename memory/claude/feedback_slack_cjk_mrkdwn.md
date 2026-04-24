---
name: Slack mrkdwn `*bold*` 對 CJK 字元 leak 成字面
description: Slack text 發 `*題目*` 這種 CJK + `*` 組合不會變粗體，星號當字面顯示；agent Slack 訊息別用 mrkdwn 粗體，走純文字或 blocks API
type: feedback
originSessionId: 08f9ecf1-0d35-4311-a34c-34bca66b0731
---
Slack `chat.postMessage` 送 `text` 帶 mrkdwn（預設 on），在**英文**周圍 `*word*` 會變粗體沒問題，但 **CJK 字元周圍 `*題目*` 不 render，星號直接漏出當字面**。修修實測 Zoro 第一則訊息 `*題目*：running point` 看到的就是 raw 星號。

**Why**：Slack mrkdwn parser 對 `*` 的邊界判斷依賴 whitespace / word boundary，對 CJK 的判定不對稱。這是 Slack 端 known 但未修的問題。

**How to apply**：
1. Agent 自動發 Slack 訊息（Zoro scout、Nami brief、Franky alert 等）**不要用 `*bold*`** — 不是「小心用」，是 CJK 語境下壓根無效
2. 要強調 heading → 改用 emoji 當錨（`🗡️` `💼` `🩺`），或用 blocks API + `section` + `mrkdwn: true` 明確指定
3. Bullet list 用 `•` 或 `-` 純字元，不要用 markdown list syntax
4. **最可靠的解**：訊息內容走**純口語 prose**，不靠格式 — 反正 Zoro / Sanji / 等 persona 都是「講話像人」不是「貼公文」

對應修法見 PR #108（Slice C2）：把 Python template 拼接換成 LLM compose with persona，prompt 明確寫「不要用 markdown」。

## 驗證

部署前 `python -c "from slack_sdk import WebClient; WebClient(token='xoxb-...').chat_postMessage(channel='C...', text='*測試*粗體')"` 在測試 channel 打一次，看星號有沒有 render — 沒 render 就不要寫進 agent。
