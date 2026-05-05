"""Tests for shared/kb_embedder.py.

model2vec is mocked (spec=) to avoid loading 25 MB model during test runs.
Tests verify shape/dim contract and batch semantics.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np

import shared.kb_embedder as kb_embedder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_model(dim: int = 256) -> MagicMock:
    """Return a mock StaticModel that returns fixed-shape float32 arrays."""
    from model2vec import StaticModel

    mock = MagicMock(spec=StaticModel)
    mock.encode.side_effect = lambda texts, **_: np.random.rand(len(texts), dim).astype(np.float32)
    return mock


# ---------------------------------------------------------------------------
# embed()
# ---------------------------------------------------------------------------


def test_embed_returns_256_dim_float32(monkeypatch):
    """embed('hello') → 1-D float32 array of length DIM=256."""
    mock = _make_mock_model()
    monkeypatch.setattr(kb_embedder, "_model", mock)

    result = kb_embedder.embed("hello world")

    assert isinstance(result, np.ndarray)
    assert result.dtype == np.float32
    assert result.shape == (kb_embedder.DIM,)
    mock.encode.assert_called_once()


def test_embed_calls_encode_with_list_of_one(monkeypatch):
    """embed() wraps the text in a list before passing to model.encode."""
    mock = _make_mock_model()
    monkeypatch.setattr(kb_embedder, "_model", mock)

    kb_embedder.embed("test")

    args, kwargs = mock.encode.call_args
    assert args[0] == ["test"]


# ---------------------------------------------------------------------------
# embed_batch()
# ---------------------------------------------------------------------------


def test_embed_batch_returns_list_of_arrays(monkeypatch):
    """embed_batch(['a', 'b', 'c']) → list of 3 float32 256-dim arrays."""
    mock = _make_mock_model()
    monkeypatch.setattr(kb_embedder, "_model", mock)

    results = kb_embedder.embed_batch(["alpha", "beta", "gamma"])

    assert len(results) == 3
    for arr in results:
        assert isinstance(arr, np.ndarray)
        assert arr.dtype == np.float32
        assert arr.shape == (kb_embedder.DIM,)


def test_embed_batch_empty_input_returns_empty_list(monkeypatch):
    """Empty input → empty list without calling model.encode."""
    mock = _make_mock_model()
    monkeypatch.setattr(kb_embedder, "_model", mock)

    results = kb_embedder.embed_batch([])

    assert results == []
    mock.encode.assert_not_called()


def test_embed_batch_single_text(monkeypatch):
    """Batch of one should work the same as embed()."""
    mock = _make_mock_model()
    monkeypatch.setattr(kb_embedder, "_model", mock)

    results = kb_embedder.embed_batch(["single"])

    assert len(results) == 1
    assert results[0].shape == (kb_embedder.DIM,)


# ---------------------------------------------------------------------------
# Lazy-load behaviour
# ---------------------------------------------------------------------------


def test_get_model_lazy_loads_once(monkeypatch):
    """_get_model() loads the model on first call and caches it."""
    monkeypatch.setattr(kb_embedder, "_model", None)

    with patch("model2vec.StaticModel.from_pretrained") as fp:
        fp.return_value = _make_mock_model()
        # First call loads
        m1 = kb_embedder._get_model()
        # Second call returns same cached instance
        m2 = kb_embedder._get_model()

    fp.assert_called_once_with(kb_embedder._MODEL_NAME)
    assert m1 is m2
