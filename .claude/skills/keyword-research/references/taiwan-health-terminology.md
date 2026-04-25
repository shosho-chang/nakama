# Taiwan Health-Domain Terminology Notes

Disambiguation tips for common zh-TW health topics when clarifying with the
user (Step 2 of the skill workflow). This file is NOT a bilingual glossary —
it's a quick reference for when topic ambiguity would meaningfully change
the research output.

## Topic Families That Usually Need Narrowing

### 飲食 / 斷食 / 生酮 / 低碳
- 間歇性斷食 → which protocol? 16:8 / 5:2 / OMAD / ADF / FMD
- 生酮飲食 → standard keto / cyclical keto / targeted keto
- 低碳 → low-carb (100-150g) vs. keto (<50g)
- 原型食物 vs. whole food vs. clean eating — confirm the user's framing

### 睡眠
- 睡眠 (general) → sleep quality, insomnia, sleep hygiene
- 失眠 → acute insomnia / chronic insomnia / sleep-onset vs. sleep-maintenance
- 睡眠呼吸中止症 (OSA) — clinical topic, different search intent
- 時差 / 輪班 → circadian disruption, not general sleep
- **深度睡眠** / Deep sleep — 學術名 N3 NREM 睡眠 / slow-wave sleep (SWS) / delta sleep。
  台灣常見譯名：深度睡眠、深層睡眠（中國大陸常見：深度睡眠、深睡眠）。
  Disambiguation：
    - 科普角度（什麼是深睡、為什麼重要） vs.
    - 實作角度（如何提升深睡比例：運動／溫度／褪黑激素／鎂） vs.
    - 病理角度（深睡缺乏與失智 / 代謝風險 / Alzheimer's glymphatic clearance）
  相鄰術語：REM 快速動眼期、入睡潛時 sleep onset latency、睡眠效率 sleep efficiency、睡眠週期 sleep cycle。

### 運動 / 訓練
- 重訓 → strength training (general) / hypertrophy / powerlifting
- HIIT → different from Zone 2 / LISS
- HRV 訓練 — specific to HRV biofeedback, not cardio in general
- VO2max 訓練 — specific protocol

### 補充劑 (supplements)
- 鎂 → magnesium (sleep) vs. magnesium (muscle) vs. magnesium types
  (glycinate / citrate / L-threonate)
- Omega-3 → EPA/DHA ratio, plant vs. fish source
- 益生菌 → strain-specific claims (L. rhamnosus / L. reuteri / B. longum)
- 肌酸 (creatine) — usually unambiguous

### 情緒 / 壓力
- 壓力 (stress) vs. 焦慮 (anxiety) vs. 憂鬱 (depression)
- 冥想 → specific practice (vipassana / TM / loving-kindness) vs. general
- 冷暴露 (cold exposure) / 冰浴 → Wim Hof vs. Søberg protocol

## Term Variations zh-TW vs. zh-CN

Google Trends under `language="zh"` may surface Simplified terms. When Claude
synthesizes `keywords`, it normalizes to Traditional, but be aware the raw
data may include:

| zh-TW | zh-CN |
|-------|-------|
| 間歇性斷食 | 间歇性断食 |
| 營養 | 营养 |
| 運動 | 运动 |
| 補充劑 | 补充剂 / 膳食补充剂 |
| 飲食 | 饮食 |
| 代謝 | 代谢 |

If the user expresses confusion about Simplified-looking outputs, explain that
Claude normalizes to Traditional in the final frontmatter / markdown.

## Cross-Language Nuances

Some English terms don't have a direct Taiwan colloquial equivalent — the
pipeline auto-translate may produce technically-correct but awkward terms:

| English | Natural zh-TW | Auto-translate tendency |
|---------|--------------|-------------------------|
| "biohacking" | 生物駭客 / 自我優化 | 生物黑客 (zh-CN flavor) |
| "circadian rhythm" | 生理時鐘 / 晝夜節律 | 晝夜節律 (academic) |
| "cold plunge" | 冰浴 / 冷水浸泡 | 冷水跳水 (too literal) |
| "deload week" | 減量週 | 卸載週 (wrong domain) |

If auto-translate seems off, ask the user to provide `en_topic` explicitly.

## Confirming Scope in Step 2

Good clarifying questions:
- "是要寫給**一般大眾**還是**深度讀者**？" — changes complexity of core keywords
- "要聚焦**台灣本地**還是**華語通用**？" — affects trend_gaps framing
- "有**具體角度**嗎？（例如：原理 / 實作 / 踩坑 / 比較）"

Bad clarifying questions (skip these; the pipeline handles them):
- "要中文還是英文？" — pipeline always runs bilingual
- "要 YouTube 標題還是 blog？" — handled via `--content-type` (Step 3)
- "要幾個關鍵字？" — pipeline targets 8-12, not user-tunable
