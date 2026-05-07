"""Franky context snapshot regenerator（ADR-023 §3 Phase 1，pre-RAG 路徑）。

每週日 21:30 台北 cron 觸發，產出 `agents/franky/state/franky_context_snapshot.md`，
inject 進 score + synthesis prompt。不依賴 ADR-020 RAG infra。

Snapshot 四區塊（依序，token budget = 9k 上限）：

  1. Active priorities       — `memory/claude/MEMORY.md` 索引前 5 條           ≤ 1k
  2. Recent ADR assumptions  — `docs/decisions/` 過去 30d git log 影響的 ADR  ≤ 4k
                                + 各 ADR Decision 段落 200 字摘要
  3. Top-N open issues       — `gh issue list --state open --limit 15 --json` ≤ 2k
  4. Recent MEMORY changes   — `memory/claude/MEMORY.md` 過去 30d git diff    ≤ 2k

Frontmatter：`generated_at` + `nakama_repo_sha`（git HEAD short）。

CLI：

  python -m agents.franky.state.context_snapshot regenerate
  python -m agents.franky.state.context_snapshot regenerate --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Token budgets（chars 概估，4 chars/token；無 tiktoken dep）
CHARS_PER_TOKEN = 4

BUDGETS = {
    "priorities": 1_000,
    "adr": 4_000,
    "issues": 2_000,
    "memory": 2_000,
}
TOTAL_BUDGET_TOKENS = sum(BUDGETS.values())  # 9_000

# Repo paths
REPO_ROOT = Path(__file__).resolve().parents[3]
MEMORY_INDEX = REPO_ROOT / "memory" / "claude" / "MEMORY.md"
ADR_DIR = REPO_ROOT / "docs" / "decisions"
SNAPSHOT_PATH = (
    REPO_ROOT / "agents" / "franky" / "state" / "franky_context_snapshot.md"
)

TAIPEI = ZoneInfo("Asia/Taipei")


# ---------------------------------------------------------------------------
# token utility
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """4-chars/token 概估；夠 budget gate 用，不需精確 BPE。"""
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def truncate_to_token_budget(text: str, budget_tokens: int) -> str:
    """若超過 budget，從尾部砍到 budget 並補 truncation marker。"""
    max_chars = budget_tokens * CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text
    marker = "\n\n_…(truncated for token budget)_\n"
    keep = max_chars - len(marker)
    if keep < 0:
        keep = max_chars
        marker = ""
    return text[:keep] + marker


# ---------------------------------------------------------------------------
# git helpers（subprocess wrappers，可被 monkeypatch）
# ---------------------------------------------------------------------------


def _run_git(args: list[str], cwd: Path | None = None) -> str:
    """Wrapper that returns stdout (stripped). Tests monkeypatch this."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (rc={result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def get_repo_sha() -> str:
    """Short git HEAD SHA（snapshot 版本控制，frontmatter 用）。"""
    try:
        return _run_git(["rev-parse", "--short", "HEAD"]).strip()
    except (RuntimeError, FileNotFoundError):
        return "unknown"


# ---------------------------------------------------------------------------
# Block 1 — Active priorities（MEMORY.md 索引前 5 條）
# ---------------------------------------------------------------------------


def build_active_priorities(memory_path: Path = MEMORY_INDEX, n: int = 5) -> str:
    """Read first ``n`` non-empty bullet lines from MEMORY.md index."""
    if not memory_path.exists():
        return "_MEMORY.md not found — fallback to empty priorities._\n"

    raw = memory_path.read_text(encoding="utf-8")
    bullets: list[str] = []
    for line in raw.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("- ") and len(stripped) > 2:
            bullets.append(stripped)
            if len(bullets) >= n:
                break

    if not bullets:
        return "_No memory entries found._\n"

    body = "\n".join(bullets) + "\n"
    return truncate_to_token_budget(body, BUDGETS["priorities"])


# ---------------------------------------------------------------------------
# Block 2 — Recent ADR assumptions（30d git log + Decision 段落 200 字摘要）
# ---------------------------------------------------------------------------


_DECISION_HEADER_RE = re.compile(r"^##\s+Decision\b", re.MULTILINE | re.IGNORECASE)
_NEXT_HEADER_RE = re.compile(r"^##\s+", re.MULTILINE)


def _extract_decision_section(adr_text: str) -> str:
    """Extract the body under ``## Decision`` up to the next ``## `` header."""
    m = _DECISION_HEADER_RE.search(adr_text)
    if not m:
        return ""
    start = m.end()
    rest = adr_text[start:]
    nxt = _NEXT_HEADER_RE.search(rest)
    if nxt:
        body = rest[: nxt.start()]
    else:
        body = rest
    return body.strip()


def _summarise(text: str, max_chars: int = 800) -> str:
    """Trim ADR Decision section to ~200 token (≈ 800 char) abstract.

    Keep leading whitespace-collapsed prose; not a semantic summary — explicit
    spec says "200 字摘要" via head-trim, no LLM cost (S2a is zero-LLM)."""
    collapsed = re.sub(r"\s+", " ", text).strip()
    if len(collapsed) <= max_chars:
        return collapsed
    return collapsed[: max_chars - 1].rstrip() + "…"


def list_recent_adrs(
    days: int = 30,
    adr_dir: Path = ADR_DIR,
) -> list[Path]:
    """ADR files in ``docs/decisions/`` whose git log shows commit in last N days."""
    if not adr_dir.exists():
        return []
    since = f"--since={days}.days.ago"
    # Compute repo-relative pathspec when possible; else pass absolute.
    try:
        rel = adr_dir.resolve().relative_to(REPO_ROOT.resolve())
        pathspec = str(rel).replace("\\", "/")
    except ValueError:
        pathspec = str(adr_dir)

    try:
        out = _run_git(
            [
                "log",
                since,
                "--name-only",
                "--pretty=format:",
                "--",
                pathspec,
            ]
        )
    except (RuntimeError, FileNotFoundError):
        return []

    seen: set[str] = set()
    result: list[Path] = []
    for line in out.splitlines():
        line = line.strip()
        if not line or not line.endswith(".md"):
            continue
        if "ADR-" not in line:
            continue
        if line in seen:
            continue
        seen.add(line)
        # git log returns repo-relative paths; also accept basename inside adr_dir
        candidates = [REPO_ROOT / line, adr_dir / Path(line).name]
        for cand in candidates:
            if cand.exists():
                result.append(cand)
                break
    return result


def build_recent_adr_assumptions(
    days: int = 30,
    adr_dir: Path = ADR_DIR,
) -> str:
    paths = list_recent_adrs(days=days, adr_dir=adr_dir)
    if not paths:
        return "_No ADR commits in the last 30 days._\n"

    chunks: list[str] = []
    for p in paths:
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        decision = _extract_decision_section(text)
        if not decision:
            continue
        summary = _summarise(decision, max_chars=800)
        chunks.append(f"### {p.stem}\n\n{summary}\n")

    body = "\n".join(chunks) if chunks else "_No Decision sections extracted._\n"
    return truncate_to_token_budget(body, BUDGETS["adr"])


# ---------------------------------------------------------------------------
# Block 3 — Top-N open issues（gh CLI）
# ---------------------------------------------------------------------------


def fetch_open_issues(limit: int = 15) -> list[dict]:
    """Wrap ``gh issue list --json …``. Tests monkeypatch this."""
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--state",
                "open",
                "--limit",
                str(limit),
                "--json",
                "number,title,labels,createdAt",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except FileNotFoundError:
        return []
    if result.returncode != 0:
        return []
    try:
        return json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return []


def build_top_open_issues(limit: int = 15) -> str:
    issues = fetch_open_issues(limit=limit)
    if not issues:
        return "_No open issues (or `gh` unavailable)._\n"

    lines: list[str] = []
    for it in issues:
        num = it.get("number", "?")
        title = (it.get("title") or "").strip()
        labels_raw = it.get("labels") or []
        labels = ",".join(
            (lbl.get("name") if isinstance(lbl, dict) else str(lbl))
            for lbl in labels_raw
        )
        created = (it.get("createdAt") or "")[:10]  # YYYY-MM-DD
        lbl_part = f" [{labels}]" if labels else ""
        lines.append(f"- #{num} {title}{lbl_part} ({created})")

    body = "\n".join(lines) + "\n"
    return truncate_to_token_budget(body, BUDGETS["issues"])


# ---------------------------------------------------------------------------
# Block 4 — Recent MEMORY changes（30d git diff added lines）
# ---------------------------------------------------------------------------


def build_recent_memory_changes(
    days: int = 30,
    memory_path: Path = MEMORY_INDEX,
) -> str:
    if not memory_path.exists():
        return "_MEMORY.md not found — no diff available._\n"

    rel = memory_path.relative_to(REPO_ROOT).as_posix()
    since = f"--since={days}.days.ago"

    try:
        # Find earliest commit in the window for this file; diff against its parent.
        log_out = _run_git(
            ["log", since, "--pretty=format:%H", "--reverse", "--", rel]
        )
    except (RuntimeError, FileNotFoundError):
        return "_git log failed — cannot build memory diff._\n"

    commits = [c.strip() for c in log_out.splitlines() if c.strip()]
    if not commits:
        return "_No MEMORY.md changes in the last 30 days._\n"

    base = commits[0] + "^"
    try:
        diff_out = _run_git(["diff", base, "HEAD", "--", rel])
    except (RuntimeError, FileNotFoundError):
        return "_git diff failed — cannot build memory diff._\n"

    added: list[str] = []
    for line in diff_out.splitlines():
        if line.startswith("+++"):
            continue
        if line.startswith("+"):
            content = line[1:].rstrip()
            stripped = content.lstrip()
            if stripped.startswith("- ") and len(stripped) > 2:
                added.append(stripped)

    if not added:
        return "_No new memory entries detected in 30d window._\n"

    body = "\n".join(added) + "\n"
    return truncate_to_token_budget(body, BUDGETS["memory"])


# ---------------------------------------------------------------------------
# Compose
# ---------------------------------------------------------------------------


def build_snapshot(
    *,
    days: int = 30,
    issue_limit: int = 15,
    priority_n: int = 5,
) -> str:
    now_taipei = datetime.now(tz=timezone.utc).astimezone(TAIPEI)
    sha = get_repo_sha()

    block1 = build_active_priorities(n=priority_n)
    block2 = build_recent_adr_assumptions(days=days)
    block3 = build_top_open_issues(limit=issue_limit)
    block4 = build_recent_memory_changes(days=days)

    frontmatter = (
        "---\n"
        f"generated_at: {now_taipei.isoformat(timespec='seconds')}\n"
        f"nakama_repo_sha: {sha}\n"
        f"token_budget_total: {TOTAL_BUDGET_TOKENS}\n"
        "schema_version: 1\n"
        "source: agents/franky/state/context_snapshot.py\n"
        "---\n"
    )

    body = (
        frontmatter
        + "\n# Franky Context Snapshot\n\n"
        + "Pre-RAG substrate per ADR-023 §3 Phase 1。inject 進 score + synthesis prompt。\n\n"
        + "## 1. Active priorities\n\n"
        + block1.rstrip()
        + "\n\n"
        + "## 2. Recent ADR assumptions (30d)\n\n"
        + block2.rstrip()
        + "\n\n"
        + "## 3. Top open issues\n\n"
        + block3.rstrip()
        + "\n\n"
        + "## 4. Recent MEMORY changes (30d)\n\n"
        + block4.rstrip()
        + "\n"
    )
    return body


def regenerate(*, dry_run: bool = False, output_path: Path = SNAPSHOT_PATH) -> str:
    """Regenerate snapshot. Returns the rendered text. Writes file unless dry_run."""
    text = build_snapshot()
    if dry_run:
        return text
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    return text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agents.franky.state.context_snapshot",
        description="Regenerate franky context snapshot (ADR-023 §3 Phase 1).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    regen = sub.add_parser("regenerate", help="Regenerate franky_context_snapshot.md")
    regen.add_argument(
        "--dry-run",
        action="store_true",
        help="Render to stdout, do not write file.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.cmd == "regenerate":
        # Re-read module attr so monkeypatch in tests is honoured.
        target = sys.modules[__name__].SNAPSHOT_PATH
        text = regenerate(dry_run=args.dry_run, output_path=target)
        if args.dry_run:
            _safe_write(text)
        else:
            _safe_write(
                f"snapshot regenerated -> {target}\n"
                f"size: {len(text)} chars (~{estimate_tokens(text)} tokens)\n"
            )
        return 0
    return 1


def _safe_write(text: str) -> None:
    """Write to stdout, falling back to UTF-8 bytes when console encoding (e.g.
    Windows cp1252) cannot represent CJK characters in the snapshot."""
    try:
        sys.stdout.write(text)
    except UnicodeEncodeError:
        buf = getattr(sys.stdout, "buffer", None)
        if buf is not None:
            buf.write(text.encode("utf-8", errors="replace"))
        else:
            sys.stdout.write(text.encode("utf-8", errors="replace").decode("ascii", errors="replace"))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
