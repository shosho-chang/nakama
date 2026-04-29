"""Audit runner — subprocess wrapper around `seo-audit-post` script (PRD #226 slice 4).

Public surface:

    AuditRunResult     — what `run` returns (audit_id + status + error info)
    run(...)           — kick off one audit synchronously (BackgroundTask blocks here)

The runner shell-outs to `.claude/skills/seo-audit-post/scripts/audit.py`
(unchanged per PRD §"`seo-audit-post` skill / pipeline 既有不動"). On success
it parses the resulting markdown's frontmatter + suggestion sections into
`AuditSuggestionV1` records, persists them via `audit_results_store.insert_run`,
and returns the new audit_id.

Why subprocess (not in-process import):

1. The audit pipeline pulls heavy deps (PageSpeed Insights HTTP, BeautifulSoup,
   Anthropic SDK for L1-L12 LLM checks). Running it inside the bridge process
   would couple uvicorn worker memory + crash blast radius to a single audit.
2. The script accepts a CLI surface that is already battle-tested
   (Phase 1.5 acceptance done 2026-04-27, see
   `memory/claude/project_seo_phase15_acceptance_done_2026_04_27.md`).
   Re-using it via subprocess keeps the contract narrow.
3. PRD §"Implementation Decisions" says BackgroundTasks not worker queue;
   subprocess gives us a clean process boundary without introducing celery / rq.

Hidden constraints:

- The script writes a markdown file to `--output-dir` and prints
  `{"output_path": "<path>"}` to stdout. We capture stdout, parse the JSON,
  read the file, then delete the temp dir. (Tests can keep the file via
  `keep_temp=True` for inspection.)
- Subprocess stdout/stderr is UTF-8. On Windows, `subprocess.run(text=True)`
  uses cp1252 by default → we explicitly `encoding="utf-8"` to avoid mojibake
  (`feedback_windows_stdout_utf8.md`).
- The script can take 30-60s. The runner sets a 300s hard timeout; PRD §
  Trace says "single-post wall-clock ~30-60s" so 5x headroom is plenty without
  letting a wedged subprocess pin a worker forever.

Not a CLI:
This module is import-only. The script `audit.py` IS the CLI;
`audit_runner` is the wrapper that bridge.py / BackgroundTask call.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import yaml

from shared import audit_results_store
from shared.log import get_logger
from shared.schemas.publishing import TargetSite
from shared.schemas.seo_audit_review import (
    AuditSuggestionV1,
    OverallGrade,
)

logger = get_logger("nakama.brook.audit_runner")

# Path to the audit script (relative to repo root). Kept as module-level so
# tests can monkeypatch a fake script for fast subprocess fakes.
_REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_SCRIPT_PATH = _REPO_ROOT / ".claude" / "skills" / "seo-audit-post" / "scripts" / "audit.py"

# Hard timeout to avoid a wedged subprocess pinning a worker. PRD said
# wall-clock 30-60s; 5x headroom = 300s.
_DEFAULT_TIMEOUT_S = 300

# Frontmatter tag for grade lookup. The `audit.py::_render_frontmatter`
# emits this exact key under `summary.overall_grade`.
_GRADE_KEYS = ("summary", "overall_grade")
_COUNT_KEYS = {
    "pass": ("summary", "pass"),
    "warn": ("summary", "warn"),
    "fail": ("summary", "fail"),
    "skip": ("summary", "skip"),
}


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditRunResult:
    """Outcome of `run`. Bridge BackgroundTask consumers don't need the markdown
    body — they redirect by `audit_id` and let the result page read from the DB.
    """

    audit_id: Optional[int]  # populated when status == "ok"
    status: str  # "ok" | "error"
    error_stage: Optional[str] = None  # "subprocess" | "parse" | "persist"
    error_message: Optional[str] = None


# ---------------------------------------------------------------------------
# Subprocess invocation
# ---------------------------------------------------------------------------


def _run_subprocess(
    *,
    url: str,
    output_dir: Path,
    focus_keyword: Optional[str],
    extra_args: tuple[str, ...],
    timeout_s: int,
) -> str:
    """Invoke the audit script. Returns the markdown file path it produced.

    Raises:
        RuntimeError — if the script fails, times out, or emits malformed JSON.
    """
    cmd: list[str] = [
        sys.executable,
        str(AUDIT_SCRIPT_PATH),
        "--url",
        url,
        "--output-dir",
        str(output_dir),
        # Slice 4 keeps the audit lean — we don't need KB internal-link
        # suggestions for the review UX in v1, and `--no-kb` shaves a Haiku
        # call (~$0.005 + 3s). Slice #234 may flip this if the review page
        # surfaces internal-link suggestions.
        "--no-kb",
    ]
    if focus_keyword:
        cmd.extend(["--focus-keyword", focus_keyword])
    cmd.extend(extra_args)

    logger.info("audit_runner subprocess_start url=%s", url)
    try:
        proc = subprocess.run(  # noqa: S603 — args list, not shell
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"audit subprocess exceeded {timeout_s}s: {exc}") from exc

    if proc.returncode != 0:
        # Surface stderr + last bit of stdout so the failing audit's "why" is
        # discoverable without re-running the subprocess.
        tail = (proc.stdout or "")[-500:]
        raise RuntimeError(
            f"audit subprocess exit={proc.returncode} stderr={proc.stderr!r} stdout_tail={tail!r}"
        )

    # Last non-empty stdout line should be `{"output_path": "..."}`. The
    # script may print logging on earlier lines; grab the JSON line.
    last_json_line = None
    for line in (proc.stdout or "").splitlines()[::-1]:
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            last_json_line = line
            break
    if last_json_line is None:
        raise RuntimeError(f"audit subprocess produced no JSON line; stdout={proc.stdout!r}")
    try:
        payload = json.loads(last_json_line)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"audit subprocess JSON parse failed: {exc}; line={last_json_line!r}"
        ) from exc

    output_path = payload.get("output_path")
    if not output_path:
        raise RuntimeError(f"audit subprocess JSON missing output_path: {payload!r}")
    return output_path


# ---------------------------------------------------------------------------
# Markdown parsing
# ---------------------------------------------------------------------------

# Frontmatter delimiter. The script emits `---\n<yaml>\n---\n\n`.
_FRONTMATTER_RE = re.compile(r"^---\n(?P<yaml>.+?)\n---\n", re.DOTALL)


def _parse_frontmatter(markdown: str) -> dict:
    match = _FRONTMATTER_RE.match(markdown)
    if not match:
        raise RuntimeError("audit markdown has no YAML frontmatter")
    try:
        return yaml.safe_load(match.group("yaml")) or {}
    except yaml.YAMLError as exc:
        raise RuntimeError(f"audit markdown frontmatter is not YAML: {exc}") from exc


def _nested_get(d: dict, keys: tuple[str, ...]) -> Optional[object]:
    cur: object = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _coerce_grade(raw: object) -> OverallGrade:
    """Validate the frontmatter grade is one of A/B+/B/C+/C/D/F."""
    if not isinstance(raw, str):
        raise RuntimeError(f"audit markdown overall_grade not a string: {raw!r}")
    if raw not in ("A", "B+", "B", "C+", "C", "D", "F"):
        raise RuntimeError(f"audit markdown overall_grade unknown: {raw!r}")
    return raw  # type: ignore[return-value]


def _coerce_count(raw: object, key: str) -> int:
    if not isinstance(raw, int) or raw < 0:
        raise RuntimeError(f"audit markdown summary.{key} not a non-negative int: {raw!r}")
    return raw


# Section parser:
#   `## 2. Critical Fixes（必修）`  → severity=fail
#   `## 3. Warnings（建議修）`     → severity=warn (covers both warns and non-critical fails)
#   Each block has the form:
#       ### [<rule_id>] <title>
#       (blank)
#       - **Actual**: <current_value>
#       - **Expected**: <suggested_value>
#       - **Fix**: <fix_suggestion>     # optional
#       (blank)
_SECTION_HEADERS = (
    ("## 2.", "fail"),
    ("## 3.", "warn"),
)
_RULE_HEADER_RE = re.compile(r"^### \[(?P<rule_id>[^\]]+)\] (?P<title>.+)$")
_FIELD_RE = re.compile(r"^- \*\*(?P<key>Actual|Expected|Fix)\*\*: (?P<value>.*)$")


def _split_sections(markdown: str) -> dict[str, str]:
    """Return a dict mapping section header (`## 2.` / `## 3.`) -> body string.

    Body extends from the line after the header until the next `## ` line or
    end of file. Body for unknown sections is dropped.
    """
    out: dict[str, str] = {}
    current_key: Optional[str] = None
    current_lines: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("## "):
            if current_key is not None:
                out[current_key] = "\n".join(current_lines)
                current_lines = []
            # Decide if this section is one we care about.
            current_key = None
            for prefix, _severity in _SECTION_HEADERS:
                if line.startswith(prefix):
                    current_key = prefix
                    break
            continue
        if current_key is not None:
            current_lines.append(line)
    if current_key is not None:
        out[current_key] = "\n".join(current_lines)
    return out


def _parse_section_blocks(body: str) -> list[tuple[str, str, dict[str, str]]]:
    """Yield `(rule_id, title, fields)` for each `### [X] Y` block in `body`.

    `fields` keys: 'actual' / 'expected' / 'fix' (lowercased), missing keys
    default to "" downstream. Empty body returns []. Skips obvious non-block
    lines (e.g. `（無）` placeholder).
    """
    blocks: list[tuple[str, str, dict[str, str]]] = []
    cur_rule: Optional[tuple[str, str]] = None
    cur_fields: dict[str, str] = {}

    def _flush() -> None:
        if cur_rule is not None:
            blocks.append((cur_rule[0], cur_rule[1], dict(cur_fields)))

    for line in body.splitlines():
        m = _RULE_HEADER_RE.match(line.strip())
        if m:
            _flush()
            cur_rule = (m.group("rule_id"), m.group("title").strip())
            cur_fields = {}
            continue
        f = _FIELD_RE.match(line.strip())
        if f and cur_rule is not None:
            cur_fields[f.group("key").lower()] = f.group("value").strip()
    _flush()
    return blocks


def _markdown_to_suggestions(markdown: str) -> list[AuditSuggestionV1]:
    """Translate the audit markdown into a list of `AuditSuggestionV1`.

    Per PRD §"Review semantics": only `fail` + `warn` are persisted (`pass`
    + `skip` are excluded). The audit script's `## 2. Critical Fixes` and
    `## 3. Warnings` sections cover those two severities respectively.
    """
    sections = _split_sections(markdown)
    out: list[AuditSuggestionV1] = []
    for prefix, severity in _SECTION_HEADERS:
        body = sections.get(prefix, "")
        for rule_id, title, fields in _parse_section_blocks(body):
            current_value = fields.get("actual", "")
            suggested_value = fields.get("expected", "")
            fix = fields.get("fix", "")
            # Use 'expected' as `suggested_value`; 'fix' supplements rationale.
            rationale = fix
            out.append(
                AuditSuggestionV1(
                    rule_id=rule_id,
                    severity=severity,  # type: ignore[arg-type]
                    title=title,
                    current_value=current_value,
                    suggested_value=suggested_value,
                    rationale=rationale,
                )
            )
    return out


# ---------------------------------------------------------------------------
# target_site resolution
# ---------------------------------------------------------------------------


def _resolve_target_site(url: str) -> Optional[TargetSite]:
    """Map URL host → target_site app name. None for non-WP / unknown host."""
    from shared.schemas.site_mapping import UnknownHostError, host_to_target_site

    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    if not host:
        return None
    try:
        return host_to_target_site(host)
    except UnknownHostError:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run(
    url: str,
    *,
    target_site: Optional[TargetSite] = None,
    wp_post_id: Optional[int] = None,
    focus_keyword: str = "",
    timeout_s: int = _DEFAULT_TIMEOUT_S,
    extra_args: tuple[str, ...] = (),
    keep_temp: bool = False,
) -> AuditRunResult:
    """Run one audit synchronously.

    Args:
        url: target URL to audit (with scheme).
        target_site: explicit override; defaults to URL-derived lookup.
        wp_post_id: WP post id when audit was kicked off from section 1.
                    None for external audits.
        focus_keyword: forwarded to `audit.py --focus-keyword`. Empty omits.
        timeout_s: subprocess hard timeout.
        extra_args: appended to the script CLI (e.g. `('--via-firecrawl',)`).
        keep_temp: when True, the markdown tmp dir is NOT deleted (debugging).

    Returns:
        `AuditRunResult` with `audit_id` populated on success, or `status='error'`
        + `error_stage` / `error_message` on failure.

    On error: nothing is persisted (no DB row), the temp markdown is cleaned
    up unless `keep_temp=True`. The router displays `error_stage` +
    `error_message` on the progress page.
    """
    resolved_target_site = target_site or _resolve_target_site(url)

    tmp_root: Optional[tempfile.TemporaryDirectory] = None
    try:
        tmp_root = tempfile.TemporaryDirectory(prefix="nakama_audit_")
        tmp_path = Path(tmp_root.name)

        # 1. Subprocess
        try:
            output_path_str = _run_subprocess(
                url=url,
                output_dir=tmp_path,
                focus_keyword=focus_keyword or None,
                extra_args=tuple(extra_args),
                timeout_s=timeout_s,
            )
        except RuntimeError as exc:
            logger.warning("audit_runner subprocess_failed url=%s err=%s", url, exc)
            return AuditRunResult(
                audit_id=None,
                status="error",
                error_stage="subprocess",
                error_message=str(exc),
            )

        markdown_path = Path(output_path_str)
        if not markdown_path.exists():
            return AuditRunResult(
                audit_id=None,
                status="error",
                error_stage="subprocess",
                error_message=(
                    f"audit script claimed output_path={output_path_str} but file missing"
                ),
            )
        markdown = markdown_path.read_text(encoding="utf-8")

        # 2. Parse
        try:
            frontmatter = _parse_frontmatter(markdown)
            grade = _coerce_grade(_nested_get(frontmatter, _GRADE_KEYS))
            counts = {
                key: _coerce_count(_nested_get(frontmatter, path), ".".join(path))
                for key, path in _COUNT_KEYS.items()
            }
            suggestions = _markdown_to_suggestions(markdown)
        except RuntimeError as exc:
            logger.warning("audit_runner parse_failed url=%s err=%s", url, exc)
            return AuditRunResult(
                audit_id=None,
                status="error",
                error_stage="parse",
                error_message=str(exc),
            )

        # 3. Persist
        try:
            audit_id = audit_results_store.insert_run(
                url=url,
                target_site=resolved_target_site,
                wp_post_id=wp_post_id,
                focus_keyword=focus_keyword,
                audited_at=datetime.now(timezone.utc),
                overall_grade=grade,
                pass_count=counts["pass"],
                warn_count=counts["warn"],
                fail_count=counts["fail"],
                skip_count=counts["skip"],
                suggestions=suggestions,
                raw_markdown=markdown,
            )
        except Exception as exc:  # noqa: BLE001 — surface any persistence failure
            logger.warning("audit_runner persist_failed url=%s err=%s", url, exc)
            return AuditRunResult(
                audit_id=None,
                status="error",
                error_stage="persist",
                error_message=f"{type(exc).__name__}: {exc}",
            )

        logger.info(
            "audit_runner ok audit_id=%d url=%s grade=%s suggestions=%d",
            audit_id,
            url,
            grade,
            len(suggestions),
        )
        return AuditRunResult(audit_id=audit_id, status="ok")
    finally:
        if tmp_root is not None and not keep_temp:
            try:
                tmp_root.cleanup()
            except OSError:  # pragma: no cover — best-effort
                pass


__all__ = [
    "AuditRunResult",
    "AUDIT_SCRIPT_PATH",
    "run",
]
