"""Books routes — bilingual EPUB library + foliate-js reader.

Slice 1D tracer bullet (issue #379): upload bilingual EPUB → list on /books →
read on /books/{id} via foliate-js. Read-only; annotation / progress / ingest
land in later slices.
"""

from __future__ import annotations

import hashlib
import io
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Cookie,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from shared.annotation_store import (
    AnnotationSetV2,
    AnnotationSetV3,
    get_annotation_store,
    upgrade_to_v3,
)
from shared.book_queue import (
    cancel as cancel_book,
)
from shared.book_queue import (
    delete_queue_row,
)
from shared.book_queue import (
    enqueue as enqueue_book,
)
from shared.book_storage import (
    BookStorageError,
    delete_book_files,
    get_book,
    insert_book,
    list_books,
    read_book_blob,
    read_cover_blob,
    store_book_files,
)
from shared.book_storage import (
    delete_book as delete_book_row,
)
from shared.epub_metadata import MalformedEPUBError, extract_metadata
from shared.epub_sanitizer import EPUBStructureError, sanitize_epub
from shared.log import get_logger
from shared.schemas.books import Book, BookProgress
from shared.source_mode import Mode, detect_book_mode
from shared.state import _get_conn
from shared.utils import slugify
from thousand_sunny.auth import check_auth

# Allowed values for the upload form ``mode`` parameter. ``"auto"`` triggers
# server-side detection from EPUB metadata.lang + body sample.
_VALID_MODE_FORM_VALUES = {"auto", "monolingual-zh", "bilingual-en-zh"}

_COVER_EXT_MEDIA_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "svg": "image/svg+xml",
}


_BODY_SAMPLE_CHARS = 1500
"""How many characters of EPUB body to feed langdetect when metadata.lang is
absent. ~1.5K is plenty for confident classification and stays small enough
that the upload route remains snappy."""


def _extract_body_sample(epub_bytes: bytes) -> str | None:
    """Pull a short text sample from the first XHTML chapter for lang
    detection fallback. Returns ``None`` on any parse failure — caller
    treats absence of a body sample as "rely on default mode".
    """
    try:
        with zipfile.ZipFile(io.BytesIO(epub_bytes)) as zf:
            xhtml_names = [
                n
                for n in zf.namelist()
                if n.lower().endswith((".xhtml", ".html"))
            ]
            if not xhtml_names:
                return None
            # Sort to pick the first content document deterministically;
            # nav.xhtml may be present but it's TOC noise — skip it.
            xhtml_names.sort()
            for name in xhtml_names:
                if "nav" in name.lower():
                    continue
                raw = zf.read(name).decode("utf-8", errors="replace")
                # Strip tags crudely — we only need a representative chunk
                # for langdetect; perfect HTML stripping isn't required.
                import re as _re

                stripped = _re.sub(r"<[^>]+>", " ", raw)
                stripped = _re.sub(r"\s+", " ", stripped).strip()
                if len(stripped) >= 100:
                    return stripped[:_BODY_SAMPLE_CHARS]
            return None
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError):
        return None


def _extract_cover_bytes(epub_bytes: bytes, cover_path: str | None) -> tuple[bytes, str] | None:
    """Return ``(bytes, ext)`` for the cover entry inside an EPUB zip, or None."""
    if not cover_path:
        return None
    ext = PurePosixPath(cover_path).suffix.lower()
    if ext not in {f".{k}" for k in _COVER_EXT_MEDIA_TYPES}:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(epub_bytes)) as zf:
            return zf.read(cover_path), ext
    except (KeyError, zipfile.BadZipFile):
        return None


logger = get_logger("nakama.web.books")
router = APIRouter()

# Serialize writes to the shared SQLite connection. Python's sqlite3 wrapper
# isn't fully thread-safe at the connection-state level (commit/rollback
# interleave under concurrent threads); see memory/claude/reference_sqlite_python_pitfalls.md.
_progress_write_lock = threading.Lock()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates" / "robin")
)


@router.get("/books", response_class=HTMLResponse)
async def books_library(request: Request, nakama_auth: str | None = Cookie(None)):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/books", status_code=302)
    books = list_books()
    enriched = [{**b.model_dump(), "ingest_status": _ingest_status(b.book_id)} for b in books]
    return templates.TemplateResponse(request, "books_library.html", {"books": enriched})


@router.get("/books/upload", response_class=HTMLResponse)
async def books_upload_form(request: Request, nakama_auth: str | None = Cookie(None)):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/books/upload", status_code=302)
    return templates.TemplateResponse(request, "book_upload.html", {})


@router.post("/books/upload")
async def books_upload(
    bilingual: UploadFile | None = File(None),
    original: UploadFile | None = File(None),
    book_id: str | None = Form(None),
    title: str = Form(""),
    lang_pair: str = Form(""),
    genre: str = Form(""),
    author: str = Form(""),
    mode: str = Form("auto"),
    nakama_auth: str | None = Cookie(None),
):
    """Upload an EPUB.

    Two supported shapes (PRD #507 Phase 1 minimal):

    - **monolingual** — single EPUB in the ``bilingual`` slot (Phase 1
      keeps the slot name; Phase 2 will rename to ``display``). Mode
      auto-detected from EPUB metadata.lang + body sample, or pinned
      explicitly via the ``mode`` form param.
    - **bilingual-with-original** — both ``bilingual`` (paired display
      copy) and ``original`` (English source EPUB) provided; ``mode``
      resolves to ``bilingual-en-zh`` by default. ``original``-only with
      no ``bilingual`` is also accepted (treated as "the original *is*
      what I want to read") — original bytes get copied into the
      bilingual.epub slot too so the Reader has something to render.

    The simplified UI sends only the file fields; everything else
    defaults / is derived. Form params are kept for direct API callers
    (scripted batch uploads, tests).
    """
    if not check_auth(nakama_auth):
        return RedirectResponse("/login", status_code=302)

    if mode not in _VALID_MODE_FORM_VALUES:
        raise HTTPException(
            400,
            detail=(
                f"invalid mode={mode!r}; expected one of "
                f"{sorted(_VALID_MODE_FORM_VALUES)}"
            ),
        )

    bilingual_bytes = (
        await bilingual.read() if bilingual is not None and bilingual.filename else b""
    )
    original_bytes_raw = (
        await original.read() if original is not None and original.filename else b""
    )

    if not bilingual_bytes and not original_bytes_raw:
        raise HTTPException(
            400, detail="upload requires at least one EPUB file (bilingual or original)"
        )

    # Track whether the upload truly carries a paired ``original``. When
    # only ``original`` was uploaded, we promote it into the bilingual
    # slot (so the Reader has a display copy) but treat the book as
    # bilingual-only — there's no second variant worth archiving.
    has_paired_original = bool(bilingual_bytes) and bool(original_bytes_raw)
    if not bilingual_bytes:
        bilingual_bytes = original_bytes_raw

    try:
        sanitized = sanitize_epub(bilingual_bytes)
    except EPUBStructureError as exc:
        raise HTTPException(400, detail=f"invalid bilingual EPUB: {exc}") from exc

    try:
        meta = extract_metadata(sanitized)
    except MalformedEPUBError as exc:
        raise HTTPException(400, detail=f"could not parse EPUB metadata: {exc}") from exc

    has_original = has_paired_original
    original_bytes = original_bytes_raw if has_original else None

    sha = hashlib.sha256(sanitized).hexdigest()
    final_title = (title.strip() or (meta.title or "").strip()) or "Untitled"
    final_author = (author.strip() or (meta.author or "").strip()) or None
    final_genre = genre.strip() or None

    # Resolve mode — explicit form value wins; ``"auto"`` consults metadata
    # then a body sample.
    if mode == "auto":
        body_sample = _extract_body_sample(sanitized)
        resolved_mode: Mode = detect_book_mode(meta.lang, body_sample)
    else:
        resolved_mode = mode  # type: ignore[assignment]

    # ``lang_pair`` is the legacy free-text field; honour an explicit form
    # value, otherwise derive from the resolved mode so existing callers
    # see consistent values.
    if lang_pair.strip():
        final_lang_pair = lang_pair.strip()
    elif resolved_mode == "monolingual-zh":
        final_lang_pair = "zh-zh"
    else:
        final_lang_pair = "en-zh"

    if not book_id:
        candidate = slugify(final_title)
        book_id = candidate or f"book-{sha[:12]}"

    cover_blob = _extract_cover_bytes(sanitized, meta.cover_path)

    try:
        store_book_files(book_id, bilingual=sanitized, original=original_bytes, cover=cover_blob)
    except BookStorageError as exc:
        raise HTTPException(400, detail=str(exc)) from exc

    book = Book(
        book_id=book_id,
        title=final_title,
        author=final_author,
        mode=resolved_mode,
        lang_pair=final_lang_pair,
        genre=final_genre,
        isbn=meta.isbn,
        published_year=meta.published_year,
        has_original=has_original,
        book_version_hash=sha,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    insert_book(book)
    logger.info(
        "uploaded book %s (title=%s, mode=%s, has_original=%s)",
        book_id,
        final_title,
        resolved_mode,
        has_original,
    )

    response = RedirectResponse(f"/books/{book_id}", status_code=303)
    if nakama_auth:
        response.set_cookie("nakama_auth", nakama_auth, httponly=True)
    return response


@router.get("/books/{book_id}", response_class=HTMLResponse)
async def book_reader(
    request: Request,
    book_id: str,
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse(f"/login?next=/books/{book_id}", status_code=302)
    try:
        book = get_book(book_id)
    except BookStorageError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    if book is None:
        raise HTTPException(404, detail=f"book not found: {book_id}")
    return templates.TemplateResponse(request, "book_reader.html", {"book": book})


def _ingest_status(book_id: str) -> str:
    row = (
        _get_conn()
        .execute("SELECT status FROM book_ingest_queue WHERE book_id = ?", (book_id,))
        .fetchone()
    )
    return row["status"] if row else "never"


@router.get("/api/books/{book_id}")
async def book_metadata(book_id: str):
    book = get_book(book_id)
    if book is None:
        raise HTTPException(404, detail=f"book not found: {book_id}")
    data = book.model_dump()
    data["ingest_status"] = _ingest_status(book_id)
    return data


@router.post("/api/books/{book_id}/ingest-request")
async def post_ingest_request(book_id: str):
    book = get_book(book_id)
    if book is None:
        raise HTTPException(404, detail=f"book not found: {book_id}")
    if not book.has_original:
        raise HTTPException(400, detail="book has no original EN file to ingest")
    enqueue_book(book_id)
    return {"ok": True}


@router.delete("/api/books/{book_id}/ingest-request")
async def delete_ingest_request(book_id: str):
    """Cancel a queued ingest. 409 if the book is already ingesting/done."""
    if get_book(book_id) is None:
        raise HTTPException(404, detail=f"book not found: {book_id}")
    if not cancel_book(book_id):
        raise HTTPException(409, detail="ingest cannot be cancelled (not queued)")
    return {"ok": True}


@router.get("/api/books/{book_id}/cover")
async def book_cover(book_id: str):
    if get_book(book_id) is None:
        raise HTTPException(404, detail=f"book not found: {book_id}")
    blob = read_cover_blob(book_id)
    if blob is None:
        raise HTTPException(404, detail="no cover image stored for this book")
    cover_bytes, ext = blob
    return Response(
        content=cover_bytes,
        media_type=_COVER_EXT_MEDIA_TYPES.get(ext, "application/octet-stream"),
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.delete("/api/books/{book_id}")
async def delete_book_endpoint(book_id: str, nakama_auth: str | None = Cookie(None)):
    """Remove the book entirely — DB rows (books / queue / progress), EPUB blobs,
    and the annotation file. Idempotent on partial state."""
    if not check_auth(nakama_auth):
        raise HTTPException(403, detail="not authenticated")
    if get_book(book_id) is None:
        raise HTTPException(404, detail=f"book not found: {book_id}")

    delete_queue_row(book_id)

    conn = _get_conn()
    with _progress_write_lock, conn:
        conn.execute("DELETE FROM book_progress WHERE book_id = ?", (book_id,))

    get_annotation_store().delete(book_id)
    delete_book_files(book_id)
    delete_book_row(book_id)

    logger.info("deleted book %s", book_id)
    return {"ok": True}


@router.get("/api/books/{book_id}/file")
async def book_file(
    book_id: str,
    lang: str = "bilingual",
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        raise HTTPException(403)
    if lang not in ("bilingual", "en"):
        raise HTTPException(400, detail="lang must be 'bilingual' or 'en'")
    try:
        blob = read_book_blob(book_id, lang=lang)  # type: ignore[arg-type]
    except BookStorageError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    return Response(content=blob, media_type="application/epub+zip")


@router.get("/api/books/{book_id}/annotations")
async def get_annotations(book_id: str):
    book = get_book(book_id)
    if book is None:
        raise HTTPException(404, detail=f"book not found: {book_id}")
    store = get_annotation_store()
    ann_set = store.load(book_id)
    if ann_set is None:
        ann_set = AnnotationSetV2(
            slug=book_id,
            book_id=book_id,
            book_version_hash=book.book_version_hash,
            items=[],
        )
    return ann_set.model_dump()


def _write_digest_in_background(book_id: str) -> None:
    try:
        from agents.robin.book_digest_writer import write_digest  # noqa: PLC0415

        write_digest(book_id)
    except Exception:
        logger.exception("book digest background task failed for book_id=%s", book_id)


@router.post("/api/books/{book_id}/annotations")
async def post_annotations(
    book_id: str,
    payload: dict,
    background_tasks: BackgroundTasks,
):
    """Accept either an ``AnnotationSetV2`` (legacy book reader payloads) or an
    ``AnnotationSetV3`` (post ADR-021 §1 round-trip from a Reader that already
    received a v3 GET response). Both are upgraded/normalised to v3 on disk.
    """
    from pydantic import ValidationError

    schema_version = payload.get("schema_version")
    try:
        if schema_version == 3:
            ann_set: AnnotationSetV2 | AnnotationSetV3 = AnnotationSetV3.model_validate(payload)
        else:
            ann_set = AnnotationSetV2.model_validate(payload)
    except ValidationError as exc:
        # Surface validation failures as 422 — same shape FastAPI produced when the
        # endpoint declared ``payload: AnnotationSetV2`` directly.
        raise HTTPException(422, detail=exc.errors()) from exc

    payload_book_id = ann_set.book_id if ann_set.book_id is not None else book_id
    if payload_book_id != book_id:
        raise HTTPException(422, detail="book_id in URL does not match payload")
    book = get_book(book_id)
    if book is None:
        raise HTTPException(404, detail=f"book not found: {book_id}")
    # ADR-021 §1: book Reader still posts v2 payloads; upgrade to v3 at the save
    # boundary so the on-disk store is uniformly v3 (existing BackgroundTasks digest
    # writer pattern is preserved — only ADR-021 v1's prose regenerate hook was
    # cancelled, and that hook never landed in code).
    get_annotation_store().save(upgrade_to_v3(ann_set))
    background_tasks.add_task(_write_digest_in_background, book_id)
    return {"ok": True, "digest_status": "queued"}


@router.get("/api/books/{book_id}/progress")
async def get_book_progress(book_id: str):
    if get_book(book_id) is None:
        raise HTTPException(404, detail=f"book not found: {book_id}")
    conn = _get_conn()
    row = conn.execute("SELECT * FROM book_progress WHERE book_id = ?", (book_id,)).fetchone()
    if row is None:
        return BookProgress(
            book_id=book_id,
            last_cfi=None,
            last_chapter_ref=None,
            last_spread_idx=0,
            percent=0.0,
            total_reading_seconds=0,
            updated_at=datetime.now(timezone.utc).isoformat(),
        ).model_dump()
    return dict(row)


@router.put("/api/books/{book_id}/progress")
async def put_book_progress(book_id: str, payload: BookProgress):
    if payload.book_id != book_id:
        raise HTTPException(422, detail="book_id in URL does not match payload")
    if get_book(book_id) is None:
        raise HTTPException(404, detail=f"book not found: {book_id}")
    conn = _get_conn()
    with _progress_write_lock, conn:
        conn.execute(
            """INSERT OR REPLACE INTO book_progress
               (book_id, last_cfi, last_chapter_ref, last_spread_idx,
                percent, total_reading_seconds, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                payload.book_id,
                payload.last_cfi,
                payload.last_chapter_ref,
                payload.last_spread_idx,
                payload.percent,
                payload.total_reading_seconds,
                payload.updated_at,
            ),
        )
    return {"ok": True}
