"""BSE textbook v3 ingest eval — verify Agent 找不找得到 BSE 內容.

Two layers:

  Layer 1 — Filesystem coverage (always runs, no LLM, no GPU)
    For each topic, check that expected concept_slugs + source_pages
    actually exist on disk under KB/Wiki/Concepts/ and KB/Wiki/Sources/.
    Proves "the BSE content is in the vault".

  Layer 2 — Hybrid retrieval recall (only with --with-hybrid)
    Calls `agents.robin.kb_search.search_kb(query, vault, engine='hybrid')`
    for each topic and checks recall@K against ground_truth_paths.
    REQUIRES `data/kb_index.db` populated with current vault state — run
    `python -m shared.kb_indexer` first if it is stale.

Usage:
    # Filesystem-only (instant, safe to run anywhere)
    python -m scripts.eval_bse_kb_search

    # With hybrid retrieval (needs reindex first; will use BGE-M3 model)
    python -m scripts.eval_bse_kb_search --with-hybrid

    # Limit to specific topics
    python -m scripts.eval_bse_kb_search --topic atp_energy_currency,lactate_threshold

Output: prints per-topic table to stdout + writes JSON summary to
``docs/runs/{date}-bse-kb-eval.json`` for later comparison.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

DEFAULT_TOPICS_PATH = _REPO_ROOT / "tests" / "fixtures" / "bse_kb_eval_topics.yaml"
DEFAULT_OUTPUT_DIR = _REPO_ROOT / "docs" / "runs"
DEFAULT_TOP_K = 15  # ADR-021 §3 frozen default


@dataclass
class Topic:
    id: str
    query: str
    description: str
    expected_concept_slugs: list[str] = field(default_factory=list)
    expected_source_pages: list[str] = field(default_factory=list)
    ground_truth_paths: list[str] = field(default_factory=list)


@dataclass
class FilesystemResult:
    topic_id: str
    expected_concepts: int
    found_concepts: int
    missing_concepts: list[str]
    expected_sources: int
    found_sources: int
    missing_sources: list[str]

    @property
    def coverage_pct(self) -> float:
        total = self.expected_concepts + self.expected_sources
        found = self.found_concepts + self.found_sources
        return 100.0 * found / total if total else 0.0


@dataclass
class HybridResult:
    topic_id: str
    query: str
    hits_count: int
    recall_at_k: float
    matched_ground_truth: list[str]
    missed_ground_truth: list[str]
    top_paths: list[str]
    error: str | None = None


def load_topics(path: Path) -> list[Topic]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        Topic(
            id=t["id"],
            query=t["query"],
            description=t.get("description", ""),
            expected_concept_slugs=list(t.get("expected_concept_slugs") or []),
            expected_source_pages=list(t.get("expected_source_pages") or []),
            ground_truth_paths=list(t.get("ground_truth_paths") or []),
        )
        for t in raw["topics"]
    ]


def _resolve_vault_path() -> Path:
    """Use the project's canonical vault resolver."""
    from shared.config import get_vault_path  # noqa: PLC0415

    return get_vault_path()


def run_layer1_filesystem(topics: list[Topic], vault: Path) -> list[FilesystemResult]:
    """Check that expected concept + source files exist on disk."""
    concepts_dir = vault / "KB" / "Wiki" / "Concepts"
    sources_dir = vault / "KB" / "Wiki" / "Sources"

    results: list[FilesystemResult] = []
    for t in topics:
        # Concepts: case-insensitive AND space/hyphen-tolerant lookup.
        # Reasons:
        #   - Windows is case-insensitive but Obsidian wikilinks are case-
        #     sensitive (so `ATP.md` and `atp.md` are both legitimate targets).
        #   - The S8 dispatcher's `_slug_from_term` PRESERVES spaces (only
        #     filesystem-illegal chars become `-`), so concept files end up as
        #     `electron transport chain.md` not `electron-transport-chain.md`.
        # We normalize both sides by lowercasing + collapsing `-` → ` `.
        def _norm(s: str) -> str:
            return s.lower().replace("-", " ").strip()

        existing_concepts_norm = {_norm(p.stem) for p in concepts_dir.glob("*.md")}
        found_c, missing_c = [], []
        for slug in t.expected_concept_slugs:
            if _norm(slug) in existing_concepts_norm:
                found_c.append(slug)
            else:
                missing_c.append(slug)

        found_s, missing_s = [], []
        for sp in t.expected_source_pages:
            page_path = sources_dir / f"{sp}.md"
            if page_path.exists():
                found_s.append(sp)
            else:
                missing_s.append(sp)

        results.append(
            FilesystemResult(
                topic_id=t.id,
                expected_concepts=len(t.expected_concept_slugs),
                found_concepts=len(found_c),
                missing_concepts=missing_c,
                expected_sources=len(t.expected_source_pages),
                found_sources=len(found_s),
                missing_sources=missing_s,
            )
        )
    return results


def run_layer2_hybrid(topics: list[Topic], vault: Path, top_k: int) -> list[HybridResult]:
    """Run hybrid retrieval for each topic and compute recall@K."""
    from agents.robin.kb_search import search_kb  # noqa: PLC0415

    results: list[HybridResult] = []
    for t in topics:
        try:
            hits = search_kb(t.query, vault, top_k=top_k, engine="hybrid")
        except Exception as e:
            results.append(
                HybridResult(
                    topic_id=t.id,
                    query=t.query,
                    hits_count=0,
                    recall_at_k=0.0,
                    matched_ground_truth=[],
                    missed_ground_truth=t.ground_truth_paths,
                    top_paths=[],
                    error=f"{type(e).__name__}: {e}",
                )
            )
            continue

        hit_paths = [h.get("path", "") for h in hits]
        # Substring match — mirrors bench_kb_search.py convention
        matched, missed = [], []
        for gt in t.ground_truth_paths:
            if any(gt in hp or hp in gt for hp in hit_paths):
                matched.append(gt)
            else:
                missed.append(gt)
        recall = len(matched) / len(t.ground_truth_paths) if t.ground_truth_paths else 0.0

        results.append(
            HybridResult(
                topic_id=t.id,
                query=t.query,
                hits_count=len(hits),
                recall_at_k=recall,
                matched_ground_truth=matched,
                missed_ground_truth=missed,
                top_paths=hit_paths[:5],  # first 5 for display
            )
        )
    return results


def render_layer1_table(results: list[FilesystemResult]) -> str:
    lines = [
        "| Topic | Concepts | Sources | Coverage | Missing |",
        "|-------|----------|---------|----------|---------|",
    ]
    for r in results:
        missing_summary = ""
        if r.missing_concepts or r.missing_sources:
            parts = []
            if r.missing_concepts:
                parts.append(f"concepts: {', '.join(r.missing_concepts[:3])}")
            if r.missing_sources:
                parts.append(f"sources: {', '.join(r.missing_sources[:2])}")
            missing_summary = "; ".join(parts)
        else:
            missing_summary = "—"
        lines.append(
            f"| {r.topic_id} | {r.found_concepts}/{r.expected_concepts} "
            f"| {r.found_sources}/{r.expected_sources} "
            f"| {r.coverage_pct:.0f}% | {missing_summary} |"
        )
    return "\n".join(lines)


def render_layer2_table(results: list[HybridResult]) -> str:
    lines = [
        "| Topic | Recall@K | Matched / Total | Top hit | Error |",
        "|-------|----------|-----------------|---------|-------|",
    ]
    for r in results:
        top = r.top_paths[0] if r.top_paths else "—"
        # Trim long paths
        if len(top) > 50:
            top = "…" + top[-47:]
        err = r.error[:30] + "…" if r.error and len(r.error) > 30 else (r.error or "—")
        gt_total = len(r.matched_ground_truth) + len(r.missed_ground_truth)
        lines.append(
            f"| {r.topic_id} | {r.recall_at_k * 100:.0f}% "
            f"| {len(r.matched_ground_truth)}/{gt_total} "
            f"| `{top}` | {err} |"
        )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="BSE KB ingest eval — filesystem + hybrid retrieval")
    p.add_argument("--topics-path", type=Path, default=DEFAULT_TOPICS_PATH)
    p.add_argument(
        "--with-hybrid",
        action="store_true",
        help=(
            "Also run Layer 2 (hybrid retrieval) — REQUIRES kb_index.db populated "
            "via `python -m shared.kb_indexer` (BGE-M3 model load, may use GPU)"
        ),
    )
    p.add_argument(
        "--topic",
        default="",
        help="Comma-separated topic IDs to limit the run (default: all)",
    )
    p.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument(
        "--no-write",
        action="store_true",
        help="Don't write JSON summary file (stdout only)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    topics = load_topics(args.topics_path)

    if args.topic:
        wanted = {x.strip() for x in args.topic.split(",") if x.strip()}
        topics = [t for t in topics if t.id in wanted]
        if not topics:
            print(f"No topics matched: {wanted}")
            return 2

    vault = _resolve_vault_path()
    print(f"Vault: {vault}")
    print(f"Topics: {len(topics)}")
    print()

    # ----- Layer 1: filesystem -----
    print("=" * 70)
    print("LAYER 1 — Filesystem coverage")
    print("=" * 70)
    l1 = run_layer1_filesystem(topics, vault)
    print(render_layer1_table(l1))

    total_expected = sum(r.expected_concepts + r.expected_sources for r in l1)
    total_found = sum(r.found_concepts + r.found_sources for r in l1)
    overall = 100.0 * total_found / total_expected if total_expected else 0.0
    print()
    print(f"Layer 1 overall coverage: {total_found}/{total_expected} = {overall:.1f}%")

    # ----- Layer 2: hybrid retrieval (optional) -----
    l2: list[HybridResult] = []
    if args.with_hybrid:
        print()
        print("=" * 70)
        print(f"LAYER 2 — Hybrid retrieval recall@{args.top_k}")
        print("=" * 70)
        l2 = run_layer2_hybrid(topics, vault, top_k=args.top_k)
        print(render_layer2_table(l2))

        # Aggregate recall (macro-average over topics with non-empty GT)
        valid = [r for r in l2 if (r.matched_ground_truth or r.missed_ground_truth)]
        if valid:
            avg_recall = sum(r.recall_at_k for r in valid) / len(valid)
            print()
            print(f"Layer 2 macro-avg recall@{args.top_k}: {avg_recall * 100:.1f}%")

    # ----- Write JSON summary -----
    if not args.no_write:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        out_path = args.output_dir / f"{datetime.now().strftime('%Y-%m-%d')}-bse-kb-eval.json"
        out_path.write_text(
            json.dumps(
                {
                    "generated": datetime.now().isoformat(timespec="seconds"),
                    "vault": str(vault),
                    "layer1": [r.__dict__ for r in l1],
                    "layer2": [r.__dict__ for r in l2],
                    "layer1_overall_pct": overall,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nJSON summary: {out_path}")

    # Exit code: 0 if all layer-1 topics ≥ 50% coverage; 2 otherwise
    failing = [r for r in l1 if r.coverage_pct < 50.0]
    return 0 if not failing else 2


if __name__ == "__main__":
    sys.exit(main())
