# Step 5 — Zoro Agent-Initiated Brainstorm (Q3 P2)

> Status: DRAFT，等修修決定下方「Open Questions」後進入實作。
> Related: [ADR-004 Slack Gateway](ADR-004-slack-gateway.md)、PR #52（P1 user-initiated brainstorm）、[memory/claude/project_multi_model_architecture.md](../../memory/claude/project_multi_model_architecture.md) Q3 區段

---

## 1. 目標

讓 Zoro 每天**自動**偵測 1–2 個值得內部討論的話題，推到 Slack 頻道，觸發 brainstorm thread。Sanji / Robin / Brook 其中 2 人加入給觀點，Nami 收斂。

**要跟 P1 的差別**：
| 差異 | P1（已完成）| P2（本 step）|
|---|---|---|
| 觸發方 | 用戶 `@nakama brainstorm <主題>` | Zoro 定時輪詢資料源後自動推 |
| 主題來源 | 用戶直接給 | Zoro 從 Trends / Reddit / YouTube 找熱點 |
| 推題身份 | `@nakama` bot（一次性 respond）| `@Zoro` bot（獨立 Slack app，主動 post）|
| 頻率 | 用戶想起就跑 | 每天 1–2 次，白天時段 |
| Gating | 無 | 四道濾網（velocity / relevance / novelty / cooldown）|

P3 是「夜間 async + budget cap + Nami 晨報整合」，本 step 不做。

## 2. 前置條件

| # | 條件 | 狀態 |
|---|---|---|
| 1 | P1 orchestrator 穩定 | ✅ PR #52 merged |
| 2 | Zoro 有獨立 Slack bot（能以 `@Zoro` 身份 `chat_postMessage`）| ⬜ 要走 `docs/runbooks/add-agent-slack-bot.md` Phase 5B |
| 3 | Zoro data sources 可用 | ✅ keyword_research 已整合 trends / reddit / twitter / youtube / autocomplete |
| 4 | KB 查詢可判斷 novelty | 需決定 source（見 §4.3）|
| 5 | cost 觀測有 panel 看得到 brainstorm 花費 | ✅ `/bridge/cost` 已上線 |

> **Zoro Slack bot 是 blocker** — 無法先 ship P2 再補。原因：主動推訊息時 Slack 只認 bot ID，不能靠 keyword routing「偽裝」成 Zoro。

## 3. 高階架構

```
                 ┌────────────────────────────────────────────────┐
                 │ cron / APScheduler（白天 09:00, 14:00 輪詢）      │
                 └──────────────────────┬─────────────────────────┘
                                        │
                                        ▼
                 ┌──────────────────────────────────────────────┐
                 │ agents/zoro/brainstorm_scout.py              │
                 │  1. gather_signals()                         │
                 │     → Trends rising / Reddit hot / YT trend  │
                 │  2. four_gates_filter()                      │
                 │     a) velocity 濾網                          │
                 │     b) relevance 濾網（四大領域）              │
                 │     c) novelty 濾網（KB 14 天內未處理）         │
                 │     d) cooldown 濾網（48h 同題不重推）          │
                 │  3. pick_best_topic() → 最多 1 個主題          │
                 │  4. publish_to_slack(topic, bot="zoro")       │
                 └──────────────────────┬─────────────────────────┘
                                        │ post to #brainstorm
                                        ▼
                 ┌──────────────────────────────────────────────┐
                 │ Zoro bot: 「@Sanji @Robin 這題值得討論嗎？」      │
                 │ 附 rationale（signals、KB 現況）                │
                 └──────────────────────┬─────────────────────────┘
                                        │ 該訊息觸發 orchestrator
                                        ▼
                 ┌──────────────────────────────────────────────┐
                 │ gateway/orchestrator.run_brainstorm(topic)    │
                 │ （既有 P1 code，零改動）                       │
                 └──────────────────────────────────────────────┘
```

**關鍵洞察**：P2 **不**改 `gateway/orchestrator.py`。只新增 Zoro 端的 scout 邏輯 + 頻道 post。把「產生 brainstorm topic」跟「跑 brainstorm」解耦。

## 4. 四道濾網 — 實作設計

### 4.1 Velocity（熱度速度）

**目的**：濾掉「冷門 / 下滑」的話題，只討論正在升溫的。

**實作**：
- Google Trends rising terms（已有 trendspy）— 取 `interest_over_time` 最近 24h slope > 閾值
- Reddit hot subreddit posts — 24h 內 upvote velocity > 閾值
- YouTube — 相關 channel 最近影片的 view count / day 加速度

**閾值起手保守**：Trends slope > +50%（過去 7 天對前 7 天）、Reddit upvote/hr > 20、YT view/hr > 1000。這些靠**真跑幾輪再 tune**，doc 不硬寫。

**輸出**：每個 signal 打 0–100 velocity score。

### 4.2 Relevance（領域命中）

**目的**：只討論四大面向（睡眠 / 飲食 / 運動 / 情緒）+ 五大學科的相關主題。

**實作選項**：
- **A. Hardcode keyword list**（類似 P1 orchestrator `_PARTICIPANT_PROFILES`）
  - 優點：零成本、deterministic、好 debug
  - 缺點：漏掉新詞（例：GLP-1、continuous glucose monitor、autophagy）
- **B. LLM 分類**（送 Claude Haiku / Grok fast 一句話判斷）
  - 優點：對新詞有 generalize 能力
  - 缺點：每輪多 4–6 次 LLM call，錢 + 延遲
- **C. Embedding 相似度**（跟四大領域 seed 詞做 cosine）
  - 優點：比 keyword 寬鬆、比 LLM 便宜
  - 缺點：要建 embedding index，第一次建有工

**建議**：**A + B 混合** — keyword 快速過濾（濾掉 80% 明顯無關的），剩下的用 Grok fast tier 過一次（每天總共 ~6 次 LLM call，< $0.01）。Claude 記憶 `user_domain_focus.md` 已有四大領域定義，直接 load 當 LLM 判準的 system。

**輸出**：relevance score 0–1。門檻 0.7 pass。

### 4.3 Novelty（KB 未處理）

**目的**：KB 最近 14 天內已經處理過同題，跳過。

**source 選項**：
- **A. `KB/log.md` append-only**（已有）— grep 最近 14 天有沒有相關 Wiki 頁面建立
- **B. `agent_memory` DB**（Phase 4 已上線）— 存 Zoro 推過的 topic + 日期
- **C. KB/Wiki/*.md 檔案 mtime + filename**

**建議**：**B**（agent_memory）。原因：
- 「Zoro 推過什麼題」是 Zoro 自己的記憶，存 agent_memory 最合語意
- 不用 grep 檔案，query 快
- 跟 cooldown 濾網共用同一個存儲

**schema**：`agent_memory.zoro.pushed_topics = [{"topic": ..., "normalized_keywords": [...], "pushed_at": ISO}]`

**判斷邏輯**：normalized keyword set 與新 topic 的 Jaccard similarity > 0.6 算同題。

### 4.4 Cooldown（同題 48h 冷卻）

**目的**：避免同一天推兩次類似題。

**實作**：直接用上面 agent_memory 的 `pushed_topics` 表。新 topic 若與 48h 內推過的任一題 Jaccard > 0.3 就 skip。

門檻比 novelty 嚴（0.3 vs 0.6），因為 cooldown 是近期冷卻、novelty 是 KB 覆蓋。

## 5. 排程與頻道

**觸發**：APScheduler cron
- 09:00 台北時間 — 早上熱點輪詢（Zoro 睜眼那刻）
- 14:00 台北時間 — 下午補推（若早上沒出題）

**22:00–09:00 不推**（夜間留給 P3 async 模式）。

**頻道**：
- 選項 A：`#general` — 跟日常訊息混在一起，增加曝光但吵
- 選項 B：新建 `#brainstorm` — 專用頻道，訊號乾淨但要切頻道看
- 選項 C：`#nakama` 如果有的話，agent 自己的對話頻道

**建議 B**（新建 `#brainstorm`），後續 Phase 3 的 Nami 晨報會把該頻道 24h 內容摘到晨報，修修就算沒進頻道也不漏資訊。

## 6. 推題訊息格式（Zoro bot 發出）

```
🗡️ 有個話題我覺得值得討論一下。

**題目**：<topic>

**為什麼現在值得討論**：
• Trends +80%（過去 7d）
• Reddit r/longevity 24h 熱 posts 3 篇
• KB 14d 內沒處理過

**相關 KB 頁面**：<linked if any>

@Sanji @Robin 願意各給一段觀點嗎？我會 tag @Nami 最後整合。
```

訊息結尾的 `@Sanji @Robin` 就是真 Slack mention（多 bot 架構能做）。該 thread 的 reply 會由 SanjiHandler / RobinHandler 各自接，Nami 最後收尾。

## 7. 成功指標 / DoD

**P2 上線 2 週內達成才算 pass**：
- 每天平均 0.8–1.5 個 brainstorm 推出（不是「每天 1.0」硬指標，容錯）
- 推出的主題**至少 70% 修修不會覺得「這不該推」**（人工評）
- 平均單 brainstorm 花費 < $0.30（參與者 2 人 + synthesizer）
- 每週至少 1 個 brainstorm 結論落地（KB 新頁面 / Brook 起草 / 任務）

不達標 → 回頭 tune 濾網參數，別擴 P3。

## 8. Open Questions（修修請回覆）

1. **頻道**：`#brainstorm` 新建，還是丟 `#general`？（§5 建議 B）
2. **觸發時間**：09:00 + 14:00 台北可以嗎？還是要不同時段（例如只 14:00 下午一次）？
3. **Participant selection 用 P1 的 keyword routing**（`_PARTICIPANT_PROFILES`）就好，還是 P2 要升級到「Zoro 自己用 LLM 決定邀誰」？（後者貴、更靈活）
4. **Relevance 濾網**：接受 A+B 混合方案（keyword pre-filter → Grok 判準）？還是只用 A 省錢？
5. **排程用 APScheduler**（輕量、in-process），還是接 Linux cron + 獨立 python script？前者簡單、後者好 debug 且 VPS 重啟不影響 scheduling。
6. **Novelty 儲存**：agent_memory 路線 OK？還是你想存 `data/zoro_pushed_topics.jsonl` 這類獨立檔案？
7. **Zoro 自己的 persona prompt** 目前只有骨架（`agents/zoro/__main__.py` raise NotImplementedError）。推題前要寫一份 `agents/zoro/prompts/scout.md`（系統 prompt），用來（1）判斷 relevance 時當 LLM judge、（2）組推題訊息的 rationale。等步驟 5 開工時一起寫？

## 9. 實作拆解（Open Questions 定案後）

按 P9 六要素展開成 task prompts：

| Task | 範圍 | 輸入 | 輸出 |
|---|---|---|---|
| T5.1 | Zoro Slack bot 上線 | runbook Phase 1 token | `gateway/bot.py` multi-bot 架構（Sanji 已完成後延伸）|
| T5.2 | `agents/zoro/brainstorm_scout.py` + 四濾網 | Q1–Q6 答案 | scout module + 單元測試 |
| T5.3 | `agents/zoro/prompts/scout.md` + relevance LLM judge | 四大領域定義 | prompt 檔 + LLM 呼叫 helper |
| T5.4 | 排程器（APScheduler or cron） | Q5 答案 | scheduler 模組 + systemd 或 in-process job |
| T5.5 | Novelty/cooldown 存儲 | agent_memory schema | `zoro.pushed_topics` CRUD helper |
| T5.6 | E2E 測試：mock data source → 預期推哪個 topic | 所有前 5 項 | integration test + 真跑 1 次手動驗 |

## 10. Rollback plan

- scheduler 關掉 cron / 停 systemd timer，其他 agent 不受影響（Zoro scout 是獨立模組）
- 若 Zoro bot 推了有問題訊息 — 單訊息 Slack delete，不影響系統 state
- 濾網 tune 偏了 → 改 config，不動 code
