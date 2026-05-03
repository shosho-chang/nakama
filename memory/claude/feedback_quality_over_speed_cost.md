---
name: 最高指導原則 — 品質為第一優先（適用全域：程式碼 / ingest / compose / 任何工作）
description: 修修最高指導原則：品質 > 速度 > 省錢；適用全域（程式碼、textbook ingest、article compose、所有 LLM 出品）；模型選擇 / 架構複雜度 / context 長度 / merge 完整度 等取捨都以品質為依據
type: feedback
created: 2026-05-01
updated: 2026-05-03
---

修修 2026-05-01 Line 1 grill Q7（Stage 1 用 Sonnet 4.6 vs Haiku）時 explicit 講出：「我不求速度也不求省錢，我求品質。品質為第一優先。」

修修 2026-05-03 ch3 textbook re-ingest C-min vs C-full 決策時再次強調：「永遠把最高品質當作最高指導原則，不管是程式碼的品質，或是我們文章的 ingest 或者 compose，完全都以最高品質為最高指導原則。」— **適用範圍全域**，不限於 compose channel。

**Why:** 修修做的是 health & wellness / longevity 內容創作，所有產出（程式碼、ingest 後的 KB 內容、compose 出的文章 / IG carousel / podcast 講稿）都是面向真人讀者或下游 agent 使用的品味產物。慢一點 / 貴一點都比品質瑕疵小。**ingest 品質特別關鍵** — 牽涉到下游 agent (Robin/Chopper) 答題的速度和精準度，每個漏掉的 verbatim quote = 一個漏掉的 authoritative citation anchor。**程式碼品質**也是 — 技術債利息會在每次 review / debug / 擴充時被付。

**How to apply:**

1. **模型選擇** — 涉及品味 / voice / judgment 工作（compose / extract / curate），用 Sonnet 4.6 或 Opus，不為了省錢降到 Haiku。Boilerplate / deterministic / 純翻譯轉換可 Haiku。
2. **架構複雜度** — 多 stage / 多 LLM call / cache / validation gate 提升品質就值得。Two-stage 比 single-shot 貴 1.3-1.5x 但品質明顯更好 → 選 two-stage。
3. **Context 切割** — 不要為了省 token 砍 context。1-hour podcast SRT 完整餵 Sonnet 200K 沒問題，不需要 chunking 砍碎。
4. **Speed 是次要** — 除非 latency 直接影響 UX（Slack 即時回應），否則慢幾秒不是問題；壞品質在 production 留疤是真的問題。
5. **Retry / validation 投資** — Stage 1 schema 不對 retry、output 驗證 gate、reflexion-style 自我審核 都是合理 cost。
6. **修修 review 時間 = 品質的反向指標** — LLM 出 70% 品質 + 修修改 20 min vs LLM 出 90% 品質 + 修修改 5 min；後者勝，因為 90% 那條 LLM cost 多 0.5x、修修時間少 15 min（CEO 時間最稀缺）— 品質優先呼應 [feedback_minimize_manual_friction.md](feedback_minimize_manual_friction.md) 最高原則。
7. **Merge / fix 完整度** — 比對兩版產出時，不要因為「diminishing returns」「合併成本」就選 partial merge。如果 full merge 拿到的是 authoritative citation anchor / RAG hit precision / 程式碼穩固性，cost 都值得（ch3 C-min vs C-full 教訓）。Bar 訂在 `≥X` 的 soft target 不該被當作「達標就停」的藉口，跟 mermaid `≥3` 同邏輯。
8. **Ingest 品質檢查清單** — vision describe 用 structured `**bold**` 標記 / verbatim quote 越多越好（每條 = 下游引用 anchor）/ wikilink density 高 = RAG 命中率高 / mermaid concept map 在 high-leverage section 必擺 / cross-ref alias 用實際章節 title 不能 hallucinate。
9. **程式碼品質** — refactor / typing / test coverage / error handling / 命名清晰 都不該為「能跑就好」打折；每一處草率 = 未來 review/debug/擴充的利息。

**與既有 memo 的張力與和解：**

- [feedback_cost_management.md](feedback_cost_management.md)：Sonnet 200k 日常主模型、Opus 按需 — 「按需」的判斷依據是品質，不是「能不能省錢」。Opus 該上就上，省錢不是選擇 Sonnet 的理由。
- [feedback_avoid_one_shot_summit.md](feedback_avoid_one_shot_summit.md)：incremental scope 不衝突 — incremental 是「先 ship 小範圍」，每個小範圍內品質仍不妥協。
- [feedback_run_dont_ask.md](feedback_run_dont_ask.md)：CEO 時間 vs LLM 時間 — 修修時間貴於 LLM cost，品質首要釋放修修時間。

**例外清單（什麼時候才用便宜模型）：**

- 純 deterministic 任務（schema validation / format conversion / 純規則轉換）
- 大量 batch 任務（同樣 prompt 跑 100x，先 Sonnet sample 5x 驗品質再決定要不要降 Haiku）
- 內部 pipeline 中間步驟（不直接 user-facing 且後續有 quality gate）
