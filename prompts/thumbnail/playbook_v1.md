# Title × Thumbnail Playbook v1

> **Status**: scaffold awaiting full extraction + clustering output.
> **Owner**: 修修 (Health & Wellness / Longevity, zh-Hant).
> **Source corpus**: 140 high-CTR thumbnails from 4 creators (Ali Abdaal, Alex Hormozi, Cleo Abram, Jeff Su).
> **Methodology**: see [`docs/research/2026-05-26-thumbnail-playbook-design.md`](../../docs/research/2026-05-26-thumbnail-playbook-design.md).
> **Machine companion**: [`playbook_data_v1.json`](playbook_data_v1.json) — LLM-consumable archetype index.

---

## 0. How to use this playbook

### For 修修 (human reader)

Read §1 (theory anchors) once. Then use §2-§5 as a lookup table when planning a video:

1. Decide content type from the One Sentence (educational / explainer / contrarian / story / tool-demo / interview).
2. Match to a **Title Archetype** (§2) and **Thumbnail Archetype** (§3).
3. Check the **Joint Pairing** map (§4) — does this title+thumb combination have empirical support in the corpus?
4. Read the **修修 brand adaptation** note (§5) for each chosen archetype — what to soften / what to avoid.
5. Hand to the brainstorm LLM with the chosen archetype IDs as constraints.

### For LLM (brainstorm prompt consumer)

The LLM brainstorm flow loads `playbook_data_v1.json` (≈1K-token archetype index). For a given video brief:

1. Receive: `one_sentence`, `keywords`, optional `target_creator_style`.
2. Pick 3 candidate archetype IDs from the index.
3. For each, lazy-load that archetype's full §2/§3 section (≈500 tokens) and the matching §5 adaptation.
4. Produce 3 ideas, each tagged with its archetype IDs.

See §6 for the brainstorm-prompt integration spec.

---

## 1. Theory anchors (the frameworks every archetype is grounded in)

Click-driver attributions in this playbook are hypothesised by matching observed title/thumbnail structure to established cognitive frameworks. They are **hypotheses, not causal claims** — see §7 caveats MC-2 and the panel integration matrix at `docs/research/2026-05-27-playbook-3way-panel-integration.md`. Each archetype's mechanism is anchored in one or more of the frameworks below. If a future LLM extends this playbook, it must use these or add a new framework explicitly.

### 1.1 Information Gap Theory (Loewenstein 1994)

> Curiosity is a deprivation state triggered when one perceives a gap between what one knows and what one wants to know.

Applies to: question-format titles ("Why does X happen?"), numbered listicles (can't predict the N items), insider-knowledge frames ("the truth about Y").

**修修-relevant variant**: 中文受眾對 information-gap 反應跟英文受眾接近，但需要 specificity 配合（純抽象的 gap 不夠 — 「為什麼空腹有氧反而會降低脂肪燃燒？」比「為什麼運動沒效果？」強）。

### 1.2 MrBeast PVP — Promise / Visual / Payoff

Promise = what title implies you'll get. Visual = thumbnail conveys the promise. Payoff = video delivers. **Mismatch between Promise and Payoff = retention collapse.**

In this playbook PVP is the structural lens for §4 Joint Pairings: does the thumbnail visually carry the promise made in the title?

### 1.3 Cialdini's 6 Principles of Influence

Authority / Social-proof / Scarcity / Commitment-consistency / Reciprocity / Liking. Most YouTube click-drivers map to **Authority** (expert title cues, credentials) and **Social-proof** (subscriber counts, "everyone is talking about"). Scarcity rare in evergreen.

### 1.4 Identity-Based Hook (Berger, "Contagious")

People share/click content that reinforces their self-image. "How To Get Ahead of 99% of People" targets identity (ambitious / above-average). "5 High Income Skills for Students" targets identity (student / future high-earner).

**修修-relevant variant**: 健康/長壽受眾的 identity hooks 通常是「重視健康的科學派」「中年要起來保養」「不想老化得太快的高敏感族」。

**v1.1 collectivist supplement (Gemini panel §1d)**: Berger's framework was developed in individualistic North American context. In Taiwan / Hong Kong Confucian-influenced collectivist culture, status-driven hooks like "Get Ahead of 99% of People" land weaker — and a **family-and-social-responsibility identity hook** is often more powerful:

- **The Responsible Provider/Child**: 「如何健康地陪伴孩子成長到 30 歲」/ 「別成為家人的負擔：中年後必須做的 3 個健康準備」
- **The Prudent Planner**: 「為自己的老年生活做好準備」(self-reliance for harmony, not competition)
- **The In-Group Knowledge Sharer**: 「值得分享給父母 / 伴侶看的 X 件事」(identity = "I have valuable information for my loved ones", not "I'm smarter than everyone")

These hooks should be over-indexed in §2 Chinese adaptations vs the corpus's individualistic frames.

### 1.5 Loss Aversion (Kahneman-Tversky)

Losses loom larger than equivalent gains. "X mistakes you're making" and "you're WASTING Y" frames leverage this. **Use sparingly in health content** — fear-of-loss in medical context can erode trust.

### 1.6 Specificity Bias

Concrete > vague. "5g of creatine for 6 weeks" beats "some creatine for a while". Specificity signals: numbers, named figures, named timeframes, dollar amounts (English-only).

**修修-relevant variant**: 中文具體性語感 — `5 克 / 6 週 / 65 歲後` 比 `一些 / 一段時間 / 老年` 強。

### 1.7 Pattern Interrupt (visual)

Thumbnails compete in the YouTube feed. Most feed is red/orange/yellow saturation; a deep-teal Cleo thumbnail interrupts and gets clicked. Same logic: faces in feed; an object-hero no-face thumbnail interrupts.

### 1.8 Face Emotion Contagion (mirror neurons)

Viewer's brain mirrors the displayed facial expression. A surprised face → viewer feels surprise → click motivation. A serious face on a "scary" topic → viewer feels gravity → click for clarification.

Mapped to 7 emotion enum (see [`emotions.yml`](emotions.yml)).

### 1.9 Numerical Anchor (Tversky-Kahneman 1974)

A salient number primes subsequent expectations. "8 HACKS" in large overlay anchors "I'll get 8 specific things in 10 minutes" before viewer reads any other text.

### 1.10 Familiarity Scaffolding

Recognisable logos, faces, tools reduce cognitive load. Jeff Su's Google Sheets / Notion / ChatGPT logos = "I know this — clicking is safe". Cleo's celebrity-guest portraits = familiarity transfer.

**修修-relevant variant**: 對健康/長壽受眾，familiarity scaffolding = 「台大醫師 / 哈佛研究 / Attia / Huberman」這類熟悉權威指標。

### 1.11 Cognitive Ease

Cognitive ease drives positive impression. Short titles, large clean text, low-clutter thumbnails feel friendlier. Pairs with **Specificity** as a counterweight (specific is concrete but not necessarily easy).

### 1.12 Insider Knowledge Frame

"What X people know that you don't", "The truth about Y", "Most people don't realise Z". Cialdini-Liking adjacent — you feel let-in-on-secret = trust + status uplift.

---

## 2. Title Archetypes

> **v1.1 panel integration note** (from `docs/research/2026-05-27-playbook-3way-panel-integration.md` items I-4 + I-5): both Codex and Gemini flag that T-A9 (Year-Anchor) and T-A10 (Cost-Risk-Reframe) function as **modifier tags** rather than primary archetypes — they attach to other archetypes rather than standing alone. v1.1 retains them in §2 for traceability but treat them as modifiers in brainstorm calls (combine with T-A1/T-A2/T-A8 rather than using standalone). Codex also notes that T-A2, T-A3, T-A5, T-A8 likely conflate 2-3 distinct sub-patterns each — v2 will split. See MC-3 (expanded).


> **v1.1 panel-required rewrite flag** (Codex audit §4 + Gemini audit §1): All `中文化範例` strings in §2 / §3 / §4 below are **conceptual sketches**, not publishable titles. Gemini specifically called out catastrophic-translation cases (T-A2's 「在腦神經科學研究出現之前你可能已經損傷了它」 reads as machine translation; T-A4 violates own §5.4 length rule at 34 chars; T-A10 invents non-evidence metrics like 「睡眠恢復力」). Before deployment in brainstorm prompts, all 中文化範例 must be rewritten by 修修 or a native-zh-Hant copywriter familiar with Taiwan/HK YouTube conventions. v1.1 retains the English-translation versions for traceability; **do not load them into production prompts as-is**. See Gemini panel §6 item #1 spec for what a properly native T-A3 adaptation looks like — that block is reproduced in §A1 below.


### T-A1. Numbered Listicle Promise

- **One-line**: Title leads with a finite small number (3–11) of items, methods, habits, or tips.
- **Frameworks**: Loewenstein (§1.1) × Cialdini commitment-consistency (§1.3) × Numerical anchor (§1.9)
- **When to use**: 當內容可以清楚拆分成獨立步驟或習慣時，例如「5 個睡眠習慣」或「8 種抗老食物」；觀眾處於探索階段、想要一個完整可操作的清單。
- **When to avoid**: 深度單一議題（如某項研究的深度解析）不適合強行加上數字，會讓內容看起來過於碎片化，削弱論述的完整性。
- **Frequency in corpus**: 11 / 140 (7.9%)
- **Creator distribution**: Ali Abdaal: 6, Jeff Su: 5
- **Canonical example**: "5 Easy Ways to Become More Self-Disciplined" — Ali Abdaal
- **Mechanism**: The finite number pre-closes the Loewenstein information gap by implying the viewer will possess a complete set of answers after watching — triggering the 'almost there' commitment effect. Odd or non-round numbers (5, 8, 11) signal that the creator counted honestly rather than padded, boosting perceived specificity. The number also acts as a cognitive scaffold, reducing the mental cost of starting because the viewer knows exactly what they are signing up for.
- **Sample examples**:
  - ali_abdaal_001: "5 Easy Ways to Become More Self-Disciplined"
  - ali_abdaal_005: "8 Lazy Habits That Save Me 20+ Hours a Week"
  - ali_abdaal_006: "8 Simple Habits to Actually Improve Your Health"
  - jeff_su_001: "10 INCREDIBLE things Google Sheets can do Right Now!"
  - jeff_su_028: "Top 5 ChatGPT Use Cases for Professionals!"
- **修修 brand fit**: A
- **Why this grade**: 健康 listicle 格式與 修修 的實證導向內容高度契合，只需將語氣從「輕鬆」調整為「簡單可執行」即可直接使用。
- **中文化範例 (2-3 個)**:
  - 「5 個研究證實的習慣，讓你的生理年齡年輕 10 歲」
  - 「8 個上班族也能做到的微習慣，每週幫你恢復 20 小時精力」
  - 「6 種哈佛研究支持的抗老食物，你家冰箱裡可能已經有了」
- **改編要點**:
  - 數字後面必須接「研究證實」「科學支持」或「實測有效」等信度標記，而非 "Easy / Simple" 的輕鬆感
  - 避免使用「輕鬆」「無痛」；改用「簡單」「5 分鐘」「微習慣」
  - 加入可量化的結果（「年輕 10 歲」「恢復 20 小時精力」）以強化 specificity bias
- **避免**:
  - 數字超過 11 個，會讓清單感覺沒有篩選，降低信度
  - 「N 個神奇方法」「超強習慣」等誇大詞語與 修修 實證語氣不符
  - 清單項目彼此重疊或過於籠統，會讓「完整感」失效

---

### T-A2. How-To with Specificity Anchor

- **One-line**: Title opens with 'How to…' or an imperative verb and pairs it with a concrete specificity marker (number, tool name, time, or named outcome).
- **Frameworks**: Loewenstein (§1.1) × Specificity bias (§1.6) × Cialdini commitment-consistency (§1.3)
- **When to use**: 觀眾有明確問題需要解決方案時，例如想改善睡眠、控制血糖、增加肌肉量；內容有清楚的步驟流程或可衡量的結果。
- **When to avoid**: 觀點性或反直覺內容不適合，「如何做到 X」的框架會讓題目預設答案明確，消滅好奇心缺口；純敘事或訪談型內容也不適合強套此框架。
- **Frequency in corpus**: 33 / 140 (23.6%)
- **Creator distribution**: Ali Abdaal: 17, Alex Hormozi: 9, Jeff Su: 5, Cleo Abram: 2
- **Canonical example**: "How to Do More in 12 Weeks Than Most People Do in 12 Months" — Ali Abdaal
- **Mechanism**: The 'How to…' prefix instantly signals a solution frame, collapsing viewer anxiety about whether they will get value. When a specificity anchor (time duration, percentile, named tool) is added, it triggers Specificity bias — the brain interprets a precise promise as more credible than a vague one, because fabrication requires effort. The combination creates a two-step click: first the identity match ('this problem is mine'), then the specificity signal that the creator actually has a concrete answer.
- **Sample examples**:
  - ali_abdaal_014: "How to Do More in 12 Weeks Than Most People Do in 12 Months"
  - ali_abdaal_013: "How to Control Your Dopamine (Before It's Too Late)"
  - ali_abdaal_023: "How to Stop Wasting Your Life (Avoid These 5 Things)"
  - jeff_su_009: "How I Manage My Time - 8 Tips that Changed My Life"
  - ali_abdaal_024: "How to Study for Exams - An Evidence-Based Masterclass"
- **修修 brand fit**: A
- **Why this grade**: 這是語料庫中最高頻的架構，且與 修修 的實證權威語氣天然相容——只需確保 specificity anchor 是具體數字或命名成果，而非模糊的「更好」。
- **中文化範例 (2-3 個)**:
  - 「如何在 12 週內，把你的靜息心率降到運動員水準（科學實證步驟）」
  - 「如何控制多巴胺，在腦神經科學研究出現之前你可能已經損傷了它」
  - 「如何真正維持健康習慣——設計系統，而不是只靠意志力（Ali Abdaal 同款方法）」
- **改編要點**:
  - Specificity anchor 優先選擇時間（「12 週」「48 小時」）或可量化結果（「靜息心率降低」「體脂減少 3%」），而非百分比排名（「超越 99% 的人」）——後者在台灣健康語境容易顯得浮誇
  - 括號內的副標可以用來加入 修修 品牌信任標記，例如「（哈佛研究方法）」「（科學實證）」
  - 「How I」第一人稱版本（如 jeff_su_009）對 修修 而言語氣更自然，可替代命令式「How to」
- **避免**:
  - 「如何輕鬆做到 X」——與品牌的反輕鬆原則衝突
  - Specificity anchor 若是虛構或無法在影片中實現，會嚴重損害信度

---

### T-A3. Contrarian Reversal

- **One-line**: Title challenges conventional wisdom or uses a 'you've been doing it wrong' frame to create cognitive dissonance and compel resolution.
- **Frameworks**: Pattern interrupt (§1.7) × Loewenstein (§1.1) × Cialdini commitment-consistency (§1.3)
- **When to use**: 當內容有真正的反直覺發現支撐時，例如某個被廣泛相信的健康觀念被新研究推翻；搭配權威來源（命名研究或專家）可以強化可信度。
- **When to avoid**: 若「反轉」論點太薄弱或只是標題噱頭，內容無法撐住反轉承諾，會損害信任；已知風險話題也要避免，因觀眾已有意識時 loss framing 效力下降。
- **Frequency in corpus**: 22 / 140 (15.7%)
- **Creator distribution**: Jeff Su: 8, Cleo Abram: 8, Alex Hormozi: 4, Ali Abdaal: 2
- **Canonical example**: "95% of People STILL Prompt ChatGPT-5 Wrong (Do This Instead)" — Jeff Su
- **Mechanism**: Contrarian titles fire the Loewenstein gap by implying the viewer holds a false belief and will leave uninformed without watching. They simultaneously trigger loss aversion — the prospective cost of continuing a wrong behaviour activates more urgently than the gain of a new behaviour. The statistical social-proof variant ('95% of people…') adds a comparison anchor that makes the viewer suddenly uncertain whether they are in the failed majority, creating an identity-threat that demands resolution.
- **Sample examples**:
  - jeff_su_003: "95% of People STILL Prompt ChatGPT-5 Wrong (Do This Instead)"
  - ali_abdaal_033: "Stop Setting Goals. Do This Instead."
  - cleo_abram_002: "Dinosaurs Were Weirder Than We Thought"
  - alex_hormozi_027: "Beating 99% of People Is Actually Easier Than You Think"
  - jeff_su_022: "Notion was HARD until I Learned These 8 Tips!"
- **修修 brand fit**: B
- **Why this grade**: 反轉架構的點擊力強，但若不搭配實證來源，容易落入恐嚇或誇大，與 修修 的 no fear-mongering 原則衝突，必須大幅調整語氣。
- **中文化範例 (2-3 個)**:
  - 「你一直吃錯了蛋白質——哈佛研究發現 90% 的人時機不對」
  - 「養生其實沒你想的那麼難，直到你建立對的系統」
  - 「95% 的人補充益生菌都沒效？研究告訴你正確的服用時機」
- **改編要點**:
  - 反轉主張必須在標題中提示具體來源（「哈佛研究」「台大醫師」），否則只是空洞的聳動
  - 統計數字（「90% 的人」）在修修語境中應降低到合理比例，或明確說明數據出處
  - 「Do This Instead」對應的中文改為「正確方法在這裡」或「這樣做才有效」，語氣更直接而不命令
- **避免**:
  - 「你做錯了！」「你被騙了！」等強烈指責語氣——此類框架在台灣閱聽人中可能引發防禦反應
  - 反轉論點若不能在影片中用研究支撐，不應使用此架構
  - 與恐懼訴求（「你的身體正在死去」）混合使用

---

### T-A4. Story-Confession / Personal Blueprint

- **One-line**: First-person hypothetical or retrospective frame: 'If I were starting over…', 'What I wish I knew…', or 'I did X and here's what happened.'
- **Frameworks**: Loewenstein (§1.1) × Cialdini authority (§1.3) × Parasocial liking (§1.7)
- **When to use**: 當 修修 的親身實踐或研究歷程是主要信度來源時，例如自我實驗、長期追蹤數據、個人健康轉變；觀眾處於「我該如何開始？」的迷惘狀態最有效。
- **When to avoid**: 修修 尚未親身驗證的主題不適合使用第一人稱框架，否則聽起來像是假設性意見而非可信建議；純科學解析影片也不需要強套個人故事。
- **Frequency in corpus**: 18 / 140 (12.9%)
- **Creator distribution**: Alex Hormozi: 8, Ali Abdaal: 6, Cleo Abram: 3, Jeff Su: 1
- **Canonical example**: "If I Started Over in 2024, I'd Do This (Full Blueprint)" — Alex Hormozi
- **Mechanism**: The first-person hypothetical fires Loewenstein's 'information gap from a known expert' variant: the viewer knows the creator succeeded and now wants the exact mental model they would apply. The confession/regret sub-variant additionally activates loss aversion by implying that the viewer can skip the painful lesson the creator had to learn the hard way. Parasocial intimacy (we know this person) converts authority into trust faster than third-party citations would.
- **Sample examples**:
  - alex_hormozi_019: "If I Wanted to Become a Millionaire In 2024, This is What I'd Do"
  - ali_abdaal_007: "9 Things I Wish I Knew Before Starting YouTube"
  - ali_abdaal_031: "My honest advice to someone who feels behind in life"
  - ali_abdaal_027: "I Read 107 Productivity Books. Here's What Actually Works."
  - cleo_abram_008: "I Challenged Boston Dynamics' Famous Atlas Robot"
- **修修 brand fit**: B
- **Why this grade**: 告白／藍圖架構可移植，但需要 修修 擁有足夠的觀眾寄生社交基礎才能讓「你相信我的判斷」機制生效；成長期頻道應搭配外部權威錨點補強信度。
- **中文化範例 (2-3 個)**:
  - 「如果我想讓生理年齡倒退 10 年，我會完全按照這個計畫做【完整藍圖】」
  - 「9 件關於長壽，我希望 30 歲前就有人告訴我的事」
  - 「我追蹤了自己的健康數據 6 個月後，這是我真心的建議」
- **改編要點**:
  - 「完整藍圖」框架需要在標題中預示結構（如「完整計畫」「6 步驟」），否則只是空泛的第一人稱宣言
  - 修修 版本應將個人故事與研究來源並列（「我讀了 50 篇研究 + 親身實踐後」），而非純靠個人魅力驅動
  - 「假設性」版本（「如果我想…我會這樣做」）對頻道訂閱者數較少時效果較弱，優先使用「我已經做了 X」的實際行動版
- **避免**:
  - Hormozi 式的財富誇耀語氣移植（「如果我想在 40 歲前成為健康百萬富翁」）
  - 沒有具體行動步驟的純心情告白，觀眾期待可操作建議
  - 「你的身體會感謝你」的情感操縱版，需確保情感框架有實證依據

---

### T-A5. Exclusive Secret / Hidden Truth

- **One-line**: Title implies the viewer is about to receive insider knowledge that is rare, suppressed, or typically withheld from the public.
- **Frameworks**: Loewenstein (§1.1) × Cialdini authority (§1.3) × Insider knowledge frame (§1.12)
- **When to use**: 當內容確實揭露非顯而易見的機制或專家層級知識時，例如解析醫學文獻中一般大眾不會注意的細節；適合搭配命名權威來源。
- **When to avoid**: 若「秘密」只是基礎常識重新包裝，或內容無法真正交付非公開資訊，此架構會讓觀眾感到被欺騙，嚴重損害信任。
- **Frequency in corpus**: 13 / 140 (9.3%)
- **Creator distribution**: Cleo Abram: 8, Jeff Su: 3, Alex Hormozi: 2
- **Canonical example**: "The Real Reason To Care About The Apple Vision Pro" — Cleo Abram
- **Mechanism**: The 'real reason' / 'what they don't tell you' frame exploits a specific Loewenstein mechanism: the viewer already believes they know the topic, so revealing a hidden layer creates a second-order curiosity gap ('I know the surface story but not the true explanation'). This is more potent than simple 'did you know?' curiosity because it simultaneously implies prior deception or incompleteness, activating mild epistemic anxiety that the click resolves.
- **Sample examples**:
  - cleo_abram_019: "The Real Reason To Care About The Apple Vision Pro"
  - cleo_abram_022: "The Truth About Egg Freezing"
  - alex_hormozi_008: "Brutally Honest Truths That Give You an Unfair Advantage"
  - jeff_su_026: "The Only AI Tools You Actually Need (12-Minute Guide)"
  - cleo_abram_020: "The Real Reason We Should Revive Extinct Animals"
- **修修 brand fit**: B
- **Why this grade**: 「真相」與「你不知道的事」框架在健康領域潛力大，但必須謹慎避免滑向陰謀論或 fear-mongering 語氣；有具體研究支撐時方可使用。
- **中文化範例 (2-3 個)**:
  - 「你真正需要在意的健康數字，不是你以為的那個（研究發現這個指標更準確）」
  - 「凍卵的真相：醫師不會主動告訴你的 5 件事」
  - 「你一直被誤導的長壽觀念——真相其實是這樣（哈佛 20 年追蹤研究）」
- **改編要點**:
  - 「真相」「秘密」等詞需立即搭配來源錨點（「哈佛研究」「台大醫師」），才能符合 修修 的實證語氣
  - 用「你真正需要在意的」代替「他們不告訴你的」，後者暗示陰謀論，前者是正向的知識引導
  - 括號內加入具體數字或研究時間跨度以增強 specificity bias
- **避免**:
  - 「醫藥公司不告訴你的秘密」——明確的陰謀論框架，嚴重損害信度
  - 「秘密武器」「隱藏技巧」等行銷術語
  - 在沒有內容支撐的情況下使用「真相」一詞

---

### T-A6. Question Curiosity Gap

- **One-line**: Title poses a direct question — often personal-stakes or wonder-framed — that the video answers.
- **Frameworks**: Loewenstein (§1.1) × Pattern interrupt (§1.7)
- **When to use**: 科普解析或「長壽奇問」類內容；當問題對觀眾有個人切身感（「我的身體會怎樣？」）或文化高度相關（「台灣人的飲食習慣」）時效果最佳。
- **When to avoid**: 問題過於抽象或學術性時，觀眾無法感受到個人利害關係；或答案已被問題本身強烈暗示時，好奇心缺口消失，點擊動力不足。
- **Frequency in corpus**: 13 / 140 (9.3%)
- **Creator distribution**: Cleo Abram: 10, Ali Abdaal: 1, Jeff Su: 1, Alex Hormozi: 1
- **Canonical example**: "What Keeps Physicists Up at Night About Black Holes? (Feat. Brian Cox)" — Cleo Abram
- **Mechanism**: Direct questions exploit Loewenstein's 'knowledge of ignorance' mechanism: once the viewer reads the question, they become aware of not knowing the answer, and the discomfort of the open information gap is only resolvable by clicking. The 'What would happen to me…' first-person variant adds an identity stake that makes the gap feel personally consequential rather than academically abstract, increasing click urgency.
- **Sample examples**:
  - cleo_abram_024: "What Bothers Physicists About Black Holes (Interview with Brian Cox)"
  - cleo_abram_025: "What If I Fell Into A Black Hole"
  - ali_abdaal_035: "Why Are You Always Distracted? (5 Mistakes You're Making)"
  - cleo_abram_026: "What If You Just Keep Digging"
  - jeff_su_031: "Why Is Your Cover Letter Getting Rejected? (5 Mistakes to Avoid)"
- **修修 brand fit**: A
- **Why this grade**: 問題框架與台灣健康受眾對「科普奇問」的高度興趣完全契合，且 Cleo Abram 的 wonder-curiosity 結構在長壽科學領域直接可移植。
- **中文化範例 (2-3 個)**:
  - 「讓頂尖醫師感到困擾的長壽問題，到底有沒有答案？（專訪台大醫學研究員）」
  - 「如果你一個月不睡覺，你的身體會發生什麼事？」
  - 「為什麼你越睡越累？5 個正在毀掉你睡眠品質的習慣」
- **改編要點**:
  - 問題必須感覺「個人切身」而非學術性——「你的身體」「你的大腦」比「人類身體」更有效
  - 在問題後加入括號補充「（科學解答）」「（專訪 XXX 醫師）」，讓 修修 的實證標記保留在標題中
  - Wonder-framing 的問題（「如果你一個月不睡覺」）對 修修 尤其有效，因為它讓困難的科學概念變得可想像
- **避免**:
  - 問題暗示恐怖或末日情境（「你的身體正在崩潰嗎？」）——違反 no fear-mongering 原則
  - 答案過於顯而易見的問題（「你知道睡眠重要嗎？」）無法創造真正的好奇心缺口
  - 過長的問題句——標題中的問句應在 15 字以內

---

### T-A7. Duration / Time-Compression Promise

- **One-line**: Title promises that a large body of knowledge or skill can be acquired in a precisely specified short time (8 min, 13 min, 80% in 20 min).
- **Frameworks**: Specificity bias (§1.6) × Cialdini commitment-consistency (§1.3) × Loewenstein (§1.1)
- **When to use**: 教學或指南類內容，主題被觀眾感知為「複雜難懂」但 修修 能將其拆解為可學習的步驟；Pareto 變體（「掌握 80%」）在複雜健康話題上尤其有效。
- **When to avoid**: 敘事型或深度訪談內容不適合，時間壓縮暗示削弱內容的深度感；複雜醫學議題若用此框架可能顯得過於輕率，損害信度。
- **Frequency in corpus**: 12 / 140 (8.6%)
- **Creator distribution**: Jeff Su: 9, Alex Hormozi: 3
- **Canonical example**: "Master 80% of NotebookLM in 13 Minutes" — Jeff Su
- **Mechanism**: The precise time anchor activates Specificity bias: '13 minutes' feels more credible than 'quickly' because it implies the creator measured it. The Pareto sub-variant ('80% in N minutes') additionally lowers the commitment threshold by explicitly telling the viewer they do not need the remaining 20%, triggering cognitive ease and reducing the 'is this worth my time?' friction that blocks clicks on long-form tutorials.
- **Sample examples**:
  - jeff_su_015: "Learn 80% of NotebookLM in Under 13 Minutes!"
  - jeff_su_017: "Master 85% of Google Gemini in 12 Minutes"
  - alex_hormozi_002: "13 Years Of Brutally Honest Business Advice in 90 Mins"
  - jeff_su_034: "寫出完美的ChatGPT指令 8分鐘搞定！"
  - jeff_su_035: "給入門者的Google AI課程 (只要10分鐘)!"
- **修修 brand fit**: B
- **Why this grade**: 公式可移植，但對健康複雜議題需謹慎選題——「掌握 80% 的長壽法則」需要確保影片真的能兌現「可操作」的承諾，而不只是概念介紹。
- **中文化範例 (2-3 個)**:
  - 「13 分鐘學會 80% 的長壽飲食原則（哈佛研究精華濃縮版）」
  - 「打造完美睡眠習慣，7 分鐘搞定！」
  - 「用這 20 分鐘，掌握間歇性斷食最關鍵的 80%」
- **改編要點**:
  - 修修 版本的 Pareto 變體（「掌握 80%」）應限制在「可學習的方法論」類內容，不適用於「診斷」或「風險評估」類影片
  - 時間承諾必須真實——影片長度應接近標題中承諾的時間，否則觀看完成率下滑損害演算法
  - Jeff Su 的「N 分鐘搞定」原生中文版可直接作為 修修 的標題模板（jeff_su_034、jeff_su_035）
- **避免**:
  - 將此框架套用於需要長期執行的健康建議（「7 天逆轉糖尿病」——過度承諾且不實）
  - 使用「搞定」「輕鬆掌握」等詞組時確認影片實際可操作性，否則評論區負評會積累
  - 時間壓縮與極複雜醫學主題的組合（如基因檢測解讀）

---

### T-A8. Authority-Research Credibility Lead

- **One-line**: Title leads with a named authority figure, institution, hyper-specific data point, or personal consumption of a large body of research to validate the content.
- **Frameworks**: Cialdini authority (§1.3) × Specificity bias (§1.6) × Loewenstein (§1.1)
- **When to use**: 研究綜合、專家訪談或大量數據分析類內容；在健康與長壽領域，命名研究機構或具體研究人數是點擊力最強的信度信號之一。
- **When to avoid**: 當「權威」是模糊的（「研究顯示」沒有具體來源）時，此架構的效力消失；也不適合純個人意見性內容，否則權威感成為空殼。
- **Frequency in corpus**: 9 / 140 (6.4%)
- **Creator distribution**: Cleo Abram: 4, Ali Abdaal: 2, Alex Hormozi: 2, Jeff Su: 1
- **Canonical example**: "I Read 107 Productivity Books. Here's What Actually Works." — Ali Abdaal
- **Mechanism**: Hyper-specific numbers (107 books, 6,642 Googlers) exploit the Specificity bias mechanism: the brain interprets precision as evidence of actual measurement, converting a self-reported claim into what feels like credibility signal. Named authority figures layer Cialdini's authority principle, and the combination makes the Loewenstein gap more potent — the viewer wants the distilled output of an effort they know they would never personally undertake.
- **Sample examples**:
  - ali_abdaal_027: "I Read 107 Productivity Books. Here's What Actually Works."
  - ali_abdaal_028: "I Tried 137 Productivity Tools. These Are The Best."
  - cleo_abram_003: "Editing Your DNA, with Nobel Prize Winner Dr. Jennifer Doudna"
  - cleo_abram_011: "NVIDIA CEO Jensen Huang's Vision for the Future"
  - jeff_su_027: "The Productivity System I Taught to 6,642 Googlers"
- **修修 brand fit**: S
- **Why this grade**: 這是 修修 品牌五大核心支柱之一（Evidence-based authority + Familiar authority anchors），幾乎不需調整就能直接使用，是語料庫中少數真正達到 S 級的架構。
- **中文化範例 (2-3 個)**:
  - 「我讀了 50 篇長壽研究論文，這些方法才是真正有效的」
  - 「哈佛長壽研究員 David Sinclair 對未來健康的完整預測」
  - 「我追蹤了 3,847 位學員的健康數據，發現這個習慣最關鍵」
- **改編要點**:
  - 命名外部權威時優先選用 修修 已建立的「熟悉權威錨點」（台大醫師 / 哈佛研究 / Attia / Huberman / Bryan Johnson），而非語料庫原版的商業人物
  - 「我讀了 N 篇研究」中的數字必須是真實數字，不可捏造——修修 的信度品牌容錯空間比 Hormozi 低得多
  - 「這些才是真正有效的」（"Here's What Actually Works"）是 Ali Abdaal 的 actually-corrective 語氣，與 修修 風格高度吻合，可直接轉用
- **避免**:
  - 命名未實際出鏡或參與內容製作的人物（會被觀眾識破）
  - 「N 年研究經驗」等無法驗證的時間宣稱
  - 數字精確但實際影片內容稀薄——高度具體的數字承諾需要同等具體的內容交付

---

### T-A9. Year-Anchor Currency Signal

- **One-line**: Title includes a specific year (2024, 2026) as a freshness and relevance marker, implying that older advice is now outdated.
- **Frameworks**: Loewenstein (§1.1) × Loss aversion (§1.5) × Specificity bias (§1.6)
- **When to use**: 年度趨勢更新、工具或研究有重大新進展、或「最新版」教學內容；在健康領域，新研究推翻舊建議時特別有效。
- **When to avoid**: 長青型內容（「睡眠基礎原理」「肌肉生長機制」）加上年份錨點反而縮短影片的長尾搜尋壽命，非必要不加。
- **Frequency in corpus**: 6 / 140 (4.3%)
- **Creator distribution**: Alex Hormozi: 3, Jeff Su: 2, Ali Abdaal: 1
- **Canonical example**: "The CORRECT Way to Use ChatGPT in 2026" — Jeff Su
- **Mechanism**: Year anchors trigger a mild loss-aversion mechanism: the viewer implicitly worries their current knowledge or strategy is already obsolete, creating urgency to update. The year also serves as a cognitive shortcut for 'this was recently verified', reducing the viewer's need to evaluate the content's credibility independently. In fast-moving domains (AI tools, business strategy), the year signal can be the single deciding factor between clicking a refreshed tutorial versus an older one.
- **Sample examples**:
  - jeff_su_024: "The CORRECT Way to Use ChatGPT in 2026"
  - ali_abdaal_003: "6 Habits to Adopt Before 2026 to Make It Your Best Year"
  - alex_hormozi_018: "If I Were Starting a Business in 2026, I'd Only Do This"
  - alex_hormozi_024: "If You Watch One Video Before 2026, Watch This…"
  - jeff_su_021: "NotebookLM Has Completely Changed (2026)"
- **修修 brand fit**: B
- **Why this grade**: 健康研究領域確實有年份更新需求，但此架構在健康內容的應用比科技工具更受限——年份錨點只在研究有重大更新時方才適用，不可泛用。
- **中文化範例 (2-3 個)**:
  - 「2026 年最新長壽研究：這 6 個習慣你一定要重新評估」
  - 「如果你想讓 2026 成為你最健康的一年，請看完這支影片⋯」
  - 「最新研究全改了！2026 你真正需要知道的間歇性斷食更新」
- **改編要點**:
  - 年份錨點在 修修 頻道中應搭配「研究有重大更新」的具體敘述（「最新研究推翻了 X」），而非純粹作為時效標記
  - 年份放在括號或句尾（「2026 最新版」）比放在標題前端更自然，避免標題開頭顯得像廣告
  - 長青型基礎科學內容（代謝機制、細胞老化原理）不加年份，保留搜尋長尾價值
- **避免**:
  - 每支影片都加年份——年份氾濫後「新鮮感」信號消失
  - 用年份錨點掩蓋內容實際上沒有更新的事實（觀眾會留言指出）
  - 跨年後忘記更新年份標記導致影片看起來過時

---

### T-A10. Cost-Risk-Reframe / Loss Aversion Lead

- **One-line**: Title foregrounds what the viewer is currently losing, wasting, or getting wrong — flipping from gain to loss framing — often with a specific number.
- **Frameworks**: Loss aversion (§1.5) × Loewenstein (§1.1) × Specificity bias (§1.6)
- **When to use**: 行為改變類內容，觀眾很可能正在進行有害習慣卻不自知；搭配具體量化成本（「浪費 80% 的睡眠恢復力」）效果最強。
- **When to avoid**: 當觀眾已充分意識到風險時，loss framing 失效且顯得說教；不適合用在已具備高健康意識的核心訂閱者，他們需要的是解決方案而非再次被提醒問題。
- **Frequency in corpus**: 6 / 140 (4.3%)
- **Creator distribution**: Alex Hormozi: 2, Jeff Su: 2, Ali Abdaal: 2
- **Canonical example**: "You're Wasting 80% of Your Time (Here's the Fix)" — Alex Hormozi
- **Mechanism**: Prospect theory predicts losses loom roughly twice as large as equivalent gains in decision-making. By foregrounding a specific cost ('80% of your time', '90% of people wrong'), these titles convert a neutral curiosity into an active threat perception. The parenthetical fix-promise ('Here's the Fix') then provides the resolution pathway, creating a push-pull structure where the loss frame drives the click and the fix promise justifies watching the full video.
- **Sample examples**:
  - alex_hormozi_035: "You're Wasting 80% of Your Time (Here's the Fix)"
  - jeff_su_030: "Why 90% of AI Presentations Fail (do this instead)"
  - ali_abdaal_023: "How to Stop Wasting Your Life (Avoid These 5 Things)"
  - ali_abdaal_013: "How to Control Your Dopamine (Before It's Too Late)"
  - jeff_su_031: "Why Is Your Cover Letter Getting Rejected? (5 Mistakes to Avoid)"
- **修修 brand fit**: B
- **Why this grade**: Loss framing 符合修修的 factual 框架原則（「研究發現 X 增加 Y 風險」），但需嚴格避免情緒化誇大；括號內的「解法承諾」是保持非 fear-mongering 的關鍵結構。
- **中文化範例 (2-3 個)**:
  - 「你正在浪費 80% 的睡眠恢復力——研究發現這個習慣讓你的深眠減少一半」
  - 「為什麼 90% 的人補充益生菌都沒效？（正確時機和劑量在這裡）」
  - 「你越睡越累的原因：5 個正在毀掉你睡眠品質的習慣（科學解法）」
- **改編要點**:
  - 損失陳述必須用事實語言（「研究發現 X 習慣使 Y 指標下降 Z%」），而非情緒語言（「你的身體正在崩潰」）
  - 括號內的解法承諾不可省略——它是將 fear-mongering 轉為 actionable advice 的關鍵緩衝
  - 量化損失數字（「80%」「一半」）需有研究支撐，不可捏造
- **避免**:
  - 「你的身體正在死去」「你的大腦已經受損」等恐嚇性表述——明確的 F 級反模式
  - 在已知高風險族群（如慢性病患者）面前過度強調損失而不提解法
  - 將此架構用於正面習慣建立類內容——loss frame 在獎勵導向的習慣養成語境中顯得格格不入

---

## 3. Thumbnail Archetypes

### T-V1. Face-Right Text-Left Dual-Zone

- **One-line**: Creator face occupies the right 40-50% of frame; high-contrast, large-type text or a key phrase dominates the left zone.
- **Frameworks**: Face emotion contagion (§1.8) × Pattern interrupt (§1.7) × Cognitive ease (§1.11)
- **When to use**: 強觀點或命令式標題的視覺配對，例如直接的行為建議或反直覺主張；創作者的表情需要與左側文字情緒一致，才能產生「情緒驗證」效果。
- **When to avoid**: 輕鬆探索型或「wonder」類內容，嚴肅表情與好奇心框架產生衝突；文字超過 6 個字時左側區塊會過於擁擠，在手機螢幕上失去可讀性。
- **Frequency in corpus**: 19 / 140 (13.6%)
- **Creator distribution**: Alex Hormozi: 9, Jeff Su: 8, Cleo Abram: 3, Ali Abdaal: 2
- **Canonical example**: Alex Hormozi — "SELL EXPENSIVE SH*T" overlay (alex_hormozi_004)
- **Mechanism**: The brain's left-to-right reading bias means viewers read the text first and then their gaze naturally travels to the face. The face then serves as an emotional validator of the claim — a serious face amplifies an alarming text; an excited face amplifies a gain claim. This bidirectional reinforcement makes the thumbnail narrative complete without requiring the viewer to read the title at all.
- **Sample examples**:
  - alex_hormozi_004: "SELL EXPENSIVE SH*T"
  - alex_hormozi_028: "PUT YOUR DAMN PHONE AWAY"
  - ali_abdaal_007: "no one cares"
  - jeff_su_016: "perplexity"
  - jeff_su_033: "teach me… automate task"
- **修修 brand fit**: B
- **Why this grade**: 版面架構完全可移植，但必須將 Hormozi 的命令式粗體文字替換為 修修 的實證關鍵詞或數字，並調整表情從「對抗性」轉為「誠懇解說型」。
- **中文化範例 (2-3 個)**:
  - 左側大字：「肌酸 5g」→ 右側：修修面帶認真解說表情（搭配 T-A8 標題）
  - 左側大字：「睡眠效率」→ 右側：修修指向左側的親切表情（搭配 T-A2 標題）
  - 左側大字：「研究推翻了」→ 右側：修修輕微皺眉的驚訝表情（搭配 T-A3 標題）
- **改編要點**:
  - 左側文字控制在 3-5 個繁中字，超過則在手機尺寸下失去衝擊力
  - 選用「數字 + 名詞」（「5g 肌酸」「12 週改變」）代替命令句（「你要這樣做」），符合修修的 specificity-over-hype 原則
  - 背景保留暗色（#1A1A1A 至 #2B2B2B）可增加醫療/科學感；若選用明亮色調則需確保表情對應的是好消息而非警示
- **避免**:
  - 粗口或高強度命令語氣文字（Hormozi 商標做法，文化上不適合台灣健康受眾）
  - 表情與左側文字情緒相反（嚴肅表情搭配正面數字，會造成認知混亂）
  - 左側文字過度設計（裝飾字體、多色漸層），損害 cognitive ease

---

### T-V2. Face-Center Tight Crop with Text Overlay

- **One-line**: Creator face fills the center frame with a tight crop (chin to forehead), with a short text overlay anchored at bottom or floating over chest.
- **Frameworks**: Face emotion contagion (§1.8) × Loewenstein (§1.1)
- **When to use**: 高情緒告白類或「個人觀點揭示」類影片；適合 修修 分享親身實驗結果或令人驚訝的研究發現，此時表情本身即是最強的點擊訊號。
- **When to avoid**: 需要視覺背景或道具說明的工具型、比較型內容；臉部特寫對無法傳達足夠情緒表現的中性話題效果極差。
- **Frequency in corpus**: 20 / 140 (14.3%)
- **Creator distribution**: Alex Hormozi: 6, Cleo Abram: 5, Ali Abdaal: 3, Jeff Su: 1
- **Canonical example**: Cleo Abram — "DID WE DO IT?" overlay (cleo_abram_014)
- **Mechanism**: Tight facial crops maximise face emotion contagion because the viewer's mirror-neuron system responds more strongly to faces that fill the visual field. The absence of competing visual elements forces the viewer to seek meaning in the expression itself, creating an automatic Loewenstein gap: 'Why does this person look like that?' The dark background strategy reduces competing stimuli in a busy feed, making the face pop as a pattern interrupt.
- **Sample examples**:
  - alex_hormozi_013: "'IT ONLY TAKES 7 DAYS'"
  - ali_abdaal_027: "(none — surprised face)"
  - cleo_abram_001: "FAKE / FAKE"
  - cleo_abram_014: "DID WE DO IT?"
  - jeff_su_006: "AI AGENTS"
- **修修 brand fit**: B
- **Why this grade**: 此版面需要 修修 具備足夠的觀眾寄生社交基礎，讓臉部本身成為點擊動力；頻道成長初期建議搭配更多資訊性視覺元素，純臉部特寫在知名度不足時效果有限。
- **中文化範例 (2-3 個)**:
  - 臉部特寫（驚訝表情）+ 底部覆字：「研究顛覆了」（搭配 T-A3 標題：「你一直吃錯了蛋白質」）
  - 臉部特寫（認真嚴肅）+ 浮動覆字：「這個數字很危險」（搭配 T-A10 標題）
  - 臉部特寫（真誠親切）+ 無覆字（搭配 T-A4「我追蹤了自己的健康數據 6 個月後」）
- **改編要點**:
  - 修修 的表情範圍應偏向「真誠驚訝」和「認真解說」，而非 Hormozi 的「高強度瞪視」——後者文化適配度低
  - 暗色背景（深藍 #1A1A2E、深灰 #1A1A1A）適合嚴肅健康議題；建議避免純黑底因為會讓人聯想到喪葬
  - 覆字建議 2-4 繁中字，使用白色或亮黃色，不超過兩行
- **避免**:
  - 完全無覆字的臉部特寫在頻道訂閱者數 <10 萬時風險較高，除非表情本身極具戲劇性
  - 強烈人工合成或 AI 處理的背景（如螢光紫渦旋）在健康頻道中顯得不可信
  - 表情過於誇張（大叫、瞪眼）——健康內容需要信任感，MrBeast 等級的誇張表情在此頻道適得其反

---

### T-V3. Split-Screen Comparison

- **One-line**: Thumbnail is divided into two equal zones — before/after, option A/option B, person A vs. person B — creating a visible contrast tension.
- **Frameworks**: Pattern interrupt (§1.7) × Loewenstein (§1.1) × Familiarity scaffolding (§1.10)
- **When to use**: 比較型、決策型或轉變型內容；在健康領域，「干預前/後」「習慣A vs.習慣B」「有做/沒做」的對比是天然適合此版面的題材。
- **When to avoid**: 沒有真正二元對比的內容不應強行使用分割版面，人工製造的對比會讓觀眾感到被操縱；單一主題的深度解析也不適合。
- **Frequency in corpus**: 12 / 140 (8.6%)
- **Creator distribution**: Cleo Abram: 5, Ali Abdaal: 4, Alex Hormozi: 1, Jeff Su: 1
- **Canonical example**: Ali Abdaal — "before / after" (ali_abdaal_020)
- **Mechanism**: Split-screen thumbnails exploit the brain's comparative evaluation system: two juxtaposed states instantly communicate that a choice or transformation is at stake. The familiarity scaffolding mechanism fires because 'before/after' is a culturally universal narrative structure that requires zero explanation. The Loewenstein gap is then generated by the missing middle — what causes the transition from left to right, which the title implies the video will explain.
- **Sample examples**:
  - alex_hormozi_029: "COLLEGE / NO COLLEGE"
  - ali_abdaal_020: "before / after"
  - ali_abdaal_023: "before / after"
  - cleo_abram_006: "FAKE / FAKE"
  - ali_abdaal_035: "before / after"
- **修修 brand fit**: A
- **Why this grade**: 「干預前後」對比在健康內容中有直接且文化普遍的應用，且不需要大幅調整——before/after 結構在台灣健康受眾中同樣具備零解釋成本的熟悉度。
- **中文化範例 (2-3 個)**:
  - 左側「一般飲食」（灰調食物照）vs. 右側「抗老飲食」（鮮豔色調）+ 修修表情在中間分隔線旁（搭配 T-A2 或 T-A6 標題）
  - 左側「8 週前：體脂 24%」vs. 右側「8 週後：體脂 19%」數字標籤對比（搭配 T-A2 標題）
  - 左側暗色調「不運動的大腦掃描」vs. 右側亮色調「運動後的大腦掃描」+ 覆字「你的大腦」（搭配 T-A6 或 T-A3 標題）
- **改編要點**:
  - 對比標籤使用繁中而非英文（「運動前」「運動後」而非 "before/after"），保持語言一致性
  - 左右色溫對比盡量明顯——冷色調（藍灰）代表「問題狀態」，暖色調或高飽和色代表「改善狀態」，符合台灣受眾的視覺直覺
  - 對比的兩個狀態必須在影片中真實呈現，不可純靠視覺誤導
- **避免**:
  - 強行對比兩個沒有明確優劣的選項（創造假二元對立，損害信度）
  - 使用 AI 合成的「before/after」人物照片（健康頻道語境下真實性尤其重要）
  - 對比區塊資訊密度過高，手機螢幕下無法辨識

---

### T-V4. Face-Left Text-Right Exposition Layout

- **One-line**: Creator face is anchored left (often pointing toward text); the right zone contains either text overlay, a visual prop, icon cluster, or diagram.
- **Frameworks**: Face emotion contagion (§1.8) × Pattern interrupt (§1.7) × Cognitive ease (§1.11)
- **When to use**: 教學解說、配方揭示或工具比較型內容；創作者的臉部指向動作將觀眾視線引導至右側的資訊區塊，適合當右側內容本身需要被「展示」的情境。
- **When to avoid**: 右側資訊區塊過於複雜密集時，手機尺寸下無法辨識，反而造成視覺混亂；不適合純情緒或告白型內容，因右側內容分散了表情的情緒焦點。
- **Frequency in corpus**: 16 / 140 (11.4%)
- **Creator distribution**: Cleo Abram: 5, Alex Hormozi: 4, Jeff Su: 4, Ali Abdaal: 3
- **Canonical example**: Jeff Su — "Perfect Prompt = [task][context][format][tone]" (jeff_su_034)
- **Mechanism**: The pointing or angled face acts as a visual arrow, leveraging the social referencing instinct — humans automatically look where others are looking. This directs viewer attention from the emotional anchor (face) to the information payload (right zone), effectively creating a two-beat thumbnail narrative: 'someone is excited' → 'about this specific thing'. The dual-zone structure also allows the thumbnail to communicate at two different reading distances (small on mobile: face; zoomed: text).
- **Sample examples**:
  - jeff_su_034: "Perfect Prompt = [task][context][format][tone]"
  - cleo_abram_002: "NO"
  - cleo_abram_031: "GRAVITY IS FAKE*"
  - cleo_abram_005: "(excited face, lightsaber prop)"
  - jeff_su_001: "= autoformat ✦"
- **修修 brand fit**: A
- **Why this grade**: 此版面對 修修 的「教學解說 + 實證框架」內容天然契合，右側區塊可以放置研究數據、補充品劑量表、或簡化的機制圖示，讓視覺直接傳達信度。
- **中文化範例 (2-3 個)**:
  - 左側：修修指向右側，略帶驚訝表情；右側：「5g 肌酸 × 6 週 = ?」（搭配 T-A8 或 T-A2 標題）
  - 左側：修修認真表情面向右；右側：簡化的間歇性斷食時間軸圖示（搭配 T-A7 標題）
  - 左側：修修微笑指向；右側：「哈佛研究 × 20 年追蹤」大字（搭配 T-A8 標題）
- **改編要點**:
  - 右側資訊區塊在手機尺寸（約 168px 寬）下仍需清晰辨識，上限約 3-4 個元素（字 + 數字 + 簡單圖示）
  - 修修 指向動作應自然而非刻意——微微轉向右側比誇張指指點點更符合親切權威的品牌調性
  - 右側資訊選用能「一眼看出意義」的視覺化元素（時間軸、箭頭前後、簡單分子圖示），而非密密麻麻的文字
- **避免**:
  - 右側放置超過 5 個視覺元素，縮圖尺寸下完全無法解讀
  - 左側臉部佔比超過 60%，導致右側資訊區塊被壓縮到無法閱讀
  - 指向動作過於做作（手伸直指著鏡頭外）——保持自然的身體語言

---

### T-V5. Whiteboard / Diagram Reveal with Creator

- **One-line**: Creator stands beside or points at a whiteboard, roadmap diagram, or dense annotated list, signalling comprehensive insider content.
- **Frameworks**: Insider knowledge frame (§1.12) × Loewenstein (§1.1) × Cognitive ease (§1.11)
- **When to use**: 完整藍圖、系統建立或大師課類內容；當影片確實提供了一個完整的框架或系統，視覺上的「密度」是一個誠實的內容信號。
- **When to avoid**: 簡單習慣建議或短篇科普不適合——圖表密度與實際內容深度不符時，觀眾看完影片後會感到受騙，損害留存率。
- **Frequency in corpus**: 5 / 140 (3.6%)
- **Creator distribution**: Alex Hormozi: 4, Ali Abdaal: 1
- **Canonical example**: Alex Hormozi — "FULL GUIDE / LONG GAME / DON'T DIVERSIFY…" (alex_hormozi_019)
- **Mechanism**: **(v1.1 panel revision — Gemini audit §4)** The dense, near-unreadable-at-thumbnail-scale diagram works via **Proof of Work / Complexity Signaling** (consistent with §1.10 Familiarity Scaffolding and §1.6 Specificity), not via Loewenstein's information gap. The viewer cannot read the diagram, but its existence signals that the creator has done extensive synthesis to compress complexity into a system — this raises credibility before the viewer commits to clicking. The mechanism is **the existence of the system, not the contents** of the diagram.
- **Sample examples**:
  - alex_hormozi_019: "FULL GUIDE / LONG GAME / DON'T DIVERSIFY…"
  - alex_hormozi_020: "START FROM ZERO / WHO YOU ARE…"
  - alex_hormozi_021: "$100,000"
  - alex_hormozi_031: "FREE SALES COURSE / 1–6 modules"
  - ali_abdaal_033: "GOALS → SYSTEMS"
- **修修 brand fit**: B
- **Why this grade**: 白板系統圖在健康領域的「長壽完整藍圖」影片中可行，但需搭配真正複雜且完整的內容交付，否則圖表密度成為欺騙性信號；Hormozi 版本的 aggressive 視覺風格也需調整為更學術的設計語言。
- **中文化範例 (2-3 個)**:
  - 修修 站在白板旁（綠色或藍色底），板上有「長壽 6 大支柱」結構圖（每個支柱下有小字說明，縮圖尺寸下剛好無法完整閱讀），搭配標題「如果我想讓生理年齡倒退 10 年，我會完全按照這個計畫做【完整藍圖】」
  - 修修 指向「HRV / 睡眠分期 / 肌肉量 / 粒線體功能」四大監測指標的流程圖，搭配 T-A8 類標題
- **改編要點**:
  - 圖表設計語言應趨向「學術簡報風格」（白底 + 深藍線條 + 清晰字體），而非 Hormozi 的螢光綠手寫粗體
  - 「部分可讀性」是關鍵設計原則——縮圖下能看到框架結構但無法讀清每個細節，製造好奇心缺口
  - 搭配 T-A4（個人藍圖）或 T-A8（權威研究）標題時最有效
- **避免**:
  - 圖表完全清晰可讀——失去好奇心缺口機制
  - 圖表完全無法辨識結構——視覺雜訊，無法傳達「系統存在」的信號
  - 將此版面用於實際上只有 3-5 個重點的簡單內容

---

### T-V6. Surprised / Excited Face with Question-Mark Overlay

> **v1.1 panel downgrade** (Gemini audit §1c, mission-critical): This archetype's grade is changed from C to **D / Avoid** for 修修's health channel. The Cleo Abram-style "found it? / solved it?" question overlay maps to **content-farm clickbait aesthetic in Taiwan/Hong Kong**, where such phrasing on health/medical topics aligns with miracle-cure scam content. There is also non-trivial regulatory risk: phrases implying medical breakthrough may run afoul of Taiwan's Health Food Control Act (健康食品管理法) and HK's equivalent advertising guidelines. The pattern's emotional cliff-hanger mechanism turns into a life-or-death question when applied to health — that's a credibility liability, not a click asset. **Use only with truly novel research the channel can cite, AND non-clickbait phrasing.**


- **One-line**: Creator displays exaggerated surprise or excitement; short overlay text ends in a question ('THEY SOLVED IT?', 'DID WE DO IT?', 'TOO GOOD?') leaving the answer open.
- **Frameworks**: Face emotion contagion (§1.8) × Loewenstein (§1.1) × MrBeast PVP (§1.2)
- **When to use**: 突破性研究發現、科學謎題解開或親身實驗揭示類內容；特別適合「科學家終於有答案了嗎？」類型的長壽研究更新影片。
- **When to avoid**: 答案已在標題中強烈暗示的內容，問號框架會顯得虛假；也不適合 修修 對已有定論的健康議題使用問號（否則暗示內容製造不確定性而非解決不確定性）。
- **Frequency in corpus**: 8 / 140 (5.7%)
- **Creator distribution**: Cleo Abram: 7, Alex Hormozi: 1
- **Canonical example**: Cleo Abram — "THEY SOLVED IT?" (cleo_abram_010)
- **Mechanism**: The open question combined with an excited or surprised face creates an 'emotional cliff-hanger': the face signals that something significant has happened, but the question refuses to confirm it. This fires the Loewenstein gap at a primal level because emotion without context is deeply uncomfortable for the human brain, which is wired to resolve ambiguous social signals. The MrBeast-PVP mechanism also activates because 'they found it' implies ongoing action the viewer risks missing.
- **Sample examples**:
  - cleo_abram_010: "THEY SOLVED IT?"
  - cleo_abram_013: "THEY SOLVED IT?"
  - cleo_abram_017: "THEY FOUND IT?"
  - cleo_abram_014: "DID WE DO IT?"
  - cleo_abram_021: "TOO GOOD?"
- **修修 brand fit**: C
- **Why this grade**: 機制理論上可行，但此版面在健康領域的應用風險較高——「解開了嗎？」問句容易暗示醫療突破，若內容不能交付真正的突破性資訊，會損害信度；需謹慎使用並加入明確的實證錨點。
- **中文化範例 (2-3 個)**:
  - 修修 驚訝表情 + 覆字「真的找到了？」（搭配標題：「長壽基因的謎題⋯研究終於有解答了？」）——需確保影片確有具體研究支撐
  - 修修 興奮表情 + 覆字「成功了嗎？」（搭配標題：「我親身測試了 David Sinclair 的 NMN 計畫 90 天」）——個人實驗語境下風險較低
- **改編要點**:
  - 問句必須在影片中誠實作答——若最終答案是「部分有效」「需要更多研究」，標題和縮圖仍可使用此架構，但影片不可虛假承諾
  - 表情應為「真誠驚訝」而非「誇張表演」——健康受眾對過度表演的可信度評分較低（參見 MC-4）
  - 高飽和背景色（Cleo 版的 #4CAF50 綠）在健康語境中需對應話題顏色，如藍色系（細胞、基因）或綠色系（飲食、植物多酚）
- **避免**:
  - 對已有科學定論的議題使用問號框架（如「睡眠重要嗎？」——答案顯而易見，問號顯得弱智）
  - 配合此版面使用「被治癒了？」「治好了嗎？」等暗示療效的問句——在台灣受到健康食品廣告法規約束
  - 過度使用此架構——在健康頻道中問號問句若每三支就出現一次，會讓頻道感覺內容品質不穩定

---

### T-V7. Object / Tool Hero with Creator Reaction

- **One-line**: A product, tool interface, diagram, or physical object is the visual hero; creator face is secondary, often reacting to or holding the object.
- **Frameworks**: Familiarity scaffolding (§1.10) × Pattern interrupt (§1.7) × Loewenstein (§1.1)
- **When to use**: 補充品評測、可穿戴健康裝置評估、或特定食物 / 飲食方案的實測類內容；當物品本身對目標觀眾具有已知的辨識度時（如知名品牌蛋白粉、Oura Ring、CGM 血糖監測器）。
- **When to avoid**: 抽象概念或機制解釋型內容，強制放入物品作為主角會造成視覺錯位；若物品在目標受眾中完全陌生，熟悉度支架機制失效。
- **Frequency in corpus**: 11 / 140 (7.9%)
- **Creator distribution**: Cleo Abram: 4, Jeff Su: 4, Ali Abdaal: 2, Alex Hormozi: 1
- **Canonical example**: Alex Hormozi — "207g protein / 86g protein (flanked by food)" (alex_hormozi_030)
- **Mechanism**: Familiar objects (branded apps, robots, physical tools) trigger the familiarity scaffolding mechanism: the viewer instantly knows the domain context without reading the title, reducing cognitive load at the moment of scroll-stopping. The creator's reactive face then adds social proof — 'someone I know finds this noteworthy' — which elevates the object from neutral to interesting through emotional contagion.
- **Sample examples**:
  - cleo_abram_008: "(robot, shocked face)"
  - jeff_su_007: "LEVEL 1 / LEVEL 999 (ChatGPT box)"
  - ali_abdaal_015: "my system (planner/book prop)"
  - alex_hormozi_030: "207g protein / 86g protein (flanked by food)"
  - jeff_su_013: "(Claude/ChatGPT logo icons, thoughtful face)"
- **修修 brand fit**: A
- **Why this grade**: 健康工具物品（補充品包裝、血糖監測器、心率帶）對 修修 目標受眾具備直接的熟悉度支架效果，且此版面不依賴頻道知名度，適合成長期頻道使用。
- **中文化範例 (2-3 個)**:
  - 畫面主角：Oura Ring 或 Apple Watch Ultra（顯示 HRV 數據），修修 在旁邊輕微驚訝表情；覆字：「這個數字改變了我的訓練」（搭配 T-A4 標題）
  - 畫面主角：肌酸粉末容器（清楚品牌可見），修修 手持並看向鏡頭；覆字：「5g × 6 週」（搭配 T-A8 標題）
  - 畫面主角：兩種魚油膠囊並排（EPA/DHA 標籤清晰），修修 一手持一瓶做對比；覆字：「你買對了嗎？」（搭配 T-A3 或 T-A5 標題）
- **改編要點**:
  - 物品必須在台灣市場有足夠知名度（Oura Ring、CGM 血糖貼片在台灣的知名度正快速成長，合適；冷門品牌補充品不合適）
  - 修修 的反應表情應對應物品傳達的訊息——拿著「好物」時帶讚許，拿著「誤用物品」時帶輕微皺眉
  - 物品佔畫面面積至少 40%，確保在手機縮圖尺寸下清楚辨識
- **避免**:
  - 物品標籤或介面上若有誇大療效聲稱，不應直接清楚呈現（食品廣告法規）
  - 手持物品的姿勢過於廣告感（笑容完美、燈光過度打亮）——降低真實感
  - 將此版面用於完全抽象的健康概念（如「腸道菌相平衡」——沒有視覺可以作為物品主角）

---

### T-V8. High-Saturation Colour Pop with Bold Command Text

- **One-line**: Thumbnail uses a single highly saturated background or colour block with 2-4 words of bold, all-caps command or statement text.
- **Frameworks**: Pattern interrupt (§1.7) × Cognitive ease (§1.11) × Cialdini authority (§1.3)
- **When to use**: 強觀點反直覺主張或行為警示類內容；當標題已經非常具體，縮圖只需要提供「情緒色調」而不需要傳達額外資訊時，此版面效率最高。
- **When to avoid**: 溫暖或細膩的內容話題（如「心理健康」「睡眠儀式」），單一強烈色彩會造成情緒衝突；也不適合需要多元視覺資訊的比較型或系統型內容。
- **Frequency in corpus**: 13 / 140 (9.3%)
- **Creator distribution**: Alex Hormozi: 5, Cleo Abram: 3, Jeff Su: 3, Ali Abdaal: 2
- **Canonical example**: Alex Hormozi — "SELL EXPENSIVE SH*T" (#F5C518 yellow) (alex_hormozi_004)
- **Mechanism**: Single saturated colour backgrounds function as pattern interrupts in a feed dominated by photographic thumbnails — the flat colour reads as 'designed' rather than 'captured', which the brain flags as novel. The bold command text then benefits from cognitive ease: 3-4 all-caps words can be processed in under 200ms, removing the cognitive cost of reading. This combination creates a two-millisecond impression before the viewer has consciously decided to engage.
- **Sample examples**:
  - alex_hormozi_004: "SELL EXPENSIVE SH*T (#F5C518 yellow)"
  - alex_hormozi_028: "PUT YOUR DAMN PHONE AWAY (#D0021B red)"
  - cleo_abram_032: "IN OR OUT? (#4CAF50 green)"
  - ali_abdaal_004: "$947 / $329 payments (#F97316 orange)"
  - jeff_su_002: "5 HOURS → 5 MIN (#1A1A2E dark)"
- **修修 brand fit**: C
- **Why this grade**: 版面的點擊機制有效，但強命令語氣和高飽和色彩在 修修 的溫和權威品牌中容易顯得格格不入；僅在強反直覺主張或行為警示內容（搭配實證緩衝）時謹慎使用。
- **中文化範例 (2-3 個)**:
  - 深藍背景（#1A2744）+ 白色大字「肌酸不用循環」（搭配 T-A3 反直覺標題）——以深色代替 Hormozi 的高飽和暖色，降低攻擊感
  - 深綠背景（#1A4731）+ 白色大字「你睡錯了」（搭配 T-A10 標題）——使用醫學感的深綠而非螢光綠
  - 深灰背景（#2B2B2B）+ 亮黃大字「5g × 6 週」（搭配 T-A8 標題，專注在具體數字而非命令語氣）
- **改編要點**:
  - 修修 版本應將高飽和色調整為「深色系高對比」（深藍、深綠、深灰配白字），而非 Hormozi 的純高飽和暖色
  - 文字應為「事實陳述」或「數字」而非「命令句」——「研究推翻了」「5g × 6 週」比「你一定要這樣做」更符合品牌
  - 此版面每季最多使用 2-3 次，避免顯得是固定公式
- **避免**:
  - 完全複製 Hormozi 的螢光黃 + 粗口命令句——文化和品牌雙重不適配
  - 高飽和暖色系（正紅、橙、螢光黃）在健康頻道中可能引發過度的緊張感
  - 將此版面用於溫和話題（睡眠建議、飲食習慣）——情緒色調衝突

---

### T-V9. App Icon / Tool Logo Cluster with Creator Face

- **One-line**: Multiple recognisable app logos, platform icons, or UI screenshots surround or flank the creator's face, signalling multi-tool expertise.
- **Frameworks**: Familiarity scaffolding (§1.10) × Numerical anchor (§1.9) × Insider knowledge frame (§1.12)
- **When to use**: 多工具比較、最佳工具精選或生態系統概覽類內容；在健康領域可轉化為「多個健康追蹤 App 比較」或「你需要的所有健康監測工具」。
- **When to avoid**: 單一工具或補充品的深度評測——多圖示會製造廣度期望，若內容只深入一個工具，觀眾會感到 bait-and-switch。
- **Frequency in corpus**: 7 / 140 (5.0%)
- **Creator distribution**: Ali Abdaal: 3, Jeff Su: 3, Alex Hormozi: 1
- **Canonical example**: Ali Abdaal — "(app icons surrounding face)" (ali_abdaal_028)
- **Mechanism**: Recognised app icons activate the familiarity scaffolding mechanism in under 100ms — the viewer already has an emotional relationship with these brands and that trust transfers to the creator adjacent to them. Multiple icons simultaneously trigger a numerical anchor: 'there are many things here, more than I could discover alone', which makes the curation value proposition immediately legible without reading the title.
- **Sample examples**:
  - ali_abdaal_028: "(app icons surrounding face)"
  - ali_abdaal_002: "$3,000/month (icons surrounding face)"
  - jeff_su_013: "(Claude/ChatGPT logos flanking)"
  - jeff_su_026: "Life / Work (icon cluster flanking)"
  - alex_hormozi_003: "Use these cheat codes (floating icon cluster)"
- **修修 brand fit**: B
- **Why this grade**: 此版面在健康語境中可用，但需要將「App 圖示」替換為「補充品品牌 logo 或健康追蹤平台圖示」，且這些品牌在台灣健康受眾中的辨識度需要評估才能確認熟悉度支架是否生效。
- **中文化範例 (2-3 個)**:
  - 修修 居中，周圍環繞 5 個健康追蹤 App 圖示（Oura、WHOOP、Apple Health、Cronometer、Garmin Connect），覆字「哪個才是真的有用？」（搭配 T-A1 或 T-A3 標題）
  - 修修 居右，左側排列 4 個補充品品牌 logo（Nordic Naturals、Now Foods、Thorne、Life Extension），覆字「我只留這幾個」（搭配 T-A5 標題）
- **改編要點**:
  - 圖示選擇必須以台灣健康受眾的實際使用率為標準——Apple Health 和 LINE Health 的辨識度遠高於冷門 longevity 工具
  - 圖示數量以 4-6 個為上限，超過則在縮圖尺寸下難以辨識個別品牌
  - 若圖示為補充品品牌，需確認品牌在台灣合法進口且無爭議聲譽
- **避免**:
  - 使用台灣受眾不熟悉的西方健康品牌 logo（反而造成熟悉度支架失效）
  - 圖示過小到手機縮圖尺寸下無法辨識品牌（完全喪失熟悉度機制）
  - 圖示與標題承諾的工具數量不符（標題說「5 個工具」但縮圖只出現 3 個 logo）

---

### T-V10. Numerical Metric Hero with Reaction Face

- **One-line**: A specific, large, or surprising number (dollar amount, time saving, count) is the dominant text element, with creator face reacting to it.
- **Frameworks**: Numerical anchor (§1.9) × Specificity bias (§1.6) × Pattern interrupt (§1.7)
- **When to use**: 可量化結果類內容，例如健康指標改善數值、追蹤天數、研究樣本數；數字本身需要在沒有任何背景說明的情況下讓目標受眾感到驚訝或好奇。
- **When to avoid**: 需要背景才能理解意義的數字（如「IL-6 降低 0.3 pg/mL」在非醫療專業受眾中缺乏震撼力）；也不適合數字是負面警示時，除非搭配解法框架。
- **Frequency in corpus**: 10 / 140 (7.1%)
- **Creator distribution**: Ali Abdaal: 3, Alex Hormozi: 3, Jeff Su: 2, Cleo Abram: 2
- **Canonical example**: Ali Abdaal — "$0 / $1M (flanking face)" (ali_abdaal_029)
- **Mechanism**: Specific numbers short-circuit the viewer's credibility evaluation: the brain treats precision as a proxy for verification ('someone counted this'), making the claim feel more trustworthy than a qualitative description. Before/after number pairs ($0 → $1M; 9 hours → 2 hours) are especially effective because they compress an entire transformation narrative into two data points, firing both the Loewenstein gap ('how?') and the loss aversion mechanism ('I could be on the wrong side of this gap').
- **Sample examples**:
  - ali_abdaal_029: "$0 / $1M (flanking face)"
  - alex_hormozi_021: "$100,000 (whiteboard)"
  - ali_abdaal_024: "9 HOURS / 2 HOURS / A+ (whiteboard)"
  - jeff_su_002: "5 HOURS → 5 MIN"
  - alex_hormozi_030: "207g protein / 86g protein"
- **修修 brand fit**: A
- **Why this grade**: 健康指標的量化數字（HRV、體脂率、睡眠分期時間、肌酸濃度）在 修修 的實證品牌中是天然的 specificity bias 引爆點，且此版面不依賴頻道知名度，適合成長期使用。
- **中文化範例 (2-3 個)**:
  - 主視覺大字：「生理年齡 45 → 38」，修修 在側面呈現認真肯定表情（搭配 T-A2 或 T-A4 標題）
  - 主視覺大字：「深眠 48 分 → 89 分」，修修 呈現微驚訝表情（搭配 T-A4「我追蹤了 6 個月」標題）
  - 主視覺大字：「207g vs 86g 蛋白質」，修修 手持兩種食物對比（搭配 T-A1 或 T-A3 標題）
- **改編要點**:
  - 選用台灣健康受眾能直接理解意義的指標：體脂率、BMI、睡眠分期時間、步數、靜息心率，而非需要醫學背景的生化指標
  - Before/after 數字對（「45 → 38 生理年齡」）是最有效的格式，壓縮整個轉變敘事
  - 數字顏色建議：before（灰色或暗紅）→ after（亮綠 #4CAF50），直覺傳達改善方向
- **避免**:
  - 使用只有醫療背景觀眾才能解讀的生化數字（縮圖需在零背景下傳達意義）
  - 誇大的before/after 數字若無研究或個人數據支撐（如「20 歲 → 50 歲逆轉」），立即損害信度
  - 數字字體過小到手機縮圖下無法辨識——數字是這個版面的主角，必須佔畫面最大視覺權重

---

## 4. Joint Pairings

### JP-1. Contrarian Title + High-Saturation Command Text Thumbnail

- **Title archetype**: T-A3
- **Thumbnail archetype**: T-V8
- **Frequency**: 9 occurrences
- **Why they pair**: The contrarian title creates a cognitive dissonance ('you're doing it wrong') that the thumbnail must immediately visualise. A bold command text on a saturated background confirms the confrontational tone — the creator is willing to say something uncomfortable directly. Together they create a unified 'blunt authority' persona signal that is unambiguous at scroll speed.
- **Creator distribution**: Alex Hormozi: 5, Jeff Su: 2, Cleo Abram: 2, Ali Abdaal: 0
- **修修 recipe**:
  - Title: 「95% 的人補充益生菌的時機都是錯的——哈佛研究告訴你正確方法」
  - Thumbnail: 深藍背景（#1A2744），修修居右，認真嚴肅表情微微皺眉；左側白色粗體大字
  - Hook 大字: 「時機全錯」（3 字）
  - 表情 (7-enum): serious（嚴肅）
  - 背景: 深藍單色 #1A2744（避免 Hormozi 的高飽和暖色，選用有醫療信任感的深藍）

---

### JP-2. Story-Confession Blueprint Title + Whiteboard Diagram Thumbnail

- **Title archetype**: T-A4
- **Thumbnail archetype**: T-V5
- **Frequency**: 5 occurrences
- **Why they pair**: The first-person blueprint title ('If I were starting over, I'd do this') promises a complete, personal system. The whiteboard diagram thumbnail provides visual proof that a system exists — the density of the diagram serves as a comprehensiveness signal. The pairing resolves the viewer's implicit scepticism ('does he actually have a plan?') before they even click, making it redundant-reinforcing in the most effective sense.
- **Creator distribution**: Alex Hormozi: 4, Ali Abdaal: 1, Jeff Su: 0, Cleo Abram: 0
- **修修 recipe**:
  - Title: 「如果我想讓生理年齡倒退 10 年，我會完全按照這個計畫做【完整藍圖】」
  - Thumbnail: 修修 站在白板旁（藍白配色，學術感），指向板上「長壽 6 大支柱」結構圖，圖表在縮圖尺寸下可見框架但細節不清晰，搭配 修修 認真解說表情
  - Hook 大字: 「完整藍圖」（4 字，放置在白板右上角或人物上方）
  - 表情 (7-enum): explaining（解說）
  - 背景: 淺灰白牆面 + 藍白板，整體偏學術研討感

---

### JP-3. Duration Promise Title + Face-Left Tool-Right Tutorial Thumbnail

- **Title archetype**: T-A7
- **Thumbnail archetype**: T-V4
- **Frequency**: 10 occurrences
- **Why they pair**: The duration promise title ('Master 80% in 13 minutes') signals brevity and structure. The clean face-left + content-right thumbnail confirms both the topic (right-zone shows the tool or formula) and the accessible tone. The pairing removes two barriers simultaneously: 'Is this worth my time?' (title) and 'Is this too technical?' (thumbnail). This is Jeff Su's dominant signature pairing.
- **Creator distribution**: Jeff Su: 9, Alex Hormozi: 1, Ali Abdaal: 0, Cleo Abram: 0
- **修修 recipe**:
  - Title: 「13 分鐘學會 80% 的間歇性斷食重點（哈佛研究精華濃縮）」
  - Thumbnail: 修修居左，微笑且略帶興奮感的臉部，視線朝右；右側白色區塊顯示簡化的斷食時間軸圖（16:8 / 18:6 標示清楚），右上角標注「哈佛研究支持」小標籤
  - Hook 大字: 「80% 重點」（放右側上方，粗體深藍色）
  - 表情 (7-enum): excited（興奮）
  - 背景: 白色或極淺灰背景（Jeff Su 風格的 clean tutorial aesthetic，傳達「好學」而非「嚇人」）

---

### JP-4. Numbered Listicle Title + Excited Face with Icon Cluster Thumbnail

- **Title archetype**: T-A1
- **Thumbnail archetype**: T-V9
- **Frequency**: 6 occurrences
- **Why they pair**: The numbered title creates a completeness expectation ('I will get all N items'). The icon cluster thumbnail visually represents that plurality — multiple logos or icons mirror the multiple items promised. The excited face validates that the items are genuinely worth collecting. The pairing is complementary-gap: the title gives the count, the thumbnail teases the content without full disclosure.
- **Creator distribution**: Ali Abdaal: 4, Jeff Su: 2, Alex Hormozi: 0, Cleo Abram: 0
- **修修 recipe**:
  - Title: 「6 種哈佛研究支持的抗老食物，你家冰箱裡可能已經有了」
  - Thumbnail: 修修居中略偏右，興奮表情；周圍環繞 6 個食物圖示（藍莓、深色葉菜、核桃、薑黃、鮭魚、橄欖油），圖示帶品牌或食物識別圖像，配色鮮豔飽和
  - Hook 大字: 「6 種」（左上角大字，或覆蓋在食物圖示群右側）
  - 表情 (7-enum): excited（興奮）
  - 背景: 深色（#1A1A1A）以讓鮮豔食物圖示跳出，或白色讓食物圖示清晰辨識

---

### JP-5. Question Curiosity Gap Title + Surprised Face Question Overlay Thumbnail

- **Title archetype**: T-A6
- **Thumbnail archetype**: T-V6
- **Frequency**: 8 occurrences
- **Why they pair**: The open-ended question title creates an information gap. The surprised-face + short question overlay thumbnail does not answer the question but confirms that the creator themselves was surprised by the answer, elevating the stakes. The double-question structure (title question + thumbnail question) reinforces rather than cancels each other — two unresolved questions compound into a stronger curiosity pull than either alone.
- **Creator distribution**: Cleo Abram: 7, Alex Hormozi: 1, Ali Abdaal: 0, Jeff Su: 0
- **修修 recipe**:
  - Title: 「讓頂尖醫師感到困擾的長壽問題，到底有沒有答案？（專訪台大醫學研究員）」
  - Thumbnail: 修修 臉部特寫或半身，展現真誠驚訝表情（眉毛上揚、嘴輕微張開）；短覆字問句置於臉部上方或旁邊，高飽和藍色或綠色背景
  - Hook 大字: 「真的嗎？」（3 字，置頂，白色粗體）
  - 表情 (7-enum): surprised（驚訝）
  - 背景: 深藍 #1A2C4A（帶科學感的深藍，而非 Cleo 的高飽和橙色，維持健康頻道的信度調性）

---

### JP-6. How-To Specificity Title + Split-Screen Before/After Thumbnail

- **Title archetype**: T-A2
- **Thumbnail archetype**: T-V3
- **Frequency**: 6 occurrences
- **Why they pair**: The how-to title promises transformation. The before/after split-screen thumbnail visually proves the transformation is achievable by showing the two states. The pairing creates a 'thumb-amplifies-title' relationship: the title tells you what you will learn, the thumbnail shows you what success looks like, creating a complete before-click value proposition.
- **Creator distribution**: Ali Abdaal: 4, Alex Hormozi: 1, Jeff Su: 1, Cleo Abram: 0
- **修修 recipe**:
  - Title: 「如何在 12 週內，把你的睡眠深眠時間從 45 分鐘增加到 90 分鐘（科學實證方法）」
  - Thumbnail: 左半邊冷灰色調顯示「深眠 45 分」的睡眠追蹤 App 截圖（暗色）；右半邊暖綠色調顯示「深眠 89 分」截圖（亮色）；修修 小頭像在中央分隔線上方，帶認可表情
  - Hook 大字: 左標：「45 分」 / 右標：「89 分」（各自在對應半邊的中央，白色粗體）
  - 表情 (7-enum): explaining（解說，帶輕微點頭認可感）
  - 背景: 左右分割，左側 #2B3A4A（藍灰冷調）/ 右側 #1A4731（深綠暖調）

---

### JP-7. Authority Research Title + Tight Face Crop (Surprised) Thumbnail

- **Title archetype**: T-A8
- **Thumbnail archetype**: T-V2
- **Frequency**: 5 occurrences
- **Why they pair**: The authority research title ('I Read 107 Books') signals effortful synthesis. The surprised tight-crop face thumbnail communicates that even the expert who did the research was astonished by what they found — validating that the content is genuinely revelatory rather than predictable. The surprise face adds the emotional hook that pure authority claims lack.
- **Creator distribution**: Ali Abdaal: 3, Alex Hormozi: 1, Cleo Abram: 1, Jeff Su: 0
- **修修 recipe**:
  - Title: 「我讀了 50 篇長壽研究論文，這些方法才是真正有效的（其餘的都是迷思）」
  - Thumbnail: 修修 臉部特寫（下巴至額頭），展現真誠驚訝表情，眼神帶著「我不敢相信研究結果」的誠實感；底部覆字
  - Hook 大字: 「50 篇論文」（底部白色覆字，4 字）
  - 表情 (7-enum): surprised（驚訝）
  - 背景: 深藍黑漸層 #0D1B2A → #1A2C4A（帶學術感的夜深工作氛圍，呼應「我讀了大量文獻」的努力感）

---

### JP-8. Exclusive Secret Title + Confrontational Overlay Thumbnail (v1.1: cautionary use only)

> **v1.1 panel warning** (Gemini audit §4): This pairing is a **brand-suicide risk** for an evidence-based health channel. Confrontational tone applied to established medical metrics (e.g. "BMI 是錯的") creates an adversarial relationship with mainstream medical consensus, which conflicts with 修修's positioning. Use this pairing ONLY when the contrarian claim is backed by published research the channel can cite, AND the language is reframed from confrontation ("X is wrong") to collective re-examination ("we may have misunderstood X"). Frequency in corpus: 4 — already near the ≥3 threshold floor; consider treating as creator-specific (Hormozi) signature rather than generalisable archetype.


- **Title archetype**: T-A5
- **Thumbnail archetype**: T-V8
- **Frequency**: 4 occurrences
- **Why they pair**: The exclusive secret title implies the mainstream narrative is incomplete. The confrontational overlay thumbnail escalates the claim from 'incomplete' to 'actively deceptive'. Together they create maximum epistemic threat — triggering both the information gap (what's the real truth?) and loss aversion (I may have been acting on false beliefs). High-risk pairing but high-click when the content delivers genuine revelation.
- **Creator distribution**: Cleo Abram: 3, Alex Hormozi: 1, Ali Abdaal: 0, Jeff Su: 0
- **修修 recipe**:
  - Title: 「你真正需要在意的健康數字，不是你以為的那個（哈佛 20 年追蹤研究顛覆了 BMI 迷思）」
  - Thumbnail: 深藍或深灰背景，修修居右帶嚴肅但非憤怒的表情；左側白色粗體大字傳達反直覺事實（以陳述句代替命令句，保持品牌調性）
  - Hook 大字: 「BMI 是錯的」（5 字，白色）
  - 表情 (7-enum): serious（嚴肅）
  - 背景: 深灰 #2B2B2B（中性深色，不使用 Cleo 的高飽和色，維持修修的證據驅動信度感）

---

## 5. 修修 Brand Adaptation Layer

### 5.1 Brand voice principles (constraints on archetype use)

修修's brand operates on these constraints. Every archetype adaptation in this playbook respects them:

1. **Evidence-based authority** — claims must reference research, doctor names, or measurable outcomes. Avoid "trust me bro" frames.
2. **Warm-but-direct tone** — neither Hormozi-aggressive nor Bryan-Johnson-clinical-cold. Closer to Attia / Ali Abdaal calm-confident.
3. **No fear-mongering** — loss-aversion frames must be factual ("研究發現 X 增加 Y 風險") not catastrophic ("你正在毀掉你的健康").
4. **No oversimplification** — "Easy" in zh-Hant health content carries credibility risk. Prefer "簡單 / 微習慣 / 5 分鐘" over "輕鬆 / 無痛 / 不用努力".
5. **Specificity over hype** — "5g 肌酸 × 6 週" beats "超強補充品".
6. **Familiar authority anchors** — 台大醫師 / 哈佛研究 / Attia / Huberman / Bryan Johnson (對長壽受眾) > 不具名「專家」.

### 5.2 Archetype × Brand-Fit Matrix

| ID | Archetype | Grade | One-line rationale | 中文化模板 |
|---|---|---|---|---|
| T-A1 | Numbered Listicle Promise | A | 健康 listicle 直接移植，只需將輕鬆語氣換為實證標記 | 「N 個{研究證實的}{健康話題}習慣，讓你{可量化結果}」 |
| T-A2 | How-To with Specificity Anchor | A | 語料庫最高頻架構，How-to + 具體數字與修修實證語氣天然契合 | 「如何在{時間}內{具體健康改變}（{來源：哈佛研究/科學實證}）」 |
| T-A3 | Contrarian Reversal | B | 點擊力強但需搭配命名研究來源才不落入 fear-mongering | 「{N%}的人都做錯了{健康行為}——{命名機構}研究告訴你正確方法」 |
| T-A4 | Story-Confession / Personal Blueprint | B | 可移植但效果依賴寄生社交基礎；成長期頻道需搭配外部權威補強 | 「如果我想{具體健康目標}，我會完全按照這個計畫做【完整藍圖】」 |
| T-A5 | Exclusive Secret / Hidden Truth | B | 「真相」框架需立即搭配來源錨點，否則滑向陰謀論語氣 | 「你真正需要在意的{健康指標}（{命名機構} {N} 年追蹤研究揭示）」 |
| T-A6 | Question Curiosity Gap | A | Wonder-curiosity 問題在台灣科普健康受眾中高度適配，Cleo 架構直接可移植 | 「如果你{極端健康行為或情境}，你的身體會發生什麼事？」 |
| T-A7 | Duration / Time-Compression Promise | B | 公式可移植但需謹慎選題，不適用複雜醫學議題 | 「{N} 分鐘掌握 80% 的{可操作健康方法}（{來源}精華）」 |
| T-A8 | Authority-Research Credibility Lead | S | 修修品牌核心五大支柱之一，可直接套用，但仍需逐次驗證該主張是否真實有研究支持 | 「我讀了{N}篇{研究領域}論文，這些才是真正有效的｜或｜{命名專家}解密{健康話題}」 |
| T-A9 | Year-Anchor Currency Signal | B | 健康研究年份更新的應用比科技領域更受限，只在研究有重大更新時使用 | 「{年份}最新研究：這{N}個{健康習慣}你需要重新評估」 |
| T-A10 | Cost-Risk-Reframe / Loss Aversion Lead | B | Loss framing 符合 factual 框架但必須搭配括號解法承諾防止 fear-mongering | 「你正在浪費{具體比例}的{健康資源}——{命名機構}發現這個原因（解法在這裡）」 |
| T-V1 | Face-Right Text-Left Dual-Zone | B | 版面可移植，文字從命令句改為數字或事實陳述；表情調整為誠懇而非對抗 | 左側：「{數字 + 名詞}」（3-5字） / 右側：修修認真解說表情 |
| T-V2 | Face-Center Tight Crop with Text Overlay | B | 效果依賴知名度；成長期建議搭配資訊性覆字補充信度 | 臉部特寫（驚訝或嚴肅）+ 底部覆字：「{反直覺研究結果}」（2-4字） |
| T-V3 | Split-Screen Comparison | A | Before/after 對比在健康領域文化普遍，零解釋成本，適合轉變型和比較型內容 | 左：冷調「干預前{指標值}」/ 右：暖調「干預後{指標值}」/ 修修小頭像居中 |
| T-V4 | Face-Left Text-Right Exposition Layout | A | 教學解說版面與修修品牌天然契合，右側可放研究數據或劑量表 | 修修居左指向右側 / 右側：「{數字或簡化機制圖}」（3-4元素上限） |
| T-V5 | Whiteboard / Diagram Reveal with Creator | B | 系統藍圖內容適用，但圖表密度必須誠實反映內容複雜度；設計語言需學術化 | 修修站於藍白學術風白板旁，指向「{系統名稱}」結構圖（縮圖尺寸下框架可見但細節模糊） |
| T-V6 | Surprised / Excited Face with Question Overlay | **D / Avoid** | 問號框架在健康突破型內容可行，但醫療突破暗示風險高，需謹慎搭配實證錨點 | 修修真誠驚訝表情 + 深藍背景 + 覆字：「{研究結論}？」（3-4字） |
| T-V7 | Object / Tool Hero with Creator Reaction | A | 健康工具（補充品、穿戴裝置）有直接的熟悉度支架效果，不依賴頻道知名度 | 主角物品佔40-60%畫面（知名健康工具/補充品）+ 修修側面反應表情 + 覆字：「{核心數字}」 |
| T-V8 | High-Saturation Colour Pop with Bold Command Text | C | 機制有效但高飽和暖色 + 命令語氣與修修溫和權威品牌衝突；僅限強反直覺主張 | 深色系背景（#1A2744 或 #2B2B2B）+ 白色粗體：「{事實陳述或數字}」（3-4字） |
| T-V9 | App Icon / Tool Logo Cluster with Creator Face | B | 可移植但需確認圖示在台灣健康受眾中的真實辨識度；圖示選擇需在地化 | 修修居中，周圍4-6個台灣受眾熟悉的健康App/品牌圖示，覆字：「{哪個才真的有效}？」 |
| T-V10 | Numerical Metric Hero with Reaction Face | A | 健康指標量化數字是修修 specificity bias 的天然引爆點，適合成長期頻道且不依賴知名度 | 主視覺大字：「{before數字}→{after數字}」（對比格式）+ 修修驚訝或認可表情 |

---

### 5.3 Hormozi adaptation (special case)

Hormozi's content is the LEAST brand-aligned for 修修 — aggressive, masculine-coded, business-domain. **However** several of his structural patterns ARE portable IF the voice is softened:

- ❌ "13 Years Of BRUTALLY HONEST Business Advice" → ✓ "13 年的長壽研究讓我學到 3 件事（沒人想說的版本）"
- ❌ "Brutally Honest Advice to My Younger Poorer Self" → ✓ "如果可以回到 30 歲，我會跟自己說的 3 件健康事"
- ❌ "How To Actually Get Rich In Your 20s" → ✓ "30 歲後想要老得慢一點，該真正在意的 3 件事"

Pattern: **steal the structure, swap aggression → vulnerability, swap business → health/longevity**.

### 5.4 Bilingual considerations (zh-Hant specific)

| Issue | English convention | zh-Hant adaptation |
|---|---|---|
| Listicle quantifier | "5 Ways" | 「5 個 / 5 種 / 5 招」 — 數字後必須有量詞 |
| Capitalisation emphasis | "BRUTALLY HONEST" | 中文無大小寫，用標點 / 引號 / 對比結構 |
| Parenthetical credibility | "(Evidence-Based)" | 「（研究實證）」「（最新研究）」「（XX 醫師審訂）」 |
| Title length | 60-80 char in English | **20-32 字繁中** — 行動裝置 feed truncate 早 |
| Power words | "ACTUALLY", "REALLY", "INCREDIBLE" | 「真正 / 其實 / 你以為的」(避免「超 / 神 / 爆」過 hype) |
| Punctuation | colon : | 全形冒號「：」更穩；逗號「，」優先全形 |
| Numbers | 100K subs, $100M | 阿拉伯數字 + 中文單位「10 萬訂閱」「破億營收」(避免直接美元) |
| Modal particles | (absent in English titles) | 「喔 / 啊 / 啦 / 耶 / 嘛」 可大幅軟化命令式語氣 — 例：「5 個睡眠殺手」→「原來這 5 件事才是睡眠殺手喔！」(Gemini panel §1b) |
| Lenticular brackets | (n/a) | 【】(全形 lenticular brackets) 是 Taiwan YT 主流 framing convention — 「【完整版】/【新手必看】/【醫師審訂】」放在標題尾或中段標記 promise (Gemini panel §1b) |
| TW vs HK terminology | (single English term) | 健康領域用詞兩岸三地有差，例：優格(TW) vs 乳酪(HK)；高麗菜(TW) vs 椰菜(HK)；單車(HK) vs 腳踏車(TW)。需建健康詞彙 glossary 並依目標受眾選擇 (Gemini panel §1b) |
| Simplified char contamination | (n/a, alphabet-based) | 中文輸入法或剪貼板易混入簡體字（台→台/臺、里→里/裡、发→發/髮）— 損害品牌信度，必須加入發布前 QA step (Gemini panel §1b) |
| Numerical unit cultural connotation | "10K / 100M" | 用「10K / 100K」signal Western tech/finance 影響，可能疏離傳統受眾；「10 萬 / 十萬」signal 在地正式調性。視 segment 選擇 (Gemini panel §1b) |

---

## 6. Brainstorm prompt integration spec

### 6.1 Lazy-load strategy

The brainstorm prompt does NOT include the full playbook (would be 8-12K tokens). Instead:

```
[brainstorm prompt header]
  - existing 5-line idea format
  - existing diversity contract
  - existing emotion enum

[archetype index — generated from playbook_data_v1.json]
  - 8-12 title archetype IDs + one-line summaries (≈400 tokens)
  - 8-12 thumb archetype IDs + one-line summaries (≈400 tokens)
  - common joint-pairing IDs (≈200 tokens)

[brand-fit lookup table]
  - archetype_id → grade (≈100 tokens)
```

Total addition: ~1.1K tokens to existing brainstorm prompt.

### 6.2 Output schema modification

Each generated idea is tagged with archetype IDs:

```
Idea 1
[archetype: T-A1, T-V3, JP-2]
大字: ...
我的表情: ...
視覺: ...
數字/圖示: ...
背景: ...
```

This enables:
- Downstream evaluation: did the picked archetype actually fit the content?
- Diversity audit: did the 3 ideas explore ≥2 different archetypes?
- Long-term: track which archetypes 修修 actually picks → revealed preferences

### 6.3 Integration code (Phase 5 deliverable)

`thousand_sunny/routers/bridge_project_thumbnails.py` `_brainstorm_user_message` extended to:

1. Load `playbook_data_v1.json` archetype index
2. Inject as a single text block in the user message
3. Parse archetype tags from LLM output (add to `shared/thumbnail_idea.py` parser)
4. Persist archetype IDs in `data/projects/{slug}/thumbnail_brainstorm_meta.json` for revealed-preference logging

---

## 7. Methodology Caveats

### MC-1. English-language YouTube conventions may not translate to zh-Hant audiences (from catalog)

The entire sample of 140 thumbnails is from English-language creators optimising for Western YouTube audiences. Conventions like profanity-as-authenticity (Hormozi), celebrity name-drops as authority (Cleo Abram × Sam Altman), and the 'broke to rich' identity arc are culturally embedded in North American aspiration narratives. Traditional Chinese health audiences on YouTube.com/tw or YouTube.com/hk may respond differently to: aggressive command text (could read as impolite rather than direct), first-person confession framings (may be perceived as boastful rather than intimate), and statistical social-proof anchors ('99% of people') that assume a competitive individualist worldview. All archetypes should be pilot-tested with zh-Hant thumbnails before treating them as validated patterns.

**Implication for 修修**: 所有從 Hormozi 語料庫萃取的命令式語氣或誇耀性第一人稱框架，在轉換為繁中時必須重新測試 CTR，不可假設英文版高點擊的架構在台港受眾中等效；尤其需要評估「99% 的人做錯了」此類競爭性社會比較表述是否適合台灣健康受眾的集體主義文化語境。

---

### MC-2. Click-driver framework attributions are post-hoc rationalisations, not causal validations (from catalog)

The click_drivers assigned to each archetype (e.g., 'Loewenstein information gap fires because…') are derived from pattern-matching the title/thumbnail structure to established psychological frameworks, not from click-through rate data, eye-tracking studies, or A/B tests. We do not have actual CTR data for any of the 140 videos. A thumbnail coded as 'Pattern interrupt' might be high-CTR because of creator fame, algorithm promotion, topic virality, or posting time — none of which are captured in structural analysis. All mechanism_writeup explanations should be treated as falsifiable hypotheses, not confirmed causal mechanisms.

**Implication for 修修**: 本手冊中的所有機制解釋應作為「有根據的假設」而非「已驗證的因果關係」；修修 應建立自己的 A/B 測試機制（每月至少 2-3 組標題或縮圖對照測試），用真實 CTR 數據驗證或推翻這些框架在健康長壽頻道的實際效力。

---

### MC-3. Several archetypes likely conflate two distinct patterns and should be split in v2 (from catalog)

T-A4 (Story-Confession / Personal Blueprint) conflates at least two different structures: (a) retrospective regret frames ('What I wish I knew', 'Things I'd tell my younger self') and (b) prospective hypothetical frames ('If I were starting today, I'd do this'). These may perform differently because retrospective frames activate empathy while prospective frames activate modelling/envy. Similarly, T-V1 (Face-Right Text-Left) includes both short command text (2-3 words, Hormozi style) and longer conversational text (Jeff Su's 'Copy these settings'). These may function as different archetypes at the psychological level despite sharing a layout structure. A v2 analysis with CTR data should attempt to split both.

**Implication for 修修**: 在使用 T-A4 時，修修 應分別測試「回顧告白版」（「9 件我希望 30 歲前就知道的事」）和「假設藍圖版」（「如果我想逆轉生理年齡，我會這樣做」）兩種子型態——它們啟動的心理機制不同，對不同觀眾狀態（已有行動意願 vs. 尚在探索）的效力也可能大相徑庭，切勿混為一談。

**v1.1 panel update** (Codex audit §2): MC-3 undercounted the conflation problem. Additional archetypes flagged for v2 split:
- **T-A2 How-To** conflates: (a) procedural how-to (steps), (b) personal workflow story ("How I Manage My Time"), (c) explainer ("AI Agents, Clearly Explained") — these create different click expectations.
- **T-A3 Contrarian Reversal** conflates: (a) accusation frames ("you're doing it wrong"), (b) scientific reframe ("Dinosaurs Were Weirder Than We Thought"), (c) social-comparison ("99% of People…") — trust-implication differs sharply.
- **T-A5 Exclusive Secret** overlaps T-A3 — "The Real Reason…" (deep explanation, trust-building) vs "what they don't tell you" (suspicion/conspiracy-adjacent, trust-risky) need separation in health context.
- **T-A8 Authority-Research** is too broad — named-expert-guest / named-institution / quantified-credential / "I-read-N-books" are different trust mechanisms (external vs self-authority).
- **T-V1 / T-V4** are production orientations of the same dual-zone face+payload layout — v2 should merge unless CTR data proves left/right matters.

---

### MC-4. Cleo Abram's double-question thumbnail pattern may be over-attributed as a transferable archetype (from catalog)

The 'Surprised Face + Short Question Overlay' thumbnail pattern (T-V6) is almost exclusively a Cleo Abram pattern (7 of 8 examples). It is included in the thumbnail archetypes because the mechanism is theoretically sound and appears in multiple creators' outputs, but the frequency threshold is marginal (8 examples, ≥2 creators). This pattern is also deeply tied to Cleo's science-explainer brand identity — it works because her audience expects wonder-curiosity content and trusts her to resolve the question. For 修修, a health channel, the 'THEY SOLVED IT?' overlay may carry connotations of medical breakthrough hype that could undermine evidence-based credibility if not carefully grounded.

**Implication for 修修**: T-V6 在 修修 頻道中評為 C 級，每季使用上限建議不超過 2 次，且必須搭配真正有突破性發現支撐的影片內容；「解開了嗎？」「找到了？」等問句若在影片中的答案是「部分有效」或「尚需更多研究」，必須在影片開頭誠實告知，以維護 修修 的實證信度品牌。

---

### MC-5. Duration/Time-compression promise archetype (T-A7) is 75% Jeff Su and may reflect his specific audience, not a universal pattern (from catalog)

9 of 12 duration-promise examples are from Jeff Su, whose audience consists primarily of productivity-focused professionals seeking efficient AI-tool onboarding. The '80% in N minutes' formula is specifically optimised for an audience with high tool-anxiety and time scarcity around learning new software. It is classified as a universal archetype (≥2 creators, ≥10 examples) because Alex Hormozi uses a compressed-wisdom variant ('13 years in 90 minutes'), but the mechanisms differ significantly: Jeff Su's version targets learning efficiency while Hormozi's targets authority compression. For 修修, the health adaptation ('Master 80% of [longevity diet] in 13 minutes') may feel reductive for complex health topics and should be used selectively for genuinely learnable, actionable content rather than nuanced medical discussions.

**Implication for 修修**: T-A7 僅適合 修修 頻道中「工具性、步驟明確、觀眾可即時操作」的內容（如「間歇性斷食的起始步驟」「如何讀懂你的睡眠報告」），絕不應套用在複雜醫學決策類內容（如「你該不該補充 NMN？」）——後者的複雜度與「80% 搞定」的承諾之間的落差會嚴重損害完成率和評論區口碑。

---

### MC-6. Sample selection bias — all four creators are already high-CTR outliers, creating a survivorship problem (from catalog)

All 140 thumbnails were selected from high-performing creators, meaning the dataset contains only observed successes. We cannot observe which title/thumbnail structures these creators tried and abandoned, which formats their audiences rejected, or whether the same structures would perform equivalently on a smaller channel without an established subscriber base. Brand recognition (Alex Hormozi's face alone drives clicks from existing fans) inflates the apparent effectiveness of creator-authority archetypes. For 修修 as a growing channel, archetypes that rely on pre-established parasocial trust (Story-Confession, personal blueprint) may underperform until the creator's face is recognisable. Archetypes that are topic-first (Numbered Listicle, Question Curiosity Gap, Duration Promise) may be more effective in the early growth phase.

**Implication for 修修**: 頻道訂閱者數未達 5 萬前，優先使用「主題驅動」而非「人格驅動」的架構——T-A1（數字清單）、T-A6（問題好奇心缺口）、T-A8（權威研究，但以命名外部機構而非 修修 本人為主體）以及 T-V7（物品主角 + 反應表情）在知名度不足時的 CTR 下滑風險最低；T-A4（個人藍圖告白）和純臉部特寫的 T-V2 建議在頻道建立足夠的寄生社交基礎後才增加使用頻率。

## 8. Versioning

- **v1** (2026-05-26): 140 corpus, 4 creators. This file.
- **v1.1** (2026-05-27): 3-way panel integration applied. Fixes: I-1 (numerical/freq recompute flag — see panel matrix), I-2 (§1.X anchor citation corrections, 29 fixes), I-3 ("NOT ad-hoc" softened), I-4 (T-A9/T-A10 flagged as modifiers), I-5 (MC-3 conflation expansion), I-7 (T-V6 grade C → D + regulatory caveat), I-8 (zh-Hant rewrite-required banner on §2), I-9 (§5.4 +5 rows: modal particles / lenticular brackets / TW-HK terminology / simplified-char QA / numerical-unit connotation), I-10 (§1.4 collectivist supplement), I-12 (causal-language softening sweep), I-13 (T-V5 mechanism rewrite: Proof of Work / Complexity Signaling), I-14 (JP-8 reframed as cautionary). Panel artifacts: `docs/research/2026-05-27-codex-thumbnail-playbook-audit.md`, `docs/research/2026-05-27-gemini-thumbnail-playbook-audit.md`, `docs/research/2026-05-27-playbook-3way-panel-integration.md`.
- **v2 backlog** (deferred): I-6 (T-V1/T-V4 merge), I-15 (Anti-Playbook of low-CTR failures), I-16 (Channel-level Portfolio Strategy in §6.4), I-17 (Dynamic grade feedback loop in §8), I-18 (frequency threshold tighten to ≥5 / ≥3). All require 修修 collaboration (CTR data + real publish results).
- **I-11 VETOED by 修修 (2026-05-27)**: Gemini's audit §3 + §6-item-4 proposed adding a 20-30-thumbnail baseline corpus from Taiwan/HK health creators to fix "Magazine Cover aesthetic" / "Calm Expert persona" / cultural-fit gaps. **修修 explicitly declined** — positioning is to **adopt Western YouTube visual language** (Ali Abdaal / Hormozi / Cleo / Jeff Su) **delivered in zh-Hant** as differentiation strategy; looking foreign-in-中文-wellness-feed is the intended pattern interrupt, not a defect. **Do not propose Chinese-creator reference corpora in future v2/v3 work.** (Linguistic guidance in §5.4 modal-particles/【】brackets/TW-HK terminology and cultural-framework supplement in §1.4 collectivist hooks remain — they handle zh-Hant LANGUAGE rendering, not Chinese-creator REFERENCE material.)
- **v2** (future): expand to 修修-published thumbnails ground-truth (修修's own past+future publishes only) + add Podcast track.
- **v3** (future): brand-adaptation grades upgraded from heuristic to revealed-preference data.

When adding a new corpus row:
1. Add new image to `E:/Thumbnail-example/{creator}/` (or future vault path).
2. `python -m scripts.extract_thumbnail_features --creator "New Creator"`
3. `python -m scripts.cluster_thumbnail_patterns`
4. Manually update §2/§3/§4/§5 sections with the new clustering output.

Cluster script is idempotent on row IDs; extraction script skips already-extracted IDs.

---

## 9. Cross-references

- ADR-033 D4 — Reference library design (this playbook is the realisation of D4 + D4.a)
- `prompts/thumbnail/brainstorm_youtube_v1.md` — System prompt this playbook feeds
- `prompts/thumbnail/brainstorm_titles_v1.md` — Title-specific brainstorm system prompt
- `prompts/thumbnail/emotions.yml` — 7-emotion enum (face emotion contagion section)
- `docs/research/2026-05-26-thumbnail-playbook-design.md` — Design decisions + method choices
- `data/thumbnail_reference_extraction_v1.json` — Raw 140-row corpus data
- `prompts/thumbnail/playbook_data_v1.json` — Machine-readable archetype catalog
