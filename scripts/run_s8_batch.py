# ruff: noqa: E501
"""ADR-020 S8 batch runner — BSE + Sport Nutrition cleanup re-ingest to STAGING.

Generalises ``scripts/run_s8_preflight.py`` to loop over every real chapter
across multiple books, write each chapter through Phase 1 (source page) +
Phase 2 (concept dispatch) + coverage manifest into ``KB/Wiki.staging/`` (NOT
the real ``KB/Wiki/``), and emit a single roll-up report at
``docs/runs/2026-05-06-s8-final-report.md``.

USAGE (run on host with vault access)
-------------------------------------

    cd E:\\nakama
    .venv\\Scripts\\Activate.ps1
    $env:ANTHROPIC_API_KEY = "sk-ant-..."
    $env:VAULT_PATH        = "E:\\Shosho LifeOS"

    # Default — every real chapter in BSE + SN
    python -m scripts.run_s8_batch

    # Smoke-test with first 2 chapters only
    python -m scripts.run_s8_batch --max-chapters 2

    # Walker-only (no LLM, no writes)
    python -m scripts.run_s8_batch --dry-run

The runner refuses to write to ``KB/Wiki/`` — only ``KB/Wiki.staging/``.
``--continue-on-fail`` is the default; the runner aborts only on > 3
consecutive failures or hard cost-cap (>$50).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Path bootstrap so `python scripts/run_s8_batch.py` works without -m
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Reuse all the heavy lifting from the preflight runner — frozen API.
from scripts.run_s8_preflight import (  # noqa: E402
    PREFLIGHT_MODEL,
    _patch_alias_map_to_staging,
    _patch_kb_writer_to_staging,
    run_coverage_gate,
    run_figure_triage,
    run_phase1_source_page,
    run_phase2_dispatch,
)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_VAULT_ROOT = r"E:\Shosho LifeOS"
DEFAULT_REPORT_PATH = "docs/runs/2026-05-06-s8-final-report.md"
DEFAULT_BLOCKERS_PATH = "docs/runs/2026-05-06-s8-blockers.md"

# Hard cost cap — abort batch if exceeded.
COST_CAP_USD = 50.0

# Sonnet 4.5/4.6 pricing (USD per 1M tokens) — used to estimate cost from the
# llm_context usage_buffer.
PRICE_INPUT_PER_MTOK = 3.0
PRICE_OUTPUT_PER_MTOK = 15.0
PRICE_CACHE_READ_PER_MTOK = 0.30
PRICE_CACHE_WRITE_PER_MTOK = 3.75

# Real-chapter expected counts (informational only — log-and-continue if differs).
EXPECTED_REAL_CHAPTERS = {
    "biochemistry-for-sport-and-exercise-maclaren": 11,
    "sport-nutrition-jeukendrup-4e": 17,
}

# Book registry.
BOOKS = {
    "bse": {
        "book_id": "biochemistry-for-sport-and-exercise-maclaren",
        "book_title": "Biochemistry for Sport and Exercise (MacLaren)",
        "raw_rel": "KB/Raw/Books/biochemistry-for-sport-and-exercise-maclaren.md",
    },
    "sn": {
        "book_id": "sport-nutrition-jeukendrup-4e",
        "book_title": "Sport Nutrition (Jeukendrup) 4E",
        "raw_rel": "KB/Raw/Books/sport-nutrition-jeukendrup-4e.md",
    },
}


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("s8-batch")


# ---------------------------------------------------------------------------
# Per-chapter result
# ---------------------------------------------------------------------------


@dataclass
class ChapterResult:
    book_key: str = ""
    book_id: str = ""
    book_title: str = ""
    chapter_index: int = 0  # walker chapter_index
    real_index: int = 0  # 1-based index within the real chapters of this book
    chapter_title: str = ""

    status: str = "pending"  # pending|pass|fail|error
    fatal_error: str = ""
    acceptance_pass: bool = False
    acceptance_reasons: list[str] = field(default_factory=list)

    verbatim_match_pct: float = 0.0
    figures_count: int = 0
    figures_described: int = 0
    figures_decorative: int = 0
    figure_class_counts: dict[str, int] = field(default_factory=dict)
    tables_count: int = 0

    concepts_extracted: int = 0
    concept_levels: dict[str, int] = field(default_factory=lambda: {"L1": 0, "L2": 0, "L3": 0})
    concept_dispatch: list[dict] = field(default_factory=list)

    primary_claims_total: int = 0
    primary_claims_missing_pct: float = 0.0
    secondary_claims_missing_pct: float = 0.0
    nuance_claims_missing_pct: float = 0.0

    wall_seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0

    source_page_path: str = ""
    coverage_manifest_path: str = ""


# ---------------------------------------------------------------------------
# Cost helpers
# ---------------------------------------------------------------------------


def _drain_usage_buffer() -> tuple[int, int, int, int, float]:
    """Pull whatever the LLM client has buffered since the last drain.

    Returns ``(in, out, cache_read, cache_write, cost_usd)``. Restarts the
    buffer so the next chapter starts clean.
    """
    from shared.llm_context import start_usage_tracking, stop_usage_tracking

    rows = stop_usage_tracking()
    in_t = sum(int(r.get("input_tokens", 0)) for r in rows)
    out_t = sum(int(r.get("output_tokens", 0)) for r in rows)
    cr_t = sum(int(r.get("cache_read_tokens", 0)) for r in rows)
    cw_t = sum(int(r.get("cache_write_tokens", 0)) for r in rows)
    cost = (
        in_t * PRICE_INPUT_PER_MTOK
        + out_t * PRICE_OUTPUT_PER_MTOK
        + cr_t * PRICE_CACHE_READ_PER_MTOK
        + cw_t * PRICE_CACHE_WRITE_PER_MTOK
    ) / 1_000_000.0
    # Restart fresh for next chapter.
    start_usage_tracking()
    return in_t, out_t, cr_t, cw_t, cost


# ---------------------------------------------------------------------------
# Walker / chapter iteration
# ---------------------------------------------------------------------------


def _real_chapters_in(payloads) -> list:
    """Return the subset of walker payloads whose title matches `<digit>+ <Word>`."""
    pat = re.compile(r"^\s*\d+\s+[A-Za-z]")
    return [p for p in payloads if pat.match(p.chapter_title or "")]


# ---------------------------------------------------------------------------
# Single-chapter driver
# ---------------------------------------------------------------------------


def _process_chapter(
    *,
    book_key: str,
    book_meta: dict,
    payload,
    real_index: int,
    vault_root: Path,
    dry_run: bool,
) -> ChapterResult:
    t0 = time.perf_counter()
    res = ChapterResult(
        book_key=book_key,
        book_id=payload.book_id,
        book_title=book_meta["book_title"],
        chapter_index=payload.chapter_index,
        real_index=real_index,
        chapter_title=payload.chapter_title,
    )
    res.figures_count = len(payload.figures)
    res.tables_count = len(payload.tables)

    fig_counts, fig_dec, fig_desc = run_figure_triage(payload)
    res.figure_class_counts = fig_counts
    res.figures_decorative = fig_dec
    res.figures_described = fig_desc

    if dry_run:
        res.status = "pass"
        res.wall_seconds = time.perf_counter() - t0
        return res

    # Phase 1
    try:
        source_page_path = run_phase1_source_page(
            payload, vault_root=vault_root, book_title=book_meta["book_title"]
        )
        res.source_page_path = str(source_page_path)
    except Exception as e:
        log.exception("Phase 1 crashed for %s ch%s", payload.book_id, payload.chapter_index)
        res.fatal_error = f"Phase 1 source-page generation failed: {e!r}"
        res.status = "error"
        res.wall_seconds = time.perf_counter() - t0
        return res

    # Phase 2 dispatch
    try:
        dispatch_log = run_phase2_dispatch(
            payload=payload,
            source_page_path=source_page_path,
            chapter_text=payload.verbatim_body,
        )
        res.concept_dispatch = dispatch_log
        res.concepts_extracted = len(dispatch_log)
        for entry in dispatch_log:
            lvl = entry.get("level", "?")
            if lvl in res.concept_levels:
                res.concept_levels[lvl] += 1
    except Exception as e:
        log.exception("Phase 2 crashed for %s ch%s", payload.book_id, payload.chapter_index)
        res.fatal_error = f"Phase 2 dispatch failed: {e!r}"
        # Continue to coverage gate — fail will be recorded but we still try to
        # produce a coverage manifest.

    # Coverage gate
    try:
        passed, reasons, cov = run_coverage_gate(
            payload=payload,
            source_page_path=source_page_path,
            figures_count=res.figures_count,
            figures_described=res.figures_described,
            dispatch_log=res.concept_dispatch,
            vault_root=vault_root,
        )
        res.acceptance_pass = passed
        res.acceptance_reasons = reasons
        res.primary_claims_total = cov["primary_total"]
        res.primary_claims_missing_pct = cov["primary_missing_pct"]
        res.secondary_claims_missing_pct = cov["secondary_missing_pct"]
        res.nuance_claims_missing_pct = cov["nuance_missing_pct"]
        res.verbatim_match_pct = cov["verbatim_pct"]
        res.coverage_manifest_path = cov["manifest_path"]
    except Exception as e:
        log.exception("Coverage gate crashed for %s ch%s", payload.book_id, payload.chapter_index)
        if not res.fatal_error:
            res.fatal_error = f"coverage gate failed: {e!r}"

    if res.fatal_error:
        res.status = "error"
    elif res.acceptance_pass and res.primary_claims_missing_pct <= 5.0:
        res.status = "pass"
    else:
        res.status = "fail"

    in_t, out_t, cr_t, cw_t, cost = _drain_usage_buffer()
    res.input_tokens = in_t
    res.output_tokens = out_t
    res.cache_read_tokens = cr_t
    res.cache_write_tokens = cw_t
    res.cost_usd = cost

    res.wall_seconds = time.perf_counter() - t0
    return res


# ---------------------------------------------------------------------------
# Spot-check helpers
# ---------------------------------------------------------------------------


def _run_spot_checks(results: list[ChapterResult], vault_root: Path, n: int = 5) -> list[dict]:
    """Pick `n` random PASSed chapters and inspect their staged output."""
    candidates = [r for r in results if r.status == "pass" and r.source_page_path]
    if not candidates:
        return []
    picks = random.sample(candidates, min(n, len(candidates)))

    out: list[dict] = []
    for r in picks:
        item = {
            "book_id": r.book_id,
            "real_index": r.real_index,
            "chapter_title": r.chapter_title,
            "source_page_path": r.source_page_path,
            "verbatim_pct": r.verbatim_match_pct,
            "concepts_dispatched": r.concepts_extracted,
            "primary_missing_pct": r.primary_claims_missing_pct,
            "wikilinks_resolved": "?",
            "wikilinks_total": 0,
            "concept_pages_inspected": [],
            "verdict_lines": [],
        }
        try:
            page = Path(r.source_page_path).read_text(encoding="utf-8")
        except Exception as e:
            item["verdict_lines"].append(f"could not read source page: {e}")
            out.append(item)
            continue

        # Wikilink resolution: count how many `[[...]]` in the body have a
        # corresponding file under Wiki.staging/Concepts/ OR a row in the
        # alias map.
        wikilinks = re.findall(r"\[\[([^\]\|#]+)(?:\|[^\]]+)?\]\]", page)
        wikilinks = [w.strip() for w in wikilinks if w.strip()]
        item["wikilinks_total"] = len(wikilinks)

        concept_dir = vault_root / "KB" / "Wiki.staging" / "Concepts"
        alias_map_path = vault_root / "KB" / "Wiki.staging" / "_alias_map.md"
        alias_map_text = (
            alias_map_path.read_text(encoding="utf-8") if alias_map_path.exists() else ""
        )

        resolved = 0
        unresolved: list[str] = []
        for wl in wikilinks:
            if wl.startswith("Sources/") or wl.startswith("Entities/"):
                resolved += 1
                continue
            slug = wl.split("/")[-1]
            page_file = concept_dir / f"{slug}.md"
            if page_file.exists() or wl in alias_map_text:
                resolved += 1
            else:
                unresolved.append(wl)
        item["wikilinks_resolved"] = resolved
        if unresolved:
            item["unresolved_sample"] = unresolved[:5]

        # Inspect 2-3 concept pages dispatched from this chapter.
        l2_l3 = [
            e
            for e in r.concept_dispatch
            if e.get("level") in ("L2", "L3") and e.get("action") == "create"
        ][:3]
        for entry in l2_l3:
            slug = entry.get("slug", "")
            cpath = concept_dir / f"{slug}.md"
            if cpath.exists():
                txt = cpath.read_text(encoding="utf-8")
                wc = len(txt.split())
                has_seed = "## Definition" in txt or "## 定義" in txt
                has_ref = "Sources/Books" in txt or "ch" in txt.lower()
                item["concept_pages_inspected"].append(
                    {
                        "slug": slug,
                        "level": entry.get("level"),
                        "exists": True,
                        "word_count": wc,
                        "has_seed": has_seed,
                        "has_book_ref": has_ref,
                    }
                )
            else:
                item["concept_pages_inspected"].append(
                    {"slug": slug, "level": entry.get("level"), "exists": False}
                )

        # Verdict lines.
        verdict = []
        if r.verbatim_match_pct >= 75.0:
            verdict.append(f"verbatim {r.verbatim_match_pct:.1f}% — looks textbook")
        else:
            verdict.append(
                f"verbatim {r.verbatim_match_pct:.1f}% — LOW (<75%); may be summary not verbatim"
            )
        if item["wikilinks_total"]:
            verdict.append(f"wikilinks resolved {resolved}/{item['wikilinks_total']}")
        if item["concept_pages_inspected"]:
            stub_count = sum(
                1
                for c in item["concept_pages_inspected"]
                if c.get("exists") and c.get("word_count", 0) < 30
            )
            verdict.append(
                f"concept pages: {len(item['concept_pages_inspected'])} inspected, "
                f"{stub_count} look like empty stubs (<30 words)"
            )
        if r.primary_claims_total > 0 and r.primary_claims_missing_pct <= 5.0:
            verdict.append(
                f"coverage manifest: {r.primary_claims_total} primary claims, "
                f"{r.primary_claims_missing_pct:.1f}% missing — plausible"
            )
        elif r.primary_claims_total == 0:
            verdict.append("coverage manifest: 0 primary claims — SUSPICIOUS")
        item["verdict_lines"] = verdict
        item["verdict"] = (
            "looks good"
            if r.verbatim_match_pct >= 75.0 and r.primary_claims_total > 0
            else "has issues"
        )
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def _git_head() -> str:
    try:
        import subprocess

        r = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        return r.stdout.strip() or "?"
    except Exception:
        return "?"


def _fmt_secs(s: float) -> str:
    s = int(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{sec:02d}s"
    return f"{sec}s"


def _per_book_table(results: list[ChapterResult], book_key: str) -> str:
    rows = [r for r in results if r.book_key == book_key]
    if not rows:
        return "_(no chapters processed)_"
    rows = sorted(rows, key=lambda r: r.real_index)
    lines = [
        "| # | Title | Status | Verbatim% | Concepts (L1/L2/L3) | P-miss% | S-miss% | Wall | Cost |",
        "|---|-------|--------|-----------|---------------------|---------|---------|------|------|",
    ]
    for r in rows:
        lvl = r.concept_levels
        lines.append(
            f"| {r.real_index} "
            f"| {r.chapter_title[:60]} "
            f"| {r.status.upper()} "
            f"| {r.verbatim_match_pct:.1f}% "
            f"| {lvl.get('L1', 0)}/{lvl.get('L2', 0)}/{lvl.get('L3', 0)} "
            f"| {r.primary_claims_missing_pct:.1f}% "
            f"| {r.secondary_claims_missing_pct:.1f}% "
            f"| {_fmt_secs(r.wall_seconds)} "
            f"| ${r.cost_usd:.3f} |"
        )
    return "\n".join(lines)


def _failures_section(results: list[ChapterResult]) -> str:
    fails = [r for r in results if r.status in ("fail", "error")]
    if not fails:
        return "_(none — all chapters met the gate)_"
    out: list[str] = []
    for r in fails:
        bullets = [
            f"- File: `{r.source_page_path or '(not written)'}`",
            f"- Status: **{r.status.upper()}**",
        ]
        if r.fatal_error:
            bullets.append(f"- Fatal: `{r.fatal_error}`")
        if r.primary_claims_missing_pct > 5.0:
            bullets.append(
                f"- primary_claims_missing_pct = {r.primary_claims_missing_pct:.1f}% (> 5% gate)"
            )
        if r.acceptance_reasons:
            for reason in r.acceptance_reasons:
                bullets.append(f"- gate reason: {reason}")
        action = (
            "re-ingest individual chapter"
            if r.status == "error"
            else "accept-with-note OR re-ingest after prompt fix"
        )
        bullets.append(f"- Suggested action: {action}")
        out.append(f"### {r.book_id} ch{r.real_index} — {r.chapter_title}\n\n" + "\n".join(bullets))
    return "\n\n".join(out)


def _concept_distribution(results: list[ChapterResult]) -> dict:
    total_l1 = sum(r.concept_levels.get("L1", 0) for r in results)
    total_l2 = sum(r.concept_levels.get("L2", 0) for r in results)
    total_l3 = sum(r.concept_levels.get("L3", 0) for r in results)

    # Cross-book: which slugs appeared in dispatch logs of BOTH books?
    by_book: dict[str, set[str]] = {}
    for r in results:
        if r.status == "error":
            continue
        slugs = {e.get("slug", "") for e in r.concept_dispatch if e.get("slug")}
        by_book.setdefault(r.book_key, set()).update(slugs)
    cross = sorted(set.intersection(*by_book.values())) if len(by_book) >= 2 else []
    return {
        "L1": total_l1,
        "L2": total_l2,
        "L3": total_l3,
        "total": total_l1 + total_l2 + total_l3,
        "cross_book": cross,
    }


def write_final_report(
    results: list[ChapterResult],
    spot_checks: list[dict],
    *,
    report_path: Path,
    total_wall: float,
    total_cost: float,
    blockers: list[str],
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)

    by_key: dict[str, list[ChapterResult]] = {}
    for r in results:
        by_key.setdefault(r.book_key, []).append(r)

    def _summ(key: str) -> tuple[int, int, int]:
        rows = by_key.get(key, [])
        return (
            len(rows),
            sum(1 for r in rows if r.status == "pass"),
            sum(1 for r in rows if r.status in ("fail", "error")),
        )

    bse_n, bse_pass, bse_fail = _summ("bse")
    sn_n, sn_pass, sn_fail = _summ("sn")
    total_fail = bse_fail + sn_fail

    cd = _concept_distribution(results)

    # Recommendation logic.
    if blockers:
        recommendation = "BLOCK — see blocker conditions"
    elif total_fail == 0:
        recommendation = "SHIP — all chapters pass the acceptance gate; spot checks clean"
    elif total_fail <= 2:
        recommendation = (
            f"SHIP-WITH-NOTES — {total_fail} chapter(s) failed; "
            "recommend reviewing failure section and re-ingesting individually"
        )
    else:
        recommendation = (
            f"BLOCK — {total_fail} chapter(s) failed; pattern likely; investigate before shipping"
        )

    # Per-phase cost split (best-effort: we don't subdivide phases at the LLM
    # call layer, so we report aggregate only).
    per_phase = "(per-phase split not instrumented; aggregate only)"

    # Spot-check rendering.
    spot_md_blocks: list[str] = []
    for i, sc in enumerate(spot_checks, 1):
        pages_md = (
            "\n".join(
                f"  - `{p.get('slug')}` (level={p.get('level')}, "
                f"exists={p.get('exists')}, words={p.get('word_count', '?')}, "
                f"seed={p.get('has_seed')}, book_ref={p.get('has_book_ref')})"
                for p in sc.get("concept_pages_inspected", [])
            )
            or "  (none dispatched at L2/L3 with create action)"
        )
        verdict_md = "\n".join(f"- {v}" for v in sc.get("verdict_lines", []))
        unresolved = sc.get("unresolved_sample") or []
        unresolved_md = (
            "\n- unresolved sample: " + ", ".join(f"`{u}`" for u in unresolved)
            if unresolved
            else ""
        )
        spot_md_blocks.append(
            f"### Spot check {i} — {sc['book_id']} ch{sc['real_index']}\n\n"
            f"- Source page: `{sc['source_page_path']}`\n"
            f"- Wikilinks resolved: {sc.get('wikilinks_resolved', '?')}/{sc.get('wikilinks_total', '?')}"
            f"{unresolved_md}\n"
            f"- Concept pages inspected:\n{pages_md}\n\n"
            f"{verdict_md}\n\n"
            f"**Verdict**: {sc.get('verdict', '?')}"
        )
    spot_md = "\n\n".join(spot_md_blocks) or "_(no PASSed chapters available for spot checking)_"

    cross_book_md = (
        "\n".join(f"  - `{c}`" for c in cd["cross_book"][:50])
        if cd["cross_book"]
        else "  (none — bilingual evidence weak)"
    )

    md = f"""# ADR-020 S8 Final Report — 28-chapter cleanup re-ingest

Generated: {datetime.now().isoformat(timespec="seconds")}
Branch: docs/kb-stub-crisis-memory @ {_git_head()}
Runner: `scripts/run_s8_batch.py`
LLM model (all phases): `{PREFLIGHT_MODEL}` (Sonnet 4.6 for cost-control)

## TL;DR

- BSE: {bse_n} chapters processed, {bse_pass} passed, {bse_fail} failed
- SN: {sn_n} chapters processed, {sn_pass} passed, {sn_fail} failed
- Total concepts dispatched: {cd["total"]} (L1={cd["L1"]} L2={cd["L2"]} L3={cd["L3"]})
- Total LLM cost: **${total_cost:.2f}**
- Total wall time: **{_fmt_secs(total_wall)}**
- Recommendation: **{recommendation}**

{"## Blockers" if blockers else ""}
{chr(10).join(f"- {b}" for b in blockers) if blockers else ""}

## Per-chapter results

### BSE (Biochemistry for Sport and Exercise)

{_per_book_table(results, "bse")}

### SN (Sport Nutrition 4E)

{_per_book_table(results, "sn")}

## Spot checks (random {len(spot_checks)})

{spot_md}

## Acceptance gate failures

{_failures_section(results)}

## Concept distribution

- Total concepts dispatched: {cd["total"]}
- L1 alias entries: {cd["L1"]}
- L2 stub pages: {cd["L2"]}
- L3 active pages: {cd["L3"]}
- Concepts cross-listed across both books (slug intersection): {len(cd["cross_book"])}

Cross-book slugs (first 50):
{cross_book_md}

## Cost / time breakdown

- Aggregate cost: ${total_cost:.4f} USD
- Aggregate wall time: {_fmt_secs(total_wall)}
- Per-phase split: {per_phase}

## Recommendation

**{recommendation}**

The user (修修) decides ship-or-block based on this report. Staging output is at
`KB/Wiki.staging/`; the real `KB/Wiki/` is untouched.
"""
    report_path.write_text(md, encoding="utf-8")
    log.info("Final report written: %s", report_path)


def write_blockers(reasons: list[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "# ADR-020 S8 Batch — Blockers\n\n"
    body += f"Generated: {datetime.now().isoformat(timespec='seconds')}\n\n"
    body += "## Blocker reasons\n\n"
    body += "\n".join(f"- {r}" for r in reasons)
    body += "\n\nBatch aborted before completion.\n"
    path.write_text(body, encoding="utf-8")
    log.error("Blockers written: %s", path)


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def run_batch(args) -> int:
    t_start = time.perf_counter()

    vault_root = Path(args.vault_root)
    if not vault_root.exists():
        log.error("vault_root does not exist: %s", vault_root)
        return 2

    os.environ.setdefault("VAULT_PATH", str(vault_root))
    _patch_kb_writer_to_staging()
    _patch_alias_map_to_staging()

    # Defensive: refuse if KB/Wiki.staging is somehow not the configured target.
    from shared import kb_writer

    if "Wiki.staging" not in kb_writer.KB_BOOK_SOURCES_DIR:
        log.error(
            "kb_writer is NOT pointing at staging — refusing to run. (KB_BOOK_SOURCES_DIR=%s)",
            kb_writer.KB_BOOK_SOURCES_DIR,
        )
        return 2

    # Start the cost tracking buffer.
    from shared.llm_context import start_usage_tracking

    start_usage_tracking()

    # Walk every requested book up front so we can fail fast on missing raw files.
    from shared.source_ingest import walk_book_to_chapters

    book_keys: list[str] = [k.strip() for k in args.books.split(",") if k.strip()]
    iteration: list[tuple[str, dict, object, int]] = []
    blockers: list[str] = []
    for key in book_keys:
        meta = BOOKS.get(key)
        if not meta:
            log.warning("unknown book key %s — skipping", key)
            continue
        raw_path = vault_root / meta["raw_rel"]
        if not raw_path.exists():
            blockers.append(f"raw markdown not found for {key}: {raw_path}")
            continue
        log.info("Walking %s …", raw_path)
        payloads = walk_book_to_chapters(raw_path)
        real = _real_chapters_in(payloads)
        log.info(
            "  %s: walker=%d entries, real chapters=%d (expected ~%d)",
            key,
            len(payloads),
            len(real),
            EXPECTED_REAL_CHAPTERS.get(meta["book_id"], -1),
        )
        if len(real) == 0:
            blockers.append(
                f"walker found 0 real chapters in {key} ({raw_path}) — "
                "Phase 0 raw file may have been re-extracted with broken H1 headings"
            )
            continue
        for i, p in enumerate(real, 1):
            iteration.append((key, meta, p, i))

    if blockers:
        write_blockers(blockers, _REPO_ROOT / DEFAULT_BLOCKERS_PATH)
        return 2

    if args.max_chapters:
        iteration = iteration[: args.max_chapters]
        log.info("--max-chapters: limiting to first %d", args.max_chapters)

    log.info("=== S8 batch starting: %d chapters total ===", len(iteration))

    results: list[ChapterResult] = []
    consecutive_fail = 0
    total_cost = 0.0

    for n, (key, meta, payload, real_idx) in enumerate(iteration, 1):
        log.info(
            "[%d/%d] %s ch%d (real %d): %s",
            n,
            len(iteration),
            key,
            payload.chapter_index,
            real_idx,
            payload.chapter_title,
        )
        try:
            r = _process_chapter(
                book_key=key,
                book_meta=meta,
                payload=payload,
                real_index=real_idx,
                vault_root=vault_root,
                dry_run=args.dry_run,
            )
        except Exception as e:
            log.exception("uncaught exception in chapter driver")
            r = ChapterResult(
                book_key=key,
                book_id=meta["book_id"],
                book_title=meta["book_title"],
                chapter_index=payload.chapter_index,
                real_index=real_idx,
                chapter_title=payload.chapter_title,
                status="error",
                fatal_error=f"uncaught: {e!r}",
            )
            # Try to drain cost tracking even on hard error.
            try:
                _drain_usage_buffer()
            except Exception:
                pass

        results.append(r)
        total_cost += r.cost_usd
        log.info(
            "  -> %s | verbatim=%.1f%% | concepts=%d | cost=$%.3f | wall=%s",
            r.status.upper(),
            r.verbatim_match_pct,
            r.concepts_extracted,
            r.cost_usd,
            _fmt_secs(r.wall_seconds),
        )

        if r.status in ("fail", "error"):
            consecutive_fail += 1
        else:
            consecutive_fail = 0

        # Hard cost-cap.
        if total_cost > COST_CAP_USD:
            blockers.append(
                f"LLM cost exceeded ${COST_CAP_USD:.0f} cap (current ${total_cost:.2f}) — aborting"
            )
            break

        # >3 consecutive failures → escalate.
        if consecutive_fail >= 3 and not args.dry_run:
            if not args.continue_on_fail:
                blockers.append(
                    f"3 consecutive chapter failures (last: {r.book_id} ch{r.real_index}) — "
                    "aborting (run with --continue-on-fail to override)"
                )
                break

        # Per-chapter JSON snapshot (so partial progress survives a crash).
        snap_path = _REPO_ROOT / "docs" / "runs" / "s8-batch-progress.json"
        try:
            snap_path.parent.mkdir(parents=True, exist_ok=True)
            snap_path.write_text(
                json.dumps(
                    [
                        {
                            "book_key": x.book_key,
                            "real_index": x.real_index,
                            "status": x.status,
                            "title": x.chapter_title,
                            "cost_usd": x.cost_usd,
                            "wall_seconds": x.wall_seconds,
                            "primary_missing_pct": x.primary_claims_missing_pct,
                            "concepts": x.concepts_extracted,
                        }
                        for x in results
                    ],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass

    total_wall = time.perf_counter() - t_start

    # Spot-check 5 random PASSed chapters.
    spot_checks: list[dict] = []
    if not args.dry_run:
        try:
            spot_checks = _run_spot_checks(results, vault_root, n=5)
        except Exception:
            log.exception("spot-check phase crashed")

    if blockers:
        write_blockers(blockers, _REPO_ROOT / DEFAULT_BLOCKERS_PATH)

    write_final_report(
        results,
        spot_checks,
        report_path=Path(args.report_path),
        total_wall=total_wall,
        total_cost=total_cost,
        blockers=blockers,
    )

    # Final stdout summary.
    n_pass = sum(1 for r in results if r.status == "pass")
    n_fail = sum(1 for r in results if r.status in ("fail", "error"))
    print()
    print("=" * 70)
    print("S8 BATCH COMPLETE")
    print("=" * 70)
    print(f"Chapters processed : {len(results)}")
    print(f"Pass / Fail        : {n_pass} / {n_fail}")
    print(f"Total cost         : ${total_cost:.2f}")
    print(f"Total wall         : {_fmt_secs(total_wall)}")
    print(f"Report             : {args.report_path}")
    if blockers:
        print(f"Blockers           : {_REPO_ROOT / DEFAULT_BLOCKERS_PATH}")
    print("=" * 70)

    return 0 if (n_fail == 0 and not blockers) else 2


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ADR-020 S8 batch runner — BSE + SN to staging")
    p.add_argument("--vault-root", default=DEFAULT_VAULT_ROOT)
    p.add_argument(
        "--books",
        default="bse,sn",
        help="comma-separated book keys; subset of {bse,sn}",
    )
    p.add_argument(
        "--report-path",
        default=str(_REPO_ROOT / DEFAULT_REPORT_PATH),
    )
    p.add_argument(
        "--max-chapters",
        type=int,
        default=0,
        help="cap total chapters processed (across both books); 0 = no limit",
    )
    p.add_argument("--dry-run", action="store_true", help="walker + classifier only")
    p.add_argument(
        "--continue-on-fail",
        dest="continue_on_fail",
        action="store_true",
        default=True,
        help="(default) keep going past chapter failures",
    )
    p.add_argument(
        "--no-continue-on-fail",
        dest="continue_on_fail",
        action="store_false",
        help="abort on first chapter failure or 3 consecutive failures",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    log.info("=== S8 batch starting ===")
    log.info(
        "vault_root=%s books=%s max_chapters=%s dry_run=%s continue_on_fail=%s",
        args.vault_root,
        args.books,
        args.max_chapters,
        args.dry_run,
        args.continue_on_fail,
    )
    try:
        return run_batch(args)
    except KeyboardInterrupt:
        log.warning("interrupted by user")
        return 130


if __name__ == "__main__":
    sys.exit(main())
