"""Robin routes — KB ingest UI, reader, and search."""

import asyncio
import platform
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
from shared.config import get_agent_config, get_vault_path
from shared.log import get_logger
from shared.state import is_file_read, mark_file_processed, mark_file_read
from shared.utils import extract_frontmatter, read_text, slugify
from thousand_sunny.auth import WEB_PASSWORD, check_auth, make_token, require_auth_or_key
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


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login(request: Request, password: str = Form(...)):
    if not WEB_PASSWORD or password == WEB_PASSWORD:
        response = RedirectResponse("/", status_code=302)
        response.set_cookie("robin_auth", make_token(password), httponly=True)
        return response
    return templates.TemplateResponse(request, "login.html", {"error": "密碼錯誤"}, status_code=401)


@router.post("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("robin_auth")
    return response


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, robin_auth: str | None = Cookie(None)):
    if not check_auth(robin_auth):
        return RedirectResponse("/login", status_code=302)
    files = _get_inbox_files()
    return templates.TemplateResponse(request, "index.html", {"files": files})


@router.get("/read", response_class=HTMLResponse)
async def read_source(request: Request, file: str, robin_auth: str | None = Cookie(None)):
    if not check_auth(robin_auth):
        return RedirectResponse("/login", status_code=302)
    inbox = _get_inbox()
    file_path = safe_resolve(inbox, file)
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

    return templates.TemplateResponse(
        request,
        "reader.html",
        {
            "filename": file,
            "content": body,
            "frontmatter": frontmatter,
            "frontmatter_raw": frontmatter_raw,
            "source_type": EXTENSION_TO_SOURCE_TYPE.get(file_path.suffix.lower(), "article"),
            "is_read": is_file_read(file_path),
        },
    )


@router.get("/files/{path:path}")
async def serve_vault_file(path: str, robin_auth: str | None = Cookie(None)):
    """提供 vault 中的圖片給 reader 顯示。"""
    if not check_auth(robin_auth):
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
    filename: str = Form(...),
    content: str = Form(...),
    robin_auth: str | None = Cookie(None),
):
    if not check_auth(robin_auth):
        raise HTTPException(403)
    inbox = _get_inbox()
    file_path = safe_resolve(inbox, filename)
    if not file_path.exists():
        raise HTTPException(404, detail=f"找不到檔案：{filename}")
    file_path.write_text(content, encoding="utf-8")
    return {"status": "ok"}


@router.post("/mark-read")
async def mark_read(
    filename: str = Form(...),
    robin_auth: str | None = Cookie(None),
):
    if not check_auth(robin_auth):
        raise HTTPException(403)
    inbox = _get_inbox()
    file_path = safe_resolve(inbox, filename)
    if not file_path.exists():
        raise HTTPException(404, detail=f"找不到檔案：{filename}")
    mark_file_read(file_path)
    return {"status": "ok"}


@router.post("/start")
async def start(
    filename: str = Form(...),
    source_type: str = Form("article"),
    content_nature: str = Form("popular_science"),
    robin_auth: str | None = Cookie(None),
):
    if not check_auth(robin_auth):
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
        plan={"create": [], "update": []},
        result={"created": [], "updated": []},
        error="",
    )

    response = RedirectResponse("/processing", status_code=302)
    response.set_cookie("robin_session", sid, httponly=True)
    if robin_auth:
        response.set_cookie("robin_auth", robin_auth, httponly=True)
    return response


@router.post("/cancel")
async def cancel(
    robin_session: str | None = Cookie(None),
    robin_auth: str | None = Cookie(None),
):
    if not check_auth(robin_auth):
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
    robin_auth: str | None = Cookie(None),
):
    if not check_auth(robin_auth):
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
async def events(session_id: str, robin_auth: str | None = Cookie(None)):
    if not check_auth(robin_auth):
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
                sess["plan"] = plan or {"create": [], "update": []}
                sess["step"] = "awaiting_approval"
                yield sse("done", {"redirect": "/review-plan"})

            elif step == "executing":
                creates = sess["plan"].get("create", [])
                updates = sess["plan"].get("update", [])
                total = len(creates) + len(updates)
                yield sse("status", {"msg": f"Robin 正在寫入 {total} 個 Wiki 頁面..."})

                await asyncio.to_thread(pipeline._execute_plan, sess["plan"], sess["summary_path"])

                title = sess.get("_title", Path(sess["raw_path"]).stem)
                slug = slugify(title)
                await asyncio.to_thread(pipeline._update_index, title, slug, sess["source_type"])

                mark_file_processed(Path(sess["file_path"]), "robin")
                Path(sess["file_path"]).unlink(missing_ok=True)

                sess["result"] = {
                    "created": [item["title"] for item in creates],
                    "updated": [item["title"] for item in updates],
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
    robin_auth: str | None = Cookie(None),
):
    if not check_auth(robin_auth):
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
    robin_auth: str | None = Cookie(None),
):
    if not check_auth(robin_auth):
        return RedirectResponse("/login", status_code=302)
    sess = _get_session(robin_session)
    if not sess:
        return RedirectResponse("/", status_code=302)
    sess["user_guidance"] = guidance.strip()
    sess["step"] = "planning"
    response = RedirectResponse("/processing", status_code=302)
    if robin_auth:
        response.set_cookie("robin_auth", robin_auth, httponly=True)
    return response


@router.get("/review-plan", response_class=HTMLResponse)
async def review_plan(
    request: Request,
    robin_session: str | None = Cookie(None),
    robin_auth: str | None = Cookie(None),
):
    if not check_auth(robin_auth):
        return RedirectResponse("/login", status_code=302)
    sess = _get_session(robin_session)
    if not sess or sess["step"] != "awaiting_approval":
        return RedirectResponse("/", status_code=302)
    plan = sess.get("plan", {"create": [], "update": []})
    return templates.TemplateResponse(
        request,
        "review_plan.html",
        {
            "file_name": sess["file_name"],
            "creates": enumerate(plan.get("create", [])),
            "updates": enumerate(plan.get("update", [])),
            "creates_list": plan.get("create", []),
            "updates_list": plan.get("update", []),
        },
    )


@router.post("/execute")
async def execute(
    request: Request,
    robin_session: str | None = Cookie(None),
    robin_auth: str | None = Cookie(None),
):
    if not check_auth(robin_auth):
        return RedirectResponse("/login", status_code=302)
    sess = _get_session(robin_session)
    if not sess:
        return RedirectResponse("/", status_code=302)

    form = await request.form()
    plan = sess.get("plan", {"create": [], "update": []})
    all_creates = plan.get("create", [])
    all_updates = plan.get("update", [])

    selected_creates = [
        all_creates[int(i)]
        for i in form.getlist("create")
        if i.isdigit() and int(i) < len(all_creates)
    ]
    selected_updates = [
        all_updates[int(i)]
        for i in form.getlist("update")
        if i.isdigit() and int(i) < len(all_updates)
    ]

    sess["plan"] = {"create": selected_creates, "update": selected_updates}
    sess["step"] = "executing"

    response = RedirectResponse("/processing", status_code=302)
    if robin_auth:
        response.set_cookie("robin_auth", robin_auth, httponly=True)
    return response


@router.get("/done", response_class=HTMLResponse)
async def done(
    request: Request,
    robin_session: str | None = Cookie(None),
    robin_auth: str | None = Cookie(None),
):
    if not check_auth(robin_auth):
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
