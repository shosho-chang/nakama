"""Keyword Research CLI — skill-friendly wrapper around agents.zoro.keyword_research.

Invoked by the `keyword-research` Claude Code skill. Also usable standalone for
scheduled runs or ad-hoc CLI use.

Writes a markdown file with a structured frontmatter block that downstream
skills (Brook compose, future SEO audit) can parse without re-running the
pipeline.

Usage:
    python scripts/run_keyword_research.py "間歇性斷食" --content-type blog
    python scripts/run_keyword_research.py "磁振造影" --en-topic "MRI" --out out.md
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from agents.zoro.keyword_research import research_keywords  # noqa: E402
from agents.zoro.report_renderer import build_frontmatter, render_markdown  # noqa: E402

# Anthropic Sonnet / Opus / Haiku 4.x rate card (USD per 1M tokens, input, output).
# Cache-write costs 1.25x input; cache-read costs 0.1x input. Aligned with
# `memory/claude/reference_llm_provider_cost_quirks.md` and Anthropic's public
# pricing as of 2026-04. Update both docs together if rates change.
_CLAUDE_RATE_USD_PER_1M: dict[str, tuple[float, float]] = {
    "claude-haiku-4": (1.0, 5.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-opus-4": (15.0, 75.0),
}


def _rate_for(model: str) -> tuple[float, float]:
    for prefix, rate in _CLAUDE_RATE_USD_PER_1M.items():
        if model.startswith(prefix):
            return rate
    return (3.0, 15.0)  # fallback to Sonnet — keep CLI honest, not silent


def _calc_cost_usd(records: list[dict]) -> float:
    """Sum USD across per-call usage records using the prefix-matched rate card."""
    total = 0.0
    for r in records:
        in_rate, out_rate = _rate_for(r.get("model", ""))
        total += r.get("input_tokens", 0) / 1_000_000 * in_rate
        total += r.get("output_tokens", 0) / 1_000_000 * out_rate
        total += r.get("cache_read_tokens", 0) / 1_000_000 * (in_rate * 0.1)
        total += r.get("cache_write_tokens", 0) / 1_000_000 * (in_rate * 1.25)
    return total


def _format_cost_summary(usage: list[dict]) -> str:
    """Render the cost summary block printed at the end of a CLI run.

    Empty ``usage`` (e.g. tracking failed) yields an empty string so callers
    can drop the section instead of printing zeros that look like a real run.
    """
    if not usage:
        return ""
    n = len(usage)
    inp = sum(r.get("input_tokens", 0) for r in usage)
    out = sum(r.get("output_tokens", 0) for r in usage)
    cache_r = sum(r.get("cache_read_tokens", 0) for r in usage)
    cache_w = sum(r.get("cache_write_tokens", 0) for r in usage)
    cost = _calc_cost_usd(usage)
    lines = [
        "",
        "成本（實測）：",
        f"  Claude API call(s)：{n} 次",
        f"    input tokens   : {inp:>7,}",
        f"    output tokens  : {out:>7,}  (Claude 4.x 把 extended thinking 計入 output)",
    ]
    if cache_r or cache_w:
        lines.append(f"    cache read     : {cache_r:>7,}  (折扣 0.1×)")
        lines.append(f"    cache write    : {cache_w:>7,}  (1.25× input)")
    lines.append(
        f"  $ 換算 (rate card per memory/claude/reference_llm_provider_cost_quirks.md)：${cost:.4f}"
    )
    lines.append("  歷史 N 次平均見 .claude/skills/keyword-research/references/cost-estimation.md")
    return "\n".join(lines)


def _default_output_path(topic: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in topic)[:50]
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return Path.cwd() / f"keyword-research-{safe}-{stamp}.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bilingual keyword research + markdown report generator."
    )
    parser.add_argument("topic", help="Chinese topic (e.g. 間歇性斷食)")
    parser.add_argument(
        "--en-topic",
        default=None,
        help="English equivalent (auto-translated if omitted)",
    )
    parser.add_argument(
        "--content-type",
        choices=["youtube", "blog"],
        default="youtube",
        help="Optimization target for title suggestions (default: youtube)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output markdown path (default: ./keyword-research-<topic>-<timestamp>.md)",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Also dump the raw result dict as JSON to this path (optional)",
    )

    args = parser.parse_args(argv)

    topic = args.topic.strip()
    if not topic:
        parser.error("topic cannot be empty")

    print(f"[keyword-research] 主題：{topic}", file=sys.stderr)
    if args.en_topic:
        print(f"[keyword-research] 英文：{args.en_topic}", file=sys.stderr)
    print(f"[keyword-research] 模式：{args.content_type}", file=sys.stderr)
    print("[keyword-research] 執行中…（預計 30-60s，取決於資料來源回應）", file=sys.stderr)

    started_at = time.monotonic()
    try:
        result = research_keywords(
            topic,
            content_type=args.content_type,
            en_topic=args.en_topic,
        )
    except RuntimeError as e:
        print(f"[keyword-research] ERROR: {e}", file=sys.stderr)
        return 2
    elapsed = time.monotonic() - started_at

    frontmatter = build_frontmatter(topic, args.en_topic or "", args.content_type, result)
    md = render_markdown(frontmatter, result)

    out_path = args.out or _default_output_path(topic)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(f"[keyword-research] 寫入：{out_path}", file=sys.stderr)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[keyword-research] JSON dump：{args.json_out}", file=sys.stderr)

    sources_used = result.get("sources_used", [])
    sources_failed = result.get("sources_failed", [])
    print(
        f"[keyword-research] sources_used={len(sources_used)} "
        f"sources_failed={len(sources_failed)} elapsed={elapsed:.1f}s",
        file=sys.stderr,
    )
    print(f"完成！耗時 {elapsed:.1f}s", file=sys.stdout)

    cost_block = _format_cost_summary(result.get("usage", []))
    if cost_block:
        print(cost_block, file=sys.stdout)

    return 0


if __name__ == "__main__":
    sys.exit(main())
