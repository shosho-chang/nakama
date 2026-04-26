"""Auto-archive alerts to Markdown stubs in `data/incidents-pending/`.

Triggered by `shared.alerts` (error severity) and `agents.franky.alert_router`
(critical severity) on the post-dedup path. Pure file IO — no Slack, no DB write.

Mac-side sync hook (out of scope) later moves files into vault `Incidents/YYYY/MM/`.
On VPS, files accumulate in `data/incidents-pending/` until that move happens.

**Dedup rule**: same `(date_local, rule_id)` → append a row under `## Repeat fires`
section in the existing file, **don't** create a new file. Re-fires across days
get separate files (one per day per rule).

The stub mirrors `docs/templates/incident-postmortem.md` schema (frontmatter +
Summary/Timeline/etc empty placeholders). Severity strings from caller map to
SEV tiers: critical→SEV-1, error→SEV-2, warning/warn→SEV-3, info→SEV-4.

Tests pin `pending_dir` explicitly. Production callers pass None → reads
`NAKAMA_INCIDENTS_PENDING_DIR` env or defaults to `<repo>/data/incidents-pending`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from shared.log import get_logger

logger = get_logger("nakama.incident_archive")

_TPE = ZoneInfo("Asia/Taipei")
_REPO_ROOT = Path(__file__).resolve().parent.parent
_FALLBACK_PENDING_DIR = _REPO_ROOT / "data" / "incidents-pending"

_SEVERITY_TO_TIER = {
    "critical": "SEV-1",
    "error": "SEV-2",
    "warning": "SEV-3",
    "warn": "SEV-3",
    "info": "SEV-4",
}

_REPEAT_SECTION = "## Repeat fires"
_REPEAT_HEADER_LINE = "| 時間（Asia/Taipei）| 訊息 |"
_REPEAT_SEPARATOR_LINE = "|---|---|"


@dataclass(frozen=True)
class ArchivedIncident:
    path: Path
    rule_id: str
    fired_at_local: datetime
    is_new: bool


def default_pending_dir() -> Path:
    """Resolve pending dir from env or repo default. Re-resolved per call so
    tests can `monkeypatch.setenv` mid-flight."""
    override = os.environ.get("NAKAMA_INCIDENTS_PENDING_DIR")
    return Path(override) if override else _FALLBACK_PENDING_DIR


def archive_incident(
    *,
    rule_id: str,
    severity: str,
    title: str,
    message: str,
    fired_at: datetime,
    context: dict[str, Any] | None = None,
    pending_dir: Path | None = None,
) -> ArchivedIncident | None:
    """Write or append an incident stub for a fired alert.

    Returns ArchivedIncident on success, or None if `pending_dir` is unwritable
    (logged at error; archiving must never raise into the alert dispatch path).
    """
    target_dir = pending_dir if pending_dir is not None else default_pending_dir()
    fired_local = (
        fired_at.astimezone(_TPE)
        if fired_at.tzinfo
        else fired_at.replace(tzinfo=timezone.utc).astimezone(_TPE)
    )

    slug = _slugify(rule_id)
    filename = f"{fired_local:%Y-%m-%d}-{slug}.md"
    path = target_dir / filename

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error("incident_archive: mkdir %s failed: %s", target_dir, exc)
        return None

    if path.exists():
        return _append_repeat(path, rule_id, fired_local, message)

    body = _render_stub(
        rule_id=rule_id,
        severity=severity,
        title=title,
        message=message,
        fired_local=fired_local,
        context=context or {},
    )
    try:
        path.write_text(body, encoding="utf-8")
    except OSError as exc:
        logger.error("incident_archive: write %s failed: %s", path, exc)
        return None

    logger.info("incident_archive: new stub path=%s rule=%s", path, rule_id)
    return ArchivedIncident(path=path, rule_id=rule_id, fired_at_local=fired_local, is_new=True)


def _slugify(rule_id: str) -> str:
    """Map a rule_id to a filesystem-safe slug. Allow `[a-z0-9_-]`, collapse
    other chars to `-`, strip leading/trailing `-`, cap at 80 chars."""
    s = rule_id.lower()
    s = re.sub(r"[^a-z0-9_-]+", "-", s)
    s = s.strip("-")
    return s[:80] or "unknown"


def _render_stub(
    *,
    rule_id: str,
    severity: str,
    title: str,
    message: str,
    fired_local: datetime,
    context: dict[str, Any],
) -> str:
    sev_tier = _SEVERITY_TO_TIER.get(severity.lower(), "SEV-3")
    detected_iso = fired_local.replace(microsecond=0).isoformat()
    postmortem_due = (fired_local + timedelta(days=7)).strftime("%Y-%m-%d")
    slug = _slugify(rule_id)
    title_yaml = title.replace('"', '\\"')
    category_tag = rule_id.split("-", 1)[0].split("_", 1)[0] or "alert"

    if context:
        ctx_lines = "\n".join(f"- `{k}`: {context[k]}" for k in sorted(context.keys()))
    else:
        ctx_lines = "_(無 context payload)_"

    return f"""---
id: {fired_local:%Y-%m-%d}-{slug}
title: "{title_yaml}"
severity: {sev_tier}
status: detected
detected_at: {detected_iso}
mitigated_at:
resolved_at:
postmortem_due: {postmortem_due}
trigger: {rule_id}
owner: 修修
tags:
  - incident
  - auto-archived
  - {category_tag}
---

# {title}

> **Auto-archived** by `shared.incident_archive.archive_incident()` at fire time.
> 流程見 `docs/runbooks/postmortem-process.md`（postmortem 7 天內補完）。
> Sync：repo `data/incidents-pending/` → Mac sync → vault `Incidents/{fired_local:%Y/%m}/`。

## Summary

<!-- TODO 一段話：發生什麼、誰受影響、影響多久、結果。 -->

**首次 fire 訊息**：{message}

## Timeline

| 時間（Asia/Taipei）| 事件 |
|---|---|
| {fired_local:%H:%M:%S} | 第一次 fire（自動歸檔本檔） |

## Detection

<!-- TODO 怎麼偵測到的？哪個 probe / cron / user report -->

## Mitigation

<!-- TODO 採取什麼動作止血、何時驗證恢復 -->

## Root cause（5-why）

<!-- TODO 至少三層 why；停在「人為失誤」是 anti-pattern -->

## Action items

| ID | 動作 | Owner | Due | 狀態 |
|---|---|---|---|---|
| | | | | |

## Lessons learned

<!-- TODO Blameless tone，寫 system gap 不寫人為 -->

## Context（at fire time）

{ctx_lines}
"""


def _append_repeat(
    path: Path, rule_id: str, fired_local: datetime, message: str
) -> ArchivedIncident:
    """Append a row to the `## Repeat fires` section, creating it if absent."""
    existing = path.read_text(encoding="utf-8").rstrip("\n")
    truncated_msg = (message[:200].replace("|", "\\|")).replace("\n", " ")

    if _REPEAT_SECTION not in existing:
        existing = (
            existing + f"\n\n{_REPEAT_SECTION}\n\n{_REPEAT_HEADER_LINE}\n{_REPEAT_SEPARATOR_LINE}"
        )

    existing += f"\n| {fired_local:%H:%M:%S} | {truncated_msg} |"
    path.write_text(existing + "\n", encoding="utf-8")
    logger.info("incident_archive: repeat fire path=%s rule=%s", path, rule_id)
    return ArchivedIncident(path=path, rule_id=rule_id, fired_at_local=fired_local, is_new=False)


# ---- Listing helpers (used by Franky weekly_digest §6) ----------------------


@dataclass(frozen=True)
class IncidentRollup:
    total: int
    by_severity: dict[str, int]  # SEV-1..SEV-4 counts
    open_count: int  # status != closed/resolved
    top_recurring: list[tuple[str, int]]  # [(rule_id, repeat_count), ...]


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
_REPEAT_ROW_RE = re.compile(r"^\|\s*\d{2}:\d{2}:\d{2}\s*\|", re.MULTILINE)


def list_pending_incidents(*, since: datetime, pending_dir: Path | None = None) -> IncidentRollup:
    """Scan `pending_dir` for incident files modified after `since`.

    Counts by SEV tier (from frontmatter), open status, and top-3 recurring
    (by `## Repeat fires` row count). Stable enough for digest rendering;
    not a query API.
    """
    target_dir = pending_dir if pending_dir is not None else default_pending_dir()
    if not target_dir.exists():
        return IncidentRollup(total=0, by_severity={}, open_count=0, top_recurring=[])

    by_severity: dict[str, int] = {}
    open_count = 0
    repeats: list[tuple[str, int]] = []
    total = 0
    since_ts = since.timestamp() if since.tzinfo else since.replace(tzinfo=timezone.utc).timestamp()

    for p in sorted(target_dir.glob("*.md")):
        try:
            stat = p.stat()
        except OSError:
            continue
        if stat.st_mtime < since_ts:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        meta = _parse_frontmatter(text)
        if not meta:
            continue
        total += 1
        sev = meta.get("severity", "SEV-3")
        by_severity[sev] = by_severity.get(sev, 0) + 1
        status = meta.get("status", "detected")
        if status not in ("closed", "resolved"):
            open_count += 1
        rule_id = meta.get("trigger", p.stem)
        # +1 for the initial fire (Timeline row), then count `## Repeat fires` rows
        n_repeats = 1 + len(_REPEAT_ROW_RE.findall(_extract_repeat_section(text)))
        repeats.append((rule_id, n_repeats))

    repeats.sort(key=lambda r: r[1], reverse=True)
    return IncidentRollup(
        total=total,
        by_severity=by_severity,
        open_count=open_count,
        top_recurring=repeats[:3],
    )


def _parse_frontmatter(text: str) -> dict[str, str] | None:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" not in line or line.lstrip().startswith("-"):
            continue
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip().strip('"')
    return out


def _extract_repeat_section(text: str) -> str:
    idx = text.find(_REPEAT_SECTION)
    return text[idx:] if idx >= 0 else ""
