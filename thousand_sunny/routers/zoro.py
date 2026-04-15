"""Zoro routes — keyword research & content intelligence."""

import asyncio

from fastapi import APIRouter, Depends, Form, HTTPException

from thousand_sunny.auth import require_auth_or_key

router = APIRouter(prefix="/zoro")


@router.post("/keyword-research")
async def zoro_keyword_research(
    topic: str = Form(...),
    content_type: str = Form("youtube"),
    en_topic: str = Form(""),
    _auth=Depends(require_auth_or_key),
):
    """Bilingual keyword research: YouTube/Trends/Reddit/Twitter + Claude synthesis."""
    if not topic.strip():
        raise HTTPException(status_code=400, detail="topic is required")
    try:
        from agents.zoro.keyword_research import research_keywords

        result = await asyncio.to_thread(
            research_keywords,
            topic.strip(),
            content_type,
            en_topic.strip() or None,
        )
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
