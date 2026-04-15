"""Brook routes — multi-turn article composition assistant."""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from agents.robin.kb_search import search_kb
from shared.config import get_vault_path
from shared.log import get_logger
from thousand_sunny.auth import check_auth, require_auth_or_key

logger = get_logger("nakama.web.brook")
router = APIRouter(prefix="/brook")
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates" / "brook")
)


@router.get("/chat", response_class=HTMLResponse)
async def brook_chat_page(
    request: Request,
    robin_auth: str | None = Cookie(None),
):
    """Brook 聊天頁面。"""
    if not check_auth(robin_auth):
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(request, "brook_chat.html", {})


@router.post("/start")
async def brook_start(
    topic: str = Form(...),
    kb_query: str | None = Form(None),
    _auth=Depends(require_auth_or_key),
):
    """開始新的 Brook 對話。"""
    if not topic.strip():
        raise HTTPException(status_code=400, detail="topic is required")

    kb_context = ""
    if kb_query and kb_query.strip():
        try:
            results = await asyncio.to_thread(search_kb, kb_query.strip(), get_vault_path())
            if results:
                kb_context = "\n".join(
                    f"- **{r['title']}**（{r['type']}）：{r.get('relevance_reason', '')}"
                    for r in results
                )
        except Exception:
            pass

    try:
        from agents.brook.compose import start_conversation

        result = await asyncio.to_thread(start_conversation, topic.strip(), kb_context)
        return result
    except Exception as e:
        logger.error(f"Brook start error: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/message")
async def brook_message(
    conversation_id: str = Form(...),
    message: str = Form(...),
    _auth=Depends(require_auth_or_key),
):
    """在既有 Brook 對話中傳送訊息。"""
    if not message.strip():
        raise HTTPException(status_code=400, detail="message is required")

    try:
        from agents.brook.compose import send_message

        result = await asyncio.to_thread(send_message, conversation_id, message.strip())
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Brook message error: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/conversations")
async def brook_list_conversations(
    _auth=Depends(require_auth_or_key),
):
    """列出最近的 Brook 對話。"""
    from agents.brook.compose import get_conversations

    result = await asyncio.to_thread(get_conversations)
    return {"conversations": result}


@router.get("/conversation/{conversation_id}")
async def brook_get_conversation(
    conversation_id: str,
    _auth=Depends(require_auth_or_key),
):
    """載入完整 Brook 對話。"""
    from agents.brook.compose import get_conversation

    result = await asyncio.to_thread(get_conversation, conversation_id)
    if not result:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return result


@router.post("/export/{conversation_id}")
async def brook_export(
    conversation_id: str,
    _auth=Depends(require_auth_or_key),
):
    """匯出 Brook 對話為文章初稿。"""
    try:
        from agents.brook.compose import export_draft

        draft = await asyncio.to_thread(export_draft, conversation_id)
        return {"draft": draft}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Brook export error: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=str(e))
