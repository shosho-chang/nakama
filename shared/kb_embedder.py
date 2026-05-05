"""KB 向量嵌入 — model2vec potion-base-8M lazy-load wrapper.

`embed()` / `embed_batch()` 回傳 256-dim float32 numpy 向量，供 kb_hybrid_search
寫入 kb_vectors vec0 表或計算相似度。

模型在第一次呼叫時從 HuggingFace 下載並快取；後續呼叫走快取，
不重複下載。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from model2vec import StaticModel

_model: StaticModel | None = None
_MODEL_NAME = "minishlab/potion-base-8M"
DIM = 256


def _get_model() -> StaticModel:
    global _model
    if _model is None:
        from model2vec import StaticModel  # noqa: PLC0415

        _model = StaticModel.from_pretrained(_MODEL_NAME)
    return _model


def embed(text: str) -> np.ndarray:
    """Embed a single text → 256-dim float32 numpy array."""
    model = _get_model()
    arr: np.ndarray = model.encode([text], show_progress_bar=False)
    return arr[0].astype(np.float32)


def embed_batch(texts: list[str]) -> list[np.ndarray]:
    """Embed a list of texts → list of 256-dim float32 numpy arrays."""
    if not texts:
        return []
    model = _get_model()
    arr: np.ndarray = model.encode(texts, show_progress_bar=False)
    return [row.astype(np.float32) for row in arr]
