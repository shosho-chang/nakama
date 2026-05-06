"""KB 向量嵌入 — model2vec potion-base-8M (legacy) + BGE-M3 (ADR-020 S6).

embed() / embed_batch() 預設使用 BGE-M3（1024-dim 跨語言模型）。
舊 potion-base-8M 256-dim 路徑仍可透過 backend="potion" 取用。

Set ``NAKAMA_EMBED_BACKEND=potion`` env var (or pass backend="potion") to
use the legacy potion model; default is ``backend="bge-m3"``.

ADR-020 S6: dense retrieval upgrades from 256d → 1024d cross-lingual.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Literal

import numpy as np

if TYPE_CHECKING:
    from model2vec import StaticModel

_POTION_MODEL_NAME = "minishlab/potion-base-8M"
_BGE_M3_MODEL_NAME = "BAAI/bge-m3"

DIM = 256
DIM_BGE_M3 = 1024

_potion_model: StaticModel | None = None
_bge_m3_model = None  # FlagModel instance

EmbedBackend = Literal["bge-m3", "potion"]

_DEFAULT_BACKEND: EmbedBackend = (
    "bge-m3" if os.environ.get("NAKAMA_EMBED_BACKEND") == "bge-m3" else "potion"
)


# ---------------------------------------------------------------------------
# Potion (legacy 256-dim)
# ---------------------------------------------------------------------------


def _get_potion_model() -> StaticModel:
    global _potion_model
    if _potion_model is None:
        from model2vec import StaticModel  # noqa: PLC0415

        _potion_model = StaticModel.from_pretrained(_POTION_MODEL_NAME)
    return _potion_model


# ---------------------------------------------------------------------------
# BGE-M3 (1024-dim cross-lingual)
# ---------------------------------------------------------------------------


def _get_bge_m3_model():
    global _bge_m3_model
    if _bge_m3_model is None:
        from FlagEmbedding import BGEM3FlagModel  # type: ignore[import]

        _bge_m3_model = BGEM3FlagModel(_BGE_M3_MODEL_NAME, use_fp16=True)
    return _bge_m3_model


def _bge_m3_encode(texts: list[str]) -> list[np.ndarray]:
    model = _get_bge_m3_model()
    output = model.encode(texts, batch_size=12, max_length=8192)
    dense = output["dense_vecs"]
    return [row.astype(np.float32) for row in dense]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def embed(text: str, *, backend: EmbedBackend | None = None) -> np.ndarray:
    """Embed a single text. Returns 1024-dim (bge-m3) or 256-dim (potion)."""
    return embed_batch([text], backend=backend)[0]


def embed_batch(texts: list[str], *, backend: EmbedBackend | None = None) -> list[np.ndarray]:
    """Embed a list of texts. Returns list of float32 numpy arrays."""
    if not texts:
        return []
    effective_backend = backend if backend is not None else _DEFAULT_BACKEND
    if effective_backend == "bge-m3":
        return _bge_m3_encode(texts)
    model = _get_potion_model()
    arr: np.ndarray = model.encode(texts, show_progress_bar=False)
    return [row.astype(np.float32) for row in arr]
