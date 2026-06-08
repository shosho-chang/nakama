# Codex (GPT-5) audit — ADR-044 Robin VPS integration

> Panel step 2/5. Verbatim. Model: GPT-5 via `codex exec --sandbox read-only`
> (full repo read access). Date: 2026-06-08. Tokens used: 263,312.

**1. Code Grounding**
- `thousand_sunny/routers/books.py:152-159` is WRONG as cited. Those lines are legacy redirects, not the upload form:
  `152:@legacy_router.get("/books/upload")`, `154:return RedirectResponse("/robin/books/upload", status_code=301)`, `157:@legacy_router.post("/books/upload")`, `159:return RedirectResponse("/robin/books/upload", status_code=308)`.
- EPUB upload itself is VERIFIED, but the ADR line range is stale. Actual POST starts at `books.py:283:@router.post("/books/upload")`; auth is `314:if not check_auth(nakama_auth):`; file bytes are read at `323-328`; empty upload is rejected at `330-332`; persistence happens at `386:store_book_files(book_id, bilingual=sanitized, original=original_bytes, cover=cover_blob)` and DB insert at `403:insert_book(book)`.
- `store_book_files` end-to-end disk write is VERIFIED: `shared/book_storage.py:114:book_dir = _books_root() / book_id`, `116:(book_dir / "bilingual.epub").write_bytes(bilingual)`, `118:(book_dir / "original.epub").write_bytes(original)`.
- Listed books page auth lines are VERIFIED but the ADR’s “auth on every route” claim is WRONG. Verified lines include `books.py:261`, `274`, `314`, `424`, `498`. Also book file serving is gated: `517:@router.get("/api/books/{book_id}/file")`, `521:nakama_auth`, `523:if not check_auth(nakama_auth):`.
- Missing books API auth is the biggest code-grounding failure. These are unauthenticated: `448:@router.get("/api/books/{book_id}")`; `458:@router.post("/api/books/{book_id}/ingest-request")`; `469:@router.delete("/api/books/{book_id}/ingest-request")`; `479:@router.get("/api/books/{book_id}/cover")`; `536:@router.get("/api/books/{book_id}/annotations")`; `562:@router.post("/api/books/{book_id}/annotations")`; `600:@router.get("/api/books/{book_id}/progress")`; `619:@router.put("/api/books/{book_id}/progress")`.
- Non-books reader/video endpoints are mostly VERIFIED as auth-gated: `robin.py:443:if not check_auth(nakama_auth):` for `/robin/read`; `680` for `/robin/files/{path:path}`; `702` for `/robin/save-annotations`; `1709` for `/robin/watchlist/{video_id}`; `1924` for video annotation POST; `2037` for video annotation DELETE.
- `DISABLE_ROBIN` gate is VERIFIED but broader than the ADR admits: `app.py:71-73` wires promotion services; `132-139` mounts `robin`, `books`, and legacy routers; `147-153` mounts `/vendor/foliate-js`. `promotion_review` and `writing_assist` routes are always included at `app.py:172-179`, but become live when lifespan wiring runs.
- `promotion_wiring.py:174` is VERIFIED: `174:elif config.promotion_mode == "llm":`, `177:raise RuntimeError(...)`.
- Map-stage fallback is VERIFIED: `ingest.py:329-334` imports `ask_local/is_server_available` and returns `ask_local`; `338:logger.warning(...)`; `339:return ask`.
- C/E vault residency is VERIFIED: `reading_source_registry.py:386-390` returns `source_id=f"inbox:{logical_original}"`, kind `inbox_document`; `469-474` emits `Watchlist/youtube/{video_id}/{transcript_path}`; `agents/robin/agent.py:105-107` copies to `KB/Raw/...`.
- `.gitignore:40:data/*` is VERIFIED. But the “cwd-relative `data/books`” concern is STALE: `shared/book_storage.py:40-41` anchors default storage to repo root, not cwd; `88:return Path(os.environ.get("NAKAMA_BOOKS_DIR", _DEFAULT_BOOKS_DIR))`.

**2. Drift Detection**
- “config + one-time migration, not new feature” is not true. Shipping this safely requires code changes: auth on all `thousand_sunny/routers/books.py` API endpoints, production auth tests, startup checks, backup policy, and route exposure decisions.
- Removing `DISABLE_ROBIN` enables hidden write/network surfaces: `/robin/translate`, `/start`, `/execute`, `/discard`, `/watchlist/add`, `/watchlist/add/confirm`, `/kb/research`, books ingest/delete/progress/annotations, and promotion commit paths.
- Foliate is a hard reader dependency, not just setup polish. `book_reader.js:5` imports `/vendor/foliate-js/view.js`; `app.py:143-146` says missing submodule means “reader page will fail to load JS”.
- Video reader needs more than vault files. `av-reader.js:100` loads `https://www.youtube.com/iframe_api`; watchlist add uses `yt-dlp` via `fetch_metadata`/`fetch_caption`.
- Article reader is not pure read-only: `agents/robin/image_fetcher.py:32` writes `vault/Files`, `61-62` downloads remote images, and `74` rewrites the source markdown. ADR-044 must prove `Files/`, `Inbox/`, `Watchlist/`, `KB/Attachments/`, and `KB/Annotations/` are actually in the VPS Syncthing set.

**3. Numerical / Operational Claims**
- Cloud fallback is wired and tested, but cost is under-specified. Default fallback is not Gemini unless configured: `shared/llm_router.py:23-25` defaults to `claude-sonnet-4-20250514`; `.env.example:21` only comments `MODEL_ROBIN=gemini-2.5-pro`.
- The repo itself warns `update_merge` can cost real money: `shared/kb_writer.py:633-638` says `update_merge` is 1x `claude-opus-4-7`, roughly `$0.15-$1.00`, edge `$1.20`.
- VPS headroom is not demonstrated. EPUB parsing uses `ebooklib`, `BeautifulSoup`, `markdownify`, and image extraction (`shared/raw_ingest.py:18-21`, `74-82`, `135-146`); video ingest uses network + caption fetch; article open can fetch remote images.
- “Mostly config” is not defensible without a benchmark: long EPUB parse, long article translate, long map-reduce ingest, video watchlist add, and promotion review start/commit on the actual VPS.

**4. Assumption Push-Back**
- Single Robin host does not mean conflict-free. `AnnotationStore` locks are process-local only: `annotation_store.py:102` says “process-local”; `129-135` overwrites the whole `KB/Annotations/{slug}.md`. Mobile Obsidian can still edit the synced file out-of-band.
- `WEB_PASSWORD` alone is weak for internet-facing upload/delete/ingest. Worse: `auth.py:36-37` returns true when `WEB_PASSWORD` is empty. That is acceptable for dev, not a production invariant.
- `NAKAMA_PROMOTION_MODE=dry_run` is misleading. It dry-runs extractor/matcher wiring, but `promotion_review.py:322-339` still calls `service.commit_approved(... vault_root=get_vault_path())`, and `PromotionCommitService` writes files.
- `data/books/` is a single point of loss. It is ignored by git, outside vault sync, and not backed up by the ADR. `state.db` is also critical for book metadata/progress.
- Dropping local GPU Qwen changes privacy, latency, cost, and reliability. The fallback is operationally real, not a harmless implementation detail.

**5. Alternatives**
- Defer B/books and ship C/E first: article/video are closer to vault-resident, while books need auth + backup + storage migration.
- Use a read-replica/dual-host split with explicit ownership: VPS for mobile reader, local Windows for GPU ingest or books until backup/auth is fixed.
- Put EPUBs in a separate Syncthing folder or backed-up object store instead of unsynced `data/books/`.
- Put Cloudflare Access, Tailscale, or another reverse-proxy auth in front of Robin, with `WEB_PASSWORD` as a second layer.
- Keep local Qwen as an optional worker behind a queue, so VPS does not force all Map-stage work to cloud.

**6. Verdict**
Reject as written. The direction may be viable, but ADR-044 currently overclaims auth, undercounts enabled surfaces, and treats storage/security/backup as deployment trivia.

Top required changes before shipping:

1. Add `check_auth(nakama_auth)` or a router dependency to every `/robin/api/books/*` endpoint, with production-mode tests using `WEB_PASSWORD`.
2. Add a deployment preflight for `WEB_PASSWORD`, `WEB_SECRET`, `VAULT_PATH`, `DB_PATH`, `NAKAMA_BOOKS_DIR`, `NAKAMA_PROMOTION_MODE`, and `NAKAMA_DEV_AUTH_BYPASS`.
3. Define backup/restore for `data/books/` and `state.db` before upload-to-VPS becomes canonical.
4. Rename or constrain `NAKAMA_PROMOTION_MODE=dry_run`; do not expose commit-capable promotion as “dry-run” without clearer UX and guardrails.
5. Document Syncthing ownership and conflict policy for `Inbox/`, `Watchlist/`, `Files/`, `KB/Annotations/`, `KB/Raw/`, and `KB/Wiki/`.
6. Keep or replace local Qwen with an explicit cost/latency/privacy plan and a real long-document benchmark.
7. Add foliate-js/systemd/mobile smoke tests, including `/vendor/foliate-js`, CSP, EPUB file fetch, annotations, and progress.
