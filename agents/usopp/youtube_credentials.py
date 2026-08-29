"""Controlled YouTube credential loading for Stage 6 workers."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable


class YouTubeCredentialError(RuntimeError):
    """Secret-free credential lifecycle failure requiring operator attention."""


def _is_invalid_grant(exc: Exception) -> bool:
    for value in exc.args:
        if isinstance(value, dict) and value.get("error") == "invalid_grant":
            return True
        if "invalid_grant" in str(value).lower():
            return True
    return False


def _persist_atomically(token_path: Path, serialized: str) -> None:
    parsed = json.loads(serialized)
    if not isinstance(parsed, dict):
        raise ValueError("authorized-user credentials must serialize as an object")

    descriptor, temporary_name = tempfile.mkstemp(
        dir=token_path.parent,
        prefix=f".{token_path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as temporary:
            temporary.write(serialized)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, token_path)
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


def load_youtube_client(
    token_path: Path,
    *,
    credentials_loader: Callable[[str], Any] | None = None,
    request_factory: Callable[[], Any] | None = None,
    client_builder: Callable[..., Any] | None = None,
) -> Any:
    """Load credentials and return an authenticated YouTube client."""

    if credentials_loader is None:
        from google.oauth2.credentials import Credentials

        credentials_loader = Credentials.from_authorized_user_file
    if client_builder is None:
        from googleapiclient.discovery import build

        client_builder = build

    if not token_path.is_file():
        raise YouTubeCredentialError(
            "YouTube credentials are unavailable; run python scripts/youtube_auth.py"
        )
    load_error: str | None = None
    try:
        credentials = credentials_loader(str(token_path))
    except ValueError:
        load_error = "YouTube credentials are malformed; run python scripts/youtube_auth.py"
    except Exception:
        load_error = "YouTube credentials could not be loaded temporarily; retry the Stage 6 worker"
    if load_error is not None:
        raise YouTubeCredentialError(load_error)
    if not credentials.valid:
        if credentials.expired and not credentials.refresh_token:
            raise YouTubeCredentialError(
                "YouTube refresh credential is missing; run python scripts/youtube_auth.py"
            )
        if not credentials.expired:
            raise YouTubeCredentialError(
                "YouTube credentials require reauthorization; run python scripts/youtube_auth.py"
            )
        if request_factory is None:
            from google.auth.transport.requests import Request

            request_factory = Request
        refresh_error: str | None = None
        try:
            credentials.refresh(request_factory())
        except Exception as exc:
            if _is_invalid_grant(exc):
                refresh_error = (
                    "YouTube refresh credential was rejected; run python scripts/youtube_auth.py"
                )
            else:
                refresh_error = (
                    "YouTube credential refresh failed temporarily; retry the Stage 6 worker"
                )
        if refresh_error is not None:
            raise YouTubeCredentialError(refresh_error)
        if not credentials.valid:
            raise YouTubeCredentialError(
                "YouTube credential refresh failed; run python scripts/youtube_auth.py"
            )
        persistence_failed = False
        try:
            _persist_atomically(token_path, credentials.to_json())
        except Exception:
            persistence_failed = True
        if persistence_failed:
            raise YouTubeCredentialError(
                "Refreshed YouTube credentials could not be saved; retry the Stage 6 worker"
            )
    try:
        return client_builder("youtube", "v3", credentials=credentials)
    except Exception:
        pass
    raise YouTubeCredentialError(
        "YouTube client initialization failed temporarily; retry the Stage 6 worker"
    )
