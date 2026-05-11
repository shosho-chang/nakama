"""Tests for shared/kb_embedder.py.

model2vec is mocked to avoid loading the 25 MB potion model during test runs;
BGE-M3 path is mocked at the encode level.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np

import shared.kb_embedder as kb_embedder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_model(dim: int = 256) -> MagicMock:
    """Return a mock StaticModel that returns fixed-shape float32 arrays."""
    mock = MagicMock()
    mock.encode.side_effect = lambda texts, **_: np.random.rand(len(texts), dim).astype(np.float32)
    return mock


# ---------------------------------------------------------------------------
# Potion path (backend="potion")
# ---------------------------------------------------------------------------


def test_embed_potion_returns_256_dim_float32(monkeypatch):
    mock = _make_mock_model()
    monkeypatch.setattr(kb_embedder, "_potion_model", mock)

    result = kb_embedder.embed("hello world", backend="potion")

    assert isinstance(result, np.ndarray)
    assert result.dtype == np.float32
    assert result.shape == (kb_embedder.DIM_POTION,)
    mock.encode.assert_called_once()


def test_embed_batch_potion_returns_list_of_arrays(monkeypatch):
    mock = _make_mock_model()
    monkeypatch.setattr(kb_embedder, "_potion_model", mock)

    results = kb_embedder.embed_batch(["alpha", "beta", "gamma"], backend="potion")

    assert len(results) == 3
    for arr in results:
        assert isinstance(arr, np.ndarray)
        assert arr.dtype == np.float32
        assert arr.shape == (kb_embedder.DIM_POTION,)


def test_embed_batch_empty_input_returns_empty_list(monkeypatch):
    mock = _make_mock_model()
    monkeypatch.setattr(kb_embedder, "_potion_model", mock)

    results = kb_embedder.embed_batch([], backend="potion")

    assert results == []
    mock.encode.assert_not_called()


def test_potion_lazy_loads_once(monkeypatch):
    monkeypatch.setattr(kb_embedder, "_potion_model", None)
    fake_static_model = SimpleNamespace(from_pretrained=MagicMock(return_value=_make_mock_model()))
    monkeypatch.setitem(sys.modules, "model2vec", SimpleNamespace(StaticModel=fake_static_model))

    m1 = kb_embedder._get_potion_model()
    m2 = kb_embedder._get_potion_model()

    fake_static_model.from_pretrained.assert_called_once_with(kb_embedder._POTION_MODEL_NAME)
    assert m1 is m2


# ---------------------------------------------------------------------------
# BGE-M3 path (default backend)
# ---------------------------------------------------------------------------


def test_embed_bge_m3_returns_1024_dim_float32(monkeypatch):
    """ADR-022: bge-m3 backend → 1024-d float32."""
    fake_dense = np.random.rand(1, kb_embedder.DIM_BGE_M3).astype(np.float32)
    mock_model = MagicMock()
    mock_model.encode.return_value = {"dense_vecs": fake_dense}
    monkeypatch.setattr(kb_embedder, "_bge_m3_model", mock_model)

    result = kb_embedder.embed("跨語言檢索", backend="bge-m3")

    assert result.dtype == np.float32
    assert result.shape == (kb_embedder.DIM_BGE_M3,)


def test_embed_batch_bge_m3_returns_list_of_arrays(monkeypatch):
    fake_dense = np.random.rand(3, kb_embedder.DIM_BGE_M3).astype(np.float32)
    mock_model = MagicMock()
    mock_model.encode.return_value = {"dense_vecs": fake_dense}
    monkeypatch.setattr(kb_embedder, "_bge_m3_model", mock_model)

    results = kb_embedder.embed_batch(["a", "b", "c"], backend="bge-m3")

    assert len(results) == 3
    for arr in results:
        assert arr.shape == (kb_embedder.DIM_BGE_M3,)


# ---------------------------------------------------------------------------
# Default backend selection (ADR-022)
# ---------------------------------------------------------------------------


def test_default_backend_is_bge_m3(monkeypatch):
    """ADR-022 AC: default backend must be bge-m3 unless opt-out env set."""
    monkeypatch.delenv("NAKAMA_EMBED_BACKEND", raising=False)
    import importlib

    importlib.reload(kb_embedder)
    assert kb_embedder.current_backend() == "bge-m3"
    assert kb_embedder.current_dim() == kb_embedder.DIM_BGE_M3


def test_potion_opt_out_via_env(monkeypatch):
    """NAKAMA_EMBED_BACKEND=potion still flips default back to legacy 256-d."""
    monkeypatch.setenv("NAKAMA_EMBED_BACKEND", "potion")
    import importlib

    importlib.reload(kb_embedder)
    assert kb_embedder.current_backend() == "potion"
    assert kb_embedder.current_dim() == kb_embedder.DIM_POTION
