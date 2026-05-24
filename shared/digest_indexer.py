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
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
_BLOCKQUOTE_RE = re.compile(r"^> (.+?)$", re.MULTILINE)


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

    def get(self, type_: str, date_: str) -> DigestEntry:
        if type_ not in DIGEST_TYPES:
            raise DigestNotFoundError(f"unknown digest type: {type_!r}")
        if not _DATE_RE.match(date_ + ".md"):
            raise DigestNotFoundError(f"invalid digest date: {date_!r}")
        if not self._file_for(type_, date_).exists():
            raise DigestNotFoundError(f"digest not found: {type_}/{date_}")
        return self._entry(type_, date_)

    def load_text(self, type_: str, date_: str) -> str:
        """Return the digest's full markdown body (no frontmatter)."""
        entry = self.get(type_, date_)
        raw = self._file_for(entry.type, entry.date).read_text(encoding="utf-8")
        m = _FRONTMATTER_RE.match(raw)
        return m.group(2) if m else raw

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
