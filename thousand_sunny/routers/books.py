"""Books routes — bilingual EPUB library + foliate-js reader.

Slice 1D tracer bullet (issue #379): upload bilingual EPUB → list on /books →
read on /books/{id} via foliate-js. Read-only; annotation / progress / ingest
land in later slices.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Cookie, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from shared.annotation_store import AnnotationSetV2, get_annotation_store
from shared.book_storage import (
    BookStorageError,
    get_book,
    insert_book,
    list_books,
    read_book_blob,
    store_book_files,
)
from shared.epub_metadata import MalformedEPUBError, extract_metadata
from shared.epub_sanitizer import EPUBStructureError, sanitize_epub
from shared.log import get_logger
from shared.schemas.books import Book, BookProgress
from shared.state import _get_conn
from thousand_sunny.auth import check_auth

logger = get_logger("nakama.web.books")
router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates" / "robin")
)


@router.get("/books", response_class=HTMLResponse)
async def books_library(request: Request, nakama_auth: str | None = Cookie(None)):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/books", status_code=302)
    books = list_books()
    return templates.TemplateResponse(request, "books_library.html", {"books": books})


@router.get("/books/upload", response_class=HTMLResponse)
async def books_upload_form(request: Request, nakama_auth: str | None = Cookie(None)):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/books/upload", status_code=302)
    return templates.TemplateResponse(request, "book_upload.html", {})


@router.post("/books/upload")
async def books_upload(
    bilingual: UploadFile = File(...),
    book_id: str = Form(...),
    title: str = Form(""),
    lang_pair: str = Form("en-zh"),
    genre: str = Form(""),
    author: str = Form(""),
    original: UploadFile | None = File(None),
    nakama_auth: str | None = Cookie(None),
):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login", status_code=302)

    bilingual_bytes = await bilingual.read()
    if not bilingual_bytes:
        raise HTTPException(400, detail="bilingual EPUB is empty")

    try:
        sanitized = sanitize_epub(bilingual_bytes)
    except EPUBStructureError as exc:
        raise HTTPException(400, detail=f"invalid bilingual EPUB: {exc}") from exc

    try:
        meta = extract_metadata(sanitized)
    except MalformedEPUBError as exc:
        raise HTTPException(400, detail=f"could not parse EPUB metadata: {exc}") from exc

    original_bytes: bytes | None = None
    has_original = False
    if original is not None and original.filename:
        original_bytes = await original.read()
        if original_bytes:
            has_original = True
        else:
            original_bytes = None

    try:
        store_book_files(book_id, bilingual=sanitized, original=original_bytes)
    except BookStorageError as exc:
        raise HTTPException(400, detail=str(exc)) from exc

    final_title = (title.strip() or (meta.title or "").strip()) or book_id
    final_author = (author.strip() or (meta.author or "").strip()) or None
    final_genre = genre.strip() or None
    final_lang_pair = lang_pair.strip() or "en-zh"

    book = Book(
        book_id=book_id,
        title=final_title,
        author=final_author,
        lang_pair=final_lang_pair,
        genre=final_genre,
        isbn=meta.isbn,
        published_year=meta.published_year,
        has_original=has_original,
        book_version_hash=hashlib.sha256(sanitized).hexdigest(),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    insert_book(book)
    logger.info("uploaded book %s (title=%s, has_original=%s)", book_id, final_title, has_original)

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


@router.get("/api/books/{book_id}")
async def book_metadata(book_id: str):
    book = get_book(book_id)
    if book is None:
        raise HTTPException(404, detail=f"book not found: {book_id}")
    return book.model_dump()


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


@router.post("/api/books/{book_id}/annotations")
async def post_annotations(book_id: str, payload: AnnotationSetV2):
    if payload.book_id != book_id:
        raise HTTPException(422, detail="book_id in URL does not match payload")
    book = get_book(book_id)
    if book is None:
        raise HTTPException(404, detail=f"book not found: {book_id}")
    get_annotation_store().save(payload)
    return {"ok": True}


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
    conn.commit()
    return {"ok": True}
