"""End-to-end Brook Line 1 podcast repurpose pipeline runner.

CLI orchestrator for #292 (Slice 9): wires Line1Extractor + 3 ChannelRenderers
into RepurposeEngine and executes a full Stage 1 → Stage 2 fan-out.

Output artifacts (7 files in ``data/repurpose/<YYYY-MM-DD>-<slug>/``):
    stage1.json
    blog.md
    fb-light.md, fb-emotional.md, fb-serious.md, fb-neutral.md
    ig-cards.json

Usage::

    # 最小
    python -m scripts.run_repurpose <srt-path>

    # 完整指定
    python -m scripts.run_repurpose <srt-path> \\
        --host "張修修" \\
        --guest "朱為民" \\
        --slug "dr-chu-ep67" \\
        --podcast-url "https://example.com/ep67"

    # Debug：跳過某 channel
    python -m scripts.run_repurpose <srt-path> --skip-channel ig

    # Plan 模式（不發 LLM call）
    python -m scripts.run_repurpose <srt-path> --dry-run

Cost tracking limitation: FBRenderer fans out 4 LLM calls in a thread pool;
``shared.llm_context._local`` is thread-local so worker threads do not inherit
the main-thread usage buffer. The printed cost summary covers only the calls
made on the main thread (Stage 1 extract + BlogRenderer + IGRenderer = 3 calls
typically); FB's 4 calls are tracked via the API call DB but not aggregated
here. See PR #300 review NOTE for the structural fix path.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from agents.brook.blog_renderer import BlogRenderer  # noqa: E402
from agents.brook.fb_renderer import FBRenderer  # noqa: E402
from agents.brook.ig_renderer import IGRenderer  # noqa: E402
from agents.brook.line1_extractor import Line1Extractor  # noqa: E402
from agents.brook.repurpose_engine import (  # noqa: E402
    BLOG_FILENAME,
    FB_TONALS,
    IG_FILENAME,
    EpisodeMetadata,
    RepurposeEngine,
    fb_filename,
)
from shared.llm_context import start_usage_tracking, stop_usage_tracking  # noqa: E402

# Channel names accepted by --skip-channel (matches engine renderers dict keys).
CHANNEL_CHOICES = ("blog", "fb", "ig")

# Sonnet 4.6 pricing (per 1M tokens, USD) — used for rough cost summary.
# Source: anthropic.com/pricing as of 2026-05; update when prices change.
_SONNET_46_INPUT_PER_MTOK = 3.0
_SONNET_46_OUTPUT_PER_MTOK = 15.0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Brook Line 1 podcast repurpose pipeline runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "srt_path",
        type=Path,
        help="Stage 1 SRT 字幕路徑（從 transcribe pipeline 產出）",
    )
    parser.add_argument(
        "--host",
        default="張修修",
        help="主持人姓名（default: 張修修）",
    )
    parser.add_argument(
        "--guest",
        default=None,
        help="來賓姓名（default: 由 LLM 從 SRT 上下文推斷）",
    )
    parser.add_argument(
        "--slug",
        default=None,
        help="輸出目錄 slug（default: SRT 檔名 stem）",
    )
    parser.add_argument(
        "--podcast-url",
        default="",
        help="Podcast 收聽連結，注入 prompt 給 CTA 段使用",
    )
    parser.add_argument(
        "--skip-channel",
        action="append",
        choices=CHANNEL_CHOICES,
        default=[],
        metavar="CHANNEL",
        help=(
            f"跳過某 channel（可重複使用，accepted: {', '.join(CHANNEL_CHOICES)}）；"
            "debug 用，例如 --skip-channel ig --skip-channel fb"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="印出 plan 不跑 LLM call",
    )
    return parser.parse_args()


def _build_renderers(skip_channels: set[str]) -> dict[str, object]:
    """Build the renderers dict for RepurposeEngine, omitting skipped channels.

    Order matters only for human-readable plan output; engine fans out in
    parallel regardless. ``dict`` preserves insertion order in 3.7+.
    """
    renderers: dict[str, object] = {}
    if "blog" not in skip_channels:
        renderers["blog"] = BlogRenderer()
    if "fb" not in skip_channels:
        renderers["fb"] = FBRenderer()
    if "ig" not in skip_channels:
        renderers["ig"] = IGRenderer()
    return renderers


def _expected_filenames(channels: list[str]) -> list[str]:
    """Predicted artifact filenames for a given channel list (excluding stage1.json)."""
    files: list[str] = []
    if "blog" in channels:
        files.append(BLOG_FILENAME)
    if "fb" in channels:
        files.extend(fb_filename(t) for t in FB_TONALS)
    if "ig" in channels:
        files.append(IG_FILENAME)
    return files


def _print_dry_run_plan(args: argparse.Namespace, channels: list[str]) -> None:
    """Print the execution plan without making any LLM call."""
    expected = ["stage1.json", *_expected_filenames(channels)]
    print("=" * 60)
    print("DRY RUN — no LLM calls will be made")
    print("=" * 60)
    print(f"SRT path     : {args.srt_path}")
    print(f"Host         : {args.host}")
    print(f"Guest        : {args.guest or '(LLM-inferred from SRT)'}")
    print(f"Slug         : {args.slug or args.srt_path.stem}")
    print(f"Podcast URL  : {args.podcast_url or '(empty)'}")
    print(f"Channels     : {channels or '(none — all skipped)'}")
    print(f"Skip         : {sorted(set(args.skip_channel)) or '(none)'}")
    print(f"Expected outputs ({len(expected)}):")
    for filename in expected:
        print(f"  - {filename}")
    print("=" * 60)


def _print_cost_summary(usage_records: list[dict]) -> None:
    """Print a rough Sonnet 4.6 cost estimate from main-thread usage records.

    Limitation: FBRenderer's 4 parallel calls run in worker threads where
    thread-local ``_local.usage_buffer`` is not inherited, so they are NOT
    counted here. The total below covers only main-thread calls (Stage 1 +
    BlogRenderer + IGRenderer when present).
    """
    if not usage_records:
        print("Cost summary: <no main-thread LLM calls tracked>")
        return

    total_in = sum(int(r.get("input_tokens") or 0) for r in usage_records)
    total_out = sum(int(r.get("output_tokens") or 0) for r in usage_records)
    cost = (
        total_in * _SONNET_46_INPUT_PER_MTOK + total_out * _SONNET_46_OUTPUT_PER_MTOK
    ) / 1_000_000
    print(
        f"Cost (main-thread calls only): ~${cost:.4f} "
        f"({len(usage_records)} calls, in={total_in:,} / out={total_out:,} tokens)"
    )
    print(
        "  ⚠️  FBRenderer's 4 parallel calls run in worker threads and are NOT "
        "counted above (tracked via API call DB only); see module docstring."
    )


def _run_engine(args: argparse.Namespace, renderers: dict[str, object]) -> int:
    """Execute the engine and print artifact summary. Returns process exit code."""
    if not args.srt_path.exists():
        print(f"Error: SRT file not found: {args.srt_path}", file=sys.stderr)
        return 2

    srt_text = args.srt_path.read_text(encoding="utf-8")
    if not srt_text.strip():
        print(f"Error: SRT file is empty: {args.srt_path}", file=sys.stderr)
        return 2

    slug = args.slug or args.srt_path.stem
    metadata = EpisodeMetadata(
        slug=slug,
        host=args.host,
        extra={
            "guest": args.guest or "受訪者",
            "podcast_episode_url": args.podcast_url,
        },
    )

    engine = RepurposeEngine(
        extractor=Line1Extractor(),
        renderers=renderers,  # type: ignore[arg-type]
    )

    print("=" * 60)
    print(f"Repurpose run: {slug}")
    print(f"  Channels: {list(renderers.keys())}")
    print(f"  Host: {args.host} / Guest: {args.guest or '(LLM-inferred)'}")
    print("=" * 60)

    start_usage_tracking()
    started = time.time()
    try:
        result = engine.run(srt_text, metadata)
    finally:
        usage = stop_usage_tracking()
    elapsed = time.time() - started

    print()
    print("=" * 60)
    print(f"Run dir: {result.run_dir}")
    print(f"Artifacts ({len(result.artifacts)}):")
    for artifact in result.artifacts:
        print(f"  ✓ {artifact.filename}  ({artifact.channel})")
    if result.errors:
        print(f"Errors ({len(result.errors)}):")
        for channel, exc in result.errors.items():
            print(f"  ✗ {channel}: {type(exc).__name__}: {exc}")
    print(f"Wall: {elapsed:.1f}s")
    _print_cost_summary(usage)
    print("=" * 60)

    # Non-zero exit if any channel failed (CI / cron can detect).
    return 1 if result.errors else 0


def main() -> None:
    args = _parse_args()

    skip_set = set(args.skip_channel)
    renderers = _build_renderers(skip_set)

    if not renderers:
        print("Error: all channels skipped — nothing to do.", file=sys.stderr)
        sys.exit(2)

    if args.dry_run:
        _print_dry_run_plan(args, list(renderers.keys()))
        return

    sys.exit(_run_engine(args, renderers))


if __name__ == "__main__":
    main()
