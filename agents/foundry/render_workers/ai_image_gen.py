"""AI image-gen wrapper — GPT Image via OpenAI Images.generate.

Why ``images.generate`` (not ``images.edit``):
  We tried ``edit`` 2026-05-27 to feed the host cutout as a reference for
  identity preservation. GPT Image 1's edit endpoint does NOT preserve face
  identity — it treats the reference as compositional/style context and
  generates a fresh person. Bad outcome for a personal-brand channel.

  Architecture pivoted to 3-layer compositing (2026-05-28):
    Layer 1 (this module): AI generates EMPTY environmental background only
                           — no people, no text. Pure backdrop.
    Layer 2 (hyperframes):  Real host cutout PNG composited on top via CSS
                           (face is 100% the actual host).
    Layer 3 (hyperframes):  Title + accent decoration via CSS.

  The reference cutout is no longer fed to OpenAI — it stays in the CSS layer
  where it can't get hallucinated away.

Default model is ``gpt-image-2``. Override via env var
``THUMBNAIL_AI_MODEL`` when comparing models.

Approximate output cost per image at landscape 1536x1024:
  gpt-image-2:
    - low    ~$0.005
    - medium ~$0.041
    - high   ~$0.165
  gpt-image-1:
    - low    ~$0.016
    - medium ~$0.063
    - high   ~$0.250

We default to ``high`` because thumbnails are the highest-leverage visual
asset per video — paying 17¢ to match Ali Abdaal / Hormozi production
quality is dramatically cheaper than the alternative (5+ days of designer
work). Override via env var ``THUMBNAIL_AI_QUALITY``.

Flow:
  build_prompt() → images.edit(image=<cutout PNG>, prompt=...) → save PNG
  to ``out_png``. Returns the saved Path.

Failure modes are surfaced as ``AIImageGenError`` — caller decides whether to
fall back to the CSS-gradient render or hard-fail.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from pathlib import Path

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

logger = logging.getLogger(__name__)


# Per OpenAI 2026 pricing page, landscape 1536x1024 output. Used for rough
# manifest estimates only; actual billing is from OpenAI usage and reference
# image input tokens.
_COST_USD_PER_IMAGE = {
    "gpt-image-2": {
        "low": 0.012,
        "medium": 0.047,
        "high": 0.186,
    },
    "gpt-image-1": {
        "low": 0.016,
        "medium": 0.063,
        "high": 0.250,
    },
}

# Supported by GPT Image models. gpt-image-2 supports this popular landscape
# size and many additional sizes.
_LANDSCAPE_SIZE = "1536x1024"
_DEFAULT_MODEL = "gpt-image-2"
_DEFAULT_QUALITY = "high"
_KNOWN_QUALITIES = {"low", "medium", "high", "auto"}

_RETRYABLE = (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError)


class AIImageGenError(Exception):
    """Raised when the GPT Image call cannot produce a usable image.

    The router treats this as a soft failure → render endpoint logs + falls
    back to the gradient/CSS-only path so the user still gets *something*.
    """


_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    """Lazy singleton — avoids constructing client at import time when the
    API key may not be in env yet (e.g. test runners without .env loaded)."""
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise AIImageGenError(
                "OPENAI_API_KEY not set in environment — cannot call GPT Image. "
                "Add to .env or run with `OPENAI_API_KEY=...` exported."
            )
        _client = AsyncOpenAI(api_key=api_key)
    return _client


def _quality_from_env(override: str | None) -> str:
    """Resolve quality: explicit arg > env > default ``high``."""
    if override:
        q = override.lower()
    else:
        q = (os.environ.get("THUMBNAIL_AI_QUALITY") or _DEFAULT_QUALITY).lower()
    if q not in _KNOWN_QUALITIES:
        logger.warning(
            "unknown THUMBNAIL_AI_QUALITY=%r, falling back to 'high'", q
        )
        q = _DEFAULT_QUALITY
    return q


def _model_from_env(override: str | None) -> str:
    """Resolve model: explicit arg > env > default ``gpt-image-2``."""
    return override or os.environ.get("THUMBNAIL_AI_MODEL") or _DEFAULT_MODEL


def _estimated_cost(model: str, quality: str) -> float | None:
    if quality == "auto":
        return None
    return _COST_USD_PER_IMAGE.get(model, {}).get(quality)


async def generate_thumbnail_bg(
    *,
    prompt: str,
    out_png: Path,
    quality: str | None = None,
    model: str | None = None,
    timeout: float = 180.0,
    max_attempts: int = 3,
    # Accepted for caller-compat but not sent to the API — see module docstring
    reference_cutout: Path | None = None,  # noqa: ARG001
) -> dict:
    """Generate an EMPTY background plate via GPT Image ``images.generate``.

    The output is a 1536x1024 PNG with NO people / NO text — caller composites
    the real host cutout + title text on top via CSS.

    Args:
        prompt: Full English prompt from a ``ThumbnailTemplate``. Must instruct
            the model to render NO people / NO text.
        out_png: Where to write the PNG.
        quality: ``low`` / ``medium`` / ``high``. Defaults from env then ``high``.
        model: OpenAI image model id. Defaults from env then ``gpt-image-2``.
        timeout: Per-request timeout in seconds.
        max_attempts: Total tries including retries on transient errors.
        reference_cutout: IGNORED (compat shim for old callers — see docstring
            at module level for the rationale).

    Returns:
        dict with run metadata for the manifest entry.

    Raises:
        AIImageGenError: API failed after all retries, malformed response.
    """
    q = _quality_from_env(quality)
    resolved_model = _model_from_env(model)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    client = _get_client()

    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            logger.info(
                "ai-image-gen start: model=%s quality=%s attempt=%d (bg-only)",
                resolved_model, q, attempt,
            )
            response = await asyncio.wait_for(
                client.images.generate(
                    model=resolved_model,
                    prompt=prompt,
                    size=_LANDSCAPE_SIZE,
                    quality=q,
                    n=1,
                ),
                timeout=timeout,
            )
            break
        except _RETRYABLE as exc:
            last_exc = exc
            wait = 2 ** (attempt - 1)
            logger.warning(
                "ai-image-gen retryable error (attempt %d/%d): %s — retrying in %ds",
                attempt, max_attempts, exc, wait,
            )
            if attempt < max_attempts:
                await asyncio.sleep(wait)
                continue
            raise AIImageGenError(
                f"GPT Image retryable error after {max_attempts} attempts: {exc}"
            ) from exc
        except BadRequestError as exc:
            # Non-retryable — prompt rejected, file format issue, etc.
            raise AIImageGenError(f"GPT Image rejected request: {exc}") from exc
        except asyncio.TimeoutError as exc:
            last_exc = exc
            logger.warning(
                "ai-image-gen timeout after %.0fs (attempt %d/%d)",
                timeout, attempt, max_attempts,
            )
            if attempt < max_attempts:
                continue
            raise AIImageGenError(
                f"GPT Image timed out after {timeout}s (×{max_attempts})"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise AIImageGenError(f"GPT Image unexpected error: {exc}") from exc
    else:
        # Defensive — should be unreachable since the for-else only triggers
        # if loop exits without break, but we raise on every non-success above.
        raise AIImageGenError(
            f"GPT Image failed after {max_attempts} attempts: {last_exc}"
        )

    # Pull the base64 PNG and write to disk.
    try:
        b64_payload = response.data[0].b64_json
    except (AttributeError, IndexError, TypeError) as exc:
        raise AIImageGenError(
            f"GPT Image returned unexpected shape: {response!r}"
        ) from exc

    if not b64_payload:
        raise AIImageGenError("GPT Image returned empty b64_json payload")

    out_png.write_bytes(base64.b64decode(b64_payload))
    estimated_cost = _estimated_cost(resolved_model, q)
    logger.info(
        "ai-image-gen done: out=%s model=%s quality=%s estimated_cost=%s",
        out_png, resolved_model, q, estimated_cost,
    )

    return {
        "model": resolved_model,
        "quality": q,
        "size": _LANDSCAPE_SIZE,
        "estimated_cost_usd": estimated_cost,
        "out_png": str(out_png.resolve()),
        "attempts": attempt,
    }


async def generate_thumbnail_from_references(
    *,
    prompt: str,
    out_png: Path,
    reference_images: list[Path] | tuple[Path, ...],
    quality: str | None = None,
    model: str | None = None,
    timeout: float = 180.0,
    max_attempts: int = 3,
) -> dict:
    """Generate a full thumbnail candidate using stored reference images.

    V3 direct API path: the UI chooses references from Nakama, then this wrapper
    sends them to OpenAI Images.edit so the user does not have to upload the same
    host/style/object images manually in another tool.
    """

    q = _quality_from_env(quality)
    resolved_model = _model_from_env(model)
    refs = [Path(path) for path in reference_images if Path(path).is_file()]
    if not refs:
        raise AIImageGenError("no readable reference images were provided")

    out_png.parent.mkdir(parents=True, exist_ok=True)
    client = _get_client()
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        handles = []
        try:
            handles = [path.open("rb") for path in refs]
            logger.info(
                "ai-image-gen full start: model=%s quality=%s refs=%d attempt=%d",
                resolved_model,
                q,
                len(refs),
                attempt,
            )
            response = await asyncio.wait_for(
                client.images.edit(
                    model=resolved_model,
                    image=handles,
                    prompt=prompt,
                    size=_LANDSCAPE_SIZE,
                    quality=q,
                    n=1,
                ),
                timeout=timeout,
            )
            break
        except _RETRYABLE as exc:
            last_exc = exc
            wait = 2 ** (attempt - 1)
            logger.warning(
                "ai-image-gen full retryable error (attempt %d/%d): %s - retrying in %ds",
                attempt,
                max_attempts,
                exc,
                wait,
            )
            if attempt < max_attempts:
                await asyncio.sleep(wait)
                continue
            raise AIImageGenError(
                f"GPT Image retryable error after {max_attempts} attempts: {exc}"
            ) from exc
        except BadRequestError as exc:
            raise AIImageGenError(f"GPT Image rejected request: {exc}") from exc
        except asyncio.TimeoutError as exc:
            last_exc = exc
            if attempt < max_attempts:
                continue
            raise AIImageGenError(
                f"GPT Image timed out after {timeout}s (x{max_attempts})"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise AIImageGenError(f"GPT Image unexpected error: {exc}") from exc
        finally:
            for handle in handles:
                try:
                    handle.close()
                except OSError:
                    pass
    else:
        raise AIImageGenError(
            f"GPT Image failed after {max_attempts} attempts: {last_exc}"
        )

    try:
        b64_payload = response.data[0].b64_json
    except (AttributeError, IndexError, TypeError) as exc:
        raise AIImageGenError(
            f"GPT Image returned unexpected shape: {response!r}"
        ) from exc

    if not b64_payload:
        raise AIImageGenError("GPT Image returned empty b64_json payload")

    out_png.write_bytes(base64.b64decode(b64_payload))
    estimated_cost = _estimated_cost(resolved_model, q)
    logger.info(
        "ai-image-gen full done: out=%s model=%s quality=%s refs=%d estimated_output_cost=%s",
        out_png,
        resolved_model,
        q,
        len(refs),
        estimated_cost,
    )
    return {
        "model": resolved_model,
        "quality": q,
        "size": _LANDSCAPE_SIZE,
        "estimated_output_cost_usd": estimated_cost,
        "out_png": str(out_png.resolve()),
        "attempts": attempt,
        "reference_images": [str(path.resolve()) for path in refs],
    }
