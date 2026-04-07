"""Robin Web UI — FastAPI app for interactive ingest."""

import asyncio
import hashlib
import hmac
import json
import os
import shutil
import time
import uuid
from pathlib import Path

from fastapi import Cookie, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from agents.robin.agent import EXTENSION_TO_RAW_DIR, EXTENSION_TO_SOURCE_TYPE
from agents.robin.ingest import IngestPipeline
from shared.config import get_agent_config, get_vault_path
from shared.log import get_logger
from shared.obsidian_writer import list_files
from shared.state import mark_file_processed
from shared.utils import extract_frontmatter, read_text, slugify

logger = get_logger("nakama.robin.web")

# ── App setup ──────────────────────────────────────────────────────────────────

app = FastAPI(docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
pipeline = IngestPipeline()

# ── Auth ───────────────────────────────────────────────────────────────────────

WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "")
WEB_SECRET = os.environ.get("WEB_SECRET", "nakama-default-secret")


def _make_token(password: str) -> str:
    return hmac.new(WEB_SECRET.encode(), password.encode(), hashlib.sha256).hexdigest()


def _check_auth(auth_cookie: str | None) -> bool:
    if not WEB_PASSWORD:
        return True  # 未設定密碼時跳過驗證（本機開發用）
    return bool(auth_cookie and auth_cookie == _make_token(WEB_PASSWORD))


def _require_auth(robin_auth: str | None = Cookie(None)) -> str | None:
    if not _check_auth(robin_auth):
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return robin_auth


# ── Session store ──────────────────────────────────────────────────────────────

sessions: dict[str, dict] = {}
SESSION_TTL = 7200  # 2 hours


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


# ── SSE helper ─────────────────────────────────────────────────────────────────

def _sse(event: str, data: dict | str) -> str:
    payload = json.dumps(data) if isinstance(data, dict) else data
    return f"event: {event}\ndata: {payload}\n\n"


# ── Vault helpers ──────────────────────────────────────────────────────────────

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
            files.append({
                "name": f.name,
                "size": f"{size_kb} KB" if size_kb >= 1 else f"{f.stat().st_size} B",
                "type": EXTENSION_TO_SOURCE_TYPE.get(f.suffix.lower(), "article"),
            })
    return files


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
async def login(password: str = Form(...)):
    if not WEB_PASSWORD or password == WEB_PASSWORD:
        response = RedirectResponse("/", status_code=302)
        response.set_cookie("robin_auth", _make_token(password), httponly=True)
        return response
    return templates.TemplateResponse(
        "login.html",
        {"request": {}, "error": "密碼錯誤"},
        status_code=401,
    )


@app.post("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("robin_auth")
    return response


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, robin_auth: str | None = Cookie(None)):
    if not _check_auth(robin_auth):
        return RedirectResponse("/login", status_code=302)
    files = _get_inbox_files()
    return templates.TemplateResponse("index.html", {"request": request, "files": files})


@app.post("/start")
async def start(filename: str = Form(...), robin_auth: str | None = Cookie(None)):
    if not _check_auth(robin_auth):
        return RedirectResponse("/login", status_code=302)

    inbox = _get_inbox()
    file_path = inbox / filename
    if not file_path.exists():
        raise HTTPException(404, detail=f"找不到檔案：{filename}")

    suffix = file_path.suffix.lower()
    raw_dir = EXTENSION_TO_RAW_DIR.get(suffix, "Articles")
    source_type = EXTENSION_TO_SOURCE_TYPE.get(suffix, "article")

    raw_dest = get_vault_path() / "KB" / "Raw" / raw_dir / filename
    raw_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(file_path, raw_dest)

    sid = _new_session(
        step="summarizing",
        file_name=filename,
        file_path=str(file_path),
        raw_path=str(raw_dest),
        source_type=source_type,
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


@app.get("/processing", response_class=HTMLResponse)
async def processing(request: Request, robin_session: str | None = Cookie(None), robin_auth: str | None = Cookie(None)):
    if not _check_auth(robin_auth):
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
    return templates.TemplateResponse("processing.html", {
        "request": request,
        "session_id": robin_session,
        "label": label,
    })


@app.get("/events/{session_id}")
async def events(session_id: str, robin_auth: str | None = Cookie(None)):
    if not _check_auth(robin_auth):
        raise HTTPException(403)

    sess = _get_session(session_id)
    if not sess:
        raise HTTPException(404)

    async def generate():
        try:
            step = sess["step"]

            if step == "summarizing":
                yield _sse("status", {"msg": "Robin 正在閱讀文件..."})

                content = read_text(Path(sess["raw_path"]))
                title = Path(sess["raw_path"]).stem
                author = ""
                if Path(sess["raw_path"]).suffix == ".md":
                    fm, body = extract_frontmatter(content)
                    title = fm.get("title", title)
                    author = fm.get("author", "")
                    content = body if body else content

                sess["_title"] = title
                sess["_author"] = author
                sess["_content"] = content

                yield _sse("status", {"msg": "正在呼叫 Claude 產出摘要（約 10-30 秒）..."})
                summary = await asyncio.to_thread(
                    pipeline._generate_summary,
                    content=content,
                    title=title,
                    author=author,
                    source_type=sess["source_type"],
                )
                sess["summary_body"] = summary

                from datetime import date
                from shared.obsidian_writer import write_page
                slug = slugify(title)
                summary_path = f"KB/Wiki/Sources/{slug}.md"
                raw_relative = str(Path(sess["raw_path"]).relative_to(get_vault_path().parent.parent))

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
                        "author": author,
                        "confidence": "medium",
                        "tags": [],
                        "related_pages": [],
                    },
                    summary,
                )
                sess["summary_path"] = summary_path
                sess["step"] = "awaiting_guidance"
                yield _sse("done", {"redirect": "/review-summary"})

            elif step == "planning":
                yield _sse("status", {"msg": "Robin 正在分析需要建立哪些概念頁面..."})
                yield _sse("status", {"msg": "正在呼叫 Claude（約 10-20 秒）..."})

                plan = await asyncio.to_thread(
                    pipeline._get_concept_plan,
                    sess["summary_body"],
                    sess["summary_path"],
                    sess["user_guidance"],
                )
                sess["plan"] = plan or {"create": [], "update": []}
                sess["step"] = "awaiting_approval"
                yield _sse("done", {"redirect": "/review-plan"})

            elif step == "executing":
                creates = sess["plan"].get("create", [])
                updates = sess["plan"].get("update", [])
                total = len(creates) + len(updates)
                yield _sse("status", {"msg": f"Robin 正在寫入 {total} 個 Wiki 頁面..."})

                await asyncio.to_thread(
                    pipeline._execute_plan,
                    sess["plan"],
                    sess["summary_path"],
                )

                title = sess.get("_title", Path(sess["raw_path"]).stem)
                slug = slugify(title)
                await asyncio.to_thread(
                    pipeline._update_index,
                    title,
                    slug,
                    sess["source_type"],
                )

                mark_file_processed(Path(sess["file_path"]), "robin")
                Path(sess["file_path"]).unlink(missing_ok=True)

                sess["result"] = {
                    "created": [item["title"] for item in creates],
                    "updated": [item["title"] for item in updates],
                }
                sess["step"] = "done"
                yield _sse("done", {"redirect": "/done"})

            elif step in ("awaiting_guidance", "awaiting_approval", "done"):
                # 如果 SSE 被重新連線但已完成，直接跳轉
                redirect_map = {
                    "awaiting_guidance": "/review-summary",
                    "awaiting_approval": "/review-plan",
                    "done": "/done",
                }
                yield _sse("done", {"redirect": redirect_map[step]})

            else:
                yield _sse("error", {"msg": f"未知狀態：{step}"})

        except Exception as e:
            logger.error(f"SSE error: {e}", exc_info=True)
            sess["step"] = "error"
            sess["error"] = str(e)
            yield _sse("error", {"msg": str(e)})

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/review-summary", response_class=HTMLResponse)
async def review_summary(request: Request, robin_session: str | None = Cookie(None), robin_auth: str | None = Cookie(None)):
    if not _check_auth(robin_auth):
        return RedirectResponse("/login", status_code=302)
    sess = _get_session(robin_session)
    if not sess or sess["step"] != "awaiting_guidance":
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("review_summary.html", {
        "request": request,
        "file_name": sess["file_name"],
        "summary": sess["summary_body"],
    })


@app.post("/submit-guidance")
async def submit_guidance(guidance: str = Form(default=""), robin_session: str | None = Cookie(None), robin_auth: str | None = Cookie(None)):
    if not _check_auth(robin_auth):
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


@app.get("/review-plan", response_class=HTMLResponse)
async def review_plan(request: Request, robin_session: str | None = Cookie(None), robin_auth: str | None = Cookie(None)):
    if not _check_auth(robin_auth):
        return RedirectResponse("/login", status_code=302)
    sess = _get_session(robin_session)
    if not sess or sess["step"] != "awaiting_approval":
        return RedirectResponse("/", status_code=302)
    plan = sess.get("plan", {"create": [], "update": []})
    return templates.TemplateResponse("review_plan.html", {
        "request": request,
        "file_name": sess["file_name"],
        "creates": enumerate(plan.get("create", [])),
        "updates": enumerate(plan.get("update", [])),
        "creates_list": plan.get("create", []),
        "updates_list": plan.get("update", []),
    })


@app.post("/execute")
async def execute(
    request: Request,
    robin_session: str | None = Cookie(None),
    robin_auth: str | None = Cookie(None),
):
    if not _check_auth(robin_auth):
        return RedirectResponse("/login", status_code=302)
    sess = _get_session(robin_session)
    if not sess:
        return RedirectResponse("/", status_code=302)

    form = await request.form()
    plan = sess.get("plan", {"create": [], "update": []})
    all_creates = plan.get("create", [])
    all_updates = plan.get("update", [])

    selected_creates = [
        all_creates[int(i)] for i in form.getlist("create")
        if i.isdigit() and int(i) < len(all_creates)
    ]
    selected_updates = [
        all_updates[int(i)] for i in form.getlist("update")
        if i.isdigit() and int(i) < len(all_updates)
    ]

    sess["plan"] = {"create": selected_creates, "update": selected_updates}
    sess["step"] = "executing"

    response = RedirectResponse("/processing", status_code=302)
    if robin_auth:
        response.set_cookie("robin_auth", robin_auth, httponly=True)
    return response


@app.get("/done", response_class=HTMLResponse)
async def done(request: Request, robin_session: str | None = Cookie(None), robin_auth: str | None = Cookie(None)):
    if not _check_auth(robin_auth):
        return RedirectResponse("/login", status_code=302)
    sess = _get_session(robin_session)
    if not sess or sess["step"] != "done":
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("done.html", {
        "request": request,
        "file_name": sess["file_name"],
        "created": sess["result"].get("created", []),
        "updated": sess["result"].get("updated", []),
    })
