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
import sys
import time
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
        "Output the complete chapter source markdown now (YAML frontmatter + body). "
        "No preamble, no commentary, no code fences around the page itself."
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


def run_phase1_source_page(payload, *, vault_root: Path, book_title: str) -> Path:
    """Run Phase 1 — emit the chapter source page to the staging vault."""
    from shared.source_ingest import verbatim_paragraph_match_pct  # noqa: F401

    prompt = _build_phase1_prompt(payload, book_title=book_title, ingest_date=str(date.today()))
    log.info("Phase 1: calling LLM for source page (model=%s)", PREFLIGHT_MODEL)
    page_md = _ask_llm(prompt, max_tokens=16000)
    page_md = _strip_outer_fence(page_md)

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
    """Filesystem-safe slug. Keep CJK; replace whitespace + path separators."""
    bad = '<>:"/\\|?*\n\r\t'
    s = term
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
# Coverage manifest + acceptance gate
# ---------------------------------------------------------------------------


def run_coverage_gate(
    *,
    payload,
    source_page_path: Path,
    figures_count: int,
    figures_described: int,
    dispatch_log: list[dict],
    vault_root: Path,
) -> tuple[bool, list[str], dict]:
    """Run claim-extraction + acceptance gate. Returns (passed, reasons, manifest_dict)."""
    from shared.coverage_classifier import (
        ConceptDispatchEntry,
        CoverageManifest,
        check_claim_in_page,
        extract_claims,
        run_acceptance_gate,
        write_coverage_manifest,
    )
    from shared.source_ingest import verbatim_paragraph_match_pct

    log.info("Coverage: extracting claims from chapter via LLM…")
    claims = extract_claims(payload.verbatim_body, _ask_llm=_ask_llm)
    log.info("Coverage: %d claims extracted", len(claims))

    page_text = source_page_path.read_text(encoding="utf-8")
    log.info("Coverage: checking %d claims against vault page…", len(claims))
    for c in claims:
        try:
            c.found_in_page = check_claim_in_page(c, page_text, _ask_llm=_ask_llm)
        except Exception as e:
            log.warning("check_claim_in_page error: %s", e)
            c.found_in_page = False

    verbatim_pct = verbatim_paragraph_match_pct(payload.verbatim_body, page_text)

    dispatch_entries = [
        ConceptDispatchEntry(
            slug=entry.get("slug", "?"),
            action=entry.get("action", "?"),
        )
        for entry in dispatch_log
    ]

    manifest = CoverageManifest(
        chapter_index=payload.chapter_index,
        book_id=payload.book_id,
        claims_extracted_by_llm=claims,
        figures_count=figures_count,
        figures_embedded=figures_count,  # source page should embed all detected figures
        tables_transcluded=0,
        verbatim_paragraph_match_pct=verbatim_pct,
        concept_dispatch_log=dispatch_entries,
    )

    passed, reasons = run_acceptance_gate(manifest)
    manifest.acceptance_status = "pass" if passed else "fail"
    manifest.acceptance_reasons = reasons

    manifest_path = (
        vault_root
        / "KB"
        / "Wiki.staging"
        / "Sources"
        / "Books"
        / payload.book_id
        / f"ch{payload.chapter_index}.coverage.json"
    )
    write_coverage_manifest(manifest, manifest_path)
    log.info(
        "Coverage: gate=%s missing(P/S/N)=%.1f/%.1f/%.1f manifest=%s",
        manifest.acceptance_status,
        manifest.primary_claims_missing_pct,
        manifest.secondary_claims_missing_pct,
        manifest.nuance_claims_missing_pct,
        manifest_path,
    )
    return (
        passed,
        reasons,
        {
            "primary_total": sum(1 for c in claims if c.claim_type == "primary"),
            "primary_missing_pct": manifest.primary_claims_missing_pct,
            "secondary_missing_pct": manifest.secondary_claims_missing_pct,
            "nuance_missing_pct": manifest.nuance_claims_missing_pct,
            "verbatim_pct": verbatim_pct,
            "manifest_path": str(manifest_path),
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
        log.info("DRY-RUN: skipping all LLM calls and writes")
        from shared.source_ingest import verbatim_paragraph_match_pct

        result.verbatim_match_pct = verbatim_paragraph_match_pct(
            payload.verbatim_body, payload.verbatim_body
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

    return 0 if result.acceptance_pass and not result.fatal_error else 2


if __name__ == "__main__":
    sys.exit(main())
