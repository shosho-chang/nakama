# Podcast Carousel pilot feedback

Read this reference when composing, rendering, correcting, or visually reviewing a Podcast Carousel. Skip it for package-status inspection.

## Page-by-page checklist

- Render every page at exactly 1080×1080 and inspect the full-resolution PNG. Square is the canonical cross-platform master because YouTube community image posts require a square-safe asset.
- Confirm the sequence is `cover → one hook → ordered points → quote → CTA`; reject Re-hooks.
- Confirm no visible string came from a template placeholder.
- Use the frozen typography hierarchy. The cover title must be at least as large as a content-page title; Hook/point headline and body retain their assigned roles. Fit must not shrink body far below its role—crossing the readable floor is `needs_review`.
- Give multiline titles breathing line-height. Headline frames must not feel cramped, and optical padding must match on all four sides.
- A square migration is a real recomposition, not a crop. Rebalance the cover and both quote variants for 1:1; retain a large guest presence without letting the cutout collide with copy. On point pages, keep the oversized numeral low enough to act as background rather than crowd the header.
- Check the Hook→point logic and semantic entailment on every page. Preserve subject, stance, and causality; never turn productive AI use into fear of replacement or swap `收入交給流量` for `內容交給流量`.
- Balance whitespace above the Hook and above each page's content optically, not by geometric centering alone. Raise body/content groups when they feel low.
- Size guest and host cutouts assertively without obscuring headlines, body copy, labels, or required marks. Check the actual silhouette, not only its bounding box.
- Alternate orange outline accents around approximately `+1.5°` and `-1.5°`; keep padding visually symmetric on every side.
- Keep the quote host-question box clear of the divider. Give the CTA logo equal external space above and below; balance adjacent quote/divider/CTA whitespace as a set.
- Break CTA episode titles at a natural Chinese punctuation boundary. Never leave one or two characters plus punctuation as an orphan final line.
- Check the complete `NN/TT` page number on every full-resolution PNG. Do not diagnose clipping from a cropped montage or scaled tool preview.
- Use the approved Apple Podcasts, Spotify, and YouTube icons. CTA contains no engagement/comment line.
- Re-check text wrapping, clipping, overlap, cutout edges, icon identity, and whitespace on every page after any correction.

## Failure to rule

| Observed failure | Durable rule |
|---|---|
| Template example text reaches a render | Treat every template string as placeholder; visible copy must come from the evidence-backed Copy Spec. |
| A second Hook is used to restart the story | Strengthen the one opening Hook and order the points beneath it; never add a Re-hook. |
| A point is interesting but does not answer the Hook | Rewrite the Hook or replace/reorder the point until the relationship is explicit without extra episode context. |
| Copy invents anxiety or changes the causal subject | Re-check entailment against evidence; preserve stance, subject, and causal direction exactly. |
| A cutout feels timid or covers copy | Enlarge for presence, then reposition or crop until all copy and marks remain unobstructed. |
| Cover title is smaller than point titles, or multiline text is cramped | Restore the frozen hierarchy, open the line-height, and equalise four-sided optical frame padding. |
| Body fit collapses far below the headline | Keep the frozen headline/body ratio; below the readable floor must be `needs_review`, not silently accepted. |
| Orange boxes feel mechanical or padding is lopsided | Alternate `±1.5°` and equalise optical padding before accepting the page. |
| Hook/body content looks vertically low | Rebalance the top whitespace and move the content group optically; do not rely on geometric centering alone. |
| Host-question box touches the divider, or CTA logo spacing is uneven | Separate the question box from the divider and equalise the CTA logo's external space above and below. |
| Platform marks are generic, duplicated, or wrong | Use only the approved Apple Podcasts, Spotify, and YouTube assets and verify each visually. |
| CTA includes a prompt to comment | Remove it; CTA is episode identity plus the three listening platforms. |
| A montage looks acceptable but one page is broken | Inspect all full-resolution pages; montage review is navigation, not QA. |
| Feedback was left blank but treated as approval of one card | Blank means no requested change; only the separate all-blank Approve action approves the revision. |
| Approve starts editing or publishing | Approve records approval only. Correction requires non-empty feedback; publishing is a separate Stage 6 concern. |
