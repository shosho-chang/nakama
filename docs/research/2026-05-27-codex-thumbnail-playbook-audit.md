## 1 — NUMERICAL / FREQUENCY GROUNDING

The corpus-level base count is correct: `playbook_data_v1.json` reports 140 rows, 4 creators, and 35 each for Ali Abdaal, Alex Hormozi, Cleo Abram, and Jeff Su. Several headline title counts also check out against raw `title_analysis.structure_primary`: §2 T-A2 says “33 / 140 (23.6%)” with Ali 17, Hormozi 9, Jeff 5, Cleo 2, and that sums correctly. §2 T-A1 says “11 / 140 (7.9%)” with Ali 6 and Jeff 5, also correct. §2 T-A3 says “22 / 140 (15.7%)” with Jeff 8, Cleo 8, Hormozi 4, Ali 2, also correct.

But the numerical layer is not clean enough to load into prompts as trusted metadata. §2 T-A10 says “6 / 140 (4.3%)” and creator distribution “Alex Hormozi: 2, Jeff Su: 2, Ali Abdaal: 2.” The catalog’s own `stats.structure_primary_distribution` says `cost-risk-reframe: 2`, not 6. Raw data confirms only two primary rows: `alex_hormozi_023` and `alex_hormozi_035`. T-A10 is silently mixing primary archetype rows with secondary loss-framing rows from T-A2, T-A3, and T-A6. That can be legitimate, but only if it is labeled as a cross-cutting modifier, not a primary title archetype. Worse, the T-A10 catalog lists frequency 6 but only 5 examples. Fix this.

Thumbnail counts have more serious drift. §3 T-V1 says “19 / 140 (13.6%)” but its creator distribution sums to 22: Alex 9 + Jeff 8 + Cleo 3 + Ali 2. Raw exact `face-right-text-left` rows are 19, but the raw creator split is Alex 6, Ali 2, Jeff 11, Cleo 0. §3 T-V2 says 20, but its creator distribution sums to 15. Raw exact `face-center-tight-crop` is 20 with Alex 8, Ali 4, Cleo 7, Jeff 1, not the section’s Alex 6, Cleo 5, Ali 3, Jeff 1. §3 T-V3 says 12, but creator distribution sums to 11.

The `universal_patterns` need correction. “Loewenstein in 133/140 titles = 95%” is numerically correct, but its evidence line says all four creators are 35/35. Raw counts are Alex 31/35, Ali 35/35, Cleo 34/35, Jeff 33/35. UP-3 is worse: it says Pattern Interrupt and Face Emotion Contagion “co-occur in 121+ thumbnails.” Raw counts are Pattern Interrupt 126, Face Emotion Contagion 121, but co-occurrence is 108, not 121+.

There is also title drift between raw rows and prose examples. §2 T-A1 cites `ali_abdaal_005` as “8 Lazy Habits…”; raw says “8 Simple Habits…”. §2 T-A3 cites `jeff_su_003` with “(Do This Instead)”; raw says “95% of People STILL Prompt ChatGPT-5 Wrong.” §2 T-A4 includes `ali_abdaal_027` under Story-Confession, but raw primary classification is `authority-research`. These are not fatal, but they prove the playbook is partly composed from paraphrase rather than exact catalog truth.

## 2 — ARCHETYPE COHERENCE & SPLITS

MC-3 correctly flags §2 T-A4 and §3 T-V1 as conflations. I agree, but it undercounts the problem.

§2 T-A2 “How-To with Specificity Anchor” conflates at least three behaviors: true procedural how-to, personal workflow story (“How I Manage My Time”), and explainer content (“AI Agents, Clearly Explained”). These do not create the same click expectation. A health viewer clicking “如何改善睡眠” expects steps; clicking “睡眠分期，科學解釋” expects understanding. Split procedural how-to from explainer.

§2 T-A3 “Contrarian Reversal” conflates accusation frames (“you’re doing it wrong”), scientific reframe frames (“Dinosaurs Were Weirder Than We Thought”), and social-comparison frames (“99% of People…”). These should not be one bucket for 修修. “研究推翻常識” can fit evidence-based health. “95% 的人都錯” is a trust-risky accusation frame.

§2 T-A5 “Exclusive Secret / Hidden Truth” overlaps heavily with T-A3. “The Real Reason…” is a deep-explanation frame; “what they don’t tell you” is a suspicion/conspiracy-adjacent frame. In health, those must be split because one builds trust and the other can damage it.

§2 T-A8 “Authority-Research Credibility Lead” is too broad. Named expert guest, named institution, quantified credential, and “I read 107 books” are different trust mechanisms. For 修修, “台大醫師解釋” and “我讀了 50 篇論文” should be separate subtypes. The first borrows external authority; the second depends on 修修’s research credibility.

§2 T-A9 “Year-Anchor Currency Signal” is not a standalone archetype. It is a modifier tag that attaches to how-to, research, tool update, or yearly planning. Same for §2 T-A10: loss framing is a modifier, not a clean primary archetype.

For thumbnails, merge or reframe §3 T-V1 and §3 T-V4. “Face-right text-left” and “Face-left text-right” are production orientations inside the same dual-zone face + payload layout. Keep orientation as a field, not two archetypes, unless CTR data proves left/right direction changes performance.

§3 T-V2 and §3 T-V6 also overlap. “Surprised face with question overlay” is an emotional/overlay variant of tight face crop, not a separate layout archetype. §3 T-V5 Whiteboard/Diagram is a subtype of §3 T-V4 exposition layout. §3 T-V7 and §3 T-V9 are both familiarity-scaffold object/logo systems; split them only if physical product, app UI, and icon cluster are separately measured.

## 3 — FRAMEWORK ATTRIBUTION RIGOR

The framework references have hard section-number errors. §2 T-A3 says “Pattern interrupt (§1.5),” but §1.5 is Loss Aversion; Pattern Interrupt is §1.7. §2 T-A5 says “Cialdini authority (§1.2)” and “Insider knowledge frame (§1.8),” but §1.2 is MrBeast PVP and §1.8 is Face Emotion Contagion. §3 T-V1 says Face Emotion Contagion is §1.7, Pattern Interrupt is §1.5, and Cognitive Ease is §1.6; all three are wrong. §3 T-V6 cites MrBeast PVP as §1.10, but PVP is §1.2. This must be fixed before prompt integration.

On attribution quality, MC-2 is too gentle. §2 T-A1 claims numbered listicles use “Cialdini commitment-consistency” and that the number “pre-closes the Loewenstein information gap.” Commitment-consistency is not earned here; the viewer has not made a prior commitment. “Pre-closes” is also conceptually muddled: the list opens a bounded gap, it does not close it.

§2 T-A2 over-attributes ordinary utility search to Loewenstein and Cialdini. Many how-to titles are not curiosity gaps; they are task-intent matches. The mechanism is “I have a problem and this promises a solution,” not necessarily deprivation-state curiosity.

§2 T-A8 says precise numbers convert self-reported effort into “what feels like audited data.” That is dangerous language for health. Precision can be fabricated. A behavioral economist would call this a credibility cue, not evidence.

§3 T-V5 says an unreadable whiteboard is “more motivating than a readable diagram.” That is pure post-hoc storytelling. In health, unreadable density can signal sophistication, but it can also signal clutter or pseudo-science. Treat it as a hypothesis.

§3 T-V10 says specific numbers “short-circuit the viewer’s credibility evaluation.” Do not teach 修修 to rely on that. Health audiences often scrutinize numbers more, especially “生理年齡 45 → 38” style claims.

## 4 — 修修-ADAPTATION REALISM

§5.2’s single S grade for §2 T-A8 is directionally right but too broad. Make it S only for verifiable external authority or real literature synthesis. “我讀了 50 篇長壽研究論文” is acceptable only if true and shown. “我追蹤了 3,847 位學員的健康數據” is not acceptable unless 修修 actually has that dataset. At sub-50K, inflated self-authority will read as fake.

Several A grades are over-optimistic. §2 T-A2 should be A only for practical behavior-change topics; otherwise B. The Chinese example “如何控制多巴胺，在腦神經科學研究出現之前你可能已經損傷了它” reads like machine translation and violates §5.1 “No fear-mongering.” §2 T-A6 should be B, not A, for health. “如果你一個月不睡覺，你的身體會發生什麼事？” is clickable, but it is a spectacle premise, not a routine longevity content engine.

Some C grades are fair. §3 T-V6 should stay C or even be treated as a rare-use pattern because “真的找到了？” and “成功了嗎？” can imply medical breakthrough hype. §3 T-V8 C is also correct; Hormozi-style command color blocks are culturally and medically risky.

The zh-Hant examples need native rewrite. “5 個研究證實的習慣，讓你的生理年齡年輕 10 歲” overclaims causality. Better: “5 個和生理年齡較低有關的習慣.” “8 個上班族也能做到的微習慣，每週幫你恢復 20 小時精力” is not a measurable health claim. “你正在浪費 80% 的睡眠恢復力” invents a metric. §5.4 says zh-Hant titles should be 20-32 characters, but the playbook violates its own rule: “如果我想讓生理年齡倒退 10 年，我會完全按照這個計畫做【完整藍圖】” is 34 characters, and the JP-8 BMI title is 44.

## 5 — METHODOLOGY GAPS

The biggest missing gap is that “high-CTR” is asserted, not auditable. There is no actual CTR, impressions, browse/search split, retention, topic velocity, upload date, or thumbnail/title revision history. Without negatives from the same creators, the corpus cannot distinguish effective structures from structures these famous creators can get away with.

The sample misses the actual target niche. MC-1 notes English-language bias, but v1 still has no Peter Attia, Huberman, Bryan Johnson, Saladino, Rhonda Patrick, or equivalent longevity/health creators. It also has no zh-Hant health baseline. A Taiwan/Hong Kong wellness feed has different typography, authority markers, medical caution norms, and title truncation behavior.

There is no inter-rater reliability. One LLM extraction pass created the raw labels, and the title drift shows insufficient QA. Before v2, run a second independent coding pass on at least 30 rows and adjudicate disagreements.

The frequency threshold from the design doc, “≥3 examples + ≥2 creators,” is too weak. §3 T-V6 is 7/8 Cleo. §4 JP-3 is 9/10 Jeff. §4 JP-5 is 7/8 Cleo. Those are creator signatures, not robust universals.

The playbook also lacks health-claims review. Examples involving “生理年齡倒退,” supplements, probiotics, sleep damage, BMI, and “治癒/成功了嗎” need evidence and regulatory screening before entering brainstorm prompts.

## 6 — FINAL VERDICT

Approve with modifications. Do not approve as-is, and do not load the current JSON into brainstorm prompts as trusted metadata.

Priority changes:

1. Recompute and freeze counts from raw data. Fix T-A10, T-V1/T-V2/T-V3 creator distributions, UP-1, UP-2, UP-3, and title/example drift. Label primary archetypes separately from secondary modifiers.

2. Refactor the taxonomy. Split T-A2, T-A3, T-A5, and T-A8. Convert T-A9 and T-A10 into modifier tags. Merge T-V1/T-V4 and fold T-V6 into T-V2 unless data proves separation.

3. Fix framework citations and downgrade causal language. Remove phrases like “NOT ad-hoc,” “This confirms,” “non-negotiable,” and “audited data.” Use “hypothesized mechanism” consistently.

4. Rewrite all 修修 examples with native zh-Hant, 20-32 character discipline, and health-evidence constraints. Ban invented percentages and biological-age reversals unless sourced.

5. Add validation data: zh-Hant health thumbnails, actual longevity niche creators, 修修’s own historical results, and negative/control examples.

The two most dangerous false-confidence spots are §1’s “Click-driver attributions… are NOT ad-hoc” and §5.2’s T-A8 “幾乎零改編直接使用.” Both overstate what this corpus proves. The playbook is useful as a pattern catalog; it is not yet a decision engine.
