"""Shared helpers — path safety + SSE formatting."""

import json
from pathlib import Path

from fastapi import HTTPException


def safe_resolve(base: Path, user_input: str) -> Path:
    """Resolve user-supplied filename against a base directory, rejecting traversal.

    Raises HTTPException(403) if the resolved path escapes the base directory.
    """
    resolved = (base / user_input).resolve()
    if not resolved.is_relative_to(base.resolve()):
        raise HTTPException(403, detail="Access denied: path traversal detected")
    return resolved


def sse(event: str, data: dict | str) -> str:
    """Format a Server-Sent Event message."""
    payload = json.dumps(data) if isinstance(data, dict) else data
    return f"event: {event}\ndata: {payload}\n\n"
