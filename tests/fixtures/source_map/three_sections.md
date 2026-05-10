---
lang: en
title: Multi-Section Document
---

# Section One Heading

Section one body content discusses the first topic in considerable detail
so the resulting candidate carries enough text for the deterministic
extractor to operate over a coherent block of prose. The section is
deliberately verbose so the total document chars cross the long-source
threshold of 1500 chars times 3 (4500 chars) and the builder emits a
per-section layout rather than a single consolidated whole document. This
fixture exercises the heading splitter on H1 boundaries and confirms each
section becomes its own ChapterCandidate with the canonical chapter_ref of
sec-1, sec-2, sec-3 respectively. We pad the section out with explanatory
prose to keep it well above the short-section ceiling so the section is
not merged or pruned by any downstream truncation step. We also include
enough text so the builder's excerpt budget split (proportional to chapter
chars) gets a meaningful fraction across each emitted review item. The
discussion is fictional — no claims are real; the fixture exists purely for
behavior testing of the deterministic builder pipeline. We continue padding
here so the section unambiguously crosses the per-section size needed for
the long-source bucket evaluation. Additional sentences exist solely to
add character count weight without changing the meaning of the test.

# Section Two Heading

Section two body content discusses a second topic with the same level of
verbosity as section one. Repeating the structure across all three sections
is intentional; it confirms that section indexing increments deterministically
(sec-1, sec-2, sec-3) and that pre-heading content above the first H1 gets
discarded by the splitter rather than leaking into sec-1's text buffer. Each
section's body should be inspected as a self-contained candidate by the
extractor, and the resulting evidence anchors should reference the
originating section. Writing this section out at length ensures that the
inbox-source layout decision tips into the long-source bucket rather than
collapsing to a single whole-document item, which would defeat the test's
intent. Padding continues with additional descriptive prose to keep the
section size proportional to the threshold the builder evaluates. We
explicitly mention nutrient distribution, hypothesis testing, mechanism
analysis and outcome trajectories because the deterministic test extractor
returns canned text and doesn't read this body — the body's only job is to
push total document chars across the layout threshold while the extractor
provides the actual claim and evidence content. Padding continues.

# Section Three Heading

Section three body content rounds out the multi-section fixture. It carries
the same length and structure as the previous two sections so all three
sections cross the per-section size needed to remain visible to the
extractor and to populate the per-section item list with three entries.
This section also confirms the splitter handles the trailing section
correctly — the section ends at end-of-file rather than at another heading,
so the splitter loop must close out the buffered section and append it.
Padding here is intentional and serves only to push total document chars
past the long-source threshold of 4500 chars. Without this padding the
document would fall back to short-source layout (single whole item), which
is also a valid path but is exercised by the short_no_headings fixture,
not this one. We round out the fixture with additional sentences mentioning
hypothesis A, mechanism B, contraindication C, and outcome D so the prose
reads as realistic source-document material rather than mechanical filler.
The deterministic test extractor returns canned claims regardless, so this
prose isn't parsed by the extractor; it exists so the chapter chars cross
the layout threshold.
