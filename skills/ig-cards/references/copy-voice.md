# Podcast Carousel copy voice (Traditional Chinese)

Read this reference whenever you draft or correct carousel prose. The Copy contract in
`SKILL.md` governs structure, evidence binding, and what is forbidden; this file governs
how the sentence itself should sound. Structure rules alone leave the prose to generic
model instincts, and those instincts have a stable, recognisable failure signature.

Every example below is a real pair from `20260901 蘇予昕`: `r001` is what the agent wrote,
`r002` is what 修修 rewrote at the Review Gate. He changed seven of eight body fields and
left every cover and CTA string untouched — the defect lives in narrative prose, not in
headlines.

## The seven tells

### 1. Land on the affirmative; never spend the emphasis on a negation

The emphasis is the single orange highlight on the card. Put it on a negation and the
brightest thing on the page is telling the reader they are wrong.

| | r001 | r002 |
|---|---|---|
| quote | 「…它是我存在的狀態，**不是我的表現優劣**」 emphasis `不是我的表現優劣` | 「…它跟我的表現好壞無關，**我的存在就是有價值的**」 emphasis `存在就是有價值` |
| point headline | 只用腦袋分析，**還不太算覺察** | 開始覺察，就是讓**情緒鬆綁**的開始 |

Same content either way; only the half that gets highlighted changes, and with it whether
the reader is being praised or corrected. This one is now machine-enforced —
`PodcastCarouselCopySpecV1` rejects an emphasis that opens with a negation.

### 2. Use the episode's own vocabulary; do not replace it with invented metaphor

The agent avoided 核心信念 and 內在小孩 and substituted self-authored figures — 線索,
印記, 放大鏡, 裝上去. 修修 put the terms back, in quotation marks, and flattened the
literary diction around them.

- `父母該給而沒給` → `父母沒做好`
- `留下的印記卻是` → `她內心留下的「核心信念」卻是`
- `表現優劣` → `表現好壞`
- He *added* a sentence that did not exist: 「「我不值得被愛」這個核心信念也會慢慢長出來。」

A reader who meets 核心信念 leaves with a term they can carry and search. A reader who
meets 印記 leaves with nothing. Terminology up, rhetoric down.

### 3. Every card must stand alone

Carousel pages are screenshotted and shared individually. A figure introduced on another
card does not survive the trip.

- r001: 「那支**放大鏡**你也內化了」 — requires the previous card.
- r002: 「那支**「覺得自己不夠好」的**放大鏡你也內化了」 — labels it in place.

Also delete motifs planted earlier and recycled later; `是第一條可以查的線索` echoed the
Hook and 修修 cut it for the plain 「都可以從原生家庭來探索」.

### 4. Never invent a person

The agent wrote a third-person protagonist and a vague witness who appear nowhere in the
transcript. This is the copy-side form of the 嚴禁幻想 red line.

- 「後來**他**想起：我爸也是這樣」 → 修修 moved the subject back to the reader.
- 「**有人**被她叫不要做，回去反而做了一點」 → 「當**自己**被允許可以什麼都不做」
- 「小三轉學被霸凌」 (no subject) → 「**予昕**小三轉學被霸凌了一整個學期」

Permitted subjects: 你, the named guest, the host, and people the transcript actually
names. Not 他, not 有人, not a composite reader-character.

### 5. Let the reader recognise a feeling, not a scene

- r001: 「主管聽兩句就說你不用講了。換了公司，同事聽兩句轉頭就走。」
- r002: 「主管念你兩句、同事不聽你把話說完，都可以**生氣三天三夜**，後來想起來還是**忿忿不平**。」

A reader may never have had the scene. Everyone recognises 生氣三天三夜. Scenes are the
agent showing craft; feelings are what makes the reader say "that's me".

### 6. Write connected sentences, close to speech

Clipped parallel fragments read as rhythm the author imposed. 修修 restored the
connectives and the hedges throughout: 大概**會有**六七千個念頭, **但**絕大部分,
**於是**讓她很小就得了胃潰瘍, 這些**持續發生而且難以化解的**情緒.

Dropping subjects and conjunctions is not concision; it hands the work to the reader.

### 7. Do not overstate to make a sentence land

- r001: 「受傷的人你不會叫他站起來跑」
- r002: 「受傷的人你不會叫他**馬上**站起來跑」

Two characters, and the claim becomes true — of course you would eventually. Trimming the
qualifier that makes a sentence correct is not tightening.

## Failure to rule

| Observed failure | Durable rule |
|---|---|
| Emphasis is a negation phrase (`不是我的表現優劣`, `還不太算覺察`) | Highlight the affirmative half. Schema-enforced; reword rather than work around it. |
| Card ends by telling the reader what is not true | Close on the claim that is true; the correction can live mid-sentence. |
| Invented metaphor replaces the episode's own term | Use 核心信念 / 內在小孩 / the guest's actual wording; quote the term rather than paraphrasing it away. |
| A figure refers back to another card (`那支放大鏡`) | Label it on the card that uses it; assume the page is seen alone. |
| A motif is planted in the Hook and recycled in a point | Cut the recycle; each page carries its own plain statement. |
| Copy introduces `他`, `有人`, or a composite character | Only 你, the named guest, the host, and transcript-named people may appear. |
| Body opens with a scene the reader may not have had | Name the feeling instead; scenes are optional colour, not the hook. |
| Sentences are clipped fragments in parallel | Restore connectives (但 / 於是 / 都可以) and write near speech. |
| The subject or causal connective was dropped for compression | Name the subject (`予昕`) and the causality (`於是`). |
| A qualifier was cut so the line reads cleanly | Keep the word that makes the claim true (`馬上`). |
| Prose praises a course, exercise, or product the episode has a commercial relationship with | Remove it or disclose it; 修修 deleted the 家庭圖 exercise from `point-not-awareness` and the short-form brand lens independently flagged the same span as undisclosed placement. |
