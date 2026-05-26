"""Project indexer — list / load LifeOS Projects from vault (Tier C).

Tier C read-path scaffolding for ``/bridge/projects`` (vault-as-substrate).
Vault is the SoT (ADR-030 D1); this module performs FS-direct reads via
``Path.iterdir`` + frontmatter parse and returns flat view models. No DB
mirror, no FTS index — matches the digest_indexer pattern (ADR-030 D2).

Layout (ADR-028, VAULT-LAYOUT.md §2):

    Projects/{title}.md

Where ``{title}`` is the human-readable project title (CJK + ASCII), used
verbatim as both file basename and slug. Per ADR-031 D9.a slugs are
NFC-normalized before comparison (cross-platform safety — macOS NFD vs
Windows/Linux NFC).

Frontmatter shape per ``docs/schemas/project-frontmatter-nested.md`` (γ):

    type: project
    content_type: youtube | podcast
    created: YYYY-MM-DD
    status: active | paused | published | archived
    priority: first | high | medium | low
    area: work | play | love | health
    search_topic: <str>
    quarter / parent_kr / publish_date  (optional, nullable)
    one_sentence / hook_text             (optional, γ Tier C)
    title_candidates / thumbnail_concept (optional, γ)
    reviews: {storyteller, coach}        (optional, γ)
    pomodoro: {est_total, actual_total}  (optional, γ)
    tags: [project, youtube|podcast, ...]
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import yaml

PROJECTS_DIR = "Projects"

# Files Obsidian creates as scratch / sync artifacts — never list as projects.
_SKIP_FILENAME_RE = re.compile(
    r"""
    ^(?:
        \..*                                 |   # dotfile
        .*\.sync-conflict-.*                 |   # Syncthing conflict file
        .*\.tmp                              |   # editor scratch
        Untitled.*                                  # Obsidian default
    )$
    """,
    re.VERBOSE,
)

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)


def normalize_slug(name: str) -> str:
    """NFC-normalize a project title for cross-platform comparison.

    macOS HFS+/APFS sometimes returns NFD (decomposed) for CJK; Windows
    NTFS and Linux ext4 return NFC. Compare/index always in NFC (ADR-031
    D9.a; aligns with ``scripts/vault_layout_audit.py`` policy).
    """
    return unicodedata.normalize("NFC", name)


@dataclass(frozen=True)
class ReviewSummary:
    """One persona review entry surfaced to the UI."""

    persona: str  # "storyteller" | "coach"
    run_at: Optional[str]  # ISO 8601 with TZ; None when missing
    score: Optional[int]  # 1-5; None when missing
    summary: str
    suggestions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjectEntry:
    slug: str  # NFC-normalized title (== file basename without .md)
    title: str  # display title (same as slug today; field reserved for future divergence)
    content_type: str  # "youtube" | "podcast"
    created: Optional[str]  # YYYY-MM-DD, normalized to string
    status: str
    priority: str
    area: str
    search_topic: str
    quarter: Optional[str]
    parent_kr: Optional[str]
    publish_date: Optional[str]
    one_sentence: str = ""
    hook_text: str = ""
    title_candidates: tuple[str, ...] = ()
    thumbnail_concept: str = ""
    reviews: tuple[ReviewSummary, ...] = ()
    pomodoro_est_total: int = 0
    pomodoro_actual_total: int = 0
    tags: tuple[str, ...] = ()
    relative_path: str = ""  # POSIX, vault-relative
    raw_frontmatter: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def detail_url(self) -> str:
        return f"/bridge/projects/{self.slug}"


class ProjectNotFoundError(FileNotFoundError):
    pass


class ProjectIndexer:
    """Filesystem-backed project reader. One instance per request scope is fine.

    Vault scan is sub-millisecond at owner's vault scale (≤20 projects expected).
    """

    def __init__(self, vault_root: Path) -> None:
        self._vault_root = Path(vault_root)

    # ── public API ─────────────────────────────────────────────────────────

    def list_all(self, *, include_archived: bool = False) -> list[ProjectEntry]:
        """Return all projects, sorted by status priority then created date desc."""
        out: list[ProjectEntry] = []
        d = self._dir()
        if not d.exists():
            return out
        for p in d.iterdir():
            if not p.is_file() or p.suffix != ".md":
                continue
            if _SKIP_FILENAME_RE.match(p.name):
                continue
            entry = self._entry_from_path(p)
            if entry is None:
                continue
            if entry.status == "archived" and not include_archived:
                continue
            out.append(entry)
        out.sort(key=_sort_key)
        return out

    def get(self, slug: str) -> ProjectEntry:
        slug = normalize_slug(slug)
        path = self._file_for(slug)
        if not path.exists():
            raise ProjectNotFoundError(f"project not found: {slug!r}")
        entry = self._entry_from_path(path)
        if entry is None:
            raise ProjectNotFoundError(f"project frontmatter unreadable: {slug!r}")
        return entry

    def load_body(self, slug: str) -> str:
        """Return md body (frontmatter stripped)."""
        entry = self.get(slug)
        raw = self._file_for(entry.slug).read_text(encoding="utf-8")
        m = _FRONTMATTER_RE.match(raw)
        return m.group(2) if m else raw

    # ── helpers ────────────────────────────────────────────────────────────

    def _dir(self) -> Path:
        return self._vault_root / PROJECTS_DIR

    def _file_for(self, slug: str) -> Path:
        return self._dir() / f"{slug}.md"

    def _entry_from_path(self, path: Path) -> Optional[ProjectEntry]:
        slug = normalize_slug(path.stem)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return None
        m = _FRONTMATTER_RE.match(raw)
        if not m:
            return None
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            return None
        if not isinstance(fm, dict) or fm.get("type") != "project":
            return None
        return _entry_from_fm(slug=slug, fm=fm, relative_path=f"{PROJECTS_DIR}/{path.name}")


def _entry_from_fm(*, slug: str, fm: dict, relative_path: str) -> ProjectEntry:
    """Build a ProjectEntry from a parsed frontmatter dict."""
    reviews_fm = fm.get("reviews") or {}
    reviews: list[ReviewSummary] = []
    if isinstance(reviews_fm, dict):
        for persona in ("storyteller", "coach"):
            r = reviews_fm.get(persona)
            if isinstance(r, dict):
                reviews.append(
                    ReviewSummary(
                        persona=persona,
                        run_at=_as_iso_str(r.get("run_at")),
                        score=_as_int(r.get("score")),
                        summary=str(r.get("summary") or ""),
                        suggestions=tuple(
                            str(s) for s in (r.get("suggestions") or []) if s is not None
                        ),
                    )
                )

    pom = fm.get("pomodoro") or {}
    est_total = _as_int(pom.get("est_total")) or 0
    actual_total = _as_int(pom.get("actual_total")) or 0

    return ProjectEntry(
        slug=slug,
        title=slug,
        content_type=str(fm.get("content_type") or ""),
        created=_as_date_str(fm.get("created")),
        status=str(fm.get("status") or "active"),
        priority=str(fm.get("priority") or "medium"),
        area=str(fm.get("area") or "work"),
        search_topic=str(fm.get("search_topic") or slug),
        quarter=_as_optional_str(fm.get("quarter")),
        parent_kr=_as_optional_str(fm.get("parent_kr")),
        publish_date=_as_date_str(fm.get("publish_date")),
        one_sentence=str(fm.get("one_sentence") or ""),
        hook_text=str(fm.get("hook_text") or ""),
        title_candidates=tuple(str(t) for t in (fm.get("title_candidates") or []) if t is not None),
        thumbnail_concept=str(fm.get("thumbnail_concept") or ""),
        reviews=tuple(reviews),
        pomodoro_est_total=est_total,
        pomodoro_actual_total=actual_total,
        tags=tuple(str(t) for t in (fm.get("tags") or []) if t is not None),
        relative_path=relative_path,
        raw_frontmatter=dict(fm),
    )


# ── Coercion helpers ───────────────────────────────────────────────────────


def _as_int(v: object) -> Optional[int]:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return None


def _as_optional_str(v: object) -> Optional[str]:
    if v is None or v == "":
        return None
    return str(v)


def _as_date_str(v: object) -> Optional[str]:
    if v is None or v == "":
        return None
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10]
    return str(v)


def _as_iso_str(v: object) -> Optional[str]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


# ── Sort key ───────────────────────────────────────────────────────────────


_STATUS_ORDER = {"active": 0, "paused": 1, "published": 2, "archived": 3}
_PRIORITY_ORDER = {"first": 0, "high": 1, "medium": 2, "low": 3}


def _sort_key(e: ProjectEntry) -> tuple[int, int, str, str]:
    """Sort: active > paused > published > archived;
    within status, first > high > medium > low; then created date desc; then slug.
    """
    return (
        _STATUS_ORDER.get(e.status, 99),
        _PRIORITY_ORDER.get(e.priority, 99),
        # date desc → negate by inverting (use sentinel '9999' for empty)
        "9999-99-99" if e.created is None else _invert_date(e.created),
        e.slug,
    )


def _invert_date(d: str) -> str:
    """Map YYYY-MM-DD → inverse string so reverse sort yields newest first."""
    try:
        y, m, day = d.split("-")
        return f"{9999 - int(y):04d}-{99 - int(m):02d}-{99 - int(day):02d}"
    except Exception:
        return d
