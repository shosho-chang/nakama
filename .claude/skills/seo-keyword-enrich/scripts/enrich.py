"""SEO keyword enrichment pipeline — Slice B GSC-only baseline.

Reads a `keyword-research` markdown report (ADR-009 frozen input contract;
see `.claude/skills/keyword-research/references/output-contract.md`), calls
Google Search Console for the last 28 days, filters striking-distance rows,
detects cannibalization warnings, and writes a `SEOContextV1`-shaped markdown
report to the vault for downstream consumption (Brook compose Slice C,
`seo-optimize-draft` Phase 2).

Scope (Slice B):
    - GSC only — DataForSEO / firecrawl / PageSpeed stubbed out
    - `competitor_serp_summary` always `None` (fill in Phase 1.5)
    - Zero LLM calls (pure parse + API + filter + build)

Design — pure functions + inject-able client
---------------------------------------------
Functions are pulled apart so each unit is directly testable without
standing up a real `GSCClient`:

- `parse_keyword_research_input(path)`       pure
- `resolve_target_site(parsed)`              pure
- `build_gsc_query_payload(site, end_date)`  pure
- `build_seo_context(rows, …)`               pure (given rows)
- `render_output_markdown(ctx)`              pure
- `enrich(input_path, output_dir, client?)`  orchestrator — `client=None`
                                             triggers `GSCClient.from_env()`,
                                             otherwise injects the given one
                                             (tests pass a fake client).

CLI: `python enrich.py --input <path> --output-dir <dir> [--dry-run]`.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from shared.gsc_client import GSCClient
from shared.log import get_logger
from shared.schemas.publishing import (
    KeywordMetricV1,
    SEOContextV1,
    TargetSite,
)
from shared.seo_enrich.cannibalization import (
    detect_cannibalization,
    load_cannibalization_thresholds,
)
from shared.seo_enrich.striking_distance import filter_striking_distance

logger = get_logger("nakama.seo_enrich.pipeline")

# Python 3.9+ supports zoneinfo; used for filename date per
# `feedback_date_filename_review_checklist.md`.
try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

_TAIPEI = ZoneInfo("Asia/Taipei")

# GSC data lag — empirically 2-3 days. Skip the most recent 3 to avoid
# partial-day numbers skewing striking-distance signal.
_GSC_END_LAG_DAYS = 3
_GSC_WINDOW_DAYS = 28

# Primary / related keyword caps. Phase 2 Brook compose-side token budget
# (ADR-009 T11) will further trim to ~10 when rendering into system prompt.
_MAX_RELATED_KEYWORDS = 20

# Env key per target site (Slice A runbook `docs/runbooks/gsc-oauth-setup.md`).
_TARGET_SITE_TO_GSC_PROPERTY_ENV: dict[TargetSite, str] = {
    "wp_shosho": "GSC_PROPERTY_SHOSHO",
    "wp_fleet": "GSC_PROPERTY_FLEET",
}

# Default target_site when frontmatter doesn't specify one. `config/target-keywords.yaml`
# (ADR-008 §6) would be the authoritative lookup once populated; until then,
# default to the primary blog and let the user override via CLI.
_DEFAULT_TARGET_SITE: TargetSite = "wp_shosho"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class EnrichInputError(ValueError):
    """Malformed keyword-research input — missing frontmatter, wrong type, etc."""


class TargetSiteResolutionError(ValueError):
    """Couldn't map the input to a known `TargetSite`."""


class GSCPropertyNotConfiguredError(RuntimeError):
    """`GSC_PROPERTY_*` env var missing for the requested target_site."""


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedInput:
    """Structured view of the relevant bits of a keyword-research markdown."""

    topic: str
    topic_en: str | None
    content_type: str | None
    target_site_hint: TargetSite | None
    primary_keyword: str  # first core_keywords entry, required
    core_keywords: tuple[str, ...]  # all core keywords (deduped, ordered)
    source_path: Path


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split markdown front-matter. Raises `EnrichInputError` if missing or
    malformed (we accept only the `---\\n…\\n---\\n` form used by keyword-research).
    """
    if not text.startswith("---"):
        raise EnrichInputError("input markdown missing frontmatter (no leading '---')")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise EnrichInputError("input markdown has no closing '---' for frontmatter")
    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as e:
        raise EnrichInputError(f"frontmatter yaml parse failed: {e}") from e
    if not isinstance(fm, dict):
        raise EnrichInputError("frontmatter is not a mapping")
    body = parts[2].lstrip("\n")
    return fm, body


def parse_keyword_research_input(path: Path) -> ParsedInput:
    """Parse a `keyword-research` markdown file into a typed `ParsedInput`.

    Validates the discriminator (`type: keyword-research`) and that
    `core_keywords` is a non-empty list with a usable `keyword` string.

    Raises:
        EnrichInputError: file missing / frontmatter missing / wrong type /
            missing / empty `core_keywords`.
    """
    if not path.is_file():
        raise EnrichInputError(f"input path does not exist: {path}")

    text = path.read_text(encoding="utf-8")
    fm, _body = _split_frontmatter(text)

    got_type = fm.get("type")
    if got_type != "keyword-research":
        raise EnrichInputError(f"expected frontmatter type='keyword-research', got {got_type!r}")

    core = fm.get("core_keywords")
    if not isinstance(core, list) or not core:
        raise EnrichInputError("frontmatter `core_keywords` missing or empty")

    # Dedupe while preserving order.
    seen: set[str] = set()
    keywords: list[str] = []
    for entry in core:
        if not isinstance(entry, dict):
            continue
        kw = entry.get("keyword")
        if not isinstance(kw, str) or not kw.strip():
            continue
        stripped = kw.strip()
        if stripped in seen:
            continue
        seen.add(stripped)
        keywords.append(stripped)

    if not keywords:
        raise EnrichInputError(
            "frontmatter `core_keywords` has no entries with a non-empty `keyword` string"
        )

    target_hint_raw = fm.get("target_site")
    target_hint: TargetSite | None = None
    if target_hint_raw is not None:
        if target_hint_raw not in _TARGET_SITE_TO_GSC_PROPERTY_ENV:
            raise EnrichInputError(
                f"frontmatter `target_site` must be one of "
                f"{list(_TARGET_SITE_TO_GSC_PROPERTY_ENV)}; got {target_hint_raw!r}"
            )
        target_hint = target_hint_raw  # type: ignore[assignment]

    topic_raw = fm.get("topic") or ""
    topic = topic_raw.strip() if isinstance(topic_raw, str) else ""
    topic_en_raw = fm.get("topic_en")
    topic_en = topic_en_raw.strip() if isinstance(topic_en_raw, str) and topic_en_raw else None
    content_type_raw = fm.get("content_type")
    content_type = (
        content_type_raw.strip() if isinstance(content_type_raw, str) and content_type_raw else None
    )

    return ParsedInput(
        topic=topic,
        topic_en=topic_en,
        content_type=content_type,
        target_site_hint=target_hint,
        primary_keyword=keywords[0],
        core_keywords=tuple(keywords),
        source_path=path,
    )


# ---------------------------------------------------------------------------
# Target-site / GSC payload helpers
# ---------------------------------------------------------------------------


def resolve_target_site(parsed: ParsedInput, override: TargetSite | None = None) -> TargetSite:
    """Resolve the TargetSite from (in priority order) CLI override → frontmatter
    hint → default `_DEFAULT_TARGET_SITE`.

    Phase 1.5 TODO: when `config/target-keywords.yaml` lands (ADR-008 §6),
    consult keyword ownership there before falling back to the default.
    """
    if override is not None:
        if override not in _TARGET_SITE_TO_GSC_PROPERTY_ENV:
            raise TargetSiteResolutionError(
                f"override target_site {override!r} not in {list(_TARGET_SITE_TO_GSC_PROPERTY_ENV)}"
            )
        return override
    if parsed.target_site_hint is not None:
        return parsed.target_site_hint
    logger.warning(
        "no target_site in frontmatter for %s; defaulting to %s",
        parsed.source_path,
        _DEFAULT_TARGET_SITE,
    )
    return _DEFAULT_TARGET_SITE


def get_gsc_property_for_target_site(
    target_site: TargetSite, env: dict[str, str] | None = None
) -> str:
    """Map target_site → GSC property string from env. `env=None` reads `os.environ`."""
    env_map = env if env is not None else os.environ
    key = _TARGET_SITE_TO_GSC_PROPERTY_ENV[target_site]
    try:
        value = env_map[key]
    except KeyError as e:
        raise GSCPropertyNotConfiguredError(
            f"{key} env var not set for target_site={target_site!r}; "
            "see docs/runbooks/gsc-oauth-setup.md"
        ) from e
    value = value.strip()
    if not value:
        raise GSCPropertyNotConfiguredError(
            f"{key} env var is empty for target_site={target_site!r}"
        )
    return value


def build_gsc_query_payload(
    target_site: TargetSite,
    *,
    end_date: date,
    env: dict[str, str] | None = None,
) -> tuple[str, str, str]:
    """Build (gsc_property, start_date_iso, end_date_iso) for a 28-day window
    ending `end_date`. Caller should pass `today - _GSC_END_LAG_DAYS`.
    """
    start = end_date - timedelta(days=_GSC_WINDOW_DAYS - 1)
    gsc_property = get_gsc_property_for_target_site(target_site, env=env)
    return gsc_property, start.isoformat(), end_date.isoformat()


def _effective_end_date(now_fn: Callable[[], datetime] | None) -> date:
    """GSC end-date = today_taipei - 3d. Inject `now_fn` in tests."""
    now = now_fn() if now_fn is not None else datetime.now(tz=_TAIPEI)
    return (now.astimezone(_TAIPEI) - timedelta(days=_GSC_END_LAG_DAYS)).date()


# ---------------------------------------------------------------------------
# SEOContext assembly
# ---------------------------------------------------------------------------


def _build_keyword_metric(row: dict[str, Any]) -> KeywordMetricV1:
    """GSC row → `KeywordMetricV1`. Clamps `avg_position` into schema range."""
    keys = row.get("keys") or []
    keyword = keys[0] if keys else ""
    # schema requires avg_position in [1.0, 200.0]; GSC can return slightly < 1.0
    # for fresh / single-impression rows. Clamp into range with a log so we don't
    # silently lose data.
    raw_position = float(row.get("position", 1.0))
    position = max(1.0, min(200.0, raw_position))
    if position != raw_position:
        logger.debug("clamped avg_position %.3f → %.3f for %r", raw_position, position, keyword)
    return KeywordMetricV1(
        keyword=keyword,
        clicks=int(row.get("clicks", 0) or 0),
        impressions=int(row.get("impressions", 0) or 0),
        ctr=float(row.get("ctr", 0.0) or 0.0),
        avg_position=position,
        source="gsc",
    )


def _select_primary_metric(
    rows: list[dict[str, Any]], primary_keyword: str
) -> KeywordMetricV1 | None:
    """Find the best GSC row for `primary_keyword` (case-insensitive exact match),
    aggregated across URLs (sum clicks / impressions, impression-weighted position).
    Returns `None` if the keyword has no GSC rows.
    """
    target = primary_keyword.strip().casefold()
    matched = [
        r
        for r in rows
        if (r.get("keys") or [""])
        and isinstance(r["keys"][0], str)
        and r["keys"][0].strip().casefold() == target
    ]
    if not matched:
        return None

    clicks = sum(int(r.get("clicks", 0) or 0) for r in matched)
    impressions = sum(int(r.get("impressions", 0) or 0) for r in matched)
    # Impression-weighted avg position; fall back to simple mean if all rows zero.
    weighted = sum(
        float(r.get("position", 0.0)) * int(r.get("impressions", 0) or 0) for r in matched
    )
    if impressions > 0:
        avg_pos = weighted / impressions
    else:
        avg_pos = sum(float(r.get("position", 1.0)) for r in matched) / len(matched)
    ctr = (clicks / impressions) if impressions > 0 else 0.0

    # Reuse `_build_keyword_metric` for clamping consistency.
    return _build_keyword_metric(
        {
            "keys": [primary_keyword],  # keep canonical casing from input
            "clicks": clicks,
            "impressions": impressions,
            "ctr": min(1.0, max(0.0, ctr)),
            "position": avg_pos,
        }
    )


def _select_related_metrics(
    rows: list[dict[str, Any]], primary_keyword: str, cap: int = _MAX_RELATED_KEYWORDS
) -> list[KeywordMetricV1]:
    """Top-N unique queries by impressions, excluding primary keyword.

    De-duplicates by keyword (GSC returns one row per query × page), picking
    the row with the highest impressions per keyword.
    """
    target = primary_keyword.strip().casefold()
    per_keyword: dict[str, dict[str, Any]] = {}
    for r in rows:
        keys = r.get("keys") or []
        if not keys or not isinstance(keys[0], str):
            continue
        kw = keys[0].strip()
        if not kw or kw.casefold() == target:
            continue
        prev = per_keyword.get(kw)
        if prev is None or int(r.get("impressions", 0) or 0) > int(prev.get("impressions", 0) or 0):
            per_keyword[kw] = r

    ranked = sorted(
        per_keyword.values(),
        key=lambda r: int(r.get("impressions", 0) or 0),
        reverse=True,
    )[:cap]
    return [_build_keyword_metric(r) for r in ranked]


def build_seo_context(
    *,
    rows: list[dict[str, Any]],
    target_site: TargetSite,
    primary_keyword: str,
    source_path: Path,
    now_fn: Callable[[], datetime] | None = None,
    thresholds: dict[str, Any] | None = None,
) -> SEOContextV1:
    """Assemble a `SEOContextV1` from GSC rows + metadata.

    `now_fn` injected for testability (default `datetime.now(timezone.utc)`).
    `thresholds` optionally override cannibalization config; `None` reads yaml.
    `competitor_serp_summary` is stubbed to `None` in Slice B.
    """
    now = now_fn() if now_fn is not None else datetime.now(tz=timezone.utc)
    # Ensure tz-aware UTC (AwareDatetime requirement).
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    primary_metric = _select_primary_metric(rows, primary_keyword)
    related = _select_related_metrics(rows, primary_keyword)
    striking = filter_striking_distance(rows)
    thresholds_cfg = thresholds if thresholds is not None else load_cannibalization_thresholds()
    warnings = detect_cannibalization(rows, thresholds=thresholds_cfg)

    return SEOContextV1(
        target_site=target_site,
        primary_keyword=primary_metric,
        related_keywords=related,
        striking_distance=striking,
        cannibalization_warnings=warnings,
        competitor_serp_summary=None,  # Phase 1.5 — firecrawl stub
        generated_at=now,
        source_keyword_research_path=str(source_path),
    )


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _render_frontmatter(ctx: SEOContextV1) -> str:
    fm = {
        "type": "seo-context",
        "schema_version": int(ctx.schema_version),
        "target_site": ctx.target_site,
        "phase": "1 (gsc-only)",
        "generated_at": ctx.generated_at.isoformat(),
        "source_keyword_research_path": ctx.source_keyword_research_path,
    }
    # `sort_keys=False` preserves the order above; `allow_unicode` for CJK paths.
    return yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip() + "\n"


def _render_human_summary(ctx: SEOContextV1) -> list[str]:
    lines = ["## 人類可讀摘要", ""]
    if ctx.primary_keyword is not None:
        pk = ctx.primary_keyword
        lines.append(
            f"- **Primary keyword**：{pk.keyword}（clicks {pk.clicks}，"
            f"impressions {pk.impressions}，avg pos {pk.avg_position:.1f}）"
        )
    else:
        lines.append("- **Primary keyword**：GSC 28 天窗內無資料（可能是新頁 / 無排名）")
    lines.append(f"- **Related keywords**：{len(ctx.related_keywords)} 組")
    lines.append(f"- **Striking distance**：{len(ctx.striking_distance)} 筆機會（排名 11-20）")
    if ctx.striking_distance:
        for sd in ctx.striking_distance[:5]:
            lines.append(
                f"  - {sd.keyword}（目前 {sd.current_position:.1f}，"
                f"28d impressions {sd.impressions_last_28d}）"
            )
    lines.append(f"- **Cannibalization warnings**：{len(ctx.cannibalization_warnings)} 筆")
    if ctx.cannibalization_warnings:
        for w in ctx.cannibalization_warnings[:5]:
            lines.append(f"  - [{w.severity}] {w.keyword} — {w.recommendation}")
    if ctx.competitor_serp_summary is None:
        lines.append("- **Competitor SERP**：未提供（Phase 1.5 firecrawl 整合後補上）")
    return lines


def render_output_markdown(ctx: SEOContextV1) -> str:
    """Serialize `ctx` to the markdown form specified in ADR-009 §B.4.

    Contract: the JSON code fence must round-trip through
    `SEOContextV1.model_validate_json(...)` byte-for-byte.
    """
    frontmatter = _render_frontmatter(ctx)
    json_block = ctx.model_dump_json(indent=2)
    body_lines: list[str] = [
        "# SEO enrichment result",
        "",
        "## SEOContextV1 (JSON)",
        "",
        "```json",
        json_block,
        "```",
        "",
    ]
    body_lines.extend(_render_human_summary(ctx))
    return "---\n" + frontmatter + "---\n\n" + "\n".join(body_lines) + "\n"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _output_filename(input_stem: str, now_fn: Callable[[], datetime] | None = None) -> str:
    now = now_fn() if now_fn is not None else datetime.now(tz=_TAIPEI)
    date_str = now.astimezone(_TAIPEI).strftime("%Y%m%d")
    return f"enriched-{input_stem}-{date_str}.md"


def enrich(
    *,
    input_path: Path,
    output_dir: Path,
    client: GSCClient | None = None,
    target_site_override: TargetSite | None = None,
    now_fn: Callable[[], datetime] | None = None,
    env: dict[str, str] | None = None,
) -> Path:
    """Run the full GSC enrich pipeline end-to-end and return the output path.

    `client=None` triggers `GSCClient.from_env()` so production calls don't
    need to plumb credentials; tests inject a fake client.
    """
    parsed = parse_keyword_research_input(input_path)
    target_site = resolve_target_site(parsed, override=target_site_override)

    end_date = _effective_end_date(now_fn)
    gsc_property, start_iso, end_iso = build_gsc_query_payload(
        target_site, end_date=end_date, env=env
    )

    if client is None:
        client = GSCClient.from_env()

    logger.info(
        "gsc_enrich_start input=%s target_site=%s property=%s window=%s..%s",
        input_path,
        target_site,
        gsc_property,
        start_iso,
        end_iso,
    )
    rows = client.query(
        site=gsc_property,
        start_date=start_iso,
        end_date=end_iso,
        dimensions=["query", "page"],
        row_limit=1000,
    )
    logger.info("gsc_enrich_rows count=%d", len(rows))

    ctx = build_seo_context(
        rows=rows,
        target_site=target_site,
        primary_keyword=parsed.primary_keyword,
        source_path=input_path,
        now_fn=lambda: datetime.now(tz=timezone.utc),
    )
    md = render_output_markdown(ctx)

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / _output_filename(input_path.stem, now_fn=now_fn)
    out_path.write_text(md, encoding="utf-8")
    logger.info("gsc_enrich_wrote path=%s", out_path)
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SEO keyword enrich pipeline — GSC-only baseline (ADR-009 Slice B)."
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Path to a `keyword-research` markdown report",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory to write the enriched markdown report",
    )
    parser.add_argument(
        "--target-site",
        choices=list(_TARGET_SITE_TO_GSC_PROPERTY_ENV.keys()),
        default=None,
        help="Override target_site resolution (else: frontmatter hint or default)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip the real GSC call; print the query payload and exit.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.dry_run:
        parsed = parse_keyword_research_input(args.input)
        target_site = resolve_target_site(parsed, override=args.target_site)
        end_date = _effective_end_date(None)
        gsc_property, start_iso, end_iso = build_gsc_query_payload(target_site, end_date=end_date)
        payload = {
            "input": str(args.input),
            "target_site": target_site,
            "gsc_property": gsc_property,
            "start_date": start_iso,
            "end_date": end_iso,
            "primary_keyword": parsed.primary_keyword,
            "core_keywords": list(parsed.core_keywords),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    out_path = enrich(
        input_path=args.input,
        output_dir=args.output_dir,
        target_site_override=args.target_site,
    )
    print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
