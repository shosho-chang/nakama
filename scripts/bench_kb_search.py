"""ADR-021 §3 + Codex amendment #6 mini-bench harness (issue #457).

Runs 5 fixture Project topics × {K=8, 15, 30} × {engine=hybrid, haiku}
against the local KB and writes a markdown report to
`docs/research/{date}-brook-synthesize-bench.md` with per-topic tables and
an aggregate summary. shosho reviews the data and freezes the
`agents/brook/synthesize/` default `top_k` + engine in a follow-up commit
(HITL gate — this script does NOT pick a winner).

Usage:
    python -m scripts.bench_kb_search
    python -m scripts.bench_kb_search --topic creatine_cognitive
    python -m scripts.bench_kb_search --k 8,15
    python -m scripts.bench_kb_search --engine hybrid

Boundaries:
- Read-only consumer of `agents.robin.kb_search.search_kb` — no
  reimplementation of retrieval logic.
- Hybrid engine requires a populated `data/kb_index.db` (BGE-M3 1024d
  rebuild, post-#469) and a local FlagEmbedding install. If that fails
  the harness records the error in the bench output and continues with
  the remaining engine/topic combinations.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

DEFAULT_KS: tuple[int, ...] = (8, 15, 30)
DEFAULT_ENGINES: tuple[str, ...] = ("hybrid", "haiku")
DEFAULT_TOPICS_PATH = _REPO_ROOT / "tests" / "fixtures" / "brook_bench_topics.yaml"
DEFAULT_OUTPUT_DIR = _REPO_ROOT / "docs" / "research"


@dataclass
class Topic:
    id: str
    query: str
    description: str
    ground_truth: list[str] = field(default_factory=list)


@dataclass
class RunResult:
    topic_id: str
    engine: str
    k: int
    hits: list[dict[str, Any]]  # KBHit-shaped (see agents.robin.kb_search.KBHit)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def load_topics(path: Path) -> list[Topic]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        Topic(
            id=t["id"],
            query=t["query"],
            description=t.get("description", ""),
            ground_truth=list(t.get("ground_truth") or []),
        )
        for t in raw["topics"]
    ]


def _hit_matches_truth(hit: dict[str, Any], truth_slug: str) -> bool:
    """A hit matches a ground-truth slug if the slug appears as a substring
    of `path` or `chunk_id`/`title`. Substring match keeps the fixture file
    forgiving — slugs can be either full paths or filename stems.
    """
    needle = truth_slug.lower()
    haystacks = [
        str(hit.get("path", "")).lower(),
        str(hit.get("title", "")).lower(),
    ]
    return any(needle in h for h in haystacks if h)


def compute_recall_precision(
    hits: list[dict[str, Any]], ground_truth: list[str]
) -> tuple[float | None, float | None, list[str]]:
    """Return (recall, precision, matched_truth_slugs).

    Recall  = |hit-matched truth slugs| / |truth slugs|
    Precision = |hits that match any truth slug| / |hits|

    Returns (None, None, []) if ground_truth is empty (uncurated topic) —
    we still surface the retrieved hits but skip metric computation.
    """
    if not ground_truth:
        return None, None, []
    if not hits:
        return 0.0, 0.0, []

    matched_truth: set[str] = set()
    matched_hit_count = 0
    for hit in hits:
        hit_matched_any = False
        for slug in ground_truth:
            if _hit_matches_truth(hit, slug):
                matched_truth.add(slug)
                hit_matched_any = True
        if hit_matched_any:
            matched_hit_count += 1

    recall = len(matched_truth) / len(ground_truth)
    precision = matched_hit_count / len(hits)
    return recall, precision, sorted(matched_truth)


def run_single(
    topic: Topic,
    *,
    engine: str,
    k: int,
    vault_path: Path,
    search_fn: Any | None = None,
) -> RunResult:
    """Invoke `search_kb` for one (topic, engine, k) combination.

    `search_fn` is injectable for unit tests — the production path imports
    `agents.robin.kb_search.search_kb` lazily so this module loads even when
    the KB index DB is absent.
    """
    if search_fn is None:
        from agents.robin.kb_search import search_kb as search_fn  # noqa: PLC0415

    try:
        hits = search_fn(
            topic.query,
            vault_path,
            top_k=k,
            engine=engine,  # type: ignore[arg-type]
        )
        return RunResult(topic_id=topic.id, engine=engine, k=k, hits=list(hits))
    except Exception as exc:  # noqa: BLE001
        return RunResult(
            topic_id=topic.id,
            engine=engine,
            k=k,
            hits=[],
            error=f"{type(exc).__name__}: {exc}",
        )


def _format_hit_preview(hit: dict[str, Any]) -> str:
    """Compact one-line representation of a hit for the markdown table."""
    path = str(hit.get("path", "")).split("/")[-1] or "?"
    heading = hit.get("heading") or ""
    score = hit.get("rrf_score")
    parts = [path]
    if heading:
        parts.append(f"§{heading[:30]}")
    if score is not None:
        parts.append(f"rrf={score:.3f}")
    return " · ".join(parts)


def render_topic_table(topic: Topic, results: list[RunResult]) -> str:
    """Render one per-topic markdown section."""
    gt_str = (
        ", ".join(f"`{s}`" for s in topic.ground_truth) if topic.ground_truth else "(uncurated)"
    )
    lines = [
        f"### Topic: {topic.id}",
        "",
        f"**Query:** {topic.query}  ",
        f"**Description:** {topic.description}  ",
        f"**Ground truth:** {gt_str}",
        "",
        "| Engine | K | Returned (top 3) | Recall | Precision |",
        "|--------|---|------------------|--------|-----------|",
    ]
    for r in sorted(results, key=lambda x: (x.engine, x.k)):
        if not r.ok:
            lines.append(f"| {r.engine} | {r.k} | _error: {r.error}_ | — | — |")
            continue
        top3 = "; ".join(_format_hit_preview(h) for h in r.hits[:3]) or "_(no hits)_"
        recall, precision, _ = compute_recall_precision(r.hits, topic.ground_truth)
        recall_str = "—" if recall is None else f"{recall:.2f}"
        precision_str = "—" if precision is None else f"{precision:.2f}"
        lines.append(f"| {r.engine} | {r.k} | {top3} | {recall_str} | {precision_str} |")
    lines.append("")
    return "\n".join(lines)


def render_aggregate(topics: list[Topic], all_results: list[RunResult]) -> str:
    """Mean recall / precision per (engine, k) across topics with ground truth."""
    by_topic: dict[str, Topic] = {t.id: t for t in topics}
    buckets: dict[tuple[str, int], list[tuple[float, float]]] = {}
    for r in all_results:
        if not r.ok:
            continue
        topic = by_topic[r.topic_id]
        if not topic.ground_truth:
            continue
        recall, precision, _ = compute_recall_precision(r.hits, topic.ground_truth)
        if recall is None or precision is None:
            continue
        buckets.setdefault((r.engine, r.k), []).append((recall, precision))

    lines = [
        "## Aggregate (mean across topics with curated ground truth)",
        "",
        "| Engine | K | Mean recall | Mean precision | N topics |",
        "|--------|---|-------------|----------------|----------|",
    ]
    for (engine, k), pairs in sorted(buckets.items()):
        mean_r = sum(p[0] for p in pairs) / len(pairs)
        mean_p = sum(p[1] for p in pairs) / len(pairs)
        lines.append(f"| {engine} | {k} | {mean_r:.2f} | {mean_p:.2f} | {len(pairs)} |")
    if not buckets:
        lines.append("| _(no curated topics or all runs errored)_ | | | | |")
    lines.append("")
    return "\n".join(lines)


def render_report(
    topics: list[Topic],
    all_results: list[RunResult],
    *,
    date: str,
    corpus_note: str,
    error_summary: str | None = None,
) -> str:
    """Build the full markdown bench report."""
    sections = [
        f"# Brook synthesize mini-bench — {date}",
        "",
        f"**Corpus:** {corpus_note}",
        "",
        "Generated by `scripts/bench_kb_search.py` (issue #457, ADR-021 §3 + ",
        "Codex amendment #6). HITL gate: shosho reviews this data and freezes ",
        "the `agents/brook/synthesize/` default `top_k` + engine in a follow-up ",
        "commit. **No winner is pinned by this script.**",
        "",
    ]
    if error_summary:
        sections += ["> **Note:** " + error_summary, ""]
    sections += ["## Per-topic results", ""]
    for topic in topics:
        topic_results = [r for r in all_results if r.topic_id == topic.id]
        sections.append(render_topic_table(topic, topic_results))
    sections.append(render_aggregate(topics, all_results))
    sections += [
        "## Reading the table",
        "",
        "- **Recall@K** — fraction of ground-truth slugs that surfaced in the top K hits.",
        "- **Precision** — fraction of returned hits that match any ground-truth slug.",
        "- Substring match on `path` / `title`; fixtures may use slug stems or full paths.",
        "- Topics with `(uncurated)` ground truth print returned hits only — eyeball-only.",
        "",
        "## Decision template (for shosho)",
        "",
        "Freeze defaults by editing `agents/brook/synthesize/` (record choice in ADR-021 §3):",
        "",
        "- `BROOK_SYNTHESIZE_TOP_K = ?`",
        "- `BROOK_SYNTHESIZE_ENGINE = ?`",
        "",
        "Rationale should reference the recall/precision bend point above.",
        "",
    ]
    return "\n".join(sections)


def run_bench(
    *,
    topics: list[Topic],
    ks: tuple[int, ...],
    engines: tuple[str, ...],
    vault_path: Path,
    search_fn: Any | None = None,
) -> list[RunResult]:
    """Cross-product runner. Errors per cell are recorded, not raised."""
    results: list[RunResult] = []
    for topic in topics:
        for engine in engines:
            for k in ks:
                results.append(
                    run_single(
                        topic,
                        engine=engine,
                        k=k,
                        vault_path=vault_path,
                        search_fn=search_fn,
                    )
                )
    return results


def _parse_csv_int(value: str) -> tuple[int, ...]:
    return tuple(int(x.strip()) for x in value.split(",") if x.strip())


def _parse_csv_str(value: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in value.split(",") if x.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench_kb_search",
        description="ADR-021 §3 mini-bench: top_k × engine recall/precision.",
    )
    parser.add_argument(
        "--topic",
        help="Filter to a single topic id (default: all from fixtures).",
    )
    parser.add_argument(
        "--k",
        type=_parse_csv_int,
        default=DEFAULT_KS,
        help="Comma-separated K values (default: 8,15,30).",
    )
    parser.add_argument(
        "--engine",
        type=_parse_csv_str,
        default=DEFAULT_ENGINES,
        help="Comma-separated engines (default: hybrid,haiku).",
    )
    parser.add_argument(
        "--topics-file",
        type=Path,
        default=DEFAULT_TOPICS_PATH,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output markdown path (default: docs/research/{today}-brook-synthesize-bench.md).",
    )
    parser.add_argument(
        "--corpus-note",
        default="BGE-M3 1024d (post-#469 rebuild). Local KB.",
    )
    args = parser.parse_args(argv)

    topics = load_topics(args.topics_file)
    if args.topic:
        topics = [t for t in topics if t.id == args.topic]
        if not topics:
            print(f"error: topic id {args.topic!r} not found in fixtures", file=sys.stderr)
            return 2

    from shared.config import get_vault_path  # noqa: PLC0415

    try:
        vault_path = get_vault_path()
    except Exception as exc:  # noqa: BLE001
        print(
            f"warning: could not resolve vault path ({exc}); using repo-root/vault", file=sys.stderr
        )
        vault_path = _REPO_ROOT / "vault"

    today = _dt.date.today().isoformat()
    output_path = args.output or (DEFAULT_OUTPUT_DIR / f"{today}-brook-synthesize-bench.md")

    print(
        f"Running bench: {len(topics)} topics × {len(args.engine)} engines × {len(args.k)} Ks "
        f"= {len(topics) * len(args.engine) * len(args.k)} runs",
        file=sys.stderr,
    )

    error_summary: str | None = None
    try:
        results = run_bench(
            topics=topics,
            ks=tuple(args.k),
            engines=tuple(args.engine),
            vault_path=vault_path,
        )
    except Exception:  # noqa: BLE001
        # Catastrophic failure (e.g. fixtures broken). Still ship a placeholder.
        error_summary = (
            "Bench harness crashed before producing results — see traceback "
            "below. Rerun locally with `python -m scripts.bench_kb_search`.\n\n"
            "```\n" + traceback.format_exc() + "\n```"
        )
        results = []

    n_errors = sum(1 for r in results if not r.ok)
    if n_errors:
        sample = next((r.error for r in results if not r.ok), "")
        error_summary = (
            f"{n_errors}/{len(results)} runs errored "
            f"(first error: {sample}). This is expected if `data/kb_index.db` is "
            "absent or FlagEmbedding is not installed in this environment — rerun "
            "locally with `python -m scripts.bench_kb_search`."
        )

    report = render_report(
        topics,
        results,
        date=today,
        corpus_note=args.corpus_note,
        error_summary=error_summary,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(f"wrote {output_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
