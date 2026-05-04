"""Robin routes — KB ingest UI, reader, and search."""

import asyncio
import platform
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request
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
)
from shared.config import get_agent_config, get_vault_path
from shared.log import get_logger
from shared.state import is_file_read, mark_file_processed, mark_file_read
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


def _get_inbox_files() -> list[dict]:
    inbox = _get_inbox()
    if not inbox.exists():
        return []
    supported = set(EXTENSION_TO_RAW_DIR.keys())
    files = []
    for f in sorted(inbox.iterdir()):
        if f.is_file() and f.suffix.lower() in supported:
            size_kb = f.stat().st_size // 1024
            files.append(
                {
                    "name": f.name,
                    "size": f"{size_kb} KB" if size_kb >= 1 else f"{f.stat().st_size} B",
                    "type": EXTENSION_TO_SOURCE_TYPE.get(f.suffix.lower(), "article"),
                    "annotatable": f.suffix.lower() in (".md", ".txt"),
                    "is_read": is_file_read(f),
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
    return {"status": "ok"}


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


@router.post("/scrape-translate")
async def scrape_translate(
    url: str = Form(...),
    source_type: str = Form("article"),
    content_nature: str = Form("popular_science"),
    nakama_auth: str | None = Cookie(None),
):
    """從 URL 抓取網頁並翻譯成雙語 Markdown，存入 inbox。"""
    if not check_auth(nakama_auth):
        return RedirectResponse("/login", status_code=302)

    _VALID_SOURCE_TYPES = {"article", "paper", "book", "video", "podcast"}
    _VALID_CONTENT_NATURES = {
        "popular_science",
        "research",
        "textbook",
        "clinical_protocol",
        "narrative",
        "commentary",
    }
    source_type = source_type if source_type in _VALID_SOURCE_TYPES else "article"
    content_nature = (
        content_nature if content_nature in _VALID_CONTENT_NATURES else "popular_science"
    )  # noqa: E501

    from shared.translator import translate_document
    from shared.web_scraper import scrape_url

    try:
        raw_text = await asyncio.to_thread(scrape_url, url)
    except RuntimeError as e:
        raise HTTPException(422, detail=f"無法擷取頁面：{e}")

    try:
        bilingual_md = await asyncio.to_thread(translate_document, raw_text)
    except Exception as e:
        logger.error(f"翻譯失敗：{e}")
        bilingual_md = raw_text  # 翻譯失敗時保留原文

    # 以 URL slug 命名文件
    from urllib.parse import urlparse

    parsed = urlparse(url)
    slug = slugify(parsed.netloc + parsed.path)[:60] or "scraped"
    filename = f"{slug}.md"
    inbox = _get_inbox()
    inbox.mkdir(parents=True, exist_ok=True)
    dest = inbox / filename
    # 避免覆蓋現有檔案
    counter = 1
    while dest.exists():
        dest = inbox / f"{slug}-{counter}.md"
        counter += 1
    filename = dest.name

    # 清理換行符防止 YAML 注入；source_type/content_nature 已通過 allowlist 驗證
    safe_title = f"{parsed.netloc}{parsed.path}".replace("\n", "").replace("\r", "")
    safe_url = url.replace("\n", "").replace("\r", "")
    frontmatter = (
        "---\n"
        f'title: "{safe_title}"\n'
        f'source: "{safe_url}"\n'
        f"source_type: {source_type}\n"
        f"content_nature: {content_nature}\n"
        "bilingual: true\n"
        "---\n\n"
    )
    dest.write_text(frontmatter + bilingual_md, encoding="utf-8")
    logger.info(f"scrape-translate 完成：{filename}")

    response = RedirectResponse(f"/read?file={filename}", status_code=303)
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

    from shared.pdf_parser import parse_pdf
    from shared.translator import translate_document

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
