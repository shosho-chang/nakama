# Content Type Guide

How to choose between the 4 content types when the user hasn't explicitly said.

## youtube

**Produces**: Video script + metadata workflow
**Body includes**: KB Research button, Keyword Research button with title suggestions,
  `%%KW-START/END%%` anchor block (consumed by keyword-research skill), `One Sentence`
  section (KB query source), `Script / Outline` section
**3 tasks**: Pre-production / Filming / Post-production
**Signals**: "影片 / YouTube / 拍 / 短片 / shorts / vlog / 錄影"

## blog

**Produces**: Long-form article workflow with SEO focus
**Body includes**: KB Research button, Keyword Research button (blog-tuned),
  `專案描述 / 預期成果` (KB query source), `Draft Outline` section
**3 tasks**: Research / Draft / Publish
**Signals**: "部落格 / blog / 文章 / 長文 / SEO / 醫療文 / 發到 Medium / 社群貼文"

## research

**Produces**: Non-publishing deep-dive knowledge building
**Body includes**: KB Research button, `專案描述 / 預期成果`, `Literature Notes`,
  `Synthesis` (no keyword research — not for publishing)
**3 tasks**: Literature Review / Synthesis / Write-up
**Signals**: "研究 / 論文 / literature / 深入研究 / 搞懂 / 理解 / 學"

Default when the topic is abstract / conceptual and the user didn't mention
a delivery format.

## podcast

**Produces**: Podcast episode workflow
**Body includes**: Episode Sentence, 來賓/大綱, KB Research button, Show Notes
**3 tasks**: Prep & Booking / Recording / Edit & Publish
**Signals**: "podcast / 錄音 / 訪談 / 來賓 / 節目 / 單集"

## Ambiguity Heuristics

| Topic looks like… | Default |
|---|---|
| Abstract concept ("超加工食品是什麼") | research |
| Practical skill ("間歇性斷食怎麼做") | youtube (most common delivery for how-to) |
| SEO-oriented ("best creatine 2026") | blog |
| Interview-oriented ("訪談 Dr. X") | podcast |

When still uncertain, **ask** rather than guess — wrong content_type means
wrong body skeleton, which means the user has to manually clean up later.
