# Capability Card — `kb-search`

**Status:** Phase 1 (HTTP wrapper baseline) — live at
`.claude/skills/kb-search/`
**License:** MIT (planned open-source extraction)
**Scope:** Thin client over Robin's existing ``POST /kb/research`` endpoint —
takes a natural-language query, returns ranked KB page hits with
Claude-generated relevance reasons, and renders a downstream-consumable
markdown report.

---

## Capability

Given a query string and a reachable ``thousand_sunny`` instance, call
``POST /kb/research``, parse the JSON ``{"results": [...]}`` response, cap
the hits client-side at ``--limit``, and render a markdown file with a
``type: kb-search-result`` frontmatter discriminator plus a ranked-hit
body. The skill itself does no retrieval, ranking, or vault I/O — it is
strictly a transport + render layer over the existing Robin endpoint.

## Input / Output Contract

**Input** — CLI arguments / env:

```
--query "zone 2 訓練"            (required, non-empty)
--limit 8                        (optional, default 8; client-side cap)
--out -|<path>                   (optional, default '-' = stdout)
--api-base http://127.0.0.1:8000 (optional, $NAKAMA_API_BASE fallback)
--api-key $WEB_SECRET            (optional, X-Robin-Key header)
```

**Output** — markdown to stdout or ``--out`` path:

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

The frontmatter ``type: kb-search-result`` is the discriminator;
``schema_version: 1`` is frozen for the lifetime of this slice.
``total_hits`` always matches the rendered ranked-hit count.

## Dependencies

- **Runtime**
  - Python 3.10+
  - `httpx >= 0.27` (already in `requirements.txt`)
- **Internal** (server-side only — the skill calls this over HTTP)
  - `agents/robin/kb_search.py` — vault scan + Haiku ranking
  - `thousand_sunny/routers/robin.py` — endpoint definition
  - `thousand_sunny/auth.py` — cookie / API-key auth
- **Credentials**
  - `WEB_SECRET` (optional) — X-Robin-Key value when server has auth on

No GCP / DataForSEO / firecrawl / Anthropic credentials needed by the
client — the server makes the Anthropic call.

## Cost

- **LLM**: 1 Claude Haiku call per query (server-side ranker), about
  $0.001 per query at typical KB sizes (~500 input + ~200 output tokens).
- **Wall clock**: ~3-8 s, dominated by the Haiku call. Vault scan is
  sub-100 ms; HTTP overhead adds ~10 ms locally.
- **Network**: 1 form POST + 1 JSON response, typically <10 KB on the
  wire.
- **Effective per-run cost**: ~$0.001.

## Open-Source Readiness

Parameterized extension points so the skill can be lifted out of Nakama:

1. **Endpoint URL is one line in the orchestrator** — `run_search()`
   builds ``api_base.rstrip("/") + "/kb/research"``; a fork on a
   ``/api/robin`` prefix only edits this builder.
2. **HTTP poster injection** — `run_search(..., post=fake_post)` lets
   forks (or tests) swap `httpx.post` for any callable matching
   ``(url, form_data, headers) -> response_json``.
3. **Clock injection** — `run_search(..., now_fn=fake_now)` makes
   markdown output reproducible for snapshot tests.
4. **No vault dependency client-side** — a fork with a different KB
   layout (different paths, no ``KB/Wiki/`` prefix) keeps using this
   skill verbatim and only changes the server-side endpoint
   implementation.
5. **Schema discriminator** — ``type: kb-search-result`` +
   ``schema_version: 1`` give downstream code a stable contract to
   parse against; bumping the schema bumps the version.

## Contract Tests

- Unit / pipeline: `tests/skills/kb_search/test_search_pipeline.py` —
  injected fake `post` + frozen `now_fn`, no real HTTP.
- Live endpoint smoke: not in CI; run locally against `127.0.0.1:8000`
  after a `thousand_sunny` start to verify auth + vault wiring.

## Limitations (Phase 1)

- **Server-side limit is hard-coded at 8** — the ranker truncates to
  ``TOP_K = 8`` in `agents/robin/kb_search.py`; the skill's
  ``--limit`` flag can only ask for ``≤8``. Lifting the cap requires a
  Robin-side change (out of scope for this skill).
- **No query expansion** — bilingual / synonym handling is whatever the
  Haiku ranker does on the raw query; no client-side rewriting.
- **No re-ranking** — the order returned by the server is preserved.
- **VPS unavailable by default** — Robin is gated behind
  ``DISABLE_ROBIN=1`` on the VPS, so this skill is a local-development
  tool today.
- **No caching** — every invocation re-runs the Haiku call.
- **No SSE / streaming** — the endpoint returns one JSON payload; if a
  future server-side change adds streaming, the client will need a
  rewrite (the SSE channel exists at ``/events/{session_id}`` for the
  Reader flow but is unrelated to ``/kb/research``).

## Roadmap

- [x] Phase 1 — HTTP wrapper + markdown render (this card)
- [ ] Phase 2 — vault auto-write hook so common queries can be archived
  to `KB/Research/searches/<slug>-<YYYYMMDD>.md` without a `--out` flag
- [ ] Phase 3 — query expansion (en↔zh-TW) and client-side re-ranking
  (e.g. boost recent KB pages)
