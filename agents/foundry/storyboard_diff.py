"""Myers-style LCS diff over storyboard beats (ADR-038 §D7).

Pure function, no IO. Emits a list of `(op, beat_id, beat)` tuples where:

- ``op = '='`` — beat unchanged (same beat_id, same content) in `before` → `after`
- ``op = '-'`` — beat removed (or content of a same-id beat changed: see below)
- ``op = '+'`` — beat added (or content of a same-id beat changed: see below)

Beats are keyed by ``beat_id``. Two beats with the same ``beat_id`` but
*different* content are emitted as a ``-`` immediately followed by a ``+`` at
the same position (modify = remove + add).

ADR-038 §D7 (v2): D7 is reframed as an `edit_log` enricher only — pure
primitive, not a multi-episode history view enabler. Keep the surface small.

Borrowing source: `course-video-manager` `app/lib/changelog-diff.ts` (concept
only — clean-room).
"""

from __future__ import annotations

from typing import Any, Literal

DiffOp = Literal["+", "-", "="]
DiffRow = tuple[DiffOp, Any, dict[str, Any]]
"""(op, beat_id, beat_payload). ``beat_id`` is taken from the source beat
unchanged (typically ``int`` but kept ``Any`` because storyboard YAML may use
strings for sub-beats)."""


def _beat_id(beat: dict[str, Any]) -> Any:
    return beat.get("beat_id")


def _lcs_table(a_ids: list[Any], b_ids: list[Any]) -> list[list[int]]:
    """Classic O(n*m) LCS length table over beat_id sequences."""
    n, m = len(a_ids), len(b_ids)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if a_ids[i] == b_ids[j]:
                dp[i][j] = dp[i + 1][j + 1] + 1
            else:
                dp[i][j] = max(dp[i + 1][j], dp[i][j + 1])
    return dp


def diff_storyboards(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
) -> list[DiffRow]:
    """Return Myers-style LCS diff between two storyboards.

    Same-id-but-different-content beats are emitted as ``-`` then ``+`` at the
    aligned position (modify shows as remove+add). Pure function; no IO.
    """
    a_ids = [_beat_id(b) for b in before]
    b_ids = [_beat_id(b) for b in after]
    dp = _lcs_table(a_ids, b_ids)

    rows: list[DiffRow] = []
    i = j = 0
    n, m = len(before), len(after)
    while i < n and j < m:
        if a_ids[i] == b_ids[j]:
            if before[i] == after[j]:
                rows.append(("=", a_ids[i], before[i]))
            else:
                # Same beat_id, different content → modify = remove + add.
                rows.append(("-", a_ids[i], before[i]))
                rows.append(("+", b_ids[j], after[j]))
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            rows.append(("-", a_ids[i], before[i]))
            i += 1
        else:
            rows.append(("+", b_ids[j], after[j]))
            j += 1
    while i < n:
        rows.append(("-", a_ids[i], before[i]))
        i += 1
    while j < m:
        rows.append(("+", b_ids[j], after[j]))
        j += 1
    return rows


def format_diff(rows: list[DiffRow]) -> str:
    """Human-readable single-line-per-row rendering for CLI output."""
    out: list[str] = []
    for op, beat_id, beat in rows:
        summary_parts: list[str] = []
        for key in ("layout", "broll_decision", "broll"):
            if key in beat:
                summary_parts.append(f"{key}={beat[key]!r}")
        summary = " ".join(summary_parts) if summary_parts else ""
        out.append(f"{op} beat_id={beat_id}{(' ' + summary) if summary else ''}")
    return "\n".join(out)
