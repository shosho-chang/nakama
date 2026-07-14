### Section 1 — CODE GROUNDING

The main code references check out. `thousand_sunny/routers/kb_review.py:330` is `@router.post("/api/permanent")`; it is create-only, checks auth, rejects empty title/body, computes `rel = f"{PERMANENT_DIR}/{slug}.md"`, and returns `409` if `dest.exists()` before writing. I found no permanent-card `PUT`, `PATCH`, or edit endpoint in `thousand_sunny/routers/*.py`; the other permanent route is read-only `GET /kb/api/permanent/peek` at `kb_review.py:670`.

`_assemble_permanent_markdown` exists at `kb_review.py:266`. It writes `type: permanent`, `status: seedling`, `author: human`, `created`, `modified`, `source_refs`, `aliases`, then appends body and optional typed edge inline fields. So the endpoint does write Permanent Note body and typed links, but only at create time through a human-authoring route.

`shared/permanent_layer.py` matches the ADR: `ALLOWED_BOOKKEEPING_KEYS = {"source_refs", "modified", "aliases"}` at line 37; `assert_not_permanent_target` is at line 69; `update_permanent_bookkeeping` starts at line 134 and rejects illegal keys at line 164. It preserves body by writing reconstructed frontmatter plus original body. It does not allow `status`, `type`, `author`, or body updates. One caveat: it reserializes all frontmatter, so it can still cause formatting churn around judgment fields even when values are unchanged.

The taxonomy references are also real: `shared/schemas/daily_review.py:37` defines `EdgeType = Literal["support", "refute", "extend"]`; `kb_review.py:58` maps the same three codes; `agents/robin/daily_review.py:665` defines `judge_edges`.

Live vault facts mostly check out. `E:\Shosho LifeOS\KB\Permanent` contains 11 markdown files; all have `status: seedling` at line 3. `Select-String` found no `::`, no `Permanent/`, and no literal `[[` in Permanent files, so typed edges and card-to-card links are currently zero. `KB\Literature` contains 12 files. `KB\MOCs` and `KB\Fleeting` returned empty. The Obsidian claim needs precision: `.obsidian` is at `E:\Shosho LifeOS\.obsidian`, not under `E:\Shosho LifeOS\KB`; the KB is inside a real Obsidian vault, but `KB` itself is not the vault root.

### Section 2 — DRIFT DETECTION

ADR-052 does contradict ADR-043 on the gate. ADR-043 decision 7 says the 3-month honest-use period is the gate and explicitly makes Slice 1 an “Obsidian-first pilot” with Bridge authoring UI after the gate. ADR-052 decision 2 reframes `/kb/graph` as the gate instrument and decision 6 adds `adopt-edge` and `status-bump` as new human-authoring endpoints. That is not a neutral interpretation; it moves a Bridge authoring surface into the gated period.

The red line can still be respected, but not by hand-waving “author: human.” A web endpoint is callable by any process with the auth cookie or local access. If `adopt-edge` and `status-bump` ship, they need explicit route naming, CSRF protection, before-hash checks, append-only logs, and tests proving no Robin/shared service imports or calls them. Status-bump is especially sensitive: `status` is a judgment field under ADR-043, so a status endpoint is acceptable only as a tightly isolated human action.

Appending edges to an existing card body also introduces a race with `update_permanent_bookkeeping`. The bookkeeping writer preserves body, but it rewrites the file from a prior read. Without file hashes or locking, a human edge append and an AI bookkeeping update can lose one another.

There is glossary drift. Code supports exactly `support/refute/extend`; ADR-052 should not imply a fourth “example/舉例” type. There is also status drift: `overview.html:29` and `kb_review.py:804` use `growing`, while `agents/robin/CONTEXT.md:112` says `seedling -> budding -> evergreen -> superseded`. Do not add `status-bump` until that ladder is canonical.

### Section 3 — NUMERICAL / FACTUAL CLAIMS

The current-card estimate is plausible but weakly evidenced. The 11 Permanent files total about 6.8KB, averaging roughly 623 bytes each. For mostly CJK cards plus YAML, “300-500 tokens/card” is plausible, but it is not proven without the exact tokenizer and future card-size distribution. The current vault is too small and homogeneous to extrapolate.

The single-call ceiling is stale unless the ADR names the actual model. ADR-052 assumes about `150K` usable context and a `200K` window. Official OpenAI docs currently list GPT-5.6 Sol/Terra/Luna with `1.05M` context and `128K` max output, so “10k title-only blows 200K” is not a universal model fact anymore; it is only true for a chosen 200K deployment budget. The arithmetic is otherwise fine: 300-500 cards × 300-500 tokens = 90K-250K before prompt overhead, so a 300-500 card cap is optimistic for a 150K usable budget. For 10k full cards, 3M-5M tokens still blows even 1.05M. Source: OpenAI models page. ([platform.openai.com](https://platform.openai.com/docs/models))

The title-only estimate, 10k × 20-40 tokens = 200K-400K, is plausible. It does not blow a 1.05M nominal window, but it is still too large to treat as a cheap routine prompt if repeated daily.

The “10MB vector index at 10k” claim is wrong as stated. OpenAI’s current embedding docs say `text-embedding-3-small` defaults to 1536 dimensions and `text-embedding-3-large` to 3072, with optional dimension reduction. Raw float32 storage for 10k vectors is about 61MB at 1536 dims and 123MB at 3072 dims, before metadata and index overhead. A 10MB number only works for ~256-dim float32 or compressed/quantized storage. If ADR-052 means “10k cards × ~1KB text = ~10MB corpus text,” say that, not “vector index.” ([platform.openai.com](https://platform.openai.com/docs/guides/embeddings))

“MOC-as-prefilter is structurally blind to cross-MOC links” is directionally sound if MOC membership is used as a hard partition. But “structurally” overstates it: overlapping MOCs, global summaries, or a second-pass global search can recover cross-MOC links. Also, there are currently zero MOCs, so MOC prefiltering is not a day-one scale brake anyway.

### Section 4 — ASSUMPTION PUSH-BACK

Decision 2 is motivated reasoning. ADR-043’s gate was designed to prevent exactly this move: building a Bridge authoring UI before the human has proven a writing/linking habit. ADR-052 flips “prove the habit before building the UI” into “build the UI to create the habit,” then calls that the same gate. That is not a legitimate reframe unless ADR-052 explicitly amends ADR-043 and admits it is relaxing the gate.

The core premise is hopeful, not evidence-based. Five weeks into the ADR-043 window, the live vault has 11 seedling cards, zero human typed edges, zero card links, zero MOCs, and zero fleeting notes. The observed behavior is “cards were created but relationships were not written.” ADR-052 assumes that a graph with AI ghost edges will convert that behavior into link-writing. It provides no evidence that spatial visualization is the missing friction.

The obvious failure mode: `/kb/graph` ships, the owner opens it, sees 11 dots plus AI guesses, maybe finds them interesting, but still writes zero reasons. The system then has more code, more route surface, a daily ghost-generation job, sidecar files, graph layout state, and new red-line risk, while the core metric remains zero human edges. Worse, the graph can create a false sense of progress: “we have relationship discovery now” when the Permanent layer still has no human-authored relationships.

If this proceeds, success must be measured by durable human judgment, not UI activity: adopted edges with written reasons, status changes with rationale, and repeat use over weeks. Page views, generated ghost counts, or “interesting suggestions” do not pass the gate.

### Section 5 — ALTERNATIVES NOT CONSIDERED

First: use Obsidian-native delivery. Generate `KB/.centaur/link_suggestions.md` or an Obsidian side note listing candidate pairs, why-hints, and `obsidian://` links. The human writes the actual edge in Obsidian. Tradeoff: weaker Bridge telemetry and less custom visualization. Benefit: no new web write endpoint, no graph clone, and it uses the graph/editor the owner already has.

Second: push ghost suggestions through the existing daily-review flow. `judge_edges` already exists at `agents/robin/daily_review.py:665`, and `/kb/review` already has the habit surface. Add one “link two existing Permanent cards” item per day, with copyable `support/refute/extend` line and required reason. Tradeoff: less visually exciting. Benefit: it tests the behavioral hypothesis directly with minimal new UI.

Third: add a non-UI weekly lint/nudge. Report orphan cards, likely duplicate concepts, stale seedlings, and “top 3 missing links” via the existing 5am review/Nami path. Tradeoff: lower interaction richness. Benefit: it keeps ADR-043 intact and creates measurable prompts without building an authoring surface.

A fourth narrow option is to change only the create flow: when creating a Permanent card, require “link to one existing card or explicitly mark no link.” That attacks the actual moment links should be written, instead of hoping a later graph session creates the habit.

### Section 6 — FINAL VERDICT

Approve with modifications. Do not approve ADR-052 as written.

Required changes:

1. Rewrite decision 2. Do not say the graph “is the gate” or that this preserves ADR-043 unchanged. Say it is a time-boxed exception/probe inside the ADR-043 gate, with explicit kill criteria.

2. Split decision 6. Slice 1 should be read-only ghost discovery plus `obsidian://` deep links or copyable edge text. Move `adopt-edge` and `status-bump` to post-gate Slice 2 unless you add hash checks, CSRF, audit log, tests, and a hard “no agent path” rule.

3. Fix decision 9. Name the model and budget. Replace the `10MB vector index` claim with dimension-based math, and distinguish corpus text size from embedding/index size.

4. Resolve taxonomy/status drift before implementation. Canonicalize `support/refute/extend`; remove any implied fourth edge type. Pick `growing` or `budding` and update `CONTEXT.md`, `overview.html`, and route code consistently.

5. Add an alternatives table that seriously evaluates Obsidian-native suggestions and daily-review delivery. Given the empty graph, those are stronger first probes than building a Bridge graph first.