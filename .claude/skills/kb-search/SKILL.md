---
name: kb-search
description: >
  Search the Robin knowledge base (KB/Wiki Sources / Concepts / Entities)
  for pages relevant to a natural-language query and return ranked hits with
  Claude-generated relevance reasons. Wraps Robin's existing
  ``POST /kb/research`` endpoint over HTTP — no re-implementation, no vault
  reads. Use when the user says "查 KB / 查知識庫 / kb search /
  搜尋知識庫 X / 找關於 X 的資料 / KB 裡有沒有 X". Do NOT use for raw
  keyword research on a topic (use ``keyword-research``) or for ingesting
  a new file into KB (that is Robin Reader / ``/start`` flow).
---

# KB Search — Query the Robin Knowledge Base

You are the interactive wrapper for the Nakama ``kb-search`` script
(`scripts/search.py`). Your job is to take a natural-language query, call
Robin's ``/kb/research`` endpoint via HTTP, and return a ranked list of
KB pages with relevance reasons — making sensible defaults for limit,
output, and API base so the user does not have to think about CLI flags.

You do NOT re-implement the retrieval pipeline. You shell out to
`scripts/search.py` and surface the markdown back to the user.

## When to Use This Skill

Trigger on intent like:

- "查 KB <query>" / "查知識庫 <query>" / "搜尋知識庫 <query>"
- "找關於 <topic> 的資料"
- "kb search <query>" / "search KB for <query>"
- "KB 裡有沒有寫過 <topic>"
- "Robin 知道 <topic> 嗎？"

Do NOT trigger for:

- Raw keyword research from a topic (use `keyword-research`)
- Writing or ingesting a new KB page (that is the Robin Reader UI)
- Enriching keyword research with GSC data (use `seo-keyword-enrich`)
- Editing or moving existing KB pages (manual / vault-side work)

## Prerequisites

- ``thousand_sunny`` reachable at the chosen ``--api-base`` (default
  ``http://127.0.0.1:8000``). On the VPS, Robin is gated behind
  ``DISABLE_ROBIN=1``, so this skill is intended for **local** runs;
  remote use needs Robin re-enabled or a dedicated read-only endpoint.
- ``WEB_SECRET`` env var set if the server requires it (production-style
  auth). In dev mode (``WEB_SECRET`` empty) the API key is ignored.
- The vault containing ``KB/Wiki/Sources``, ``Concepts``, ``Entities``
  must be configured for the running ``thousand_sunny`` (Robin reads
  it server-side; the skill only sees the JSON response).

## Workflow Overview

```
Step 1. Parse query from user message
Step 2. Resolve api-base + api-key from env / flags
Step 3. Confirm scope + cost                  [CONFIRM, fast-mode skippable]
Step 4. Invoke search.py
Step 5. Surface ranked hits + offer next-step hand-offs
```

### Step 1 — Parse query

Extract from the user message:

- ``query`` (required) — natural-language string in zh-TW or en
- ``limit`` (optional) — default 8 (server caps at 8 today)
- ``out`` (optional) — write to a vault path or ``-`` (stdout, default)

If the query is empty or unclear, ask the user. Do NOT guess.

### Step 2 — Resolve API base + key

- ``--api-base`` defaults to ``$NAKAMA_API_BASE`` then ``http://127.0.0.1:8000``
- ``--api-key`` defaults to ``$WEB_SECRET``; sent as ``X-Robin-Key`` header
- In dev mode (``WEB_SECRET`` empty) the key is ignored server-side, so you
  can omit ``--api-key`` for a local one-off

### Step 3 — Cost + time estimate (confirm before running)

- LLM cost: **~$0.001** per query (one Claude Haiku call to rank pages
  the server already has loaded)
- Wall clock: **~3-8 s**, dominated by the Haiku call
- Network: 1 form POST to the endpoint, response is JSON-encoded

For a one-shot interactive query, fast-mode is fine — show the line
"預估：~5s, ~$0.001, query=<query>" and proceed unless the user objects.

### Step 4 — Invoke search.py

Run from any cwd (the script does not need ``shared.*`` imports, so no
``sys.path`` shim or ``python -m`` is required — call it as a plain file):

```bash
python .claude/skills/kb-search/scripts/search.py \
    --query "zone 2 訓練" \
    --limit 8 \
    --out -
```

Or write to a vault path:

```bash
python .claude/skills/kb-search/scripts/search.py \
    --query "zone 2 訓練" \
    --out "$VAULT/KB/Research/searches/zone-2-2026-04-26.md"
```

### Step 5 — Summary + hand-off

Show the user:

```
完成！查到 N 個 KB 頁面（耗時 X.Xs）

Top 候選：
  1. <title> (KB/Wiki/.../<slug>) — <relevance_reason>
  2. ...

下一步建議：
  → 如果 N=0：可以先跑 `keyword-research` 確認外部有沒有可 ingest 的素材
  → 如果有候選：直接打開 vault 對應頁面
  → 如要寫稿：把 markdown path 餵給 Brook compose / `article-compose`
```

---

## Output Contract (for downstream consumers)

The pipeline writes a markdown file with a stable frontmatter discriminator
plus a ranked-hit body. Downstream skills (Brook compose, future
``kb-research-aggregate``) parse the frontmatter to detect ``type:
kb-search-result`` and the body for human display.

Example output:

````markdown
---
type: kb-search-result
schema_version: 1
generated_at: 2026-04-26T03:00:00+00:00
api_base: http://127.0.0.1:8000
query: "zone 2 訓練"
total_hits: 3
---

# KB Search Result

Query: **zone 2 訓練**

## Top hits
1. **Zone 2 訓練協議** — `KB/Wiki/Concepts/zone-2-protocol` (concept)
   - Relevance: 主題即 zone 2 訓練心率區間與適應機制
   - Preview: Zone 2 是 60-70% 最大心率的有氧區間…

## Wiki page candidates
- [[KB/Wiki/Concepts/zone-2-protocol]]
- [[KB/Wiki/Sources/peter-attia-zone2-podcast]]
````

### Stable guarantees

Downstream consumers can rely on:

- Frontmatter ``type: kb-search-result`` discriminator
- ``schema_version: 1`` during this slice's lifetime
- ``total_hits`` always matches the number of items rendered in the body
- ``generated_at`` is ISO 8601 with explicit ``+00:00`` offset

Not stable (may evolve):

- Body markdown structure (headings, ordering)
- Wiki-link path format (``[[KB/Wiki/...]]``) — may switch to relative
  ``[[<slug>]]`` once vault link conventions stabilize

---

## Cost

- **LLM**: 1 Claude Haiku call per query (KB page ranker; ``agents/robin/
  kb_search.py``). At ~500 input tokens + ~200 output tokens that is
  about $0.001 per query.
- **Wall clock**: ~3-8 s (Haiku call dominates; vault scan is sub-100 ms).
- **Network**: 1 form-encoded POST + 1 JSON response (typically <10 KB).

No GSC, DataForSEO, firecrawl, or any external API touch — the skill is
strictly Robin-side.

---

## Open-Source Friendliness

This skill is part of the Nakama repo and intended to be extractable.
Design constraints already in place:

1. **No hardcoded endpoint** — ``--api-base`` is configurable via flag or
   ``$NAKAMA_API_BASE`` env. A fork hosting Robin behind a different prefix
   (e.g. ``/api/robin``) only edits the URL builder in
   ``run_search`` (one line).
2. **No vault dependency** — the skill never reads the Obsidian vault; it
   only consumes the JSON the server returns. A fork with a different KB
   layout (no ``KB/Wiki/...`` paths) can adopt this skill verbatim by
   swapping the server-side endpoint implementation.
3. **HTTP poster injection** — ``run_search(..., post=fake)`` lets a fork
   plug in a different transport (gRPC, in-process, mocked) without
   editing the orchestrator.
4. **Clock injection** — ``run_search(..., now_fn=fake_now)`` enables
   reproducible markdown output for snapshot tests.
5. **Frozen output schema** — ``type: kb-search-result`` +
   ``schema_version: 1`` make it safe for downstream code to discriminate.

See `docs/capabilities/kb-search.md` for the full capability card.

---

## References

| When | Read |
|---|---|
| Endpoint shape | `thousand_sunny/routers/robin.py` (`@router.post("/kb/research")`) |
| Retrieval pipeline | `agents/robin/kb_search.py` |
| Auth model | `thousand_sunny/auth.py` (`require_auth_or_key` / `X-Robin-Key`) |
| Output contract example | `docs/capabilities/kb-search.md` |
| Skill scaffolding pitfalls | `memory/claude/feedback_skill_scaffolding_pitfalls.md` |
| Open-source readiness checklist | `memory/claude/feedback_open_source_ready.md` |
