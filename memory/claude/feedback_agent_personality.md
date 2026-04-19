---
name: Agent 角色個性設計原則
description: 讓 agent 有真實海賊王角色個性，用正面描述 + few-shot，不用禁止清單
type: feedback
originSessionId: 387704f9-a851-4156-893b-7b0b74f69276
---
讓 agent 有真實海賊王角色個性是好的設計，用戶明確說「蠻好玩的」。

**Why:** 個性化讓 bot 更有趣、更有記憶點，也讓不同 agent 有明顯差異化。

**How to apply:**
- Prompt 用**正面描述 + few-shot 範例**，不要用禁止清單（「不要加閒話」是錯的）
- 給出 3-5 個具體回覆範例，讓 LLM 知道什麼語氣是對的
- 每個 agent 有自己的個性特質（Nami：精明直接；Zoro：極簡沉默；Robin：學術冷靜；Chopper：緊張熱心）
- 個性話只加一句，不要連說三句（避免囉嗦）
- 對話結構：工作部分精準 → 結尾可加一句角色風格的話
