"""Claude model pricing (USD per 1M tokens) for cost dashboard.

Prices are per **million** tokens. Keep this file updated when Anthropic
revises pricing or a new model family ships.

Lookup order (see ``get_pricing``):
1. Exact model id match (best)
2. Family prefix match (``claude-opus`` / ``claude-sonnet`` / ``claude-haiku``)
3. Environment override via ``NAKAMA_PRICING_{INPUT,OUTPUT,CACHE_READ,CACHE_WRITE}_USD_PER_MTOK``
4. Fallback to all-zero (dashboard still renders, but cost column reads $0)

``calc_cost`` combines token counts with a pricing dict to a USD float.

Sources (checked 2026-04-19):
- Anthropic pricing page
- Family defaults mirror the latest 4.x tier. If 5.x ships with a different
  schedule, add an explicit ``claude-*-5-*`` entry above the family default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    """Per-1M-token prices in USD."""

    input_usd_per_mtok: float
    output_usd_per_mtok: float
    cache_read_usd_per_mtok: float
    cache_write_usd_per_mtok: float  # a.k.a. cache_creation / ephemeral write

    def to_dict(self) -> dict:
        return {
            "input_usd_per_mtok": self.input_usd_per_mtok,
            "output_usd_per_mtok": self.output_usd_per_mtok,
            "cache_read_usd_per_mtok": self.cache_read_usd_per_mtok,
            "cache_write_usd_per_mtok": self.cache_write_usd_per_mtok,
        }


# ---------------------------------------------------------------------------
# Family defaults (used when an exact model id doesn't match). Reflect the
# Claude 4.x pricing schedule published by Anthropic.
# ---------------------------------------------------------------------------

_FAMILY_DEFAULTS: dict[str, ModelPricing] = {
    "claude-opus": ModelPricing(
        input_usd_per_mtok=15.0,
        output_usd_per_mtok=75.0,
        cache_read_usd_per_mtok=1.50,
        cache_write_usd_per_mtok=18.75,
    ),
    "claude-sonnet": ModelPricing(
        input_usd_per_mtok=3.0,
        output_usd_per_mtok=15.0,
        cache_read_usd_per_mtok=0.30,
        cache_write_usd_per_mtok=3.75,
    ),
    "claude-haiku": ModelPricing(
        input_usd_per_mtok=0.80,
        output_usd_per_mtok=4.0,
        cache_read_usd_per_mtok=0.08,
        cache_write_usd_per_mtok=1.0,
    ),
    # xAI — 需要特定 ID 覆寫的放 _MODEL_OVERRIDES。這裡只放最通用的 family 兜底。
    # 沒有 cache_write 計費（xAI 自動 cache，不收寫入費）。
    "grok-4-fast": ModelPricing(
        input_usd_per_mtok=0.20,
        output_usd_per_mtok=0.50,
        cache_read_usd_per_mtok=0.05,
        cache_write_usd_per_mtok=0.0,
    ),
    "grok-4": ModelPricing(
        input_usd_per_mtok=2.0,
        output_usd_per_mtok=6.0,
        cache_read_usd_per_mtok=0.20,
        cache_write_usd_per_mtok=0.0,
    ),
    "grok-": ModelPricing(  # 未知 Grok variant 保守用 grok-4 tier
        input_usd_per_mtok=2.0,
        output_usd_per_mtok=6.0,
        cache_read_usd_per_mtok=0.20,
        cache_write_usd_per_mtok=0.0,
    ),
}


# Explicit per-model overrides. Populate only when a specific model's pricing
# diverges from its family default (e.g. a long-context tier).
_MODEL_OVERRIDES: dict[str, ModelPricing] = {}


_ZERO = ModelPricing(0.0, 0.0, 0.0, 0.0)


def _env_override() -> ModelPricing | None:
    """Global env-var override — applies to every model when set.

    Useful for "contract price" scenarios. Set all four vars or none.
    """
    keys = (
        "NAKAMA_PRICING_INPUT_USD_PER_MTOK",
        "NAKAMA_PRICING_OUTPUT_USD_PER_MTOK",
        "NAKAMA_PRICING_CACHE_READ_USD_PER_MTOK",
        "NAKAMA_PRICING_CACHE_WRITE_USD_PER_MTOK",
    )
    raw = [os.environ.get(k) for k in keys]
    if not all(raw):
        return None
    try:
        return ModelPricing(*(float(v) for v in raw))  # type: ignore[arg-type]
    except ValueError:
        return None


def get_pricing(model: str) -> ModelPricing:
    """Resolve pricing for a model id.

    Precedence: env override → exact match → family prefix → zero-fallback.
    """
    env = _env_override()
    if env is not None:
        return env

    if model in _MODEL_OVERRIDES:
        return _MODEL_OVERRIDES[model]

    for prefix, pricing in _FAMILY_DEFAULTS.items():
        if model.startswith(prefix):
            return pricing

    return _ZERO


def calc_cost(
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Return cost in USD for one API call's token counts."""
    p = get_pricing(model)
    return (
        input_tokens * p.input_usd_per_mtok
        + output_tokens * p.output_usd_per_mtok
        + cache_read_tokens * p.cache_read_usd_per_mtok
        + cache_write_tokens * p.cache_write_usd_per_mtok
    ) / 1_000_000
