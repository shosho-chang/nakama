---
name: Agent 對外訊息用 LLM compose with persona，別用 Python template
description: Slack / Email / 貼文等 agent 發的訊息，pipe 原始訊號 + persona prompt 給 LLM 產自然文字；Python string template 會長成公文 + 沒法處理 edge case（如假陽性）
type: feedback
originSessionId: 08f9ecf1-0d35-4311-a34c-34bca66b0731
---
Zoro scout 第一則貼文用 Python `format_publish_message()` 字串拼接，輸出像公文（「題目：X / 訊號：... / 為什麼值得討論：...」），修修回報「不像一個對話」。改 Slice C2（PR #108）把 compose 交 LLM 帶 Zoro persona → 輸出自然口語 + 額外 bonus：LLM 看 signal 裡 related keywords 是 `kate hudson / ray romano` 會自己寫「這是 Netflix 影集不是跑步，應該是假陽性」，**template 做不到這種 self-aware 坦白**。

**Why**：
- Template 只能做 data substitution，無法判斷「這筆 signal 看起來怪」
- Agent persona 的聲音靠 few-shot 和 prompt 規則塑造，template 無法承接
- 成本極低：Sonnet compose ~$0.002/tick，1 call/天 = $0.06/月，完全可忽略

**How to apply**：
1. Agent 發外部訊息（Slack brainstorm、Sanji 社群回覆、Nami 晨報、Brook 草稿、Franky alert）都走 LLM compose，**不要在 Python 裡做字串 format with f-string 或 join**
2. Compose prompt 獨立一份（例 `prompts/zoro/compose_message.md`），跟 persona prompt 和 judge prompt **分開**（職責不同）
3. Compose prompt 要明確：
   - Persona 再敘述一次（LLM 不是永遠有 context）
   - **禁 markdown**（Slack CJK 問題，見 `feedback_slack_cjk_mrkdwn`）
   - 列清楚幾個 few-shot 包含**正常案例 + 假陽性/異常案例**，讓 LLM 知道遇到怪 data 怎麼坦白
   - 邀請 / CTA 的語氣明確（「@Sanji @Robin 一人一段？」vs「願意各給一段觀點嗎？」— 後者太客套不 Zoro）
4. Fallback template 仍要有 — LLM 炸了時走 template 保底，但也要無 markdown（不是 `*bold*`）
5. LLM 忘了包含強制元素（mention、CTA）→ 程式層補救，但 prompt 優先

## 驗證

Merge 前 dry-run（`record=False, publish=False`）用真 signal data 打一次 LLM，**肉眼讀輸出**判斷語氣。一句話不對味就調 few-shot，不是調 prompt 規則。
