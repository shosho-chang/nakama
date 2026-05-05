"""Write IngestResult into Inbox/kb/{slug}.md (PRD docs/plans/2026-05-04-stage-1-ingest-unify.md).

``InboxWriter.write_to_inbox(result, slug)`` owns:

- frontmatter serialisation (``fulltext_status`` / ``fulltext_source`` /
  ``fulltext_layer`` / ``original_url`` per PRD §資料 schema).
- filename collision counter (``slug.md`` → ``slug-1.md`` → ``slug-2.md``)
  reusing the pattern from the legacy ``/scrape-translate`` route lines 316-321.
- < 200-char hard block: even if URLDispatcher already tagged the result
  ``status='failed'``, the writer is the single point that enforces "no
  garbage written to inbox" (defence-in-depth) — failed results are written
  as a small placeholder file with ``fulltext_status: failed`` so the inbox
  row UI has something to render the ❌ icon next to.
- Same-URL repeat detection via frontmatter ``original_url`` reverse lookup —
  re-pasting a URL whose ingest already produced an inbox file returns the
  existing path without re-writing (matches the ``/pubmed-to-reader``
  short-circuit pattern from ``thousand_sunny.routers.robin``).

The placeholder writer (`write_placeholder`) is the partner method called
from the ``/scrape-translate`` endpoint **before** dispatching the
BackgroundTask — it gives the inbox row immediate "🔄 處理中" feedback while
the background fetch runs.
"""

from __future__ import annotations

from pathlib import Path

from shared.log import get_logger
from shared.schemas.ingest_result import IngestResult
from shared.utils import extract_frontmatter, read_text

logger = get_logger("nakama.robin.inbox_writer")

# Filename pattern when the same slug is written multiple times. Mirrors
# the existing ``/scrape-translate`` line 316-321 counter pattern.
_COLLISION_FORMAT = "{stem}-{counter}{suffix}"

# Placeholder body — short enough to not pollute reader render, descriptive
# enough that 修修 sees something useful if they click into a still-processing
# row. The BackgroundTask overwrites this when the dispatcher finishes.
_PLACEHOLDER_BODY = "_Robin 正在後台抓取這個 URL — 請稍候，inbox 會自動更新狀態。_\n"


def _yaml_safe(value: str) -> str:
    """Strip CR/LF + leading/trailing whitespace for any YAML scalar context.

    Use for *bare* (unquoted) scalar fields where the value comes from a
    controlled vocabulary (status / layer / source_type / content_nature) —
    the strip is mostly defensive; controlled vocab won't have newlines.
    """
    return value.replace("\n", " ").replace("\r", " ").strip()


def _yaml_double_quoted(value: str) -> str:
    """Escape a value for use inside a YAML double-quoted scalar (``"..."``).

    Must be called whenever the value is interpolated between literal ``"``
    characters in the frontmatter template — markdown ``# headings`` (which
    InboxWriter passes through as ``title``) routinely contain ``"``, ``\\``,
    ``:``, and other YAML-meaningful characters that break ``yaml.safe_load``
    if unescaped (``feedback_yaml_scalar_safety``).

    Escape order matters: ``\\`` first (so our own ``\\`` insertions aren't
    re-escaped on the next pass), then ``"``. After escaping, strip CR/LF
    (YAML allows them but our consumers don't expect multi-line values).
    """
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", " ")
        .replace("\r", " ")
        .strip()
    )


class InboxWriter:
    """Write IngestResult / placeholder into ``Inbox/kb/{slug}.md``."""

    def __init__(self, inbox_dir: Path) -> None:
        """Args:
        inbox_dir: Absolute path to the vault's ``Inbox/kb`` directory.
            Caller is responsible for ensuring the parent vault exists;
            ``write_*`` methods will ``mkdir(parents=True, exist_ok=True)``
            before writing.
        """
        self._inbox_dir = inbox_dir

    # ── Public API ───────────────────────────────────────────────────────────

    def find_existing_for_url(self, original_url: str) -> Path | None:
        """Return the inbox path whose frontmatter ``original_url`` matches, or None.

        Used by the ``/scrape-translate`` endpoint to short-circuit re-pasting
        of an already-ingested URL. Reads every ``*.md`` in the inbox dir (cheap
        — inbox is intended to be small + transient). Returns the first match.
        """
        return self._find_by_frontmatter("original_url", original_url)

    def find_existing_for_zotero_item(self, item_key: str) -> Path | None:
        """Return the inbox path whose frontmatter ``zotero_item_key`` matches, or None.

        Slice 1 #389 — used by the Zotero dispatch path so re-pasting the same
        ``zotero://`` link short-circuits to the existing inbox file rather
        than overwriting (and losing annotation references).
        """
        return self._find_by_frontmatter("zotero_item_key", item_key)

    def _find_by_frontmatter(self, key: str, value: str) -> Path | None:
        """Linear scan of inbox markdowns for a frontmatter key/value match.

        Shared backend for ``find_existing_for_url`` / ``find_existing_for_zotero_item``.
        Inbox is intended to be small + transient; full directory scan is cheap.
        """
        if not self._inbox_dir.exists():
            return None
        for path in sorted(self._inbox_dir.iterdir()):
            if path.suffix.lower() != ".md" or not path.is_file():
                continue
            try:
                content = read_text(path)
            except OSError:
                continue
            fm, _ = extract_frontmatter(content)
            if fm.get(key) == value:
                return path
        return None

    def write_placeholder(
        self,
        *,
        slug: str,
        original_url: str,
        title: str,
        source_type: str = "article",
        content_nature: str = "popular_science",
    ) -> Path:
        """Write a status='processing' placeholder file and return its Path.

        Called synchronously by ``/scrape-translate`` BEFORE the BackgroundTask
        kicks off, so the inbox row is visible the moment the user redirects
        back to the inbox view (PRD §Pipeline / API step "立刻寫 placeholder").

        Filename collision uses the same counter pattern as ``write_to_inbox``.
        """
        self._inbox_dir.mkdir(parents=True, exist_ok=True)
        dest = self._next_available_path(slug)
        frontmatter = self._serialise_frontmatter(
            title=title,
            original_url=original_url,
            source_type=source_type,
            content_nature=content_nature,
            fulltext_status="processing",
            fulltext_layer="readability",
            fulltext_source="(處理中)",
            note=None,
        )
        dest.write_text(frontmatter + _PLACEHOLDER_BODY, encoding="utf-8")
        logger.info("inbox placeholder written: %s", dest.name)
        return dest

    def write_to_inbox(
        self,
        result: IngestResult,
        slug: str,
        *,
        existing_path: Path | None = None,
        source_type: str = "article",
        content_nature: str = "popular_science",
    ) -> Path:
        """Persist an ``IngestResult`` (ready or failed) to the inbox.

        Args:
            result: Output from ``URLDispatcher.dispatch()``.
            slug: Filename stem (no extension). Caller derives this from the
                URL (the ``/scrape-translate`` route uses
                ``slugify(netloc + path)[:60] or 'scraped'``).
            existing_path: If set, overwrite this file instead of allocating
                a fresh collision-counter name. Used by the BackgroundTask to
                replace the placeholder file in place.
            source_type / content_nature: Forwarded into frontmatter for the
                downstream Robin ingest pipeline (preserves the form values
                from the user's POST).

        Returns:
            The final ``Path`` written. For failed results, the file is still
            written (with ``fulltext_status: failed`` + the dispatcher's note)
            so the inbox row can render an ❌ icon.
        """
        self._inbox_dir.mkdir(parents=True, exist_ok=True)

        dest = existing_path if existing_path is not None else self._next_available_path(slug)

        # Zotero-sourced ingests force ``source_type`` to ``"zotero"`` — the
        # caller's kwarg default ``"article"`` is for URL ingest and would mis-tag
        # the row.
        effective_source_type = "zotero" if result.zotero_item_key is not None else source_type

        frontmatter = self._serialise_frontmatter(
            title=result.title,
            original_url=result.original_url,
            source_type=effective_source_type,
            content_nature=content_nature,
            fulltext_status=result.status,
            fulltext_layer=result.fulltext_layer,
            fulltext_source=result.fulltext_source,
            note=result.note,
            zotero_item_key=result.zotero_item_key,
            zotero_attachment_path=result.zotero_attachment_path,
            attachment_type=result.attachment_type,
        )

        if result.status == "failed":
            # Body for failed results is just the note — keeps the file lightweight
            # and gives the reader something readable if 修修 clicks in.
            body = (result.note or result.error or "(抓取失敗)") + "\n"
        else:
            body = result.markdown.rstrip() + "\n"

        dest.write_text(frontmatter + body, encoding="utf-8")
        logger.info(
            "inbox write: %s (status=%s, layer=%s)",
            dest.name,
            result.status,
            result.fulltext_layer,
        )
        return dest

    # ── Internals ────────────────────────────────────────────────────────────

    def _next_available_path(self, slug: str) -> Path:
        """Return ``slug.md`` or first ``slug-N.md`` that doesn't yet exist."""
        base = self._inbox_dir / f"{slug}.md"
        if not base.exists():
            return base
        counter = 1
        while True:
            candidate = self._inbox_dir / _COLLISION_FORMAT.format(
                stem=slug, counter=counter, suffix=".md"
            )
            if not candidate.exists():
                return candidate
            counter += 1

    @staticmethod
    def _serialise_frontmatter(
        *,
        title: str,
        original_url: str,
        source_type: str,
        content_nature: str,
        fulltext_status: str,
        fulltext_layer: str,
        fulltext_source: str,
        note: str | None,
        zotero_item_key: str | None = None,
        zotero_attachment_path: str | None = None,
        attachment_type: str | None = None,
    ) -> str:
        """Return YAML frontmatter block (newline-delimited, ends with ``---\\n\\n``).

        Free-text fields (title / urls / fulltext_source / note) are wrapped in
        double quotes and escaped via ``_yaml_double_quoted`` — they routinely
        carry ``"`` and ``\\`` from real-world headlines / URLs, and unescaped
        characters silently break ``yaml.safe_load`` in downstream readers.

        Controlled-vocabulary fields (source_type / content_nature /
        fulltext_status / fulltext_layer) emit bare values — the caller has
        already allowlisted them, so quoting just adds noise.
        """
        lines = [
            "---",
            f'title: "{_yaml_double_quoted(title)}"',
            f'source: "{_yaml_double_quoted(original_url)}"',
            f'original_url: "{_yaml_double_quoted(original_url)}"',
            f"source_type: {_yaml_safe(source_type)}",
            f"content_nature: {_yaml_safe(content_nature)}",
            f"fulltext_status: {_yaml_safe(fulltext_status)}",
            f"fulltext_layer: {_yaml_safe(fulltext_layer)}",
            f'fulltext_source: "{_yaml_double_quoted(fulltext_source)}"',
        ]
        if note:
            lines.append(f'note: "{_yaml_double_quoted(note)}"')
        # Zotero-specific fields (Slice 1 #389) — emitted only when present so
        # URL-ingest frontmatter stays free of zotero_* keys.
        if zotero_item_key is not None:
            lines.append(f"zotero_item_key: {_yaml_safe(zotero_item_key)}")
        if zotero_attachment_path is not None:
            lines.append(f'zotero_attachment_path: "{_yaml_double_quoted(zotero_attachment_path)}"')
        if attachment_type is not None:
            lines.append(f"attachment_type: {_yaml_safe(attachment_type)}")
        lines.extend(["---", ""])
        return "\n".join(lines) + "\n"
