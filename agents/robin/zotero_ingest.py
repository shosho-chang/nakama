"""Two-file fan-out for Zotero-sourced ingest (ADR-019, Slice 3 #391).

Entry point: ``produce_source_pages(slug, *, vault_root, zotero_root)``

Reads:
- ``Inbox/kb/{slug}.md`` — frontmatter with Zotero metadata (item_key /
  attachment_path / attachment_type / title / original_url)
- ``Inbox/kb/{slug}-bilingual.md`` — bilingual content for annotated page
  (written by the /translate route; may be absent if user hasn't translated yet)
- ``KB/Annotations/{ann_slug}.md`` — annotation set for the bilingual file
  (keyed on the bilingual file's annotation_slug, as the reader saves it)

Writes (to ``KB/Wiki/Sources/``):
- ``{slug}.md`` — raw source page (content re-extracted from Zotero attachment,
  LLM-untouched; never copied from inbox MD per ADR-019 zero-trust guarantee)
- ``{slug}--annotated.md`` — bilingual MD with annotation callouts woven in
  (only when the bilingual sibling exists)

Returns ``(raw_path, annotated_path)`` — annotated_path is ``None`` when
the bilingual sibling is absent.

Concept / entity extraction is deliberately NOT triggered here (ADR-019 §MVP
scope / Q10 — annotation-aware extraction is Phase 2 after 3-5 papers run
through).

Inbox files + annotation files are NOT removed after ingest (no lifecycle
management — Phase 2 policy per acceptance criteria).
"""

from __future__ import annotations

from pathlib import Path

from agents.robin.annotation_weave import weave
from shared.annotation_store import AnnotationStore, annotation_slug
from shared.log import get_logger
from shared.pdf_parser import parse_pdf
from shared.utils import extract_frontmatter, read_text

logger = get_logger("nakama.robin.zotero_ingest")

_SOURCES_DIR = Path("KB") / "Wiki" / "Sources"
_INBOX_DIR = Path("Inbox") / "kb"

# Trafilatura option set mirrors zotero_sync.sync_zotero_item for consistency.
_TRAFILATURA_OPTS: dict = dict(
    output_format="markdown",
    include_comments=False,
    include_tables=True,
    include_images=False,  # source page is text-only; assets already in vault
    include_links=True,
    favor_recall=True,
)


def produce_source_pages(
    slug: str,
    *,
    vault_root: Path,
    zotero_root: Path,
) -> tuple[Path, Path | None]:
    """Fan out inbox Zotero item to raw + annotated source pages (ADR-019).

    Args:
        slug:        Inbox file stem (e.g. ``"Sleep-Research-Paper"``).
        vault_root:  Absolute path to the vault root directory.
        zotero_root: Absolute path to the local Zotero library root (used only
                     when the attachment path in frontmatter points there).

    Returns:
        ``(raw_path, annotated_path)`` — ``annotated_path`` is ``None``
        when ``{slug}-bilingual.md`` does not exist in the inbox.

    Raises:
        FileNotFoundError: inbox file not found.
        ValueError:        inbox file is not a Zotero-sourced item (missing
                           ``zotero_item_key`` frontmatter field).
    """
    inbox_dir = vault_root / _INBOX_DIR
    sources_dir = vault_root / _SOURCES_DIR
    sources_dir.mkdir(parents=True, exist_ok=True)

    # ── Read inbox metadata ───────────────────────────────────────────────────
    inbox_path = inbox_dir / f"{slug}.md"
    if not inbox_path.exists():
        raise FileNotFoundError(f"Inbox file not found: {inbox_path}")

    fm, _ = extract_frontmatter(read_text(inbox_path))
    item_key = fm.get("zotero_item_key")
    if not item_key:
        raise ValueError(f"Not a Zotero-sourced inbox file (missing zotero_item_key): {slug}")

    attachment_path_str = str(fm.get("zotero_attachment_path", "") or "")
    attachment_type = str(fm.get("attachment_type", "") or "text/html")
    title = str(fm.get("title", slug) or slug)
    original_url = str(fm.get("original_url", fm.get("source", "")) or "")

    # ── Re-extract raw content from attachment ────────────────────────────────
    attachment_path = Path(attachment_path_str)
    raw_md = _extract_attachment(attachment_path, attachment_type)

    # ── Write raw source page ─────────────────────────────────────────────────
    annotated_sibling = f"{slug}--annotated.md"
    bilingual_path = inbox_dir / f"{slug}-bilingual.md"
    has_bilingual = bilingual_path.exists()

    raw_fm = _raw_frontmatter(
        title=title,
        original_url=original_url,
        item_key=item_key,
        attachment_path=attachment_path_str,
        attachment_type=attachment_type,
        annotated_sibling=annotated_sibling if has_bilingual else None,
    )
    raw_path = sources_dir / f"{slug}.md"
    raw_path.write_text(raw_fm + raw_md.rstrip() + "\n", encoding="utf-8")
    logger.info("zotero_ingest: raw source page written: %s", raw_path.name)

    if not has_bilingual:
        logger.info("zotero_ingest: no bilingual sibling for %s — annotated page skipped", slug)
        return raw_path, None

    # ── Load bilingual body + annotations ─────────────────────────────────────
    bilingual_content = read_text(bilingual_path)
    bilingual_fm, bilingual_body = extract_frontmatter(bilingual_content)

    ann_store = AnnotationStore()
    # Annotations are keyed on the bilingual file's annotation_slug (the reader
    # saves annotations using the bilingual filename + frontmatter).
    bil_ann_slug = annotation_slug(bilingual_path.name, bilingual_fm)
    ann_set = ann_store.load(bil_ann_slug)
    ann_items = ann_set.items if ann_set is not None else []

    woven_body = weave(bilingual_body, ann_items)

    # ── Write annotated source page ───────────────────────────────────────────
    ann_fm = _annotated_frontmatter(
        title=title,
        original_url=original_url,
        item_key=item_key,
        attachment_path=attachment_path_str,
        attachment_type=attachment_type,
        raw_source=f"{slug}.md",
    )
    ann_path = sources_dir / annotated_sibling
    ann_path.write_text(ann_fm + woven_body.rstrip() + "\n", encoding="utf-8")
    logger.info("zotero_ingest: annotated source page written: %s", ann_path.name)

    return raw_path, ann_path


# ── Extraction helpers ────────────────────────────────────────────────────────


def _extract_attachment(attachment_path: Path, attachment_type: str) -> str:
    """Re-extract markdown from the Zotero attachment file.

    HTML snapshot → Trafilatura (lazy import — matches the ``import fitz``
    pattern in ``zotero_assets.py``).  PDF → ``shared.pdf_parser.parse_pdf``.
    Returns empty string if extraction yields nothing.
    """
    if attachment_type == "text/html":
        import trafilatura  # noqa: PLC0415

        html = attachment_path.read_text(encoding="utf-8")
        return trafilatura.extract(html, **_TRAFILATURA_OPTS) or ""
    # PDF fallback (attachment_type == "application/pdf")
    return parse_pdf(attachment_path, with_tables=True)


# ── Frontmatter helpers ───────────────────────────────────────────────────────


def _yaml_dq(value: str) -> str:
    """Escape ``value`` for YAML double-quoted scalar context."""
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", " ")
        .replace("\r", " ")
        .strip()
    )


def _raw_frontmatter(
    *,
    title: str,
    original_url: str,
    item_key: str,
    attachment_path: str,
    attachment_type: str,
    annotated_sibling: str | None,
) -> str:
    lines = [
        "---",
        f'title: "{_yaml_dq(title)}"',
        f'source: "{_yaml_dq(original_url)}"',
        f'original_url: "{_yaml_dq(original_url)}"',
        f"zotero_item_key: {item_key}",
        f'zotero_attachment_path: "{_yaml_dq(attachment_path)}"',
        f"attachment_type: {attachment_type}",
    ]
    if annotated_sibling is not None:
        lines.append(f'annotated_sibling: "{_yaml_dq(annotated_sibling)}"')
    lines.extend(["---", ""])
    return "\n".join(lines) + "\n"


def _annotated_frontmatter(
    *,
    title: str,
    original_url: str,
    item_key: str,
    attachment_path: str,
    attachment_type: str,
    raw_source: str,
) -> str:
    lines = [
        "---",
        f'title: "{_yaml_dq(title)}"',
        f'source: "{_yaml_dq(original_url)}"',
        f'original_url: "{_yaml_dq(original_url)}"',
        f"zotero_item_key: {item_key}",
        f'zotero_attachment_path: "{_yaml_dq(attachment_path)}"',
        f"attachment_type: {attachment_type}",
        f'raw_source: "{_yaml_dq(raw_source)}"',
        "---",
        "",
    ]
    return "\n".join(lines) + "\n"
