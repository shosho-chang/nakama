"""Robin routes — KB ingest UI, reader, and search."""

import asyncio
import platform
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from agents.robin.agent import (
    EXTENSION_TO_RAW_DIR,
    EXTENSION_TO_SOURCE_TYPE,
    SOURCE_TYPE_TO_RAW_DIR,
)
from agents.robin.image_fetcher import fetch_images
from agents.robin.inbox_writer import InboxWriter
from agents.robin.ingest import IngestPipeline
from agents.robin.kb_search import search_kb
from agents.robin.url_dispatcher import URLDispatcher, URLDispatcherConfig
from shared.annotation_store import (
    AnnotationSet,
    AnnotationStore,
    annotation_slug,
    get_annotation_store,
)
from shared.config import get_agent_config, get_vault_path
from shared.discard_service import DiscardService
from shared.image_fetcher import download_markdown_images
from shared.log import get_logger
from shared.state import is_file_read, mark_file_processed, mark_file_read
from shared.translator import translate_document
from shared.utils import extract_frontmatter, read_text, slugify
from thousand_sunny.auth import check_auth, require_auth_or_key
from thousand_sunny.helpers import safe_resolve, sse

logger = get_logger("nakama.web.robin")
router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates" / "robin")
)
pipeline = IngestPipeline()


def _send_to_recycle_bin(path: Path) -> None:
    """刪除檔案至回收桶（Windows）或直接刪除（Linux）。遵守 CLAUDE.md 刪除規則。"""
    if platform.system() == "Windows":
        ps_cmd = (
            "Add-Type -AssemblyName Microsoft.VisualBasic; "
            "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("
            f"'{path}', 'OnlyErrorDialogs', 'SendToRecycleBin')"
        )
        subprocess.run(["powershell", "-Command", ps_cmd], check=False)
    else:
        path.unlink(missing_ok=True)


# ── Session store ─────────────────────────────────────────────────────────────

sessions: dict[str, dict] = {}
SESSION_TTL = 7200


def _new_session(**kwargs) -> str:
    sid = str(uuid.uuid4())
    sessions[sid] = {"created_at": time.time(), **kwargs}
    _cleanup_sessions()
    return sid


def _get_session(sid: str | None) -> dict | None:
    if not sid:
        return None
    return sessions.get(sid)


def _cleanup_sessions():
    now = time.time()
    expired = [k for k, v in sessions.items() if now - v["created_at"] > SESSION_TTL]
    for k in expired:
        del sessions[k]


# ── Vault helpers ─────────────────────────────────────────────────────────────


def _get_inbox() -> Path:
    cfg = get_agent_config("robin")
    return get_vault_path() / cfg.get("inbox_path", "Inbox/kb")


def _get_sources() -> Path:
    """KB/Wiki/Sources — 已 ingest 的文件（含 PubMed source pages 與雙語閱讀版本）。"""
    return get_vault_path() / "KB" / "Wiki" / "Sources"


# Reader 可讀寫的基底目錄白名單；外部路徑遭拒，防止路徑穿越。
_READER_BASES = {
    "inbox": _get_inbox,
    "sources": _get_sources,
}


def _resolve_reader_base(base: str) -> Path:
    """依白名單取得基底目錄；不在白名單直接 400。"""
    resolver = _READER_BASES.get(base)
    if resolver is None:
        raise HTTPException(400, detail=f"未知的 reader base：{base}")
    return resolver()


def _looks_like_web_clipper(fm: dict) -> bool:
    """True when frontmatter looks like Obsidian Web Clipper output.

    Web Clipper writes ``tags: [clippings, ...]`` (YAML list) or rarely a bare
    string. We also accept any md with a ``source`` URL but no ``original_url``
    key as a permissive fallback (covers Web Clipper variants with custom tag
    templates).
    """
    tags = fm.get("tags")
    if isinstance(tags, list) and "clippings" in tags:
        return True
    if isinstance(tags, str) and tags.strip() == "clippings":
        return True
    if fm.get("source") and not fm.get("original_url"):
        return True
    return False


def _get_inbox_files() -> list[dict]:
    inbox = _get_inbox()
    if not inbox.exists():
        return []
    supported = set(EXTENSION_TO_RAW_DIR.keys())
    # Collapse `{stem}.md` + `{stem}-bilingual.md` siblings: when the bilingual
    # variant exists, hide the raw `{stem}.md` so the inbox lists one row per
    # logical source. The bilingual file is what the user reads + annotates;
    # the raw sibling stays on disk for re-translation but is not user-facing.
    bilingual_stems = {
        f.name[: -len("-bilingual.md")]
        for f in inbox.iterdir()
        if f.is_file() and f.name.endswith("-bilingual.md")
    }
    files = []
    for f in sorted(inbox.iterdir()):
        if f.is_file() and f.suffix.lower() in supported:
            if f.suffix.lower() == ".md" and not f.name.endswith("-bilingual.md"):
                if f.stem in bilingual_stems:
                    continue
            size_kb = f.stat().st_size // 1024
            # Slice 1 (issue #352): inbox row status icon — read frontmatter
            # ``fulltext_status`` if present (URL ingest pipeline writes it).
            # Files without that field (manual drops, legacy placeholders) get
            # an empty status string so the template suppresses the icon.
            status = ""
            source_label = ""
            title = ""
            if f.suffix.lower() == ".md":
                try:
                    fm, _ = extract_frontmatter(read_text(f))
                    status = str(fm.get("fulltext_status", "") or "")
                    source_label = str(fm.get("fulltext_source", "") or "")
                    title = str(fm.get("title", "") or "").strip()
                    # Obsidian Web Clipper files (Chrome plugin) drop into
                    # Inbox/kb/ with their own frontmatter shape (no
                    # fulltext_status / fulltext_source — just title / source /
                    # author / tags=[clippings]). Synthesise a display row so
                    # the inbox lists them as "ready" with a "Web Clipper"
                    # source label, without rewriting the user's vault file.
                    if not status and _looks_like_web_clipper(fm):
                        status = "ready"
                        if not source_label:
                            source_label = "Web Clipper"
                except OSError:
                    pass
            files.append(
                {
                    "name": f.name,
                    "title": title,
                    "size": f"{size_kb} KB" if size_kb >= 1 else f"{f.stat().st_size} B",
                    "type": EXTENSION_TO_SOURCE_TYPE.get(f.suffix.lower(), "article"),
                    "annotatable": f.suffix.lower() in (".md", ".txt"),
                    "is_read": is_file_read(f),
                    "fulltext_status": status,
                    "fulltext_source": source_label,
                }
            )
    return files


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, nakama_auth: str | None = Cookie(None)):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/", status_code=302)
    files = _get_inbox_files()
    return templates.TemplateResponse(request, "index.html", {"files": files})


@router.get("/read", response_class=HTMLResponse)
async def read_source(
    request: Request,
    file: str,
    base: str = "inbox",
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login", status_code=302)
    base_dir = _resolve_reader_base(base)
    file_path = safe_resolve(base_dir, file)
    if not file_path.exists():
        raise HTTPException(404, detail=f"找不到檔案：{file}")
    if file_path.suffix.lower() not in (".md", ".txt"):
        raise HTTPException(400, detail="此檔案格式不支援線上閱讀")

    fetched = await asyncio.to_thread(fetch_images, file_path)
    if fetched:
        logger.info(f"已為 {file} 下載 {fetched} 張外部圖片")

    content = read_text(file_path)
    frontmatter, body = extract_frontmatter(content)

    frontmatter_raw = ""
    if frontmatter and content.startswith("---"):
        frontmatter_raw = content[: content.index("---", 3) + 3]

    slug = annotation_slug(file, frontmatter)
    ann_store: AnnotationStore = get_annotation_store()
    ann_set = ann_store.load(slug)
    annotations = [item.model_dump() for item in ann_set.items] if ann_set else []

    return templates.TemplateResponse(
        request,
        "reader.html",
        {
            "filename": file,
            "base": base,
            "slug": slug,
            "content": body,
            "frontmatter": frontmatter,
            "frontmatter_raw": frontmatter_raw,
            "annotations": annotations,
            "unsynced_count": ann_store.unsynced_count(slug),
            "source_type": EXTENSION_TO_SOURCE_TYPE.get(file_path.suffix.lower(), "article"),
            "is_read": is_file_read(file_path),
            "is_bilingual": bool(frontmatter.get("bilingual")),
        },
    )


@router.get("/files/{path:path}")
async def serve_vault_file(path: str, nakama_auth: str | None = Cookie(None)):
    """提供 vault 中的圖片給 reader 顯示。"""
    if not check_auth(nakama_auth):
        raise HTTPException(403)
    vault = get_vault_path()
    for base_dir in (vault / "Files", vault):
        try:
            candidate = safe_resolve(base_dir, path)
        except HTTPException:
            continue
        if candidate.exists() and candidate.is_file():
            return FileResponse(candidate)
    raise HTTPException(404)


@router.post("/save-annotations")
async def save_annotations(
    ann_set: AnnotationSet,
    nakama_auth: str | None = Cookie(None),
):
    """Accept a structured AnnotationSet and persist to KB/Annotations/{slug}.md.

    The original source file is never mutated (ADR-017).
    """
    if not check_auth(nakama_auth):
        raise HTTPException(403)
    # Validate that the declared base is known (prevents arbitrary slug writes from
    # unknown bases, even though KB/Annotations/ is the uniform destination).
    _resolve_reader_base(ann_set.base)
    store: AnnotationStore = get_annotation_store()
    store.save(ann_set)
    return {"status": "ok", "unsynced_count": store.unsynced_count(ann_set.slug)}


@router.post("/sync-annotations/{slug}")
async def sync_annotations(
    slug: str,
    nakama_auth: str | None = Cookie(None),
):
    """Sync AnnotationStore[slug] annotations into matching Concept page ## 個人觀點 sections.

    Returns a SyncReport with counts and any errors (ADR-017 Slice 2).
    """
    if not check_auth(nakama_auth):
        raise HTTPException(403)
    from agents.robin.annotation_merger import ConceptPageAnnotationMerger

    merger = ConceptPageAnnotationMerger()
    report = await asyncio.to_thread(merger.sync_source_to_concepts, slug)
    store: AnnotationStore = get_annotation_store()
    if not report.errors:
        await asyncio.to_thread(store.mark_synced, slug)
    report.unsynced_count = store.unsynced_count(slug)
    return report


@router.post("/mark-read")
async def mark_read(
    filename: str = Form(...),
    base: str = Form("inbox"),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        raise HTTPException(403)
    base_dir = _resolve_reader_base(base)
    file_path = safe_resolve(base_dir, filename)
    if not file_path.exists():
        raise HTTPException(404, detail=f"找不到檔案：{filename}")
    mark_file_read(file_path)
    return {"status": "ok"}


@router.get("/discard-info")
async def discard_info(
    file: str,
    base: str = "inbox",
    nakama_auth: str | None = Cookie(None),
):
    """Return ``{ slug, annotation_count }`` so frontend can render confirm prompt.

    Used by the「丟掉這篇」reader header button + inbox row delete button to
    fetch the count BEFORE showing the dialog (PRD §User Stories U24
    confirm 文字「丟掉「{filename}」**和 {N} 條 annotation**？」).

    Slice 5 (issue #356).
    """
    if not check_auth(nakama_auth):
        raise HTTPException(403)
    base_dir = _resolve_reader_base(base)
    file_path = safe_resolve(base_dir, file)
    if not file_path.exists():
        raise HTTPException(404, detail=f"找不到檔案：{file}")
    service = DiscardService()
    slug, count = service.annotation_count_for(file_path)
    return {"slug": slug, "annotation_count": count}


@router.post("/discard")
async def discard(
    file: str,
    base: str = "inbox",
    nakama_auth: str | None = Cookie(None),
):
    """Send a vault file (and its annotation companion) to recycle bin.

    Slice 5 (issue #356) — backs the reader header「丟掉這篇」button + inbox
    row delete button (PRD §User Stories U24/U25). The destructive logic
    lives in ``shared.discard_service.DiscardService`` so the endpoint stays
    a thin wrapper (auth + path resolution + redirect).

    Confirmation prompt 由前端 dialog 處理（POST 時已經確認過），所以後端直接
    執行；caller 不需要再傳 confirm flag。404 when the file doesn't exist
    means the inbox row was already gone (race with another tab) — frontend
    treats this as a successful discard.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse("/login", status_code=302)
    base_dir = _resolve_reader_base(base)
    file_path = safe_resolve(base_dir, file)
    # Idempotent: if the file is already gone we still call into the service
    # so any orphan annotation companion gets cleaned up.
    service = DiscardService()
    report = service.discard(file_path, base=base)
    logger.info(
        "discard endpoint: %s (deleted_file=%s, annotation_deleted=%s, count=%d)",
        file_path.name,
        report.deleted_file,
        report.annotation_deleted,
        report.annotation_count,
    )

    response = RedirectResponse("/", status_code=303)
    if nakama_auth:
        response.set_cookie("nakama_auth", nakama_auth, httponly=True)
    return response


_VALID_SOURCE_TYPES = {"article", "paper", "book", "video", "podcast"}
_VALID_CONTENT_NATURES = {
    "popular_science",
    "research",
    "textbook",
    "clinical_protocol",
    "narrative",
    "commentary",
}


def _slug_from_url(url: str) -> str:
    """Same slug derivation the legacy ``/scrape-translate`` used (line 311).

    Kept as a free function so the BackgroundTask can derive the same name
    the placeholder writer used (without re-doing the slugify dance inline).
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return slugify(parsed.netloc + parsed.path)[:60] or "scraped"


def _image_downloader_adapter(
    markdown: str,
    attachments_abs_dir: Path,
    vault_relative_prefix: str,
) -> tuple[str, list[str]]:
    """Adapter from URLDispatcher's positional shape to ``download_markdown_images`` kwargs.

    ``URLDispatcher`` calls the configured downloader as
    ``downloader(markdown, attachments_abs_dir, vault_relative_prefix)`` (so the
    schema-of-protocol matches what was promised in PR #357 review). The
    underlying ``shared.image_fetcher.download_markdown_images`` uses keyword
    arguments + a different parameter name (``dest_dir`` rather than
    ``attachments_abs_dir``). This adapter bridges the two without refactoring
    the existing image_fetcher (it has its own callers + tests we don't want
    to touch in Slice 4 scope).
    """
    return download_markdown_images(
        markdown,
        dest_dir=attachments_abs_dir,
        vault_relative_prefix=vault_relative_prefix,
    )


def _ingest_url_in_background(
    *,
    url: str,
    placeholder_path: Path,
    source_type: str,
    content_nature: str,
) -> None:
    """BackgroundTask body: dispatch URL → write final IngestResult into placeholder.

    Replaces the placeholder file in-place so the inbox row's filename never
    changes (the user can bookmark it once they redirect back to inbox view).

    On crash (anything raised by dispatcher / writer / vault path resolution),
    we still attempt a best-effort overwrite of the placeholder with
    ``fulltext_status=failed`` so the inbox row flips off 🔄. Without this
    fallback a writer crash would leave the row stuck on "處理中" forever and
    Slice 1 has no delete UI to recover (Slice 5 #356 adds the delete button).
    """
    try:
        # Combined config: Slice 4 image fetch (per-URL slug dir) + Slice 2 academic
        # 5-layer fallback (pubmed PDF dir). Separate slots avoid path collision
        # when a URL routes through both pipelines (general readability + image,
        # vs academic fetch_fulltext bypass).
        import os

        from agents.robin.pubmed_fulltext import fetch_fulltext as _fetch_fulltext

        slug = placeholder_path.stem
        image_attachments_abs_dir = get_vault_path() / "KB" / "Attachments" / "inbox" / slug
        image_vault_relative_prefix = f"KB/Attachments/inbox/{slug}/"

        # Academic fetch_fulltext only activates when email is configured —
        # NCBI / Unpaywall both require contact email. Without it, academic
        # URLs fall through to the general readability path (Slice 1 baseline).
        _email = (
            os.environ.get("UNPAYWALL_EMAIL") or os.environ.get("NOTIFY_TO", "")
        ).strip() or None
        _ncbi_key = os.environ.get("PUBMED_API_KEY") or None
        _fulltext_dir = get_vault_path() / "KB" / "Attachments" / "pubmed"
        _fulltext_prefix = "KB/Attachments/pubmed"

        config = URLDispatcherConfig(
            fetch_fulltext_fn=_fetch_fulltext if _email else None,
            image_downloader_fn=_image_downloader_adapter,
            email=_email,
            ncbi_api_key=_ncbi_key,
            fulltext_attachments_abs_dir=_fulltext_dir if _email else None,
            fulltext_vault_relative_prefix=_fulltext_prefix if _email else None,
            image_attachments_abs_dir=image_attachments_abs_dir,
            image_vault_relative_prefix=image_vault_relative_prefix,
        )
        dispatcher = URLDispatcher(config)
        result = dispatcher.dispatch(url)
        writer = InboxWriter(_get_inbox())
        writer.write_to_inbox(
            result,
            slug=placeholder_path.stem,
            existing_path=placeholder_path,
            source_type=source_type,
            content_nature=content_nature,
        )
    except Exception as exc:  # noqa: BLE001 — never let a BackgroundTask raise
        logger.exception("scrape-translate background task crashed (url=%s)", url)
        _flip_placeholder_to_failed(
            placeholder_path=placeholder_path,
            url=url,
            exc=exc,
            source_type=source_type,
            content_nature=content_nature,
        )


def _flip_placeholder_to_failed(
    *,
    placeholder_path: Path,
    url: str,
    exc: BaseException,
    source_type: str,
    content_nature: str,
) -> None:
    """Best-effort overwrite of the placeholder when the BG task itself crashes.

    Wrapped in its own try/except: if even this recovery write fails (vault
    unreachable, disk full), we log and give up — the row will stay 🔄 but
    that's no worse than the pre-fix behaviour.
    """
    from shared.schemas.ingest_result import IngestResult

    try:
        crash_result = IngestResult(
            status="failed",
            fulltext_layer="unknown",
            fulltext_source="(後台任務崩潰)",
            markdown="",
            title=placeholder_path.stem,
            original_url=url,
            error=f"{type(exc).__name__}: {exc}",
            note="後台任務崩潰，請重試或檢查日誌",
        )
        writer = InboxWriter(_get_inbox())
        writer.write_to_inbox(
            crash_result,
            slug=placeholder_path.stem,
            existing_path=placeholder_path,
            source_type=source_type,
            content_nature=content_nature,
        )
    except Exception:  # noqa: BLE001 — last-resort logging
        logger.exception(
            "could not flip placeholder to failed (placeholder=%s, url=%s)",
            placeholder_path,
            url,
        )


@router.post("/scrape-translate")
async def scrape_translate(
    background_tasks: BackgroundTasks,
    url: str = Form(...),
    source_type: str = Form("article"),
    content_nature: str = Form("popular_science"),
    nakama_auth: str | None = Cookie(None),
):
    """Stage 1 URL ingest entry-point (PRD docs/plans/2026-05-04-stage-1-ingest-unify.md).

    Slice 1 behaviour (issue #352):

    1. Validate auth + form values (allowlist source_type / content_nature).
    2. Same-URL short-circuit: if any inbox file's frontmatter already has
       ``original_url == url``, redirect straight to ``/read`` without
       re-fetching.
    3. Write a ``status='processing'`` placeholder file under
       ``Inbox/kb/{slug}.md`` so the inbox view immediately shows a 🔄 row.
    4. Schedule a ``BackgroundTask`` that runs ``URLDispatcher.dispatch()``
       and overwrites the placeholder with the final ``IngestResult`` (ready
       or failed).
    5. Redirect 303 → ``/`` (inbox view) so the user sees their pending row.

    Slice 2+ will widen URLDispatcher to academic 5-layer fallback. Slice 3
    will wire image_fetcher. Slice 4 will add discard / translate buttons.
    """
    if not check_auth(nakama_auth):
        return RedirectResponse("/login", status_code=302)

    source_type = source_type if source_type in _VALID_SOURCE_TYPES else "article"
    content_nature = (
        content_nature if content_nature in _VALID_CONTENT_NATURES else "popular_science"
    )

    inbox = _get_inbox()
    writer = InboxWriter(inbox)

    # Same-URL short-circuit (PRD §Pipeline / API "短路條件" + acceptance #6).
    # Always redirect to inbox (`/`) — the new-ingest path also lands there, so
    # the user sees a consistent destination whether the URL was new or repeat.
    existing = writer.find_existing_for_url(url)
    if existing is not None:
        logger.info("scrape-translate short-circuit (existing url): %s", existing.name)
        response = RedirectResponse("/", status_code=303)
        if nakama_auth:
            response.set_cookie("nakama_auth", nakama_auth, httponly=True)
        return response

    slug = _slug_from_url(url)
    from urllib.parse import urlparse

    parsed = urlparse(url)
    placeholder_title = f"{parsed.netloc}{parsed.path}".strip("/") or url
    placeholder_path = writer.write_placeholder(
        slug=slug,
        original_url=url,
        title=placeholder_title,
        source_type=source_type,
        content_nature=content_nature,
    )

    background_tasks.add_task(
        _ingest_url_in_background,
        url=url,
        placeholder_path=placeholder_path,
        source_type=source_type,
        content_nature=content_nature,
    )

    response = RedirectResponse("/", status_code=303)
    if nakama_auth:
        response.set_cookie("nakama_auth", nakama_auth, httponly=True)
    return response


_BILINGUAL_SUFFIX = "-bilingual.md"
_FULLTEXT_STATUS_RE = re.compile(r"^fulltext_status:\s*\S+\s*$", re.MULTILINE)
_BILINGUAL_FRONTMATTER = (
    "---\n"
    'title: "{title} — 雙語閱讀版"\n'
    'source: "{source}"\n'
    'original_url: "{source}"\n'
    "source_type: {source_type}\n"
    "content_nature: {content_nature}\n"
    "fulltext_status: translated\n"
    "fulltext_layer: {layer}\n"
    'fulltext_source: "{fulltext_source}"\n'
    "bilingual: true\n"
    'derived_from: "Inbox/kb/{stem}.md"\n'
    "---\n\n"
)


def _bilingual_path_for(source_path: Path) -> Path:
    """Return the ``-bilingual.md`` sibling path for a Slice 3 inbox source.

    Idempotent for already-bilingual inputs: re-translating a path that
    already ends in ``-bilingual.md`` returns the SAME path (defends
    against a UI bug where the bilingual reader re-posts its own filename).
    """
    if source_path.name.endswith(_BILINGUAL_SUFFIX):
        return source_path
    return source_path.with_name(source_path.stem + _BILINGUAL_SUFFIX)


def _flip_status_to_translated(source_path: Path) -> None:
    """Mutate ``fulltext_status`` in the source frontmatter to ``translated``.

    Targeted regex replace on the single status line — keeps the rest of
    the YAML block (and the markdown body) byte-for-byte identical so
    annotation references that were anchored to the body still resolve.
    Silent no-op if the file lacks the field (manual drops, legacy
    placeholders) — we don't synthesise a status retroactively.
    """
    try:
        text = read_text(source_path)
    except OSError:
        logger.exception("could not read source for status flip: %s", source_path)
        return
    new_text, count = _FULLTEXT_STATUS_RE.subn("fulltext_status: translated", text, count=1)
    if count == 0:
        logger.info("no fulltext_status field to flip in %s — skipping", source_path.name)
        return
    source_path.write_text(new_text, encoding="utf-8")


def _flip_status_to_translating(source_path: Path) -> None:
    """Mutate ``fulltext_status`` in the source frontmatter to ``translating``.

    Mirror of :func:`_flip_status_to_translated`. The ``translating`` state
    is a transient intermediate marker (``ready`` → ``translating`` →
    ``translated``) that the inbox row can render so 修修 sees the file is
    in flight rather than (a) being dumped onto a 404 bilingual reader page
    or (b) clicking 「翻譯」 a second time. The BG task flips it forward
    to ``translated`` on completion via :func:`_flip_status_to_translated`,
    so a crash mid-translate leaves the row stuck on ``translating`` —
    intentional surface so the user can notice + retry rather than the row
    silently snapping back to ``ready`` and hiding the failure.

    Silent no-op if the file lacks the field — same contract as the
    ``translated`` flipper.
    """
    try:
        text = read_text(source_path)
    except OSError:
        logger.exception("could not read source for status flip: %s", source_path)
        return
    new_text, count = _FULLTEXT_STATUS_RE.subn("fulltext_status: translating", text, count=1)
    if count == 0:
        logger.info("no fulltext_status field to flip in %s — skipping", source_path.name)
        return
    source_path.write_text(new_text, encoding="utf-8")


def _translate_in_background(
    *,
    source_path: Path,
    bilingual_path: Path,
) -> None:
    """BackgroundTask body: run translate_document → write bilingual.md → flip status.

    Mirrors the ``/pubmed-to-reader`` translate flow but with two
    differences: (a) the source is the URL-ingested ``Inbox/kb/{slug}.md``
    rather than ``KB/Attachments/pubmed/{pmid}.{pdf,md}``, and (b) on
    translator failure we do NOT write a partial bilingual file — the
    user can read the original under the same inbox row, so silently
    falling back like the PubMed path would just hide the failure.
    """
    try:
        content = read_text(source_path)
    except OSError:
        logger.exception("translate BG: could not read source %s", source_path)
        return
    fm, body = extract_frontmatter(content)
    raw_md = body or content

    try:
        bilingual_md = translate_document(raw_md)
    except Exception:  # noqa: BLE001 — never let a BackgroundTask raise
        logger.exception("translate BG crashed for %s", source_path.name)
        return

    title = str(fm.get("title", source_path.stem) or source_path.stem)
    source_url = str(fm.get("original_url", fm.get("source", "")) or "")
    source_type = str(fm.get("source_type", "article") or "article")
    content_nature = str(fm.get("content_nature", "popular_science") or "popular_science")
    layer = str(fm.get("fulltext_layer", "readability") or "readability")
    fulltext_source = str(fm.get("fulltext_source", "Readability") or "Readability")

    frontmatter = _BILINGUAL_FRONTMATTER.format(
        title=title.replace('"', '\\"'),
        source=source_url.replace('"', '\\"'),
        source_type=source_type,
        content_nature=content_nature,
        layer=layer,
        fulltext_source=fulltext_source.replace('"', '\\"'),
        stem=source_path.stem,
    )
    bilingual_path.write_text(frontmatter + bilingual_md, encoding="utf-8")
    _flip_status_to_translated(source_path)
    logger.info("translate BG complete: %s", bilingual_path.name)


@router.post("/translate")
async def translate(
    background_tasks: BackgroundTasks,
    file: str,
    nakama_auth: str | None = Cookie(None),
):
    """Trigger on-demand translation of an inbox source (Slice 3, issue #354).

    Flow (PRD docs/plans/2026-05-04-stage-1-ingest-unify.md §Pipeline / API):

    1. Auth gate.
    2. Validate ``file`` (markdown only, no path traversal).
    3. Short-circuit: if ``{stem}-bilingual.md`` already exists, redirect
       straight to the reader without scheduling a BG task — mirrors
       ``/pubmed-to-reader`` line 499 and the PRD §Pipeline / API
       "短路條件" / acceptance #6.
    4. Else flip the source row to ``fulltext_status: translating``,
       schedule ``_translate_in_background``, and redirect back to the
       inbox view (``/``) — NOT ``/read?file={stem}-bilingual.md``.

    Why the redirect target is ``/`` and not the bilingual reader:
    translation takes ~3min on a long article; redirecting straight to
    ``/read?file={stem}-bilingual.md`` raced the BG write and 404'd
    every long article (BMJ Medicine reproduction 2026-05-04). Sending
    the user back to the inbox lets them watch the 🔄 (translating)
    icon flip to 📖 (translated), then click 「閱讀」 to jump in once the
    file actually exists. Costs one extra click but trades a 100%
    failure mode for a 0% one.

    The BG task writes ``Inbox/kb/{stem}-bilingual.md`` and mutates the
    source frontmatter to ``fulltext_status: translated``. On translator
    crash the bilingual file is never written; the source row is left in
    ``translating`` so the failure is visible (mirror of the
    "no silent fallback to raw" choice in ``_translate_in_background``).
    """
    if not check_auth(nakama_auth):
        return RedirectResponse("/login", status_code=302)

    inbox = _get_inbox()
    source_path = safe_resolve(inbox, file)
    if not source_path.exists():
        raise HTTPException(404, detail=f"找不到檔案：{file}")
    if source_path.suffix.lower() != ".md":
        raise HTTPException(400, detail="只有 markdown 檔案能翻譯")

    bilingual_path = _bilingual_path_for(source_path)
    if bilingual_path.exists():
        logger.info("translate short-circuit (bilingual exists): %s", bilingual_path.name)
        response = RedirectResponse(f"/read?file={bilingual_path.name}", status_code=303)
        if nakama_auth:
            response.set_cookie("nakama_auth", nakama_auth, httponly=True)
        return response

    # Flip BEFORE scheduling so the inbox row reflects "in flight" the
    # moment the user is redirected back. Doing it inside the BG body
    # would leave a window where the row still shows ✅ ready but the
    # translate button is being processed → looks idle, invites a
    # second click.
    _flip_status_to_translating(source_path)

    background_tasks.add_task(
        _translate_in_background,
        source_path=source_path,
        bilingual_path=bilingual_path,
    )
    response = RedirectResponse("/", status_code=303)
    if nakama_auth:
        response.set_cookie("nakama_auth", nakama_auth, httponly=True)
    return response


_PUBMED_FT_DIR = "KB/Attachments/pubmed"
_PMID_RE = re.compile(r"^\d+$")


@router.get("/pubmed-to-reader")
async def pubmed_to_reader(
    pmid: str,
    nakama_auth: str | None = Cookie(None),
):
    """將 PubMed 下載的 OA 全文轉為雙語 Markdown，並跳轉到 reader。

    Source 優先順序：
    1. ``KB/Attachments/pubmed/{pmid}.pdf`` → parse_pdf → translate
    2. ``KB/Attachments/pubmed/{pmid}.md`` → 直接 translate（oa_html case，
       publisher HTML 已被 `pubmed_html.fetch_publisher_html()` 轉成 markdown）

    - 輸出：``KB/Wiki/Sources/pubmed-{pmid}-bilingual.md``（與原 source 並列）
    - 第二次點同一篇會 short-circuit：發現雙語檔已存在就直接跳 reader，不重翻
    - 翻譯成本：Claude Sonnet，每篇約 5–15 萬字（看來源長度），成本 $0.3–1
    """
    if not check_auth(nakama_auth):
        return RedirectResponse("/login", status_code=302)

    if not _PMID_RE.match(pmid):
        raise HTTPException(400, detail="pmid 必須是純數字")

    sources_dir = _get_sources()
    bilingual_name = f"pubmed-{pmid}-bilingual.md"
    bilingual_path = sources_dir / bilingual_name

    # 已翻譯過就直接開 reader，不重翻
    if bilingual_path.exists():
        response = RedirectResponse(f"/read?file={bilingual_name}&base=sources", status_code=303)
        if nakama_auth:
            response.set_cookie("nakama_auth", nakama_auth, httponly=True)
        return response

    attachments_dir = get_vault_path() / _PUBMED_FT_DIR
    pdf_path = attachments_dir / f"{pmid}.pdf"
    html_md_path = attachments_dir / f"{pmid}.md"

    # Lazy rebind so test suites patching ``shared.translator.translate_document``
    # (e.g. tests/test_pubmed_to_reader_route.py) still hit this branch — without
    # the lazy import this function would close over the module-level binding
    # imported at the top of the file, bypassing the patch.
    from shared.pdf_parser import parse_pdf
    from shared.translator import translate_document  # noqa: F811

    raw_md: str
    source_kind: str
    derived_from: str
    if pdf_path.exists():
        try:
            raw_md = await asyncio.to_thread(parse_pdf, pdf_path, with_tables=True)
        except Exception as e:
            logger.error(f"PDF 解析失敗（PMID {pmid}）：{e}", exc_info=True)
            raise HTTPException(500, detail=f"PDF 解析失敗：{e}") from e
        source_kind = "pdf"
        derived_from = f"{_PUBMED_FT_DIR}/{pmid}.pdf"
    elif html_md_path.exists():
        try:
            raw_md = await asyncio.to_thread(html_md_path.read_text, encoding="utf-8")
        except Exception as e:
            logger.error(f"讀取 publisher HTML markdown 失敗（PMID {pmid}）：{e}", exc_info=True)
            raise HTTPException(500, detail=f"讀 HTML md 失敗：{e}") from e
        source_kind = "html"
        derived_from = f"{_PUBMED_FT_DIR}/{pmid}.md"
    else:
        raise HTTPException(
            404,
            detail=(
                f"找不到 {_PUBMED_FT_DIR}/{pmid}.pdf 或 {_PUBMED_FT_DIR}/{pmid}.md — "
                "可能是 non-OA 論文，Robin digest 未下載"
            ),
        )

    try:
        bilingual_md = await asyncio.to_thread(translate_document, raw_md)
    except Exception as e:
        logger.error(f"翻譯失敗（PMID {pmid}）：{e}", exc_info=True)
        bilingual_md = raw_md  # fallback：留純原文，使用者仍能閱讀 + annotate

    safe_pmid = pmid  # 已通過 _PMID_RE 驗證
    frontmatter = (
        "---\n"
        f'title: "PubMed {safe_pmid} — 雙語閱讀版"\n'
        f"pmid: {safe_pmid}\n"
        f'source: "https://pubmed.ncbi.nlm.nih.gov/{safe_pmid}/"\n'
        "source_type: paper\n"
        "content_nature: research\n"
        "bilingual: true\n"
        f"source_kind: {source_kind}\n"
        f'derived_from: "{derived_from}"\n'
        "---\n\n"
    )
    sources_dir.mkdir(parents=True, exist_ok=True)
    bilingual_path.write_text(frontmatter + bilingual_md, encoding="utf-8")
    logger.info(f"pubmed-to-reader 完成：{bilingual_name} (source={source_kind})")

    response = RedirectResponse(f"/read?file={bilingual_name}&base=sources", status_code=303)
    if nakama_auth:
        response.set_cookie("nakama_auth", nakama_auth, httponly=True)
    return response


@router.post("/start")
async def start(
    filename: str = Form(...),
    source_type: str = Form("article"),
    content_nature: str = Form("popular_science"),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login", status_code=302)

    inbox = _get_inbox()
    file_path = safe_resolve(inbox, filename)
    if not file_path.exists():
        raise HTTPException(404, detail=f"找不到檔案：{filename}")

    raw_dir = SOURCE_TYPE_TO_RAW_DIR.get(source_type, "Articles")
    raw_dest = get_vault_path() / "KB" / "Raw" / raw_dir / filename
    raw_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(file_path, raw_dest)

    sid = _new_session(
        step="summarizing",
        file_name=filename,
        file_path=str(file_path),
        raw_path=str(raw_dest),
        source_type=source_type,
        content_nature=content_nature,
        summary_body="",
        summary_path="",
        user_guidance="",
        plan={"concepts": [], "entities": []},
        result={"created": [], "updated": []},
        error="",
    )

    response = RedirectResponse("/processing", status_code=302)
    response.set_cookie("robin_session", sid, httponly=True)
    if nakama_auth:
        response.set_cookie("nakama_auth", nakama_auth, httponly=True)
    return response


@router.post("/cancel")
async def cancel(
    robin_session: str | None = Cookie(None),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login", status_code=302)

    sess = _get_session(robin_session)
    if sess:
        sess["step"] = "cancelled"
        # 清理已複製到 KB/Raw 的檔案（若尚在摘要階段，尚未產出任何 Wiki 頁面）
        raw_path = Path(sess.get("raw_path", ""))
        if raw_path.exists() and not sess.get("summary_path"):
            _send_to_recycle_bin(raw_path)
            logger.info(f"Cancel: 已清理 {raw_path}")

    response = RedirectResponse("/", status_code=302)
    response.delete_cookie("robin_session")
    return response


@router.get("/processing", response_class=HTMLResponse)
async def processing(
    request: Request,
    robin_session: str | None = Cookie(None),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login", status_code=302)
    sess = _get_session(robin_session)
    if not sess:
        return RedirectResponse("/", status_code=302)

    step_labels = {
        "summarizing": "Robin 正在閱讀文件並產出摘要...",
        "planning": "Robin 正在分析概念與實體...",
        "executing": "Robin 正在寫入 Wiki 頁面...",
    }
    label = step_labels.get(sess["step"], "處理中...")
    return templates.TemplateResponse(
        request, "processing.html", {"session_id": robin_session, "label": label}
    )


@router.get("/events/{session_id}")
async def events(session_id: str, nakama_auth: str | None = Cookie(None)):
    if not check_auth(nakama_auth):
        raise HTTPException(403)

    sess = _get_session(session_id)
    if not sess:
        raise HTTPException(404)

    async def generate():
        try:
            step = sess["step"]

            if step == "cancelled":
                yield sse("done", {"redirect": "/"})
                return

            if step == "summarizing":
                yield sse("status", {"msg": "Robin 正在閱讀文件..."})

                raw = Path(sess["raw_path"])
                if raw.suffix.lower() == ".pdf":
                    from shared.pdf_parser import parse_pdf

                    yield sse("status", {"msg": "正在解析 PDF..."})
                    content = await asyncio.to_thread(parse_pdf, raw)
                else:
                    content = read_text(raw)
                title = raw.stem
                author = ""
                if Path(sess["raw_path"]).suffix == ".md":
                    fm, body = extract_frontmatter(content)
                    title = fm.get("title", title)
                    author = fm.get("author", "")
                    content = body if body else content

                sess["_title"] = title
                sess["_author"] = author
                sess["_content"] = content

                is_large = len(content) > pipeline.LARGE_DOC_THRESHOLD
                if is_large:
                    from agents.robin.chunker import chunk_document

                    n_chunks = len(chunk_document(content))
                    yield sse(
                        "status",
                        {
                            "msg": f"偵測到大文件（{len(content):,} 字），"
                            f"將分 {n_chunks} 段 Map-Reduce 摘要，請耐心等候..."
                        },
                    )
                else:
                    yield sse("status", {"msg": "正在呼叫 Claude 產出摘要（約 10-30 秒）..."})

                summary = await asyncio.to_thread(
                    pipeline._generate_summary,
                    content=content,
                    title=title,
                    author=author,
                    source_type=sess["source_type"],
                    content_nature=sess.get("content_nature", ""),
                )
                sess["summary_body"] = summary

                from datetime import date

                from shared.obsidian_writer import write_page

                slug = slugify(title)
                summary_path = f"KB/Wiki/Sources/{slug}.md"
                try:
                    raw_relative = str(Path(sess["raw_path"]).relative_to(get_vault_path()))
                except ValueError:
                    raw_relative = str(Path(sess["raw_path"]))

                await asyncio.to_thread(
                    write_page,
                    summary_path,
                    {
                        "title": title,
                        "type": "source",
                        "status": "draft",
                        "created": str(date.today()),
                        "updated": str(date.today()),
                        "source_refs": [raw_relative],
                        "source_type": sess["source_type"],
                        "content_nature": sess.get("content_nature", "popular_science"),
                        "author": author,
                        "confidence": "medium",
                        "tags": [],
                        "related_pages": [],
                    },
                    summary,
                )
                sess["summary_path"] = summary_path
                sess["step"] = "awaiting_guidance"
                yield sse("done", {"redirect": "/review-summary"})

            elif step == "planning":
                yield sse("status", {"msg": "Robin 正在分析需要建立哪些概念頁面..."})
                yield sse("status", {"msg": "正在呼叫 Claude（約 10-20 秒）..."})

                plan = await asyncio.to_thread(
                    pipeline._get_concept_plan,
                    sess["summary_body"],
                    sess["summary_path"],
                    sess["user_guidance"],
                    content_nature=sess.get("content_nature", ""),
                )
                sess["plan"] = plan or {"concepts": [], "entities": []}
                sess["step"] = "awaiting_approval"
                yield sse("done", {"redirect": "/review-plan"})

            elif step == "executing":
                concepts = sess["plan"].get("concepts", [])
                entities = sess["plan"].get("entities", [])
                writes = sum(
                    1
                    for c in concepts
                    if c.get("action") in ("create", "update_merge", "update_conflict")
                ) + len(entities)
                noop_count = sum(1 for c in concepts if c.get("action") == "noop")
                msg = f"Robin 正在寫入 {writes} 個 Wiki 頁面"
                if noop_count:
                    msg += f"，並補充 {noop_count} 個既有頁面的引用"
                yield sse("status", {"msg": msg + "..."})

                await asyncio.to_thread(pipeline._execute_plan, sess["plan"], sess["summary_path"])

                title = sess.get("_title", Path(sess["raw_path"]).stem)
                slug = slugify(title)
                await asyncio.to_thread(pipeline._update_index, title, slug, sess["source_type"])

                mark_file_processed(Path(sess["file_path"]), "robin")
                _send_to_recycle_bin(Path(sess["file_path"]))

                concept_create = [
                    c.get("title") or c.get("slug") or "?"
                    for c in concepts
                    if c.get("action") == "create"
                ]
                concept_update = [
                    c.get("title") or c.get("slug") or "?"
                    for c in concepts
                    if c.get("action") in ("update_merge", "update_conflict")
                ]
                concept_noop = [
                    c.get("title") or c.get("slug") or "?"
                    for c in concepts
                    if c.get("action") == "noop"
                ]
                entity_create = [e.get("title", "?") for e in entities]
                sess["result"] = {
                    "created": concept_create + entity_create,
                    "updated": concept_update,
                    "referenced": concept_noop,
                }
                sess["step"] = "done"
                yield sse("done", {"redirect": "/done"})

            elif step in ("awaiting_guidance", "awaiting_approval", "done"):
                redirect_map = {
                    "awaiting_guidance": "/review-summary",
                    "awaiting_approval": "/review-plan",
                    "done": "/done",
                }
                yield sse("done", {"redirect": redirect_map[step]})

            else:
                yield sse("error", {"msg": f"未知狀態：{step}"})

        except Exception as e:
            logger.error(f"SSE error: {e}", exc_info=True)
            sess["step"] = "error"
            sess["error"] = str(e)
            yield sse("error", {"msg": str(e)})

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/review-summary", response_class=HTMLResponse)
async def review_summary(
    request: Request,
    robin_session: str | None = Cookie(None),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login", status_code=302)
    sess = _get_session(robin_session)
    if not sess or sess["step"] != "awaiting_guidance":
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        request,
        "review_summary.html",
        {"file_name": sess["file_name"], "summary": sess["summary_body"]},
    )


@router.post("/submit-guidance")
async def submit_guidance(
    guidance: str = Form(default=""),
    robin_session: str | None = Cookie(None),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login", status_code=302)
    sess = _get_session(robin_session)
    if not sess:
        return RedirectResponse("/", status_code=302)
    sess["user_guidance"] = guidance.strip()
    sess["step"] = "planning"
    response = RedirectResponse("/processing", status_code=302)
    if nakama_auth:
        response.set_cookie("nakama_auth", nakama_auth, httponly=True)
    return response


@router.get("/review-plan", response_class=HTMLResponse)
async def review_plan(
    request: Request,
    robin_session: str | None = Cookie(None),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login", status_code=302)
    sess = _get_session(robin_session)
    if not sess or sess["step"] != "awaiting_approval":
        return RedirectResponse("/", status_code=302)
    plan = sess.get("plan", {"concepts": [], "entities": []})
    return templates.TemplateResponse(
        request,
        "review_plan.html",
        {
            "file_name": sess["file_name"],
            "concepts": list(enumerate(plan.get("concepts", []))),
            "entities": list(enumerate(plan.get("entities", []))),
            "concepts_list": plan.get("concepts", []),
            "entities_list": plan.get("entities", []),
        },
    )


@router.post("/execute")
async def execute(
    request: Request,
    robin_session: str | None = Cookie(None),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login", status_code=302)
    sess = _get_session(robin_session)
    if not sess:
        return RedirectResponse("/", status_code=302)

    form = await request.form()
    plan = sess.get("plan", {"concepts": [], "entities": []})
    all_concepts = plan.get("concepts", [])
    all_entities = plan.get("entities", [])

    selected_concepts = [
        all_concepts[int(i)]
        for i in form.getlist("concept")
        if i.isdigit() and int(i) < len(all_concepts)
    ]
    selected_entities = [
        all_entities[int(i)]
        for i in form.getlist("entity")
        if i.isdigit() and int(i) < len(all_entities)
    ]

    sess["plan"] = {"concepts": selected_concepts, "entities": selected_entities}
    sess["step"] = "executing"

    response = RedirectResponse("/processing", status_code=302)
    if nakama_auth:
        response.set_cookie("nakama_auth", nakama_auth, httponly=True)
    return response


@router.get("/done", response_class=HTMLResponse)
async def done(
    request: Request,
    robin_session: str | None = Cookie(None),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login", status_code=302)
    sess = _get_session(robin_session)
    if not sess or sess["step"] != "done":
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        request,
        "done.html",
        {
            "file_name": sess["file_name"],
            "created": sess["result"].get("created", []),
            "updated": sess["result"].get("updated", []),
            "referenced": sess["result"].get("referenced", []),
        },
    )


@router.post("/kb/research")
async def kb_research(
    query: str = Form(...),
    _auth=Depends(require_auth_or_key),
):
    """Search KB/Wiki for pages relevant to query."""
    results = await asyncio.to_thread(search_kb, query, get_vault_path())
    return {"results": results}
