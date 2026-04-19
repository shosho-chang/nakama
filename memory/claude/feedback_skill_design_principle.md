---
name: Skill 設計原則 — 互動式 workflow vs 確定性函式
description: 新功能先評估層級：互動式 workflow → skill、確定性函式 → shared/*.py、agent 只做觸發與編排；skill 粒度扁平且通常 agent-specific
type: feedback
created: 2026-04-18
---
開發 Nakama 新功能時，先評估它屬於哪一層，選錯層級會造成 token 浪費或過度複雜。

**三層分工：**

1. **Skill（互動式 workflow，LLM 驅動）**
   - 特徵：多步驟、需人類檢查點、有不確定性、依情境決策
   - 例：kb-ingest（摘要→提取→寫頁）、article-compose（大綱→逐段→匯出）、transcribe（切片→校正→仲裁）
   - 粒度：一個 skill = 一個聚焦 workflow，**不要把多個 task 塞進一個 skill**（反例：不要做「Nami 主 skill 包含 morning-brief + collab-reply 兩個 task」→ 改成兩個獨立 skill）
   - Owner：通常 agent-specific，跨 agent 共用是例外（目前只有 kb-search 規劃為共用）

2. **shared/*.py（確定性函式）**
   - 特徵：輸入 → 輸出明確、無互動、可被 skill 和 agent 共同 import
   - 例：translator、web_scraper、pdf_parser、chunker、audio_clip、gemini_client
   - 真正的跨組件共用靠這層，不靠 skill

3. **Agent（排程 / 事件消費 / 編排）**
   - 特徵：skill 做不到的事 — cron 觸發、Event Bus 消費、跨 skill 編排、Web UI、審核閘門
   - Agent 不重寫 skill 的邏輯，只調用 skill；依情境擇一載入對應 skill 的 SKILL.md（這才是省 token 的機制）

**Why：** 修修 2026-04-18 確認後同意採用。核心洞察：「skill 給 LLM 用有不確定性，確定性函式應放 Python module」。同時釐清 skill 粒度應扁平（多個小 skill 按需載入）而非巢狀（一個 skill 包多 task）。

**How to apply（新功能評估順序）：**
1. 先跑 prior-art-research（是否有現成 skill / shared module / MCP 可用）
2. 若要自建，判斷是否互動式 workflow：是 → skill；否 → shared/*.py
3. 若需排程 / 事件 / Web UI / 審核：agent 層只寫觸發與編排，核心邏輯下推到 skill 或 shared
4. 若功能自然跨 agent（如 KB 檢索），設計成共用 skill；否則預設 agent-specific

**開源友善提醒：** 每個 skill 都可能被單獨抽出開源，設計時避免硬編碼個人路徑/假設（延伸 `feedback_open_source_ready.md`）。
