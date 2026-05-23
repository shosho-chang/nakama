"""Robin routes — KB ingest UI, reader, and search."""

import asyncio
import os
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
from agents.robin.ingest import IngestPipeline
from agents.robin.kb_search import search_kb
from shared.annotation_store import (
    AnnotationSet,
    AnnotationStore,
    annotation_slug,
    get_annotation_store,
    upgrade_to_v3,
)
from shared.config import get_agent_config, get_vault_path
from shared.discard_service import DiscardService
from shared.llm_context import set_current_agent
from shared.log import get_logger
from shared.reading_source_registry import InboxKey, ReadingSourceRegistry
from shared.state import is_file_read, mark_file_processed, mark_file_read
from shared.translator import translate_document
from shared.utils import extract_frontmatter, read_text, slugify
from thousand_sunny.auth import check_auth, require_auth_or_key
from thousand_sunny.helpers import safe_resolve, sse

logger = get_logger("nakama.web.robin")
# ``router`` keeps the root-prefix routes (ingest flow: ``/``, ``/start``,
# ``/processing``, etc.) that were not part of the R6 rename group.
router = APIRouter()
# ``robin_router`` hosts the R6 canonical reader endpoints under ``/robin/*``
# (9 endpoints: ``/robin/read``, ``/robin/files/*``, ``/robin/events/*``,
# ``/robin/save-annotations``, ``/robin/sync-annotations/*``,
# ``/robin/mark-read``, ``/robin/discard-info``, ``/robin/discard``,
# ``/robin/translate``). Codex audit §1: keep query-string shape —
# path-segment slug migration is a separate ADR.
robin_router = APIRouter(prefix="/robin")
# ``legacy_router`` preserves the legacy root-prefix paths as 301 (GET) /
# 308 (POST) redirects to the new ``/robin/*`` URLs.
legacy_router = APIRouter()
_TEMPLATE_ROOT = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(
    directory=[str(_TEMPLATE_ROOT / "robin"), str(_TEMPLATE_ROOT / "bridge")]
)
pipeline = IngestPipeline()


def _shosho_asset_version() -> str:
    """Return an 8-char hash of the Shosho design-system CSS files.

    Used to bust Cloudflare's /static/* edge cache when tokens.css,
    bridge.css, bridge-pages.css, reader.css or robin.css change.
    Bridge CSS is included because ``robin/index.html`` now carries
    chassis-nav (ADR-029 / #665).
    """
    import hashlib

    static_dir = Path(__file__).resolve().parent.parent / "static" / "shosho"
    h = hashlib.sha1()
    for css in (
        "tokens.css",
        "bridge.css",
        "bridge-pages.css",
        "reader.css",
        "robin.css",
        "theme.js",
    ):
        path = static_dir / css
        if path.exists():
            h.update(path.read_bytes())
    return h.hexdigest()[:8]


_SHOSHO_ASSET_VERSION = _shosho_asset_version()


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


def _inbox_rel() -> str:
    """Vault-relative inbox path from config (ADR-028: ``Inbox/web``)."""
    cfg = get_agent_config("robin")
    return cfg.get("inbox_path", "Inbox/web")


def _get_inbox() -> Path:
    return get_vault_path() / _inbox_rel()


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
            fm_source_type = ""
            fm_content_nature = ""
            if f.suffix.lower() == ".md":
                try:
                    fm, _ = extract_frontmatter(read_text(f))
                    status = str(fm.get("fulltext_status", "") or "")
                    source_label = str(fm.get("fulltext_source", "") or "")
                    title = str(fm.get("title", "") or "").strip()
                    fm_source_type = str(fm.get("source_type", "") or "").strip()
                    fm_content_nature = str(fm.get("content_nature", "") or "").strip()
                    # Obsidian Web Clipper files (Chrome plugin) drop into
                    # Inbox/web/ with their own frontmatter shape (no
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
                    "type": fm_source_type
                    or EXTENSION_TO_SOURCE_TYPE.get(f.suffix.lower(), "article"),
                    "content_nature": fm_content_nature or "popular_science",
                    "annotatable": f.suffix.lower() in (".md", ".txt"),
                    "is_read": is_file_read(f),
                    "fulltext_status": status,
                    "fulltext_source": source_label,
                }
            )
    return files


# ── Routes ────────────────────────────────────────────────────────────────────


def _render_inbox(request: Request) -> HTMLResponse:
    files = _get_inbox_files()
    return templates.TemplateResponse(
        request,
        "index.html",
        {"files": files, "asset_version": _SHOSHO_ASSET_VERSION},
    )


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, nakama_auth: str | None = Cookie(None)):
    # Per-machine landing override: ROBIN_INDEX_REDIRECT=/bridge makes / land
    # on Bridge dashboard instead of Robin Inbox (local dev where Bridge is
    # the canonical control plane). Default unset preserves Robin-as-home.
    override = os.environ.get("ROBIN_INDEX_REDIRECT")
    if override:
        return RedirectResponse(override, status_code=302)
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/", status_code=302)
    return _render_inbox(request)


@robin_router.get("", response_class=HTMLResponse)
async def robin_home(request: Request, nakama_auth: str | None = Cookie(None)):
    # Stable Robin Inbox landing at /robin, used by chassis-nav so the ROBIN
    # link still works when ROBIN_INDEX_REDIRECT is set on / above.
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/robin", status_code=302)
    return _render_inbox(request)


@robin_router.get("/read", response_class=HTMLResponse)
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

    # ADR-024 Slice 2 (#510): inbox-side slug derives from ReadingSourceRegistry
    # so the bilingual-sibling collapse rule mirrors _get_inbox_files. The
    # sources-side base (KB/Wiki/Sources/...) keeps the legacy ad-hoc derivation
    # since the registry only models BookKey + InboxKey today.
    if base == "inbox":
        rs = ReadingSourceRegistry().resolve(InboxKey(f"{_inbox_rel()}/{file}"))
        if rs is None:
            raise HTTPException(404, detail=f"找不到檔案：{file}")
        slug = rs.annotation_key
    else:
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
            "asset_version": _SHOSHO_ASSET_VERSION,
        },
    )


@robin_router.get("/files/{path:path}")
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


@robin_router.post("/save-annotations")
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
    # ADR-021 §1: persist as v3 (the Reader UI still posts the v1 shape; we upgrade
    # at the boundary so the on-disk store is uniformly v3 going forward).
    store.save(upgrade_to_v3(ann_set))
    return {"status": "ok", "unsynced_count": store.unsynced_count(ann_set.slug)}


@robin_router.post("/sync-annotations/{slug}")
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


@robin_router.post("/mark-read")
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


@robin_router.get("/discard-info")
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


@robin_router.post("/discard")
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

    response = RedirectResponse("/robin", status_code=303)
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
    'derived_from: "{inbox_rel}/{stem}.md"\n'
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

    On translator failure we do NOT write a partial bilingual file — the
    user can still read the original under the same inbox row.

    Thread-local agent attribution: FastAPI BackgroundTasks run in a
    threadpool that does NOT inherit the request handler's
    :mod:`shared.llm_context` ``_local``. Setting agent here (rather than
    in :func:`translate`) is what makes cost rows + ``api_calls.agent``
    say ``"robin"`` instead of ``"unknown"`` for translator LLM calls.
    """
    set_current_agent("robin")
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
        inbox_rel=_inbox_rel(),
    )
    bilingual_path.write_text(frontmatter + bilingual_md, encoding="utf-8")
    _flip_status_to_translated(source_path)
    logger.info("translate BG complete: %s", bilingual_path.name)


@robin_router.post("/translate")
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
       straight to the reader without scheduling a BG task — PRD
       §Pipeline / API "短路條件" / acceptance #6.
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

    The BG task writes ``Inbox/web/{stem}-bilingual.md`` and mutates the
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
        response = RedirectResponse(f"/robin/read?file={bilingual_path.name}", status_code=303)
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
    response = RedirectResponse("/robin", status_code=303)
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

    response = RedirectResponse("/robin", status_code=302)
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
        return RedirectResponse("/robin", status_code=302)

    step_labels = {
        "summarizing": "Robin 正在閱讀文件並產出摘要...",
        "planning": "Robin 正在分析概念與實體...",
        "executing": "Robin 正在寫入 Wiki 頁面...",
    }
    label = step_labels.get(sess["step"], "處理中...")
    return templates.TemplateResponse(
        request,
        "processing.html",
        {
            "session_id": robin_session,
            "label": label,
            "asset_version": _SHOSHO_ASSET_VERSION,
        },
    )


@robin_router.get("/events/{session_id}")
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
        return RedirectResponse("/robin", status_code=302)
    return templates.TemplateResponse(
        request,
        "review_summary.html",
        {
            "file_name": sess["file_name"],
            "summary": sess["summary_body"],
            "asset_version": _SHOSHO_ASSET_VERSION,
        },
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
        return RedirectResponse("/robin", status_code=302)
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
        return RedirectResponse("/robin", status_code=302)
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
            "asset_version": _SHOSHO_ASSET_VERSION,
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
        return RedirectResponse("/robin", status_code=302)

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
        return RedirectResponse("/robin", status_code=302)
    return templates.TemplateResponse(
        request,
        "done.html",
        {
            "file_name": sess["file_name"],
            "created": sess["result"].get("created", []),
            "updated": sess["result"].get("updated", []),
            "referenced": sess["result"].get("referenced", []),
            "asset_version": _SHOSHO_ASSET_VERSION,
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


# ── Legacy redirects — root-prefix → /robin/* (R6) ───────────────────────────
# Per /architecture v2 R6: the 10 reader endpoints below were moved under the
# ``/robin/*`` prefix to teach correct Robin Knowledge-tier ownership. Legacy
# paths are preserved as 301 (GET, browser-cacheable) / 308 (POST, method+body
# preserving) redirects so in-flight bookmarks, fetch() calls, and form
# replays land at the new URL without downgrading to GET. Codex audit §1
# caveat: query-string shape is preserved (no path-segment slug migration).


@legacy_router.get("/read")
async def _legacy_read_redirect(request: Request):
    # 301 + preserve full query string (``file=...&base=...``). Codex §1: do
    # NOT rewrite into a path segment — that is a separate data-model ADR.
    qs = request.url.query
    target = "/robin/read" + (f"?{qs}" if qs else "")
    return RedirectResponse(target, status_code=301)


@legacy_router.get("/files/{path:path}")
async def _legacy_files_redirect(path: str):
    return RedirectResponse(f"/robin/files/{path}", status_code=301)


@legacy_router.get("/events/{session_id}")
async def _legacy_events_redirect(session_id: str):
    # SSE: EventSource follows 301 on the initial connection, so a legacy
    # ``/events/{sid}`` URL still resolves to the live stream at
    # ``/robin/events/{sid}``. Once the redirect is consumed the stream is
    # served by the canonical handler with no buffering wrapper.
    return RedirectResponse(f"/robin/events/{session_id}", status_code=301)


@legacy_router.post("/save-annotations")
async def _legacy_save_annotations_redirect():
    # 308 preserves method+body so the JSON POST replays at the new URL.
    return RedirectResponse("/robin/save-annotations", status_code=308)


@legacy_router.post("/sync-annotations/{slug}")
async def _legacy_sync_annotations_redirect(slug: str):
    return RedirectResponse(f"/robin/sync-annotations/{slug}", status_code=308)


@legacy_router.post("/mark-read")
async def _legacy_mark_read_redirect():
    return RedirectResponse("/robin/mark-read", status_code=308)


@legacy_router.get("/discard-info")
async def _legacy_discard_info_redirect(request: Request):
    qs = request.url.query
    target = "/robin/discard-info" + (f"?{qs}" if qs else "")
    return RedirectResponse(target, status_code=301)


@legacy_router.post("/discard")
async def _legacy_discard_redirect(request: Request):
    qs = request.url.query
    target = "/robin/discard" + (f"?{qs}" if qs else "")
    return RedirectResponse(target, status_code=308)


@legacy_router.post("/translate")
async def _legacy_translate_redirect(request: Request):
    qs = request.url.query
    target = "/robin/translate" + (f"?{qs}" if qs else "")
    return RedirectResponse(target, status_code=308)


