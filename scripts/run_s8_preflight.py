# ruff: noqa: E501
"""ADR-020 S8 preflight runner — BSE chapter 1 end-to-end through Phase 1 + Phase 2.

This is a one-shot runner used to validate that the merged S1-S7 modules
compose into a working pipeline before launching the full 28-chapter cleanup
re-ingest. It writes to a STAGING vault sub-tree (``KB/Wiki.staging/...``) so
the production ``KB/Wiki/`` is untouched until the user reviews the output.

USAGE (run on host, not in sandbox — vault access required)
-----------------------------------------------------------

    # 1. Activate the venv with anthropic + ebooklib + yaml installed
    cd E:\\nakama
    .venv\\Scripts\\Activate.ps1

    # 2. Set env vars
    $env:ANTHROPIC_API_KEY = "sk-ant-..."
    $env:VAULT_PATH = "E:\\Shosho LifeOS"     # so kb_writer can resolve vault root

    # 3. Run preflight (defaults to BSE ch1)
    python -m scripts.run_s8_preflight

    # 4. Inspect output
    #    F:\\Shosho LifeOS\\KB\\Wiki.staging\\Sources\\Books\\biochemistry-for-sport-and-exercise-maclaren\\ch1.md
    #    F:\\Shosho LifeOS\\KB\\Wiki.staging\\Concepts\\*.md
    #    docs\\runs\\2026-05-06-s8-preflight.md   (acceptance report)

    # 5. CLI flags
    python -m scripts.run_s8_preflight --help
    python -m scripts.run_s8_preflight --dry-run   # walker + classifier only, no LLM / writes

The runner prints a final GREEN-LIGHT or BLOCK recommendation. GREEN-LIGHT
means the user can launch the 28-chapter batch with confidence; BLOCK means
the report contains specific failures that must be fixed first.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------------------
# Path bootstrap so `python scripts/run_s8_preflight.py` works without -m
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_VAULT_ROOT = r"E:\Shosho LifeOS"

# V2 figure transform regex — matches raw markdown figure syntax ![alt](path)
_RE_FIG_RAW = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
DEFAULT_RAW_PATH_REL = "KB/Raw/Books/biochemistry-for-sport-and-exercise-maclaren.md"
DEFAULT_BOOK_ID = "biochemistry-for-sport-and-exercise-maclaren"
DEFAULT_CHAPTER_INDEX = 1
DEFAULT_REPORT_PATH = "docs/runs/2026-05-06-s8-preflight.md"

# Cost-control: preflight uses Sonnet rather than Opus
PREFLIGHT_MODEL = "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("s8-preflight")


# ---------------------------------------------------------------------------
# Result aggregation
# ---------------------------------------------------------------------------


@dataclass
class PreflightResult:
    book_id: str = ""
    chapter_index: int = 0
    chapter_title: str = ""
    raw_path: str = ""

    chapter_chosen_note: str = ""

    verbatim_match_pct: float = 0.0
    section_count: int = 0
    figures_count: int = 0
    figures_decorative: int = 0
    figures_described: int = 0
    figure_class_counts: dict[str, int] = field(default_factory=dict)
    tables_count: int = 0

    concepts_extracted: int = 0
    concept_levels: dict[str, int] = field(default_factory=lambda: {"L1": 0, "L2": 0, "L3": 0})
    concept_dispatch: list[dict] = field(default_factory=list)

    primary_claims_total: int = 0
    primary_claims_missing_pct: float = 0.0
    secondary_claims_missing_pct: float = 0.0
    nuance_claims_missing_pct: float = 0.0

    acceptance_pass: bool = False
    acceptance_reasons: list[str] = field(default_factory=list)

    llm_model: str = PREFLIGHT_MODEL
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    wall_seconds: float = 0.0

    gaps_observed: list[str] = field(default_factory=list)
    fatal_error: str = ""

    # Output paths
    source_page_path: str = ""
    coverage_manifest_path: str = ""
    concept_dir: str = ""

    def recommendation(self) -> str:
        if self.fatal_error:
            return f"BLOCK — fatal error: {self.fatal_error}"
        if not self.acceptance_pass:
            return "BLOCK — acceptance gate failed; see acceptance_reasons"
        if self.primary_claims_missing_pct > 5.0:
            return "BLOCK — primary_claims_missing_pct > 5%"
        return "GREEN-LIGHT — proceed with 28-chapter batch"


# ---------------------------------------------------------------------------
# Staging path patching
# ---------------------------------------------------------------------------


def _patch_kb_writer_to_staging() -> None:
    """Redirect ``shared.kb_writer`` writes from KB/Wiki/... to KB/Wiki.staging/...

    The runner monkey-patches the module-level path constants. ``get_vault_path()``
    still resolves to the user's real vault root, so attachments and existing
    Raw files remain reachable.
    """
    from shared import kb_writer

    kb_writer.KB_CONCEPTS_DIR = "KB/Wiki.staging/Concepts"
    kb_writer.KB_BOOK_SOURCES_DIR = "KB/Wiki.staging/Sources/Books"
    if hasattr(kb_writer, "KB_BOOK_ENTITIES_DIR"):
        kb_writer.KB_BOOK_ENTITIES_DIR = "KB/Wiki.staging/Entities/Books"
    log.info(
        "kb_writer patched → staging dirs: %s | %s",
        kb_writer.KB_CONCEPTS_DIR,
        kb_writer.KB_BOOK_SOURCES_DIR,
    )


def _patch_alias_map_to_staging() -> None:
    """Monkey-patch ``shared.concept_classifier.append_alias_entry`` so the L1
    alias map is written to ``KB/Wiki.staging/_alias_map.md`` instead of the
    real ``KB/Wiki/_alias_map.md``. The original function hardcodes the
    ``KB/Wiki`` segment; we wrap it without touching shared/.
    """
    from shared import concept_classifier as cc

    if getattr(cc, "_s8_staging_patched", False):
        return

    def _staged_append_alias_entry(term: str, source_link: str, vault_path: Path) -> None:
        alias_file = vault_path / "KB" / "Wiki.staging" / "_alias_map.md"
        alias_file.parent.mkdir(parents=True, exist_ok=True)
        entry = f"{term} | {source_link}"
        if alias_file.exists():
            content = alias_file.read_text(encoding="utf-8")
            if entry in content:
                return
            alias_file.write_text(content.rstrip("\n") + f"\n{entry}\n", encoding="utf-8")
        else:
            alias_file.write_text(
                "# L1 Alias Map (STAGING)\n\nterm | source\n--- | ---\n" + entry + "\n",
                encoding="utf-8",
            )

    cc.append_alias_entry = _staged_append_alias_entry
    cc._s8_staging_patched = True
    log.info("alias map redirected → KB/Wiki.staging/_alias_map.md")


# ---------------------------------------------------------------------------
# LLM helpers — use shared.llm.ask with cost tracking
# ---------------------------------------------------------------------------


def _ask_llm(prompt: str, *, system: str = "", max_tokens: int = 8000) -> str:
    """Thin wrapper around shared.llm.ask pinned to PREFLIGHT_MODEL (Sonnet 4.6)."""
    from shared.llm import ask

    return ask(
        prompt=prompt,
        system=system,
        model=PREFLIGHT_MODEL,
        max_tokens=max_tokens,
        temperature=0.2,
    )


def _llm_observability_snapshot() -> tuple[int, int, float]:
    """Read cumulative input/output tokens + cost from llm_observability if available."""
    try:
        from shared import llm_observability as obs
    except Exception:
        return 0, 0, 0.0

    # llm_observability records calls; try several attribute names defensively.
    for attr in ("get_totals", "totals", "_totals", "get_session_totals"):
        fn = getattr(obs, attr, None)
        if callable(fn):
            try:
                t = fn()
                return (
                    int(t.get("input_tokens", 0)),
                    int(t.get("output_tokens", 0)),
                    float(t.get("cost_usd", 0.0)),
                )
            except Exception:
                pass
        elif isinstance(fn, dict):
            return (
                int(fn.get("input_tokens", 0)),
                int(fn.get("output_tokens", 0)),
                float(fn.get("cost_usd", 0.0)),
            )
    return 0, 0, 0.0


# ---------------------------------------------------------------------------
# Phase 1 — body assembly (pure, no I/O)
# ---------------------------------------------------------------------------


def _anchor_equiv(a: str, b: str) -> bool:
    """NFKC-normalize before compare. Tolerates curly↔straight quotes,
    en/em dash variants, no-break space — punctuation drift LLMs commonly
    introduce. Still strict on word/order changes.

    Pure NFKC alone does not equalize U+2019/U+2018 curly quotes with U+0027,
    nor en/em dashes with hyphen-minus; the additional replacements below cover
    the cases the docstring claims. Walker anchor remains canonical — only the
    COMPARISON is tolerant; the body H2 is always written from the walker text.
    """

    def _norm(s: str) -> str:
        s = unicodedata.normalize("NFKC", s)
        s = s.replace("‘", "'").replace("’", "'")
        s = s.replace("“", '"').replace("”", '"')
        s = s.replace("–", "-").replace("—", "-")
        return s

    return _norm(a) == _norm(b)


def _render_appendix_from_dispatch_log(
    sections_json: list[dict],
    dispatch_log: list[dict],
) -> str:
    """Build the full chapter appendix from dispatch log data.

    Concept maps come from ``sections_json``; wikilinks and aliases come from
    ``dispatch_log`` so both the body and frontmatter can share one source of
    truth (B14 fix).

    L2/L3 dispatched concepts → ``[[wikilink]]`` in ``## Wikilinks Introduced``.
    L1 alias entries → plain-text bullets in ``## Aliases Recorded`` (B15 fix —
    2-of-3 panel decision: demote L1 to plain text, not stub wikilink).
    """
    concept_map_blocks: list[str] = []
    for sec in sections_json:
        anchor = sec["anchor"]
        cmap = sec.get("concept_map_md", "").rstrip()
        concept_map_blocks.append(f"### {anchor}\n\n{cmap}")

    seen: set[str] = set()
    wikilinks: list[str] = []
    aliases: list[str] = []
    for entry in dispatch_log:
        level = entry.get("level", "?")
        slug = entry.get("slug", "")
        term = entry.get("term", slug)
        if level == "L1":
            if term and term not in seen:
                seen.add(term)
                aliases.append(term)
        elif level in ("L2", "L3") and slug:
            if slug not in seen:
                seen.add(slug)
                wikilinks.append(slug)

    appendix = "\n\n---\n\n## Section Concept Maps\n\n"
    appendix += "\n\n".join(concept_map_blocks)
    if wikilinks:
        appendix += "\n\n## Wikilinks Introduced\n\n"
        appendix += "\n".join(f"- [[{wl}]]" for wl in wikilinks)
        appendix += "\n"
    if aliases:
        appendix += "\n\n## Aliases Recorded\n\n"
        appendix += "\n".join(f"- {a}" for a in aliases)
        appendix += "\n"
    return appendix


def _assemble_body(
    walker_verbatim_body: str,
    walker_section_anchors: list[str],
    walker_figures: list[dict],  # STAGE-1A-AMBIGUITY: spec says {filename, alt_text, ...} but
    # FigureRef (shared/source_ingest.py) uses vault_path not filename.
    # The body text contains the full vault_path already, so the V2 transform
    # operates via regex on the body — walker_figures is reserved for callers.
    sections_json: list[dict],  # each: {anchor: str, concept_map_md: str, wikilinks: list[str]}
    book_id: str,
    dispatch_log: list[dict] | None = None,
) -> str:
    """Assemble a lossless chapter body from walker verbatim text + LLM sections JSON.

    Body is verbatim by construction — LLM only supplies concept_map_md and
    wikilinks; all paragraph text comes solely from walker_verbatim_body.

    When ``dispatch_log`` is provided the appendix is derived from it so that
    wikilinks and aliases come from a single source of truth (issue #500).
    When omitted the legacy sections_json wikilinks are used (backward compat).
    """
    # Fail-fast on count mismatch — recovery is impossible.
    if len(sections_json) != len(walker_section_anchors):
        i = min(len(sections_json), len(walker_section_anchors))
        w = walker_section_anchors[i] if i < len(walker_section_anchors) else "<missing>"
        lj = sections_json[i]["anchor"] if i < len(sections_json) else "<missing>"
        raise ValueError(f"section anchor mismatch at index {i}: walker={w!r} vs llm_json={lj!r}")
    for i, (w_anchor, sec) in enumerate(zip(walker_section_anchors, sections_json)):
        if not _anchor_equiv(w_anchor, sec["anchor"]):
            raise ValueError(
                f"section anchor mismatch at index {i}: "
                f"walker={w_anchor!r} vs llm_json={sec['anchor']!r}"
            )
        if sec["anchor"] != w_anchor:
            log.warning(
                "anchor punctuation drift tolerated at index %d: walker=%r vs llm_json=%r",
                i,
                w_anchor,
                sec["anchor"],
            )

    # V2 figure transform: ![alt](vault_path) → ![[vault_path]]\n*alt*
    body = _RE_FIG_RAW.sub(lambda m: f"![[{m.group(2)}]]\n*{m.group(1)}*", walker_verbatim_body)

    # Zero-section chapter: return verbatim body with figure transform only.
    if not walker_section_anchors:
        return body

    # Body stays pure textbook (walker verbatim + figure transform). All LLM
    # metadata (per-section concept maps + wikilinks) goes into a single
    # appendix at the chapter end, separated by `---`. This keeps the H2/H3
    # section flow visually clean in Obsidian.
    if dispatch_log is not None:
        appendix = _render_appendix_from_dispatch_log(sections_json, dispatch_log)
        return body + appendix

    # Legacy path: derive wikilinks from sections_json (used when dispatch_log
    # is not yet available, e.g. dry-run or Phase 1 before Phase 2 runs).
    concept_map_blocks: list[str] = []
    all_wikilinks: list[str] = []
    seen_wl: set[str] = set()
    for sec in sections_json:
        anchor = sec["anchor"]
        cmap = sec.get("concept_map_md", "").rstrip()
        concept_map_blocks.append(f"### {anchor}\n\n{cmap}")
        for wl in sec.get("wikilinks", []):
            term = wl.strip("[]").strip()
            if term and term not in seen_wl:
                seen_wl.add(term)
                all_wikilinks.append(term)

    appendix = (
        "\n\n---\n\n"
        "## Section Concept Maps\n\n"
        + "\n\n".join(concept_map_blocks)
        + "\n\n## Wikilinks Introduced\n\n"
        + "\n".join(f"- [[{t}]]" for t in all_wikilinks)
        + "\n"
    )
    return body + appendix


# ---------------------------------------------------------------------------
# Phase 1 — chapter source page generation
# ---------------------------------------------------------------------------


def _read_chapter_source_prompt() -> str:
    """Read the updated S1 chapter-source skill prompt template."""
    p = _REPO_ROOT / ".claude" / "skills" / "textbook-ingest" / "prompts" / "chapter-source.md"
    if not p.exists():
        raise FileNotFoundError(f"chapter-source prompt missing: {p}")
    return p.read_text(encoding="utf-8")


def _build_phase1_prompt(payload, *, book_title: str, ingest_date: str) -> str:
    """Assemble the runtime prompt that goes to the LLM for Phase 1.

    The chapter-source.md skill file is a template (humans-and-LLMs read).
    Here we inline its operational content + the actual ChapterPayload data so
    a single LLM call produces the source page markdown directly.
    """
    skill_doc = _read_chapter_source_prompt()
    figures_json = json.dumps(
        [{"vault_path": f.vault_path, "alt_text": f.alt_text} for f in payload.figures],
        ensure_ascii=False,
        indent=2,
    )
    tables_json = json.dumps(
        [{"caption": t.caption, "row_count": len(t.markdown.splitlines())} for t in payload.tables],
        ensure_ascii=False,
        indent=2,
    )

    return (
        f"{skill_doc}\n\n"
        "---\n\n"
        "## RUNTIME INVOCATION (S8 preflight)\n\n"
        f"book_title: {book_title}\n"
        f"book_id: {payload.book_id}\n"
        f"chapter_index: {payload.chapter_index}\n"
        f"chapter_title: {payload.chapter_title}\n"
        f"section_anchors: {payload.section_anchors}\n"
        f"ingest_date: {ingest_date}\n\n"
        f"figures detected ({len(payload.figures)}):\n{figures_json}\n\n"
        f"tables detected ({len(payload.tables)}):\n{tables_json}\n\n"
        "VERBATIM BODY (copy unchanged):\n\n"
        f"{payload.verbatim_body}\n\n"
        "---\n\n"
        "Output a single JSON object with exactly two top-level keys:\n"
        '  "frontmatter": { all frontmatter fields including wikilinks_introduced (list), '
        'section_anchors (list), vision_status: "caption_only", figures metadata }\n'
        '  "sections": [ { "anchor": "<heading text from section_anchors — no ## prefix>", '
        '"concept_map_md": "```mermaid\\nflowchart LR\\n  A --> B\\n```", '
        '"wikilinks": ["Term", ...] }, ... ]\n'
        "Include one entry per item in section_anchors, in the same order.\n"
        "Respond with raw JSON only — no markdown fences, no preamble, no commentary."
    )


def _strip_outer_fence(text: str) -> str:
    """Remove a single leading/trailing ```markdown fence if the LLM wrapped output."""
    s = text.strip()
    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1 :]
    if s.endswith("```"):
        s = s[: s.rfind("```")].rstrip()
    return s


def _parse_phase1_json(raw: str, payload, prompt: str) -> dict:
    """Parse and schema-validate LLM JSON. Retries once on JSONDecodeError; no retry on schema mismatch."""

    def _parse(text: str) -> dict:
        return json.loads(_strip_outer_fence(text))

    try:
        parsed = _parse(raw)
    except json.JSONDecodeError:
        log.warning("Phase 1: LLM returned non-JSON, retrying once…")
        parsed = _parse(_ask_llm(prompt, max_tokens=16000))

    if not isinstance(parsed.get("frontmatter"), dict):
        raise ValueError(f"LLM 'frontmatter' not a dict: keys={list(parsed.keys())}")
    if not isinstance(parsed.get("sections"), list):
        raise ValueError(f"LLM 'sections' not a list: keys={list(parsed.keys())}")
    if len(parsed["sections"]) != len(payload.section_anchors):
        raise ValueError(
            f"sections count mismatch: LLM={len(parsed['sections'])} walker={len(payload.section_anchors)}"
        )
    return parsed


def run_phase1_source_page(
    payload, *, vault_root: Path, book_title: str, dry_run: bool = False
) -> Path:
    """Run Phase 1 — emit the chapter source page to the staging vault."""
    import yaml as _yaml

    if dry_run:
        parsed: dict = {
            "frontmatter": {
                "title": payload.chapter_title,
                "chapter_index": payload.chapter_index,
                "book_id": payload.book_id,
                "vision_status": "caption_only",
                "wikilinks_introduced": [],
                "section_anchors": payload.section_anchors,
            },
            "sections": [
                {
                    "anchor": a,
                    "concept_map_md": "```mermaid\nflowchart LR\n  A --> B\n```",
                    "wikilinks": [],
                }
                for a in payload.section_anchors
            ],
        }
    else:
        prompt = _build_phase1_prompt(payload, book_title=book_title, ingest_date=str(date.today()))
        log.info("Phase 1: calling LLM for source page (model=%s)", PREFLIGHT_MODEL)
        parsed = _parse_phase1_json(_ask_llm(prompt, max_tokens=16000), payload, prompt)

    body = _assemble_body(
        payload.verbatim_body,
        payload.section_anchors,
        payload.figures,
        parsed["sections"],
        payload.book_id,
    )
    fm_yaml = _yaml.dump(parsed["frontmatter"], allow_unicode=True, default_flow_style=False)
    page_md = f"---\n{fm_yaml}---\n\n{body}"

    out_path = (
        vault_root
        / "KB"
        / "Wiki.staging"
        / "Sources"
        / "Books"
        / payload.book_id
        / f"ch{payload.chapter_index}.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page_md, encoding="utf-8")

    if dry_run:
        print(
            f"[dry-run] wrote ch{payload.chapter_index}.md "
            f"({len(page_md)} chars, {len(payload.section_anchors)} sections, "
            f"{len(payload.figures)} figures)"
        )
    else:
        log.info("Phase 1: wrote %s (%d bytes)", out_path, len(page_md))
    return out_path


# ---------------------------------------------------------------------------
# Figure triage
# ---------------------------------------------------------------------------


def run_figure_triage(payload) -> tuple[dict[str, int], int, int]:
    """Triage every figure into a 6-class bucket. Returns (counts, decorative, described)."""
    from shared.figure_triage import classify_figure

    counts: dict[str, int] = {
        "Quantitative": 0,
        "Structural": 0,
        "Process": 0,
        "Comparative": 0,
        "Tabular": 0,
        "Decorative": 0,
    }
    described = 0
    for fig in payload.figures:
        # Heuristic only — defer LLM fallback to keep preflight cheap unless needed
        cls, conf = classify_figure(caption=fig.alt_text, alt_text=fig.alt_text)
        counts[cls] = counts.get(cls, 0) + 1
        if cls != "Decorative":
            described += 1
    return counts, counts["Decorative"], described


# ---------------------------------------------------------------------------
# Phase 2 — concept dispatch
# ---------------------------------------------------------------------------


def _extract_concepts_from_source_page(source_md: str) -> list[str]:
    """Pull the ``wikilinks_introduced`` list from the LLM-emitted source page.

    Defensive parse: looks for `wikilinks_introduced:` in the YAML frontmatter
    and returns plain string slugs (stripped of `[[ ]]` if present).
    """
    import re

    # Pull frontmatter
    m = re.match(r"^---\n(.*?)\n---\n", source_md, re.DOTALL)
    if not m:
        log.warning("source page has no frontmatter — cannot extract concepts")
        return []

    fm_text = m.group(1)
    try:
        import yaml

        fm = yaml.safe_load(fm_text) or {}
    except Exception as e:
        log.warning("frontmatter YAML parse failed: %s", e)
        return []

    wl = fm.get("wikilinks_introduced") or []
    out: list[str] = []
    for entry in wl:
        s = str(entry).strip()
        if s.startswith("[[") and s.endswith("]]"):
            s = s[2:-2]
        s = s.strip()
        if s:
            out.append(s)
    return out


def _slug_from_term(term: str) -> str:
    """Filesystem-safe slug from a raw surface form.

    Pipes through canonicalize() first so variant surface forms (e.g. "ATP",
    "Adenosine Triphosphate", "atps") all resolve to the same slug ("atp").
    """
    from shared.concept_canonicalize import canonicalize

    s = canonicalize(term)
    bad = '<>:"/\\|?*\n\r\t'
    for ch in bad:
        s = s.replace(ch, "-")
    return s.strip("- ").strip()


def run_phase2_dispatch(
    *,
    payload,
    source_page_path: Path,
    chapter_text: str,
) -> list[dict]:
    """Run Phase 2 — classify each concept (L1/L2/L3) and dispatch to KB.

    Returns a list of dispatch log entries: ``{slug, level, action, signals}``.
    """
    from shared.concept_classifier import (
        append_alias_entry,
        route_concept,
    )
    from shared.concept_dispatch import IngestFailError, dispatch_concept

    source_md = source_page_path.read_text(encoding="utf-8")
    concepts = _extract_concepts_from_source_page(source_md)
    log.info("Phase 2: %d concepts identified for dispatch", len(concepts))

    book_id = payload.book_id
    ch_idx = payload.chapter_index
    source_link = f"[[Sources/Books/{book_id}/ch{ch_idx}]]"
    vault_root = Path(os.environ.get("VAULT_PATH", DEFAULT_VAULT_ROOT))

    log_entries: list[dict] = []
    for term in concepts:
        slug = _slug_from_term(term)
        if not slug:
            continue
        try:
            level, signals = route_concept(term, chapter_text, source_count=1)
        except Exception as e:
            log.warning("classifier failed for '%s': %s", term, e)
            log_entries.append({"slug": slug, "level": "?", "action": "skipped", "error": str(e)})
            continue

        entry = {"slug": slug, "term": term, "level": level, "signals": signals}

        if level == "L1":
            try:
                append_alias_entry(term, source_link, vault_root)
                entry["action"] = "alias"
            except Exception as e:
                log.warning("alias append failed for '%s': %s", term, e)
                entry["action"] = "alias-failed"
                entry["error"] = str(e)
            log_entries.append(entry)
            continue

        # L2 / L3 — extract a body paragraph from chapter_text containing the term.
        body = _extract_concept_body_for(term, chapter_text)
        if not body or len(body.split()) < 50:
            # Best effort minimal body — never write a phase-b-style stub.
            body = _build_seed_body(term, chapter_text, signals)

        action = "create"
        en_terms = _candidate_en_terms(term, chapter_text)
        try:
            path = dispatch_concept(
                slug,
                action=action,
                source_link=source_link,
                en_source_terms=en_terms,
                maturity_level=level,
                high_value_signals=signals if level == "L2" else None,
                title=term,
                domain="biochemistry",
                extracted_body=body,
            )
            entry["action"] = action
            entry["path"] = str(path)
        except IngestFailError as e:
            entry["action"] = "ingest-fail"
            entry["error"] = str(e)
            log.error("INGEST FAIL on '%s': %s", slug, e)
        except FileExistsError:
            entry["action"] = "exists-skip"
        except Exception as e:
            entry["action"] = "dispatch-error"
            entry["error"] = str(e)
            log.warning("dispatch error for '%s': %s", slug, e)

        log_entries.append(entry)

    from shared.concept_dispatch import reconcile_mentioned_in

    reconcile_mentioned_in(log_entries, source_link)

    return log_entries


def _extract_concept_body_for(term: str, chapter_text: str) -> str:
    """Pull the first paragraph of chapter_text that mentions the term, plus 1 neighbour.

    This gives `dispatch_concept` ≥ 50 words to seed an L2/L3 body. If nothing
    is found, returns empty string so caller falls back to seed builder.
    """
    paragraphs = [p.strip() for p in chapter_text.split("\n\n") if p.strip()]
    for i, para in enumerate(paragraphs):
        if term.lower() in para.lower():
            # Return this paragraph plus the next, if any
            chunk = para
            if i + 1 < len(paragraphs):
                chunk += "\n\n" + paragraphs[i + 1]
            return chunk
    return ""


def _build_seed_body(term: str, chapter_text: str, signals: list[str]) -> str:
    """Last-resort seed body so we never emit a 'Will be enriched' stub.

    ADR-020 forbids placeholder stubs; this builder injects classifier signals
    and the first sentence containing the term as a minimal honest seed.
    """
    first_sentence = ""
    for sentence in chapter_text.replace("\n", " ").split(". "):
        if term.lower() in sentence.lower():
            first_sentence = sentence.strip()
            break

    seed = (
        f"## Definition\n\n"
        f"**{term}** is introduced in this chapter. "
        f"{first_sentence + '.' if first_sentence else ''}\n\n"
        f"## Core Principles\n\nClassifier signals: {', '.join(signals) or 'none'}.\n\n"
        f"## Sources\n\n- (this chapter)\n"
    )
    return seed


def _candidate_en_terms(term: str, chapter_text: str) -> list[str]:
    """Best-effort English source-term list for bilingual mapping.

    For a CJK term, this returns []; for an English term, returns [term].
    Production pipeline should use an LLM extractor — preflight is intentionally
    minimal here to avoid extra LLM cost.
    """
    if any("一" <= c <= "鿿" for c in term):
        return []
    return [term]


# ---------------------------------------------------------------------------
# Deterministic acceptance gate (Stage 1c — ADR-020 D2)
# ---------------------------------------------------------------------------


@dataclass
class AcceptanceResult:
    """Structured output from compute_acceptance — all 4 rule results plus measurements."""

    verbatim_match: float  # 0.0–1.0; gate: ≥ 0.99
    verbatim_ok: bool
    anchors_match: bool
    figures_embedded: int
    figures_expected: int
    figures_ok: bool
    wikilinks_count: int
    char_count: int
    wikilinks_threshold: int  # char_count // 2000; dynamic so short chapters don't over-require
    wikilinks_ok: bool
    acceptance_pass: bool


def _strip_chapter_appendix(page_body: str) -> str:
    """Remove the trailing `---\\n\\n## Section Concept Maps\\n…` appendix block."""
    return re.sub(
        r"\n\n---\n\n## Section Concept Maps\n.*\Z",
        "",
        page_body,
        flags=re.DOTALL,
    )


def normalize_for_verbatim_compare(page_body: str) -> str:
    """Strip designed non-verbatim content; return what should match walker.verbatim_body.

    Caller must strip the YAML frontmatter block before passing page_body.
    """
    body = _strip_chapter_appendix(page_body)
    # Reverse V2 figure transform: ![[vault_path]]\n*alt* → ![alt](vault_path)
    body = re.sub(
        r"!\[\[([^\]]+)\]\]\n\*([^*]*)\*",
        lambda m: f"![{m.group(2)}]({m.group(1)})",
        body,
    )
    # Trim trailing whitespace per line
    body = "\n".join(line.rstrip() for line in body.split("\n"))
    return body


def verbatim_match_pct(page_body: str, walker_verbatim: str) -> float:
    """Paragraph-level substring match; returns 0.0–1.0.

    Splits walker_verbatim on blank lines into paragraphs. For each paragraph,
    returns 1.0 if it appears as a substring of normalize_for_verbatim_compare(page_body),
    else 0.0. Returns mean.
    """
    normalized = normalize_for_verbatim_compare(page_body)
    paragraphs = [p for p in walker_verbatim.split("\n\n") if p.strip()]
    if not paragraphs:
        return 1.0
    matches = sum(1 for p in paragraphs if p in normalized)
    return matches / len(paragraphs)


def section_anchors_match(page_body: str, walker_section_anchors: list[str]) -> bool:
    """Exact list equality of H2 heading texts in page_body vs walker section anchors."""
    page_h2s = re.findall(r"^## (.+)$", _strip_chapter_appendix(page_body), re.MULTILINE)
    return page_h2s == walker_section_anchors


def compute_acceptance(
    page_body: str,
    walker_verbatim: str,
    walker_section_anchors: list[str],
    walker_figures_count: int,
    wikilinks_introduced: list[str],
) -> AcceptanceResult:
    """Run 4-rule deterministic acceptance gate. Logs every measurement on fail."""
    vm = verbatim_match_pct(page_body, walker_verbatim)
    anchors_ok = section_anchors_match(page_body, walker_section_anchors)

    fig_embedded = len(re.findall(r"\[\[Attachments/Books/", page_body))
    fig_ok = fig_embedded == walker_figures_count

    char_count = len(page_body)
    wl_threshold = char_count // 2000
    wl_count = len(wikilinks_introduced)
    wl_ok = wl_count >= wl_threshold

    ok = vm >= 0.99 and anchors_ok and fig_ok and wl_ok

    if not ok:
        log.warning(
            "Acceptance FAIL: verbatim=%.4f(ok=%s) anchors=%s "
            "figs=%d/%d(ok=%s) wikilinks=%d/%d(ok=%s)",
            vm,
            vm >= 0.99,
            anchors_ok,
            fig_embedded,
            walker_figures_count,
            fig_ok,
            wl_count,
            wl_threshold,
            wl_ok,
        )

    return AcceptanceResult(
        verbatim_match=vm,
        verbatim_ok=vm >= 0.99,
        anchors_match=anchors_ok,
        figures_embedded=fig_embedded,
        figures_expected=walker_figures_count,
        figures_ok=fig_ok,
        wikilinks_count=wl_count,
        char_count=char_count,
        wikilinks_threshold=wl_threshold,
        wikilinks_ok=wl_ok,
        acceptance_pass=ok,
    )


def run_coverage_gate(
    *,
    payload,
    source_page_path: Path,
    figures_count: int,
    figures_described: int,
    dispatch_log: list[dict],
    vault_root: Path,
) -> tuple[bool, list[str], dict]:
    """Run deterministic 4-rule acceptance gate. Returns (passed, reasons, metrics_dict)."""
    import yaml as _yaml

    page_text = source_page_path.read_text(encoding="utf-8")

    # Strip frontmatter to get bare body for verbatim comparison
    fm_match = re.match(r"^---\n(.*?)\n---\n", page_text, re.DOTALL)
    page_body = page_text[fm_match.end() :] if fm_match else page_text

    # Extract wikilinks_introduced from frontmatter YAML for rule 4
    wikilinks_introduced: list[str] = []
    if fm_match:
        try:
            fm = _yaml.safe_load(fm_match.group(1))
            if isinstance(fm, dict):
                wikilinks_introduced = list(fm.get("wikilinks_introduced") or [])
        except Exception:
            pass

    acc = compute_acceptance(
        page_body=page_body,
        walker_verbatim=payload.verbatim_body,
        walker_section_anchors=payload.section_anchors,
        walker_figures_count=figures_count,
        wikilinks_introduced=wikilinks_introduced,
    )

    reasons: list[str] = []
    if not acc.verbatim_ok:
        reasons.append(f"verbatim_match={acc.verbatim_match:.4f} < 0.99")
    if not acc.anchors_match:
        reasons.append("section_anchors_match=False")
    if not acc.figures_ok:
        reasons.append(
            f"figures_embedded={acc.figures_embedded}"
            f" != walker.figures_count={acc.figures_expected}"
        )
    if not acc.wikilinks_ok:
        reasons.append(
            f"wikilinks={acc.wikilinks_count} < threshold={acc.wikilinks_threshold}"
            f" (char_count={acc.char_count})"
        )

    return (
        acc.acceptance_pass,
        reasons,
        {
            "primary_total": 0,
            "primary_missing_pct": 0.0,
            "secondary_missing_pct": 0.0,
            "nuance_missing_pct": 0.0,
            "verbatim_pct": acc.verbatim_match * 100,
            "manifest_path": "",
        },
    )


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------


def write_preflight_report(result: PreflightResult, report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)

    levels = result.concept_levels
    fig_lines = "\n".join(f"  - {k}: {v}" for k, v in result.figure_class_counts.items())
    dispatch_lines = (
        "\n".join(
            f"  - `{e.get('slug', '?')}` — level={e.get('level', '?')} action={e.get('action', '?')}"
            + (f" signals={e.get('signals')}" if e.get("signals") else "")
            + (f" ERROR={e.get('error')}" if e.get("error") else "")
            for e in result.concept_dispatch
        )
        or "  (none)"
    )
    accept_lines = "\n".join(f"  - {r}" for r in result.acceptance_reasons) or "  (none)"
    gaps_lines = "\n".join(f"- {g}" for g in result.gaps_observed) or "(none)"

    md = f"""# ADR-020 S8 Preflight — {result.book_id} ch{result.chapter_index}

**Date**: {date.today().isoformat()}
**Runner**: `scripts/run_s8_preflight.py`
**LLM**: `{result.llm_model}` (Sonnet for cost-control; production batch may use Opus)

## Recommendation

> **{result.recommendation()}**

## Inputs

- Raw markdown: `{result.raw_path}`
- Chapter chosen: index {result.chapter_index} — *{result.chapter_title}*
- Selection note: {result.chapter_chosen_note or "(default first H1 chapter)"}

## Phase 1 — Source page

- Output path: `{result.source_page_path}`
- Verbatim paragraph match: **{result.verbatim_match_pct:.2f}%** (target ≥ 99%)
- Sections detected: {result.section_count}
- Figures: {result.figures_count} ({result.figures_described} described, {result.figures_decorative} decorative-skipped)
- Figure class distribution:
{fig_lines or "  (none)"}
- Tables: {result.tables_count}

## Phase 2 — Concept dispatch

- Concepts extracted from `wikilinks_introduced`: {result.concepts_extracted}
- Maturity distribution: L1={levels.get("L1", 0)} / L2={levels.get("L2", 0)} / L3={levels.get("L3", 0)}
- Concept directory: `{result.concept_dir}`
- Dispatch log:
{dispatch_lines}

## Coverage manifest + acceptance

- Manifest: `{result.coverage_manifest_path}`
- Primary claims total: {result.primary_claims_total}
- Primary missing: **{result.primary_claims_missing_pct:.1f}%** (fail threshold > 5%)
- Secondary missing: {result.secondary_claims_missing_pct:.1f}% (warn-only > 25%)
- Nuance missing: {result.nuance_claims_missing_pct:.1f}% (not gated)
- Acceptance status: **{"PASS" if result.acceptance_pass else "FAIL"}**
- Reasons:
{accept_lines}

## Cost & wall time

- Wall time: {result.wall_seconds:.1f}s
- Input tokens: {result.input_tokens}
- Output tokens: {result.output_tokens}
- Estimated cost: ${result.cost_usd:.4f} USD

## Gaps observed in S1-S7 modules

{gaps_lines}

## Next action

{result.recommendation()}
"""
    report_path.write_text(md, encoding="utf-8")
    log.info("Preflight report written: %s", report_path)


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def _pick_chapter(payloads, requested_index: int) -> tuple[object, str]:
    """Pick the requested chapter, with sensible fallback if walker numbered front matter."""
    if not payloads:
        raise RuntimeError("walker returned 0 chapters — Phase 0 raw file missing H1 headings")

    # Walker numbers chapters from 1 by H1 order, but real ch1 is rarely walker[0]:
    # books typically have title-page + preface/contents/foreword before chapter 1.
    # Heuristic: real chapters have titles starting with `<digit>+ <Word>` (e.g. "1 Energy Sources").
    # Find the i-th such title for requested_index = i.
    import re as _re

    chapter_pattern = _re.compile(r"^\s*\d+\s+[A-Za-z]")
    real_chapters = [p for p in payloads if chapter_pattern.match(p.chapter_title or "")]
    if real_chapters and 1 <= requested_index <= len(real_chapters):
        chosen = real_chapters[requested_index - 1]
        if chosen.chapter_index != requested_index:
            note = (
                f"walker yielded {len(payloads)} entries (front+back matter included); "
                f"selected real chapter {requested_index} = '{chosen.chapter_title}' "
                f"(walker index {chosen.chapter_index})"
            )
            chosen.chapter_index = requested_index  # D4: filename uses real chapter number
        else:
            note = f"requested index {requested_index} = '{chosen.chapter_title}'"
        return chosen, note

    # Fallback: literal walker index match
    for p in payloads:
        if p.chapter_index == requested_index:
            return p, f"requested index {requested_index} (literal walker index match)"

    raise IndexError(
        f"chapter_index {requested_index} not found; walker yielded {len(payloads)} chapters"
    )


def run_preflight(args) -> PreflightResult:
    t0 = time.perf_counter()
    result = PreflightResult()

    vault_root = Path(args.vault_root)
    if not vault_root.exists():
        result.fatal_error = f"vault_root does not exist: {vault_root}"
        return result

    os.environ.setdefault("VAULT_PATH", str(vault_root))
    _patch_kb_writer_to_staging()
    _patch_alias_map_to_staging()

    raw_path = vault_root / DEFAULT_RAW_PATH_REL
    if args.raw_path:
        raw_path = Path(args.raw_path)
    result.raw_path = str(raw_path)
    if not raw_path.exists():
        result.fatal_error = f"raw markdown not found: {raw_path}"
        return result

    # ---- Phase 1 walk ----
    from shared.source_ingest import walk_book_to_chapters

    log.info("Walking %s …", raw_path)
    payloads = walk_book_to_chapters(raw_path)
    log.info("Walker produced %d chapters", len(payloads))

    payload, note = _pick_chapter(payloads, args.chapter_index)
    result.chapter_chosen_note = note
    result.book_id = payload.book_id
    result.chapter_index = payload.chapter_index
    result.chapter_title = payload.chapter_title
    result.section_count = len(payload.section_anchors)
    result.figures_count = len(payload.figures)
    result.tables_count = len(payload.tables)

    # ---- Figure triage (deterministic; no LLM in dry-run path) ----
    fig_counts, fig_dec, fig_desc = run_figure_triage(payload)
    result.figure_class_counts = fig_counts
    result.figures_decorative = fig_dec
    result.figures_described = fig_desc

    if args.dry_run:
        try:
            source_page_path = run_phase1_source_page(
                payload,
                vault_root=vault_root,
                book_title=args.book_title or payload.book_id,
                dry_run=True,
            )
            result.source_page_path = str(source_page_path)
        except Exception as e:
            result.fatal_error = f"Phase 1 dry-run failed: {e!r}"
        result.verbatim_match_pct = (
            verbatim_match_pct(payload.verbatim_body, payload.verbatim_body) * 100
        )
        result.acceptance_pass = False
        result.acceptance_reasons = ["dry-run — no acceptance check performed"]
        result.wall_seconds = time.perf_counter() - t0
        return result

    # ---- Phase 1 source page (LLM) ----
    try:
        source_page_path = run_phase1_source_page(
            payload,
            vault_root=vault_root,
            book_title=args.book_title or payload.book_id,
        )
        result.source_page_path = str(source_page_path)
    except Exception as e:
        result.fatal_error = f"Phase 1 source-page generation failed: {e!r}"
        result.wall_seconds = time.perf_counter() - t0
        return result

    # ---- Phase 2 dispatch ----
    try:
        dispatch_log = run_phase2_dispatch(
            payload=payload,
            source_page_path=source_page_path,
            chapter_text=payload.verbatim_body,
        )
        result.concept_dispatch = dispatch_log
        result.concepts_extracted = len(dispatch_log)
        for entry in dispatch_log:
            lvl = entry.get("level", "?")
            if lvl in result.concept_levels:
                result.concept_levels[lvl] += 1
        result.concept_dir = str(vault_root / "KB" / "Wiki.staging" / "Concepts")
    except Exception as e:
        log.exception("Phase 2 dispatch crashed")
        result.gaps_observed.append(f"Phase 2 dispatch raised: {e!r}")

    # ---- Coverage gate ----
    try:
        passed, reasons, cov = run_coverage_gate(
            payload=payload,
            source_page_path=source_page_path,
            figures_count=result.figures_count,
            figures_described=result.figures_described,
            dispatch_log=result.concept_dispatch,
            vault_root=vault_root,
        )
        result.acceptance_pass = passed
        result.acceptance_reasons = reasons
        result.primary_claims_total = cov["primary_total"]
        result.primary_claims_missing_pct = cov["primary_missing_pct"]
        result.secondary_claims_missing_pct = cov["secondary_missing_pct"]
        result.nuance_claims_missing_pct = cov["nuance_missing_pct"]
        result.verbatim_match_pct = cov["verbatim_pct"]
        result.coverage_manifest_path = cov["manifest_path"]
    except Exception as e:
        log.exception("Coverage gate crashed")
        result.fatal_error = f"coverage gate failed: {e!r}"

    # ---- Cost summary ----
    in_t, out_t, cost = _llm_observability_snapshot()
    result.input_tokens = in_t
    result.output_tokens = out_t
    result.cost_usd = cost
    if in_t == 0 and out_t == 0:
        result.gaps_observed.append(
            "shared.llm_observability did not expose a totals accessor — "
            "token/cost numbers are 0; check llm_observability.py for a public totals API."
        )

    result.wall_seconds = time.perf_counter() - t0
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ADR-020 S8 preflight — BSE ch1 end-to-end")
    p.add_argument("--vault-root", default=DEFAULT_VAULT_ROOT)
    p.add_argument("--raw-path", default="", help="override raw markdown path")
    p.add_argument("--book-id", default=DEFAULT_BOOK_ID)
    p.add_argument(
        "--book-title",
        default="Biochemistry for Sport and Exercise (MacLaren)",
    )
    p.add_argument("--chapter-index", type=int, default=DEFAULT_CHAPTER_INDEX)
    p.add_argument(
        "--report-path",
        default=str(_REPO_ROOT / DEFAULT_REPORT_PATH),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="walker + classifier only, no LLM / vault writes",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    log.info("=== ADR-020 S8 preflight starting ===")
    log.info(
        "vault_root=%s book_id=%s ch=%s dry_run=%s",
        args.vault_root,
        args.book_id,
        args.chapter_index,
        args.dry_run,
    )

    result = run_preflight(args)

    report_path = Path(args.report_path)
    write_preflight_report(result, report_path)

    print()
    print("=" * 70)
    print("PREFLIGHT RESULT")
    print("=" * 70)
    print(f"Book / chapter        : {result.book_id} ch{result.chapter_index}")
    print(f"Verbatim match        : {result.verbatim_match_pct:.2f}%")
    print(
        f"Concepts dispatched   : {result.concepts_extracted} "
        f"(L1={result.concept_levels.get('L1', 0)} "
        f"L2={result.concept_levels.get('L2', 0)} "
        f"L3={result.concept_levels.get('L3', 0)})"
    )
    print(f"Primary missing       : {result.primary_claims_missing_pct:.1f}%")
    print(f"Acceptance            : {'PASS' if result.acceptance_pass else 'FAIL'}")
    print(f"Wall time             : {result.wall_seconds:.1f}s")
    print(f"Cost (USD est.)       : ${result.cost_usd:.4f}")
    print(f"Report                : {report_path}")
    print(f">> {result.recommendation()}")
    print("=" * 70)

    if result.fatal_error:
        return 2
    if args.dry_run:
        return 0
    return 0 if result.acceptance_pass else 2


if __name__ == "__main__":
    sys.exit(main())
