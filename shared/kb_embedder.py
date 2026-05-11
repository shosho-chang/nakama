"""KB 向量嵌入 — BGE-M3 (default) + potion-base-8M (legacy) lazy-load wrapper.

`embed()` / `embed_batch()` 預設走 BGE-M3 (1024-dim, 跨語言)；設定
``NAKAMA_EMBED_BACKEND=potion`` env var (or pass ``backend="potion"``) 可
反向 opt-out 走 legacy potion-base-8M (256-dim, 英文單語)。

模型在第一次呼叫時下載並 cache；後續呼叫走 cache，不重複下載。

ADR-022: BGE-M3 是全 KB retrieval 預設，配合 ``kb_vectors`` 1024-dim 向量表。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Literal

import numpy as np

if TYPE_CHECKING:
    from model2vec import StaticModel

_POTION_MODEL_NAME = "minishlab/potion-base-8M"
_BGE_M3_MODEL_NAME = "BAAI/bge-m3"

DIM_POTION = 256
DIM_BGE_M3 = 1024

# Back-compat alias — legacy callers expecting potion's 256.
DIM = DIM_POTION

EmbedBackend = Literal["bge-m3", "potion"]

_DEFAULT_BACKEND: EmbedBackend = (
    "potion" if os.environ.get("NAKAMA_EMBED_BACKEND") == "potion" else "bge-m3"
)

_potion_model: StaticModel | None = None
_bge_m3_model = None  # FlagEmbedding.BGEM3FlagModel instance


def current_backend() -> EmbedBackend:
    """Return the active default backend (respects NAKAMA_EMBED_BACKEND env)."""
    return _DEFAULT_BACKEND


def current_dim(backend: EmbedBackend | None = None) -> int:
    """Return embedding dim for the given (or default) backend."""
    effective = backend if backend is not None else _DEFAULT_BACKEND
    return DIM_BGE_M3 if effective == "bge-m3" else DIM_POTION


def _get_potion_model() -> StaticModel:
    global _potion_model
    if _potion_model is None:
        from model2vec import StaticModel  # noqa: PLC0415

        _potion_model = StaticModel.from_pretrained(_POTION_MODEL_NAME)
    return _potion_model


def _get_bge_m3_model():
    global _bge_m3_model
    if _bge_m3_model is None:
        from FlagEmbedding import BGEM3FlagModel  # type: ignore[import]  # noqa: PLC0415

        _bge_m3_model = BGEM3FlagModel(_BGE_M3_MODEL_NAME, use_fp16=True)
    return _bge_m3_model


def _bge_m3_encode(texts: list[str]) -> list[np.ndarray]:
    model = _get_bge_m3_model()
    output = model.encode(texts, batch_size=12, max_length=8192)
    dense = output["dense_vecs"]
    return [row.astype(np.float32) for row in dense]


def _potion_encode(texts: list[str]) -> list[np.ndarray]:
    model = _get_potion_model()
    arr: np.ndarray = model.encode(texts, show_progress_bar=False)
    return [row.astype(np.float32) for row in arr]


def embed(text: str, *, backend: EmbedBackend | None = None) -> np.ndarray:
    """Embed a single text → float32 numpy array (1024-d bge-m3 or 256-d potion)."""
    return embed_batch([text], backend=backend)[0]


def embed_batch(texts: list[str], *, backend: EmbedBackend | None = None) -> list[np.ndarray]:
    """Embed a list of texts → list of float32 numpy arrays."""
    if not texts:
        return []
    effective = backend if backend is not None else _DEFAULT_BACKEND
    if effective == "bge-m3":
        return _bge_m3_encode(texts)
    return _potion_encode(texts)
