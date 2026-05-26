"""Digest indexer — list / load Robin PubMed + Franky AI digest files.

Tier A read-path scaffolding for `/bridge/digests` (vault-as-substrate).
Vault is the SoT; this module performs FS-direct reads via path glob and
returns a flat view model. No DB mirror, no FTS index.

Layout:
    KB/Wiki/Digests/PubMed/{YYYY-MM-DD}.md   (Robin)
    KB/Wiki/Digests/AI/{YYYY-MM-DD}.md       (Franky)

Date format is `YYYY-MM-DD` per both agents (see
`agents.robin.pubmed_digest._write_digest_page` and
`agents.franky.news_digest._write_digest_page`). Asia/Taipei TZ.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import yaml

DIGEST_TYPES: tuple[str, ...] = ("pubmed", "ai")

_DIR_FOR: dict[str, str] = {
    "pubmed": "KB/Wiki/Digests/PubMed",
    "ai": "KB/Wiki/Digests/AI",
}

_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
# Syncthing conflict file pattern: `<basename>.sync-conflict-YYYYMMDD-HHMMSS-DEVICE.<ext>`
# Reference: https://docs.syncthing.net/users/syncing.html#conflicting-changes
_SYNC_CONFLICT_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})\.sync-conflict-"
    r"(?P<ts>\d{8}-\d{6})-(?P<device>[^.]+)\.md$"
)
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
_BLOCKQUOTE_RE = re.compile(r"^> (.+?)$", re.MULTILINE)


@dataclass(frozen=True)
class ConflictFile:
    """A Syncthing-generated `*.sync-conflict-*.md` file within a digest dir.

    Surfaces D2 silent-data-loss risk (ADR-030 Gemini audit / Issue #696):
    when two devices edit the same digest while offline, Syncthing keeps the
    later write as a conflict file instead of merging. The Bridge landing
    banner uses this to flag conflicts so the owner doesn't lose notes.
    """

    type: str  # "pubmed" | "ai" — same canonical slug as DigestEntry
    original_date: str  # YYYY-MM-DD of the conflicted-with digest
    relative_path: str  # POSIX, vault-relative
    conflict_timestamp: str  # raw YYYYMMDD-HHMMSS from filename
    device: str  # short device name Syncthing tagged the conflict with


@dataclass(frozen=True)
class DigestEntry:
    type: str  # "pubmed" | "ai"
    date: str  # YYYY-MM-DD
    relative_path: str  # POSIX, vault-relative
    selected_count: Optional[int]
    editor_pick_count: Optional[int]  # PubMed only
    summary: str  # editor note (first blockquote of body), trimmed

    @property
    def detail_url(self) -> str:
        return f"/bridge/digests/{self.type}/{self.date}"


class DigestNotFoundError(FileNotFoundError):
    pass


class DigestIndexer:
    """Filesystem-backed digest reader. One instance per request scope is fine —
    no module-level state, no cache. The 14-file/7-day landing glob is sub-ms.
    """

    def __init__(self, vault_root: Path) -> None:
        self._vault_root = Path(vault_root)

    # ── public API ─────────────────────────────────────────────────────────

    def types(self) -> tuple[str, ...]:
        return DIGEST_TYPES

    def latest_per_type(self) -> dict[str, Optional[DigestEntry]]:
        """Newest digest per type. None when the directory has no files."""
        out: dict[str, Optional[DigestEntry]] = {}
        for t in DIGEST_TYPES:
            dates = self._list_dates(t)
            out[t] = self._entry(t, dates[0]) if dates else None
        return out

    def last_n_days(self, n: int = 7) -> list[DigestEntry]:
        """Entries for the last `n` calendar days (Asia/Taipei), newest first.
        Missing days are skipped — empty list when nothing exists.
        """
        today = datetime.now(ZoneInfo("Asia/Taipei")).date()
        entries: list[DigestEntry] = []
        for delta in range(n):
            d = (today - timedelta(days=delta)).isoformat()
            for t in DIGEST_TYPES:
                if self._file_for(t, d).exists():
                    entries.append(self._entry(t, d))
        return entries

    def last_n_days_by_date(
        self, n: int = 7, *, skip_today: bool = False
    ) -> list[tuple[str, dict[str, Optional[DigestEntry]]]]:
        """Returns ``[(date_iso, {type_slug: entry_or_None})]`` newest first.

        Unlike :meth:`last_n_days`, this includes every date in the window —
        even when both types are missing — so the landing's "past 7 days"
        timeline shows a continuous date column instead of skipping days.

        ``skip_today=True`` starts the window at yesterday — useful when the
        caller already surfaces today's digests separately (hero cards) and
        doesn't want the timeline to repeat them.
        """
        today = datetime.now(ZoneInfo("Asia/Taipei")).date()
        start = 1 if skip_today else 0
        out: list[tuple[str, dict[str, Optional[DigestEntry]]]] = []
        for delta in range(start, start + n):
            d = (today - timedelta(days=delta)).isoformat()
            slots: dict[str, Optional[DigestEntry]] = {}
            for t in DIGEST_TYPES:
                slots[t] = self._entry(t, d) if self._file_for(t, d).exists() else None
            out.append((d, slots))
        return out

    def get(self, type_: str, date_: str) -> DigestEntry:
        if type_ not in DIGEST_TYPES:
            raise DigestNotFoundError(f"unknown digest type: {type_!r}")
        if not _DATE_RE.match(date_ + ".md"):
            raise DigestNotFoundError(f"invalid digest date: {date_!r}")
        if not self._file_for(type_, date_).exists():
            raise DigestNotFoundError(f"digest not found: {type_}/{date_}")
        return self._entry(type_, date_)

    def list_conflict_files(self) -> list[ConflictFile]:
        """Return all `*.sync-conflict-*.md` files under the digest directories.

        Issue #696. Empty list when none — the common case. Newest conflict
        first (by timestamp encoded in the filename).
        """
        out: list[ConflictFile] = []
        for t in DIGEST_TYPES:
            d = self._dir_for(t)
            if not d.exists():
                continue
            for p in d.iterdir():
                m = _SYNC_CONFLICT_RE.match(p.name)
                if not m:
                    continue
                out.append(
                    ConflictFile(
                        type=t,
                        original_date=m.group("date"),
                        relative_path=f"{_DIR_FOR[t]}/{p.name}",
                        conflict_timestamp=m.group("ts"),
                        device=m.group("device"),
                    )
                )
        out.sort(key=lambda c: c.conflict_timestamp, reverse=True)
        return out

    def load_text(self, type_: str, date_: str) -> str:
        """Return the digest's full markdown body (no frontmatter)."""
        entry = self.get(type_, date_)
        raw = self._file_for(entry.type, entry.date).read_text(encoding="utf-8")
        m = _FRONTMATTER_RE.match(raw)
        return m.group(2) if m else raw

    def load_studies(self, type_: str, date_: str) -> list:
        """Parse the digest body into structured ``DigestStudy`` entries.

        Returns ``[]`` if the file is missing or the parser finds no entries
        (e.g. body is corrupted / schema drift). Callers decide whether to
        fall back to raw markdown rendering or show an empty state. Lazy
        import keeps the parser optional for indexer-only callers.
        """
        from shared.digest_parser import parse_ai_digest, parse_pubmed_digest

        try:
            body = self.load_text(type_, date_)
        except DigestNotFoundError:
            return []
        if type_ == "pubmed":
            return parse_pubmed_digest(body)
        if type_ == "ai":
            return parse_ai_digest(body)
        return []

    # ── helpers ────────────────────────────────────────────────────────────

    def _dir_for(self, type_: str) -> Path:
        return self._vault_root / _DIR_FOR[type_]

    def _file_for(self, type_: str, date_: str) -> Path:
        return self._dir_for(type_) / f"{date_}.md"

    def _list_dates(self, type_: str) -> list[str]:
        d = self._dir_for(type_)
        if not d.exists():
            return []
        dates: list[str] = []
        for p in d.iterdir():
            m = _DATE_RE.match(p.name)
            if m:
                dates.append(m.group(1))
        dates.sort(reverse=True)
        return dates

    def _entry(self, type_: str, date_: str) -> DigestEntry:
        path = self._file_for(type_, date_)
        raw = path.read_text(encoding="utf-8")
        m = _FRONTMATTER_RE.match(raw)
        if m:
            try:
                fm = yaml.safe_load(m.group(1)) or {}
            except yaml.YAMLError:
                fm = {}
            body = m.group(2)
        else:
            fm = {}
            body = raw
        bm = _BLOCKQUOTE_RE.search(body)
        summary = bm.group(1).strip() if bm else ""
        return DigestEntry(
            type=type_,
            date=date_,
            relative_path=f"{_DIR_FOR[type_]}/{date_}.md",
            selected_count=_as_int(fm.get("selected_count")),
            editor_pick_count=_as_int(fm.get("editor_pick_count")),
            summary=summary,
        )


def _as_int(v: object) -> Optional[int]:
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.isdigit():
        return int(v)
    return None


def today_taipei() -> date:
    return datetime.now(ZoneInfo("Asia/Taipei")).date()
