# Codex (GPT-5) Audit — ADR-028 Vault Layout Consolidation

**Auditor:** Codex CLI v0.128.0 (gpt-5.5, reasoning_effort=xhigh)
**Date:** 2026-05-19
**Subject:** ADR-028 + `docs/VAULT-LAYOUT.md` on branch `docs/adr-028-vault-layout` (PR #611)
**Panel step:** 2 of 5 (multi-agent-panel skill)
**Posture:** Push-back / drift detection / fact verification

Verbatim final response from Codex. Tokens used: ~210,430.

---

**1 — CODE GROUNDING**

I used `E:\nakama-vault-layout` for the review because `E:\nakama` is currently on `main` and did not contain `ADR-028`. Most cited files exist, but several line references and producer claims are wrong.

Verified true: `agents/robin/ingest.py:583,600` writes `KB/index.md`; `agents/robin/pubmed_digest.py:210` writes to `KB/Attachments/pubmed`; `:527` appends `KB/log.md`; `agents/franky/reporter.py:277` writes `AgentReports/franky/{period}.md`; `agents/franky/agent.py:33` reads `AgentReports/dev-backlog.md`; `extensions/news-coo/src/vault/imageFetcher.ts:122,165` creates `attachments/{slug}` and rewrites markdown to that prefix; `shared/lifeos_templates/project_blog.md.tpl:195-196` uses `%%KW-START%% / %%KW-END%%`.

Verified false or misleading: `gateway/handlers/nami.py:1002` is `write_page(path, frontmatter, notes)` for `TaskNotes/Tasks`, not `Nami/Notes`. The Nami note contract is actually in `gateway/handlers/nami.py:458-525`, `:1773`, and enforced by `shared/vault_rules.py:14-20`. `shared/kb_writer.py:475` is `_append_to_section`, not Entity schema. `docs/VAULT-LAYOUT.md:167` claims `KB/Raw/Papers` is produced by `pubmed_digest.py:477,522`; that file writes `KB/Wiki/Sources/pubmed-{pmid}.md` at `:476-477` and `KB/Wiki/Digests/PubMed/{today}.md` at `:521-522`, not Raw/Papers.

The attachment bug is real, but Claude's proof is too narrow. `shared/promotion_commit.py` has zero meaningful `attach*` / `image` / `shutil` / `copyfile` logic, yes. But the legacy Robin path is `agents/robin/agent.py:105-129`: it copies the Inbox markdown to `KB/Raw/{dir}` with `shutil.copy2`, calls ingest, then `file_path.unlink()` deletes the Inbox markdown. That path also ignores sibling `attachments/{slug}/`. `PR-Promotion-Attachment-Fix` must patch both `shared/promotion_commit.py` and `agents/robin/agent.py`, or one live path remains broken.

The biggest implementation mismatch is `docs/VAULT-LAYOUT.md` itself. It says `Status: Active (post-ADR-028)` at line 3 and lists `Files/`, `Nami/`, `AgentBriefs/`, `AgentReports/`, `Schemas/`, `Case Studies/`, and `Incidents/` as absent/deleted at lines 141-146. They still exist in the vault. `scripts/vault_layout_audit.py` is only a skeleton; every audit function returns `[]` at lines 68-116, so it currently reports "No drift detected" by construction.

**2 — DRIFT DETECTION**

ADR-028 mostly aligns with ADR-001 and ADR-027 on role ownership: Brook owns SEO Audit per ADR-027 lines 60-66, so `AgentOutputs/brook/seo-audit/` is correct. But ADR-028 Section 4 contradicts itself: its tree puts `seo-audit/` under `franky`, then line 94 says that is the wrong agent and corrects to Brook. Fix the tree, not just the note.

ADR-011 drift is handled partially well. `VAULT-LAYOUT.md` Section 7 admits the Concept dispatcher is unreachable from textbook-ingest Phase B at lines 295-299. That is honest. But the D2 line reference is wrong: it says Entity v1 schema is codified in `shared/kb_writer.py:475`; it is not.

ADR-017 alignment is mostly good: `KB/Annotations/{slug}.md` matches ADR-017 lines 26-33 and code. Minor correction: `VAULT-LAYOUT.md:177` says "ADR-017 (v1/v2/v3 discriminated union)"; ADR-017 documents v1/v2, while v3 exists in code and later ADR context. Cite the right authority.

ADR-020 alignment is acceptable for `KB/Raw/Books/`, `KB/Wiki/Sources/Books/`, `KB/Wiki/Entities/Books/`, and `_alias_map.md`. `_alias_map.md` has actual producers in `shared/concept_classifier.py` and staging patches in `scripts/run_s8_preflight.py`.

ADR-024 is not contradicted. It defines Source Promotion and RCP, but it does not solve attachment migration. ADR-028 correctly adds that missing contract. The sequencing is wrong, addressed below.

**3 — NUMERICAL / FACTUAL CLAIMS**

"644+ Concept pages" is technically true but materially misleading. Current vault count is `2,762` files in `E:\Shosho LifeOS\KB\Wiki\Concepts\*.md`. If the count is used to justify not renaming `KB/`, the argument is stronger than stated, but the ADR should use the real number.

"69 in `Files/`" is true. I counted 69 files under `E:\Shosho LifeOS\Files`: 47 `.png`, 10 `.jpg`, 4 `.jpeg`, 7 `.gif`, 1 `.webp`.

The split "38 paper figures + 31 Journal pasted images" is false against the current vault. Basename-reference scan found 35 distinct root `Files/` images referenced by `KB/Raw/Papers` or `Inbox/kb`, and 34 distinct images referenced by `Journals/Daily`, with zero overlap. The journal set is 33 `Pasted image*` files plus one non-pasted `febcdd94-..._498x230.webp`. Referring source counts are also off: `KB/Raw/Papers` has 6 referring markdown files, not 9; `Inbox/kb` has 3; `Journals/Daily` has 7. Direct `Files/` string refs are only in 3 Raw paper files and 3 Inbox files; journal refs are Obsidian-style basename embeds.

"10 follow-up PRs" is true: ADR-028 lines 292-301 list exactly 10 PRs.

Other count issue: ADR-028 line 294 says top-level `Inbox/*.md` has 3 files. Current top-level `Inbox` has 17 files, including 7 `.md`, 8 `.epub`, and 2 `.mhtml`.

Also: ADR-028 line 141 promises a mapping table in `docs/VAULT-LAYOUT.md §migration-files-category-a`. That section does not exist.

**4 — ASSUMPTION PUSH-BACK**

"No top-level folder renames for PARA purity" is the right call. This is not owner avoidance. With 2,762 Concept pages and 73 non-doc code/prompt files containing `KB/`, renaming `KB/` to `Resources/` would be churn without behavioral gain. Do not rename `KB/`. Do explicitly defer any `Areas/` decision until the OKR grill, because that decision is about life-system semantics, not this attachment/output cleanup.

The Journals exception is too broad. The phrase "applies retroactively to any future similar mechanical rewrite" creates precedent. Replace it with: one-time exception for ADR-028 `Files/` Category B only; future Journal rewrites require their own PR approval and a diff-only verifier proving path-only edits. The purpose of the red line is voice protection, but precedent language matters.

`AgentOutputs/{agent}/{kind}/` is worth doing, but not first. Option β "just fix READMEs" does not solve `shared/vault_rules.py` hard-coded `Nami/Notes/` or Franky's real read/write paths. The consolidation creates a cleaner contract. But its cost is real, and it should not precede the attachment bug.

`PR-Promotion-Attachment-Fix` must move from #8 to #1. Right now, cleaning Inbox can silently break image references. Any vault migration that moves `Inbox/kb` or deletes old capture folders before fixing attachment promotion increases blast radius. Patch promotion first, then restructure Inbox.

"`Files/` empty, deleted" needs a rollback and verification plan. Require a generated migration manifest: old path, new path, referring markdown file, pre/post hash. Run a dry-run that proves zero remaining references to every root `Files/` basename before deleting anything. Delete via recycle-bin semantics, not permanent removal.

The News Coo FSA root re-pick claim is true but incomplete. `handle.ts:24-40` persists one `FileSystemDirectoryHandle` in IndexedDB, and `options.ts:20-24` overwrites it on pick. But any already-open popup holds the old handle loaded at `popup.ts:75-76` and can still write to old `Inbox/kb`. Add a transition plan: close extension popups, re-pick, run a straggler scan for new writes in old `Inbox/kb`, and keep Robin dual-reading old and new Inbox paths for one release.

**5 — ALTERNATIVES NOT CONSIDERED**

First, split current-state and target-state docs. `VAULT-LAYOUT.md` should either be "current active contract" or "post-Phase-3 target," not both. The clean alternative is: keep `VAULT-LAYOUT.md` current, put the desired tree in ADR-028 as target architecture, and update `VAULT-LAYOUT.md` after each migration PR.

Second, use structured marker blocks instead of positional tokens. A YAML-in-comment block can carry `agent`, `section`, `schema_version`, and `updated_at`. Positional `%%agent-zoro-keywords-start%%` is parseable, but metadata-free and harder to evolve.

Third, split `AgentOutputs` by durability. Nami notes and Brook SEO audit outputs belong in the vault because they remain meaningful as life/work artifacts. Franky weekly health reports, vault audits, and `dev-backlog.md` are Nakama operational artifacts; by the ADR's own heuristic, they are less meaningful if Nakama is rewritten. Put those in repo `docs/reports/` or `data/ops/`, or explicitly justify why they stay in the vault.

**6 — FINAL VERDICT**

Approve with modifications. Do not merge as-is as an "Active post-ADR" canonical reference.

Top required changes:

1. Fix factual counts and add the missing `Files/` migration mapping table. Use 2,762 Concepts, 69 root files, actual 35/34 split, 6 Raw paper sources, 3 Inbox sources, 7 Journal files.

2. Move `PR-Promotion-Attachment-Fix` to PR-1 and patch both `shared/promotion_commit.py` and `agents/robin/agent.py`.

3. Change `VAULT-LAYOUT.md` to current-state truth or label it target-state. Add drift entries for pending `Files/`, `AgentOutputs`, `Inbox`, and orphan-folder migrations.

4. Narrow the Journals exception to this one migration only.

5. Add explicit migration verification, rollback, and News Coo transition steps before any vault folder deletion.
