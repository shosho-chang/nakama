# title-brainstorm PLAYBOOK

> 快速查表：情緒 angle → 對應 archetype_id；grade gate；archetype index。
> 完整說明見 `prompts/thumbnail/playbook_v1.md`。

---

## 情緒 angle → 推薦 title archetype 映射

每次用 Step 4 疊加 angle 時，同步決定 `archetype_id`；tag 格式：`archetype: [T-AN, T-VN]`。

| 情緒 angle | 主推 archetype | 備選 |
|---|---|---|
| 好奇缺口 | **T-A6** Question Curiosity Gap [A] | T-A5 Exclusive Secret [B] |
| 恐懼／損失 | **T-A10** Cost-Risk-Reframe [B] | T-A3 Contrarian Reversal [B] |
| 渴望／嚮往 | **T-A2** How-To Specificity Anchor [A] | T-A7 Duration Promise [B] |
| 反直覺／衝突 | **T-A3** Contrarian Reversal [B] | T-A6 Question Curiosity Gap [A] |
| 共鳴／被看見 | **T-A4** Story-Confession Blueprint [B] | T-A10 Cost-Risk-Reframe [B] |
| 內幕／窺探 | **T-A5** Exclusive Secret [B] | T-A8 Authority-Research Lead [S] |

> **跨角度補丁（任何 angle 可加乘）**：
> - 數字/精確 → T-A1 Numbered Listicle [A]
> - 權威/研究 → T-A8 Authority-Research [S]
> - 時效/年份 → T-A9 Year-Anchor [B]

---

## Title D/F-grade gate

`emit_packages.py` 自動剔除 D/F-grade title archetype：

| Grade | 處理 |
|---|---|
| **S / A** | 直接採用 |
| **B** | 採用（需重品牌適配） |
| **C** | 採用（需明確 hedge） |
| **D** | **剔除** — 品牌可信度風險 |
| **F** | **剔除** — antipattern |

目前 title archetypes（T-A1–T-A10）全為 S/A/B 等級，無 D/F；gate 為未來擴充保留。
Thumbnail archetypes 有 T-V6 [D]，thumbnail 側 gate 在 S7 thumbnail brainstorm 處理。

---

## Archetype index（compact，取自 `format_playbook_index_for_prompt()`）

## Playbook archetype index (pick by ID; full text in `prompts/thumbnail/playbook_v1.md`)

Use these distilled archetype catalogs as your style anchor — NOT vision few-shot images. Each archetype is graded for 修修's Health & Wellness / Longevity brand fit.

### Title archetypes

- **T-A1** [A] Numbered Listicle Promise — Title leads with a finite small number (3–11) of items, methods, habits, or tips.
- **T-A2** [A] How-To with Specificity Anchor — Title opens with 'How to…' or an imperative verb and pairs it with a concrete specificity marker (number, tool name, time, or named outcome).
- **T-A3** [B] Contrarian Reversal — Title challenges conventional wisdom or uses a 'you've been doing it wrong' frame to create cognitive dissonance and compel resolution.
- **T-A4** [B] Story-Confession / Personal Blueprint — First-person hypothetical or retrospective frame: 'If I were starting over…', 'What I wish I knew…', or 'I did X and here's what happened.'
- **T-A5** [B] Exclusive Secret / Hidden Truth — Title implies the viewer is about to receive insider knowledge that is rare, suppressed, or typically withheld from the public.
- **T-A6** [A] Question Curiosity Gap — Title poses a direct question — often personal-stakes or wonder-framed — that the video answers.
- **T-A7** [B] Duration / Time-Compression Promise — Title promises that a large body of knowledge or skill can be acquired in a precisely specified short time (8 min, 13 min, 80% in 20 min).
- **T-A8** [S] Authority-Research Credibility Lead — Title leads with a named authority figure, institution, hyper-specific data point, or personal consumption of a large body of research to validate the content.
- **T-A9** [B] Year-Anchor Currency Signal — Title includes a specific year (2024, 2026) as a freshness and relevance marker, implying that older advice is now outdated.
- **T-A10** [B] Cost-Risk-Reframe / Loss Aversion Lead — Title foregrounds what the viewer is currently losing, wasting, or getting wrong — flipping from gain to loss framing — often with a specific number.

### Thumbnail archetypes

- **T-V1** [B] Face-Right Text-Left Dual-Zone — Creator face occupies the right 40-50% of frame; high-contrast, large-type text or a key phrase dominates the left zone.
- **T-V2** [B] Face-Center Tight Crop with Text Overlay — Creator face fills the center frame with a tight crop (chin to forehead), with a short text overlay anchored at bottom or floating over chest.
- **T-V3** [A] Split-Screen Comparison — Thumbnail is divided into two equal zones — before/after, option A/option B, person A vs. person B — creating a visible contrast tension.
- **T-V4** [A] Face-Left Text-Right Exposition Layout — Creator face is anchored left (often pointing toward text); the right zone contains either text overlay, a visual prop, icon cluster, or diagram.
- **T-V5** [B] Whiteboard / Diagram Reveal with Creator — Creator stands beside or points at a whiteboard, roadmap diagram, or dense annotated list, signalling comprehensive insider content.
- **T-V6** [D] Surprised / Excited Face with Question-Mark Overlay — Creator displays exaggerated surprise or excitement; short overlay text ends in a question ('THEY SOLVED IT?', 'DID WE DO IT?', 'TOO GOOD?') leaving the answer open.
- **T-V7** [A] Object / Tool Hero with Creator Reaction — A product, tool interface, diagram, or physical object is the visual hero; creator face is secondary, often reacting to or holding the object.
- **T-V8** [C] High-Saturation Colour Pop with Bold Command Text — Thumbnail uses a single highly saturated background or colour block (#D0021B, #F5C518, #4CAF50) with 2-4 words of bold, all-caps command or statement text.
- **T-V9** [B] App Icon / Tool Logo Cluster with Creator Face — Multiple recognisable app logos, platform icons, or UI screenshots surround or flank the creator's face, signalling multi-tool expertise.
- **T-V10** [A] Numerical Metric Hero with Reaction Face — A specific, large, or surprising number (dollar amount, time saving, count) is the dominant text element, with creator face reacting to it.

### Common joint pairings (high-frequency title × thumb combos)

- **JP-1** Contrarian Title + High-Saturation Command Text Thumbnail (T-A3 × T-V8, n=9)
- **JP-2** Story-Confession Blueprint Title + Whiteboard Diagram Thumbnail (T-A4 × T-V5, n=5)
- **JP-3** Duration Promise Title + Face-Right Clean-Background Tutorial Thumbnail (T-A7 × T-V4, n=10)
- **JP-4** Numbered Listicle Title + Excited Face with Icon Cluster Thumbnail (T-A1 × T-V9, n=6)
- **JP-5** Question Curiosity Gap Title + Surprised Face Question Overlay Thumbnail (T-A6 × T-V6, n=8)
- **JP-6** How-To Specificity Title + Split-Screen Before/After Thumbnail (T-A2 × T-V3, n=6)
- **JP-7** Authority Research Title + Tight Face Crop (Surprised) Thumbnail (T-A8 × T-V2, n=5)
- **JP-8** Exclusive Secret Title + 'YOU'RE BEING LIED TO' Confrontational Overlay Thumbnail (T-A5 × T-V8, n=4)

### Brand-fit grade meaning
- **S** = direct copy works for 修修 brand voice
- **A** = minor adaptation (translate + light tweak)
- **B** = heavy adaptation (preserve click-driver, change voice substantially)
- **C** = risky — pattern works but conflicts with evidence-based health voice; use sparingly with explicit hedges
- **D** = avoid — regulatory or brand-credibility risk (e.g. T-V6 Question Overlay under Taiwan Health Food Control Act)
- **F** = antipattern — would damage brand credibility
