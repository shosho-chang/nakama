"""KB search skill — call Robin /kb/research and render markdown.

The Robin agent already implements a Claude-Haiku-ranked KB search over
``KB/Wiki/{Sources,Concepts,Entities}`` (see ``agents/robin/kb_search.py``,
PR #119, 100% test coverage).  This skill is a thin HTTP client wrapper —
it calls the existing endpoint, parses the JSON response, and renders a
markdown report with a downstream-consumable frontmatter block.

Design — pure functions + injectable HTTP poster
------------------------------------------------
Functions are split so each unit is directly testable without spinning up
a real ``thousand_sunny`` instance:

- ``build_request_payload(query)``                        pure
- ``parse_response(payload)``                             pure
- ``render_markdown(query, hits, generated_at, source)``  pure
- ``run_search(query, …, post=…, now_fn=…)``              orchestrator —
                                                          ``post=None`` uses
                                                          ``httpx.post``,
                                                          ``now_fn=None`` uses
                                                          ``datetime.now``;
                                                          tests inject both.

CLI:

    python .claude/skills/kb-search/scripts/search.py \\
        --query "zone 2 訓練" [--limit 8] [--out -|<path>] \\
        [--api-base http://127.0.0.1:8000] [--api-key $WEB_SECRET]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

DEFAULT_API_BASE = "http://127.0.0.1:8000"
DEFAULT_LIMIT = 8
DEFAULT_TIMEOUT_SECS = 60.0


# ---------------------------------------------------------------------------
# Data shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KbHit:
    """Single hit returned by Robin's ``/kb/research`` endpoint.

    Mirrors the response shape from ``agents/robin/kb_search.py`` —
    type / title / path / preview / relevance_reason. Frozen so accidental
    mutation between parse and render fails loudly.
    """

    type: str
    title: str
    path: str
    preview: str
    relevance_reason: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "KbHit":
        return cls(
            type=str(d.get("type", "")),
            title=str(d.get("title", "")),
            path=str(d.get("path", "")),
            preview=str(d.get("preview", "")),
            relevance_reason=str(d.get("relevance_reason", "")),
        )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def build_request_payload(query: str) -> dict[str, str]:
    """Build the form-encoded body for ``POST /kb/research``."""
    return {"query": query}


def parse_response(payload: dict[str, Any]) -> list[KbHit]:
    """Parse the JSON body returned by ``/kb/research`` into ``KbHit``s.

    The endpoint returns ``{"results": [...]}``. Anything else (missing key,
    non-list value, malformed dict items) is treated as an empty result —
    callers downstream can detect ``len(hits) == 0`` and surface "no hits"
    to the user.
    """
    results = payload.get("results")
    if not isinstance(results, list):
        return []
    hits: list[KbHit] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        hits.append(KbHit.from_dict(item))
    return hits


def _fmt_iso_utc(dt: datetime) -> str:
    """ISO 8601 with explicit ``+00:00`` offset (downstream-friendly)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def render_markdown(
    *,
    query: str,
    hits: list[KbHit],
    generated_at: datetime,
    api_base: str,
) -> str:
    """Render the skill's stable output contract.

    Frontmatter ``type: kb-search-result`` is the discriminator; downstream
    consumers can look for it without parsing the body.
    """
    fm_lines = [
        "---",
        "type: kb-search-result",
        "schema_version: 1",
        f"generated_at: {_fmt_iso_utc(generated_at)}",
        f"api_base: {api_base}",
        f"query: {json.dumps(query, ensure_ascii=False)}",
        f"total_hits: {len(hits)}",
        "---",
        "",
    ]

    body_lines: list[str] = ["# KB Search Result", "", f"Query: **{query}**", ""]

    if not hits:
        body_lines += ["_No hits._", ""]
        return "\n".join(fm_lines + body_lines)

    body_lines += ["## Top hits", ""]
    for i, hit in enumerate(hits, start=1):
        body_lines.append(f"{i}. **{hit.title}** — `{hit.path}` ({hit.type})")
        if hit.relevance_reason:
            body_lines.append(f"   - Relevance: {hit.relevance_reason}")
        if hit.preview:
            preview = hit.preview.replace("\n", " ").strip()
            body_lines.append(f"   - Preview: {preview}")
        body_lines.append("")

    body_lines += ["## Wiki page candidates", ""]
    for hit in hits:
        body_lines.append(f"- [[{hit.path}]]")
    body_lines.append("")

    return "\n".join(fm_lines + body_lines)


# ---------------------------------------------------------------------------
# HTTP transport
# ---------------------------------------------------------------------------


PostFn = Callable[[str, dict[str, str], dict[str, str]], dict[str, Any]]
"""Signature: ``post(url, form_data, headers) -> response_json``.

Tests inject a fake to skip the network; production uses ``_default_post``
which wraps ``httpx.post``.
"""


class KbSearchError(RuntimeError):
    """Raised when the endpoint returns a non-2xx or malformed body."""


def _default_post(url: str, data: dict[str, str], headers: dict[str, str]) -> dict[str, Any]:
    try:
        resp = httpx.post(url, data=data, headers=headers, timeout=DEFAULT_TIMEOUT_SECS)
    except httpx.HTTPError as e:  # network / DNS / timeout
        raise KbSearchError(f"HTTP error calling {url}: {e}") from e
    if resp.status_code != 200:
        raise KbSearchError(f"{url} returned {resp.status_code}: {resp.text[:200]}")
    try:
        return resp.json()
    except (ValueError, json.JSONDecodeError) as e:
        raise KbSearchError(f"{url} returned non-JSON body: {e}") from e


def _build_headers(api_key: str | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if api_key:
        headers["X-Robin-Key"] = api_key
    return headers


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_search(
    *,
    query: str,
    api_base: str = DEFAULT_API_BASE,
    api_key: str | None = None,
    limit: int = DEFAULT_LIMIT,
    post: PostFn | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> tuple[list[KbHit], str]:
    """Run a KB search and return (hits, markdown).

    ``post`` and ``now_fn`` are injection seams for tests — pass ``None``
    and the orchestrator falls back to ``httpx.post`` and ``datetime.now``.
    Both must be forwarded to every call site that observes them; missing
    a forward is the same flavour of bug called out in
    ``feedback_skill_scaffolding_pitfalls.md`` (3rd pitfall).
    """
    if not query or not query.strip():
        raise ValueError("query must be non-empty")
    if limit < 1:
        raise ValueError(f"limit must be >= 1 (got {limit})")

    poster = post if post is not None else _default_post
    clock = now_fn if now_fn is not None else (lambda: datetime.now(tz=timezone.utc))

    url = api_base.rstrip("/") + "/kb/research"
    payload = build_request_payload(query)
    headers = _build_headers(api_key)

    response_json = poster(url, payload, headers)
    hits = parse_response(response_json)

    # Server caps at TOP_K=8 today; client-side limit lets callers ask for
    # fewer (e.g. for inline summaries) without a server-side change.
    capped = hits[:limit]

    md = render_markdown(
        query=query,
        hits=capped,
        generated_at=clock(),
        api_base=api_base,
    )
    return capped, md


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kb-search",
        description="Search the Robin KB and render a markdown report.",
    )
    p.add_argument("--query", required=True, help="Natural-language search query.")
    p.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Max hits to keep client-side (default {DEFAULT_LIMIT}; server caps at 8).",
    )
    p.add_argument(
        "--out",
        default="-",
        help="Output path; '-' (default) writes to stdout.",
    )
    p.add_argument(
        "--api-base",
        default=os.environ.get("NAKAMA_API_BASE", DEFAULT_API_BASE),
        help=(f"thousand_sunny base URL (default $NAKAMA_API_BASE or {DEFAULT_API_BASE})."),
    )
    p.add_argument(
        "--api-key",
        default=os.environ.get("WEB_SECRET") or None,
        help="X-Robin-Key value; falls back to $WEB_SECRET. Optional in dev mode.",
    )
    return p


def _write_output(markdown: str, out: str) -> None:
    if out == "-":
        sys.stdout.write(markdown)
        if not markdown.endswith("\n"):
            sys.stdout.write("\n")
        return
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        hits, md = run_search(
            query=args.query,
            api_base=args.api_base,
            api_key=args.api_key,
            limit=args.limit,
        )
    except (ValueError, KbSearchError) as e:
        sys.stderr.write(f"kb-search: {e}\n")
        return 2
    _write_output(md, args.out)
    if args.out != "-":
        sys.stderr.write(f"kb-search: wrote {len(hits)} hit(s) → {args.out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
